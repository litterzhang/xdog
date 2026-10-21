"""Session-owned trusted extension processes using the existing bounded stdio owner."""
from __future__ import annotations

import asyncio
import base64
import contextvars
import inspect
import json
import re
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

from xdog.agent.async_work import run_owned, settle_cleanup, wait_owned
from xdog.agent.core import AgentMessage, AgentTool, AgentToolResult
from xdog.agent.cross_loop import submit_remote
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.extensions.commands import declaration as command_declaration
from xdog.agent.extensions.commands import external_name
from xdog.agent.extensions.context import decode_context
from xdog.agent.extensions.dialogs import REMOVED_HOST_CAPABILITIES, validate_answer, validate_sdk_dialog
from xdog.agent.extensions.hooks import FLAGS
from xdog.agent.extensions.hooks import declaration as hook_declaration
from xdog.agent.extensions.input import InputRejected, decision, input_text
from xdog.agent.extensions.location import ExtensionRoot
from xdog.agent.extensions.native_commands import Binding as NativeCommandBinding
from xdog.agent.extensions.native_commands import Commands as NativeCommandDefinitions
from xdog.agent.extensions.native_commands import NativeCommands
from xdog.agent.extensions.native_commands import prepare as native_command_binding
from xdog.agent.extensions.shortcuts import registrations, snapshot_rows
from xdog.agent.extensions.state import ExtensionEntries
from xdog.agent.extensions.tool_result import details_snapshot, host_result
from xdog.agent.extensions.tools import declaration as tool_declaration
from xdog.agent.extensions.transfer import BLOCK, ContextTransfer, owned_spool
from xdog.agent.extensions.views import ViewRegistry
from xdog.agent.extensions.worker import manifest
from xdog.agent.mcp import McpManager
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.manager import McpTool
from xdog.agent.mcp.stdio import MAX_FRAME, McpError, StdioClient
from xdog.agent.messages import messages_to_dicts
from xdog.agent.types import HandledInput
from xdog.ai.types import ImageContent, Model, TextContent, UserMessage

if TYPE_CHECKING:
    from xdog.agent.extensions.compaction import CompactionDecision

T = TypeVar("T")
_tool_registration: contextvars.ContextVar[str] = contextvars.ContextVar("extension_tool_registration", default="")
_dialogs_allowed: contextvars.ContextVar[bool] = contextvars.ContextVar("extension_host_dialogs", default=True)


@contextmanager
def silent_extension_ui() -> Iterator[None]:
    """Keep inherited validation authoritative without borrowing another run's UI lease."""
    token = _dialogs_allowed.set(False)
    try:
        yield
    finally:
        _dialogs_allowed.reset(token)


def names(value: Any) -> tuple[str, ...]:
    if (not isinstance(value, (list, tuple)) or len(value) > 16
            or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", name) for name in value)
            or len({name.lower() for name in value}) != len(value)):
        raise ValueError("extensions must be at most 16 distinct trusted extension IDs")
    return tuple(value)


