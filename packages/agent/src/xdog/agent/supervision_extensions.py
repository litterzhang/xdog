"""Independent extension workers/state for children; no borrowed parent metadata proxies."""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.agent import Agent
from xdog.agent.core import AgentMessage
from xdog.agent.events import AgentEvent, MessageEndEvent, ToolExecutionEndEvent
from xdog.agent.extensions.dialogs import DialogBroker, validate_answer
from xdog.agent.extensions.runtime import ExtensionRuntime, silent_extension_ui
from xdog.agent.extensions.state import ExtensionEntries
from xdog.agent.supervision import ChildSpec, settle_on
from xdog.agent.supervision_store import ChildStore
from xdog.agent.types import AfterToolCallContext, AfterToolCallResult, BeforeToolCallContext, BeforeToolCallResult
from xdog.ai.types import TextContent
from xdog.ai.utils.validation import validate_tool_arguments


class ChildExtensions:
    def __init__(self, parent: ExtensionRuntime, store: ChildStore) -> None:
        self.parent, self.store = parent, store
        self._ui_lock = threading.RLock()
        self.owners: dict[str, ExtensionRuntime] = {}
        self.default_ui: tuple[str, Callable[[dict[str, Any]], None]] | None = None
        self._bindings: dict[str, tuple[str, Callable[[dict[str, Any]], None]]] = {}
        self._dialogs: dict[str, DialogBroker] = {}
        self._pending_readers: dict[str, Callable[[], Awaitable[bool | None]]] = {}

    @property
    def has_pending_dialogs(self) -> bool:
        """Authoritative child UI admission, including unpublished nested views."""
        with self._ui_lock:
            return any(broker.has_pending for broker in self._dialogs.values())

    def reserve(self, child: str) -> None:
        with self._ui_lock:
            binding = self.default_ui
            if binding is not None:
                self._bindings[child] = binding


    def contains(self, child: str, identifier: str, token: str) -> bool:
        with self._ui_lock:
            binding, broker = self._bindings.get(child), self._dialogs.get(child)
            return binding is not None and binding[0] == token and broker is not None and broker.contains(identifier)

    def answer(self, child: str, identifier: str, token: str, value: Any) -> bool:
        with self._ui_lock:
            if not self.contains(child, identifier, token):
                return False
            return self._dialogs[child].resolve(identifier, value)

    def input_owner(self, child: str, identifier: str, token: str) -> bool:
        with self._ui_lock:
            binding, broker = self._bindings.get(child), self._dialogs.get(child)
            return (binding is not None and binding[0] == token and broker is not None
                    and broker.input_owner(identifier))


    def cancel_dialogs(self, token: str, supported: Callable[[dict[str, Any]], bool]) -> None:
        """Revoke unsupported pending dialogs even before their UI event was delivered."""
        with self._ui_lock:
            brokers = tuple(broker for child, broker in self._dialogs.items()
                            if self._bindings.get(child) is not None and self._bindings[child][0] == token)
        for broker in brokers:
            broker.cancel_where(lambda dialog: not supported(dialog))

    def bind_pending_messages(self, child: str, agent: Agent) -> None:
        """Read only this original Agent's live boolean on its owning loop."""
        runtime = self.owners.get(child)
        if runtime is None or child in self._pending_readers:
            raise ValueError('Unknown or already bound child pending-message owner')
        loop = asyncio.get_running_loop()
        session_id = str(runtime.context().get('session_id', ''))

        async def pending() -> bool | None:
            async def read() -> bool | None:
                with self._ui_lock:
                    if (self._pending_readers.get(child) is not pending or self.owners.get(child) is not runtime
                            or runtime._closed or self.parent._closed
                            or str(runtime.context().get('session_id', '')) != session_id):
                        return None
                return bool(agent.pending_inputs()) or agent.has_queued_messages()
            if loop.is_closed() or not loop.is_running():
                return None
            try:
                return await settle_on(loop, read())
            except (ValueError, RuntimeError):
                return None

        self._pending_readers[child] = pending
        runtime.pending_messages = pending

    def detach(self, token: str) -> None:
        with self._ui_lock:
            if self.default_ui is not None and self.default_ui[0] == token:
                self.default_ui = None
            for child, binding in tuple(self._bindings.items()):
                if binding[0] == token:
                    self._bindings.pop(child, None)
                    broker = self._dialogs.get(child)
                    if broker is not None:
                        broker.close()
                    owner = self.owners.get(child)
                    if owner is not None:
                        owner.dialog_handler = None


    def check_parent(self) -> None:
        if any(row["state"] == "failed" for row in self.parent.snapshot()["extensions"]):
            raise ValueError("Parent extension validation unavailable; unload/reload failed extensions explicitly")

    async def create(self, spec: ChildSpec, publish: Callable[[dict[str, Any]], None],
                     *, model: str = "", thinking: str = "off", initialize: bool = True) -> ExtensionRuntime:
        self.check_parent()
        names = tuple(row["name"] for row in self.parent.snapshot()["extensions"] if row["state"] != "unloaded")
        inherited = json.loads(json.dumps(self.parent.entries.snapshot())) if spec.inherit_context else {}
        entries = ExtensionEntries(inherited, lambda value: self.store.save_entries(spec.id, value))
        runtime = ExtensionRuntime(names, self.parent.root, self.parent.cwd, settings=self.parent.settings,
            context=lambda: {**self.parent.context(), "child_id": spec.id, "child_access": spec.access,
                             "model": model or self.parent.context().get("model", ""), "thinking": thinking,
                             "child_config": spec.config().to_dict()},
            entries=entries)
        with self._ui_lock:
            self.owners[spec.id] = runtime
            runtime.publish = publish
            binding = self._bindings.get(spec.id)
            if binding is not None:
                token, sink = binding
                broker = DialogBroker()
                self._dialogs[spec.id] = broker
                session_id = str(self.parent.context().get("session_id", ""))
                broker.publish = lambda event: sink({**event,
                    "type": ("child_" + event["type"]
                            if event["type"].startswith(("extension_dialog",))
                            else event["type"]),
                    "child_id": spec.id, "child_owner": token, "session_id": session_id})
                async def ask(extension: str, value: dict[str, Any]) -> Any:
                    with self._ui_lock:
                        attached = self._bindings.get(spec.id) == binding
                    if not attached:
                        return validate_answer(value, None)
                    return await broker.ask(extension, value)
                runtime.dialog_handler = ask
                runtime.dialog_cancel = broker.cancel_extension
        if initialize:
            await self.start(spec, runtime)
        return runtime

    async def start(self, spec: ChildSpec, runtime: ExtensionRuntime) -> None:
        if self.owners.get(spec.id) is not runtime:
            raise ValueError("Child extension invocation owner changed")
        await runtime.initialize()
        await runtime.event("turn_start", {"user_input": spec.task})

    async def context(self, owner: ExtensionRuntime, messages: list[AgentMessage]) -> list[AgentMessage]:
        self.check_parent()
        with silent_extension_ui():
            messages = await self.parent.transform(messages)
        return await owner.transform(messages)

    async def pre(self, owner: ExtensionRuntime, ctx: BeforeToolCallContext) -> BeforeToolCallResult | None:
        try:
            self.check_parent()
            # Parent hooks retain authority, including in-memory validation
            # established by its commands; a fresh worker alone cannot inherit it.
            # Only child TOOL executors/state/dialog workers are independent.
            if self.parent.enabled:
                with silent_extension_ui():
                    effective = await self.parent.pre(ctx.tool_call.name, dict(ctx.args))
                if effective is None:
                    raise ValueError("Parent extension vetoed this child tool call")
                tool = next(tool for tool in ctx.context.tools or () if tool.name == ctx.tool_call.name)
                if not validate_tool_arguments(effective, tool.parameters).valid:
                    raise ValueError("Parent extension rewrote child arguments outside the tool schema")
                ctx.args.clear()
                ctx.args.update(effective)
            if owner.enabled:
                await owner.event("tool_start", {"tool_name": ctx.tool_call.name})
                effective = await owner.pre(ctx.tool_call.name, dict(ctx.args))
                if effective is None:
                    raise ValueError("Child extension vetoed this tool call")
                tool = next(tool for tool in ctx.context.tools or () if tool.name == ctx.tool_call.name)
                if not validate_tool_arguments(effective, tool.parameters).valid:
                    raise ValueError("Child extension rewrote parameters outside the tool schema")
                ctx.args.clear()
                ctx.args.update(effective)
        except Exception as exc:
            return BeforeToolCallResult(block=True, reason=str(exc))
        return None

    async def post(self, owner: ExtensionRuntime, ctx: AfterToolCallContext) -> AfterToolCallResult | None:
        if ctx.is_error or not owner.enabled or any(not isinstance(part, TextContent) for part in ctx.result.content):
            return None
        text = "\n".join(part.text for part in ctx.result.content if isinstance(part, TextContent))
        try:
            result = await owner.post(ctx.tool_call.name, ctx.args, text)
        except Exception:
            return None
        return AfterToolCallResult(content=(TextContent(text=result),)) if result != text else None

    async def event(self, spec: ChildSpec, event: AgentEvent) -> None:
        owner = self.owners.get(spec.id)
        if owner is None:
            return
        if isinstance(event, MessageEndEvent):
            await owner.event("message_end", {"role": getattr(event.message, "role", "custom")})
        elif isinstance(event, ToolExecutionEndEvent):
            await owner.event("tool_end", {"tool_name": event.tool_name, "is_error": event.is_error})

    async def close(self, spec: ChildSpec) -> None:
        with self._ui_lock:
            self._pending_readers.pop(spec.id, None)
            owner = self.owners.pop(spec.id, None)
            broker = self._dialogs.pop(spec.id, None)
            self._bindings.pop(spec.id, None)
        if broker is not None:
            broker.close()
        if owner is not None:
            owner.pending_messages = None
            owner.dialog_handler = None  # Cleanup cannot create a new question after releasing its UI owner.
            try:
                await owner.event("turn_end")
            finally:
                value = owner.entries.snapshot()
                try:
                    await owner.aclose()
                finally:
                    await asyncio.to_thread(self.store.save_entries, spec.id, value)
