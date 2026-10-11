"""Owned extension dialog futures; UI answers are not tool-permission decisions."""
from __future__ import annotations

import asyncio
import contextvars
import math
import threading
import uuid
from collections.abc import Callable
from typing import Any

from xdog.agent.extensions.custom import answer as custom_answer
from xdog.agent.extensions.custom import options as custom_options
from xdog.agent.extensions.editor import validate_editor_answer, validate_editor_request
from xdog.agent.extensions.forms import validate_fields, validate_form_answer
from xdog.agent.extensions.session_context import validate_session_answer, validate_session_request

# Only an owned host renderer can establish a parent. Plugin-supplied parent IDs
# are discarded, and the same broker/extension/future is independently checked.
render_owner: contextvars.ContextVar[tuple[Any, str] | None] = contextvars.ContextVar(
    "custom_render_owner", default=None)

def validate_dialog(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict)
            or value.get("kind") not in ("select", "input", "confirm", "form", "editor", "session", "custom")
            or not isinstance(value.get("id"), str) or not 0 < len(value["id"]) <= 128):
        raise ValueError("Invalid extension dialog")
    if (value.get('operation') in {'name', 'rename', 'navigate', 'active_tools', 'select_tools', 'compact', 'message',
                                   'queue_input', 'pending_messages', 'context_message'}
            and value['kind'] != 'session'):
        raise ValueError('Session metadata/navigation operations require a session dialog')
    for key, limit in (("title", 1000), ("message", 8000), ("default", 16000)):
        text = value.get(key, "")
        if not isinstance(text, str) or len(text.encode("utf-8")) > limit:
            raise ValueError("Invalid extension dialog text")
    if not value.get("title", "").strip():
        raise ValueError("Extension dialog title must not be empty")
    timeout = value.get("timeout", 300)
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0.1 <= timeout <= 600:
        raise ValueError("Invalid extension dialog deadline")
    choices = value.get("choices", [])
    if (not isinstance(choices, list) or len(choices) > 32
            or any(not isinstance(item, str) or not item or len(item.encode("utf-8")) > 1000 for item in choices)
            or len(set(choices)) != len(choices) or value["kind"] == "select" and not choices):
        raise ValueError("Invalid extension dialog choices")
    result = dict(value)
    if value["kind"] == "form":
        result["fields"] = validate_fields(value.get("fields"))
    elif value["kind"] == "editor":
        validate_editor_request(value)
    elif value["kind"] == "custom":
        result.update(custom_options(value))
        view_id = value.get("view_id")
        if (not isinstance(view_id, str) or not 0 < len(view_id) <= 128
                or not view_id.isascii() or not all(char.isalnum() or char in "_-" for char in view_id)):
            raise ValueError("Invalid custom-view identity")
    elif value["kind"] == "session":
        validate_session_request(value)
        if value.get("operation") == "navigate":
            result["target"] = dict(value["target"])
    return result


def validate_answer(dialog: dict[str, Any], answer: Any) -> Any:
    if answer is None:
        return False if dialog["kind"] == "confirm" else None
    if dialog["kind"] == "custom":
        return custom_answer(answer)
    if dialog["kind"] == "session":
        return validate_session_answer(dialog, answer)
    if dialog["kind"] == "editor":
        return validate_editor_answer(dialog, answer)
    if dialog["kind"] == "form":
        return validate_form_answer(dialog, answer)
    if dialog["kind"] == "confirm" and type(answer) is bool:
        return answer
    if dialog["kind"] == "select" and isinstance(answer, str) and answer in dialog["choices"]:
        return answer
    if dialog["kind"] == "input" and isinstance(answer, str) and len(answer.encode("utf-8")) <= 16000:
        return answer
    raise ValueError("Invalid extension dialog answer")