class _Client(StdioClient):
    def __init__(self, config: McpServerConfig, owner: ExtensionRuntime) -> None:
        super().__init__(config, owner.cwd)
        self.owner = owner
        self._provider_snapshot: tuple[int, list[dict[str, Any]]] | None = None
        self._hook_snapshot: dict[str, Any] | None = None
        self._command_snapshot: dict[str, Any] | None = None
        self._tool_snapshot: dict[str, Any] | None = None
        self._discovering_tools = False
        self._bootstrap_ui: list[dict[str, Any]] = []
        self._ui_admitted = False
        self._inventory_cache_supported = False
        self._inventories: dict[str, None] = {}


    def _accept_ui(self, rows: Any) -> None:
        current = self.owner._manager._clients.get(self.config.name)
        if not self._ui_admitted:
            if (current is self or current is None and self.state == "connecting") and not self.owner._closed:
                # Preserve setup contributions until this actual process is admitted.
                if not isinstance(rows, list) or len(self._bootstrap_ui) + len(rows) > 32:
                    raise ValueError("Extension setup UI exceeds its capacity")
                self._bootstrap_ui.extend(rows)
            return
        if current is not self:
            return
        if self._bootstrap_ui:
            pending, self._bootstrap_ui = self._bootstrap_ui, []
            self.owner.accept_ui(self.config.name, pending, client=self)
        self.owner.accept_ui(self.config.name, rows, client=self)

    async def request(self, method: str, params: dict[str, Any], *, version: str = "") -> dict[str, Any]:
        from xdog.agent.extensions.invocation import InvocationLease, _current
        original = self.owner.context()
        session_id, child_id = str(original.get('session_id', '')), str(original.get('child_id', ''))
        generation = self.owner.generation
        def alive() -> bool:
            context = self.owner.context()
            return (not self.owner._closed and self.owner.generation == generation
                and self.owner._manager._clients.get(self.config.name) is self
                and self.config.name not in self.owner._failed | self.owner._unloaded
                and str(context.get('session_id', '')) == session_id and str(context.get('child_id', '')) == child_id)
        parent = _current.get()
        if (parent is not None and (parent.owner_key is not self or not parent._open
                or parent.session_id != session_id or parent.child_id != child_id)):
            parent = None
        lease = InvocationLease(self.config.name, session_id, child_id, method, alive, self, parent)
        token = _current.set(lease)
        try:
            result = await self._request(method, params, version=version)
            # A successful JSON-RPC response can still contain a failed ToolResult.
            await lease.complete(result.pop('_xdog_view_invalidated', False) is not True
                                 and result.get('isError') is not True)
            return result
        finally:
            try:
                await lease.complete(False)
            finally:
                _current.reset(token)

    async def _request(self, method: str, params: dict[str, Any], *, version: str = "") -> dict[str, Any]:
        inventory_key = ""
        value: Any = None  # Session controls return distinct scalar/dict shapes, each validated below.
        view_pins: list[tuple[str, object]] = []

        def view_scope_current() -> bool:
            from xdog.agent.extensions.invocation import current_invocation
            return (not view_pins or current_invocation() is not None and all(
                self.owner.views.is_current(self.config.name, view, token) for view, token in view_pins))

        try:
            payload = dict(params)
            from xdog.agent.extensions.command_catalog import METHODS as menu_methods
            menu_handler = self.owner.command_menu_handler
            menu_allowed = (method in menu_methods and _dialogs_allowed.get() and menu_handler is not None
                and not self.owner._closed)
            from xdog.agent.extensions.provider_oauth_runtime import AUTH_REVIEW
            provider_auth_review = AUTH_REVIEW.get() if method == 'xdog/provider_auth' else None
            from xdog.agent.extensions.pending_messages import METHODS as pending_methods
            pending_reader = self.owner.pending_messages
            pending_allowed = method in pending_methods and pending_reader is not None and not self.owner._closed
            from xdog.agent.extensions.views import METHODS as view_methods
            views_allowed = method in view_methods and self.owner.views.has(self.config.name)
            if method not in {"xdog/context_begin", "xdog/context_chunk", "xdog/context_read", "xdog/context_close"}:
                owner_context = self.owner.context()
                projected_context = {key: item for key, item in {**owner_context, **params.get("context", {})}.items()
                                     if key not in REMOVED_HOST_CAPABILITIES}
                payload["context"] = {**projected_context,
                                      "entries": self.owner.entries.for_extension(self.config.name),
                                      "has_ui": (provider_auth_review is not None or _dialogs_allowed.get()
                                                 and (self.owner.dialog_handler is not None
                                                      or self.owner.command_menu_handler is not None
                                                      or self.owner.catalog_handler is not None)
                                                 and not self.owner._closed),
                                      "host_provider_auth": provider_auth_review is not None,
                                      "host_views": views_allowed,
                                      "host_pending_messages": pending_allowed,
                                      "host_command_menu": menu_allowed,
                                      "host_catalog": (method == "xdog/command" and _dialogs_allowed.get()
                                                       and self.owner.catalog_handler is not None
                                                       and not self.owner._closed),
                                      "host_session": (method == "xdog/command" and _dialogs_allowed.get()
                                                       and self.owner.dialog_handler is not None
                                                       and not self.owner._closed)}
                # Read-only host projection is distinct from command mutation/UI
                # authority. Caller context can never impersonate this envelope.
                from xdog.agent.extensions.inspection import envelope
                session_id = str(owner_context.get("session_id", ""))
                owner_id = str(owner_context.get("child_id") or session_id)
                projected = None
                if self.owner.inspection is not None and not self.owner._closed:
                    try:
                        projected = envelope(self.owner.inspection(), session_id=session_id, owner_id=owner_id)
                    except (ValueError, TypeError):
                        projected = None
                payload["context"].update(session_id=session_id, child_id=owner_context.get("child_id", ""),
                                          host_inspection=projected)
                if projected is not None:
                    payload["context"]["model"] = projected["model"]["id"]
                from xdog.agent.session_name import name_snapshot
                named = None
                if self.owner.session_name is not None and not self.owner._closed:
                    try:
                        named = name_snapshot(self.owner.session_name(), session_id=session_id, owner_id=owner_id)
                    except (ValueError, TypeError, UnicodeError, RuntimeError, OSError):
                        named = None
                payload['context']['host_session_name'] = named  # Caller context cannot forge name metadata.
                inventory = None
                if self.owner.tool_inventory is not None and not self.owner._closed:
                    from xdog.agent.extensions.tool_inventory import validate as validate_tools
                    try:
                        inventory = validate_tools(self.owner.tool_inventory(),
                                                   session_id=session_id, owner_id=owner_id)
                    except (ValueError, TypeError, UnicodeError, RuntimeError, OSError):
                        inventory = None
                payload['context']['host_tool_inventory_ref'] = None
                if inventory is not None and self._inventory_cache_supported:
                    from xdog.agent.extensions.tool_inventory import fingerprint
                    inventory_key = fingerprint(inventory)
                    if inventory_key in self._inventories:
                        payload['context']['host_tool_inventory_ref'] = inventory_key
                        inventory = None
                payload['context']['host_tool_inventory'] = inventory  # Never trust caller/worker supplied metadata.
            if method == "tools/call":
                payload["_xdog_tool_registration"] = _tool_registration.get()
            if method == "server/discover":
                payload["extension_settings"] = self.owner.settings.get(self.config.name, {})
            if (payload.get('context', {}).get('host_tool_inventory') is not None
                    or payload.get('context', {}).get('host_tool_inventory_ref') is not None):
                # Optional inspection must not push a formerly valid call over the
                # protocol frame cap. Reserve space for the normal RPC/meta envelope;
                # the generic transport still rejects genuinely excessive call data.
                if len(json.dumps(payload, ensure_ascii=True).encode()) > MAX_FRAME - 4096:
                    payload['context']['host_tool_inventory'] = None
                    payload['context']['host_tool_inventory_ref'] = None
                    inventory_key = ''
            if (payload.get('context', {}).get('host_session_name') is not None
                    and len(json.dumps(payload, ensure_ascii=True).encode()) > MAX_FRAME - 4096):
                payload['context']['host_session_name'] = None  # Optional metadata must not reject valid callbacks.
            result = await super().request(method, payload, version=version)
            if 'xdog_tool_inventory_miss' in result:
                missed = result.pop('xdog_tool_inventory_miss')
                if (not isinstance(missed, str) or not inventory_key or missed != inventory_key
                        or payload.get('context', {}).get('host_tool_inventory_ref') != missed):
                    raise ValueError('Invalid worker inventory-cache receipt')
                self._inventories.pop(missed, None)
                inventory_key = ''  # Reload metadata on a later invocation, never replay this callback.
            if inventory_key and self._inventory_cache_supported and inventory_key not in self._inventories:
                if len(self._inventories) >= 8:
                    self._inventories.pop(next(iter(self._inventories)))
                self._inventories[inventory_key] = None  # Only after this worker acknowledged the invocation.
            while "xdog_pending" in result or "xdog_auth_pending" in result:
                if "xdog_tool_snapshot" in result:
                    await self.owner.accept_tool_registration(self.config.name, self, result.pop("xdog_tool_snapshot"))
                if "xdog_command_snapshot" in result:
                    self.owner.accept_command_registration(self.config.name, self, result.pop("xdog_command_snapshot"))
                if "xdog_hook_snapshot" in result:
                    self.owner.accept_hook_registration(self.config.name, self, result.pop("xdog_hook_snapshot"))
                if "xdog_provider_snapshot" in result:
                    self.owner.accept_provider_registration(
                        self.config.name, self, result.pop("xdog_provider_snapshot"))
                if 'xdog_auth_pending' in result:
                    call_id = result.get('xdog_auth_pending')
                    if (method != 'xdog/provider_auth' or provider_auth_review is None
                            or not isinstance(call_id, str) or re.fullmatch('[a-f0-9]{32}', call_id) is None):
                        raise ValueError('Invalid pending provider authentication owner')
                    result = await super().request('xdog/provider_auth_poll', {'call_id': call_id})
                    continue
                call_id = result.get("xdog_pending")
                if not isinstance(call_id, str) or not 0 < len(call_id) <= 128:
                    raise ValueError("Invalid pending extension callback")
                prompt = validate_sdk_dialog(result.get("xdog_prompt"))
                contributions = result.pop("xdog_entries", [])
                if contributions and method != 'xdog/provider_auth' and view_scope_current():
                    await self.owner.commit_entries(self.config.name, contributions)
                auth_ui = result.pop("xdog_ui", [])
                if method != 'xdog/provider_auth' and view_scope_current():
                    self._accept_ui(auth_ui)
                handler = self.owner.dialog_handler if _dialogs_allowed.get() else None
                if method == 'xdog/provider_auth':
                    handler = provider_auth_review
                elif prompt["kind"] == "session" and method != "xdog/command":
                    handler = None  # Non-command read snapshots cannot borrow a live session dialog owner.
                if not view_scope_current():
                    value = None  # Continue settling this callback, without a successor's grants/UI.
                elif prompt['kind'] == 'view':
                    from xdog.agent.extensions.invocation import current_invocation
                    view_lease = current_invocation()
                    value = None
                    if views_allowed and view_lease is not None:
                        binding = self.owner.views.token(self.config.name, prompt['name'])
                        if binding is not None:
                            view_pins.append((prompt['name'], binding))
                        try:
                            async with asyncio.timeout(min(30, float(prompt.get('timeout', 30)))):
                                value = await self.owner.views.read(self.config.name, prompt['name'], prompt['query'])
                        except (ValueError, TypeError, RuntimeError, OSError, TimeoutError):
                            value = None
                        if current_invocation() is not view_lease:
                            value = None
                elif self.config.name in self.owner._display_only and prompt['kind'] == 'session':
                    value = None  # Display profiles have no reviewed host-control/data-export proposals.
                elif prompt['kind'] == 'session' and prompt.get('operation') == 'commands' and menu_allowed:
                    from xdog.agent.extensions.command_catalog import answer as menu_answer
                    from xdog.agent.extensions.invocation import current_invocation
                    menu_lease = current_invocation()
                    value = None
                    if (menu_lease is not None and menu_handler is not None
                            and self.owner.command_menu_handler is menu_handler):
                        identity = {'invocation': menu_lease.id, 'session_id': menu_lease.session_id,
                                    'child_id': menu_lease.child_id, 'method': method}
                        try:
                            async with asyncio.timeout(min(30, float(prompt.get('timeout', 30)))):
                                value = menu_answer(prompt, await menu_handler(self.config.name,
                                    {**prompt, 'command_menu': identity}))
                        except (ValueError, TypeError, RuntimeError, OSError, TimeoutError):
                            value = None
                        if (self.owner.command_menu_handler is not menu_handler
                                or current_invocation() is not menu_lease or not menu_lease.owner_alive()
                                or value is not None and value['session_id'] != menu_lease.session_id):
                            value = None
                elif prompt['kind'] == 'session' and prompt.get('operation') == 'pending_messages':
                    value = None
                    if pending_allowed and pending_reader is not None and self.owner.pending_messages is pending_reader:
                        from xdog.agent.extensions.invocation import current_invocation
                        pending_lease = current_invocation()
                        if pending_lease is not None:
                            try:
                                async with asyncio.timeout(min(float(prompt.get('timeout', 30)), 30)):
                                    value = pending_reader()
                                    if inspect.isawaitable(value):
                                        value = await value
                            except (ValueError, TypeError, RuntimeError, OSError, TimeoutError):
                                value = None
                            if (self.owner.pending_messages is not pending_reader
                                    or current_invocation() is not pending_lease or type(value) is not bool):
                                value = None
                elif prompt["kind"] == "session" and prompt.get("operation") == "catalog":
                    catalog = (self.owner.catalog_handler
                               if method == "xdog/command" and _dialogs_allowed.get() else None)
                    try:
                        value = await catalog(self.config.name, prompt) if catalog is not None else None
                    except (ValueError, OSError):
                        # Missing/foreign targets or changed membership are read
                        # refusals, not broken plugin callbacks. No retry/adoption.
                        value = None
                else:
                    value = await handler(self.config.name, prompt) if handler is not None else None
                value = validate_answer(prompt, value)
                result = await super().request("xdog/ui_reply", {
                    "call_id": call_id, "dialog_id": prompt["id"], "value": value})
            if (self.config.name in self.owner._display_only and method == "xdog/command"
                    and "xdog_command_result" in result):
                raise ValueError("Display-only commands return text, not host action proposals")
            if not view_scope_current():
                # Earlier valid reads cannot be published after a branch/provider
                # binding changes later in the same awaited callback.
                return {"_xdog_view_invalidated": True, **(
                    {"output": "View unavailable: original owner changed"} if method == "xdog/command" else {})}
            if "xdog_tool_snapshot" in result:
                await self.owner.accept_tool_registration(self.config.name, self, result.pop("xdog_tool_snapshot"))
            if "xdog_command_snapshot" in result:
                self.owner.accept_command_registration(self.config.name, self, result.pop("xdog_command_snapshot"))
            if "xdog_hook_snapshot" in result:
                self.owner.accept_hook_registration(self.config.name, self, result.pop("xdog_hook_snapshot"))
            if "xdog_provider_snapshot" in result:
                self.owner.accept_provider_registration(
                    self.config.name, self, result.pop("xdog_provider_snapshot"))
            contributions = result.pop("xdog_entries", [])
            if contributions and method != 'xdog/provider_auth':
                await self.owner.commit_entries(self.config.name, contributions)
            auth_ui = result.pop("xdog_ui", [])
            if method != 'xdog/provider_auth':
                self._accept_ui(auth_ui)
            if method == "tools/call":
                result = host_result(result)
            return result
        except BaseException:
            self.owner.fail(self.config.name)
            await self.close()  # A timed-out/canceled callback is never replayed or left running.
            raise


    async def close(self) -> None:
        if getattr(self, '_provider_auth_active', False):
            from xdog.agent.extensions.provider_oauth_runtime import close_auth_client
            await close_auth_client(self)
        else:
            await super().close()


