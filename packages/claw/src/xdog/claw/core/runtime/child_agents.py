"""Claw session-owned children with inherited tool allowlists and isolated extensions."""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from xdog.agent import Agent, AgentConfig, AgentMessage, BeforeToolCallContext, BeforeToolCallResult
from xdog.agent.access import read_only_call
from xdog.agent.async_work import run_owned
from xdog.agent.capabilities.children import ChildPort
from xdog.agent.core import AgentTool, AgentToolResult, StreamFn, ToolExecutionMode
from xdog.agent.cross_loop import settle_on
from xdog.agent.supervision import RESERVED_TOOLS, ChildSpec, child_prompt
from xdog.agent.supervision_compaction import ArchivedSummaryStream, preserve_model_metadata
from xdog.agent.supervision_model import resolve_child_model
from xdog.agent.supervision_policy import ChildDelegation, ChildSkills, ParentHandle
from xdog.agent.types import AfterToolCallContext, AfterToolCallResult, HandledInput
from xdog.ai.types import AssistantMessage, Context, StreamOptions, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.runtime.child_extensions import ChildExtensions
from xdog.claw.core.runtime.child_runtime import ClawChildRuntime
from xdog.claw.core.runtime.child_store import ChildStore

if TYPE_CHECKING:
    from xdog.claw.core.persistence.session_preparation import PreparedChildren
    from xdog.claw.core.runtime.session import AgentSession


@dataclass(frozen=True)
class _ParentSnapshot:
    """Only immutable routing/delegation inputs, never parent history or prompt."""
    model: str
    stream: StreamFn
    options: StreamOptions
    tools: tuple[AgentTool, ...]
    context: dict[str, Any]
    tool_execution: ToolExecutionMode
    context_window: int
    max_prompt_tokens: int
    supports_tool_calls: bool | None
    extension_owners: tuple[tuple[str, str], ...]
    mcp_tools: tuple[str, ...]


@dataclass(frozen=True)
class _ToolAdmission:
    prepared: BeforeToolCallContext
    tool: AgentTool
    name: str
    call: ToolCall
    assistant: AssistantMessage
    arguments: dict[str, Any]
    encoded_arguments: str
    tool_revision: int
    access_revision: int
    token: object


