"""Claw session-owned children with inherited tool allowlists and isolated extensions."""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xdog.agent import Agent, AgentConfig, AgentMessage, BeforeToolCallContext, BeforeToolCallResult
from xdog.agent.access import read_only_call
from xdog.agent.async_work import run_owned
from xdog.agent.capabilities.children import ChildPort
from xdog.agent.capabilities.runtime import CapabilitySet
from xdog.agent.child_compaction import ArchivedSummaryStream, preserve_model_metadata
from xdog.agent.child_model import resolve_child_model
from xdog.agent.child_policy import ChildDelegation, ChildSkills, ParentHandle, _matches
from xdog.agent.children import RESERVED_TOOLS, ChildSpec, child_prompt
from xdog.agent.core import AgentTool, AgentToolResult, StreamFn, ToolExecutionMode
from xdog.agent.cross_loop import settle_on
from xdog.agent.mcp import McpRegistry
from xdog.agent.tool_effects import ToolEffect
from xdog.agent.tools import ToolRepo
from xdog.agent.tools.filesystem_constraints import (
    capture_filesystem_roots as _filesystem_roots,
)
from xdog.agent.tools.filesystem_constraints import decode_filesystem_roots, encode_filesystem_roots
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
    mcp_registry: McpRegistry
    mcp_tools: tuple[AgentTool, ...]
    filesystem_roots: tuple[Path, ...] | None
    filesystem_ceiling: tuple[Path, ...] | None


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
    runtime: ClawChildRuntime
    def __init__(self, session: AgentSession, prepared: PreparedChildren) -> None:
        self.session = session
        branch = session._store.active_branch(session.meta.session_id) or "head"
        if branch != prepared.branch:
            raise ValueError("Prepared child catalog belongs to another context branch")
        self.store = ChildStore.scoped(session._store._sessions_dir, session.meta.session_id, branch)
        self.capabilities = ChildPort(session.meta.session_id, branch,
            lambda: (*session._runtime.capabilities.owned_tools, *session._runtime.capabilities.tools,
                     *(() if session._runtime.application_capabilities is None
                       else session._runtime.application_capabilities.owned_tools),
                     *session._runtime.capability_assembly.tools),
            parent_valid=lambda identifier: identifier in self.grants and self.parent.live)
        self.snapshots: dict[str, _ParentSnapshot] = {}
        # Original-owner ceiling survives idle-round teardown. A later message
        # to the same child ID must not capture a newly broadened parent grant.
        # This authority is never reconstructed from persisted child records.
        self.filesystem_limits: dict[str, tuple[Path, ...] | None] = {}
        self.grants: dict[str, ChildDelegation] = {}
        self.scoped_tools: dict[str, dict[str, AgentTool]] = {}
        self.scope_owners: dict[str, CapabilitySet] = {}
        self.skill_grants: dict[str, ChildSkills] = {}
        parent_agent = session.agent
        self.parent = ParentHandle(
            alive=lambda: (not self.runtime.closed and not session.extensions._closed
                           and not session._runtime._closing and session.agent is parent_agent
                           and session._runtime._active_session is session),
            tools=lambda: parent_agent.active_tools,
            skills=lambda: parent_agent._skills)
        self.extensions = ChildExtensions(session.extensions, self.store)
        records = json.loads(prepared.records_json)
        self.runtime = ClawChildRuntime(self.create, self.store.save, capture=self.capture, begin=self.store.begin,
            persist_inputs=self.store.save_inputs, persist_return=self.store.save_return,
            persist_admission=self.store.save_admission, archive_compaction=self.store.archive_compaction,
            forget=self.store.forget, finalize=self.finish,
            on_event=self.extensions.event, records=records, presets=session._runtime.child_presets,
            owner_loop=lambda: session._host_loop)
        self.capabilities.runtime = self.runtime
        self.runtime.diagnostic = prepared.diagnostic
    async def capture(self, spec: ChildSpec) -> ChildSpec:
        """Validate actual parent grants before any child ID/store/task admission."""
        spec = replace(spec, tool_scopes=spec.config().to_dict()["tool_scopes"])
        agent = self.session.agent
        owner = self.session._runtime.capabilities
        available = tuple(tool for tool in agent.active_tools
                          if tool.name not in RESERVED_TOOLS and (self.capabilities.inherited(tool)
                              or (spec.tools is not None and tool.name in spec.tool_scopes
                                  and owner.can_delegate_child_tool(tool))))
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
        if (roots := _filesystem_roots(source)) is not None:
            context["fs_confine_to"] = roots
        for tool, keys in (('message', ('_message_port',)), ('memory', ('_memory_search', '_memory_manager'))):
            if tool in names:
                context.update({key: source[key] for key in keys if key in source})
        if context.get('_message_port') is not None:
            context['_message_port'] = context['_message_port'].for_child(spec.id)
        state = _ParentSnapshot(agent.state.model, agent.stream_fn, agent.options, original.tools,
            context, agent.tool_execution, agent.context_window, agent.max_prompt_tokens, agent.supports_tool_calls,
            self.session._runtime.mcp,
            tuple(tool for tool in original.tools
                  if self.session._runtime.mcp.owns_tool(tool)), _filesystem_roots(source), _filesystem_roots(source))
        skills = await run_owned(self.parent.capture_skills, spec.skills)
        # The owner may have closed, narrowed tools or replaced its skill source
        # while owned filesystem validation was settling. Never publish that work.
        current = self.parent.capture_tools(original.names, original.actions)
        if any(a is not b for a, b in zip(original.tools, current.tools, strict=True)):
            raise ValueError('Original parent tool delegation changed during child admission')
        allowed = self.capabilities.capture(spec, agent.active_tools, names)
        grant = self.parent.capture_tools(allowed, spec.tool_actions)
        ceilings = self.runtime.delegation_schemas(spec.id)
        if ceilings:
            if set(grant.names) != set(ceilings):
                raise ValueError("Saved child delegation is unavailable; no implicit grant on continuation")
            grant = replace(grant, schemas=tuple(copy.deepcopy(ceilings[name]) for name in grant.names))
        scoped = {}
        for name, scope in spec.tool_scopes.items():
            actual = next((tool for tool in grant.tools if tool.name == name), None)
            if actual is None:
                raise ValueError("Scoped tool is outside the original child delegation")
            scoped[name] = await owner.delegate_child_tool(actual, spec, scope)
            grant.scoped_parameters(name, scoped[name].parameters)
        grant = replace(grant, schemas=tuple(
            grant.scoped_parameters(tool.name, scoped[tool.name].parameters)
            if tool.name in scoped else grant.schemas[index]
            for index, tool in enumerate(grant.tools)))
        current = self.parent.capture_tools(grant.names, grant.actions)
        if any(a is not b for a, b in zip(grant.tools, current.tools, strict=True)):
            raise ValueError("Parent tool delegation changed during scope validation")
        if scoped and self.session._runtime.capabilities is not owner:
            raise ValueError("Original scoped capability owner retired during delegation")
        if _filesystem_roots(agent.tool_context) != state.filesystem_roots:
            raise ValueError("Parent filesystem execution constraints changed during child admission")
        ceiling = state.filesystem_roots
        if "filesystem" in grant.names:
            historical, data = self.runtime.filesystem_history(spec.id)
            if spec.id in self.filesystem_limits:
                ceiling = self.filesystem_limits[spec.id]
            elif historical:
                # Missing old metadata is explicitly deny-all, not unconfined.
                ceiling = () if data is None else decode_filesystem_roots(data)
            if ceiling != () and ceiling != state.filesystem_roots:
                raise ValueError("Original child filesystem execution constraints changed")
            self.filesystem_limits[spec.id] = ceiling
        self.scoped_tools[spec.id] = scoped
        self.scope_owners[spec.id] = owner
        self.grants[spec.id] = grant
        self.skill_grants[spec.id] = skills
        context = dict(state.context)
        if "filesystem" in grant.names and ceiling is not None:
            context["fs_confine_to"] = ceiling
        if 'message' not in grant.names:
            context.pop('_message_port', None)
        if 'memory' not in grant.names:
            context.pop('_memory_search', None)
            context.pop('_memory_manager', None)
        self.snapshots[spec.id] = replace(state, tools=grant.tools, context=context, filesystem_ceiling=ceiling)
        self.extensions.reserve(spec.id)
        return replace(spec, tools=grant.names, skills=skills.slugs)

    async def create(self, spec: ChildSpec, publish: Callable[[dict[str, Any]], None]) -> Agent:
        session, runtime = self.session, self.session._runtime
        state = self.snapshots[spec.id]
        if _filesystem_roots(session.agent.tool_context) != state.filesystem_roots:
            raise ValueError("Parent filesystem execution constraints changed before child construction")
        allowed_tools = self.grants[spec.id].names
        role_tools = await self.capabilities.tools(spec)
        if role_tools:
            allowed_tools = (*(allowed_tools or ()), *(tool.name for tool in role_tools))
        # Child hooks see the admission-time ceiling, never a later parent grant.
        extension_spec = replace(spec, tools=allowed_tools)
        grant = self.grants[spec.id]
        if "filesystem" in grant.names:
            self.runtime.record_filesystem_history(spec.id, encode_filesystem_roots(state.filesystem_ceiling))
        scoped = self.scoped_tools[spec.id]
        scope_owner = self.scope_owners[spec.id]
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
        # Original schema is an immutable admission snapshot; a later mutation
        # of the parent's nested parameters cannot broaden child advertisement.
        proxies = tuple(replace(scoped.get(tool.name, tool), parameters=(
                                    grant.scoped_parameters(tool.name, scoped[tool.name].parameters)
                                    if tool.name in scoped else grant.provider_parameters(tool.name)))
                        for tool in grant.tools)
        admissions: dict[str, _ToolAdmission] = {}
        executing: set[object] = set()
        parent_agent = session.agent
        parent_access = session.access

        def bound_call(ctx: BeforeToolCallContext) -> bool:
            return self.capabilities.permits(spec.id, ctx.tool_call.name, ctx.context.tools or ())

        def safe_call(ctx: BeforeToolCallContext) -> bool:
            if ctx.tool_call.name not in scoped:
                return read_only_call(ctx.tool_call.name, ctx.args)
            actual = next(tool for tool in grant.tools if tool.name == ctx.tool_call.name)
            if runtime.capabilities is not scope_owner or not scope_owner.can_delegate_child_tool(actual):
                raise ValueError("Original scoped capability tool is no longer delegable")
            if not _matches(scoped[ctx.tool_call.name].parameters, ctx.args):
                return False
            effect = runtime.capability_assembly.tool_effect(actual, ctx.args) or ToolEffect("execute", "unknown")
            return effect.access == "read"

        def check_policy(ctx: BeforeToolCallContext) -> None:
            if ctx.tool_call.name == "filesystem" and (
                    _filesystem_roots(parent_agent.tool_context) != state.filesystem_roots):
                raise ValueError("Parent filesystem execution constraints changed; child delegation is invalid")
            self.parent.check_tool(grant, ctx, capability_bound=bound_call(ctx))
            self.extensions.check_parent()
            if (session.access is not parent_access or session.agent is not parent_agent):
                raise ValueError('Original child parent policy owner changed')
            # Scoped binding validity is required even for a writable child.
            # Replacing a scope is retirement, not permission to finish using
            # the still-live former provider after an awaited validator.
            if ctx.tool_call.name in scoped:
                actual = next(tool for tool in grant.tools if tool.name == ctx.tool_call.name)
                if runtime.capabilities is not scope_owner or not scope_owner.can_delegate_child_tool(actual):
                    raise ValueError("Original scoped capability owner/identity retired")
            if (session.access.state.read_only or spec.access == 'read-only') and not (
                    bound_call(ctx) or safe_call(ctx)):
                raise ValueError('Child/parent access is read-only')
            name = ctx.tool_call.name
            original_mcp = next((tool for tool in state.mcp_tools if tool.name == name), None)
            if original_mcp is not None and (runtime.mcp is not state.mcp_registry
                    or parent_agent.mcp_registry is not state.mcp_registry
                    or not state.mcp_registry.allowed(name)
                    or not state.mcp_registry.is_current_tool(original_mcp)):
                raise ValueError('Child MCP tool use disabled or original registration changed')

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

        def protect(tool: AgentTool, source: AgentTool) -> AgentTool:
            # Preserve original permission validators across child delegation.
            # Later narrowing cannot replace a captured validator with a grant.
            captured_validators = (None if tool.name in scoped else source.validate_arguments,
                                   tool.validate_arguments)
            proxy: AgentTool
            def validators() -> tuple[Callable[[dict[str, Any]], Any], ...]:
                selected: list[Callable[[dict[str, Any]], Any]] = []
                for validate in (*captured_validators,
                                 None if tool.name in scoped else source.validate_arguments):
                    if validate is not None and not any(validate is previous for previous in selected):
                        selected.append(validate)
                return tuple(selected)

            async def validate_arguments(params: dict[str, Any]) -> None:
                for validate in validators():
                    checked = validate(params)
                    if inspect.isawaitable(checked):
                        await checked

            async def invoke(call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
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
                    if tool.name == "filesystem" and _filesystem_roots(ctx or {}) != state.filesystem_ceiling:
                        raise ValueError("Child filesystem execution constraints differ from original delegation")
                    return await tool.execute(call_id, params, cancel, on_update, ctx=ctx)
                finally:
                    executing.discard(admitted.token)
                    if admissions.get(call_id) is admitted:
                        admissions.pop(call_id)
            async def execute(call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                              on_update: Any = None, *, ctx: dict[str, Any] | None = None) -> AgentToolResult:
                if tool.name in scoped:
                    if cancel is not None and cancel.is_set():
                        raise asyncio.CancelledError
                    caller = asyncio.get_running_loop()
                    async def update(result: AgentToolResult) -> None:
                        if on_update is not None:
                            async def deliver() -> None:
                                await on_update(result)
                            await settle_on(caller, deliver())
                    return await settle_on(scope_owner.owner_loop,
                                           invoke(call_id, params, None, update, ctx=ctx))
                return await invoke(call_id, params, cancel, on_update, ctx=ctx)
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
        delegated_mcp = tuple(proxy for source, proxy in zip(grant.tools, delegated, strict=True)
                              if any(source is original for original in state.mcp_tools))
        def is_mcp_proxy(tool: AgentTool) -> bool:
            return any(tool is proxy for proxy in delegated_mcp)
        tools = tuple(tool for tool in delegated if not is_mcp_proxy(tool)) + role_tools
        mcp = McpRegistry.from_tools(delegated_mcp)
        repo = ToolRepo(*tools, builtin=False)

        def refresh_delegated_tools() -> None:
            nonlocal repo
            current = self.parent.capture_tools(None).tools if self.parent.live else ()
            selected_tools = [proxy for source, proxy in zip(grant.tools, delegated, strict=True)
                              if any(source is item for item in current)]
            # Keep exact original capability identities; a same-named replacement
            # never regains authority. Hooks/providers/UI do not supply tools.
            selected_tools.extend(tool for tool in role_tools if self.parent.live
                                  and self.capabilities.permits(spec.id, tool.name, (tool,)))
            replacement = ToolRepo(*(tool for tool in selected_tools if not is_mcp_proxy(tool)), builtin=False)
            mcp.replace_delegated_tools(tool for tool in selected_tools if is_mcp_proxy(tool))
            agent.sync_mcp_tools()
            replacement.publish(agent)
            repo = replacement
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
                refresh_delegated_tools()

        async def after(ctx: AfterToolCallContext, cancel: asyncio.Event | None = None) -> AfterToolCallResult | None:
            try:
                return await self.extensions.post(extension, ctx)
            finally:
                refresh_delegated_tools()

        agent: Agent
        async def context(messages: list[AgentMessage]) -> list[AgentMessage]:
            try:
                result = await self.extensions.context(extension, messages)
                return result
            finally:
                refresh_delegated_tools()

        async def input_transform(message: UserMessage, source: str,
                                  cancel: asyncio.Event | None = None) -> UserMessage | HandledInput:
            try:
                self.extensions.check_parent()
                return await extension.input(message, source, channel="child")
            finally:
                refresh_delegated_tools()

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
            if any(any(source is tool for tool in grant.tools) and (runtime.mcp is not state.mcp_registry
                    or parent_agent.mcp_registry is not state.mcp_registry
                    or not state.mcp_registry.is_current_tool(source)) for source in state.mcp_tools):
                raise ValueError('Original parent MCP delegation changed; no child model request')
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
            tools=repo.tools, mcp_registry=mcp, parent=self.parent,
            skills=skills, active_skills=skills.slugs,
            tool_ctx={**state.context, '_skill_repo': skills},
            transform_context=context, before_tool_call=before, after_tool_call=after, input_transform=input_transform)
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
        refresh_delegated_tools()
        return agent

    async def finish(self, spec: ChildSpec) -> None:
        self.snapshots.pop(spec.id, None)
        self.grants.pop(spec.id, None)
        self.scoped_tools.pop(spec.id, None)
        self.scope_owners.pop(spec.id, None)
        self.skill_grants.pop(spec.id, None)
        await self.extensions.close(spec)