class _Manager(McpManager):
    def __init__(self, owner: ExtensionRuntime, configs: dict[str, Any]) -> None:
        super().__init__(configs, owner.cwd)
        self.owner = owner

    async def _bridge(self, coroutine: Coroutine[Any, Any, T]) -> T:
        try:
            loop = await asyncio.to_thread(self._ensure_loop)
        except BaseException:
            coroutine.close()
            raise
        # Extension callbacks share MCP's owner lifecycle. In particular, close
        # can win after loop acquisition but before callback admission.
        with self._start_lock:
            if self._closed:
                coroutine.close()
                raise McpError("MCP manager is closed")
            pending = submit_remote(loop, coroutine)
        return await pending

    async def _connect(self, name: str) -> None:
        if name in self._clients:
            raise McpError("Disconnect the previous extension owner before reload")
        client = _Client(self.configs[name], self.owner)
        with self._lock:
            self._clients[name] = client
        try:
            await client.connect()
            await self._discover(name)
            if self._rejected.get(name):
                raise ValueError("Extension tool registration rejected")
        except BaseException:
            await self._disconnect(name)
            raise

    async def _discover(self, name: str) -> None:
        client = self._clients.get(name)
        if not isinstance(client, _Client):
            raise ValueError("Extension tool owner unavailable")
        client._discovering_tools = True
        try:
            await super()._discover(name)
            if self._rejected.get(name):
                raise ValueError("Extension tool registration rejected")
            identities = {row["name"]: row["registration_id"] for row in (
                client._tool_snapshot["tools"] if client._tool_snapshot is not None else [])}
            with self._lock:
                self._tools = tuple(replace(tool, registration_id=identities.get(tool.remote_name, ""))
                                    if tool.server == name else tool for tool in self._tools)
            if client._tool_snapshot is not None and {tool.remote_name for tool in self._tools
                                                      if tool.server == name} != set(identities):
                raise ValueError("Extension tool catalog/identity mismatch")
        finally:
            client._discovering_tools = False

    async def _call(self, tool: McpTool, arguments: dict[str, Any]) -> AgentToolResult:
        client = self._clients.get(tool.server)
        if (not isinstance(client, _Client) or client._tool_snapshot is None
                or not any(row["name"] == tool.remote_name and row["registration_id"] == tool.registration_id
                           for row in client._tool_snapshot["tools"])):
            raise ValueError("Extension tool registration changed; old approval/executor not adopted")
        token = _tool_registration.set(tool.registration_id)
        try:
            return await super()._call(tool, arguments)
        finally:
            _tool_registration.reset(token)

    def _result_details(self, tool: McpTool, result: dict[str, Any]) -> dict[str, Any]:
        metadata = super()._result_details(tool, result)
        metadata.update(extension=tool.server, extension_details_present="xdog_result_details" in result)
        if "xdog_result_details" in result:
            metadata["extension_details"] = details_snapshot(result["xdog_result_details"])
        return metadata

    async def _close_clients(self) -> None:
        await self.owner._cancel_view_jobs()
        async def finish(client: Any) -> None:
            if client.state == "connected":
                try:
                    await client.request("xdog/event", {"event": "session_end"})
                except BaseException:
                    pass
        await asyncio.gather(*(finish(client) for client in tuple(self._clients.values())))
        await super()._close_clients()


