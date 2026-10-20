"""Process-local trusted Python extension API; hosts retain tool/turn authority."""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import re
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from xdog.agent.extensions.dialogs import validate_answer, validate_sdk_dialog
from xdog.agent.extensions.hooks import EVENTS
from xdog.agent.extensions.provider_auth import ProviderAuth, bind_callback
from xdog.agent.extensions.provider_oauth import OAuthBinding, ProviderOAuth
from xdog.agent.extensions.shortcuts import shortcut_key
from xdog.agent.extensions.tool_result import _UNSET, make_result
from xdog.agent.mcp.manager import _schema


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", value):
        raise ValueError("Extension contribution IDs must be 1-32 ASCII letters/digits/underscore")
    return value


async def invoke(callback: Callable[..., Any], *args: Any) -> Any:
    result = callback(*args)
    return await result if inspect.isawaitable(result) else result


class ExtensionAPI:
    """Register hooks/tools/commands and contribute bounded declarative UI text.

    Extension code is trusted code with the process user's permissions. This API
    cannot switch host planning/approval modes or start a second model loop.
    """
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.tools: dict[str, tuple[str, dict[str, Any], Callable[..., Any]]] = {}
        self._tool_revision = 0
        self._tool_ids: dict[str, str] = {}
        self.commands: dict[str, tuple[str, Callable[..., Any]]] = {}
        self._command_revision = 0
        self._command_public_names: dict[str, str] = {}
        self._command_completions: dict[str, tuple[str, Callable[..., Any]]] = {}
        self.shortcuts: list[dict[str, str]] = []
        self.events: dict[str, list[Callable[..., Any]]] = {}
        self._hook_revision = 0
        self.pre: list[Callable[..., Any]] = []
        self.post: list[Callable[..., Any]] = []
        self.ui: list[dict[str, Any]] = []
        self.entries: list[dict[str, Any]] = []
        self.context_hooks: list[Callable[..., Any]] = []
        self.input_hooks: list[Callable[..., Any]] = []
        self.providers: dict[str, tuple[dict[str, Any], Callable[..., Any]]] = {}
        self._provider_revision = 0
        self._call_id: contextvars.ContextVar[str] = contextvars.ContextVar("extension_call", default="")
        self._has_command_menu = contextvars.ContextVar("extension_command_menu", default=False)
        self._has_catalog = contextvars.ContextVar("extension_command_catalog", default=False)
        self._has_session = contextvars.ContextVar("extension_command_session", default=False)
        self._command_menu_session = contextvars.ContextVar("extension_command_menu_session", default="")
        self._session_name: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
            "extension_session_name", default=None)
        self._inspection: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
            "extension_invocation_inspection", default=None)
        self._tool_inventory: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
            "extension_invocation_tools", default=None)
        self._has_pending_messages = contextvars.ContextVar("extension_pending_messages", default=False)
        self._has_views = contextvars.ContextVar("extension_host_views", default=False)
        self._has_ui: contextvars.ContextVar[bool] = contextvars.ContextVar("extension_has_ui", default=False)
        self._dialog_events: dict[str, asyncio.Event] = {}
        self._dialogs: dict[str, tuple[dict[str, Any], asyncio.Future[Any]]] = {}
        self._retired_dialogs: dict[str, deque[str]] = {}

    @property
    def has_ui(self) -> bool:
        """Invocation-scoped availability hint, never authority to open a host surface."""
        return self._has_ui.get()

    async def select(self, title: str, choices: list[str], *, message: str = "", timeout: float = 300) -> str | None:
        value = await self._ask("select", title, message, choices=choices, timeout=timeout)
        return value if isinstance(value, str) else None

    async def input(self, title: str, *, message: str = "", default: str = "", timeout: float = 300) -> str | None:
        value = await self._ask("input", title, message, default=default, timeout=timeout)
        return value if isinstance(value, str) else None

    async def confirm(self, title: str, message: str = "", *, timeout: float = 300) -> bool:
        return bool(await self._ask("confirm", title, message, timeout=timeout))


    async def read_view(self, name: str, *, query: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Read an explicitly granted host snapshot, independently of UI permission."""
        from xdog.agent.extensions.views import answer, request
        packet = request({"name": name, "query": {} if query is None else query})
        value = await self._ask("view", "Read host view", "Read-only owner-bound data",
                                _host_read=True, **packet)
        return answer(value)

    async def get_session(self, *, timeout: float = 30) -> dict[str, Any] | None:
        """Current host metadata only; no transcript or session mutation."""
        if not self._has_session.get():
            return None
        value = await self._ask("session", "Inspect command session", "Read-only current owner metadata",
                                operation="snapshot", timeout=timeout)
        return value if isinstance(value, dict) else None

    async def get_session_name(self, *, timeout: float = 30) -> str | None:
        """Live command-owner name or a captured invocation snapshot; None means unavailable."""
        if not self._has_session.get():
            captured = self._session_name.get()
            return captured['name'] if captured is not None else None
        value = await self._ask('session', 'Inspect session name', 'Read-only current owner metadata',
                                operation='name', timeout=timeout)
        return value['name'] if isinstance(value, dict) else None


    async def get_command_catalog(self, *, query: str = '', offset: int = 0, revision: str = '',
                                  timeout: float = 30) -> dict[str, Any] | None:
        """Read this invocation's explicitly bound host menu; never execute or borrow another owner."""
        from xdog.agent.extensions.command_catalog import request
        options = {'query': query, 'offset': offset, 'revision': revision}
        request(options)
        if (not self._has_command_menu.get() and not self._has_session.get()):
            return None
        result = await self._ask('session', 'Inspect terminal command menu',
            'Read-only discovery, not execution approval or remote availability', operation='commands',
            timeout=timeout, **options)
        if isinstance(result, dict) and result['session_id'] != self._command_menu_session.get():
            raise ValueError('Command menu reply belongs to another session')
        return result if isinstance(result, dict) else None

    async def get_model(self, *, timeout: float = 30) -> dict[str, Any] | None:
        """Owner-projected public model snapshot, or live interactive-command inspection."""
        return await self._inspect_session("model", timeout)

    async def get_context_usage(self, *, timeout: float = 30) -> dict[str, Any] | None:
        """Owned response measurement with age; never a borrowed parent count or token estimate."""
        return await self._inspect_session("usage", timeout)

    async def get_tool_inventory(self, *, query: str = '', offset: int = 0,
                                 revision: str = '') -> dict[str, Any] | None:
        """Read paged owning-agent definitions captured at invocation admission, not permissions.

        Selection metadata, when present, distinguishes configured and active tools; it is
        not a permission grant. The snapshot does not refresh during an awaited callback.
        """
        from xdog.agent.extensions.tool_inventory import page
        return page(self._tool_inventory.get(), query=query, offset=offset, revision=revision)

    async def get_active_tools(self, *, timeout: float = 30) -> dict[str, Any] | None:
        """Command host read or original callback's captured names/revision; no permission grant.

        Non-command data comes from the existing invocation tool inventory, not a
        new live dialog or another host's queue/body/selection proxy.
        """
        if not self._has_session.get():
            from xdog.agent.extensions.active_tools import answer
            current = self._tool_inventory.get()
            if current is None or 'selection_revision' not in current or not current.get('session_id'):
                return None
            return answer({'session_id': current['session_id'], 'revision': current['selection_revision'],
                           'configured': [row['name'] for row in current['tools']], 'active': current['active_tools']})
        value = await self._ask('session', 'Inspect active tools', 'Configured and selected names only',
                                operation='active_tools', timeout=timeout)
        return value if isinstance(value, dict) else None


    async def _inspect_session(self, operation: str, timeout: float) -> dict[str, Any] | None:
        if not self._has_session.get():
            current = self._inspection.get()
            if current is None:
                return None
            from xdog.agent.extensions.inspection import validate
            return validate(operation, current[operation])
        value = await self._ask("session", "Inspect active model/context",
                                "Read-only; no model call or permission changes",
                                operation=operation, timeout=timeout)
        return value if isinstance(value, dict) else None


    async def has_pending_messages(self, *, timeout: float = 30) -> bool | None:
        """Read the original Agent's live pending boolean; None means unavailable.

        No queued text, images, IDs, ordering, another host's queue or write grant
        is returned. This is a host read at call time, not an invocation snapshot.
        """
        if not self._has_pending_messages.get():
            return None
        value = await self._ask('session', 'Inspect pending input', 'Owning Agent boolean only',
                                operation='pending_messages', timeout=timeout, _host_read=True)
        return value if type(value) is bool else None


    async def list_sessions(self, *, query: str = "", offset: int = 0, revision: str = "",
                            timeout: float = 30) -> dict[str, Any] | None:
        return await self._catalog("sessions", "", "", query, offset, revision, timeout)

    async def get_session_tree(self, session_id: str = "", *, branch_id: str = "", query: str = "",
                               offset: int = 0, revision: str = "", timeout: float = 30) -> dict[str, Any] | None:
        return await self._catalog("tree", session_id, branch_id, query, offset, revision, timeout)

    async def _catalog(self, scope: str, session_id: str, branch_id: str, query: str,
                       offset: int, revision: str, timeout: float) -> dict[str, Any] | None:
        if not self._has_catalog.get():
            return None
        result = await self._ask("session", "Read host navigation catalog", "Read-only metadata/checkpoint previews",
            operation="catalog", scope=scope, session_id=session_id, branch_id=branch_id,
            query=query, offset=offset, revision=revision, timeout=timeout)
        return result if isinstance(result, dict) else None


    async def _ask(self, kind: str, title: str, message: str, **options: Any) -> Any:
        _host_read = options.pop("_host_read", False) is True
        dialog = validate_sdk_dialog({"kind": kind, "title": title, "message": message,
                                  "id": uuid.uuid4().hex, **options})
        host_read = _host_read and (
            kind == "view" and self._has_views.get()
            or self._has_pending_messages.get() and kind == "session"
            and options.get("operation") == "pending_messages")
        if kind == "view" and not host_read or not self._has_ui.get() and not host_read:
            return validate_answer(dialog, None)
        call_id = self._call_id.get()
        if not call_id or call_id in self._dialogs:
            raise ValueError("Dialog does not have an available callback owner")
        future = asyncio.get_running_loop().create_future()
        self._dialogs[call_id] = dialog, future
        self._dialog_events[call_id].set()
        try:
            async with asyncio.timeout(float(dialog.get("timeout", 300))):
                return await future
        except TimeoutError:
            return validate_answer(dialog, None)
        finally:
            self._dialogs.pop(call_id, None)
            self._retired_dialogs.setdefault(call_id, deque(maxlen=32)).append(dialog["id"])
            self._dialog_events[call_id].clear()

    def _reply(self, call_id: str, dialog_id: str, value: Any) -> None:
        item = self._dialogs.get(call_id)
        if item is None or item[0]["id"] != dialog_id:
            raise ValueError("Extension dialog owner changed")
        dialog, future = item
        answer = validate_answer(dialog, value)
        self._dialog_events[call_id].clear()
        if not future.done():
            future.set_result(answer)

    def _hook(self, rows: list[Callable[..., Any]], callback: Callable[..., Any]) -> Callable[[], None]:
        if not callable(callback) or len(rows) >= 32:
            raise ValueError("Extension hooks require callable callbacks, at most 32 per kind/event")
        async def registered(*args: Any) -> Any:
            return await invoke(callback, *args)
        self._advance_hook_revision()
        rows.append(registered)
        def dispose() -> None:
            if registered in rows:
                self._advance_hook_revision()
                rows.remove(registered)  # Registration identity, not callback equality or a future replacement.
        return dispose

    def _advance_hook_revision(self) -> None:
        if self._hook_revision >= 2**53:
            raise ValueError("Extension hook revision capacity reached")
        self._hook_revision += 1

    def hook_snapshot(self) -> dict[str, Any]:
        return {"revision": self._hook_revision, "events": [key for key, rows in self.events.items() if rows],
                "pre": bool(self.pre), "post": bool(self.post), "context": bool(self.context_hooks),
                "input": bool(self.input_hooks)}

    def on(self, event: str, callback: Callable[..., Any]) -> Callable[[], None]:
        if not isinstance(event, str) or event not in EVENTS:
            raise ValueError("Unknown extension lifecycle event")
        return self._hook(self.events.setdefault(event, []), callback)

    def context(self, callback: Callable[..., Any]) -> Callable[[], None]:
        """Transform ephemeral messages; disposer is owned, idempotent and does not mutate history."""
        return self._hook(self.context_hooks, callback)

    def on_input(self, callback: Callable[..., Any]) -> Callable[[], None]:
        """Submitted-input listener; removal cannot grant tool/command/model dispatch authority."""
        return self._hook(self.input_hooks, callback)

    def register_provider(self, name: str, models: list[dict[str, Any]], callback: Callable[..., Any], *,
                          replace: bool = False, auth: ProviderAuth | None = None,
                          oauth: ProviderOAuth | None = None) -> Callable[[], None]:
        """Declare/update an owned provider; old disposers cannot remove replacements.

        Optional auth resolves only in this worker at stream admission and adds a
        fourth ProviderCredential callback argument; metadata never contains auth secrets.
        OAuth bindings add explicit reviewed login/refresh and native-vault lifecycle;
        credential values and callback objects remain private to this worker.
        """
        from xdog.agent.extensions.providers import registration
        info = registration(name, models)
        key = info['name']
        if (not callable(callback) or type(replace) is not bool or key in self.providers and not replace
                or key not in self.providers and len(self.providers) >= 4):
            raise ValueError('Provider callbacks require distinct names/callables and at most four registrations')
        if oauth is not None:
            if not isinstance(oauth, ProviderOAuth) or auth is not None:
                raise ValueError('Provider OAuth requires a private callback binding, separate from env auth')
            bound = OAuthBinding(self, info, callback, oauth, lambda: self.providers.get(key) is entry)
            info['oauth'] = bound.status()
            callback = bound
        elif auth is not None:
            callback = bind_callback(callback, auth, lambda: self.providers.get(key) is entry)
        info['registration_id'] = uuid.uuid4().hex
        entry = info, callback
        self._advance_provider_revision()
        self.providers[key] = entry
        def unregister() -> None:
            if self.providers.get(key) is entry:
                self._advance_provider_revision()
                self.providers.pop(key)
        return unregister

    def unregister_provider(self, name: str) -> bool:
        if not isinstance(name, str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', name) is None:
            raise ValueError('Invalid provider name')
        key = name.lower()
        if key not in self.providers:
            return False
        self._advance_provider_revision()
        self.providers.pop(key)
        return True

    def _advance_provider_revision(self) -> None:
        if self._provider_revision >= 2**53:
            raise ValueError('Provider registration revision capacity reached')
        self._provider_revision += 1


    def append_entry(self, kind: str, data: Any) -> None:
        from xdog.agent.extensions.state import validate_entries
        if len(self.entries) >= 16:
            raise ValueError("Extension entry response capacity exceeded")
        row = {"kind": identifier(kind), "data": data}
        validate_entries({"entry": [{"id": str(index), **item} for index, item in enumerate([*self.entries, row])]})
        self.entries.append(row)

    def drain_entries(self) -> list[dict[str, Any]]:
        rows, self.entries = self.entries, []
        return rows

    def before_tool(self, callback: Callable[..., Any]) -> Callable[[], None]:
        return self._hook(self.pre, callback)

    def after_tool(self, callback: Callable[..., Any]) -> Callable[[], None]:
        return self._hook(self.post, callback)

    def _advance_tool_revision(self) -> None:
        if self._tool_revision >= 2**53:
            raise ValueError("Extension tool revision capacity reached")
        self._tool_revision += 1

    def tool_snapshot(self) -> dict[str, Any]:
        return {"revision": self._tool_revision, "tools": [
            {"name": name, "registration_id": self._tool_ids[name]} for name in self.tools]}

    def tool(self, name: str, description: str, parameters: dict[str, Any], callback: Callable[..., Any], *,
             replace: bool = False) -> Callable[[], None]:
        name = identifier(name)
        if (not callable(callback) or type(replace) is not bool or not isinstance(description, str)
                or len(description.encode()) > 4000 or name in self.tools and not replace
                or name not in self.tools and len(self.tools) >= 32
                or any(key != name and key.lower() == name.lower() for key in self.tools)):
            raise ValueError("Invalid/duplicate/capacity-limited extension tool")
        entry = (description, _schema(parameters), callback)
        self._advance_tool_revision()
        self.tools[name] = entry
        token = uuid.uuid4().hex
        self._tool_ids[name] = token
        def dispose() -> None:
            if self._tool_ids.get(name) == token:
                self._advance_tool_revision()
                self.tools.pop(name)
                self._tool_ids.pop(name)
        return dispose

    def result(self, content: list[dict[str, Any]] | None = None, *, structured: Any = _UNSET,
               details: Any = _UNSET, is_error: bool = False) -> dict[str, Any]:
        """Build bounded text/image/JSON tool data; no command execution or implicit file reads."""
        return make_result(content, structured=structured, details=details, is_error=is_error)

    def _advance_command_revision(self) -> None:
        if self._command_revision >= 2**53:
            raise ValueError("Extension command revision capacity reached")
        self._command_revision += 1

    def command_snapshot(self) -> dict[str, Any]:
        return {"revision": self._command_revision,
                "commands": [{"name": name, "description": row[0],
                    **({'public_name': self._command_public_names[name]} if name in self._command_public_names else {}),
                    **({'completion_id': self._command_completions[name][0]}
                       if name in self._command_completions else {})} for name, row in self.commands.items()],
                "shortcuts": [dict(row) for row in self.shortcuts]}

    def command(self, name: str, description: str, callback: Callable[..., Any], *,
                replace: bool = False, complete: Callable[..., Any] | None = None,
                public_name: str | None = None) -> Callable[[], None]:
        name = identifier(name)
        if public_name is not None:
            from xdog.agent.extensions.commands import public_name as validate_public_name
            public_name = validate_public_name(public_name)
            if any(key != name and value == public_name for key, value in self._command_public_names.items()):
                raise ValueError('Duplicate public extension command name')
        if (not callable(callback) or complete is not None and not callable(complete)
                or type(replace) is not bool or not isinstance(description, str)
                or len(description.encode()) > 1000 or name in self.commands and not replace
                or name not in self.commands and len(self.commands) >= 32
                or any(key != name and key.lower() == name.lower() for key in self.commands)):
            raise ValueError("Invalid/duplicate/capacity-limited extension command")
        entry = (description, callback)
        self._advance_command_revision()
        self.commands[name] = entry
        self._command_public_names.pop(name, None)
        if public_name is not None:
            self._command_public_names[name] = public_name
        self._command_completions.pop(name, None)
        if complete is not None:
            self._command_completions[name] = uuid.uuid4().hex, complete
        def dispose() -> None:
            if self.commands.get(name) is entry:
                self._advance_command_revision()
                self.commands.pop(name)
                self._command_public_names.pop(name, None)
                self._command_completions.pop(name, None)
                self.shortcuts[:] = [row for row in self.shortcuts if row["name"].lower() != name.lower()]
        return dispose

    def shortcut(self, name: str, key: str, description: str, callback: Callable[..., Any], *,
                 public_name: str | None = None) -> Callable[[], None]:
        """Owned command+binding; removal cannot consume typing or mutate host key policy."""
        name, key = identifier(name), shortcut_key(key)
        if len(self.shortcuts) >= 32 or any(row["key"] == key for row in self.shortcuts):
            raise ValueError("Extension shortcut capacity/duplicate key")
        if not isinstance(description, str) or len(description.encode()) > 1000:
            raise ValueError("Invalid extension shortcut description")
        command_dispose = self.command(name, description, callback, public_name=public_name)
        row = {"name": name, "key": key, "description": description}
        self.shortcuts.append(row)
        def dispose() -> None:
            if any(current is row for current in self.shortcuts):
                self._advance_command_revision()
                self.shortcuts[:] = [current for current in self.shortcuts if current is not row]
            command_dispose()
        return dispose

    def notify(self, text: str) -> None:
        self._ui("notice", "", [text])

    def status(self, key: str, text: str | None) -> None:
        key = identifier(key)
        if text is None:
            self._ui("status", key, [], clear=True)
        else:
            self._ui("status", key, [text])


    def clear_status(self, key: str) -> None:
        self.status(key, None)


    def _ui(self, kind: str, key: str, lines: list[str], *, clear: bool = False) -> None:
        if (len(self.ui) >= 32 or not isinstance(lines, list) or len(lines) > 8
                or any(not isinstance(line, str) or len(line.encode("utf-8")) > 1000 for line in lines)):
            raise ValueError("Extension UI contribution limit exceeded")
        self.ui.append({"kind": kind, "key": key, "lines": list(lines), **({"clear": True} if clear else {})})

    def drain_ui(self) -> list[dict[str, Any]]:
        rows, self.ui = self.ui, []
        return rows
