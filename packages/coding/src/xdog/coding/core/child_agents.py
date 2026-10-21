"""Coding-owned child factory: independent approvals/extensions, dynamic parent gates."""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from xdog.agent import Agent, AgentConfig, AgentMessage, BeforeToolCallContext, BeforeToolCallResult
from xdog.agent.access import read_only_call
from xdog.agent.capabilities.children import ChildPort
from xdog.agent.core import AgentState
from xdog.agent.history import history_points
from xdog.agent.messages import dicts_to_messages, messages_to_dicts
from xdog.agent.supervision import RESERVED_TOOLS, ChildSpec, ChildSupervisor, child_prompt
from xdog.agent.supervision_config import ChildPresets
from xdog.agent.supervision_extensions import ChildExtensions
from xdog.agent.supervision_model import resolve_child_model
from xdog.agent.supervision_store import ChildStore
from xdog.agent.types import AfterToolCallContext, AfterToolCallResult, HandledInput
from xdog.ai.types import UserMessage
from xdog.coding.core.permissions import PermissionDecision, PermissionManager, PermissionRequest

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession



class CodingChildren:
    def __init__(self, session: AgentSession) -> None:
        self.session = session
        self.token = uuid.uuid4().hex
        branch = session.session_data.active_branch or "head"
        self.store = ChildStore.scoped(session.session_manager._dir, session.session_id, branch)
        self.capabilities = ChildPort(session.session_id, branch, lambda: session.capabilities.tools)
        self.snapshots: dict[str, AgentState] = {}
        self.tool_limits: dict[str, tuple[str, ...] | None] = {}
        self.extensions = ChildExtensions(session.extensions, self.store)
        self.permissions: dict[str, PermissionManager] = {}
        self.approver: Callable[[str, PermissionRequest], None] | None = None
        self.approval_alive: Callable[[], bool] | None = None
        self.preset_error = ""
        try:
            presets = ChildPresets(session.settings.child_presets)
        except (ValueError, TypeError) as exc:
            presets = ChildPresets()
            self.preset_error = "Invalid child preset catalog: " + str(exc)
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
            on_event=self.extensions.event, records=records, presets=presets)
        self.capabilities.supervisor = self.supervisor
        self.supervisor.diagnostic = diagnostic

    def capture(self, spec: ChildSpec) -> None:
        if self.session._context_changes:
            raise RuntimeError("Session context/policy is changing; refresh before starting child work")
        agent = self.session.agent
        selection = agent.active_tool_selection
        allowed = spec.tools
        if selection is not None:
            ceiling = tuple(name for name in selection if name not in RESERVED_TOOLS)
            if allowed is not None and not set(allowed) <= set(ceiling):
                raise ValueError('Child tool profile exceeds captured parent active-tool selection')
            allowed = ceiling if allowed is None else allowed
        allowed = self.capabilities.capture(spec, agent.active_tools, allowed)
        self.tool_limits[spec.id] = allowed
        self.snapshots[spec.id] = replace(agent.state, tools=agent.active_tools)
        self.extensions.reserve(spec.id)

    async def create(self, spec: ChildSpec, publish: Callable[[dict[str, Any]], None]) -> Agent:
        session = self.session
        state = self.snapshots[spec.id]  # Snapshot taken at admission, before asynchronous startup/persistence.
        allowed_tools = self.tool_limits[spec.id]
        role_tools = await self.capabilities.tools(spec)
        if role_tools:
            allowed_tools = (*(allowed_tools or ()), *(tool.name for tool in role_tools))
        # Child extension context and every later registration sync keep this
        # admission-time ceiling, not a later parent selection or worker tool list.
        extension_spec = replace(spec, tools=allowed_tools)
        points = history_points(state.messages)
        prefix = state.messages[:points[-1].index + 1] if spec.inherit_context and points else ()
        inherited = dicts_to_messages(json.loads(json.dumps(messages_to_dicts(list(prefix)))))
        selected = await asyncio.to_thread(resolve_child_model, session.agent.stream_fn, state.model,
            session.agent.options,
            spec.config(), session.context_window,
            session.agent.supports_tool_calls, parent_max_prompt_tokens=session.agent.max_prompt_tokens)
        inherited = selected.history(inherited)
        extension = await self.extensions.create(extension_spec, publish, model=selected.id,
                                                 thinking=selected.options.thinking or "off", initialize=False)
        manager = PermissionManager(session.permissions.mode)
        self.permissions[spec.id] = manager
        def request(value: PermissionRequest) -> None:
            if self.approver is None:
                manager.resolve(value.id, "deny")
            else:
                self.approver(spec.id, value)
        manager.set_request_handler(request)
        tools = tuple(tool for tool in state.tools
                      if tool.name not in RESERVED_TOOLS and self.capabilities.inherited(tool)
                      and tool.name not in session.extensions.aliases
                      and tool.name not in {tool.name for tool in role_tools})
        tools = (*tools, *role_tools, *extension.tools())
        async def before(ctx: BeforeToolCallContext, cancel: asyncio.Event | None = None
                         ) -> BeforeToolCallResult | None:
            try:
                def bound_call() -> bool:
                    return self.capabilities.permits(spec.id, ctx.tool_call.name, ctx.context.tools or ())
                if (spec.access == "read-only" or session.access.state.read_only) and not (
                        bound_call() or read_only_call(ctx.tool_call.name, ctx.args)):
                    return BeforeToolCallResult(block=True, reason="Child/parent access is read-only")
                blocked = await self.extensions.pre(extension, ctx)
                if blocked is not None:
                    return blocked
                if (spec.access == "read-only" or session.access.state.read_only) and not (
                        bound_call() or read_only_call(ctx.tool_call.name, ctx.args)):
                    return BeforeToolCallResult(block=True, reason="Child/parent access is read-only")
                if bound_call():
                    return None  # Actual host-bound context/report capability, not a name exemption.
                manager.set_mode(session.permissions.mode)
                approval_alive = self.approval_alive
                result = await manager.before_tool_call(ctx, cancel)
                if (result is None and approval_alive is not None and not approval_alive()
                        and session.permissions.mode != "allow-all"
                        and not read_only_call(ctx.tool_call.name, ctx.args)):
                    manager.clear_session_rules()
                    return BeforeToolCallResult(block=True, reason="Child approval UI owner changed during review")
                if session.permissions.mode == "deny" and not read_only_call(ctx.tool_call.name, ctx.args):
                    return BeforeToolCallResult(block=True, reason="Parent permission policy changed to deny")
                if session.access.state.read_only and not read_only_call(ctx.tool_call.name, ctx.args):
                    return BeforeToolCallResult(block=True, reason="Parent entered read-only scope during approval")
                return result
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
                if session._base_context_transform is not None:
                    messages = await session._base_context_transform(messages)
                result = await self.extensions.context(extension, messages)
                return result
            finally:
                extension.sync_tools(agent, allowed=allowed_tools)

        async def input_transform(message: UserMessage, source: str,
                                  cancel: asyncio.Event | None = None) -> UserMessage | HandledInput:
            try:
                self.extensions.check_parent()
                if session._base_input_transform is not None:
                    value = await session._base_input_transform(message, source, cancel)
                    if isinstance(value, HandledInput):
                        return value
                    message = value
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
            compaction=selected.compaction(child_stream)), tools=tools,
            tool_ctx=session.agent.tool_context, transform_context=context,
            before_tool_call=before, after_tool_call=after, input_transform=input_transform)
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
        self.snapshots.pop(spec.id, None)
        self.tool_limits.pop(spec.id, None)
        manager = self.permissions.pop(spec.id, None)
        if manager is not None:
            manager.close()
        await self.extensions.close(spec)

    def resolve(self, child_id: str, request_id: str, decision: PermissionDecision) -> bool:
        manager = self.permissions.get(child_id)
        return manager is not None and manager.resolve(request_id, decision)

    def has_pending(self, child_id: str, request_id: str) -> bool:
        manager = self.permissions.get(child_id)
        return manager is not None and manager.has_pending(request_id)

    def abort(self) -> None:
        for manager in tuple(self.permissions.values()):
            manager.deny_all()
        self.supervisor.abort()

    def close(self) -> None:
        self.session._retire_capabilities()
        for manager in tuple(self.permissions.values()):
            manager.close()
        self.supervisor.close()