class ExtensionRuntime:
    def __init__(self, enabled: Any, root: Path, cwd: Path, *, settings: Mapping[str, Any] | None = None,
                 context: Callable[[], dict[str, Any]] | None = None, timeout: float = 3,
                 entries: ExtensionEntries | None = None,
                 inspection: Callable[[], dict[str, Any] | None] | None = None,
                 prepared_root: ExtensionRoot | None = None) -> None:
        location = prepared_root or ExtensionRoot.read(root)
        self.enabled, self.root, self.cwd = names(enabled), location.adopt(root), cwd
        self.settings = dict(settings or {})
        self.entries = entries or ExtensionEntries({}, lambda value: None)
        self._context_lock = asyncio.Lock()
        self._pre_lock = asyncio.Lock()
        self.context = context or (lambda: {"cwd": str(cwd)})
        self.inspection = inspection
        self.views = ViewRegistry()
        self._view_hints: dict[tuple[str, str], tuple[object, str, _Client, int, tuple[str, str]]] = {}
        self._view_jobs: dict[tuple[str, str], asyncio.Task[None]] = {}
        self.tool_inventory: Callable[[], dict[str, Any] | None] | None = None
        self.session_name: Callable[[], dict[str, str] | None] | None = None
        self.command_menu_handler: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None
        self.catalog_handler: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None
        self.dialog_cancel: Callable[[str], None] | None = None
        self.pending_messages: Callable[[], bool | None | Awaitable[bool | None]] | None = None
        self.dialog_handler: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None
        self.publish: Callable[[dict[str, Any]], None] = lambda event: None
        self._closed = False
        self._generation = 0
        self._initialized = False
        self._activation_lock = asyncio.Lock()
        self._infos: dict[str, dict[str, Any]] = {}
        self._reserved_commands: frozenset[str] = frozenset()
        self._skill_commands: frozenset[str] = frozenset()
        self._native_commands = NativeCommands()
        self._close_task: asyncio.Task[None] | None = None
        self._input_required: set[str] = set()
        self._provider_lock = asyncio.Lock()
        self._failed: set[str] = set()
        self._display_only: set[str] = set()
        self._unloaded: set[str] = set()
        self._aliases: set[str] = set()
        self._tool_wrappers: dict[str, tuple[McpTool, Any, AgentTool]] = {}
        self._ui: dict[str, dict[str, Any]] = {}
        self._diagnostics: list[str] = []
        configs = {name: {"command": sys.executable, "args": ["-P", str(Path(__file__).with_name("worker.py")),
                    str(self.root / name)], "timeout": timeout}
                   for name in self.enabled}
        self._manager = _Manager(self, configs)

    def notify_view_changed(self, view: str, revision: str = "") -> None:
        """Coalesce hints on the existing process owner; never load a worker/model."""
        from xdog.agent.extensions.views import identifier as view_name
        view = view_name(view)
        if not isinstance(revision, str) or len(revision.encode("utf-8")) > 256:
            raise ValueError("View revision hints must be bounded text")
        loop = self._manager._loop
        if self._closed or self._manager._closed or loop is None or loop.is_closed():
            return
        context = self.context()
        scope = str(context.get("session_id", "")), str(context.get("child_id", ""))
        generation = self._generation
        with self._manager._lock:
            clients = tuple(self._manager._clients.items())
        targets = [(extension, client, token) for extension, client in clients
                   if isinstance(client, _Client) and (token := self.views.token(extension, view)) is not None]

        def admit() -> None:
            if self._closed or self._manager._closed:
                return
            for extension, client, token in targets:
                if not self._view_target(extension, view, token, client, generation, scope):
                    continue
                key = extension, view
                self._view_hints[key] = token, revision, client, generation, scope
                if key not in self._view_jobs:
                    self._view_jobs[key] = asyncio.create_task(self._send_view_hints(key))
        try:
            loop.call_soon_threadsafe(admit)
        except RuntimeError:
            pass  # Closing won; hints are not authoritative facts or obligations.

    def _view_target(self, extension: str, view: str, token: object, client: _Client,
                     generation: int, scope: tuple[str, str]) -> bool:
        context = self.context()
        return (not self._closed and not self._manager._closed and self._generation == generation
                and self._manager._clients.get(extension) is client and client.state == "connected"
                and extension not in self._failed | self._unloaded
                and "view_changed" in self._infos.get(extension, {}).get("events", ())
                and (str(context.get("session_id", "")), str(context.get("child_id", ""))) == scope
                and self.views.is_current(extension, view, token))

    async def _send_view_hints(self, key: tuple[str, str]) -> None:
        extension, view = key
        try:
            await asyncio.sleep(0)
            while key in self._view_hints:
                token, revision, client, generation, scope = self._view_hints.pop(key)
                if not self._view_target(extension, view, token, client, generation, scope):
                    continue
                await self._rpc(extension, "xdog/event", {"event": "view_changed",
                    "context": {"view": view, "revision": revision}})
        except asyncio.CancelledError:
            raise
        except Exception:
            self.fail(extension)  # A display failure is never a business-operation retry.
        finally:
            self._view_hints.pop(key, None)
            self._view_jobs.pop(key, None)

    async def _cancel_view_jobs(self, extension: str = "") -> None:
        keys = tuple(key for key in self._view_jobs if not extension or key[0] == extension)
        jobs = tuple(self._view_jobs[key] for key in keys)
        for key in keys:
            self._view_hints.pop(key, None)
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    @property
    def validation_failed(self) -> frozenset[str]:
        # Unknown/default extensions remain required. Only an explicitly
        # validated display profile has no validation authority to lose.
        return frozenset(self._failed - self._unloaded - self._display_only)

    def _profile(self, name: str, meta: dict[str, Any]) -> None:
        if meta.get("display_only") is True:
            self._display_only.add(name)
        else:
            self._display_only.discard(name)

    def _display_hooks(self, name: str, declaration: dict[str, Any]) -> None:
        if name in self._display_only and (
                any(declaration.get(flag) for flag in FLAGS)
                or set(declaration.get("events", ())) - {"session_start", "session_end", "view_changed"}):
            raise ValueError("Display-only extensions cannot register validation/model hooks")

    async def commit_entries(self, name: str, contributions: Any) -> None:
        if self._closed:
            raise RuntimeError("Extension entry owner closed; late state was not written")
        if name in self._display_only and contributions:
            raise ValueError("Display-only extensions cannot persist conversation entries")
        await run_owned(self.entries.append, name, contributions)

    async def transform(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        if not self.enabled:
            return messages
        await self.initialize()
        if self.validation_failed:
            raise ValueError("Extension context validation unavailable; unload failed extensions explicitly")
        async def run() -> list[AgentMessage]:
            async with self._context_lock:
                current = messages
                for name, info in tuple(self._infos.items()):
                    if name in self._failed | self._unloaded or not info.get("context"):
                        continue
                    try:
                        async with asyncio.timeout(max(30, self._manager.configs[name].timeout)):
                            value = await self._transfer_context(name, messages_to_dicts(current))
                            current = await run_owned(decode_context, value, current)
                    except BaseException:
                        self.fail(name)
                        await self._manager._disconnect(name)
                        raise
                return current
        return await self._manager._bridge(run())

    async def _transfer_context(self, name: str, messages: list[dict[str, Any]]) -> Any:
        return await self._transfer_value(name, messages)

    async def _transfer_value(self, name: str, value: Any, *, operation: str = 'context') -> Any:
        receiver = ContextTransfer()
        try:
            async with owned_spool(value) as (file, size, checksum):
                identifier = uuid.uuid4().hex
                await self._rpc(name, "xdog/context_begin", {"id": identifier, "bytes": size, "sha256": checksum})
                index = 0
                while block := await run_owned(file.read, BLOCK):
                    await self._rpc(name, "xdog/context_chunk", {"id": identifier, "index": index,
                                    "data": base64.b64encode(block).decode("ascii")})
                    index += 1
                result = await self._rpc(name, "xdog/context_finish", {"id": identifier, "chunks": index,
                                                                        "operation": operation})
                if result.get("id") != identifier:
                    raise ValueError("Extension context result identity mismatch")
                await run_owned(receiver.begin, result)
                index = 0
                while receiver.received < receiver.size:
                    block_result = await self._rpc(name, "xdog/context_read", {"id": identifier, "index": index})
                    await run_owned(receiver.chunk, block_result)
                    index += 1
                value = await run_owned(receiver.finish, {"id": identifier, "chunks": index})
                await self._rpc(name, "xdog/context_close", {"id": identifier})
                return value
        finally:
            receiver.close()

    def fail(self, name: str) -> None:
        self._generation += 1
        self._failed.add(name)
        if self.dialog_cancel is not None:
            self.dialog_cancel(name)
        for key in tuple(self._ui):
            if key.startswith(name + ":"):
                self._ui.pop(key, None)
        if not self._closed:
            self.publish({"type": "extension_notice", "extension": name,
                          "text": "Extension failed/disabled; effects uncertain, not replayed",
                          "snapshot": self.snapshot()})

    def accept_ui(self, name: str, rows: Any, *, client: _Client | None = None) -> None:
        if (self._closed or client is not None and (self._manager._clients.get(name) is not client
                or client.state == "disconnected" or name in self._failed | self._unloaded)):

            return
        if not isinstance(rows, list) or len(rows) > 32:
            raise ValueError("Invalid extension UI response")
        # Validate and prepare the whole owned batch before committing/publishing it.
        pending = dict(self._ui)
        changes: list[tuple[dict[str, Any], str, bool]] = []
        for row in rows:
            if (not isinstance(row, dict) or row.keys() - {"kind", "key", "lines", "clear"}
                    or row.get("kind") not in ("notice", "status")
                    or not isinstance(row.get("key"), str) or not isinstance(row.get("lines"), list)
                    or len(row["lines"]) > 8 or any(not isinstance(line, str) or len(line.encode("utf-8")) > 1000
                                                  for line in row["lines"])
                    or type(row.get("clear", False)) is not bool
                    or row.get("clear", False) and (row["kind"] == "notice" or row["lines"])):
                raise ValueError("Invalid extension UI contribution")
            if row["kind"] != "notice" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", row["key"]):
                raise ValueError("Invalid extension UI contribution identity")
            key = name + ":" + row["key"]
            changed = False
            if row.get("clear", False):
                current = pending.get(key)
                if current is not None and current["kind"] == row["kind"]:
                    pending.pop(key)
                    changed = True
            elif row["kind"] != "notice":
                normalized = {"kind": row["kind"], "key": row["key"], "lines": list(row["lines"])}
                changed = pending.get(key) != normalized
                pending[key] = normalized
            if len(pending) > 32:
                raise ValueError("Extension status capacity exceeded")
            changes.append((row, "\n".join(row["lines"]), changed))
        self._ui = pending
        for row, text, changed in changes:
            if row["kind"] == "notice" or changed:
                self.publish({"type": "extension_notice" if row["kind"] == "notice" else "extension_ui",
                              "extension": name, "text": text, "snapshot": self.snapshot()})

    async def initialize(self) -> None:
        if not self.enabled or self._closed:
            return
        await self._manager._bridge(self._initialize())

    async def _initialize(self) -> None:
        async with self._activation_lock:
            try:
                await self._initialize_locked()
            except asyncio.CancelledError:
                # Startup/import effects are uncertain; never silently retry
                # selected-but-unsettled extensions on a successor turn.
                for name in set(self.enabled) - set(self._infos):
                    self.fail(name)
                raise

    async def _initialize_locked(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        pending = list(self.enabled)
        loaded: set[str] = set()
        while pending:
            progressed = False
            for name in tuple(pending):
                try:
                    from xdog.agent.extensions.discovery import extension_directory
                    directory = await run_owned(extension_directory, name, self.root)
                    if directory is None:
                        raise ValueError("Configured extension is not installed or present in its explicit root")
                    meta = await run_owned(partial(manifest, directory, check_entry=False))
                    self._profile(name, meta)
                    dependencies = set(meta.get("dependencies", []))
                    if not dependencies <= set(self.enabled):
                        raise ValueError("Extension dependency is not explicitly enabled")
                    if dependencies & set(pending):
                        continue
                    if not dependencies <= loaded:
                        raise ValueError("Extension dependency failed")
                    await self._load(name, directory=directory)
                    loaded.add(name)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.fail(name)
                    await self._manager._disconnect(name)
                pending.remove(name)
                progressed = True
            if not progressed:
                for name in pending:
                    self.fail(name)
                break

    async def _load(self, name: str, *, directory: Path | None = None) -> None:
        self._generation += 1
        if name not in self.enabled:
            raise ValueError("Extension is not explicitly trusted in global configuration")
        if directory is None:
            from xdog.agent.extensions.discovery import extension_directory
            directory = await run_owned(extension_directory, name, self.root)
            if directory is None:
                raise ValueError("Configured extension is not installed")
        meta = await run_owned(partial(manifest, directory, check_entry=False))
        self._profile(name, meta)
        if not await run_owned((directory / (meta['entry_point'] + '.py')).is_file):
            raise ValueError("Extension entry module is missing")
        config = self._manager.configs[name]
        self._manager.configs[name] = replace(config, command=(*config.command[:-1], str(directory)))
        await self._manager._connect(name)
        info = await self._rpc(name, "xdog/info", {})
        self._display_hooks(name, info)
        if name in self._display_only and (info.get("providers") or any(
                tool.server == name for tool in self._manager._tools)):
            raise ValueError("Display-only extensions cannot register tools/providers")
        if (not isinstance(info.get("commands"), list) or len(info["commands"]) > 32
                or not isinstance(info.get("events"), list) or type(info.get("pre")) is not bool
                or type(info.get("post")) is not bool):
            raise ValueError("Invalid extension registration")
        seen = set()
        for command in info["commands"]:
            if (not isinstance(command, dict) or not isinstance(command.get("name"), str)
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", command["name"])
                    or command["name"].lower() in seen or not isinstance(command.get("description"), str)
                    or len(command["description"].encode("utf-8")) > 1000):
                raise ValueError("Invalid/duplicate extension command")
            seen.add(command["name"].lower())
        if (sum(len(value["commands"]) for key, value in self._infos.items() if key != name)
                + len(info["commands"]) > 64):
            raise ValueError("Extension command capacity exceeded")
        from xdog.agent.extensions.providers import provider_catalog
        normalized = provider_catalog(info.get("providers", []))
        revision = info.get("provider_revision", 0)
        if type(revision) is not int or not 0 <= revision <= 2**53:
            raise ValueError("Invalid extension provider revision")
        client = cast(_Client, self._manager._clients[name])
        if client._provider_snapshot is not None and client._provider_snapshot[0] >= revision:
            current_revision, current = client._provider_snapshot
            if current_revision == revision and current != normalized:
                raise ValueError("Conflicting extension provider snapshot")
            revision, normalized = current_revision, current
        if (sum(len(row['models']) for other, declared in self._infos.items() if other != name
                for row in declared.get('providers', [])) + sum(len(row['models']) for row in normalized) > 64):
            raise ValueError("Extension provider model catalog capacity exceeded")
        info['providers'], info['provider_revision'] = normalized, revision
        client._provider_snapshot = revision, normalized
        hooks = hook_declaration({"revision": info.get("hook_revision", 0), "events": info["events"],
                                  **{flag: info.get(flag, False) for flag in FLAGS}})
        if client._hook_snapshot is not None and client._hook_snapshot["revision"] >= hooks["revision"]:
            if client._hook_snapshot["revision"] == hooks["revision"] and client._hook_snapshot != hooks:
                raise ValueError("Conflicting extension hook snapshot")
            hooks = client._hook_snapshot
        client._hook_snapshot = hooks
        info.update(events=hooks["events"], hook_revision=hooks["revision"], **{flag: hooks[flag] for flag in FLAGS})
        commands = command_declaration({"revision": info.get("command_revision", 0),
                                        "commands": info["commands"], "shortcuts": info.get("shortcuts", [])})
        if client._command_snapshot is not None and client._command_snapshot["revision"] >= commands["revision"]:
            if (client._command_snapshot["revision"] == commands["revision"]
                    and client._command_snapshot != commands):
                raise ValueError("Conflicting extension command snapshot")
            commands = client._command_snapshot
        self._command_routes((name, commands['commands']))
        client._command_snapshot = commands
        info.update(commands=commands["commands"], shortcuts=commands["shortcuts"],
                    command_revision=commands["revision"])
        seen = {row["name"].lower() for row in commands["commands"]}
        shortcut_rows = registrations(info.get("shortcuts", []))
        if any(row["name"].lower() not in seen for row in shortcut_rows):
            raise ValueError("Extension shortcut does not name an actual registered command")
        count = sum(len(value.get("shortcuts", [])) for key, value in self._infos.items() if key != name)
        if count + len(shortcut_rows) > 64:
            raise ValueError("Extension shortcut capacity exceeded")
        info["shortcuts"] = shortcut_rows
        if type(info.get("input", False)) is not bool:
            raise ValueError("Invalid extension input-hook registration")
        if info.get("input", False):
            self._input_required.add(name)
        else:
            self._input_required.discard(name)  # A successful explicit reload may change declared capabilities.
        cached_inventory = info.get('tool_inventory_cache', 0)
        if type(cached_inventory) is not int or cached_inventory not in (0, 1):
            raise ValueError('Invalid worker tool-inventory cache capability')
        client._inventory_cache_supported = cached_inventory == 1
        self._infos[name] = info
        self._failed.discard(name)
        self._unloaded.discard(name)
        client._ui_admitted = True
        client._accept_ui([])  # Only validated/admitted setup contributions reach the host snapshot.
        await self._rpc(name, "xdog/event", {"event": "session_start"})

    async def _rpc(self, name: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        client = self._manager._clients.get(name)
        if client is None:
            raise ValueError("Extension is not active")
        return await client.request(method, params)

    def bind_native_commands(self, extension_id: str, commands: NativeCommandDefinitions, *,
                             valid: Callable[[], bool],
                             shadow_skills: set[str] | frozenset[str] = frozenset()) -> Callable[[], None]:
        """Bind trusted installed callbacks; no RPC/data can grant native authority.

        Hosts declare builtin and skill reservations separately. Only explicitly
        named skill entries may overlap; builtins/other extension owners never do.
        The returned disposer retires only this exact binding, not its successor.
        """
        binding = native_command_binding(extension_id, commands, valid, shadow_skills)
        if self._closed or binding.valid() is not True:
            raise ValueError('Native command owner is closed or stale')
        if any(name.lower() == extension_id.lower() for name in self.enabled):
            raise ValueError('Native command identity conflicts with a selected extension worker')
        active = self._native_commands.active()
        if (any(value.extension.lower() == extension_id.lower() for value in active)
                or len(self.enabled) + len(active) >= 16):
            raise ValueError('Native command extension identity is already owned or capacity exceeded')
        self._command_routes(native_candidate=binding)
        self._native_commands.bind(binding)

        def remove() -> None:
            if self._native_commands.remove(binding) and not self._closed:
                self.publish({'type': 'extension_ui', 'snapshot': self.snapshot()})

        try:
            self.publish({'type': 'extension_ui', 'snapshot': self.snapshot()})
        except BaseException:
            self._native_commands.remove(binding)
            raise
        return remove

    def snapshot(self) -> dict[str, Any]:
        return {"extensions": [{"name": name, "state": "unloaded" if name in self._unloaded else
                                 "failed" if name in self._failed else
                                 "active" if name in self._infos else "not loaded"}
                                for name in self.enabled] + [
                                    {'name': binding.extension, 'state': 'active'}
                                    for binding in self._native_commands.active()],
                "commands": self.commands(), "command_completions": self.command_completions(),
                **({'command_owners': self.command_owners()} if any('public_name' in row
                    for _, row in self._command_routes().values()) else {}),
                "ui": dict(self._ui), "shortcuts": self.shortcuts()}

    def _command_declarations(self, candidate: tuple[str, list[dict[str, Any]]] | None = None,
                              native_candidate: NativeCommandBinding | None = None
                              ) -> dict[str, list[dict[str, Any]]]:
        declarations = {name: info['commands'] for name, info in tuple(self._infos.items())
                        if name not in self._failed | self._unloaded}
        if candidate is not None:
            declarations[candidate[0]] = candidate[1]
        for binding in (*self._native_commands.active(),
                        *((native_candidate,) if native_candidate is not None else ())):
            if binding.extension in declarations:
                raise ValueError('Native command identity conflicts with an extension worker')
            declarations[binding.extension] = binding.rows()
        if sum(len(rows) for rows in declarations.values()) > 64:
            raise ValueError('Extension command capacity exceeded')
        return declarations

    def _command_reserved(self, owner: str, command: str,
                          native_candidate: NativeCommandBinding | None = None) -> bool:
        if command in self._reserved_commands:
            return True
        if command not in self._skill_commands:
            return False
        binding = (native_candidate if native_candidate is not None and native_candidate.extension == owner else
                   self._native_commands.resolve(owner, command))
        return binding is None or command not in binding.shadow_skills

    def _command_routes(self, candidate: tuple[str, list[dict[str, Any]]] | None = None, *,
                        native_candidate: NativeCommandBinding | None = None
                        ) -> dict[str, tuple[str, dict[str, Any]]]:
        if self._closed:
            return {}
        routes: dict[str, tuple[str, dict[str, Any]]] = {}
        claimed: set[str] = set()
        for name, rows in self._command_declarations(candidate, native_candidate).items():
            for row in rows:
                selected = external_name(name, row)
                if selected in claimed:
                    raise ValueError(f'Extension command name conflict: /{selected} is already registered')
                claimed.add(selected)
                if self._command_reserved(name, selected, native_candidate):
                    if (candidate is not None and name == candidate[0]
                            or native_candidate is not None and name == native_candidate.extension):
                        raise ValueError(f'Extension command name conflict: /{selected} is reserved')
                    continue
                routes[selected] = (name, row)
        return routes

    def command_conflicts(self) -> dict[str, str]:
        if self._closed:
            return {}
        return {external_name(name, row): name for name, rows in self._command_declarations().items()
                for row in rows if self._command_reserved(name, external_name(name, row))}

    def reserve_commands(self, names: set[str] | frozenset[str], *,
                         skill_commands: set[str] | frozenset[str] = frozenset()) -> None:
        """Reserve builtins fail-closed; explicit native overlap may shadow only skills."""
        previous = self.command_conflicts()
        self._reserved_commands = frozenset(name.removeprefix('/').lower() for name in names)
        self._skill_commands = frozenset(name.removeprefix('/').lower() for name in skill_commands)
        for command, owner in self.command_conflicts().items():
            if previous.get(command) != owner:
                self.publish({'type': 'extension_notice', 'extension': owner,
                    'text': f'Command conflict: /{command} ({owner}) is unavailable; '
                            'remove the conflicting skill/configuration or unload this extension.',
                    'snapshot': self.snapshot()})

    def commands(self) -> dict[str, str]:
        return {name: row['description'] for name, (_, row) in self._command_routes().items()}

    def command_owners(self) -> dict[str, str]:
        return {name: owner for name, (owner, _) in self._command_routes().items()}

    def _command_route(self, command: str) -> tuple[str, dict[str, Any]] | None:
        for public, (owner, row) in self._command_routes().items():
            selected = command.lower()
            if (selected in {public, f"ext-{owner}-{row['name']}".lower()}
                    and not self._command_reserved(owner, selected)):
                return owner, row
        return None

    def command_owner(self, command: str) -> str | None:
        route = self._command_route(command)
        return route[0] if route is not None else None

    def is_native_command(self, command: str) -> bool:
        """Actual bound native contribution, not a name/manifest authority claim."""
        route = self._command_route(command)
        return route is not None and self._native_commands.resolve(route[0], route[1]['name']) is not None

    def shortcuts(self) -> list[dict[str, str]]:
        routes = {(owner, row['name'].lower()): name for name, (owner, row) in self._command_routes().items()}
        result = []
        for name, info in tuple(self._infos.items()):
            if name in self._failed | self._unloaded:
                continue
            for row in info.get('shortcuts', []):
                command = routes.get((name, row['name'].lower()))
                if command is None:
                    continue
                action = f"ext-{name}-{row['name']}".lower()
                result.append({'extension': name, 'command': command, 'key': row['key'],
                               'description': row['description'], **({'action': action} if command != action else {})})
        return result

    def accept_provider_registration(self, name: str, client: _Client, value: Any) -> None:
        """Adopt only current-owner, monotonic, bounded catalog metadata; never a model call."""
        if (self._closed or self._manager._clients.get(name) is not client or client.state == "disconnected"):
            return
        from xdog.agent.extensions.providers import provider_catalog
        if not isinstance(value, dict) or value.keys() != {'revision', 'providers'}:
            raise ValueError("Invalid live provider snapshot")
        revision = value['revision']
        if type(revision) is not int or not 0 <= revision <= 2**53:
            raise ValueError("Invalid live provider revision")
        normalized = provider_catalog(value['providers'], live=True)
        if name in self._display_only and normalized:
            raise ValueError("Display-only extensions cannot register model providers")
        previous = client._provider_snapshot
        if previous is not None and revision <= previous[0]:
            if revision == previous[0] and normalized != previous[1]:
                raise ValueError("Conflicting live provider revision")
            return  # Old/duplicate receipts cannot rewrite the current catalog.
        if (sum(len(row['models']) for other, info in self._infos.items() if other != name
                for row in info.get('providers', [])) + sum(len(row['models']) for row in normalized) > 64):
            raise ValueError("Live provider model capacity exceeded")
        client._provider_snapshot = revision, normalized
        info = self._infos.get(name)
        if info is not None and name not in self._failed | self._unloaded:
            self._infos[name] = {**info, 'providers': normalized, 'provider_revision': revision}
            # Model catalogs are read from this cache by their owned controls.
            # Provider-only changes do not enqueue unchanged terminal/UI snapshots.

    async def accept_tool_registration(self, name: str, client: _Client, value: Any) -> None:
        if self._closed or self._manager._clients.get(name) is not client or client.state == "disconnected":
            return
        current = tool_declaration(value)
        if name in self._display_only and current['tools']:
            raise ValueError("Display-only extensions cannot register tools")
        previous = client._tool_snapshot
        if previous is not None and current["revision"] <= previous["revision"]:
            if current["revision"] == previous["revision"] and current != previous:
                raise ValueError("Conflicting live extension tool revision")
            return
        client._tool_snapshot = current
        if client.state == "connected" and not client._discovering_tools:
            await self._manager._discover(name)
        # Session initialization projects the fresh catalog into the same actual
        # agent. No automatic turn or permission-policy modification is performed.

    def accept_command_registration(self, name: str, client: _Client, value: Any) -> None:
        if self._closed or self._manager._clients.get(name) is not client or client.state == "disconnected":
            return
        current = command_declaration(value)
        previous = client._command_snapshot
        if previous is not None and current["revision"] <= previous["revision"]:
            if current["revision"] == previous["revision"] and current != previous:
                raise ValueError("Conflicting live extension command revision")
            return
        if (sum(len(info["commands"]) for other, info in self._infos.items() if other != name)
                + len(current["commands"]) > 64 or sum(len(info.get("shortcuts", []))
                for other, info in self._infos.items() if other != name) + len(current["shortcuts"]) > 64):
            raise ValueError("Live extension command/shortcut capacity exceeded")
        self._command_routes((name, current['commands']))
        client._command_snapshot = current
        info = self._infos.get(name)
        if info is not None and name not in self._failed | self._unloaded:
            self._infos[name] = {**info, "commands": current["commands"], "shortcuts": current["shortcuts"],
                                 "command_revision": current["revision"]}
            self.publish({"type": "extension_ui", "snapshot": self.snapshot()})

    def accept_hook_registration(self, name: str, client: _Client, value: Any) -> None:
        if self._closed or self._manager._clients.get(name) is not client or client.state == "disconnected":
            return
        current = hook_declaration(value)
        self._display_hooks(name, current)
        previous = client._hook_snapshot
        if previous is not None and current["revision"] <= previous["revision"]:
            if current["revision"] == previous["revision"] and current != previous:
                raise ValueError("Conflicting live extension hook revision")
            return
        client._hook_snapshot = current
        info = self._infos.get(name)
        if info is not None and name not in self._failed | self._unloaded:
            self._infos[name] = {**info, "events": current["events"], "hook_revision": current["revision"],
                                 **{flag: current[flag] for flag in FLAGS}}
            if current["input"]:
                self._input_required.add(name)
            else:
                self._input_required.discard(name)  # Explicit owned disposal, not a failed-worker bypass.


    @property
    def generation(self) -> int:
        """Monotonic worker-selection lease, including failure/reload/unload/close."""
        return self._generation


    def provider_auth_catalog(self) -> tuple[dict[str, Any], ...]:
        from xdog.agent.extensions.provider_oauth_runtime import catalog
        return catalog(self)

    async def provider_auth_control(self, identifier: str, action: str, *,
                                    ask: Callable[[dict[str, Any]], Awaitable[Any]], alive: Callable[[], bool],
                                    storage: dict[str, str] | None = None) -> dict[str, Any]:
        from xdog.agent.extensions.provider_oauth_runtime import control
        return await control(self, identifier, action, ask=ask, alive=alive, storage=storage)

    def provider_models(self) -> tuple[Model, ...]:
        from xdog.agent.extensions.providers import model_from_registration
        return tuple(model_from_registration(name, provider, row)
                     for name, info in tuple(self._infos.items()) if name not in self._failed | self._unloaded
                     for provider in info.get('providers', []) for row in provider['models'])

    def provider_model(self, identifier: str) -> Model | None:
        return next((model for model in self.provider_models() if model.id == identifier), None)

    async def provider_events(self, identifier: str, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        await self.initialize()
        model = self.provider_model(identifier)
        if model is None or self._closed:
            raise ValueError("Explicit extension provider model is unavailable; no fallback")
        selected = next((name, provider, row['id']) for name, info in tuple(self._infos.items())
                        for provider in info.get('providers', []) for row in provider['models']
                        if 'ext-' + name + '-' + provider['name'] + '/' + row['id'] == identifier)
        name, declaration, short = selected
        provider, registration_id = declaration['name'], declaration.get('registration_id')
        generation, client = self.generation, self._manager._clients.get(name)
        async with owned_spool(request) as (file, size, checksum):
            token = ''
            def owner_alive() -> bool:
                return (not self._closed and self.generation == generation
                        and self._manager._clients.get(name) is client)
            def alive() -> bool:
                current = next((row for row in self._infos.get(name, {}).get('providers', [])
                                if row['name'] == provider), None)
                return (owner_alive() and current is not None and current.get('registration_id') == registration_id
                        and self.provider_model(identifier) == model)
            async def rpc(method: str, params: dict[str, Any], *, closing: bool = False) -> dict[str, Any]:
                if not (owner_alive() if closing else alive()):
                    raise ValueError("Extension provider selection changed")
                with silent_extension_ui():
                    return await self._manager._bridge(self._rpc(name, method, params))
            locked = [False]
            async def lock() -> None:
                await self._provider_lock.acquire()
                locked[0] = True
            try:
                await self._manager._bridge(lock())
                transfer = uuid.uuid4().hex
                await rpc('xdog/provider_begin', {'id': transfer, 'bytes': size, 'sha256': checksum})
                index = 0
                while data := await run_owned(file.read, BLOCK):
                    await rpc('xdog/provider_chunk', {'id': transfer, 'index': index,
                                                    'data': base64.b64encode(data).decode('ascii')})
                    index += 1
                started = await rpc('xdog/provider_start', {'id': transfer, 'chunks': index,
                                                           'provider': provider, 'model': short,
                                                           'registration_id': registration_id})
                token = started.get('id', '')
                if not isinstance(token, str) or re.fullmatch(r'[a-f0-9]{32}', token) is None:
                    raise ValueError("Invalid extension provider stream identity")
                while True:
                    result = await rpc('xdog/provider_next', {'id': token})
                    from xdog.agent.extensions.providers import wire_event
                    event = wire_event(result.get('event'))
                    if not alive():
                        raise ValueError("Extension provider owner changed before delivery")
                    yield event
                    if event['type'] in {'done', 'error'}:
                        return
            finally:
                file.close()
                try:
                    if token and owner_alive():
                        await rpc('xdog/provider_close', {'id': token}, closing=True)
                finally:
                    if locked[0] and not self._manager._closed:
                        async def unlock() -> None:
                            self._provider_lock.release()
                        await self._manager._bridge(unlock())

    @property
    def aliases(self) -> set[str]:
        return set(self._aliases)

    def tools(self) -> tuple[AgentTool, ...]:
        result = []
        specs = {tool.alias: tool for tool in self._manager._tools}
        for tool in self._manager.tools():
            spec = specs.get(tool.name)
            if spec is None:
                continue
            client = self._manager._clients.get(spec.server)
            def current(spec: McpTool = cast(McpTool, spec), client: Any = client) -> bool:
                return (not self._closed and spec.server not in self._failed | self._unloaded
                        and self._manager._clients.get(spec.server) is client and isinstance(client, _Client)
                        and client.state == "connected" and client._tool_snapshot is not None
                        and any(row["name"] == spec.remote_name and row["registration_id"] == spec.registration_id
                                for row in client._tool_snapshot["tools"]))
            async def validate(params: dict[str, Any], original: AgentTool = tool,
                               alive: Callable[[], bool] = current) -> None:
                if not alive():
                    raise ValueError("Extension tool registration changed before argument/approval admission")
                if original.validate_arguments is not None:
                    checked = original.validate_arguments(params)
                    if inspect.isawaitable(checked):
                        await checked
                if not alive():
                    raise ValueError("Extension tool registration changed during argument validation")
            if not current():
                continue  # Retain historical aliases/guards, but do not advertise retired registrations.
            name = tool.name.replace("mcp_", "ext_", 1)
            if len(self._aliases) >= 2048 and name not in self._aliases:
                raise ValueError("Extension historical tool alias capacity reached; start a fresh session")
            self._aliases.add(name)
            cached = self._tool_wrappers.get(name)
            if cached is not None and cached[0] is spec and cached[1] is client:
                result.append(cached[2])
            else:
                wrapped = replace(tool, name=name, label=tool.label.replace("MCP ", "Extension ", 1),
                                  validate_arguments=validate)
                self._tool_wrappers[name] = spec, client, wrapped
                result.append(wrapped)
        live = {tool.name for tool in result}
        self._tool_wrappers = {name: row for name, row in self._tool_wrappers.items() if name in live}
        return tuple(result)

    def sync_tools(self, agent: Any, *, allowed: tuple[str, ...] | None = None) -> None:
        """Project on the caller's owning agent loop, never from the worker reader thread."""
        current = self.tools()
        base = tuple(tool for tool in agent.state.tools if tool.name not in self._aliases)
        selected = (*base, *current)
        agent.set_tools(tuple(tool for tool in selected if allowed is None or tool.name in allowed))

    def tool_owner(self, alias: str) -> str:
        original = alias.replace("ext_", "mcp_", 1)
        return next((tool.server for tool in self._manager._tools if tool.alias == original), "")

    async def event(self, event: str, extra: dict[str, Any] | None = None) -> None:
        await self.initialize()
        if self._closed or not self.enabled:
            return
        async def run() -> None:
            for name, info in tuple(self._infos.items()):
                if name in self._failed | self._unloaded or event not in info["events"]:
                    continue
                try:
                    await self._rpc(name, "xdog/event", {"event": event,
                                                     "context": {**self.context(), **(extra or {})}})
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.fail(name)
        await self._manager._bridge(run())

    async def before_compaction(self, preparation: dict[str, Any]) -> "CompactionDecision":
        from xdog.agent.extensions.compaction import CompactionDecision, decision
        await self.initialize()
        if self._closed or self.validation_failed:
            raise ValueError('Extension compaction validation unavailable; unload failed extensions explicitly')
        if not self.enabled:
            return CompactionDecision()
        async def run() -> CompactionDecision:
            async with self._context_lock:
                selected = CompactionDecision()
                with silent_extension_ui():
                    for name, info in tuple(self._infos.items()):
                        if name in self._failed | self._unloaded or 'session_before_compact' not in info['events']:
                            continue
                        client = self._manager._clients.get(name)
                        try:
                            if type(info.get('compaction_hooks')) is not int or info['compaction_hooks'] != 1:
                                raise ValueError('Updated compaction extension worker required')
                            async with asyncio.timeout(max(30, self._manager.configs[name].timeout)):
                                result = decision(await self._transfer_value(name, preparation, operation='compaction'))
                            if (self._closed or name in self._failed | self._unloaded
                                    or self._manager._clients.get(name) is not client):
                                raise ValueError('Compaction extension owner changed')
                            if result.cancel:
                                return replace(result, source=name)
                            if result.summary is not None:
                                if selected.summary is not None:
                                    raise ValueError('Multiple custom compaction providers conflict')
                                selected = replace(result, source=name)
                        except asyncio.CancelledError:
                            # A silent callback cannot be left holding the worker's
                            # reader/transfer owner after the host operation ends.
                            self.fail(name)
                            await self._manager._disconnect(name)
                            raise
                        except Exception:
                            self.fail(name)
                            await self._manager._disconnect(name)
                            raise ValueError('Extension compaction hook failed; '
                                             'original history retained, no replay') from None
                return selected
        return await self._manager._bridge(run())

    async def pre(self, tool: str, params: dict[str, Any]) -> dict[str, Any] | None:
        await self.initialize()
        if self.validation_failed:
            raise ValueError("Extension validation unavailable; explicitly unload failed extensions before tools run")
        if not self.enabled:
            return params
        async def run() -> dict[str, Any] | None:
            async with self._pre_lock:
                if self.validation_failed:
                    raise ValueError("Extension validation unavailable; unload failed extensions explicitly")
                current = dict(params)
                for name, info in tuple(self._infos.items()):
                    if name in self._unloaded or not info["pre"]:
                        continue
                    value = await self._rpc(name, "xdog/pre", {"name": tool, "arguments": current})
                    if value.get("blocked") is True:
                        return None
                    if not isinstance(value.get("arguments"), dict):
                        raise ValueError("Invalid extension parameter transform")
                    current = value["arguments"]
                return current
        return await self._manager._bridge(run())

    async def post(self, tool: str, params: dict[str, Any], text: str) -> str:
        if not self.enabled or self._closed:
            return text
        async def run() -> str:
            current = text
            for name, info in tuple(self._infos.items()):
                if name in self._failed | self._unloaded or not info["post"]:
                    continue
                value = await self._rpc(name, "xdog/post", {"name": tool, "arguments": params, "text": current})
                if not isinstance(value.get("text"), str):
                    raise ValueError("Invalid extension result transform")
                current = value["text"]
            return current
        return await self._manager._bridge(run())

    async def input(self, message: UserMessage, source: str = "prompt", *,
                    channel: str = "") -> UserMessage | HandledInput:
        """Canonical pre-admission text filtering, retaining original typed attachments/timestamps."""
        await self.initialize()
        participating = [name for name in self.enabled if name in self._input_required and name not in self._unloaded]
        if not participating:
            return message
        if any(name in self._failed for name in participating):
            raise InputRejected("Extension input validation unavailable; unload/reload the failed extension explicitly")
        parts = message.content if not isinstance(message.content, str) else (TextContent(text=message.content),)
        text = input_text("\n".join(part.text for part in parts if isinstance(part, TextContent)))
        images = tuple(part for part in parts if isinstance(part, ImageContent))
        metadata = {"source": source, "channel": channel,
                    "images": [{"mime_type": image.mime_type, "encoded_bytes": len(image.data)} for image in images]}
        async def run() -> str | HandledInput:
            current = text
            for name in participating:
                if name in self._failed or name in self._unloaded or self._closed:
                    raise InputRejected("Input hook owner changed; original input not admitted")
                try:
                    response = await self._rpc(name, "xdog/input", {"text": current, "metadata": metadata})
                    wire = response.get("decision")
                    if not isinstance(wire, dict):
                        raise ValueError("Input hook response omitted its explicit typed decision")
                    result = decision(wire)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.fail(name)
                    await self._manager._disconnect(name)
                    raise InputRejected("Extension input hook failed; original input not admitted, no replay") from None
                if result.action == "handled":
                    return HandledInput(result.feedback)
                if result.action == "reject":
                    raise InputRejected("Extension rejected input: " + result.reason)
                if result.action == "transform":
                    current = result.text
            if not current.strip() and not images:
                raise InputRejected("Input hook produced an empty text-only prompt; original input not admitted")
            return current
        transformed = await self._manager._bridge(run())
        if isinstance(transformed, HandledInput):
            return transformed
        if transformed == text:
            return message
        return replace(message, content=(TextContent(text=transformed), *images))

    def command_completions(self) -> dict[str, str]:
        """Current callback identities; metadata grants no editor/command/tool authority."""
        return {name: row['completion_id'] for name, (_, row) in self._command_routes().items()
                if 'completion_id' in row}

    async def complete_command(self, command: str, prefix: str, identity: str, *,
                               timeout: float = 1, argument_input: dict[str, Any] | None = None) -> dict[str, Any]:
        from xdog.agent.extensions.argument_completion import argument_context, completion_id, items, plain
        argument_input = argument_context(argument_input, prefix)
        plain(prefix, 4096)
        completion_id(identity)
        if type(timeout) not in (int, float) or not 0.1 <= timeout <= 5:
            raise ValueError('Invalid argument completion deadline')
        selected = command.lower()
        if self.command_completions().get(selected) != identity:
            return {'status': 'stale', 'items': []}
        route = self._command_route(selected)
        if route is None:
            return {'status': 'stale', 'items': []}
        name, row = route
        # Resolve the callback on the same current owner used by discovery.
        with silent_extension_ui():
            async with asyncio.timeout(timeout):
                result = await self._manager._bridge(self._rpc(name, 'xdog/command_complete', {
                    'name': row['name'], 'prefix': prefix, 'completion_id': identity,
                    'argument_context': argument_input}))
        if self.command_completions().get(selected) != identity or result.get('status') == 'stale':
            return {'status': 'stale', 'items': []}
        if set(result) != {'status', 'items'} or result['status'] != 'ok':
            raise ValueError('Invalid argument completion callback result')
        return {'status': 'ok', 'items': items(result['items'])}

    async def _native_command(self, command: str, args: str, route: tuple[str, dict[str, Any]],
                              binding: NativeCommandBinding) -> str:
        def routed() -> bool:
            return self._command_route(command) == route
        return await self._native_commands.command(binding, route[1]['name'], args, routed)

    async def command(self, command: str, args: str) -> str | ExtensionCommandResult:
        # Native dispatch stays on the real calling owner and never initializes
        # configured workers, including display-only workers, as a side effect.
        if command.lower() in self.command_conflicts():
            raise ValueError(f'Extension command conflict: /{command}; resolve the conflict or unload the extension')
        route = self._command_route(command)
        if route is not None:
            owner, row = route
            binding = self._native_commands.resolve(owner, row['name'])
            if binding is not None:
                return await self._native_command(command, args, route, binding)
        if route is None and self._native_commands.claims(command):
            raise ValueError('Native command owner is stale or conflicted')
        await self.initialize()
        if command.lower() in self.command_conflicts():
            raise ValueError(f'Extension command conflict: /{command}; resolve the conflict or unload the extension')
        route = self._command_route(command)
        if route is None:
            raise ValueError('Unknown extension command')
        name, row = route
        binding = self._native_commands.resolve(name, row['name'])
        if binding is not None:
            return await self._native_command(command, args, route, binding)
        result = await self._manager._bridge(self._rpc(name, "xdog/command", {"name": row["name"], "args": args}))
        if "xdog_command_result" in result:
            if type(result["xdog_command_result"]) is not int or result["xdog_command_result"] != 1:
                raise ValueError("Unsupported extension command result version")
            return ExtensionCommandResult.from_dict({"extension": name,
                "command": f"ext-{name}-{row['name']}".lower(), "result": result.get("command_content")})
        if not isinstance(result.get("output"), str):
            raise ValueError("Invalid extension command text")
        return cast(str, result["output"])

    async def manage(self, action: str, name: str = "") -> dict[str, Any]:
        await self.initialize()
        if action == "list":
            return self.snapshot()
        if name not in self.enabled or action not in {"reload", "unload"}:
            raise ValueError("Usage: /extensions [list|reload NAME|unload NAME] (global trusted IDs only)")
        async def run() -> None:
            self._generation += 1
            await self._cancel_view_jobs(name)
            client = self._manager._clients.get(name)
            if client is not None and client.state == "connected":
                await self._rpc(name, "xdog/event", {"event": "session_end"})
            await self._manager._disconnect(name)
            self._infos.pop(name, None)
            self._unloaded.add(name)
            for key in tuple(self._ui):
                if key.startswith(name + ":"):
                    self._ui.pop(key, None)
            if action == "reload":
                self._unloaded.discard(name)
                try:
                    await self._load(name)
                except BaseException:
                    self.fail(name)  # Failed reload is not an implicit authorized unload/bypass.
                    raise
        await self._manager._bridge(run())
        if not self._closed:
            self.publish({"type": "extension_ui", "snapshot": self.snapshot()})
        return self.snapshot()

    async def aclose(self) -> None:
        self.close()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name='extension-runtime-close')
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        await settle_cleanup(self._native_commands.aclose, self._manager.aclose,
                             message='Extension runtime cleanup failed')

    def close(self) -> None:
        self.views.close()
        self._tool_wrappers.clear()
        if not self._closed:
            self._generation += 1
        self.entries.close()
        self._closed = True
        self._native_commands.close()
        self._manager.close()


def validate_snapshot(value: Any) -> dict[str, Any]:
    """Validate bounded host snapshots before UI/completion consumes socket data."""
    if not isinstance(value, dict):
        raise ValueError("Invalid extension snapshot")
    if {"terminal_input", "terminal_generation", "terminal_events"} & value.keys():
        raise ValueError("Extension terminal input APIs have been removed")
    if set(value) - {'extensions', 'commands', 'ui', 'shortcuts', 'command_completions', 'command_owners'}:
        raise ValueError('Extension snapshots cannot restore pending dialogs or host controls')
    rows, commands, ui = value.get("extensions"), value.get("commands"), value.get("ui")
    if (not isinstance(rows, list) or len(rows) > 16 or not isinstance(commands, dict) or len(commands) > 64
            or not isinstance(ui, dict) or len(ui) > 32):
        raise ValueError("Invalid extension snapshot capacity")
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'name', 'state'} or not isinstance(row.get("name"), str)
                or not isinstance(row.get("state"), str)
                or row["state"] not in {"not loaded", "active", "failed", "unloaded"}):
            raise ValueError("Invalid extension status")
    for key, description in commands.items():
        if (not isinstance(key, str) or len(key) > 80 or not isinstance(description, str)
                or len(description.encode("utf-8")) > 1000):
            raise ValueError("Invalid extension command snapshot")
    owners = value.get('command_owners', {})
    active_owners = {row['name'] for row in rows if row['state'] == 'active'}
    if (not isinstance(owners, dict) or len(owners) > 64 or set(owners) - set(commands)
            or any(not isinstance(owner, str) or owner not in active_owners for owner in owners.values())):
        raise ValueError('Invalid extension command ownership snapshot')
    from xdog.agent.extensions.commands import owner_for, public_name
    for command in commands:
        qualified_owner = owner_for(command, {})
        if qualified_owner is None:
            public_name(command)
            if command not in owners:
                raise ValueError('Public extension command requires explicit active owner')
        elif command in owners and owners[command].lower() != qualified_owner:
            raise ValueError('Qualified extension command ownership cannot be changed')
        owner = owner_for(command, owners)
        if owner is None or owner.lower() not in {name.lower() for name in active_owners}:
            raise ValueError('Extension command requires an active owner')
    completions = value.get('command_completions', {})
    if not isinstance(completions, dict) or len(completions) > 64 or set(completions) - set(commands):
        raise ValueError('Invalid argument completion command catalog')
    from xdog.agent.extensions.argument_completion import completion_id
    for identity in completions.values():
        completion_id(identity)
    active = {row["name"] for row in rows if row["state"] == "active"}
    snapshot_rows(value.get("shortcuts", []), commands, active, owners=owners)
    for key, row in ui.items():
        if (not isinstance(key, str) or len(key) > 80 or not isinstance(row, dict)
                or set(row) - {'kind', 'key', 'lines'} or row.get("kind") not in {"status"}
                or 'key' in row and (not isinstance(row['key'], str)
                                    or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', row['key']) is None)
                or not isinstance(row.get("lines"), list)
                or len(row["lines"]) > 8 or any(not isinstance(line, str) or len(line.encode("utf-8")) > 1000
                                               for line in row["lines"])):
            raise ValueError("Invalid extension status snapshot")
    return value
