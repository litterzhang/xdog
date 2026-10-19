"""Owned extension dialog futures; UI answers are not tool-permission decisions."""
from __future__ import annotations

import asyncio
import math
import threading
import uuid
from collections.abc import Callable
from typing import Any

from xdog.agent.extensions.session_context import (
    READ_ONLY_SESSION_OPERATIONS as READ_ONLY_SESSION_OPERATIONS,
)
from xdog.agent.extensions.session_context import (
    validate_session_answer,
    validate_session_request,
)

# Retired grants are stripped from worker context, never interpreted as authority.
REMOVED_HOST_CAPABILITIES = frozenset({
    'host_editor', 'host_editor_write', 'host_navigation', 'host_name_request',
    'host_active_tools_request', 'host_context_message', 'host_queued_input', 'host_compaction_request',
})


def validate_dialog(value: Any) -> dict[str, Any]:
    """Basic questions and read-only queries are the complete dialog vocabulary."""
    if not isinstance(value, dict):
        raise ValueError('Invalid extension dialog')
    kind = value.get('kind')
    if not isinstance(kind, str) or kind not in {'select', 'input', 'confirm', 'session', 'view'}:
        raise ValueError('Extension host mutation/control APIs and custom UI have been removed')
    fields = {'kind', 'id', 'title', 'message', 'timeout', 'extension'}
    if kind == 'session':
        validate_session_request(value)
    elif kind == 'view':
        from xdog.agent.extensions.views import request
        fields.update({'name', 'query'})
        if set(value) - fields:
            raise ValueError('View request cannot choose host routing or methods')
        request({'name': value.get('name'), 'query': value.get('query')})
    else:
        fields.update({'default', 'choices'})
        if kind in {'input', 'confirm'}:
            fields.add('provider_auth')  # Explicit auth ownership is checked separately by its runtime.
        if set(value) - fields:
            raise ValueError('Unsupported extension dialog fields; no host control grant')
    if not isinstance(value.get('id'), str) or not 0 < len(value['id']) <= 128:
        raise ValueError('Invalid extension dialog identity')
    if 'extension' in value and (not isinstance(value['extension'], str)
                                 or not 0 < len(value['extension'].encode()) <= 128):
        raise ValueError('Invalid extension dialog namespace')
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
            or len(set(choices)) != len(choices) or kind == "select" and not choices):
        raise ValueError("Invalid extension dialog choices")
    result = dict(value)
    if 'choices' in result:
        result['choices'] = list(choices)  # Caller mutation cannot change a painted question's allowed answers.
    return result


def validate_sdk_dialog(value: Any) -> dict[str, Any]:
    """SDK and general broker share one policy; there is no legacy control backdoor."""
    return validate_dialog(value)


def validate_answer(dialog: dict[str, Any], answer: Any) -> Any:
    dialog = validate_dialog(dialog)  # Even a canceled raw legacy packet is not a valid retained dialog.
    if answer is None:
        return False if dialog["kind"] == "confirm" else None
    if dialog["kind"] == "view":
        from xdog.agent.extensions.views import answer as view_answer
        return view_answer(answer)
    if dialog["kind"] == "session":
        return validate_session_answer(dialog, answer)
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

    @property
    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def contains(self, identifier: str) -> bool:
        with self._lock:
            return identifier in self._pending

    def input_owner(self, identifier: str) -> bool:
        """Only the most recently admitted live dialog may accept interactive input.

        Inspect broker admission, not delayed frontend publication; pending
        questions must protect their owner before a transport paints them.
        """
        with self._lock:
            live = [key for key, (_dialog, _loop, future) in self._pending.items() if not future.done()]
            return bool(live) and live[-1] == identifier


    async def ask(self, extension: str, value: dict[str, Any]) -> Any:
        dialog = validate_dialog(value)
        if dialog["kind"] == "view":
            raise ValueError("Read-only views use their host grant, not UI approval")
        return await self._ask(extension, dialog)

    async def ask_command_menu(self, extension: str, value: dict[str, Any]) -> Any:
        """Admit host-stamped read-only menu identity, never plugin dialog fields."""
        from xdog.agent.extensions.command_catalog import invocation_scope

        packet = dict(value)
        identity = invocation_scope(packet.pop('command_menu', None))
        dialog = validate_dialog(packet)
        if identity is None or dialog.get('kind') != 'session' or dialog.get('operation') != 'commands':
            raise ValueError('Owned read-only command menu identity required')
        return await self._ask(extension, dialog, command_menu=identity)

    async def _ask(self, extension: str, dialog: dict[str, Any], *,
                   command_menu: dict[str, str] | None = None) -> Any:
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
            self._pending[identifier] = dialog, loop, future
        try:
            # Trusted routing metadata is publication-only. Keep the worker
            # vocabulary and stored answer validation free of host-only fields.
            published = {**dialog, **({'command_menu': dict(command_menu)} if command_menu is not None else {})}
            self.publish({"type": "extension_dialog", "extension": extension, "dialog": published})
            async with asyncio.timeout(float(dialog.get("timeout", 300))):
                return validate_answer(dialog, await future)
        except TimeoutError:
            return validate_answer(dialog, None)
        finally:
            with self._lock:
                self._pending.pop(identifier, None)
            self.publish({"type": "extension_dialog_closed", "id": identifier})

    def resolve(self, identifier: str, answer: Any) -> bool:
        with self._lock:
            item = self._pending.get(identifier)
        if item is None:
            return False
        dialog, loop, future = item
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
