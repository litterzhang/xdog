"""Claw session-owned children with inherited tool allowlists and isolated extensions."""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from xdog.agent import Agent, AgentConfig, AgentMessage, BeforeToolCallContext, BeforeToolCallResult
from xdog.agent.access import read_only_call
from xdog.agent.core import AgentState, AgentTool, AgentToolResult
from xdog.agent.history import history_points
from xdog.agent.messages import dicts_to_messages, messages_to_dicts
from xdog.agent.supervision import RESERVED_TOOLS, ChildSpec, ChildSupervisor, child_prompt, settle_on
from xdog.agent.supervision_extensions import ChildExtensions
from xdog.agent.supervision_model import resolve_child_model
from xdog.agent.supervision_store import ChildStore
from xdog.agent.types import AfterToolCallContext, AfterToolCallResult, HandledInput
from xdog.ai.types import UserMessage

if TYPE_CHECKING:
    from xdog.claw.core.runtime.session import AgentSession


class ClawChildren:
    supervisor: ChildSupervisor
    def __init__(self, session: AgentSession) -> None:
        self.session = session
        branch = session._store.active_branch(session.meta.session_id) or "head"
        self.store = ChildStore.scoped(session._store._sessions_dir, session.meta.session_id, branch)
        self._task_profiles: dict[str, tuple[str, str, str]] = {}
        self._task_children: dict[str, tuple[str, str, str]] = {}
        self._review_profiles: dict[str, tuple[str, int]] = {}
        self._review_children: dict[str, tuple[str, int]] = {}
        self.snapshots: dict[str, AgentState] = {}
        self.tool_limits: dict[str, tuple[str, ...] | None] = {}
        self.extensions = ChildExtensions(session.extensions, self.store)
        diagnostic = ""
        try:
            records = self.store.load()
        except (ValueError, TypeError, OSError, UnicodeError) as exc:
            records = []
            diagnostic = "Child catalog unavailable; repair saved records before delegation: " + str(exc)[:500]
        self.supervisor = ChildSupervisor(self.create, self.store.save, capture=self.capture, begin=self.store.begin,
            persist_inputs=self.store.save_inputs, persist_return=self.store.save_return,
            persist_admission=self.store.save_admission, archive_compaction=self.store.archive_compaction,
            forget=self.store.forget, finalize=self.finish,
            on_event=self.extensions.event, records=records, presets=session._runtime.child_presets)
        self.supervisor.diagnostic = diagnostic
        def wake_parent(event: dict[str, Any]) -> None:
            loop = self.session._host_loop
            if loop is not None and not loop.is_closed() and event.get('type') == 'child_status':
                def receive() -> None:
                    manager = self.session._runtime.goal_manager
                    try:
                        manager.record_children_status(event.get('snapshot', {}).get('children', []))
                    finally:
                        manager._wake_fn()
                loop.call_soon_threadsafe(receive)
        self.supervisor.publish = wake_parent

    async def start_goal_task(self, goal_id: str, task_id: str, attempt_id: str,
                              prompt: str, access: str) -> str:
        from xdog.claw.core.planning.goal_manager import role_instructions
        label = 'task-' + uuid.uuid4().hex
        self._task_profiles[label] = (goal_id, task_id, attempt_id)
        try:
            return await self.supervisor.start(prompt, access=access, inherit_context=False,
                config={'label': label, 'instructions': role_instructions('task-worker.md')})
        finally:
            self._task_profiles.pop(label, None)

    async def start_goal_review(self, goal_id: str, revision: int, prompt: str) -> str:
        from xdog.claw.core.planning.goal_manager import role_instructions
        label = 'review-' + uuid.uuid4().hex
        self._review_profiles[label] = (goal_id, revision)
        try:
            return await self.supervisor.start(prompt, access='inherit', inherit_context=False,
                config={'label': label, 'instructions': role_instructions('goal-reviewer.md')})
        finally:
            self._review_profiles.pop(label, None)

    def capture(self, spec: ChildSpec) -> None:
        worker = self._task_profiles.get(spec.label)
        if worker is not None:
            self._task_children[spec.id] = worker
        profile = self._review_profiles.get(spec.label)
        if profile is not None:
            self._review_children[spec.id] = profile
        agent = self.session.agent
        selection = agent.active_tool_selection
        allowed = spec.tools
        if selection is not None:
            ceiling = tuple(name for name in selection if name not in RESERVED_TOOLS)
            if allowed is not None and not set(allowed) <= set(ceiling):
                raise ValueError('Child tool profile exceeds captured parent active-tool selection')
            allowed = ceiling if allowed is None else allowed
        if worker is not None:
            allowed = tuple(tool.name for tool in agent.active_tools
                            if tool.name not in RESERVED_TOOLS | {'goal', 'eyes'})
        if profile is not None:
            allowed = tuple(tool.name for tool in agent.active_tools
                            if tool.name in {'filesystem', 'bash', 'current_time'})
        self.tool_limits[spec.id] = allowed
        self.snapshots[spec.id] = replace(agent.state, tools=agent.active_tools)
        self.extensions.reserve(spec.id)

    async def create(self, spec: ChildSpec, publish: Callable[[dict[str, Any]], None]) -> Agent:
        session, runtime = self.session, self.session._runtime
        state = self.snapshots[spec.id]
        allowed_tools = self.tool_limits[spec.id]
        worker = self._task_children.get(spec.id)
        if worker is not None:
            if session._host_loop is None:
                raise RuntimeError('Task worker requires the group host loop')
            async def bind_task() -> None:
                runtime.goal_manager.bind_worker(*worker, spec.id)
            await settle_on(session._host_loop, bind_task())
            allowed_tools = (*allowed_tools, 'task_report') if allowed_tools is not None else None
        review = self._review_children.get(spec.id)
        if review is not None:
            if session._host_loop is None:
                raise RuntimeError('Reviewer requires the existing group host loop')
            async def bind() -> None:
                runtime.goal_manager.bind_reviewer(review[0], review[1], spec.id)
            await settle_on(session._host_loop, bind())
            allowed_tools = (*allowed_tools, 'goal_review') if allowed_tools is not None else None
        # Child extension context and every later registration sync keep this
        # admission-time ceiling, not a later parent selection or worker tool list.
        extension_spec = replace(spec, tools=allowed_tools)
        points = history_points(state.messages)
        prefix = state.messages[:points[-1].index + 1] if spec.inherit_context and points else ()
        inherited = dicts_to_messages(json.loads(json.dumps(messages_to_dicts(list(prefix)))))
        selected = await asyncio.to_thread(resolve_child_model, session.agent.stream_fn, state.model,
            session.agent.options,
            spec.config(), runtime.context_window,
            session.agent.supports_tool_calls, parent_max_prompt_tokens=session.agent.max_prompt_tokens)
        inherited = selected.history(inherited)
        extension = await self.extensions.create(extension_spec, publish, model=selected.id,
                                                 thinking=selected.options.thinking or "off", initialize=False)
        tools = tuple(tool for tool in state.tools
                      if tool.name not in RESERVED_TOOLS | {'goal', 'eyes'}
                      and tool.name not in session.extensions.aliases)
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
        # These tools own group/channel/memory/goal/skill services rather than
        # independent subprocess/file operations. Execute on the original host
        # loop while preserving child-loop update and cancellation ownership.
        services = {"group_message", "memory", "skill"}
        tools = tuple(host_owned(tool) if tool.name in services else tool for tool in tools)
        if worker is not None:
            from xdog.ai.types import TextContent
            async def report(call_id: str, params: dict[str, Any], cancel: Any = None,
                             on_update: Any = None, ctx: Any = None) -> AgentToolResult:
                async def write() -> None:
                    await runtime.goal_manager.write_report(runtime.group.id, *worker, spec.id, params)
                if session._host_loop is None:
                    raise RuntimeError('Group host loop unavailable')
                await settle_on(session._host_loop, write())
                return AgentToolResult(content=(TextContent(text='Report saved atomically; not accepted/completed.'),))
            tools = (*tools, AgentTool(name='task_report', description=(
                'Atomically save your assigned attempt report; identity and path are host-bound. '
                'submitted is a proposal, not task acceptance or Goal completion.'),
                parameters={'type': 'object', 'additionalProperties': False,
                    'required': ['revision', 'state', 'summary', 'evidence', 'blockers'], 'properties': {
                        'revision': {'type': 'integer', 'minimum': 1},
                        'state': {'type': 'string', 'enum': ['running', 'submitted', 'blocked']},
                        'summary': {'type': 'string'}, 'evidence': {'type': 'array', 'items': {'type': 'string'}},
                        'blockers': {'type': 'array', 'items': {'type': 'string'}}}}, execute=report))
        if review is not None:
            from xdog.ai.types import TextContent
            async def verdict(call_id: str, params: dict[str, Any], cancel: Any = None,
                              on_update: Any = None, ctx: Any = None) -> AgentToolResult:
                async def submit() -> None:
                    runtime.goal_manager.receive_review(review[0], review[1], spec.id, params)
                if session._host_loop is None:
                    raise RuntimeError('Group host loop is unavailable')
                await settle_on(session._host_loop, submit())
                return AgentToolResult(content=(TextContent(
                    text='Review recorded; stop now. Not a Goal completion ACK.'),))
            tools = (*tools, AgentTool(name='goal_review', description=(
                'Submit your independent per-condition findings, then stop. This tool does not complete the Goal.'),
                parameters={'type': 'object', 'required': ['verdict', 'conditions', 'summary'],
                    'additionalProperties': False, 'properties': {
                        'verdict': {'type': 'string', 'enum': ['passed', 'continue', 'blocked']},
                        'conditions': {'type': 'array', 'items': {'type': 'object',
                            'required': ['condition', 'status', 'evidence', 'reason'],
                            'additionalProperties': False, 'properties': {
                                'condition': {'type': 'string'},
                                'status': {'type': 'string', 'enum': ['passed', 'failed', 'blocked']},
                                'evidence': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1},
                                'reason': {'type': 'string'}}}}, 'summary': {'type': 'string'}}}, execute=verdict))
        async def before(ctx: BeforeToolCallContext, cancel: asyncio.Event | None = None
                         ) -> BeforeToolCallResult | None:
            try:
                report_only = worker is not None and ctx.tool_call.name == 'task_report'
                if (session.access.state.read_only or spec.access == "read-only" and not report_only
                        ) and not read_only_call(ctx.tool_call.name, ctx.args):
                    return BeforeToolCallResult(block=True, reason="Child/parent access is read-only")
                blocked = await self.extensions.pre(extension, ctx)
                if blocked is not None:
                    return blocked
                safe = read_only_call(ctx.tool_call.name, ctx.args)
                if (session.access.state.read_only or spec.access == "read-only" and not report_only) and not safe:
                    return BeforeToolCallResult(block=True, reason="Child/parent access is read-only")
                if ctx.tool_call.name in extension.aliases:
                    if extension.tool_owner(ctx.tool_call.name) not in runtime.extension_allow_tools:
                        return BeforeToolCallResult(
                            block=True, reason="Child extension tool use disabled by parent policy")
                if any(tool.name == ctx.tool_call.name for tool in runtime.mcp.tools()):
                    if not runtime.mcp.allowed(ctx.tool_call.name):
                        return BeforeToolCallResult(block=True, reason="Child MCP tool use disabled by parent policy")
                return None
            finally:
                extension.sync_tools(agent, allowed=allowed_tools)

        async def after(ctx: AfterToolCallContext, cancel: asyncio.Event | None = None) -> AfterToolCallResult | None:
            try:
                return await self.extensions.post(extension, ctx)
            finally:
                extension.sync_tools(agent, allowed=allowed_tools)

        agent: Agent
        async def context(messages: list[AgentMessage]) -> list[AgentMessage]:
            try:
                result = await self.extensions.context(extension, messages)
                return result
            finally:
                extension.sync_tools(agent, allowed=allowed_tools)

        async def input_transform(message: UserMessage, source: str,
                                  cancel: asyncio.Event | None = None) -> UserMessage | HandledInput:
            try:
                self.extensions.check_parent()
                return await extension.input(message, source, channel="child")
            finally:
                extension.sync_tools(agent, allowed=allowed_tools)

        from xdog.agent.extensions.providers import ExtensionProviderStream
        base_stream = session.agent.stream_fn
        if isinstance(base_stream, ExtensionProviderStream):
            base_stream = base_stream.base
        child_stream = selected.stream(ExtensionProviderStream(extension, base_stream))
        agent = Agent(child_stream,
            config=AgentConfig(model=selected.id,
            system_prompt=selected.prompt(child_prompt(state.system_prompt, spec)),
            tool_execution=session.agent.tool_execution,
            context_window=selected.context_window, max_prompt_tokens=selected.max_prompt_tokens,
            supports_tool_calls=selected.supports_tool_calls, options=selected.options,
            compaction=selected.compaction(child_stream)),
            tools=(*tools, *extension.tools()), tool_ctx={
                key: value for key, value in session.agent.tool_context.items()
                if key not in {'_goal_agent', '_goal_manager'}},
            transform_context=context, before_tool_call=before, after_tool_call=after, input_transform=input_transform)
        if allowed_tools is not None:
            allowed = set(allowed_tools)
            agent.set_tools(tuple(tool for tool in agent.state.tools if tool.name in allowed))
        agent.replace_messages(inherited)
        from xdog.agent.extensions.inspection import capture
        inspection_session = str(extension.context().get("session_id", ""))
        def inspection() -> dict[str, Any] | None:
            if (extension._closed or self.extensions.parent._closed
                    or extension.context().get("session_id") != inspection_session):
                return None
            metadata = (extension.provider_model(agent.state.model) if agent.state.model.startswith("ext-")
                        else selected.metadata)
            return capture(agent, inspection_session, spec.id, metadata, usage_floor=len(inherited))
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
        inventory_tools = (*tools, *extension.tools())
        agent.set_tools(tuple(tool for tool in inventory_tools if allowed_tools is None or tool.name in allowed_tools))
        return agent

    async def finish(self, spec: ChildSpec) -> None:
        self._task_children.pop(spec.id, None)
        self._review_children.pop(spec.id, None)
        self.snapshots.pop(spec.id, None)
        self.tool_limits.pop(spec.id, None)
        await self.extensions.close(spec)