class ClawChildren:
    supervisor: ClawChildRuntime
    def __init__(self, session: AgentSession, prepared: PreparedChildren) -> None:
        self.session = session
        branch = session._store.active_branch(session.meta.session_id) or "head"
        if branch != prepared.branch:
            raise ValueError("Prepared child catalog belongs to another context branch")
        self.store = ChildStore.scoped(session._store._sessions_dir, session.meta.session_id, branch)
        self.capabilities = ChildPort(session.meta.session_id, branch,
            lambda: (*session._runtime.capabilities.tools, *session._runtime.application_tools()),
            parent_valid=lambda identifier: identifier in self.grants and self.parent.live)
        self.snapshots: dict[str, _ParentSnapshot] = {}
        self.grants: dict[str, ChildDelegation] = {}
        self.skill_grants: dict[str, ChildSkills] = {}
        parent_agent = session.agent
        self.parent = ParentHandle(
            alive=lambda: (not self.supervisor.closed and not session.extensions._closed
                           and not session._runtime._closing and session.agent is parent_agent
                           and session._runtime._active_session is session),
            tools=lambda: parent_agent.active_tools,
            skills=lambda: parent_agent._skills)
        self.tool_limits: dict[str, tuple[str, ...] | None] = {}
        self.extensions = ChildExtensions(session.extensions, self.store)
        records = json.loads(prepared.records_json)
        self.supervisor = ClawChildRuntime(self.create, self.store.save, capture=self.capture, begin=self.store.begin,
            persist_inputs=self.store.save_inputs, persist_return=self.store.save_return,
            persist_admission=self.store.save_admission, archive_compaction=self.store.archive_compaction,
            forget=self.store.forget, finalize=self.finish,
            on_event=self.extensions.event, records=records, presets=session._runtime.child_presets,
            owner_loop=lambda: session._host_loop)
        self.capabilities.supervisor = self.supervisor
        self.supervisor.diagnostic = prepared.diagnostic
    async def capture(self, spec: ChildSpec) -> None:
        """Validate actual parent grants before any child ID/store/task admission."""
        agent = self.session.agent
        available = tuple(tool for tool in agent.active_tools
                          if tool.name not in RESERVED_TOOLS and self.capabilities.inherited(tool))
        names = tuple(tool.name for tool in available) if spec.tools is None else spec.tools
        if not set(names) <= {tool.name for tool in available}:
            raise ValueError('Child tool profile exceeds parent delegable active tools')
        original = self.parent.capture_tools(names, spec.tool_actions)
        # Snapshot only explicit product fields; future private parent context
        # must never become implicitly delegable. Service references are carried
        # only for tools present in the actual frozen grant.
        source = agent.tool_context
        context = {key: source[key] for key in ('group_id', 'workspace_dir', 'fs_workspace', 'data_dir')
                   if key in source}
        for tool, keys in (('message', ('_send_fn',)), ('memory', ('_memory_search', '_memory_manager'))):
            if tool in names:
                context.update({key: source[key] for key in keys if key in source})
        state = _ParentSnapshot(agent.state.model, agent.stream_fn, agent.options, original.tools,
            context, agent.tool_execution, agent.context_window, agent.max_prompt_tokens, agent.supports_tool_calls,
            tuple((tool.name, self.session.extensions.tool_owner(tool.name)) for tool in original.tools
                  if tool.name in self.session.extensions.aliases),
            tuple(tool.name for tool in original.tools
                  if any(current.name == tool.name for current in self.session._runtime.mcp.tools())))
        skills = await run_owned(self.parent.capture_skills, spec.skills)
        # The owner may have closed, narrowed tools or replaced its skill source
        # while owned filesystem validation was settling. Never publish that work.
        current = self.parent.capture_tools(original.names, original.actions)
        if any(a is not b for a, b in zip(original.tools, current.tools, strict=True)):
            raise ValueError('Original parent tool delegation changed during child admission')
        allowed = self.capabilities.capture(spec, agent.active_tools, names)
        grant = self.parent.capture_tools(allowed, spec.tool_actions)
        self.grants[spec.id] = grant
        self.skill_grants[spec.id] = skills
        self.tool_limits[spec.id] = grant.names
        context = dict(state.context)
        if 'message' not in grant.names:
            context.pop('_send_fn', None)
        if 'memory' not in grant.names:
            context.pop('_memory_search', None)
            context.pop('_memory_manager', None)
        self.snapshots[spec.id] = replace(state, tools=grant.tools, context=context)
        self.extensions.reserve(spec.id)

    async def create(self, spec: ChildSpec, publish: Callable[[dict[str, Any]], None]) -> Agent:
        session, runtime = self.session, self.session._runtime
        state = self.snapshots[spec.id]
        allowed_tools = self.tool_limits[spec.id]
        role_tools = await self.capabilities.tools(spec)
        if role_tools:
            allowed_tools = (*(allowed_tools or ()), *(tool.name for tool in role_tools))
        # Child extension context and every later registration sync keep this
        # admission-time ceiling, not a later parent selection or worker tool list.
        extension_spec = replace(spec, tools=allowed_tools)
        grant = self.grants[spec.id]
        skills = self.skill_grants[spec.id]
        await run_owned(self.parent.check_skills, skills)
        current = self.parent.capture_tools(grant.names, grant.actions)
        if any(source is not actual for source, actual in zip(grant.tools, current.tools, strict=True)):
            raise ValueError('Original parent tool delegation changed during child preparation')
        selected = await run_owned(resolve_child_model, state.stream, state.model, state.options,
            spec.config(), state.context_window, state.supports_tool_calls,
            parent_max_prompt_tokens=state.max_prompt_tokens)
        extension = await self.extensions.create(extension_spec, publish, model=selected.id,
                                                 thinking=selected.options.thinking or "off", initialize=False)
        extension_names = dict(state.extension_owners)
        # Original schema is an immutable admission snapshot; a later mutation
        # of the parent's nested parameters cannot broaden child advertisement.
        proxies = tuple(replace(tool, parameters=copy.deepcopy(schema))
                        for tool, schema in zip(grant.tools, grant.schemas, strict=True))
        extension_bindings: dict[str, tuple[AgentTool, AgentTool]] = {}
        admissions: dict[str, _ToolAdmission] = {}
        executing: set[object] = set()
        parent_agent = session.agent
        parent_access = session.access

        def bound_call(ctx: BeforeToolCallContext) -> bool:
            return self.capabilities.permits(spec.id, ctx.tool_call.name, ctx.context.tools or ())

        def check_policy(ctx: BeforeToolCallContext) -> None:
            self.parent.check_tool(grant, ctx, capability_bound=bound_call(ctx))
            self.extensions.check_parent()
            if (session.access is not parent_access or session.agent is not parent_agent):
                raise ValueError('Original child parent policy owner changed')
            if (session.access.state.read_only or spec.access == 'read-only') and not (
                    bound_call(ctx) or read_only_call(ctx.tool_call.name, ctx.args)):
                raise ValueError('Child/parent access is read-only')
            name = ctx.tool_call.name
            if name in extension_names:
                expected = extension_names[name]
                binding = extension_bindings.get(name)
                if (expected not in runtime.extension_allow_tools
                        or session.extensions.tool_owner(name) != expected
                        or extension.tool_owner(name) != expected or binding is None
                        or not any(tool is binding[0] for tool in extension.tools())):
                    raise ValueError('Child extension tool use disabled or original registration changed')
            if name in state.mcp_tools and (not runtime.mcp.allowed(name)
                    or not any(tool.name == name for tool in runtime.mcp.tools())):
                raise ValueError('Child MCP tool use disabled by parent policy')

        def encoded_arguments(params: dict[str, Any]) -> str:
            return json.dumps(params, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':'))

        def check_effect(call_id: str, params: dict[str, Any], proxy: AgentTool,
                         admitted: _ToolAdmission) -> None:
            if (admissions.get(call_id) is not admitted or admitted.tool is not proxy
                    or proxy.name != admitted.name or admitted.call.id != call_id
                    or admitted.call.name != admitted.name or admitted.prepared.tool_call is not admitted.call
                    or admitted.prepared.assistant_message is not admitted.assistant
                    or not any(part is admitted.call for part in admitted.assistant.content)
                    or params is not admitted.arguments or encoded_arguments(params) != admitted.encoded_arguments):
                raise ValueError('Child tool executor does not match its original call/arguments admission')
            check_policy(replace(admitted.prepared, args=params))
            if (parent_agent.tool_selection_revision != admitted.tool_revision
                    or parent_access.state.revision != admitted.access_revision):
                raise ValueError('Parent policy changed between child tool admission and actual execution')

        def protect(tool: AgentTool, source: AgentTool, *, registration: AgentTool | None = None) -> AgentTool:
            # Preserve both the original parent's permission validator and the
            # independent child's registration validator. Later narrowing must
            # also apply, but cannot replace the captured validator with a grant.
            captured_validators = (source.validate_arguments, tool.validate_arguments)
            proxy: AgentTool
            def validators() -> tuple[Callable[[dict[str, Any]], Any], ...]:
                selected: list[Callable[[dict[str, Any]], Any]] = []
                for validate in (*captured_validators, source.validate_arguments,
                                 registration.validate_arguments if registration is not None else None):
                    if validate is not None and not any(validate is previous for previous in selected):
                        selected.append(validate)
                return tuple(selected)

            async def validate_arguments(params: dict[str, Any]) -> None:
                for validate in validators():
                    checked = validate(params)
                    if inspect.isawaitable(checked):
                        await checked

            async def execute(call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                              on_update: Any = None, *, ctx: dict[str, Any] | None = None) -> AgentToolResult:
                admitted = admissions.get(call_id)
                if admitted is None:
                    raise ValueError('Child executor has no original tool admission')
                # A different proxy/argument object cannot consume or erase an
                # original admission. Its own executor does consume the receipt,
                # even when an in-place argument mutation is then refused.
                if admitted.tool is not proxy or params is not admitted.arguments:
                    check_effect(call_id, params, proxy, admitted)
                if admitted.token in executing:
                    raise ValueError('Child tool admission already owns an executor')
                executing.add(admitted.token)
                try:
                    check_effect(call_id, params, proxy, admitted)
                    if skills.slugs:
                        await run_owned(self.parent.check_skills, skills)
                        check_effect(call_id, params, proxy, admitted)
                    # Preparation precedes hooks and sibling effects. A pure
                    # validator may be async and own dynamic permission state;
                    # replay it against the exact admitted effect arguments.
                    for validate in validators():
                        checked = validate(params)
                        if inspect.isawaitable(checked):
                            await checked
                        check_effect(call_id, params, proxy, admitted)
                    if cancel is not None and cancel.is_set():
                        raise asyncio.CancelledError
                    if tool.execute is None:
                        raise ValueError('Delegated child tool has no executor')
                    return await tool.execute(call_id, params, cancel, on_update, ctx=ctx)
                finally:
                    executing.discard(admitted.token)
                    if admissions.get(call_id) is admitted:
                        admissions.pop(call_id)
            proxy = replace(tool, execute=execute,
                            validate_arguments=validate_arguments if validators() else None)
            return proxy

        def host_owned(original: AgentTool) -> AgentTool:
            async def execute(call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                              on_update: Any = None, ctx: dict[str, Any] | None = None) -> AgentToolResult:
                loop = session._host_loop
                if loop is None:
                    raise RuntimeError("Child host service needs an active owning loop")
                child_loop = asyncio.get_running_loop()
                async def update(result: AgentToolResult) -> None:
                    if on_update is not None:
                        async def deliver() -> None:
                            await on_update(result)
                        await settle_on(child_loop, deliver())
                async def invoke() -> AgentToolResult:
                    assert original.execute is not None
                    return await original.execute(call_id, params, None, update, ctx=ctx)
                # Cancellation of this bridge cancels/settles the actual host
                # task. Never pass a child-loop Event into a host-loop waiter.
                return await settle_on(loop, invoke())
            return replace(original, execute=execute)
        # These tools own group/channel/memory/skill services rather than
        # independent subprocess/file operations. Execute on the original host
        # loop while preserving child-loop update and cancellation ownership.
        services = {"message", "memory"}
        delegated = tuple(protect(skills.tool(tool, lambda: self.parent.live) if tool.name == 'skill' else
                                  host_owned(tool) if tool.name in services else tool, source)
                          for tool, source in zip(proxies, grant.tools, strict=True))
        tools = tuple(tool for tool in delegated if tool.name not in extension_names) + role_tools

        def sync_tools() -> None:
            current = self.parent.capture_tools(None).tools if self.parent.live else ()
            current_extensions = extension.tools()
            selected_tools: list[AgentTool] = []
            for source, proxy in zip(grant.tools, delegated, strict=True):
                if not any(source is item for item in current):
                    continue
                if source.name in extension_names:
                    binding = extension_bindings.get(source.name)
                    if binding is not None and any(binding[0] is item for item in current_extensions):
                        selected_tools.append(binding[1])
                else:
                    selected_tools.append(proxy)
            # Keep exact original role capability identities/bridges; a label or
            # a same-named replacement is never sufficient to regain authority.
            selected_tools.extend(tool for tool in role_tools if self.parent.live
                                  and self.capabilities.permits(spec.id, tool.name, (tool,)))
            agent.set_tools(tuple(selected_tools))
        async def before(ctx: BeforeToolCallContext, cancel: asyncio.Event | None = None
                         ) -> BeforeToolCallResult | None:
            try:
                check_policy(ctx)
                await run_owned(self.parent.check_skills, skills)
                check_policy(ctx)
                blocked = await self.extensions.pre(extension, ctx)
                if blocked is not None:
                    return blocked
                await run_owned(self.parent.check_skills, skills)
                check_policy(ctx)
                if not bound_call(ctx):
                    if ctx.tool_call.id in admissions:
                        raise ValueError('Duplicate pending child tool call ID; original admission is not replaceable')
                    if (sum(isinstance(part, ToolCall) and part.id == ctx.tool_call.id
                            for part in ctx.assistant_message.content) != 1
                            or not any(part is ctx.tool_call for part in ctx.assistant_message.content)):
                        raise ValueError('Child tool call is not unique in its original assistant message')
                    selected_tool = next((tool for tool in ctx.context.tools or ()
                                          if tool.name == ctx.tool_call.name), None)
                    if selected_tool is None or not isinstance(ctx.args, dict):
                        raise ValueError('Original child tool/arguments are unavailable')
                    admissions[ctx.tool_call.id] = _ToolAdmission(ctx, selected_tool, ctx.tool_call.name,
                        ctx.tool_call, ctx.assistant_message, ctx.args, encoded_arguments(ctx.args),
                        parent_agent.tool_selection_revision, parent_access.state.revision, object())
                return None
            except (ValueError, PermissionError) as exc:
                return BeforeToolCallResult(block=True, reason=str(exc))
            finally:
                sync_tools()

        async def after(ctx: AfterToolCallContext, cancel: asyncio.Event | None = None) -> AfterToolCallResult | None:
            try:
                return await self.extensions.post(extension, ctx)
            finally:
                sync_tools()

        agent: Agent
        async def context(messages: list[AgentMessage]) -> list[AgentMessage]:
            try:
                result = await self.extensions.context(extension, messages)
                return result
            finally:
                sync_tools()

        async def input_transform(message: UserMessage, source: str,
                                  cancel: asyncio.Event | None = None) -> UserMessage | HandledInput:
            try:
                self.extensions.check_parent()
                return await extension.input(message, source, channel="child")
            finally:
                sync_tools()

        from xdog.agent.extensions.providers import ExtensionProviderStream
        base_stream = state.stream
        if isinstance(base_stream, ExtensionProviderStream):
            base_stream = base_stream.base
        routed_stream = selected.stream(ExtensionProviderStream(extension, base_stream))
        async def check_request() -> None:
            # Resolve filesystem skill revocation off-loop immediately before
            # EVERY model request, including native compaction summary calls.
            await run_owned(self.parent.check_skills, skills)
            current = self.parent.capture_tools(grant.names, grant.actions)
            if any(source is not actual for source, actual in zip(grant.tools, current.tools, strict=True)):
                raise ValueError('Original parent tool delegation changed; no child model request')
        def guarded(model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
            return ArchivedSummaryStream(lambda: routed_stream(model, context, options), check_request)
        child_stream = preserve_model_metadata(guarded, routed_stream)
        agent = Agent(child_stream,
            config=AgentConfig(model=selected.id,
            system_prompt=selected.prompt(child_prompt(None, spec)),
            tool_execution=state.tool_execution,
            context_window=selected.context_window, max_prompt_tokens=selected.max_prompt_tokens,
            supports_tool_calls=selected.supports_tool_calls, options=selected.options,
            compaction=selected.compaction(child_stream)),
            tools=tools, parent=self.parent,
            skills=skills, active_skills=skills.slugs,
            tool_ctx={**state.context, '_skill_manager': skills},
            transform_context=context, before_tool_call=before, after_tool_call=after, input_transform=input_transform)
        if allowed_tools is not None:
            allowed = set(allowed_tools)
            agent.set_tools(tuple(tool for tool in agent.state.tools if tool.name in allowed))
        from xdog.agent.extensions.inspection import capture
        inspection_session = str(extension.context().get("session_id", ""))
        def inspection() -> dict[str, Any] | None:
            if (extension._closed or self.extensions.parent._closed
                    or extension.context().get("session_id") != inspection_session):
                return None
            metadata = (extension.provider_model(agent.state.model) if agent.state.model.startswith("ext-")
                        else selected.metadata)
            return capture(agent, inspection_session, spec.id, metadata, usage_floor=0)
        extension.inspection = inspection
        def inventory() -> dict[str, Any] | None:
            from xdog.agent.extensions.tool_inventory import capture as capture_tools
            if (extension._closed or self.extensions.parent._closed
                    or extension.context().get('session_id') != inspection_session):
                return None
            return capture_tools(agent, inspection_session, spec.id)
        extension.tool_inventory = inventory

        self.extensions.bind_pending_messages(spec.id, agent)
        await self.extensions.start(extension_spec, extension)
        for tool in extension.tools():
            if tool.name in extension_names and extension.tool_owner(tool.name) == extension_names[tool.name]:
                index = grant.names.index(tool.name)
                extension_bindings[tool.name] = tool, protect(replace(tool,
                    parameters=copy.deepcopy(grant.schemas[index])), grant.tools[index], registration=tool)
        sync_tools()
        return agent

    async def finish(self, spec: ChildSpec) -> None:
        self.snapshots.pop(spec.id, None)
        self.tool_limits.pop(spec.id, None)
        self.grants.pop(spec.id, None)
        self.skill_grants.pop(spec.id, None)
        await self.extensions.close(spec)
