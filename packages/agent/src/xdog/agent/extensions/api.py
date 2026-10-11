"""Process-local trusted Python extension API; hosts retain tool/turn authority."""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import re
import uuid
import weakref
from collections import deque
from collections.abc import Callable
from typing import Any, Literal

from xdog.agent.extensions.dialogs import validate_answer, validate_dialog
from xdog.agent.extensions.hooks import EVENTS
from xdog.agent.extensions.provider_auth import ProviderAuth, bind_callback
from xdog.agent.extensions.provider_oauth import OAuthBinding, ProviderOAuth
from xdog.agent.extensions.shortcuts import shortcut_key
from xdog.agent.extensions.tool_result import _UNSET, make_result
from xdog.agent.mcp.manager import _schema
from xdog.ai.types import ImageContent


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
        self.terminal_hooks: list[Callable[..., Any]] = []
        self._call_id: contextvars.ContextVar[str] = contextvars.ContextVar("extension_call", default="")
        self._has_name_request = contextvars.ContextVar("extension_name_request", default=False)
        self._has_command_menu = contextvars.ContextVar("extension_command_menu", default=False)
        self._has_catalog = contextvars.ContextVar("extension_command_catalog", default=False)
        self._has_navigation = contextvars.ContextVar("extension_command_navigation", default=False)
        self._has_session = contextvars.ContextVar("extension_command_session", default=False)
        self._command_menu_session = contextvars.ContextVar("extension_command_menu_session", default="")
        self._session_name: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
            "extension_session_name", default=None)
        self._inspection: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
            "extension_invocation_inspection", default=None)
        self._tool_inventory: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
            "extension_invocation_tools", default=None)
        self._has_editor_write = contextvars.ContextVar("extension_editor_write", default=False)
        self._has_queued_input = contextvars.ContextVar("extension_queued_input", default=False)
        self._has_compaction_request = contextvars.ContextVar("extension_compaction_request", default=False)
        self._has_active_tools_request = contextvars.ContextVar("extension_active_tools_request", default=False)
        self._has_pending_messages = contextvars.ContextVar("extension_pending_messages", default=False)
        self._has_context_message = contextvars.ContextVar("extension_context_message", default=False)
        self._has_editor = contextvars.ContextVar("extension_command_editor", default=False)
        self._has_custom = contextvars.ContextVar("extension_custom_view", default=False)
        self._has_overlay_controls = contextvars.ContextVar("extension_overlay_controls", default=False)
        self._has_refresh = contextvars.ContextVar("extension_custom_refresh", default=False)
        self._has_ui: contextvars.ContextVar[bool] = contextvars.ContextVar("extension_has_ui", default=False)
        self._custom_callbacks: dict[str, tuple[Callable[..., Any], Callable[..., Any], str]] = {}
        self._dialog_events: dict[str, asyncio.Event] = {}
        self._dialogs: dict[str, tuple[dict[str, Any], asyncio.Future[Any]]] = {}
        self._frame_owners: dict[str, tuple[str, str]] = {}
        self._retired_dialogs: dict[str, deque[str]] = {}
        self._render_notify: Callable[[dict[str, str]], None] | None = None
        self._render_scheduled: set[str] = set()
        self._overlay_notify: Callable[[dict[str, Any]], None] | None = None
        self._overlay_sequence = 0
        self._overlay_pending: dict[str, list[dict[str, Any]]] = {}
        self._custom_admitted: set[str] = set()
        self._view_bindings: weakref.WeakKeyDictionary[asyncio.Task[Any], tuple[str, str]] = weakref.WeakKeyDictionary()
        self._custom_phase = contextvars.ContextVar("extension_custom_phase", default="")

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

    async def form(self, title: str, fields: list[dict[str, Any]], *, message: str = "",
                   timeout: float = 300) -> dict[str, Any] | None:
        """Reviewed typed fields; None on cancellation/headless, never implicit defaults."""
        value = await self._ask("form", title, message, fields=fields, timeout=timeout)
        return value if isinstance(value, dict) else None

    async def request_editor_text(self, text: str, *, timeout: float = 300) -> bool:
        """Propose exact unsent text under original host review; True means queued only.

        No composer read/attachments/turn admission. Callback failure/cancellation
        discards the proposal; application still needs the host's original CAS lease.
        """
        from xdog.agent.extensions.editor_request import proposal
        text = proposal(text)
        if not self._has_editor_write.get() or self._custom_phase.get() == 'render':
            return False
        value = await self._ask('editor', 'Review extension draft proposal',
            'Replace only this owner’s unsent text after callback success and unchanged draft review. '
            'Attachments stay owned; nothing is sent and no tool permission changes.',
            operation='propose', text=text, timeout=timeout)
        return isinstance(value, dict) and value.get('approved') is True

    async def get_editor(self, *, timeout: float = 30) -> dict[str, Any] | None:
        """Captured command draft; unavailable to tools/hooks/headless contexts."""
        if not self._has_editor.get():
            return None
        value = await self._ask("editor", "Read current command editor", "No agent input or permission changes",
                                operation="read", timeout=timeout)
        return value if isinstance(value, dict) else None

    async def set_editor(self, text: str, revision: str, *, timeout: float = 30) -> bool:
        """CAS text replacement under the same command lease; never submit a turn."""
        if not self._has_editor.get():
            return False
        value = await self._ask("editor", "Replace captured command editor", "Attachments stay owned by the composer",
                                operation="replace", text=text, revision=revision, timeout=timeout)
        return bool(value.get("applied")) if isinstance(value, dict) else False

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

    async def set_session_name(self, name: str, *, timeout: float = 300) -> bool:
        """Queue reviewed metadata CAS after callback success; True is not a persistence receipt."""
        from xdog.agent.session_name import session_name
        selected = session_name(name)
        if not self._has_session.get():
            captured = self._session_name.get()
            if not self._has_name_request.get() or captured is None:
                return False
            return await self._ask('session', 'Review original parent session name',
                'Queue metadata CAS only after this callback and its original producer successfully settle. '
                'No history, composer, model or tool permission changes.', operation='rename', name=selected,
                expected_name=captured['name'], name_after_run=True, timeout=timeout) is True
        current = await self.get_session_name(timeout=timeout)
        if current is None:
            return False
        return await self._ask('session', 'Review extension session name',
            'Only metadata changes after callback success. No history/model/tool/permission change.',
            operation='rename', name=selected, expected_name=current, timeout=timeout) is True

    async def get_command_catalog(self, *, query: str = '', offset: int = 0, revision: str = '',
                                  timeout: float = 30) -> dict[str, Any] | None:
        """Read this invocation's explicitly bound host menu; never execute or borrow another owner."""
        from xdog.agent.extensions.command_catalog import request
        options = {'query': query, 'offset': offset, 'revision': revision}
        request(options)
        if (not self._has_command_menu.get() and not self._has_session.get()) or self._custom_phase.get() == 'render':
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

    async def set_active_tools(self, names: list[str] | None, revision: int, *, timeout: float = 300) -> bool:
        """Stage reviewed selection; non-command hosts also wait for the entire producer.

        True is staged, never applied. The owning host retains its idle durable
        CAS, permissions, inventory revision, and child profile ceilings.
        """
        from xdog.agent.extensions.active_tools import request
        request({'operation': 'select_tools', 'names': names, 'revision': revision})
        if not self._has_session.get() and not self._has_active_tools_request.get():
            return False
        return await self._ask('session', 'Review active tool selection',
            'Request deferred selection only; existing permissions and child ceilings remain.',
            operation='select_tools', names=names, revision=revision, timeout=timeout,
            **({'after_run': True} if not self._has_session.get() else {})) is True

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

    async def request_context_message(self, custom_type: str, content: Any, *, display: bool = True,
                                      details: Any = None, deliver_as: str = 'next_turn', trigger_turn: bool = False,
                                      timeout: float = 300) -> bool:
        """Review canonical custom context for the next explicit user prompt, never trigger a turn.

        Content is text or a tuple of typed text/image parts. Details stay opaque
        metadata. Child owners, render callbacks and unsupported hosts are refused.
        """
        from xdog.agent.extensions.context_message import proposal
        value = proposal(custom_type, content, display=display, details=details,
                         delivery=deliver_as, trigger_turn=trigger_turn)
        if not self._has_context_message.get():
            return False
        return await self._ask('session', 'Review extension context delivery',
            'Apply only the reviewed delivery/trigger policy after successful original callback. '
            'next_turn never triggers a turn; steer/follow_up use canonical custom context, not user input. '
            'Metadata is not model input or tool permission.',
            operation='context_message', timeout=timeout, **value) is True

    async def request_message(self, text: str = "", *, images: tuple[ImageContent, ...] = (),
                              timeout: float = 300) -> bool:
        """Queue an explicitly reviewed text/image new turn after command success.

        True is request acceptance, not model execution or completion. The host
        labels extension provenance and retains normal input hooks/permissions.
        """
        from xdog.agent.extensions.message_request import proposal
        value = proposal(text, images)
        if not self._has_session.get():
            return False
        return await self._ask('session', 'Review extension message',
            'After this callback succeeds, start a new model turn with this extension-origin text and explicit images. '
            'Existing tool approvals still apply. Unsent drafts and attachments are not included.',
            operation='message', timeout=timeout, **value) is True

    async def has_pending_messages(self, *, timeout: float = 30) -> bool | None:
        """Read the original Agent's live pending boolean; None means unavailable.

        No queued text, images, IDs, ordering, another host's queue or write grant
        is returned. This is a host read at call time, not an invocation snapshot.
        """
        if not self._has_pending_messages.get() or self._custom_phase.get() == 'render':
            return None
        value = await self._ask('session', 'Inspect pending input', 'Owning Agent boolean only',
                                operation='pending_messages', timeout=timeout, _host_read=True)
        return value if type(value) is bool else None

    async def request_queued_input(self, text: str, *, delivery: Literal['steer', 'follow_up'] = 'steer',
                                   images: tuple[ImageContent, ...] = (), timeout: float = 300) -> bool:
        """Request reviewed active-run input after callback success, never start a new turn.

        True means staged, not admitted/consumed. Images are explicit inline values,
        never borrowed drafts, paths or references. Host model/permission policy remains.
        """
        from xdog.agent.extensions.queued_input import proposal
        value = proposal(text, delivery, images)
        if not self._has_queued_input.get() or self._custom_phase.get() == 'render':
            return False
        return await self._ask('session', 'Review extension queued input',
            'Queue only to the original active run after callback success and explicit review. '
            'This does not start a new turn or include unsent drafts. Existing permissions still apply.',
            operation='queue_input', **value, timeout=timeout) is True

    async def request_compaction(self, instructions: str = '', *, timeout: float = 300) -> bool:
        """Queue reviewed host compaction after command success; True is not completion.

        Tool/hooks require a separately supported original parent-run review owner.
        The host, not the extension worker, runs the canonical compaction lifecycle.
        """
        from xdog.agent.summarization import summary_instructions
        instructions = summary_instructions(instructions)
        if not self._has_session.get() and not self._has_compaction_request.get():
            return False
        return await self._ask('session', 'Review extension compaction',
            'After callback success, the host may call the selected model, replace older context, '
            'and run permitted memory-flush tools. Existing permissions remain.',
            operation='compact', instructions=instructions, timeout=timeout,
            **({'after_run': True} if not self._has_session.get() else {})) is True

    async def open_sessions(self, *, tree: bool = False, timeout: float = 30) -> bool:
        """Queue the native browser after successful command completion; user selects activation."""
        if not self._has_session.get():
            return False
        value = await self._ask("session", "Open native session browser", "No automatic activation or agent input",
                                operation="browse", scope="tree" if tree else "sessions", timeout=timeout)
        return value is True

    async def navigate_session(self, action: str, *, session_id: str = "", branch_id: str = "",
                               index: int = -1, timeout: float = 300) -> bool:
        """Queue reviewed navigation after callback success, not a completion/activation receipt."""
        if not self._has_navigation.get():
            return False
        result = await self._ask("session", "Review extension session navigation",
            "This changes the active conversation only after callback success. Existing permissions remain.",
            operation="navigate", target={"action": action, "session_id": session_id,
                                          "branch_id": branch_id, "index": index}, timeout=timeout)
        return result is True

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

    async def custom(self, title: str, render: Callable[..., Any], on_input: Callable[..., Any], *,
                     message: str = "", timeout: float = 300, fullscreen: bool = False,
                     key_events: tuple[str, ...] = ("press",), overlay: dict[str, Any] | None = None,
                     live_overlay: bool = False) -> Any:
        """Viewport-aware owned view; callbacks produce data only, never terminal commands or host approval."""
        if not callable(render) or not callable(on_input):
            raise ValueError("Custom views require render/input callbacks")
        if not self._has_ui.get() or not self._has_custom.get():
            return None
        from xdog.agent.extensions.custom import options as custom_options
        presentation = custom_options({'fullscreen': fullscreen, 'key_events': key_events,
                                       'overlay': overlay, 'live_overlay': live_overlay})
        if presentation.get('live_overlay') and not self._has_overlay_controls.get():
            return None
        call_id = self._call_id.get()
        if not call_id or call_id in self._dialogs or call_id in self._custom_callbacks:
            raise ValueError("Custom view does not have an available callback owner")
        view_id = uuid.uuid4().hex
        task = asyncio.current_task()
        if task is not None:
            self._view_bindings.pop(task, None)  # This task explicitly starts a new view invocation.
        self._custom_callbacks[call_id] = render, on_input, view_id
        try:
            return await self._ask("custom", title, message, view_id=view_id, timeout=timeout,
                                   fullscreen=fullscreen, key_events=key_events, overlay=overlay,
                                   live_overlay=live_overlay)
        finally:
            self._custom_callbacks.pop(call_id, None)
            self._custom_admitted.discard(call_id)
            self._overlay_pending.pop(call_id, None)

    def _custom_request_owner(self) -> str:
        owner = self._call_id.get()
        while owner not in self._custom_callbacks and owner in self._frame_owners:
            owner = self._frame_owners[owner][0]
        callbacks = self._custom_callbacks.get(owner)
        task = asyncio.current_task()
        if callbacks is None or task is None:
            return ''
        identity = owner, callbacks[2]
        bound = self._view_bindings.get(task)
        if bound is not None and bound != identity:
            return ''  # An old background task cannot adopt a sequential/nested replacement view.
        self._view_bindings[task] = identity
        return owner

    def request_render(self) -> bool:
        """Coalesce a repaint of this invocation's live custom view; never propose completion.

        Call on the worker event loop after changing background view state. A task
        created by the custom-view invocation retains its owner until that view closes.
        """
        if self._custom_phase.get() == "render":
            raise ValueError("Custom rendering cannot request another render")
        if not self._has_refresh.get():
            return False
        owner = self._custom_request_owner()
        callbacks, dialog = self._custom_callbacks.get(owner), self._dialogs.get(owner)
        if (callbacks is None or dialog is None or dialog[1].done()
                or dialog[0]["kind"] != "custom" or self._render_notify is None):
            return False
        if owner in self._render_scheduled:
            return True
        self._render_scheduled.add(owner)
        identifier, view_id = dialog[0]["id"], callbacks[2]
        def send() -> None:
            self._render_scheduled.discard(owner)
            current = self._dialogs.get(owner)
            if (current is not None and not current[1].done() and current[0]["id"] == identifier
                    and self._custom_callbacks.get(owner) == callbacks and self._render_notify is not None):
                self._render_notify({"call_id": owner, "dialog_id": identifier, "view_id": view_id})
        asyncio.get_running_loop().call_soon(send)
        return True

    def request_overlay(self, action: str) -> bool:
        """Request hide/show/focus/unfocus for this live overlay; admission is not a focus receipt.

        The host preserves approvals, nested questions and other protected owners.
        """
        if action not in ('hide', 'show', 'focus', 'unfocus'):
            raise ValueError('Unknown custom overlay action')
        if self._custom_phase.get() == 'render':
            raise ValueError('Custom rendering cannot control overlay presentation')
        if not self._has_overlay_controls.get() or self._overlay_notify is None:
            return False
        owner = self._custom_request_owner()
        callbacks, item = self._custom_callbacks.get(owner), self._dialogs.get(owner)
        if (callbacks is None or item is None or item[1].done() or not item[0].get('live_overlay')
                or item[0].get('overlay') is None or self._overlay_sequence >= 2**53 - 1):
            return False
        queue = self._overlay_pending.get(owner)
        if queue is not None and len(queue) >= 32:
            return False
        self._overlay_sequence += 1
        value = {'call_id': owner, 'dialog_id': item[0]['id'], 'view_id': callbacks[2],
                 'sequence': self._overlay_sequence, 'action': action}
        if queue is not None:
            queue.append(value)
            return True
        self._overlay_pending[owner] = [value]
        asyncio.get_running_loop().call_soon(self._flush_overlay, owner)
        return True

    def _flush_overlay(self, owner: str) -> None:
        if owner not in self._custom_admitted:
            return  # Keep bounded desired controls until the first owned host frame request.
        queued = self._overlay_pending.pop(owner, [])
        if not queued:
            return
        current, callbacks = self._dialogs.get(owner), self._custom_callbacks.get(owner)
        if (current is None or current[1].done() or current[0]['id'] != queued[0]['dialog_id']
                or callbacks is None or callbacks[2] != queued[0]['view_id'] or self._overlay_notify is None):
            return
        for update in queued:
            self._overlay_notify(update)

    async def _custom_frame(self, call_id: str, dialog_id: str, value: Any, *,
                            frame_call_id: str = "") -> dict[str, Any]:
        from xdog.agent.extensions.custom import event, frame
        owned = self._dialogs.get(call_id)
        callbacks = self._custom_callbacks.get(call_id)
        if (owned is None or callbacks is None or owned[0]["id"] != dialog_id or owned[1].done()
                or owned[0]["kind"] != "custom" or owned[0]["view_id"] != callbacks[2]):
            raise ValueError("Custom-view owner changed/closed")
        # A worker-owned frame callback can ask nested questions without lending
        # another command's ID/capabilities or overwriting its suspended parent.
        if frame_call_id and self._frame_owners.get(frame_call_id) != (call_id, dialog_id):
            raise ValueError("Custom frame callback owner changed")
        self._custom_admitted.add(call_id)
        if call_id in self._overlay_pending:
            asyncio.get_running_loop().call_soon(self._flush_overlay, call_id)
        token = self._call_id.set(frame_call_id or call_id)
        phase = self._custom_phase.set("input")
        try:
            request = event(value)
            done = None
            if request["kind"] == "key":
                request.setdefault("event_type", "press")
            if request["kind"] == "key" and request["event_type"] not in owned[0]["key_events"]:
                raise ValueError("Custom key event was not opted in")
            if request["kind"] != "resize":
                done = await invoke(callbacks[1], request)
                if done is not None and (not isinstance(done, dict) or set(done) != {"done", "value"}
                                         or done["done"] is not True):
                    raise ValueError("Custom input must return None or an explicit done/value proposal")
            self._custom_phase.set("render")
            rendered = await invoke(callbacks[0], request["width"], request["height"])
            if not isinstance(rendered, (list, dict)):
                raise ValueError("Custom render must return lines or a lines/cursor frame")
            content = {"lines": rendered} if isinstance(rendered, list) else dict(rendered)
            if set(content) - {"lines", "cursor"}:
                raise ValueError("Only custom input callbacks can propose completion")
            if done is not None:
                content.update(done)
            return frame(content, request)
        finally:
            self._custom_phase.reset(phase)
            self._call_id.reset(token)

    async def _ask(self, kind: str, title: str, message: str, **options: Any) -> Any:
        _host_read = options.pop("_host_read", False) is True
        if self._custom_phase.get() == "render":
            raise ValueError("Custom rendering cannot open dialogs")
        dialog = validate_dialog({"kind": kind, "title": title, "message": message,
                                  "id": uuid.uuid4().hex, **options})
        if not self._has_ui.get() and not (_host_read and self._has_pending_messages.get()
                                          and kind == "session" and options.get("operation") == "pending_messages"):
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

    def on_terminal_input(self, callback: Callable[..., Any], *,
                          events: tuple[str, ...] = ('key', 'paste')) -> Callable[[], None]:
        """Register bounded input events; release is opt-in observation, never editing authority."""
        from xdog.agent.extensions.terminal_input import event_kinds
        kinds = event_kinds(events)
        if not kinds:
            raise ValueError("Terminal input hooks require at least one event kind")
        if not callable(callback) or len(self.terminal_hooks) >= 32 or callback in self.terminal_hooks:
            raise ValueError("Terminal input hooks require distinct callables, at most 32 per extension")
        if not hasattr(self, '_terminal_event_kinds'):
            self._terminal_event_kinds: dict[int, tuple[str, ...]] = {}
        self._terminal_event_kinds[id(callback)] = kinds
        self.terminal_hooks.append(callback)
        registered = True
        def unsubscribe() -> None:
            nonlocal registered
            if not registered:
                return
            registered = False
            if callback in self.terminal_hooks:
                self.terminal_hooks.remove(callback)
                self._terminal_event_kinds.pop(id(callback), None)
        return unsubscribe

    def terminal_events_for(self, callback: Callable[..., Any]) -> tuple[str, ...]:
        if not hasattr(self, '_terminal_event_kinds'):
            return ('key', 'paste')
        return self._terminal_event_kinds.get(id(callback), ('key', 'paste'))

    def terminal_events(self) -> list[str]:
        return [kind for kind in ('key', 'paste', 'release')
                if any(kind in self.terminal_events_for(callback) for callback in self.terminal_hooks)]

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
                    **({'completion_id': self._command_completions[name][0]}
                       if name in self._command_completions else {})} for name, row in self.commands.items()],
                "shortcuts": [dict(row) for row in self.shortcuts]}

    def command(self, name: str, description: str, callback: Callable[..., Any], *,
                replace: bool = False, complete: Callable[..., Any] | None = None) -> Callable[[], None]:
        name = identifier(name)
        if (not callable(callback) or complete is not None and not callable(complete)
                or type(replace) is not bool or not isinstance(description, str)
                or len(description.encode()) > 1000 or name in self.commands and not replace
                or name not in self.commands and len(self.commands) >= 32
                or any(key != name and key.lower() == name.lower() for key in self.commands)):
            raise ValueError("Invalid/duplicate/capacity-limited extension command")
        entry = (description, callback)
        self._advance_command_revision()
        self.commands[name] = entry
        self._command_completions.pop(name, None)
        if complete is not None:
            self._command_completions[name] = uuid.uuid4().hex, complete
        def dispose() -> None:
            if self.commands.get(name) is entry:
                self._advance_command_revision()
                self.commands.pop(name)
                self._command_completions.pop(name, None)
                self.shortcuts[:] = [row for row in self.shortcuts if row["name"].lower() != name.lower()]
        return dispose

    def shortcut(self, name: str, key: str, description: str, callback: Callable[..., Any]) -> Callable[[], None]:
        """Owned command+binding; removal cannot consume typing or mutate host key policy."""
        name, key = identifier(name), shortcut_key(key)
        if len(self.shortcuts) >= 32 or any(row["key"] == key for row in self.shortcuts):
            raise ValueError("Extension shortcut capacity/duplicate key")
        if not isinstance(description, str) or len(description.encode()) > 1000:
            raise ValueError("Invalid extension shortcut description")
        command_dispose = self.command(name, description, callback)
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

    def widget(self, key: str, lines: list[str] | None) -> None:
        key = identifier(key)
        if lines is None:
            self._ui("widget", key, [], clear=True)
        else:
            self._ui("widget", key, lines)

    def clear_status(self, key: str) -> None:
        self.status(key, None)

    def clear_widget(self, key: str) -> None:
        self.widget(key, None)

    def _ui(self, kind: str, key: str, lines: list[str], *, clear: bool = False) -> None:
        if (len(self.ui) >= 32 or not isinstance(lines, list) or len(lines) > 8
                or any(not isinstance(line, str) or len(line.encode("utf-8")) > 1000 for line in lines)):
            raise ValueError("Extension UI contribution limit exceeded")
        self.ui.append({"kind": kind, "key": key, "lines": list(lines), **({"clear": True} if clear else {})})

    def drain_ui(self) -> list[dict[str, Any]]:
        rows, self.ui = self.ui, []
        return rows