class DialogBroker:
    def __init__(self) -> None:
        self.publish: Callable[[dict[str, Any]], None] = lambda event: None
        self._pending: dict[str, tuple[dict[str, Any], asyncio.AbstractEventLoop, asyncio.Future[Any]]] = {}
        self._lock = threading.Lock()
        self._closed = False
        from xdog.agent.extensions.custom_broker import CustomFrames
        self.custom = CustomFrames(self)

    @property
    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def contains(self, identifier: str) -> bool:
        with self._lock:
            return identifier in self._pending

    def input_owner(self, identifier: str) -> bool:
        """Only the most recently admitted live dialog may accept interactive input.

        Inspect broker admission, not delayed frontend publication; pending nested
        questions must protect their owner before a transport paints them.
        """
        with self._lock:
            live = [key for key, (_dialog, _loop, future) in self._pending.items() if not future.done()]
            return bool(live) and live[-1] == identifier

    def custom_input_owner(self, identifier: str) -> bool:
        """Check live overlay presentation as well as nested dialog input ownership."""
        with self._lock:
            live = [key for key, (_dialog, _loop, future) in self._pending.items() if not future.done()]
            item = self._pending.get(identifier)
            if not live or live[-1] != identifier or item is None:
                return False
            dialog = item[0]
            if dialog['kind'] != 'custom':
                return False
            overlay = dialog.get('overlay')
            if overlay is None:
                return True
            state = dialog.get('overlay_state', {'hidden': False,
                'capturing': not overlay.get('non_capturing', False)})
            return state['hidden'] is False and state['capturing'] is True

    async def ask_custom(self, extension: str, value: dict[str, Any], render: Any) -> Any:
        return await self.ask(extension, value, renderer=render)

    def request_custom(self, identifier: str, event: dict[str, Any]) -> bool:
        return self.custom.request(identifier, event)

    def refresh_custom(self, extension: str, view_id: str) -> bool:
        """Find only this extension's live owned view; no caller-selected host ID."""
        with self._lock:
            identifier = next((key for key, (dialog, _loop, future) in self._pending.items()
                               if dialog["kind"] == "custom" and dialog.get("extension") == extension
                               and dialog.get("view_id") == view_id and not future.done()), None)
        return identifier is not None and self.custom.refresh(identifier)

    def control_custom(self, extension: str, view_id: str, sequence: int, action: str) -> bool:
        if type(sequence) is not int or not 0 < sequence < 2**53 or action not in ('hide', 'show', 'focus', 'unfocus'):
            return False
        with self._lock:
            owned = next(((key, dialog) for key, (dialog, _loop, future) in self._pending.items()
                          if dialog['kind'] == 'custom' and dialog.get('extension') == extension
                          and dialog.get('view_id') == view_id and dialog.get('live_overlay')
                          and dialog.get('overlay') is not None and not future.done()), None)
            if owned is None:
                return False
            identifier, dialog = owned
            previous = dialog.get('overlay_state', {'sequence': 0, 'hidden': False,
                                                     'capturing': not dialog['overlay'].get('non_capturing', False)})
            if sequence <= previous['sequence']:
                return False
            if not self.custom.revoke(identifier):
                return False
            state = {**previous, 'sequence': sequence}
            if action in ('hide', 'show', 'focus'):
                state['hidden'] = action == 'hide'
            if action in ('focus', 'unfocus'):
                state['capturing'] = action == 'focus'
            dialog['overlay_state'] = state
        self.publish({'type': 'extension_custom_control', 'extension': extension, 'id': identifier,
                      'state': dict(state)})
        return True

    async def ask(self, extension: str, value: dict[str, Any], *, renderer: Any = None) -> Any:
        dialog = validate_dialog(value)
        if (dialog["kind"] == "custom") != (renderer is not None):
            raise ValueError("Custom dialogs require an owned renderer; other dialogs cannot borrow it")
        if renderer is not None and not callable(renderer):
            raise ValueError("Custom renderer must be callable")
        dialog.pop("parent_id", None)
        dialog.pop("overlay_state", None)  # Presentation state is host-owned, never worker supplied.
        parent_id = ""
        owner = render_owner.get()
        if owner is not None and owner[0] is self:
            parent_id = owner[1]
        identifier = uuid.uuid4().hex
        dialog["extension"] = extension
        dialog["id"] = identifier  # Host-generated IDs cannot be guessed from plugin callback counters.
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        with self._lock:
            if self._closed:
                return validate_answer(dialog, None)
            if len(self._pending) >= 8:
                raise ValueError("Extension dialog queue is full")
            if parent_id:
                parent = self._pending.get(parent_id)
                if (parent is None or parent[2].done() or parent[0]["kind"] != "custom"
                        or parent[0].get("extension") != extension):
                    raise ValueError("Nested dialog parent is not live/owned")
                depth, ancestor = 1, parent_id
                while self._pending[ancestor][0].get("parent_id"):
                    depth += 1
                    ancestor = self._pending[ancestor][0]["parent_id"]
                if depth >= 4:
                    raise ValueError("Nested dialog depth exceeded")
                dialog["parent_id"] = parent_id
            self._pending[identifier] = dialog, loop, future
        if parent_id:
            self.custom.suspend(parent_id, True)
        if renderer is not None:
            self.custom.add(identifier, extension, loop, future, renderer, key_events=dialog["key_events"])
        try:
            self.publish({"type": "extension_dialog", "extension": extension, "dialog": dialog})
            async with asyncio.timeout(float(dialog.get("timeout", 300))):
                result = await future
            if (result is True and dialog["kind"] == "session"
                    and dialog.get("operation") in {"navigate", "rename", "select_tools", "compact", "message"}):
                # A transport acknowledgment is not proof of the callback's value.
                # Publish on the owning loop, before closure/callback completion.
                self.publish({"type": "extension_dialog_accepted", "id": identifier,
                              "extension": extension, "operation": dialog["operation"]})
            return result
        except TimeoutError:
            return validate_answer(dialog, None)
        finally:
            with self._lock:
                self._pending.pop(identifier, None)
                descendants = tuple(key for key, item in self._pending.items()
                                    if item[0].get("parent_id") == identifier)
            for child in descendants:
                self.resolve(child, None)
            if parent_id:
                self.custom.suspend(parent_id, False)
            await self.custom.remove(identifier)
            self.publish({"type": "extension_dialog_closed", "id": identifier})

    def resolve(self, identifier: str, answer: Any) -> bool:
        return self._resolve(identifier, answer, custom_completion=False)

    def _resolve(self, identifier: str, answer: Any, *, custom_completion: bool) -> bool:
        with self._lock:
            item = self._pending.get(identifier)
        if item is None:
            return False
        dialog, loop, future = item
        if dialog["kind"] == "custom" and answer is not None and not custom_completion:
            raise ValueError("Custom completion requires a live painted frame/input proposal")
        answer = validate_answer(dialog, answer)
        def finish() -> None:
            if not future.done():
                future.set_result(answer)
        try:
            loop.call_soon_threadsafe(finish)
        except RuntimeError:
            return False
        return True

    def cancel_extension(self, extension: str) -> None:
        with self._lock:
            ids = tuple(key for key, value in self._pending.items() if value[0].get("extension") == extension)
        for identifier in ids:
            self.resolve(identifier, None)

    def cancel_where(self, predicate: Callable[[dict[str, Any]], bool]) -> None:
        """Cancel host-owned pending kinds, including not-yet-delivered UI publications."""
        with self._lock:
            ids = tuple(key for key, item in self._pending.items() if predicate(dict(item[0])))
        for identifier in ids:
            self.resolve(identifier, None)

    def cancel_all(self) -> None:
        with self._lock:
            ids = tuple(self._pending)
        for identifier in ids:
            self.resolve(identifier, None)

    def close(self) -> None:
        self._closed = True
        self.cancel_all()
