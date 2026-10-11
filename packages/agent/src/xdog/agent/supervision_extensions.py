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
from xdog.agent.supervision import ChildSpec
from xdog.agent.supervision_queued_input import Admit, Discard
from xdog.agent.supervision_store import ChildStore
from xdog.agent.types import AfterToolCallContext, AfterToolCallResult, BeforeToolCallContext, BeforeToolCallResult
from xdog.ai.types import ImageContent, TextContent
from xdog.ai.utils.validation import validate_tool_arguments


class ChildExtensions:
    def __init__(self, parent: ExtensionRuntime, store: ChildStore) -> None:
        self.parent, self.store = parent, store
        self._ui_lock = threading.RLock()
        self.owners: dict[str, ExtensionRuntime] = {}
        self.default_ui: tuple[str, Callable[[dict[str, Any]], None]] | None = None
        self._bindings: dict[str, tuple[str, Callable[[dict[str, Any]], None]]] = {}
        self._dialogs: dict[str, DialogBroker] = {}
        self._editor_write_tokens: set[str] = set()
        self._editor_write_generations: dict[str, int] = {}
        self._editor_write_handlers: dict[str, Any] = {}
        from xdog.agent.supervision_queued_input import ChildQueuedInputs
        self._queued_inputs = ChildQueuedInputs(self)

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

    def _publish_terminal(self, child: str, *, closing: bool = False) -> None:
        with self._ui_lock:
            binding, owner = self._bindings.get(child), self.owners.get(child)
            if binding is None or owner is None:
                return
            token, sink = binding
            info = {"active": owner.terminal_input_available and not closing,
                    "generation": owner.generation + int(closing),
                    "events": list(owner.terminal_events) if not closing else []}
        sink({"type": "child_terminal_registry", "child_id": child, "child_owner": token,
              "session_id": str(self.parent.context().get("session_id", "")), "terminal": info, "closing": closing})

    def terminal_metadata(self, token: str) -> dict[str, dict[str, Any]]:
        with self._ui_lock:
            return {child: {"active": owner.terminal_input_available, "generation": owner.generation,
                            "events": list(owner.terminal_events)}
                    for child, owner in self.owners.items()
                    if self._bindings.get(child) is not None and self._bindings[child][0] == token}

    def capture_terminal(self, child: str, token: str, generation: int
                         ) -> tuple[ExtensionRuntime, Callable[[], bool]]:
        if type(generation) is not int or not 0 <= generation <= 2**53:
            raise ValueError("Invalid child terminal generation")
        self.check_parent()
        with self._ui_lock:
            owner = self.owners.get(child)
            binding = self._bindings.get(child)
        def alive() -> bool:
            with self._ui_lock:
                broker = self._dialogs.get(child)
                return (owner is not None and self.owners.get(child) is owner
                        and self._bindings.get(child) == binding and binding is not None and binding[0] == token
                        and owner.generation == generation and not owner._closed
                        and (broker is None or not broker.has_pending))
        if owner is None or not alive():
            raise ValueError("Child terminal worker/UI owner changed")
        return owner, alive

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

    def custom_input_owner(self, child: str, identifier: str, token: str) -> bool:
        with self._ui_lock:
            binding, broker = self._bindings.get(child), self._dialogs.get(child)
            return (binding is not None and binding[0] == token and broker is not None
                    and broker.custom_input_owner(identifier))

    def request_custom(self, child: str, identifier: str, token: str, event: dict[str, Any]) -> bool:
        with self._ui_lock:
            if not self.contains(child, identifier, token):
                return False
            return self._dialogs[child].request_custom(identifier, event)

    def cancel_dialogs(self, token: str, supported: Callable[[dict[str, Any]], bool]) -> None:
        """Revoke unsupported pending dialogs even before their UI event was delivered."""
        with self._ui_lock:
            brokers = tuple(broker for child, broker in self._dialogs.items()
                            if self._bindings.get(child) is not None and self._bindings[child][0] == token)
        for broker in brokers:
            broker.cancel_where(lambda dialog: not supported(dialog))

    def editor_write_enabled(self, token: str) -> bool:
        with self._ui_lock:
            return token in self._editor_write_tokens

    def editor_write_generation(self, token: str) -> int:
        with self._ui_lock:
            return self._editor_write_generations.get(token, 0)

    def enable_editor_write(self, token: str, enabled: bool) -> None:
        with self._ui_lock:
            if enabled != (token in self._editor_write_tokens):
                self._editor_write_generations[token] = self._editor_write_generations.get(token, 0) + 1
            if enabled:
                self._editor_write_tokens.add(token)
            else:
                self._editor_write_tokens.discard(token)
            for child, binding in self._bindings.items():
                if binding[0] == token and child in self.owners:
                    self.owners[child].editor_write_handler = (
                        self._editor_write_handlers.get(child) if enabled else None)
                    if not enabled and child in self._dialogs:
                        self._dialogs[child].cancel_where(lambda dialog: dialog.get('operation') == 'propose')

    def enable_queued_input(self, token: str, enabled: bool) -> None:
        self._queued_inputs.enable(token, enabled)

    def set_queued_input_sender(self, token: str,
                                sender: Callable[[dict[str, Any]], Awaitable[bool | None]] | None) -> None:
        self._queued_inputs.set_sender(token, sender)

    def queued_input_generation(self, token: str) -> int:
        return self._queued_inputs.generation(token)

    def bind_queued_input(self, child: str, agent: Agent, *,
                          validate_images: Callable[[tuple[ImageContent, ...]], Awaitable[None]] | None = None,
                          queue_alive: Callable[[], bool] | None = None,
                          admit: Admit | None = None, discard: Discard | None = None) -> None:
        self._queued_inputs.bind(child, agent, validate_images=validate_images, queue_alive=queue_alive,
                                 admit=admit, discard=discard)

    def detach(self, token: str) -> None:
        self.enable_queued_input(token, False)
        self.set_queued_input_sender(token, None)
        self.enable_editor_write(token, False)
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
                        owner.editor_write_handler = None
                        owner.dialog_handler = None
                        owner.custom_handler = None
                        owner.custom_refresh_handler = None
                        owner.custom_control_handler = None


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
            def owned_publish(event: dict[str, Any]) -> None:
                publish(event)
                if event.get("type") in {"extension_ui", "extension_notice"}:
                    self._publish_terminal(spec.id)
            runtime.publish = owned_publish
            binding = self._bindings.get(spec.id)
            if binding is not None:
                token, sink = binding
                broker = DialogBroker()
                self._dialogs[spec.id] = broker
                session_id = str(self.parent.context().get("session_id", ""))
                broker.publish = lambda event: sink({**event,
                    "type": ("child_" + event["type"]
                            if event["type"].startswith(("extension_dialog", "extension_custom"))
                            else event["type"]),
                    "child_id": spec.id, "child_owner": token, "session_id": session_id,
                    **({'queued_input_generation': self.queued_input_generation(token)}
                       if event.get('dialog', {}).get('operation') == 'queue_input' else {})})
                async def ask(extension: str, value: dict[str, Any]) -> Any:
                    with self._ui_lock:
                        attached = self._bindings.get(spec.id) == binding
                    if not attached:
                        return validate_answer(value, None)
                    return await broker.ask(extension, value)
                async def custom(extension: str, value: dict[str, Any], render: Any) -> Any:
                    with self._ui_lock:
                        attached = self._bindings.get(spec.id) == binding
                    if not attached:
                        return None
                    return await broker.ask_custom(extension, value, render)
                runtime.custom_handler = custom
                runtime.custom_refresh_handler = broker.refresh_custom
                runtime.custom_control_handler = broker.control_custom
                async def editor_write(extension: str, value: dict[str, Any]) -> Any:
                    from xdog.agent.extensions.editor_request import reviewed
                    generation = self.editor_write_generation(token)
                    def live() -> bool:
                        with self._ui_lock:
                            return (self._bindings.get(spec.id) == binding and self.owners.get(spec.id) is runtime
                                and token in self._editor_write_tokens
                                and self.editor_write_generation(token) == generation
                                and not runtime._closed)
                    async def review(name: str, dialog: dict[str, Any]) -> Any:
                        return await ask(name, {**dialog, 'editor_write_generation': generation}) if live() else None
                    def send(event: dict[str, Any]) -> None:
                        if live() or event['type'].endswith('_discard'):
                            sink({**event, 'type': 'child_' + event['type'], 'child_id': spec.id,
                                  'child_owner': token, 'session_id': session_id,
                                  'editor_write_generation': generation})
                    async def commit(event: dict[str, Any]) -> None:
                        if live():
                            send(event)
                        else:
                            send({**event, 'type': 'extension_editor_write_discard'})
                    return await reviewed(extension, value, ask=review, commit=commit, discard=send)
                self._editor_write_handlers[spec.id] = editor_write
                if token in self._editor_write_tokens:
                    runtime.editor_write_handler = editor_write
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
        self._publish_terminal(spec.id)

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
        await self._queued_inputs.close(spec.id)
        self._publish_terminal(spec.id, closing=True)
        with self._ui_lock:
            self._editor_write_handlers.pop(spec.id, None)
            owner = self.owners.pop(spec.id, None)
            broker = self._dialogs.pop(spec.id, None)
            self._bindings.pop(spec.id, None)
        if broker is not None:
            broker.close()
        if owner is not None:
            owner.custom_handler = None
            owner.custom_refresh_handler = None
            owner.custom_control_handler = None
            owner.editor_write_handler = None
            owner.dialog_handler = None  # Cleanup cannot create a new question after releasing its UI owner.
            try:
                await owner.event("turn_end")
            finally:
                value = owner.entries.snapshot()
                try:
                    await owner.aclose()
                finally:
                    await asyncio.to_thread(self.store.save_entries, spec.id, value)
