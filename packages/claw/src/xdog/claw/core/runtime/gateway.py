"""Gateway daemon — Unix socket server for claw runtime.

Manages the Orchestrator lifecycle and accepts TUI client connections
over a Unix domain socket using a JSON-line protocol.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import stat
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from xdog.agent import AgentConfig
from xdog.agent.extensions.custom import rich as custom_rich
from xdog.agent.extensions.forms import multi_choice_form
from xdog.agent.mcp.completion import McpCompletion
from xdog.agent.mcp.context import McpDocument, McpEntry
from xdog.agent.mcp.manager import McpManager
from xdog.agent.mcp.subscriptions import CATALOGS, notification_filters, resource_uri
from xdog.agent.supervision_config import ChildConfig
from xdog.ai.types import ImageContent, StreamOptions, ThinkingLevel
from xdog.claw.channels.tui.image_upload import ImageUploads
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.elicitation_protocol import ElicitationConnection
from xdog.claw.core.runtime.mcp_protocol import McpSender
from xdog.claw.core.runtime.orchestrator import Orchestrator, QueuedInputHost
from xdog.claw.core.runtime.parent_draft_protocol import ParentDraftConnection
from xdog.claw.core.runtime.sampling_protocol import SamplingConnection
from xdog.claw.core.runtime.session import AgentSession
from xdog.claw.core.runtime.source_protocol import SourceSender
from xdog.claw.core.runtime.terminal_protocol import TerminalInputJobs, TerminalTarget
from xdog.claw.core.runtime.transcript_source import SourceCapture
from xdog.claw.core.types import Group, UserInput

logger = logging.getLogger(__name__)

_HISTORY_FRAME_LIMIT = 512 * 1024


@dataclass(frozen=True, slots=True)
class _ClientRun:
    group_id: str
    task: asyncio.Task[None]
    started: asyncio.Event


def _history_chunks(history: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group projected history into JSON frames below the reader limit."""
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = 0
    for entry in history:
        encoded_size = len(json.dumps(entry, ensure_ascii=False).encode("utf-8")) + 1
        if current and current_size + encoded_size > _HISTORY_FRAME_LIMIT:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(entry)
        current_size += encoded_size
    if current:
        chunks.append(current)
    return chunks


_THINKING_LEVELS: tuple[ThinkingLevel, ...] = ("minimal", "low", "medium", "high", "xhigh")


def _as_thinking_level(raw: str | None) -> ThinkingLevel | None:
    """Narrow a configured string to a level the provider accepts.

    Group config comes from a file, so anything can be in there; an unknown
    value used to be forwarded verbatim instead of falling back."""
    for level in _THINKING_LEVELS:
        if raw == level:
            return level
    return None

# Maximum bytes per JSON-line message from client (1 MB)
_MAX_LINE_LENGTH = 1_048_576


def _build_model_and_options(config: ClawConfig) -> tuple[str, Any]:
    """Resolve the global model name and stream_fn from config.

    Returns ``(model_name, stream_fn)``.
    """
    from xdog.agent.helpers import stream_fn_from_provider
    from xdog.claw.core.runtime.group import resolve_model_name

    if not config.model and (not config.groups or any(not group.model_id for group in config.groups)):
        raise ValueError("No primary model configured. Run `xdog-claw onboard` first.")
    model_name = resolve_model_name(config.model)

    # Build stream_fn from ai runtime
    import xdog.ai as ai
    runtime = ai.load()
    sfn = stream_fn_from_provider(runtime) if runtime.active_providers() else None

    return model_name, sfn


def _groups_from_config(config: ClawConfig) -> list[Group]:
    """Convert GroupDef entries from config into Group instances."""
    groups = [
        Group(
            id=gdef.id,
            name=gdef.name,
            is_main=gdef.is_main,
            workspace=gdef.workspace,
            enabled_tools=config.enabled_tools,
            image_model=config.image_model,
            agent_config=AgentConfig(
                model=gdef.model_id,
                options=StreamOptions(
                    thinking=_as_thinking_level(gdef.thinking_level),
                    temperature=gdef.temperature,
                    max_tokens=gdef.max_tokens,
                ),
            ),
        )
        for gdef in config.groups
    ]
    return groups or [Group(
        id="main", name="Claw", is_main=True,
        enabled_tools=config.enabled_tools, image_model=config.image_model,
    )]


class GatewayServer:
    """Unix socket server that bridges TUI clients to the Orchestrator."""

    _SCHEDULER_POLL_INTERVAL = 30  # seconds between tick() calls

    def __init__(self, config: ClawConfig) -> None:
        self._config = config
        self._socket_path = Path(config.socket_path)
        self._pid_path = Path(config.pid_file)
        self._server: asyncio.Server | None = None
        self._orchestrator: Orchestrator | None = None
        self._scheduler_task: asyncio.Task[Any] | None = None
        self._client_tasks: set[asyncio.Task[Any]] = set()
        self._client_counter = 0
        self._group_runs: dict[str, str] = {}
        self._group_runs_lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        self._stop_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the gateway: orchestrator + socket server."""
        logger.info("Starting claw gateway...")
        if self._stop_task is not None:
            if not self._stop_task.done():
                raise RuntimeError("Gateway shutdown is still in progress")
            self._stop_task.result()
            self._stop_task = None
            self._shutdown_event.clear()

        # Initialize orchestrator
        data_dir = Path(self._config.data_dir)
        model, stream_fn = _build_model_and_options(self._config)
        self._orchestrator = Orchestrator(
            self._config,
            model=model, stream_fn=stream_fn,
            data_dir=data_dir,
        )

        # Register groups
        for group in _groups_from_config(self._config):
            self._orchestrator.register_group(group)

        try:
            for runtime in self._orchestrator._runtimes.values():
                current = runtime.get_new_session_id() or ""
                selected = runtime.transcript_store.get_extension_model(current) if current else ""
                if runtime.model.startswith("ext-") or selected:
                    session = runtime.get_or_create_session(allow_reset=False)
                    await session.initialize_extensions(require_model=True)
            await self._orchestrator.start()
        except BaseException:
            await self._orchestrator.stop()
            raise

        # Wire WeChat channel if enabled
        if self._config.weixin_enabled:
            try:
                self._start_weixin_channel()
            except Exception:
                logger.exception(
                    "Failed to start WeChat channel — gateway continues without it"
                )

        # Clean up stale socket
        if self._socket_path.exists():
            self._socket_path.unlink()

        # Ensure parent directory exists
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Start Unix socket server
        self._server = await asyncio.start_unix_server(
            self._on_client_connect, path=str(self._socket_path), limit=_MAX_LINE_LENGTH
        )

        # Restrict socket permissions to owner only (0600)
        os.chmod(str(self._socket_path), stat.S_IRUSR | stat.S_IWUSR)

        # Write PID file
        self._write_pid()

        # Start scheduler polling loop
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        logger.info("Gateway listening on %s (PID: %d)", self._socket_path, os.getpid())

    def _on_client_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Wrap client handler in a tracked task for clean shutdown."""
        self._client_counter += 1
        client_id = self._client_counter
        task = asyncio.create_task(self._handle_client(reader, writer, client_id))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        client_id: int = 0,
    ) -> None:
        """Handle a single TUI client connection."""
        peer = f"client-{client_id}"
        logger.info("Client connected: %s", peer)

        write_lock = asyncio.Lock()
        runs: dict[str, _ClientRun] = {}
        receipts: dict[str, tuple[str, dict[str, Any]]] = {}
        extension_jobs: set[asyncio.Task[None]] = set()
        extension_controls: dict[str, tuple[asyncio.Task[None], str, str]] = {}
        extension_started: set[str] = set()
        argument_jobs: dict[str, tuple[asyncio.Task[None], str, str]] = {}
        argument_started: set[str] = set()
        arguments_supported = False
        argument_context_supported = False
        argument_cancel_supported = False
        argument_canceling: set[str] = set()
        argument_context_jobs: set[str] = set()
        session_jobs: dict[str, tuple[asyncio.Task[None], str, str]] = {}
        name_read_jobs: dict[str, tuple[asyncio.Task[None], str, str]] = {}
        name_read_supported = False
        session_started: set[str] = set()
        compact_jobs: dict[str, tuple[asyncio.Task[None], str, str]] = {}
        compact_ids: set[str] = set()
        compact_started: set[str] = set()
        extension_compact_ids: set[str] = set()
        compaction_supported = False
        compact_canceled: set[str] = set()
        def cancel_compaction(identifier: str, *, repeat: bool = False) -> None:
            binding = compact_jobs.get(identifier)
            if binding is None or identifier in compact_canceled:
                return
            if identifier not in compact_started:
                # Let the task publish its one canceled result and settle the
                # reserved owner. Canceling before coroutine admission skips it.
                compact_canceled.add(identifier)
            elif repeat or not binding[0].cancelling():
                binding[0].cancel()
        saved_source_supported = False
        saved_timeline_supported = False
        child_jobs: set[asyncio.Task[None]] = set()
        auth_jobs: set[asyncio.Task[None]] = set()
        roots_supported = False
        root_jobs: dict[str, asyncio.Task[None]] = {}
        root_views: dict[str, tuple[str, tuple[str, int], McpManager, str, threading.Event]] = {}
        root_ids: set[str] = set()
        watch_jobs: set[asyncio.Task[None]] = set()
        watch_views: dict[str, tuple[str, str, McpManager, str]] = {}
        auth_closing: set[str] = set()
        auth_views: dict[str, tuple[str, tuple[str, int], str, threading.Event]] = {}
        child_receipts: dict[str, tuple[str, dict[str, Any] | None]] = {}
        dialog_owners: dict[str, tuple[str, str]] = {}
        child_dialog_owners: dict[str, tuple[str, str, str, str]] = {}
        custom_dialog_ids: set[str] = set()
        custom_child_ids: set[str] = set()
        rich_dialog_ids: set[str] = set()
        nested_dialog_ids: set[str] = set()
        child_ui_token = uuid.uuid4().hex
        child_ui_connected = True
        return_supported = False
        return_token = uuid.uuid4().hex
        return_revoked = threading.Event()
        navigation_supported = False
        session_supported = False
        inspection_supported = False
        name_supported = False
        active_tools_supported = False
        extension_compaction_supported = False
        extension_compaction_generation = 0
        provider_auth_supported = False
        provider_auth_generation = 0
        provider_auth_ids: set[str] = set()
        extension_message_supported = False
        context_message_supported = False
        context_delivery_supported = False
        context_message_generation = 0
        context_grants: dict[str, tuple[str, str, str, int, tuple[str, int]]] = {}
        extension_message_generation = 0
        message_images_supported = False
        message_images_generation = 0
        queued_input_supported = False
        queued_input_generation = 0
        child_queued_input_supported = False
        child_queued_input_generation = 0
        # Only extension additions on this connection/run are eligible for withdrawal.
        extension_queue: dict[str, tuple[str, str, str, str, str]] = {}
        # One-shot grants contain only host-produced text and the original command owner.
        message_grants: dict[str, tuple[str, str, int, str, Callable[[], bool], float, tuple[ImageContent, ...]]] = {}
        active_tools_generation = 0
        command_catalog_supported = False
        command_catalog_generation = 0
        command_menu_dialogs: dict[str, tuple[str, str, int]] = {}
        mcp_content_types_supported = False
        combined_watch_supported = False
        combined_watch_jobs: set[asyncio.Task[Any]] = set()
        catalog_watch_supported = False
        catalog_watch_jobs: set[asyncio.Task[Any]] = set()
        editor_supported = False
        editor_write_supported = False
        editor_write_generation = 0
        editor_write_reviews: dict[str, tuple[dict[str, Any], str, str, str, str, int]] = {}
        custom_nested_supported = False
        custom_rich_supported = False
        skills_supported = False
        private_jwt_supported = False
        private_auth_views: set[str] = set()
        tool_inventory_supported = False
        tool_inventory_jobs: dict[str, asyncio.Task[None]] = {}
        custom_live_overlay_supported = False
        live_overlay_dialog_ids: set[str] = set()
        custom_overlay_supported = False
        overlay_dialog_ids: set[str] = set()
        custom_supported = False
        forms_supported = False
        multi_forms_supported = False
        multi_form_ids: set[str] = set()
        command_results_supported = False
        reasoning_supported = False
        child_ui_loop = asyncio.get_running_loop()
        child_ui_writes: set[asyncio.Task[None]] = set()
        uploads = ImageUploads()

        async def write(data: dict[str, Any], *, strict: bool = False) -> None:
            if data.get('type') in {'input_consumed', 'input_returned', 'input_handled', 'input_rejected'}:
                queue_id = data.get('id')
                queued = extension_queue.get(queue_id) if isinstance(queue_id, str) else None
                if queued is not None and queued[1] == data.get('run_id'):
                    extension_queue.pop(str(queue_id), None)
            if (data.get('type') in {'extension_dialog', 'child_extension_dialog'}
                    and ('provider_auth_host' in data.get('dialog', {})
                         or 'provider_auth_menu' in data.get('dialog', {}))):
                if (not provider_auth_supported or data['type'] != 'extension_dialog'
                        or data.get('request_id') not in provider_auth_ids
                        or type(data['dialog'].get('provider_auth_generation')) is not int
                        or data['dialog']['provider_auth_generation'] != provider_auth_generation):
                    if self._orchestrator is not None:
                        gid = str(data.get('group_id', ''))
                        if data['type'] == 'child_extension_dialog':
                            self._orchestrator.answer_child_dialog(gid, str(data.get('session_id', '')),
                                str(data.get('child_id', '')), data['dialog']['id'], child_ui_token, None)
                        else:
                            self._orchestrator.answer_extension_dialog(gid,
                                self._orchestrator.get_new_session_id(gid) or '', data['dialog']['id'], None)
                    return
            if data.get("type") == "child_terminal_registry":
                if (not terminal_jobs.child_supported or not terminal_jobs.supported
                        or data.get("child_owner") != child_ui_token or self._orchestrator is None
                        or self._orchestrator.get_new_session_id(str(data.get("group_id", "")))
                        != data.get("session_id")):
                    return
                data = {**data, "terminal_owner": terminal_jobs.owner}
            if data.get("type") == "extension_result" and "command_content" in data and not command_results_supported:
                data = {key: value for key, value in data.items() if key != "command_content"}
                data["output"] = str(data.get("output", "")) + "; updated rich-command viewer required"
            if (data.get("type") in {"extension_dialog", "child_extension_dialog"}
                    and (data.get("dialog", {}).get("kind") == "form" and (not forms_supported
                             or multi_choice_form(data["dialog"]) and not multi_forms_supported)
                         or data.get("dialog", {}).get("kind") == "custom" and (not custom_supported
                             or custom_rich(data["dialog"]) and not custom_rich_supported
                             or data["dialog"].get("overlay") is not None and not custom_overlay_supported
                             or data["dialog"].get("live_overlay") and not custom_live_overlay_supported)
                         or data.get("dialog", {}).get("kind") == "editor" and (
                             data['dialog'].get('operation') == 'propose' and not editor_write_supported
                             or data['dialog'].get('operation') != 'propose' and not editor_supported)
                         or data.get("dialog", {}).get("kind") == "session" and (
                             not session_supported and data["dialog"].get("operation") != "commands"
                             and data["dialog"].get("name_after_run") is not True
                             and not (data['dialog'].get('operation') == 'select_tools'
                                      and data['dialog'].get('after_run') is True and after_run_tools.enabled)
                             or data["dialog"].get("operation") in ("model", "usage") and not inspection_supported
                             or data['dialog'].get('operation') in ('name', 'rename') and (
                                 not after_run_name.enabled if data['dialog'].get('name_after_run') is True
                                 else not name_supported)
                             or data['dialog'].get('operation') == 'commands' and not command_catalog_supported
                             or data['dialog'].get('operation') == 'compact' and (
                                 not after_run_compaction.enabled if data['dialog'].get('after_run') is True
                                 else not extension_compaction_supported)
                             or data['dialog'].get('operation') == 'message' and (not extension_message_supported
                                 or bool(data['dialog'].get('image_summaries')) and not message_images_supported)
                             or data['dialog'].get('operation') == 'context_message' and (
                                 not context_message_supported or data['dialog'].get('delivery') != 'next_turn'
                                 and not context_delivery_supported)
                             or data['dialog'].get('operation') == 'queue_input' and (
                                 not child_queued_input_supported if data.get('type') == 'child_extension_dialog'
                                 else not queued_input_supported)
                             or data['dialog'].get('operation') in {'active_tools', 'select_tools'}
                             and (not after_run_tools.enabled if data['dialog'].get('after_run') is True
                                  else not active_tools_supported))
                         or data.get("dialog", {}).get("operation") == "navigate" and not navigation_supported
                         or data.get("dialog", {}).get("parent_id") and not custom_nested_supported)
                    and self._orchestrator is not None):
                key = str(data["dialog"]["id"])
                group = str(data.get("group_id", ""))
                if data["type"] == "child_extension_dialog":
                    self._orchestrator.answer_child_dialog(group, str(data.get("session_id", "")),
                        str(data.get("child_id", "")), key, child_ui_token, None)
                else:
                    self._orchestrator.answer_extension_dialog(group,
                        self._orchestrator.get_new_session_id(group) or "", key, None)
                return  # Older viewers must not receive a form they cannot render/validate.
            if (data.get("type") == "extension_dialog" and data.get("dialog", {}).get("kind") == "session"
                    and data["dialog"].get("operation") in ("model", "usage", "name")
                    and self._orchestrator is not None):
                # The command/group reservation and original broker own this read.
                # No stale client footer/catalog, new model turn or UI answer is used.
                group = str(data.get("group_id", ""))
                session_id = self._orchestrator.get_new_session_id(group) or ""
                key = str(data["dialog"]["id"])
                inspected = self._orchestrator.inspect_extension_session(
                    group, session_id, key, data["dialog"]["operation"])
                self._orchestrator.answer_extension_dialog(group, session_id, key, inspected)
                return
            if (data.get('type') == 'child_extension_dialog'
                    and data.get('dialog', {}).get('operation') == 'queue_input'
                    or data.get('type') == 'child_extension_input_queued'):
                runtime = (self._orchestrator._runtimes.get(str(data.get('group_id', '')))
                           if self._orchestrator is not None else None)
                child_session = runtime._active_session if runtime is not None else None
                if (not child_queued_input_supported or child_session is None
                        or data.get('child_owner') != child_ui_token
                        or data.get('session_id') != child_session.meta.session_id
                        or type(data.get('queued_input_generation')) is not int
                        or data['queued_input_generation'] !=
                            child_session.children.extensions.queued_input_generation(child_ui_token)):
                    if data.get('type') == 'child_extension_input_queued':
                        raise ValueError('Original child queued-input grant changed')
                    if self._orchestrator is not None:
                        self._orchestrator.answer_child_dialog(str(data.get('group_id', '')),
                            str(data.get('session_id', '')), str(data.get('child_id', '')),
                            data['dialog']['id'], child_ui_token, False)
                    return
                data = {**data, 'child_queued_input_generation': child_queued_input_generation}
            if (data.get('type') == 'extension_dialog'
                    and data.get('dialog', {}).get('operation') == 'queue_input'):
                data = {**data, 'queued_input_generation': queued_input_generation}
            if (data.get('type') in {'extension_dialog', 'child_extension_dialog'}
                    and data.get('dialog', {}).get('operation') == 'propose'):
                identity = data['dialog'].get('editor_write', {})
                key = identity.get('id')
                if (not editor_write_supported or not isinstance(key, str) or key in editor_write_reviews
                        or len(editor_write_reviews) >= 8 or self._orchestrator is None):
                    if self._orchestrator is not None:
                        if data['type'] == 'child_extension_dialog':
                            self._orchestrator.answer_child_dialog(str(data.get('group_id', '')),
                                str(data.get('session_id', '')), str(data.get('child_id', '')),
                                data['dialog']['id'], child_ui_token, None)
                        else:
                            self._orchestrator.answer_extension_dialog(str(data.get('group_id', '')),
                                str(identity.get('session_id', '')), data['dialog']['id'], None)
                    return
                editor_write_reviews[key] = (dict(identity), str(data.get('group_id', '')),
                    str(identity.get('session_id', '')), str(data.get('child_id', '')),
                    str(data.get('child_owner', '')), editor_write_generation)
                data = {**data, 'dialog': {**data['dialog'], 'editor_write_generation': editor_write_generation}}
            if (data.get('type') == 'extension_dialog'
                    and data.get('dialog', {}).get('operation') == 'context_message'):
                data = {**data, 'context_message_generation': context_message_generation}
            if data.get('type') in {'extension_editor_write_commit', 'extension_editor_write_discard',
                                   'child_extension_editor_write_commit', 'child_extension_editor_write_discard'}:
                identity = data.get('editor_write', {})
                write_key = identity.get('id') if isinstance(identity, dict) else None
                review = editor_write_reviews.get(write_key) if isinstance(write_key, str) else None
                if (review is None or review[:5] != (identity, str(data.get('group_id', '')),
                        str(identity.get('session_id', '')), str(data.get('child_id', '')),
                        str(data.get('child_owner', '')))):
                    return
                editor_write_reviews.pop(str(write_key), None)
                if not editor_write_supported or review[5] != editor_write_generation:
                    return
                data = {**data, 'editor_write_generation': review[5], 'session_id': review[2]}
                strict = True
            if (data.get('type') == 'extension_dialog' and data.get('dialog', {}).get('operation') == 'commands'
                    and 'command_menu_generation' in data['dialog']):
                dialog = data['dialog']
                if (not command_catalog_supported or dialog['command_menu_generation'] != command_catalog_generation):
                    if self._orchestrator is not None:
                        self._orchestrator.answer_extension_dialog(str(data.get('group_id', '')),
                            str(dialog.get('command_menu', {}).get('session_id', '')), dialog['id'], None)
                    return
                command_menu_dialogs[dialog['id']] = (str(data.get('group_id', '')),
                    str(dialog.get('command_menu', {}).get('session_id', '')), command_catalog_generation)
            if data.get("type") == "extension_dialog" and self._orchestrator is not None:
                key = data.get("dialog", {}).get("id")
                group = str(data.get("group_id", ""))
                if isinstance(key, str):
                    dialog_owners[key] = group, self._orchestrator.get_new_session_id(group) or ""
                    if multi_choice_form(data["dialog"]):
                        multi_form_ids.add(key)
                    if data["dialog"].get("parent_id"):
                        nested_dialog_ids.add(key)
                    data["session_id"] = dialog_owners[key][1]
                    if data["dialog"]["kind"] == "custom":
                        if data["dialog"].get("overlay") is not None:
                            overlay_dialog_ids.add(key)
                        if data["dialog"].get("live_overlay"):
                            live_overlay_dialog_ids.add(key)
                        custom_dialog_ids.add(key)
                        if custom_rich(data["dialog"]):
                            rich_dialog_ids.add(key)
            elif data.get("type") == "extension_dialog_closed":
                command_menu_dialogs.pop(str(data.get("id", "")), None)
                custom_dialog_ids.discard(str(data.get("id", "")))
                rich_dialog_ids.discard(str(data.get("id", "")))
                overlay_dialog_ids.discard(str(data.get("id", "")))
                live_overlay_dialog_ids.discard(str(data.get("id", "")))
                nested_dialog_ids.discard(str(data.get("id", "")))
                multi_form_ids.discard(str(data.get("id", "")))
                dialog_owners.pop(str(data.get("id", "")), None)
            if data.get("type") == "child_extension_dialog":
                key = data.get("dialog", {}).get("id")
                if isinstance(key, str):
                    if multi_choice_form(data["dialog"]):
                        multi_form_ids.add(key)
                    if data["dialog"].get("parent_id"):
                        nested_dialog_ids.add(key)
                    child_dialog_owners[key] = (str(data.get("group_id", "")), str(data.get("session_id", "")),
                                                str(data.get("child_id", "")), child_ui_token)
                    if data["dialog"]["kind"] == "custom":
                        if data["dialog"].get("overlay") is not None:
                            overlay_dialog_ids.add(key)
                        if data["dialog"].get("live_overlay"):
                            live_overlay_dialog_ids.add(key)
                        custom_child_ids.add(key)
                        if custom_rich(data["dialog"]):
                            rich_dialog_ids.add(key)
            elif data.get("type") == "child_extension_dialog_closed":
                custom_child_ids.discard(str(data.get("id", "")))
                rich_dialog_ids.discard(str(data.get("id", "")))
                overlay_dialog_ids.discard(str(data.get("id", "")))
                live_overlay_dialog_ids.discard(str(data.get("id", "")))
                nested_dialog_ids.discard(str(data.get("id", "")))
                multi_form_ids.discard(str(data.get("id", "")))
                child_dialog_owners.pop(str(data.get("id", "")), None)
            async with write_lock:
                kind = str(data.get("type", ""))
                if kind in {"extension_custom_frame", "extension_custom_error", "extension_custom_control"}:
                    owner = dialog_owners.get(str(data.get("id", "")))
                    if (owner is None or not custom_supported
                            or str(data.get("id", "")) in rich_dialog_ids and not custom_rich_supported
                            or str(data.get("id", "")) in overlay_dialog_ids and not custom_overlay_supported
                            or str(data.get("id", "")) in live_overlay_dialog_ids and not custom_live_overlay_supported
                            or data.get("group_id") != owner[0]
                            or self._orchestrator is None
                            or self._orchestrator.get_new_session_id(owner[0]) != owner[1]):
                        return
                    data = {**data, "session_id": owner[1]}
                elif kind in {"child_extension_custom_frame", "child_extension_custom_error",
                              "child_extension_custom_control"}:
                    child = child_dialog_owners.get(str(data.get("id", "")))
                    if (child is None or not custom_supported
                            or str(data.get("id", "")) in rich_dialog_ids and not custom_rich_supported
                            or str(data.get("id", "")) in overlay_dialog_ids and not custom_overlay_supported
                            or str(data.get("id", "")) in live_overlay_dialog_ids and not custom_live_overlay_supported
                            or data.get("group_id") != child[0]
                            or data.get("session_id") != child[1] or data.get("child_id") != child[2]
                            or data.get("child_owner") != child[3] or self._orchestrator is None
                            or self._orchestrator.get_new_session_id(child[0]) != child[1]):
                        return
                if data.get("type") in {"source_begin", "source_chunk", "source_end",
                                       "mcp_data_begin", "mcp_data_chunk", "mcp_data_end"}:
                    if (self._orchestrator is None or self._orchestrator.source_lease(str(data["group_id"]))
                            != (data["session_id"], data["revision"])):
                        raise ValueError("Session/context changed before source frame could be sent")
                if strict:
                    # The legacy writer swallows disconnects. A delivery grant requires
                    # the actual complete result to pass drain without transport failure.
                    if writer.is_closing():
                        raise ConnectionError('Extension message transport closed')
                    writer.write((json.dumps(data) + '\n').encode('utf-8'))
                    await writer.drain()
                else:
                    await self._write_response(writer, data)

        def terminal_capture(group: str, session: str, generation: int) -> TerminalTarget:
            if self._orchestrator is None or extension_jobs:
                raise ValueError("Terminal runtime owner unavailable")
            return self._orchestrator.capture_terminal_input(group, session, generation)

        def child_terminal_capture(group: str, session: str, generation: int,
                                   child: str, token: str) -> TerminalTarget:
            if self._orchestrator is None or token != child_ui_token or not child_ui_connected:
                raise ValueError("Foreign child terminal connection")
            return self._orchestrator.capture_child_terminal(group, session, generation, child, token)

        terminal_jobs = TerminalInputJobs(terminal_capture, write, capture_child=child_terminal_capture)
        assert self._orchestrator is not None
        parent_draft_connection = ParentDraftConnection(self._orchestrator,
            Path(self._config.data_dir) / 'saved-parent-drafts', write)
        sampling_connection = SamplingConnection(self._orchestrator, write)
        elicitation_connection = ElicitationConnection(self._orchestrator, write)
        from xdog.claw.core.runtime.after_run_tools import AfterRunToolSelectionConnection
        after_run_tools = AfterRunToolSelectionConnection(self, write,
            lambda: child_ui_connected and not writer.is_closing())
        from xdog.claw.core.runtime.after_run_compaction import AfterRunCompactionConnection
        after_run_compaction = AfterRunCompactionConnection(self, write,
            lambda: child_ui_connected and not writer.is_closing())
        from xdog.claw.core.runtime.after_run_name import AfterRunNameConnection
        after_run_name = AfterRunNameConnection(self, write,
            lambda: child_ui_connected and not writer.is_closing())

        def child_ui_done(task: asyncio.Task[None]) -> None:
            child_ui_writes.discard(task)
            if not task.cancelled():
                task.exception()  # Disconnected output cannot leak an unobserved task exception.

        def child_ui(group: str, *, run_id: str = "", request_id: str = ""
                     ) -> tuple[str, Callable[[dict[str, Any]], None]]:
            def publish(event: dict[str, Any]) -> None:
                def send() -> None:
                    if child_ui_connected:
                        task = asyncio.create_task(write({**event, "group_id": group,
                                                         "origin_run": run_id, "origin_request": request_id}))
                        child_ui_writes.add(task)
                        task.add_done_callback(child_ui_done)
                if not child_ui_loop.is_closed():
                    child_ui_loop.call_soon_threadsafe(send)
            return child_ui_token, publish

        child_queue_senders: dict[str, Callable[[dict[str, Any]], Awaitable[bool | None]]] = {}
        def child_queue_host(group: str) -> Callable[[AgentSession], None] | None:
            if not child_queued_input_supported:
                return None
            if group not in child_queue_senders:
                async def send(event: dict[str, Any]) -> bool | None:
                    async def deliver() -> bool:
                        generation = child_queued_input_generation
                        if not child_ui_connected or not child_queued_input_supported or writer.is_closing():
                            return False
                        try:
                            await write({**event, 'group_id': group}, strict=True)
                            return (child_ui_connected and child_queued_input_supported
                                    and child_queued_input_generation == generation and not writer.is_closing())
                        except (ValueError, RuntimeError, OSError):
                            return False
                    from xdog.agent.supervision import settle_on
                    return await settle_on(child_ui_loop, deliver())
                child_queue_senders[group] = send
            def attach(session: AgentSession) -> None:
                session.children.extensions.set_queued_input_sender(child_ui_token, child_queue_senders[group])
                session.children.extensions.enable_queued_input(child_ui_token, child_queued_input_supported)
            return attach

        def context_message_host(group: str, run_id: str) -> Callable[[], bool] | None:
            if not context_message_supported:
                return None
            generation = context_message_generation
            def alive() -> bool:
                run = runs.get(run_id)
                return (child_ui_connected and context_message_supported and generation == context_message_generation
                        and not writer.is_closing() and run is not None and run.group_id == group
                        and not run.task.done() and self._group_runs.get(group) == run_id)
            return alive

        def queued_input_host(group: str, run_id: str, prefix: str) -> QueuedInputHost | None:
            if not queued_input_supported:
                return None
            generation = queued_input_generation
            def alive() -> bool:
                run = runs.get(run_id)
                return (child_ui_connected and queued_input_supported and generation == queued_input_generation
                        and not writer.is_closing() and run is not None and run.group_id == group
                        and not run.task.done() and self._group_runs.get(group) == run_id)
            async def apply(identifier: str, content: str, delivery: str, images: tuple[ImageContent, ...],
                            current: Callable[[], bool]) -> None:
                async def admit() -> None:
                    if self._orchestrator is None or not current():
                        return
                    session_id = self._orchestrator.get_new_session_id(group) or ''
                    reply = {'type': 'extension_input_queued', 'group_id': group, 'run_id': run_id,
                        'session_id': session_id, 'id': identifier, 'content': content, 'delivery': delivery,
                        'image_count': len(images), 'queued_input_generation': generation, 'success': False}
                    added = False
                    try:
                        await asyncio.to_thread(self._orchestrator.validate_images, group, images)
                        if not current():
                            raise ValueError('Original queue owner changed during image validation')
                        added = self._orchestrator.control_input(group, 'add', prefix + identifier, content,
                            delivery, images=images, images_validated=True)
                        if not added:
                            raise ValueError('Original active queue refused the proposal')
                        extension_queue[identifier] = group, run_id, session_id, prefix, content
                        await write({**reply, 'success': True}, strict=True)
                        if not current():
                            raise ValueError('Queue grant withdrawn before admission receipt settled')
                    except asyncio.CancelledError:
                        if added:
                            self._orchestrator.control_input(group, 'remove', prefix + identifier)
                        extension_queue.pop(identifier, None)
                        raise
                    except (ValueError, RuntimeError, OSError):
                        # The original callback is still settling: remove only its own
                        # unconsumed addition, never cancel the run or another queued input.
                        if added:
                            self._orchestrator.control_input(group, 'remove', prefix + identifier)
                        extension_queue.pop(identifier, None)
                        await write({**reply, 'error': 'Extension queue admission failed/withdrawn; no replay'})
                from xdog.agent.supervision import settle_on
                await settle_on(child_ui_loop, admit())
            return alive, apply

        def command_menu_host() -> tuple[Callable[[], bool], int] | None:
            if not command_catalog_supported:
                return None
            generation = command_catalog_generation
            return (lambda: child_ui_connected and command_catalog_supported
                    and command_catalog_generation == generation and not writer.is_closing()), generation

        def editor_write_host(group: str, run_id: str
                              ) -> tuple[Callable[[], bool], Callable[[dict[str, Any]], None]] | None:
            if not editor_write_supported:
                return None
            generation = editor_write_generation
            def alive() -> bool:
                return (child_ui_connected and editor_write_supported and generation == editor_write_generation
                        and not writer.is_closing())
            def publish(event: dict[str, Any]) -> None:
                def send() -> None:
                    if child_ui_connected:
                        task = asyncio.create_task(write({**event, 'group_id': group, 'run_id': run_id}))
                        child_ui_writes.add(task)
                        task.add_done_callback(child_ui_done)
                child_ui_loop.call_soon_threadsafe(send)
            return alive, publish

        def capture_source(group: str, session: str) -> SourceCapture:
            if self._orchestrator is None:
                raise ValueError("Orchestrator not initialized")
            return self._orchestrator.capture_source(group, session)

        source_sender = SourceSender(
            capture_source,
            lambda group: self._orchestrator.source_lease(group) if self._orchestrator else ("", -1),
            write,
        )
        from xdog.claw.core.runtime.child_source import prepare_child_source
        child_scope = ""
        def child_capture(group: str, scope: str) -> SourceCapture:
            if self._orchestrator is None:
                raise ValueError("Child source owner unavailable")
            session, separator, child = scope.rpartition(":")
            runtime = self._orchestrator._runtimes.get(group)
            if (not separator or runtime is None or (runtime.get_new_session_id() or "") != session
                    or runtime._active_session is None or group in self._orchestrator._navigation_groups):
                raise ValueError("Child source session changed")
            captured = runtime._active_session.children.supervisor.capture_source(child)
            return SourceCapture(scope, lambda: [captured.to_dict()], revision=runtime._source_revision)
        def child_current(group: str) -> tuple[str, int]:
            try:
                value = child_capture(group, child_scope)
                return child_scope, value.revision
            except (ValueError, RuntimeError):
                return "", -1
        async def cancel_argument_job(key: str) -> None:
            binding = argument_jobs.get(key)
            if binding is None or key in argument_canceling:
                return
            argument_canceling.add(key)
            binding[0].cancel()
            if key not in argument_started:
                # The existing done callback alone releases this reservation. A canceled
                # receipt must not precede that cleanup or reject a coalesced successor.
                await asyncio.gather(binding[0], return_exceptions=True)
                await write({'type': 'argument_completion_result', 'request_id': key,
                    'group_id': binding[1], 'session_id': binding[2], 'status': 'canceled', 'items': []})

        async def child_write(value: dict[str, Any]) -> None:
            await write({**value, "type": value["type"].replace("source_", "child_source_", 1)})
        child_sender = SourceSender(child_capture, child_current, child_write, prepare_child_source)

        async def fetch_mcp(request: dict[str, Any]) -> tuple[McpEntry, ...] | McpDocument | McpCompletion:
            if self._orchestrator is None:
                raise ValueError("Orchestrator not initialized")
            return await self._orchestrator.mcp_context(
                request["group_id"], request["action"], request["server"],
                request.get("name", ""), request.get("arguments", {}),
            )

        mcp_sender = McpSender(
            fetch_mcp,
            lambda group: self._orchestrator.source_lease(group) if self._orchestrator else ("", -1),
            write, content_types=lambda: mcp_content_types_supported,
        )
        try:
            while not reader.at_eof():
                try:
                    line = await reader.readuntil(b"\n")
                except asyncio.LimitOverrunError as exc:
                    await write({"type": "protocol_error" if runs else "error", "message": "Message too large"})
                    # readuntil leaves the oversized bytes buffered. Consume only
                    # that rejected frame, not its successor, without readline's
                    # second LimitOverrun -> ValueError conversion.
                    try:
                        await reader.readexactly(exc.consumed)
                        while True:
                            try:
                                await reader.readuntil(b"\n")
                                break
                            except asyncio.LimitOverrunError as more:
                                await reader.readexactly(more.consumed)
                    except asyncio.IncompleteReadError:
                        break
                    continue
                except asyncio.IncompleteReadError:
                    break

                if not line:
                    break

                try:
                    request = json.loads(line.decode("utf-8").strip())
                except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
                    await write({"type": "protocol_error" if runs else "error", "message": "Invalid JSON"})
                    continue

                if not isinstance(request, dict):
                    await write({
                        "type": "protocol_error" if runs else "error", "message": "Request must be a JSON object",
                    })
                    continue
                msg_type = request.get("type", "")
                if msg_type == 'ping' and 'child_image_return' in request:
                    value = request['child_image_return']
                    if type(value) is not int or value not in (0, 1):
                        await write({'type': 'protocol_error', 'message': 'child_image_return requires integer 0 or 1'})
                        continue
                    if return_supported != (value == 1):
                        return_revoked.set()
                        return_token = uuid.uuid4().hex
                        return_revoked = threading.Event()
                    return_supported = value == 1
                if msg_type == "ping" and "terminal_child_input" in request:
                    terminal_jobs.negotiate_child(request["terminal_child_input"])
                if msg_type == "ping" and "terminal_input" in request:
                    terminal_jobs.negotiate(request["terminal_input"])
                if msg_type == "ping" and "terminal_release_input" in request:
                    terminal_jobs.negotiate_release(request["terminal_release_input"])
                if msg_type == "terminal_input_cancel":
                    terminal_jobs.cancel(request)
                    continue
                if msg_type == "terminal_input":
                    await terminal_jobs.request(request)
                    continue
                if msg_type == 'ping' and 'saved_source' in request:
                    saved_source_supported = type(request['saved_source']) is int and request['saved_source'] == 1
                    if not saved_source_supported:
                        # Withdrawal invalidates any pending archived read, not a live model turn.
                        source_sender.cancel_saved()
                if msg_type == 'ping' and 'saved_timeline' in request:
                    saved_timeline_supported = type(request['saved_timeline']) is int and request['saved_timeline'] == 1
                    if not saved_timeline_supported:
                        source_sender.cancel_saved()
                if msg_type == 'saved_source_read':
                    rid, group, owner, target, branch = (request.get(key, '') for key in
                        ('request_id', 'group_id', 'session_id', 'target_session_id', 'branch_id'))
                    saved_scope = {'target_session_id': target, 'branch_id': branch}
                    if (not saved_source_supported or not isinstance(rid, str) or not 0 < len(rid) <= 128
                            or not isinstance(group, str) or not isinstance(owner, str)
                            or not isinstance(target, str)
                            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', target)
                            or not isinstance(branch, str)
                            or branch == '@timeline' and not saved_timeline_supported
                            or branch and branch != '@timeline'
                            and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', branch)):
                        await write({'type': 'source_error', 'request_id': rid, 'group_id': group,
                                     'session_id': owner, **saved_scope,
                                     'error': 'Updated saved-source gateway and valid owner/scope required'})
                    else:
                        def saved_capture(gid: str = group, sid: str = owner, tid: str = target,
                                          bid: str = branch) -> SourceCapture:
                            if (not saved_source_supported or self._orchestrator is None
                                    or bid == '@timeline' and not saved_timeline_supported):
                                raise ValueError('Saved source capability withdrawn')
                            return self._orchestrator.capture_saved_source(gid, sid, tid, bid)
                        source_sender.request(rid, group, owner, capture=saved_capture, scope=saved_scope)
                    continue
                if msg_type == 'ping' and 'session_compaction' in request:
                    compaction_supported = (type(request['session_compaction']) is int
                                            and request['session_compaction'] == 1)
                    if not compaction_supported:
                        for compact_id in tuple(compact_jobs):
                            cancel_compaction(compact_id)
                if msg_type == 'ping' and 'extension_name_after_run' in request:
                    after_run_name.negotiate(type(request['extension_name_after_run']) is int
                                            and request['extension_name_after_run'] == 1)
                if msg_type == 'extension_name_cancel':
                    accepted_name_cancel = after_run_name.cancel(request)
                    await write({'type': 'extension_name_cancel_ack', 'request_id': request.get('request_id'),
                                 'accepted': accepted_name_cancel})
                    continue
                if msg_type == 'ping' and 'extension_active_tools_after_run' in request:
                    after_run_tools.negotiate(type(request['extension_active_tools_after_run']) is int
                                              and request['extension_active_tools_after_run'] == 1)
                if msg_type == 'ping' and 'extension_compaction_after_run' in request:
                    after_run_compaction.negotiate(type(request['extension_compaction_after_run']) is int
                        and request['extension_compaction_after_run'] == 1)
                if msg_type == 'session_compact_cancel':
                    if after_run_compaction.cancel(request):
                        continue
                    identifier = request.get('request_id')
                    compact_binding = compact_jobs.get(identifier) if isinstance(identifier, str) else None
                    if (compact_binding is not None and isinstance(identifier, str)
                            and request.get('group_id') == compact_binding[1]
                            and request.get('session_id') == compact_binding[2]):
                        cancel_compaction(identifier, repeat=True)
                    continue
                if msg_type == 'session_compact':
                    from xdog.agent.extensions.compaction import CompactionVeto
                    from xdog.agent.summarization import summary_instructions
                    rid, group, sid = (request.get(key) for key in ('request_id', 'group_id', 'session_id'))
                    focus = request.get('instructions', '')
                    try:
                        if (not compaction_supported or not isinstance(rid, str) or not 0 < len(rid) <= 128
                                or rid in compact_ids or len(compact_ids) >= 256
                                or not isinstance(group, str) or not isinstance(sid, str)
                                or self._orchestrator is None or len(extension_jobs) >= 8):
                            raise ValueError('Updated compaction gateway and valid fresh owner/request required')
                        focus = summary_instructions(focus)
                        generation = request.get('extension_compaction_generation')
                        if 'extension_compaction_generation' in request and (
                                type(generation) is not int or generation != extension_compaction_generation
                                or not extension_compaction_supported):
                            raise ValueError('Reviewed extension compaction capability changed; no replay')
                        if not self._orchestrator.try_reserve_group(group):
                            raise ValueError('Compaction requires an idle group')
                    except (ValueError, RuntimeError) as exc:
                        await write({'type': 'session_compact_result', 'request_id': rid, 'group_id': group,
                                     'success': False, 'error': str(exc)})
                        continue
                    compact_ids.add(rid)
                    async def compact_job(identifier: str, gid: str, expected: str, instructions: str,
                                          generation: int | None) -> None:
                        compact_started.add(identifier)
                        result: dict[str, Any] = {'type': 'session_compact_result', 'request_id': identifier,
                                                  'group_id': gid, 'session_id': expected, 'success': False}
                        try:
                            if identifier in compact_canceled:
                                raise asyncio.CancelledError
                            assert self._orchestrator is not None
                            result.update(success=True, **await self._orchestrator.compact_session(
                                gid, expected, instructions, lambda: compaction_supported and child_ui_connected
                                and (generation is None or extension_compaction_supported
                                     and generation == extension_compaction_generation)))
                        except asyncio.CancelledError:
                            result.update(canceled=True,
                                error=('Compaction canceled before summary started; original history retained'
                                       if identifier in compact_canceled else
                                       'Compaction canceled; an admitted commit may have completed; '
                                       'inspect /transcript before retrying'))
                        except CompactionVeto as exc:
                            result['error'] = str(exc)
                        except Exception:
                            # Provider errors may contain credentials or submitted content.
                            result['error'] = 'Compaction failed; original history retained unless commit was admitted'
                        await write(result)
                    job = asyncio.create_task(compact_job(rid, group, sid, focus, generation))
                    extension_jobs.add(job)
                    compact_jobs[rid] = job, group, sid
                    if generation is not None:
                        extension_compact_ids.add(rid)
                    def compact_done(completed: asyncio.Task[None], identifier: str = str(rid),
                                     gid: str = str(group)) -> None:
                        extension_jobs.discard(completed)
                        compact_jobs.pop(identifier, None)
                        extension_compact_ids.discard(identifier)
                        compact_started.discard(identifier)
                        compact_canceled.discard(identifier)
                        if self._orchestrator is not None:
                            self._orchestrator.release_group_reservation(gid)
                        if not completed.cancelled():
                            completed.exception()
                    job.add_done_callback(compact_done)
                    continue
                if msg_type == 'ping' and 'skill_controls' in request:
                    skills_supported = type(request['skill_controls']) is int and request['skill_controls'] == 1
                if msg_type == 'skills_control':
                    rid, group, sid = (request.get(key) for key in ('request_id', 'group_id', 'session_id'))
                    action, slug = request.get('action', 'list'), request.get('slug', '')
                    reserved = False
                    try:
                        if (not skills_supported or not isinstance(rid, str) or not 0 < len(rid) <= 128
                                or not isinstance(group, str) or not isinstance(sid, str)
                                or not isinstance(action, str) or action not in {'list', 'load', 'unload'}
                                or not isinstance(slug, str)
                                or self._orchestrator is None or len(extension_jobs) >= 8):
                            raise ValueError('Updated skill controls and valid owner required')
                        if action != 'list':
                            reserved = self._orchestrator.try_reserve_group(group)
                            if not reserved:
                                raise ValueError('Skill selection requires an idle group')
                    except (ValueError, RuntimeError) as exc:
                        await write({'type': 'skills_result', 'request_id': rid, 'group_id': group,
                                     'success': False, 'error': str(exc)})
                        continue
                    async def skills_job(identifier: str, gid: str, expected: str, operation: str,
                                         name: str, owns: bool) -> None:
                        result: dict[str, Any] = {'type': 'skills_result', 'request_id': identifier,
                                                  'group_id': gid, 'success': False}
                        try:
                            assert self._orchestrator is not None
                            if not skills_supported:
                                raise ValueError('Skill capability withdrawn')
                            result.update(success=True, **await self._orchestrator.skills_control(
                                gid, expected, operation, name, lambda: skills_supported and child_ui_connected))
                        except (ValueError, RuntimeError, OSError) as exc:
                            result['error'] = str(exc)
                        await write(result)
                    job = asyncio.create_task(skills_job(rid, group, sid, action, slug, reserved))
                    extension_jobs.add(job)
                    def skills_done(completed: asyncio.Task[None], gid: str = str(group),
                                    owns: bool = reserved) -> None:
                        extension_jobs.discard(completed)
                        if owns and self._orchestrator is not None:
                            self._orchestrator.release_group_reservation(gid)
                        if not completed.cancelled():
                            completed.exception()
                    job.add_done_callback(skills_done)
                    continue
                if msg_type == 'ping' and 'mcp_private_key_jwt' in request:
                    private_jwt_supported = (type(request['mcp_private_key_jwt']) is int
                                             and request['mcp_private_key_jwt'] == 1)
                    if not private_jwt_supported and self._orchestrator is not None:
                        for private_view in tuple(private_auth_views):
                            binding = auth_views.get(private_view)
                            if binding is None or private_view in auth_closing:
                                continue
                            binding[3].set()  # Revoke token admission before asynchronous signer/POST settlement.
                            auth_closing.add(private_view)
                            async def close_private(key: str, owner: Any) -> None:
                                try:
                                    assert self._orchestrator is not None
                                    runtime_owner = self._orchestrator._runtimes.get(owner[0])
                                    if runtime_owner is not None:
                                        await runtime_owner.mcp.detach_auth(owner[2])
                                finally:
                                    auth_views.pop(key, None)
                                    auth_closing.discard(key)
                                    private_auth_views.discard(key)
                            private_closing = asyncio.create_task(close_private(private_view, binding))
                            auth_jobs.add(private_closing)
                            private_closing.add_done_callback(auth_jobs.discard)
                if msg_type == 'ping' and 'tool_inventory' in request:
                    tool_inventory_supported = type(request['tool_inventory']) is int and request['tool_inventory'] == 1
                    if not tool_inventory_supported:
                        for pending_inventory in tuple(tool_inventory_jobs.values()):
                            pending_inventory.cancel()
                if msg_type == 'tool_inventory_cancel':
                    canceled_inventory = tool_inventory_jobs.get(str(request.get('request_id', '')))
                    if canceled_inventory is not None:
                        canceled_inventory.cancel()
                    continue
                if msg_type == 'tool_inventory_read':
                    rid, group, session_id = (request.get(key) for key in ('request_id', 'group_id', 'session_id'))
                    try:
                        if (not tool_inventory_supported or not isinstance(rid, str) or not 0 < len(rid) <= 128
                                or not isinstance(group, str) or not isinstance(session_id, str)
                                or len(session_id) > 256 or type(request.get('include_selection', False)) is not bool
                                or self._orchestrator is None or len(tool_inventory_jobs) >= 8
                                or rid in tool_inventory_jobs):
                            raise ValueError('Invalid/unsupported/busy tool inventory request')
                        runtime = self._orchestrator._runtimes.get(group)
                        if runtime is None or session_id and (runtime.get_new_session_id() or '') != session_id:
                            raise ValueError('Tool inventory owner changed')
                        active = runtime.get_or_create_session(allow_reset=False)
                        if session_id and active.meta.session_id != session_id:
                            raise ValueError('Tool inventory owner changed')
                    except (ValueError, RuntimeError):
                        await write({'type': 'tool_inventory_result', 'request_id': rid, 'group_id': group,
                                     'session_id': session_id, 'success': False})
                        continue
                    async def inventory_job(identifier: str, gid: str, sid: str, owner: Any,
                                            runtime_owner: Any, requested_session: str,
                                            include_selection: bool) -> None:
                        try:
                            snapshot = await asyncio.to_thread(owner._extension_tools)
                            if (not tool_inventory_supported or runtime_owner._active_session is not owner
                                    or owner.meta.session_id != sid or snapshot is None):
                                raise ValueError('Tool inventory owner unavailable')
                            if not include_selection:
                                from xdog.agent.extensions.tool_inventory import SELECTION_FIELDS
                                snapshot = {key: value for key, value in snapshot.items()
                                            if key not in SELECTION_FIELDS}
                            await write({'type': 'tool_inventory_result', 'request_id': identifier,
                                         'group_id': gid, 'session_id': sid, 'requested_session_id': requested_session,
                                         'success': True, 'snapshot': snapshot})
                        except asyncio.CancelledError:
                            raise
                        except (ValueError, RuntimeError, TypeError, OSError):
                            await write({'type': 'tool_inventory_result', 'request_id': identifier,
                                         'group_id': gid, 'session_id': sid, 'requested_session_id': requested_session,
                                         'success': False})
                        finally:
                            if tool_inventory_jobs.get(identifier) is asyncio.current_task():
                                tool_inventory_jobs.pop(identifier, None)
                    job = asyncio.create_task(inventory_job(rid, group, active.meta.session_id,
                                                            active, runtime, session_id,
                                                            request.get('include_selection', False)))
                    tool_inventory_jobs[rid] = job
                    def inventory_done(done: asyncio.Task[None], identifier: str = str(rid)) -> None:
                        if tool_inventory_jobs.get(identifier) is done:
                            tool_inventory_jobs.pop(identifier, None)
                    job.add_done_callback(inventory_done)
                    extension_jobs.add(job)
                    job.add_done_callback(extension_jobs.discard)
                    continue
                if msg_type == 'ping' and any(key in request for key in
                        ('parent_saved_drafts', 'parent_saved_images', 'parent_saved_save', 'parent_saved_discard',
                         'parent_saved_uploads')):
                    parent_draft_connection.negotiate(request)
                if msg_type == 'parent_draft_control':
                    await parent_draft_connection.request(request)
                    continue
                if msg_type == 'ping' and 'mcp_elicitation' in request:
                    elicitation_connection.negotiate(request['mcp_elicitation'])
                if msg_type == 'mcp_elicitation_control':
                    await elicitation_connection.request(request)
                    continue
                if msg_type == 'ping' and 'mcp_sampling' in request:
                    sampling_connection.negotiate(request['mcp_sampling'])
                if msg_type == 'mcp_sampling_control':
                    await sampling_connection.request(request)
                    continue
                if msg_type == 'ping' and 'mcp_roots' in request:
                    roots_supported = type(request['mcp_roots']) is int and request['mcp_roots'] == 1
                    if not roots_supported:
                        for root_binding in tuple(root_views.values()):
                            root_binding[4].set()
                        for root_job in tuple(root_jobs.values()):
                            root_job.cancel()
                        for root_key, root_binding in tuple(root_views.items()):
                            if root_key not in root_jobs:
                                await root_binding[2].detach_roots(root_binding[3])
                                root_views.pop(root_key, None)
                if msg_type == "ping" and "mcp_combined_watch" in request:
                    combined_watch_supported = (type(request["mcp_combined_watch"]) is int
                                                and request["mcp_combined_watch"] == 1)
                    if not combined_watch_supported:
                        for task in tuple(combined_watch_jobs):
                            if not task.cancelling():
                                task.cancel()
                        for record in tuple(watch_views.values()):
                            async def detach_combined(owner: tuple[str, str, McpManager, str]) -> None:
                                await owner[2].detach_notification_watches(owner[3])
                            detached = asyncio.create_task(detach_combined(record))
                            watch_jobs.add(detached)
                            detached.add_done_callback(watch_jobs.discard)
                if msg_type == "ping" and "mcp_catalog_watch" in request:
                    catalog_watch_supported = (type(request["mcp_catalog_watch"]) is int
                                               and request["mcp_catalog_watch"] == 1)
                    if not catalog_watch_supported:
                        for task in tuple(catalog_watch_jobs):
                            if not task.cancelling():
                                task.cancel()
                        for record in tuple(watch_views.values()):
                            async def detach_catalog(owner: tuple[str, str, McpManager, str]) -> None:
                                await owner[2].detach_catalog_watches(owner[3])
                            detached = asyncio.create_task(detach_catalog(record))
                            watch_jobs.add(detached)
                            detached.add_done_callback(watch_jobs.discard)
                if msg_type == "ping" and "mcp_content_types" in request:
                    mcp_content_types_supported = (type(request["mcp_content_types"]) is int
                                                   and request["mcp_content_types"] == 1)
                if msg_type == "ping" and "extension_navigation" in request:
                    navigation_supported = (type(request["extension_navigation"]) is int
                                            and request["extension_navigation"] == 1)
                if msg_type == 'ping' and 'child_extension_queued_inputs' in request:
                    child_queued_input_generation += 1
                    child_queued_input_supported = (type(request['child_extension_queued_inputs']) is int
                                                    and request['child_extension_queued_inputs'] == 1)
                    if self._orchestrator is not None:
                        for queued_group, runtime in self._orchestrator._runtimes.items():
                            child_session = runtime._active_session
                            if child_session is not None:
                                child_session.children.extensions.enable_queued_input(child_ui_token, False)
                                attach_child_queue = child_queue_host(queued_group)
                                if attach_child_queue is not None:
                                    attach_child_queue(child_session)
                if msg_type == 'ping' and 'extension_context_delivery_modes' in request:
                    context_delivery_supported = (type(request['extension_context_delivery_modes']) is int
                                                   and request['extension_context_delivery_modes'] == 1)
                    context_message_generation += 1
                if msg_type == 'ping' and 'extension_context_messages' in request:
                    context_message_generation += 1
                    context_message_supported = (type(request['extension_context_messages']) is int
                                                  and request['extension_context_messages'] == 1)
                    if self._orchestrator is not None:
                        for dialog_id, (dialog_group, _sid) in tuple(dialog_owners.items()):
                            runtime = self._orchestrator._runtimes.get(dialog_group)
                            if runtime is not None and runtime._active_session is not None:
                                def owned_context(dialog: dict[str, Any], key: str = dialog_id) -> bool:
                                    return dialog.get('id') == key and dialog.get('operation') == 'context_message'
                                runtime._active_session.extension_dialogs.cancel_where(owned_context)
                if msg_type == 'ping' and 'extension_queued_inputs' in request:
                    queued_input_generation += 1
                    queued_input_supported = (type(request['extension_queued_inputs']) is int
                                              and request['extension_queued_inputs'] == 1)
                    if self._orchestrator is not None:
                        for queue_id, queued_owner in tuple(extension_queue.items()):
                            queue_group, queue_run, queue_session, queue_prefix, queue_content = queued_owner
                            if self._orchestrator.get_new_session_id(queue_group) == queue_session:
                                removed = self._orchestrator.control_input(
                                    queue_group, 'remove', queue_prefix + queue_id)
                                if removed:
                                    await write({'type': 'input_returned', 'run_id': queue_run, 'group_id': queue_group,
                                                 'id': queue_id, 'content': queue_content})
                            extension_queue.pop(queue_id, None)
                        for dialog_id, (dialog_group, _sid) in tuple(dialog_owners.items()):
                            runtime = self._orchestrator._runtimes.get(dialog_group)
                            if runtime is not None and runtime._active_session is not None:
                                def owned_queue(dialog: dict[str, Any], key: str = dialog_id) -> bool:
                                    return dialog.get('id') == key and dialog.get('operation') == 'queue_input'
                                runtime._active_session.extension_dialogs.cancel_where(owned_queue)
                if msg_type == 'ping' and 'extension_provider_auth' in request:
                    provider_auth_generation += 1
                    provider_auth_supported = (type(request['extension_provider_auth']) is int
                                               and request['extension_provider_auth'] == 1)
                    for auth_id in tuple(provider_auth_ids):
                        auth_control = extension_controls.get(auth_id)
                        if auth_control is not None and not auth_control[0].cancelling():
                            auth_control[0].cancel()
                            if auth_id not in extension_started:
                                await write({'type': 'extension_result', 'request_id': auth_id,
                                    'group_id': auth_control[1], 'success': False, 'canceled': True,
                                    'error': 'Provider authentication canceled before worker admission'})
                if msg_type == 'ping' and 'extension_message_images' in request:
                    message_images_generation += 1
                    message_images_supported = (type(request['extension_message_images']) is int
                                                and request['extension_message_images'] == 1)
                    for message_token, message_grant in tuple(message_grants.items()):
                        if message_grant[6]:
                            message_grants.pop(message_token, None)
                    if self._orchestrator is not None:
                        for dialog_id, (dialog_group, _sid) in tuple(dialog_owners.items()):
                            runtime = self._orchestrator._runtimes.get(dialog_group)
                            if runtime is not None and runtime._active_session is not None:
                                def owned_images(dialog: dict[str, Any], key: str = dialog_id) -> bool:
                                    return (dialog.get('id') == key and dialog.get('operation') == 'message'
                                            and bool(dialog.get('image_summaries')))
                                runtime._active_session.extension_dialogs.cancel_where(owned_images)
                if msg_type == 'ping' and 'extension_message' in request:
                    extension_message_generation += 1
                    extension_message_supported = (type(request['extension_message']) is int
                                                   and request['extension_message'] == 1)
                    message_grants.clear()
                    # Revoke only this connection's pending message reviews, never a run.
                    if self._orchestrator is not None:
                        for dialog_id, (dialog_group, _sid) in tuple(dialog_owners.items()):
                            runtime = self._orchestrator._runtimes.get(dialog_group)
                            if runtime is not None and runtime._active_session is not None:
                                def owned_message(dialog: dict[str, Any], key: str = dialog_id) -> bool:
                                    return dialog.get('id') == key and dialog.get('operation') == 'message'
                                runtime._active_session.extension_dialogs.cancel_where(owned_message)
                if msg_type == 'ping' and 'extension_compaction' in request:
                    extension_compaction_generation += 1
                    extension_compaction_supported = (type(request['extension_compaction']) is int
                                                      and request['extension_compaction'] == 1)
                    for compact_id in tuple(extension_compact_ids):
                        cancel_compaction(compact_id)
                    if self._orchestrator is not None:
                        for dialog_id, (dialog_group, _sid) in tuple(dialog_owners.items()):
                            runtime = self._orchestrator._runtimes.get(dialog_group)
                            if runtime is not None and runtime._active_session is not None:
                                def owned_compaction(dialog: dict[str, Any], key: str = dialog_id) -> bool:
                                    return dialog.get('id') == key and dialog.get('operation') == 'compact'
                                runtime._active_session.extension_dialogs.cancel_where(owned_compaction)
                if msg_type == 'ping' and 'extension_active_tools' in request:
                    active_tools_generation += 1
                    active_tools_supported = (type(request['extension_active_tools']) is int
                                              and request['extension_active_tools'] == 1)
                    if not active_tools_supported and self._orchestrator is not None:
                        for tool_dialog_id, (tool_group, _tool_session) in tuple(dialog_owners.items()):
                            runtime = self._orchestrator._runtimes.get(tool_group)
                            if runtime is not None and runtime._active_session is not None:
                                broker = runtime._active_session.extension_dialogs
                                def own_selection(dialog: dict[str, Any], owned: str = tool_dialog_id) -> bool:
                                    return dialog.get('id') == owned and dialog.get('operation') == 'select_tools'
                                broker.cancel_where(own_selection)
                if msg_type == 'ping' and 'extension_session_name' in request:
                    name_supported = (type(request['extension_session_name']) is int
                                      and request['extension_session_name'] == 1)
                if msg_type == 'ping' and 'session_name_read' in request:
                    name_read_supported = (type(request['session_name_read']) is int
                                           and request['session_name_read'] == 1)
                    if not name_read_supported:
                        for read_task, _, _ in tuple(name_read_jobs.values()):
                            read_task.cancel()
                if msg_type == 'ping' and 'extension_argument_context' in request:
                    argument_context_supported = (type(request['extension_argument_context']) is int
                                                  and request['extension_argument_context'] == 1)
                    if not argument_context_supported:
                        for argument_key in tuple(argument_context_jobs):
                            await cancel_argument_job(argument_key)
                if msg_type == 'ping' and 'extension_argument_cancel' in request:
                    argument_cancel_supported = (type(request['extension_argument_cancel']) is int
                                                 and request['extension_argument_cancel'] == 1)
                if msg_type == 'ping' and 'extension_argument_completion' in request:
                    arguments_supported = (type(request['extension_argument_completion']) is int
                                           and request['extension_argument_completion'] == 1)
                    if not arguments_supported:
                        for argument_key in tuple(argument_jobs):
                            await cancel_argument_job(argument_key)
                if msg_type == 'ping' and 'extension_command_catalog' in request:
                    enabled = (type(request['extension_command_catalog']) is int
                               and request['extension_command_catalog'] == 1)
                    if enabled != command_catalog_supported:
                        command_catalog_generation += 1
                    command_catalog_supported = enabled
                    if not enabled and self._orchestrator is not None:
                        for menu_key, (menu_group, menu_session, _generation) in tuple(command_menu_dialogs.items()):
                            self._orchestrator.answer_extension_dialog(menu_group, menu_session, menu_key, None)
                        command_menu_dialogs.clear()
                if msg_type == "ping" and "extension_session_inspection" in request:
                    inspection_supported = (type(request["extension_session_inspection"]) is int
                                            and request["extension_session_inspection"] == 1)
                if msg_type == "ping" and "extension_session" in request:
                    session_supported = type(request["extension_session"]) is int and request["extension_session"] == 1
                if msg_type == 'ping' and 'extension_editor_write' in request:
                    editor_write_generation += 1
                    editor_write_supported = (type(request['extension_editor_write']) is int
                                              and request['extension_editor_write'] == 1)
                    editor_write_reviews.clear()
                    if self._orchestrator is not None:
                        for runtime in self._orchestrator._runtimes.values():
                            session = runtime._active_session
                            if session is not None:
                                # Off/on cancels only this connection's proposals, not its tools/turns.
                                session.children.extensions.enable_editor_write(child_ui_token, False)
                                session.children.extensions.enable_editor_write(child_ui_token, editor_write_supported)
                        for dialog_id, (dialog_group, _sid) in tuple(dialog_owners.items()):
                            runtime = self._orchestrator._runtimes.get(dialog_group)
                            if runtime is not None and runtime._active_session is not None:
                                def owned_proposal(dialog: dict[str, Any], key: str = dialog_id) -> bool:
                                    return dialog.get('id') == key and dialog.get('operation') == 'propose'
                                runtime._active_session.extension_dialogs.cancel_where(owned_proposal)
                if msg_type == "ping" and "extension_editor" in request:
                    editor_supported = type(request["extension_editor"]) is int and request["extension_editor"] == 1
                if msg_type == "ping" and "extension_custom" in request:
                    custom_supported = type(request["extension_custom"]) is int and request["extension_custom"] == 1
                    if not custom_supported and self._orchestrator is not None:
                        for custom_id in tuple(custom_dialog_ids):
                            owner = dialog_owners.get(custom_id)
                            if owner is not None:
                                self._orchestrator.answer_extension_dialog(*owner, custom_id, None)
                        for custom_id in tuple(custom_child_ids):
                            child = child_dialog_owners.get(custom_id)
                            if child is not None:
                                self._orchestrator.answer_child_dialog(*child[:3], custom_id, child[3], None)
                if msg_type == "ping" and "extension_custom_rich" in request:
                    custom_rich_supported = (type(request["extension_custom_rich"]) is int
                                             and request["extension_custom_rich"] == 1)
                    if not custom_rich_supported and self._orchestrator is not None:
                        for rich_id in tuple(rich_dialog_ids):
                            rich_parent = dialog_owners.get(rich_id)
                            rich_child = child_dialog_owners.get(rich_id)
                            if rich_parent is not None:
                                self._orchestrator.answer_extension_dialog(*rich_parent, rich_id, None)
                            if rich_child is not None:
                                self._orchestrator.answer_child_dialog(*rich_child[:3], rich_id, rich_child[3], None)
                if msg_type == "ping" and "extension_custom_live_overlay" in request:
                    custom_live_overlay_supported = (type(request["extension_custom_live_overlay"]) is int
                                                     and request["extension_custom_live_overlay"] == 1)
                    if not custom_live_overlay_supported and self._orchestrator is not None:
                        for live_id in tuple(live_overlay_dialog_ids):
                            live_parent = dialog_owners.get(live_id)
                            live_child = child_dialog_owners.get(live_id)
                            if live_parent is not None:
                                self._orchestrator.answer_extension_dialog(*live_parent, live_id, None)
                            if live_child is not None:
                                self._orchestrator.answer_child_dialog(*live_child[:3], live_id, live_child[3], None)
                if msg_type == "ping" and "extension_custom_overlay" in request:
                    custom_overlay_supported = (type(request["extension_custom_overlay"]) is int
                                                and request["extension_custom_overlay"] == 1)
                    if not custom_overlay_supported and self._orchestrator is not None:
                        for overlay_id in tuple(overlay_dialog_ids):
                            overlay_parent = dialog_owners.get(overlay_id)
                            overlay_child = child_dialog_owners.get(overlay_id)
                            if overlay_parent is not None:
                                self._orchestrator.answer_extension_dialog(*overlay_parent, overlay_id, None)
                            if overlay_child is not None:
                                self._orchestrator.answer_child_dialog(
                                    *overlay_child[:3], overlay_id, overlay_child[3], None)
                if msg_type == "ping" and "extension_custom_nested" in request:
                    custom_nested_supported = (type(request["extension_custom_nested"]) is int
                                               and request["extension_custom_nested"] == 1)
                    if not custom_nested_supported and self._orchestrator is not None:
                        for nested_id in tuple(nested_dialog_ids):
                            nested_parent = dialog_owners.get(nested_id)
                            nested_child = child_dialog_owners.get(nested_id)
                            if nested_parent is not None:
                                self._orchestrator.answer_extension_dialog(*nested_parent, nested_id, None)
                            if nested_child is not None:
                                self._orchestrator.answer_child_dialog(
                                    *nested_child[:3], nested_id, nested_child[3], None)
                if msg_type == "ping" and "reasoning_controls" in request:
                    reasoning_supported = (type(request["reasoning_controls"]) is int
                                           and request["reasoning_controls"] == 1)
                if msg_type == "ping" and "extension_multi_forms" in request:
                    multi_forms_supported = (type(request["extension_multi_forms"]) is int
                                             and request["extension_multi_forms"] == 1)
                    if not multi_forms_supported and self._orchestrator is not None:
                        for form_id in tuple(multi_form_ids):
                            parent_form = dialog_owners.get(form_id)
                            child_form = child_dialog_owners.get(form_id)
                            if parent_form is not None:
                                self._orchestrator.answer_extension_dialog(*parent_form, form_id, None)
                            if child_form is not None:
                                self._orchestrator.answer_child_dialog(*child_form[:3], form_id, child_form[3], None)
                if msg_type == "ping" and "extension_forms" in request:
                    forms_supported = type(request["extension_forms"]) is int and request["extension_forms"] == 1
                if msg_type == "ping" and "extension_command_results" in request:
                    command_results_supported = (type(request["extension_command_results"]) is int
                                                 and request["extension_command_results"] == 1)
                if not isinstance(msg_type, str):
                    await write({"type": "protocol_error" if runs else "error", "message": "Invalid request type"})
                    continue
                if msg_type == "mcp_resource_watch":
                    rid, view, group, session = (request.get(key) for key in
                                                ("request_id", "view", "group_id", "session_id"))
                    action, server, uri = request.get("action"), request.get("server", ""), request.get("uri", "")
                    catalog = request.get("catalog", "")
                    combined = request.get("notifications")
                    watch_reply = {"type": "mcp_resource_watch_result", "request_id": rid, "view": view,
                             "group_id": group, "session_id": session, "success": False}
                    try:
                        if (not isinstance(rid, str) or not 0 < len(rid) <= 128 or not isinstance(view, str)
                                or re.fullmatch(r"[a-f0-9]{32}", view) is None or not isinstance(group, str)
                                or not isinstance(session, str) or not isinstance(server, str)
                                or action not in ("watch", "unwatch", "close") or len(watch_jobs) >= 8
                                or self._orchestrator is None):
                            raise ValueError("Invalid/busy resource watch request")
                        watch_binding = watch_views.get(view)
                        if action == "close":
                            watch_views.pop(view, None)
                        else:
                            if "notifications" in request:
                                if not combined_watch_supported or catalog or uri:
                                    raise ValueError("Updated combined-watch capability required")
                                combined = notification_filters(combined)
                            elif catalog:
                                if (not catalog_watch_supported or not isinstance(catalog, str)
                                        or catalog not in CATALOGS):
                                    raise ValueError("Updated catalog-watch capability required")
                            else:
                                resource_uri(uri)
                            runtime = self._orchestrator._runtimes.get(group)
                            if runtime is None or (runtime.get_new_session_id() or "") != session:
                                raise ValueError("Resource watch group/session changed")
                            if watch_binding is None:
                                if len(watch_views) >= 16:
                                    raise ValueError("Resource watch view capacity reached")
                                watch_binding = (group, session, runtime.mcp,
                                                 "watch:" + child_ui_token + ":" + uuid.uuid4().hex)
                                watch_views[view] = watch_binding
                            elif watch_binding[:2] != (group, session) or watch_binding[2] is not runtime.mcp:
                                raise ValueError("Resource watch view owner changed")
                    except (ValueError, RuntimeError, UnicodeError):
                        await write({**watch_reply, "error": "Resource watch refused; inspect owner/capability"})
                        continue
                    loop = asyncio.get_running_loop()
                    async def watch_job(record: tuple[str, str, McpManager, str] | None, op: str,
                                        srv: str, resource: str, vid: str, response: dict[str, Any],
                                        catalog_kind: str, combined_filters: dict[str, Any] | None) -> None:
                        def alive() -> bool:
                            return (child_ui_connected and record is not None and watch_views.get(vid) is record
                                    and (not catalog_kind or catalog_watch_supported)
                                    and (combined_filters is None or combined_watch_supported)
                                    and self._orchestrator is not None
                                    and (self._orchestrator.get_new_session_id(record[0]) or "") == record[1])
                        def notice(data: dict[str, Any]) -> None:
                            def publish() -> None:
                                if alive() and record is not None:
                                    task = asyncio.create_task(write({"type": "mcp_resource_watch_notice", "view": vid,
                                        "group_id": record[0], "session_id": record[1], "data": data}))
                                    child_ui_writes.add(task)
                                    task.add_done_callback(child_ui_done)
                            loop.call_soon_threadsafe(publish)
                        try:
                            if record is None:
                                data: dict[str, Any] = {"state": "stopped"}
                            elif op == "close":
                                await record[2].detach_resource_watches(record[3])
                                data = {"state": "stopped"}
                            elif combined_filters is not None:
                                if op == "unwatch":
                                    data = await record[2].unwatch_notifications(srv, combined_filters, lease=record[3])
                                else:
                                    data = await record[2].watch_notifications(srv, combined_filters, lease=record[3],
                                                                            on_change=notice, alive=alive)
                            elif catalog_kind:
                                if op == "unwatch":
                                    data = await record[2].unwatch_catalog(srv, catalog_kind, lease=record[3])
                                else:
                                    data = await record[2].watch_catalog(srv, catalog_kind, lease=record[3],
                                                                        on_change=notice, alive=alive)
                            elif op == "unwatch":
                                data = await record[2].unwatch_resource(srv, resource, lease=record[3])
                            else:
                                data = await record[2].watch_resource(srv, resource, lease=record[3],
                                                                     on_change=notice, alive=alive)
                            response.update(success=True, data=data)
                        except Exception:
                            response["error"] = "Resource watch failed/unsupported; no read or request replay"
                        await write(response)
                    job = asyncio.create_task(watch_job(watch_binding, action, server, uri, view, watch_reply,
                                                        catalog, combined))
                    if combined is not None:
                        combined_watch_jobs.add(job)
                        job.add_done_callback(combined_watch_jobs.discard)
                    if catalog:
                        catalog_watch_jobs.add(job)
                        job.add_done_callback(catalog_watch_jobs.discard)
                    watch_jobs.add(job)
                    job.add_done_callback(watch_jobs.discard)
                    continue
                if msg_type == "mcp_context_cancel":
                    identifier = request.get("request_id")
                    if isinstance(identifier, str) and 0 < len(identifier) <= 128:
                        mcp_sender.cancel(identifier)
                    continue
                if msg_type == "mcp_context":
                    identifier, group = request.get("request_id"), request.get("group_id")
                    session = request.get("session_id")
                    try:
                        if (not isinstance(identifier, str) or not 0 < len(identifier) <= 128
                                or not isinstance(group, str) or not isinstance(session, str)
                                or self._orchestrator is None or group not in self._orchestrator.get_group_ids()):
                            raise ValueError("Invalid MCP data request")
                        if (self._orchestrator.get_new_session_id(group) or "") != session:
                            raise ValueError("Session changed before MCP read")
                        action, server, name = request.get("action"), request.get("server"), request.get("name", "")
                        args = request.get("arguments", {})
                        if (not isinstance(action, str)
                                or action not in {"resources", "templates", "prompts", "read", "prompt",
                                                  "complete_prompt", "complete_template", "complete_resource"}
                                or not isinstance(server, str) or not isinstance(name, str)
                                or not isinstance(args, dict)
                                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in args.items())):
                            raise ValueError("Invalid MCP context operation")
                        mcp_sender.request(request)
                    except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
                        await write({"type": "mcp_data_error", "request_id": identifier, "group_id": group,
                                     "session_id": session, "error": str(exc)})
                    continue
                if msg_type in {"extension_custom_request", "child_extension_custom_request"}:
                    key = request.get("id")
                    accepted = False
                    ack_generation: int | None = None
                    if (custom_supported and isinstance(key, str) and self._orchestrator is not None
                            and (key not in rich_dialog_ids or custom_rich_supported)
                            and (key not in overlay_dialog_ids or custom_overlay_supported)
                            and (key not in live_overlay_dialog_ids or custom_live_overlay_supported)):
                        try:
                            from xdog.agent.extensions.custom import event as validate_custom_event
                            custom_event = validate_custom_event(request.get("event"))
                            ack_generation = custom_event.get('generation')
                            if msg_type.startswith("child_"):
                                child = child_dialog_owners.get(key)
                                if (child is not None and request.get("group_id") == child[0]
                                        and request.get("session_id") == child[1]
                                        and request.get("child_id") == child[2]
                                        and request.get("child_owner") == child[3]):
                                    accepted = self._orchestrator.request_child_custom(
                                        *child[:3], key, child[3], custom_event)
                            else:
                                owner = dialog_owners.get(key)
                                if (owner is not None and request.get("group_id") == owner[0]
                                        and request.get("session_id") == owner[1]):
                                    accepted = self._orchestrator.request_extension_custom(
                                        *owner, key, custom_event)
                        except (ValueError, TypeError):
                            pass
                    ack = {"type": msg_type.replace("_request", "_ack"), "id": key, "accepted": accepted,
                           "group_id": request.get("group_id"), "session_id": request.get("session_id")}
                    if ack_generation is not None:
                        # A refused old viewport must not revoke a newer request in
                        # the frontend. Only the fully validated event may be echoed.
                        ack['generation'] = ack_generation
                    if msg_type.startswith("child_"):
                        ack.update(child_id=request.get("child_id"), child_owner=request.get("child_owner"))
                    await write(ack)
                    continue
                if msg_type == "child_extension_dialog_answer":
                    key = request.get("id")
                    child_owner = child_dialog_owners.get(key) if isinstance(key, str) else None
                    accepted = False
                    if (child_owner is not None and self._orchestrator is not None
                            and request.get("group_id") == child_owner[0]
                            and request.get("session_id") == child_owner[1]
                            and request.get("child_id") == child_owner[2]
                            and request.get("child_owner") == child_owner[3]):
                        try:
                            assert isinstance(key, str)
                            accepted = self._orchestrator.answer_child_dialog(
                                *child_owner[:3], key, child_owner[3], request.get("value"))
                        except (ValueError, TypeError):
                            pass
                    if accepted and isinstance(key, str):
                        child_dialog_owners.pop(key, None)
                    await write({"type": "child_extension_dialog_ack", "id": key, "accepted": accepted})
                    continue
                if msg_type == "extension_dialog_answer":
                    key = request.get("id")
                    owner = dialog_owners.get(key) if isinstance(key, str) else None
                    accepted = False
                    menu_owner = command_menu_dialogs.get(key) if isinstance(key, str) else None
                    if menu_owner is not None:
                        value = request.get('value')
                        if (not command_catalog_supported or menu_owner[2] != command_catalog_generation
                                or request.get('group_id', menu_owner[0]) != menu_owner[0]
                                or request.get('session_id', menu_owner[1]) != menu_owner[1]
                                or value is not None and (not isinstance(value, dict)
                                                         or value.get('session_id') != menu_owner[1])):
                            owner = None
                    if owner is not None and self._orchestrator is not None and isinstance(key, str):
                        try:
                            accepted = self._orchestrator.answer_extension_dialog(
                                owner[0], owner[1], key, request.get("value"))
                        except (ValueError, TypeError):
                            pass
                    if accepted and isinstance(key, str):
                        dialog_owners.pop(key, None)
                    await write({"type": "extension_dialog_ack", "id": key, "accepted": accepted})
                    continue
                if msg_type == "reasoning_control":
                    rid, group, session = (request.get(key, "") for key in ("request_id", "group_id", "session_id"))
                    level = request.get("thinking")
                    reasoning_response = {"type": "reasoning_result", "request_id": rid, "group_id": group,
                                          "success": False}
                    reserved = False
                    try:
                        if (not reasoning_supported or not isinstance(rid, str) or not 0 < len(rid) <= 128
                                or not isinstance(group, str) or not isinstance(session, str)
                                or level is not None and (not isinstance(level, str) or len(level) > 16)
                                or self._orchestrator is None or len(extension_jobs) >= 8):
                            raise ValueError("Updated gateway and valid reasoning control required")
                        reserved = self._orchestrator.try_reserve_group(group)
                        if not reserved:
                            raise ValueError("Reasoning controls require an idle owner")
                    except (ValueError, RuntimeError):
                        if reserved and self._orchestrator is not None:
                            self._orchestrator.release_group_reservation(group)
                        reasoning_response["error"] = ("Reasoning controls require an updated gateway "
                                                       "and idle session owner")
                        await write(reasoning_response)
                        continue
                    async def reasoning_job(gid: str, sid: str, target: str | None, result: dict[str, Any]) -> None:
                        try:
                            assert self._orchestrator is not None
                            result.update(success=True, **await self._orchestrator.reasoning(gid, sid, target))
                        except (ValueError, RuntimeError, OSError) as exc:
                            result["error"] = str(exc) or "Reasoning owner changed; no fallback/retry"
                        finally:
                            if self._orchestrator is not None:
                                self._orchestrator.release_group_reservation(gid)
                        await write(result)
                    job = asyncio.create_task(reasoning_job(group, session, level, reasoning_response))
                    extension_jobs.add(job)
                    job.add_done_callback(extension_jobs.discard)
                    continue
                if msg_type == "extension_model_control":
                    rid, group, session, selected = (request.get(key, "") for key in
                                                      ("request_id", "group_id", "session_id", "model"))
                    result = {"type": "extension_model_result", "request_id": rid, "group_id": group,
                              "success": False}
                    reserved = False
                    try:
                        if (not isinstance(rid, str) or not 0 < len(rid) <= 128
                                or not isinstance(group, str) or not isinstance(session, str)
                                or not isinstance(selected, str) or len(selected) > 256 or self._orchestrator is None
                                or len(extension_jobs) >= 8):
                            raise ValueError("Invalid extension model control")
                        reserved = self._orchestrator.try_reserve_group(group)
                        if not reserved:
                            raise ValueError("Extension model selection/catalog requires an idle group")
                    except (ValueError, RuntimeError, OSError):
                        result['error'] = 'Extension model control failed; explicit active model/idle owner required'
                        await write(result)
                        continue
                    async def model_job(gid: str, sid: str, target: str, response: dict[str, Any]) -> None:
                        try:
                            assert self._orchestrator is not None
                            response.update(success=True, **await self._orchestrator.extension_models(gid, sid, target))
                        except (ValueError, RuntimeError, OSError):
                            response['error'] = 'Model owner/catalog changed; no fallback'
                        finally:
                            if self._orchestrator is not None:
                                self._orchestrator.release_group_reservation(gid)
                        await write(response)
                    job = asyncio.create_task(model_job(group, session, selected, result))
                    extension_jobs.add(job)
                    job.add_done_callback(extension_jobs.discard)
                    continue
                if msg_type == 'extension_control_cancel':
                    key = request.get('request_id')
                    control_binding = extension_controls.get(key) if isinstance(key, str) else None
                    if (control_binding is not None and request.get('group_id') == control_binding[1]
                            and request.get('expected_session') == control_binding[2]):
                        control_binding[0].cancel()
                        if key not in extension_started:
                            await write({'type': 'extension_result', 'request_id': key, 'group_id': control_binding[1],
                                'success': False, 'canceled': True,
                                'error': 'Extension control canceled before worker admission'})
                    continue
                if msg_type == 'argument_completion_cancel':
                    key = request.get('request_id')
                    argument_binding = argument_jobs.get(key) if isinstance(key, str) else None
                    if (argument_cancel_supported and key not in argument_canceling
                            and argument_binding is not None and request.get('group_id') == argument_binding[1]
                            and request.get('session_id') == argument_binding[2]):
                        await cancel_argument_job(str(key))
                    continue
                if msg_type == 'argument_completion':
                    rid, gid, sid = request.get('request_id'), request.get('group_id'), request.get('session_id')
                    reserved = False
                    argument_reply: dict[str, Any] = {'type': 'argument_completion_result', 'request_id': rid,
                                             'group_id': gid, 'session_id': sid, 'status': 'unavailable', 'items': []}
                    try:
                        from xdog.agent.extensions.argument_completion import argument_context, completion_id, plain
                        if (not arguments_supported or not isinstance(rid, str) or not rid or len(rid) > 128
                                or not isinstance(gid, str)
                                or not isinstance(sid, str) or not sid or len(argument_jobs) >= 8
                                or rid in argument_jobs or self._orchestrator is None):
                            raise ValueError('Invalid/busy argument completion owner')
                        command = plain(request.get('command'), 80)
                        prefix = plain(request.get('prefix'), 4096)
                        identity = completion_id(request.get('identity'))
                        if request.get('argument_context') is not None and not argument_context_supported:
                            raise ValueError('Argument context requires independently negotiated support')
                        metadata = argument_context(request.get('argument_context'), prefix)
                        async with self._group_runs_lock:
                            reserved = gid not in self._group_runs and self._orchestrator.try_reserve_group(gid)
                        if not reserved:
                            raise ValueError('Argument completion requires idle group')
                        async def argument_job(key: str, group: str, session: str, name: str,
                                               value: str, token: str, response: dict[str, Any],
                                               captured_input: dict[str, Any] | None) -> None:
                            argument_started.add(key)
                            try:
                                assert self._orchestrator is not None
                                result = await self._orchestrator.complete_extension_argument(
                                    group, session, name, value, token, captured_input)
                                if captured_input is not None and not argument_context_supported:
                                    raise asyncio.CancelledError
                                response.update(**result)
                            except asyncio.CancelledError:
                                response['status'] = 'canceled'
                            except (ValueError, RuntimeError, OSError, TimeoutError):
                                response['status'] = 'unavailable'
                            finally:
                                if self._orchestrator is not None:
                                    failed = self._orchestrator.extension_failure_snapshot(group, session)
                                    if failed is not None:
                                        response['snapshot'] = failed['snapshot']
                                    self._orchestrator.release_group_reservation(group)
                            await write(response)
                        argument_task = asyncio.create_task(argument_job(rid, gid, sid, command,
                                                                        prefix, identity, argument_reply, metadata))
                        argument_jobs[rid] = argument_task, gid, sid
                        if metadata is not None:
                            argument_context_jobs.add(rid)
                        def argument_done(done: asyncio.Task[None], key: str = str(rid), group: str = str(gid)) -> None:
                            argument_jobs.pop(key, None)
                            if key not in argument_started and self._orchestrator is not None:
                                self._orchestrator.release_group_reservation(group)
                            argument_started.discard(key)
                            argument_canceling.discard(key)
                            argument_context_jobs.discard(key)
                            if not done.cancelled():
                                done.exception()
                        argument_task.add_done_callback(argument_done)
                    except (ValueError, RuntimeError, OSError):
                        if reserved and self._orchestrator is not None:
                            self._orchestrator.release_group_reservation(str(gid))
                        await write(argument_reply)
                    continue
                if msg_type == "extension_control":
                    identifier, group = request.get("request_id"), request.get("group_id")
                    action, session = request.get("action"), request.get("session_id")
                    name, args = request.get("name", ""), request.get("args", "")
                    try:
                        if (not isinstance(identifier, str) or not 0 < len(identifier) <= 128
                                or not isinstance(group, str) or not isinstance(session, str)
                                or not isinstance(action, str)
                                or action not in {"list", "reload", "unload", "command", "auth"}
                                or action == "auth" and not provider_auth_supported
                                or not isinstance(name, str) or not isinstance(args, str)
                                or len(args.encode("utf-8")) > 64 * 1024 or self._orchestrator is None
                                or len(extension_jobs) >= 8 or identifier in extension_controls):
                            raise ValueError("Invalid/busy extension control")
                        async with self._group_runs_lock:
                            reserved = group not in self._group_runs and self._orchestrator.try_reserve_group(group)
                        if not reserved:
                            raise ValueError("Extension controls require idle group ownership")
                    except (ValueError, RuntimeError, OSError) as exc:
                        await write({"type": "extension_result", "request_id": identifier, "group_id": group,
                                     "success": False, "error": str(exc)})
                        continue
                    control_loop = asyncio.get_running_loop()
                    async def extension_job(rid: str, gid: str, op: str, sid: str, key: str, text: str) -> None:
                        extension_started.add(rid)
                        reply = {"type": "extension_result", "request_id": rid, "group_id": gid, "success": False}
                        writes: list[asyncio.Task[None]] = []
                        tools_generation = active_tools_generation
                        compact_generation = extension_compaction_generation
                        message_generation = extension_message_generation
                        image_generation = message_images_generation
                        context_generation = context_message_generation
                        context_commits: list[Callable[[], Awaitable[None]]] = []
                        context_started: list[str] = []
                        async def context_schedule(commit: Callable[[], Awaitable[None]]) -> None:
                            if len(context_commits) >= 8:
                                raise ValueError('Context command proposal capacity exceeded')
                            context_commits.append(commit)
                        async def context_trigger(value: dict[str, Any]) -> None:
                            context_started.append(value['id'])
                        def context_alive() -> bool:
                            return (child_ui_connected and context_message_supported and session_supported
                                    and context_message_generation == context_generation and not writer.is_closing())
                        write_generation = editor_write_generation
                        auth_generation = provider_auth_generation
                        def auth_alive() -> bool:
                            return (op == "auth" and child_ui_connected and provider_auth_supported
                                    and provider_auth_generation == auth_generation and not writer.is_closing())
                        def editor_alive() -> bool:
                            return (child_ui_connected and editor_write_supported
                                    and write_generation == editor_write_generation and not writer.is_closing())
                        delivery: list[tuple[str, Callable[[], bool], tuple[ImageContent, ...]]] = []
                        def message_alive() -> bool:
                            return (child_ui_connected and extension_message_supported and session_supported
                                    and extension_message_generation == message_generation and not writer.is_closing())
                        def image_alive() -> bool:
                            return (child_ui_connected and message_images_supported
                                    and message_images_generation == image_generation and not writer.is_closing())
                        def message_ready(content: str, images: tuple[ImageContent, ...],
                                          alive: Callable[[], bool]) -> None:
                            delivery.append((content, alive, images))
                        def compact_alive() -> bool:
                            return (child_ui_connected and compaction_supported and extension_compaction_supported
                                    and extension_compaction_generation == compact_generation
                                    and not writer.is_closing())
                        def tools_alive() -> bool:
                            return (child_ui_connected and active_tools_supported
                                    and active_tools_generation == tools_generation and not writer.is_closing())
                        def publish(event: dict[str, Any]) -> None:
                            if op == 'auth' and event.get('type') == 'extension_dialog':
                                event = {**event, 'dialog': {**event['dialog'],
                                                            'provider_auth_generation': auth_generation}}
                            if (event.get('type') == 'extension_dialog'
                                    and event['dialog'].get('operation') == 'compact'):
                                event = {**event, 'dialog': {**event['dialog'],
                                                            'compaction_generation': compact_generation}}
                            if (event.get('type') == 'extension_dialog'
                                    and event['dialog'].get('operation') == 'message'):
                                event = {**event, 'dialog': {**event['dialog'],
                                                            'message_generation': message_generation,
                                                            **({'message_images_generation': image_generation}
                                                               if event['dialog'].get('image_summaries') else {})}}
                            async def auth_write(payload: dict[str, Any]) -> None:
                                try:
                                    await write(payload, strict=True)
                                except (OSError, ValueError, RuntimeError):
                                    if payload.get('type') == 'extension_dialog' and self._orchestrator is not None:
                                        self._orchestrator.answer_extension_dialog(gid,
                                            self._orchestrator.get_new_session_id(gid) or '',
                                            payload['dialog']['id'], None)
                                    raise
                            def send() -> None:
                                payload = {**event, "group_id": gid, "request_id": rid}
                                writes.append(asyncio.create_task(auth_write(payload) if op == 'auth' else
                                    write(payload, strict=bool(delivery) or event.get('operation') == 'message'
                                          or event.get('dialog', {}).get('operation') == 'message')))
                            control_loop.call_soon_threadsafe(send)
                        try:
                            assert self._orchestrator is not None
                            result = await self._orchestrator.manage_extensions(gid, op, key, text, sid, publish,
                                tool_selection_alive=tools_alive, compaction_alive=compact_alive,
                                message_alive=message_alive, message_ready=message_ready,
                                message_images_alive=image_alive,
                                context_message_alive=context_alive,
                                context_schedule=context_schedule, context_trigger=context_trigger,
                                editor_write_alive=editor_alive, command_menu=command_menu_host(),
                                provider_auth_alive=auth_alive)
                            reply.update(success=True, **result)
                        except asyncio.CancelledError:
                            reply.update(canceled=True,
                                         error='Extension control canceled; effects uncertain, not replayed')
                        except (ValueError, RuntimeError, OSError) as exc:
                            reply['error'] = str(exc)
                        finally:
                            if self._orchestrator is not None:
                                if reply.get('success') is not True:
                                    snapshot = self._orchestrator.extension_failure_snapshot(gid, sid)
                                    if snapshot is not None:
                                        reply['failure_snapshot'] = snapshot
                                self._orchestrator.release_group_reservation(gid)
                        if writes:
                            try:
                                await asyncio.gather(*writes)
                            except (ConnectionError, OSError):
                                # Lost native review/commit publication cannot leave an apparent
                                # successful command or authorize a later message dispatch.
                                reply.update(success=False, error='Extension UI delivery failed; no replay')
                                delivery.clear()
                        grant = None
                        if (reply.get('success') is True and reply.get('message_queued') is True
                                and len(delivery) == 1 and delivery[0][1]()):
                            token = uuid.uuid4().hex
                            grant = (gid, sid, message_generation, delivery[0][0], delivery[0][1],
                                     time.monotonic() + 300, delivery[0][2])
                            reply['message_delivery'] = {'token': token, 'generation': message_generation}
                        else:
                            reply['message_queued'] = False
                        context_source = self._orchestrator.source_lease(gid) if self._orchestrator else ('', -1)
                        reply['context_pending'] = bool(context_commits)
                        await write(reply, strict=grant is not None or bool(context_commits))
                        if reply.get('success') is True and context_alive():
                            context_reserved = False
                            try:
                                if context_commits:
                                    assert self._orchestrator is not None
                                    context_reserved = self._orchestrator.try_reserve_group(gid)
                                    if not context_reserved or self._orchestrator.source_lease(gid) != context_source:
                                        raise ValueError('Canonical idle owner changed after command delivery')
                                for commit in context_commits:
                                    await commit()  # Never persist before complete command-result delivery.
                            except BaseException:
                                await write({'type': 'extension_context_delivery_result', 'request_id': rid,
                                    'group_id': gid, 'session_id': sid, 'success': False,
                                    'error': 'Canonical context delivery failed/uncertain; no replay'}, strict=True)
                                raise
                            finally:
                                if context_reserved and self._orchestrator is not None:
                                    self._orchestrator.release_group_reservation(gid)
                            if context_commits:
                                await write({'type': 'extension_context_delivery_result', 'request_id': rid,
                                    'group_id': gid, 'session_id': sid, 'success': True,
                                    'continuation': len(context_started) == 1}, strict=True)
                            if len(context_started) == 1 and self._orchestrator is not None and context_alive():
                                context_token = uuid.uuid4().hex
                                source = self._orchestrator.source_lease(gid)
                                context_grants[context_token] = (
                                    gid, source[0], context_started[0], context_generation, source)
                                if len(context_grants) > 8:
                                    context_grants.pop(next(iter(context_grants)))
                                await write({'type': 'extension_context_continuation', 'request_id': rid,
                                    'group_id': gid, 'session_id': source[0], 'generation': context_generation,
                                    'token': context_token, 'id': context_started[0]}, strict=True)
                        if grant is not None and grant[4]():
                            # No suspension between successful result delivery and grant publication.
                            if len(message_grants) >= 8:
                                message_grants.pop(next(iter(message_grants)))
                            message_grants[token] = grant
                    task = asyncio.create_task(extension_job(identifier, group, action, session, name, args))
                    extension_jobs.add(task)
                    extension_controls[identifier] = task, group, session
                    if action == "auth":
                        provider_auth_ids.add(identifier)
                    def control_done(done: asyncio.Task[None], rid: str = str(identifier),
                                     gid: str = str(group)) -> None:
                        extension_jobs.discard(done)
                        extension_controls.pop(rid, None)
                        provider_auth_ids.discard(rid)
                        if rid not in extension_started and self._orchestrator is not None:
                            self._orchestrator.release_group_reservation(gid)
                        extension_started.discard(rid)
                        if not done.cancelled():
                            done.exception()
                    task.add_done_callback(control_done)
                    continue
                if msg_type == "child_control":
                    identifier, group = request.get("request_id"), request.get("group_id")
                    action, session = request.get("action"), request.get("session_id")
                    child, text = request.get("child_id", ""), request.get("text", "")
                    access = request.get("access", "read-only")
                    selected_config = request.get("config", {})
                    reserved = False
                    try:
                        if (not isinstance(identifier, str) or not 0 < len(identifier) <= 128
                                or not isinstance(group, str) or not isinstance(session, str)
                                or not isinstance(action, str) or action not in {
                                    "list", "presets", "start", "inspect", "steer", "follow-up",
                                    "cancel", "wait", "close", "queue"}
                                or not isinstance(child, str) or not isinstance(text, str)
                                or len(text.encode("utf-8")) > 16000 or not isinstance(access, str)
                                or access not in {"read-only", "inherit"} or self._orchestrator is None):
                            raise ValueError("Invalid child control")
                        ChildConfig.from_dict(selected_config)  # Preserve sparse preset overrides for host resolution.
                        return_command: dict[str, Any] = {}
                        if action == 'queue':
                            from xdog.agent.supervision_queue import request as queue_request
                            return_command = queue_request(json.loads(text))
                        returning = return_command.get('action') in {'return_prepare', 'return_commit', 'return_cancel'}
                        view = request.get('queue_view', '')
                        if returning and (not return_supported or request.get('child_return_owner') != return_token
                                or not isinstance(view, str) or len(view) != 32
                                or any(char not in '0123456789abcdef' for char in view)):
                            raise ValueError('Current negotiated child return owner and queue view required')
                        captured_return = return_token
                        captured_revoked = return_revoked
                        signature = json.dumps(request, sort_keys=True)
                        previous = child_receipts.get(identifier)
                        if previous is not None:
                            if previous[0] != signature:
                                raise ValueError("Child request ID reused with different content")
                            if previous[1] is not None:
                                await write(previous[1])
                            else:
                                await write({"type": "child_pending", "request_id": identifier, "group_id": group})
                            continue
                        images = uploads.resolve(request.get('image_refs'))
                        if images and action not in {'start', 'steer', 'follow-up'}:
                            raise ValueError('Child images belong only to start/steer/follow-up')
                        if len(child_jobs) >= 8:
                            raise ValueError("Child control job capacity reached")
                        if len(child_receipts) >= 128:
                            victim = next((key for key, value in child_receipts.items()
                                           if value[1] is not None and json.loads(value[0])["action"] != "start"), None)
                            if victim is not None:
                                child_receipts.pop(victim)
                            elif action == "start":
                                raise ValueError("Child start receipt capacity reached; reconnect explicitly")
                            # Never refuse deallocation/inspection just because
                            # start replay receipts filled the connection budget.
                        if action == "start":
                            async with self._group_runs_lock:
                                reserved = group not in self._group_runs and self._orchestrator.try_reserve_group(group)
                            if not reserved:
                                raise ValueError("User child start requires idle group ownership")
                        child_receipts[identifier] = signature, None
                    except (ValueError, RuntimeError, OSError) as exc:
                        await write({"type": "child_result", "request_id": identifier, "group_id": group,
                                     "success": False, "error": str(exc)})
                        continue
                    async def child_job(rid: str, gid: str, op: str, sid: str, key: str, content: str,
                                        policy: str, owns_group: bool, fingerprint: str,
                                        configuration: dict[str, Any], input_images: tuple[ImageContent, ...],
                                        refs: object, returning: bool, lease: str, revoked: threading.Event,
                                        view: str) -> None:
                        reply = {"type": "child_result", "request_id": rid, "group_id": gid, "success": False}
                        prepared_token = ''
                        active_return_owner: Any = None
                        def return_alive() -> bool:
                            return (child_ui_connected and not revoked.is_set()
                                    and return_supported and return_token == lease)
                        host_lease = lease + ':' + view
                        if returning:
                            reply.update(queue_view=view, child_return_owner=lease)
                        try:
                            assert self._orchestrator is not None
                            if returning and not return_alive():
                                raise ValueError('Live return connection revoked before admission')
                            result = await self._orchestrator.manage_children(
                                gid, op, key, content, policy, sid,
                                child_ui(gid, request_id=rid) if op == "start" else None,
                                editor_write_enabled=editor_write_supported, child_queue=child_queue_host(gid),
                                config=configuration,
                                images=input_images, return_owner=host_lease if returning else '',
                                return_alive=return_alive)
                            data = result.get('data')
                            if returning and isinstance(data, dict):
                                prepared_token = data.get('queue', {}).get('return_token', '')
                                if not return_alive():
                                    raise ValueError('Live return connection revoked after work; inspect queue')
                            if isinstance(data, dict) and 'image_recovery' in data:
                                from xdog.agent.supervision_transfer import send_recovery
                                recovery = data.pop('image_recovery')
                                command = json.loads(content)
                                runtime = self._orchestrator._runtimes[gid]
                                active = runtime._active_session
                                if active is None:
                                    raise ValueError('Child image copy owner disappeared')
                                recovery_owner = active.children
                                active_return_owner = recovery_owner
                                scope = {'request_id': rid, 'group_id': gid, 'session_id': sid, 'child_id': key,
                                         'input_id': command['id'], 'queue_revision': command['revision']}
                                if returning:
                                    scope.update(child_return_owner=lease, queue_view=view)
                                async def current() -> bool:
                                    if (runtime._active_session is not active or active.children is not recovery_owner
                                            or self._orchestrator is None
                                            or gid in self._orchestrator._navigation_groups):
                                        return False
                                    if returning:
                                        return return_alive() and await recovery_owner.supervisor.return_current(
                                            key, prepared_token, host_lease)
                                    return recovery_owner.supervisor.recovery_current(key, command['revision'])
                                await send_recovery(recovery, scope, write, current)
                                if not await current():
                                    raise ValueError('Child image copy owner changed before receipt')
                            if isinstance(data, dict) and any(key in data for key in ('messages', 'task_images')):
                                # Canonical image-bearing records cannot fit a socket line. The
                                # separately owned child-source reader provides complete DISPLAY
                                # history; controls carry only their actual receipt/metadata.
                                from xdog.agent.messages import dicts_to_messages
                                from xdog.agent.supervision_images import image_parts
                                has_images = (data.get('task_images')
                                              or any(row.get('images') for row in data.get('undelivered_inputs', []))
                                              or any(row.get('images') for row in data.get('returned_backups', []))
                                              or image_parts(dicts_to_messages(data.get('messages', []))))
                                if has_images:
                                    result['data'] = {key: value for key, value in data.items()
                                                      if key not in {'messages', 'task_images', 'undelivered_inputs',
                                                                     'returned_backups', 'partial', 'live_tools'}}
                                    result['data']['history_omitted'] = (
                                        'Use child_source_read for complete display history')
                            reply.update(success=True, **result)
                            uploads.discard_refs(refs)
                        except (ValueError, RuntimeError, OSError) as exc:
                            reply["error"] = str(exc)
                        finally:
                            if (returning and prepared_token and active_return_owner is not None
                                    and not reply['success'] and json.loads(content)['action'] == 'return_prepare'):
                                async def revoke() -> None:
                                    command = json.loads(content)
                                    try:
                                        await active_return_owner.supervisor.control('queue', key, json.dumps({
                                            'action': 'return_cancel', 'id': command['id'],
                                            'revision': command['revision'], 'token': prepared_token}),
                                            return_owner=host_lease)
                                    except (ValueError, RuntimeError):
                                        pass
                                cleanup = asyncio.create_task(revoke())
                                while not cleanup.done():
                                    try:
                                        await asyncio.shield(cleanup)
                                    except asyncio.CancelledError:
                                        continue
                                cleanup.result()
                            if owns_group and self._orchestrator is not None:
                                self._orchestrator.release_group_reservation(gid)
                        if op in {"list", "presets", "inspect", "wait"} or len(child_receipts) > 128:
                            child_receipts.pop(rid, None)  # Reads are safe to repeat; avoid caching large transcripts.
                        else:
                            child_receipts[rid] = fingerprint, reply
                        if terminal_jobs.child_supported and self._orchestrator is not None:
                            reply.update(terminal_children=self._orchestrator.child_terminal_metadata(
                                gid, str(reply.get("session_id", "")), child_ui_token),
                                         child_terminal_owner=child_ui_token)
                        await write(reply)
                    task = asyncio.create_task(child_job(identifier, group, action, session, child, text, access,
                                                         reserved, signature, selected_config, images,
                                                         request.get('image_refs'), returning, captured_return,
                                                         captured_revoked, view))
                    child_jobs.add(task)
                    task.add_done_callback(child_jobs.discard)
                    continue
                if msg_type == "plan_control":
                    identifier, group = request.get("request_id"), request.get("group_id")
                    action, session = request.get("action"), request.get("session_id")
                    revision, objective = request.get("revision"), request.get("objective", "")
                    plan_reply = {"type": "plan_result", "request_id": identifier, "group_id": group, "success": False}
                    reserved = False
                    try:
                        if (not isinstance(identifier, str) or not 0 < len(identifier) <= 128
                                or not isinstance(group, str) or not isinstance(session, str)
                                or not isinstance(action, str)
                                or action not in {"show", "start", "execute", "revise", "stop"}
                                or type(revision) is not int or not isinstance(objective, str)
                                or len(objective.encode("utf-8")) > 4000 or self._orchestrator is None):
                            raise ValueError("Invalid planning control")
                        if action != "show":
                            async with self._group_runs_lock:
                                reserved = group not in self._group_runs and self._orchestrator.try_reserve_group(group)
                            if not reserved:
                                raise ValueError("Plan transitions require an idle group")
                        result = await self._orchestrator.manage_plan(group, action, revision, objective, session)
                        plan_reply.update(success=True, **result)
                    except (ValueError, RuntimeError, OSError) as exc:
                        plan_reply["error"] = str(exc)
                    finally:
                        if reserved and self._orchestrator is not None:
                            assert isinstance(group, str)
                            self._orchestrator.release_group_reservation(group)
                    await write(plan_reply)
                    continue
                if msg_type == 'mcp_roots_control':
                    rid, group, view, sid = (request.get(key) for key in
                                             ('request_id', 'group_id', 'view', 'session_id'))
                    operation, server = request.get('action'), request.get('server', '')
                    receipt = request.get('review_id', '')
                    reserved = False
                    try:
                        if (not isinstance(rid, str) or not 0 < len(rid) <= 128
                                or not isinstance(group, str) or not isinstance(sid, str)
                                or not isinstance(view, str) or not re.fullmatch(r'[a-f0-9]{32}', view)
                                or not isinstance(server, str) or not isinstance(receipt, str) or len(receipt) > 128
                                or operation not in {'prepare', 'commit', 'cancel', 'detach'}
                                or self._orchestrator is None or group not in self._orchestrator.get_group_ids()
                                or operation not in {'cancel', 'detach'} and (rid in root_ids or len(root_ids) >= 256)):
                            raise ValueError('Invalid/used root control identity')
                        if operation not in {'cancel', 'detach'}:
                            root_ids.add(rid)
                        control_root_binding = root_views.get(view)
                        if operation in {'detach', 'cancel'}:
                            if control_root_binding is None or control_root_binding[0] != group:
                                raise ValueError('Root view is not attached to this connection')
                            # Immediate revocation before asynchronous cleanup.
                            control_root_binding[4].set()
                            if view in root_jobs:
                                root_jobs[view].cancel()
                                await write({'type': 'mcp_roots_result', 'request_id': rid, 'view': view,
                                             'group_id': group, 'success': True, 'data': {'state': 'detaching'}})
                                continue
                        else:
                            if (not roots_supported or len(root_jobs) >= 8
                                    or self._orchestrator.source_lease(group)[0] != sid):
                                raise ValueError('Current negotiated root view/session required')
                            root_scope = self._orchestrator.source_lease(group)
                            if control_root_binding is None:
                                if operation != 'prepare' or len(root_views) >= 8:
                                    raise ValueError('Prepare this connection root view first')
                                control_root_binding = (group, root_scope, self._orchestrator._runtimes[group].mcp,
                                           uuid.uuid4().hex, threading.Event())
                                root_views[view] = control_root_binding
                            if (control_root_binding[0] != group or control_root_binding[1] != root_scope
                                    or control_root_binding[4].is_set()):
                                raise ValueError('Root view ownership changed')
                            if view in root_jobs:
                                raise ValueError('Root view still owns work')
                            async with self._group_runs_lock:
                                reserved = group not in self._group_runs and self._orchestrator.try_reserve_group(group)
                            if not reserved:
                                raise ValueError('Root disclosure changes require an idle group')
                        assert control_root_binding is not None
                    except (ValueError, RuntimeError, OSError) as exc:
                        await write({'type': 'mcp_roots_result', 'request_id': rid, 'view': view,
                                     'group_id': group, 'success': False, 'error': str(exc)})
                        continue
                    async def roots_job(identifier: str, key: str, gid: str, expected: str, op: str,
                                        srv: str, proof: str, selected: Any,
                                        owner: tuple[str, tuple[str, int], McpManager, str, threading.Event],
                                        owns: bool) -> None:
                        reply = {'type': 'mcp_roots_result', 'request_id': identifier, 'view': key,
                                 'group_id': gid, 'session_id': expected, 'success': False}
                        def alive() -> bool:
                            if (not child_ui_connected or not roots_supported or owner[4].is_set()
                                    or self._orchestrator is None
                                    or self._orchestrator.source_lease(gid) != owner[1]):
                                return False
                            runtime = self._orchestrator._runtimes[gid]
                            active = runtime._active_session
                            return (runtime.mcp is owner[2] and (owns or not self._orchestrator.is_group_running(gid))
                                    and (active is None or not active.agent.is_streaming
                                         and not active.children.supervisor.active))
                        try:
                            if op in {'cancel', 'detach'}:
                                await owner[2].detach_roots(owner[3])
                                root_views.pop(key, None)
                                reply.update(success=True, data={'state': 'canceled'})
                            else:
                                result = await owner[2].manage_roots(srv, op, selected, receipt=proof,
                                                                    lease=owner[3], alive=alive)
                                if alive():
                                    reply.update(success=True, data=result)
                        except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
                            reply['error'] = str(exc)
                        finally:
                            if owner[4].is_set():
                                await owner[2].detach_roots(owner[3])
                                root_views.pop(key, None)
                            root_jobs.pop(key, None)
                        await write(reply)
                    job = asyncio.create_task(roots_job(rid, view, group, sid, operation, server, receipt,
                        request.get('roots'), control_root_binding, reserved))
                    root_jobs[view] = job
                    def root_done(completed: asyncio.Task[None], key: str = str(view), gid: str = str(group),
                                  owns: bool = reserved) -> None:
                        if root_jobs.get(key) is completed:
                            root_jobs.pop(key, None)
                        if owns and self._orchestrator is not None:
                            self._orchestrator.release_group_reservation(gid)
                        if not completed.cancelled():
                            completed.exception()
                    job.add_done_callback(root_done)
                    continue
                if msg_type == "mcp_auth_control":
                    rid, group, view = request.get("request_id"), request.get("group_id"), request.get("lease_id")
                    action, server = request.get("action"), request.get("server", "")
                    session, receipt = request.get("session_id"), request.get("review_id", "")
                    reserved = False
                    try:
                        if (not isinstance(rid, str) or not 0 < len(rid) <= 128 or not isinstance(group, str)
                                or not isinstance(view, str) or not re.fullmatch(r"[a-f0-9]{32}", view)
                                or not isinstance(session, str) or not isinstance(server, str)
                                or not isinstance(receipt, str) or len(receipt) > 128
                                or not isinstance(action, str)
                                or action not in {"prepare", "start", "refresh_prepare", "refresh",
                                                  "status", "cancel", "forget", "detach",
                                                  "credentials_save_prepare", "credentials_save",
                                                  "credentials_restore_prepare", "credentials_restore",
                                                  "credentials_forget_prepare", "credentials_forget"}
                                or self._orchestrator is None or group not in self._orchestrator.get_group_ids()
                                or len(auth_jobs) >= 8 and action not in {"detach", "cancel"}):
                            raise ValueError("Invalid/busy authorization control")
                        if action not in {"detach", "cancel"}:
                            configured = self._orchestrator._runtimes[group].mcp.configs.get(server)
                            profile = configured.oauth if configured is not None else None
                            if profile is not None and profile.token_auth_method == 'private_key_jwt':
                                if not private_jwt_supported:
                                    raise ValueError('Updated private-key JWT viewer capability required')
                            if (action in {'start', 'refresh'} and profile is not None
                                    and profile.token_auth_method != "none"):
                                if (request.get("token_auth_method") != profile.token_auth_method
                                        or request.get("client_secret_env") != profile.client_secret_env):
                                    raise ValueError("Explicit client-auth method/source review required before start")
                        auth_scope = self._orchestrator.source_lease(group)
                        existing = auth_views.get(view)
                        if action == "detach":
                            if existing is None or existing[0] != group:
                                raise ValueError("Authorization view is no longer attached to this group")
                            existing[3].set()  # Revoke callback admission immediately, before bridge settlement.
                        else:
                            if (auth_scope[0] != session
                                    or existing is not None and (existing[0], existing[1]) != (group, auth_scope)):
                                raise ValueError("Authorization view/session changed; refresh consent")
                            if existing is None:
                                if (action not in {"prepare", "refresh_prepare", "status", "credentials_save_prepare",
                                                   "credentials_restore_prepare", "credentials_forget_prepare"}
                                        or len(auth_views) >= 8):
                                    raise ValueError("Prepare this connection's authorization review first")
                                existing = group, auth_scope, uuid.uuid4().hex, threading.Event()
                                auth_views[view] = existing
                            if existing[3].is_set() and action not in {"status", "cancel"}:
                                raise ValueError("Authorization view was detached")
                            if action == "cancel":
                                existing[3].set()  # Stops commit on receipt, before asynchronous cancel settlement.
                        assert existing is not None
                        if (action not in {'detach', 'cancel'} and profile is not None
                                and profile.token_auth_method == 'private_key_jwt'):
                            private_auth_views.add(view)
                        if action in {"detach", "cancel"}:
                            if view in auth_closing:
                                await write({"type": "mcp_auth_result", "request_id": rid, "lease_id": view,
                                             "group_id": group, "success": True, "data": {"state": "detaching"}})
                                continue
                            auth_closing.add(view)  # At most one deallocation job per bounded view.
                        if action.startswith("credentials_") or action in {"prepare", "start", "refresh_prepare",
                                                                          "refresh", "forget"}:
                            async with self._group_runs_lock:
                                reserved = group not in self._group_runs and self._orchestrator.try_reserve_group(group)
                            if not reserved:
                                raise ValueError("Authorization changes require an idle group")
                    except (ValueError, RuntimeError, OSError) as exc:
                        await write({"type": "mcp_auth_result", "request_id": rid, "lease_id": view,
                                     "group_id": group, "success": False, "error": str(exc)})
                        continue
                    auth_owned = threading.Event()
                    if reserved:
                        auth_owned.set()
                    async def auth_job(identifier: str, gid: str, key: str, op: str, srv: str, sid: str, proof: str,
                                       binding: tuple[str, tuple[str, int], str, threading.Event],
                                       owns: bool, own_event: threading.Event) -> None:
                        reply = {"type": "mcp_auth_result", "request_id": identifier, "group_id": gid,
                                 "lease_id": key, "session_id": sid, "success": False}
                        runtime = self._orchestrator._runtimes[gid] if self._orchestrator is not None else None
                        def alive() -> bool:
                            if (binding[3].is_set() or self._orchestrator is None
                                    or self._orchestrator.source_lease(gid) != binding[1]):
                                return False
                            active = runtime._active_session if runtime is not None else None
                            return ((own_event.is_set() or not self._orchestrator.is_group_running(gid))
                                    and (active is None or not active.agent.is_streaming
                                         and not active.children.supervisor.active))
                        try:
                            assert self._orchestrator is not None and runtime is not None
                            if op == "detach":
                                await runtime.mcp.detach_auth(binding[2])
                                auth_views.pop(key, None)
                                private_auth_views.discard(key)
                                result: dict[str, Any] = {"state": "detached"}
                            else:
                                result = await self._orchestrator.manage_mcp_auth(
                                    gid, srv, op, proof, sid, binding[2], alive)
                            if op == "detach" or not binding[3].is_set():
                                reply.update(success=True, data=result)
                        except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
                            reply["error"] = str(exc)
                        finally:
                            if op in {"detach", "cancel"}:
                                auth_closing.discard(key)
                            own_event.clear()
                            if owns and self._orchestrator is not None:
                                self._orchestrator.release_group_reservation(gid)
                        await write(reply)
                    task = asyncio.create_task(auth_job(rid, group, view, action, server, session, receipt,
                                                         existing, reserved, auth_owned))
                    auth_jobs.add(task)
                    task.add_done_callback(auth_jobs.discard)
                    continue
                if msg_type == "mcp_control":
                    identifier, group = request.get("request_id"), request.get("group_id")
                    action, server = request.get("action"), request.get("server", "")
                    mcp_reply = {"type": "mcp_result", "request_id": identifier, "group_id": group, "success": False}
                    reserved = False
                    if action == 'owner':
                        mcp_reply.update({key: request[key] for key in ('view', 'expected_session')
                                          if isinstance(request.get(key), str) and len(request[key]) <= 128})
                    try:
                        if (not isinstance(identifier, str) or not 0 < len(identifier) <= 128
                                or not isinstance(group, str) or not isinstance(server, str)
                                or not isinstance(action, str)
                                or action not in {"list", "status", "connect", "disconnect", "refresh", "owner"}):
                            raise ValueError("Invalid MCP control")
                        if self._orchestrator is None or group not in self._orchestrator.get_group_ids():
                            raise ValueError("Unknown group")
                        if action not in {"list", "status"}:
                            async with self._group_runs_lock:
                                reserved = group not in self._group_runs and self._orchestrator.try_reserve_group(group)
                            if not reserved:
                                raise ValueError("MCP changes require an idle group")
                        if action == 'owner':
                            # Explicit cold browser opening selects its own idle session;
                            # a changed/nonempty owner is never silently borrowed.
                            view = request.get('view')
                            if (request.get('expected_session') != '' or not isinstance(view, str)
                                    or re.fullmatch(r'[a-f0-9]{32}', view) is None
                                    or self._orchestrator.source_lease(group)[0] != ''
                                    or self._orchestrator.source_lease(group)[1] < 0):
                                raise ValueError('MCP browser owner changed; reopen the current session')
                            runtime = self._orchestrator._runtimes[group]
                            selected = runtime.get_or_create_session(allow_reset=False)
                            mcp_reply['session_id'] = selected.meta.session_id
                        statuses = await self._orchestrator.manage_mcp(
                            group, 'list' if action == 'owner' else action, server)
                        mcp_reply.update(success=True, servers=statuses)
                    except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
                        mcp_reply["error"] = str(exc)
                    finally:
                        if reserved and self._orchestrator is not None:
                            assert isinstance(group, str)
                            self._orchestrator.release_group_reservation(group)
                    await write(mcp_reply)
                    continue
                if msg_type in {"child_source_read", "child_source_cancel"}:
                    source_id, group = request.get("request_id"), request.get("group_id")
                    session, child = request.get("session_id"), request.get("child_id")
                    if (not isinstance(source_id, str) or not 0 < len(source_id) <= 128
                            or not isinstance(group, str) or len(group) > 256
                            or not isinstance(session, str) or len(session) > 128
                            or not isinstance(child, str) or not re.fullmatch(r"[a-f0-9]{12}", child)):
                        await write({"type": "child_source_error", "request_id": source_id,
                                     "error": "Invalid child source request"})
                    elif msg_type == "child_source_cancel":
                        child_sender.cancel(source_id)
                    else:
                        child_scope = session + ":" + child
                        child_sender.request(source_id, group, child_scope)
                    continue
                if msg_type in {"source_read", "source_cancel"}:
                    source_id = request.get("request_id")
                    group, session = request.get("group_id"), request.get("session_id")
                    if (not isinstance(source_id, str) or not 0 < len(source_id) <= 128
                            or not isinstance(group, str) or len(group) > 256
                            or not isinstance(session, str) or len(session) > 128):
                        await write({"type": "source_error", "request_id": source_id,
                                     "error": "Invalid source request"})
                    elif msg_type == "source_cancel":
                        source_sender.cancel(source_id)
                    else:
                        source_sender.request(source_id, group, session)
                    continue
                if msg_type == "attachment_chunk":
                    try:
                        status = uploads.chunk(request)
                        await write({"type": "attachment_ack", "id": request.get("id"), "status": status})
                    except (ValueError, TypeError) as exc:
                        await write({"type": "attachment_ack", "id": request.get("id"), "error": str(exc)})
                    continue
                if msg_type == 'session_control_cancel':
                    key = request.get('request_id')
                    read_binding = name_read_jobs.get(key) if isinstance(key, str) else None
                    if (read_binding is not None and request.get('group_id') == read_binding[1]
                            and request.get('expected_session') == read_binding[2]):
                        read_binding[0].cancel()
                        if key not in session_started:
                            await write({'type': 'session_result', 'request_id': key, 'action': 'name',
                                'group_id': read_binding[1], 'session_id': read_binding[2], 'success': False,
                                'canceled': True, 'error': 'Session name read canceled before admission'})
                        continue
                    session_binding = session_jobs.get(key) if isinstance(key, str) else None
                    if (session_binding is not None and request.get('group_id') == session_binding[1]
                            and request.get('expected_session') == session_binding[2]):
                        session_binding[0].cancel()
                        if key not in session_started:
                            await write({'type': 'session_result', 'request_id': key, 'success': False,
                                         'canceled': True, 'error': 'Navigation canceled before preparation'})
                    continue
                if msg_type == "session_control":
                    group_id = str(request.get("group_id", "main"))
                    session_request_id = str(request.get("request_id", ""))
                    session_action = request.get("action")
                    reply: dict[str, Any] = {"type": "session_result", "request_id": session_request_id,
                                             "action": session_action, "success": False}
                    try:
                        if self._orchestrator is None or group_id not in self._orchestrator.get_group_ids():
                            raise ValueError("Unknown group")
                        if not isinstance(session_action, str) or session_action not in {
                            "list", "tree", "resume", "new", "fork", "branch", "goto", "rename", "name",
                        }:
                            raise ValueError("Invalid session operation")
                        if not session_request_id or len(session_request_id) > 128:
                            raise ValueError("Invalid request ID")
                        if session_action == 'name':
                            expected = request.get('expected_session')
                            if (not name_read_supported or not isinstance(expected, str) or not expected
                                    or len(expected) > 128 or type(request.get('request_id')) is not str
                                    or any(key in request for key in ('session_id', 'branch_id', 'index',
                                                                     'name', 'expected_name'))):
                                raise ValueError('Session name read requires negotiated support and a current owner')
                            if len(name_read_jobs) >= 8 or session_request_id in name_read_jobs | session_jobs:
                                raise ValueError('Session name read capacity/identity unavailable')
                            async def name_job(gid: str, rid: str, sid: str) -> None:
                                from xdog.claw.core.persistence.transcript_store import SessionNameOwnerChanged
                                session_started.add(rid)
                                response: dict[str, Any] = {'type': 'session_result', 'action': 'name',
                                    'request_id': rid, 'group_id': gid, 'session_id': sid, 'success': False}
                                assert self._orchestrator is not None
                                try:
                                    value = await self._orchestrator.read_session_name(gid, sid)
                                    if not name_read_supported:
                                        raise asyncio.CancelledError
                                    response.update(success=True, **value)
                                except asyncio.CancelledError:
                                    response.update(canceled=True,
                                                    error='Session name read canceled; no metadata changed')
                                except SessionNameOwnerChanged:
                                    response.update(status='stale', error='Active session changed; refresh /sessions')
                                except (ValueError, RuntimeError, OSError, TimeoutError):
                                    response.update(status='unavailable',
                                                    error='Session name unavailable; no cached fallback')
                                await write(response)
                            read_task = asyncio.create_task(name_job(group_id, session_request_id, expected))
                            name_read_jobs[session_request_id] = read_task, group_id, expected
                            def name_done(done: asyncio.Task[None], rid: str = session_request_id) -> None:
                                name_read_jobs.pop(rid, None)
                                session_started.discard(rid)
                                if not done.cancelled():
                                    done.exception()
                            read_task.add_done_callback(name_done)
                            continue
                        if session_action in {"list", "tree"}:
                            scope = str(request.get("scope", "sessions"))
                            query = str(request.get("query", ""))[:1000]
                            offset = request.get("offset", 0)
                            if type(offset) is not int or offset < 0:
                                raise ValueError("Invalid page offset")
                            entries = await self._orchestrator.navigation_entries(group_id, scope)
                            matches = [entry for entry in entries if query.casefold() in
                                       f"{entry['id']} {entry['title']} {entry.get('detail', '')}".casefold()]
                            offset = min(offset, max(0, len(matches) - 1))
                            reply.update(success=True, scope=scope, query=query, offset=offset,
                                         total=len(matches), entries=matches[offset:offset + 50],
                                         active_session=self._orchestrator.get_new_session_id(group_id) or "")
                            await write(reply)
                            continue
                        name = ''
                        expected_name = None
                        if session_action == 'rename':
                            from xdog.agent.session_name import session_name
                            name = session_name(request.get('name'))
                            if 'expected_name' in request:
                                expected_name = session_name(request['expected_name'])
                            if (not isinstance(request.get('expected_session'), str)
                                    or not request['expected_session'] or len(request['expected_session']) > 128
                                    or request.get('session_id') or request.get('branch_id')
                                    or request.get('index', -1) != -1):
                                raise ValueError('Session naming requires an explicit current owner only')
                        async with self._group_runs_lock:
                            reserved = (
                                group_id not in self._group_runs and self._orchestrator.try_reserve_group(group_id)
                            )
                        if not reserved:
                            raise ValueError("Session changes require an idle group")
                        index = request.get("index", -1)
                        if (type(index) is not int or len(session_jobs) >= 8
                                or session_request_id in session_jobs | name_read_jobs):
                            self._orchestrator.release_group_reservation(group_id)
                            raise ValueError("Invalid/busy navigation owner")
                        async def session_job(gid: str, rid: str, action: str, sid: str, bid: str, point: int,
                                              expected: str, response: dict[str, Any], new_name: str = '',
                                              previous_name: str | None = None) -> None:
                            session_started.add(rid)
                            assert self._orchestrator is not None
                            lease = self._orchestrator.source_lease(gid)
                            try:
                                if action == 'rename':
                                    if previous_name is None:
                                        selected = await self._orchestrator.rename_session(gid, expected, new_name)
                                    else:
                                        selected = await self._orchestrator.rename_session(gid, expected, new_name,
                                                                                          expected_name=previous_name)
                                    response.update(group_id=gid, session_id=expected, session_name=selected)
                                else:
                                    await self._orchestrator.navigate_session(gid, action, sid, bid, point, expected)
                                response.update(success=True)
                            except asyncio.CancelledError:
                                if self._orchestrator.source_lease(gid) != lease:
                                    response.update(success=True, receipt_canceled=True)
                                else:
                                    response.update(canceled=True,
                                                    error='Navigation canceled; current context retained')
                            except (ValueError, RuntimeError, OSError) as exc:
                                response['error'] = (
                                    'Session name changed since review; not saved. Inspect /name before retrying'
                                    if action == 'rename' and isinstance(exc, ValueError)
                                    and str(exc).startswith('Session name changed since review') else
                                    'Target preparation/navigation failed; current context retained')
                            await write(response)
                            if response.get('success') is True and action != 'rename':
                                info = await asyncio.to_thread(self._orchestrator.get_session_info, gid)
                                history = info.pop('history', [])
                                await write({'type': 'session_changed', 'request_id': rid,
                                             **info, 'history_count': len(history)})
                                for rows in _history_chunks(history):
                                    await write({'type': 'history_chunk', 'entries': rows})
                                if history:
                                    await write({'type': 'history_end'})
                        task = asyncio.create_task(session_job(group_id, session_request_id, session_action,
                            str(request.get('session_id', '')), str(request.get('branch_id', '')), index,
                            str(request.get('expected_session', '')), reply, name, expected_name))
                        session_jobs[session_request_id] = task, group_id, str(request.get('expected_session', ''))
                        def session_done(completed: asyncio.Task[None], rid: str = session_request_id,
                                         gid: str = group_id) -> None:
                            session_jobs.pop(rid, None)
                            session_started.discard(rid)
                            if self._orchestrator is not None:
                                self._orchestrator.release_group_reservation(gid)
                            if not completed.cancelled():
                                completed.exception()
                        task.add_done_callback(session_done)
                    except (ValueError, RuntimeError, OSError) as exc:
                        reply["error"] = str(exc)
                        await write(reply)
                    continue
                if msg_type == "input_control":
                    run_id = str(request.get("run_id", ""))
                    group_id = str(request.get("group_id", "main"))
                    request_id = request.get("request_id")
                    external_id = request.get("input_id")
                    action = request.get("action")
                    content = request.get("content", "")
                    delivery = request.get("delivery", "")
                    direction = request.get("direction", 0)
                    response: dict[str, Any] = {"type": "input_ack", "run_id": run_id, "request_id": request_id,
                                "input_id": external_id, "success": False, "snapshot": [],
                                "error": "Invalid input control or no owned active run"}
                    try:
                        content_bytes = content.encode("utf-8") if isinstance(content, str) else b""
                    except UnicodeError:
                        content_bytes = b""
                    valid = (
                        isinstance(request_id, str) and 0 < len(request_id) <= 128
                        and isinstance(external_id, str) and 0 < len(external_id) <= 96
                        and isinstance(content, str) and len(content_bytes) <= 64 * 1024
                        and (not content or bool(content_bytes))
                        and isinstance(action, str) and action in {"add", "edit", "remove", "move"}
                        and isinstance(delivery, str) and (action != "add" or delivery in {"steer", "follow_up"})
                        and (action not in {"add", "edit"} or bool(content.strip()) or bool(request.get("image_refs")))
                        and (action != "move" or type(direction) is int and direction in {-1, 1})
                    )
                    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
                    if isinstance(request_id, str) and request_id in receipts:
                        prior_fingerprint, prior_response = receipts[request_id]
                        uploads.discard_refs(request.get("image_refs"))
                        await write(prior_response if fingerprint == prior_fingerprint else {
                            **response, "error": "Request ID was reused with different content",
                        })
                        continue
                    run = runs.get(run_id)
                    if valid and run is not None and run.group_id == group_id and not run.task.done():
                        assert isinstance(action, str) and isinstance(external_id, str)
                        assert self._orchestrator is not None
                        prefix = hashlib.sha256(f"{client_id}:{run_id}".encode()).hexdigest()[:16] + ":"
                        try:
                            images = uploads.resolve(request.get("image_refs")) if action == "add" else ()
                            if images:
                                await asyncio.to_thread(self._orchestrator.validate_images, group_id, images)
                            # Validation yields; a settled run must not enqueue into
                            # a successor session that now owns the same group.
                            success = not run.task.done() and runs.get(run_id) is run and (
                                self._orchestrator.control_input(
                                    group_id, action, prefix + external_id, content, delivery, direction, images,
                                    images_validated=True,
                                )
                            )
                            response["success"] = success
                            if success:
                                response.pop("error", None)
                            else:
                                response["error"] = "Input already selected/consumed or at queue boundary"
                            response["snapshot"] = [
                                dict(id=item.id[len(prefix):], delivery=item.delivery,
                                     selected=item.selected, image_count=len(item.images))
                                for item in self._orchestrator.pending_inputs(group_id, prefix)
                            ]
                        except Exception as exc:
                            response["error"] = str(exc)
                    uploads.discard_refs(request.get("image_refs"))
                    if isinstance(request_id, str) and 0 < len(request_id) <= 128:
                        receipts[request_id] = (fingerprint, response)
                        if len(receipts) > 256:
                            receipts.pop(next(iter(receipts)))
                    await write(response)
                    continue
                if msg_type == "message":
                    requested_alive: Callable[[], bool] | None = None
                    requested_images: tuple[ImageContent, ...] = ()
                    context_record = ''
                    if 'extension_context_token' in request:
                        context_token = request.get('extension_context_token')
                        context_grant = (context_grants.pop(context_token, None)
                                         if isinstance(context_token, str) else None)
                        if (context_grant is None or not context_message_supported or self._orchestrator is None
                                or context_grant[0] != request.get('group_id')
                                or context_grant[1] != request.get('session_id')
                                or type(request.get('extension_context_generation')) is not int
                                or request['extension_context_generation'] != context_grant[3]
                                or context_message_generation != context_grant[3]
                                or self._orchestrator.source_lease(context_grant[0]) != context_grant[4]
                                or any(key in request for key in (
                                    'content', 'images', 'image_refs', 'origin', 'delivery'))):
                            await write({'type': 'error', 'run_id': request.get('run_id'),
                                         'message': 'Canonical context grant stale or unavailable; no replay'})
                            continue
                        context_record = context_grant[2]
                        assert context_grant is not None
                        def continuation_alive(
                            grant: tuple[str, str, str, int, tuple[str, int]] | None = context_grant,
                        ) -> bool:
                            return (grant is not None and child_ui_connected
                                and context_message_supported and not writer.is_closing()
                                and context_message_generation == grant[3] and self._orchestrator is not None
                                and self._orchestrator.source_lease(grant[0]) == grant[4])
                        requested_alive = continuation_alive
                        request = {**request, 'content': '', '_context_record': context_record}
                    elif 'extension_message_token' in request:
                        token = request.get('extension_message_token')
                        grant = message_grants.pop(token, None) if isinstance(token, str) else None
                        if (grant is None or not extension_message_supported
                                or grant[0] != request.get('group_id') or grant[1] != request.get('session_id')
                                or type(request.get('extension_message_generation')) is not int
                                or request['extension_message_generation'] != grant[2]
                                or grant[2] != extension_message_generation or time.monotonic() >= grant[5]
                                or not grant[4]() or any(key in request for key in (
                                    'content', 'image_refs', 'images', 'origin', 'delivery', 'scope'))):
                            await write({'type': 'error', 'run_id': request.get('run_id'),
                                         'message': 'Reviewed message grant unavailable/changed; no replay'})
                            continue
                        requested_alive, requested_images = grant[4], grant[6]
                        request = {**request, 'content': grant[3], '_message_alive': requested_alive}
                    elif 'extension_message_generation' in request:
                        await write({'type': 'error', 'run_id': request.get('run_id'),
                                     'message': 'Reviewed message requires an owned delivery token'})
                        continue
                    run_id = str(request.get("run_id") or f"run-{time.time_ns()}")
                    prefix = hashlib.sha256(f"{client_id}:{run_id}".encode()).hexdigest()[:16] + ":"
                    request = {**request, "run_id": run_id, "_input_scope": prefix, "_message_alive": requested_alive,
                               "_context_record": context_record,
                               "_child_ui": child_ui(str(request.get("group_id", "main")), run_id=run_id),
                               "_editor_write": editor_write_host(str(request.get("group_id", "main")), run_id),
                               "_context_message_alive": context_message_host(
                                   str(request.get("group_id", "main")), run_id),
                               "_command_menu": command_menu_host(),
                               "_queued_input": queued_input_host(str(request.get("group_id", "main")), run_id, prefix),
                               "_child_queue": child_queue_host(str(request.get("group_id", "main")))}
                    group_id = str(request.get("group_id", "main"))
                    try:
                        images = (requested_images if requested_alive is not None
                                  else uploads.resolve(request.get("image_refs")))
                    except ValueError as exc:
                        uploads.discard_refs(request.get("image_refs"))
                        await write({"type": "error", "run_id": run_id, "message": str(exc)})
                        continue
                    uploads.discard_refs(request.get("image_refs"))
                    if self._orchestrator and group_id not in self._orchestrator.get_group_ids():
                        await write({
                            "type": "error",
                            "message": f"Unknown group: {group_id}",
                            "run_id": run_id,
                        })
                        continue
                    current_session = self._orchestrator.get_new_session_id(group_id) if self._orchestrator else None
                    if "session_id" in request and request["session_id"] != (current_session or ""):
                        await write({"type": "error", "run_id": run_id,
                                     "message": "Active session changed; refresh and resume before sending"})
                        continue
                    content = request.get("content")
                    try:
                        if self._orchestrator is not None:
                            await asyncio.to_thread(self._orchestrator.validate_images, group_id, images)
                        request = {**request, "_images": images}
                    except ValueError as exc:
                        await write({"type": "error", "run_id": run_id, "message": str(exc)})
                        continue
                    if not isinstance(content, str) or not (content.strip() or images or context_record):
                        await write({
                            "type": "error",
                            "message": "Empty message content",
                            "run_id": run_id,
                        })
                        continue
                    if len(run_id) > 128:
                        await write({"type": "error", "message": "Invalid run_id", "run_id": run_id})
                        continue
                    if runs:
                        await write({
                            "type": "error",
                            "message": "Client already has an active run",
                            "run_id": run_id,
                        })
                        continue
                    async with self._group_runs_lock:
                        busy = (
                            group_id in self._group_runs
                            or not bool(
                                self._orchestrator
                                and self._orchestrator.try_reserve_group(group_id)
                            )
                        )
                        stale_session = False
                        if not busy:
                            assert self._orchestrator is not None
                            current_session = self._orchestrator.get_new_session_id(group_id) or ""
                            stale_session = ("session_id" in request and request["session_id"] != current_session
                                             or requested_alive is not None and not requested_alive())
                            if stale_session:
                                self._orchestrator.release_group_reservation(group_id)
                            else:
                                self._group_runs[group_id] = run_id
                    if stale_session:
                        await write({"type": "error", "run_id": run_id,
                                     "message": "Active session changed; refresh and resume before sending"})
                        continue
                    if busy:
                        await write({
                            "type": "busy",
                            "group_id": group_id,
                            "run_id": run_id,
                        })
                        continue
                    assert self._orchestrator is not None
                    started = asyncio.Event()
                    compact_run = after_run_compaction.create(group_id, run_id)
                    name_run = after_run_name.create(group_id, run_id, compact_run)
                    tools_run = after_run_tools.create(group_id, run_id, compact_run)
                    request = {**request, "_after_run_compaction": compact_run, "_after_run_name": name_run,
                               "_after_run_tools": tools_run}
                    task = asyncio.create_task(
                        self._handle_chat_message(
                            request,
                            writer,
                            write=write,
                            started=started,
                        )
                    )
                    group_id = str(request.get("group_id", "main"))
                    runs[run_id] = _ClientRun(
                        group_id=group_id,
                        task=task,
                        started=started,
                    )
                    await started.wait()
                    await write({
                        "type": "run_ack",
                        "group_id": request.get("group_id", "main"),
                        "run_id": run_id,
                        "session_id": self._orchestrator.get_new_session_id(group_id) or "",
                    })

                    def _remove_run(_task: asyncio.Task[None], rid: str = run_id, gid: str = group_id) -> None:
                        runs.pop(rid, None)

                        async def _release() -> None:
                            if self._orchestrator:
                                self._orchestrator.release_group_reservation(gid)
                            async with self._group_runs_lock:
                                if self._group_runs.get(gid) == rid:
                                    self._group_runs.pop(gid, None)

                        asyncio.create_task(_release())

                    task.add_done_callback(_remove_run)
                    after_run_compaction.attach(compact_run, task)
                    after_run_name.attach(name_run, task)
                    after_run_tools.attach(tools_run, task)
                    continue
                if msg_type == "reset":
                    reset_group = str(request.get("group_id", "main"))
                    async with self._group_runs_lock:
                        reset_busy = reset_group in self._group_runs
                    if reset_busy or bool(
                        self._orchestrator
                        and self._orchestrator.is_group_running(reset_group)
                    ):
                        await write({
                            "type": "error",
                            "message": "Cannot reset while a run is active",
                        })
                        continue
                if msg_type == "abort":
                    run_id = str(request.get("run_id", ""))
                    group_id = str(request.get("group_id", "main"))
                    run = runs.get(run_id)
                    requested = bool(
                        run
                        and run.group_id == group_id
                        and self._orchestrator
                        and self._orchestrator.abort_group(run.group_id)
                    )
                    await write({
                        "type": "abort_ack",
                        "group_id": group_id,
                        "run_id": run_id,
                        "status": "requested" if requested else "not_active",
                    })
                    continue
                await self._dispatch(request, writer, terminal_owner=terminal_jobs.owner
                                     if terminal_jobs.supported else "", child_terminal_owner=child_ui_token
                                     if terminal_jobs.child_supported else "", private_jwt_active=private_jwt_supported,
                                     skill_controls_active=skills_supported, compaction_active=compaction_supported,
                                     saved_source_active=saved_source_supported,
                                     saved_timeline_active=saved_timeline_supported,
                                     name_read_active=name_read_supported, roots_active=roots_supported,
                                     sampling_active=sampling_connection.supported,
                                     active_tools_active=active_tools_supported,
                                     active_tools_after_run=after_run_tools.enabled,
                                     active_tools_after_run_generation=after_run_tools.generation,
                                     extension_name_after_run=after_run_name.enabled,
                                     name_after_run_generation=after_run_name.generation,
                                     extension_compaction_after_run=after_run_compaction.enabled,
                                     compaction_after_run_generation=after_run_compaction.generation,
                                     extension_compaction_active=(extension_compaction_supported
                                                                  and compaction_supported),
                                     compaction_generation=extension_compaction_generation,
                                     message_images_active=message_images_supported,
                                     message_images_generation=message_images_generation,
                                     extension_message_active=extension_message_supported,
                                     message_generation=extension_message_generation,
                                     terminal_release_active=terminal_jobs.release_supported,
                                     child_queued_input_active=child_queued_input_supported,
                                     child_queued_input_generation=child_queued_input_generation,
                                     child_queued_input_owner=child_ui_token if child_queued_input_supported else "",
                                     queued_input_active=queued_input_supported,
                                     queued_input_generation=queued_input_generation,
                                     context_message_active=context_message_supported,
                                     context_delivery_active=context_delivery_supported,
                                     context_message_generation=context_message_generation,
                                     provider_auth_active=provider_auth_supported,
                                     provider_auth_generation=provider_auth_generation,
                                     editor_write_active=editor_write_supported,
                                     editor_write_generation=editor_write_generation,
                                     editor_write_owner=child_ui_token if editor_write_supported else "",
                                     command_menu_generation=command_catalog_generation,
                                     elicitation_active=elicitation_connection.supported,
                                     parent_draft_capabilities=parent_draft_connection.capabilities(),
                                     child_return_owner=return_token if return_supported else '')

        except (ConnectionResetError, BrokenPipeError):
            logger.info("Client disconnected: %s", peer)
        except Exception:
            logger.exception("Error handling client %s", peer)
        finally:
            child_ui_connected = False
            await parent_draft_connection.close()
            await elicitation_connection.close()
            await sampling_connection.close()
            await after_run_tools.close()
            await after_run_compaction.close()
            await after_run_name.close()
            for root_binding in tuple(root_views.values()):
                root_binding[4].set()
            for root_job in tuple(root_jobs.values()):
                root_job.cancel()
            await asyncio.gather(*tuple(root_jobs.values()), return_exceptions=True)
            await asyncio.gather(*(owner[2].detach_roots(owner[3]) for owner in tuple(root_views.values())),
                                 return_exceptions=True)
            root_views.clear()
            return_revoked.set()
            child_ui_connected = False
            for argument_task, _, _ in tuple(argument_jobs.values()):
                argument_task.cancel()
            await asyncio.gather(*(row[0] for row in tuple(argument_jobs.values())), return_exceptions=True)
            for read_task, _, _ in tuple(name_read_jobs.values()):
                read_task.cancel()
            for session_task, _, _ in tuple(session_jobs.values()):
                session_task.cancel()
            # Cancel model/tool ownership before settling an uninterruptible
            # source filesystem read; source cleanup must not postpone abort.
            for run in tuple(runs.values()):
                if self._orchestrator:
                    self._orchestrator.abort_group(run.group_id)
            await asyncio.gather(*(binding[0] for binding in tuple(session_jobs.values())), return_exceptions=True)
            await asyncio.gather(*(binding[0] for binding in tuple(name_read_jobs.values())), return_exceptions=True)
            for key, owner in tuple(dialog_owners.items()):
                if self._orchestrator is not None:
                    self._orchestrator.answer_extension_dialog(owner[0], owner[1], key, None)
            for binding in tuple(auth_views.values()):
                binding[3].set()
            for job in tuple(auth_jobs):
                job.cancel()
            await asyncio.gather(*auth_jobs, return_exceptions=True)
            if self._orchestrator is not None:
                await asyncio.gather(*(self._orchestrator._runtimes[binding[0]].mcp.detach_auth(binding[2])
                                       for binding in auth_views.values()), return_exceptions=True)
            child_ui_connected = False
            for job in tuple(watch_jobs):
                job.cancel()
            await asyncio.gather(*watch_jobs, return_exceptions=True)
            await asyncio.gather(*(record[2].detach_resource_watches(record[3]) for record in watch_views.values()),
                                 return_exceptions=True)
            watch_views.clear()
            if self._orchestrator is not None:
                self._orchestrator.detach_child_ui(child_ui_token)
            for task in tuple(child_ui_writes):
                task.cancel()
            await asyncio.gather(*child_ui_writes, return_exceptions=True)
            for job in tuple(child_jobs):
                job.cancel()
            await asyncio.gather(*child_jobs, return_exceptions=True)
            for job in tuple(extension_jobs):
                job.cancel()
            await asyncio.gather(*extension_jobs, return_exceptions=True)
            await terminal_jobs.close()
            await mcp_sender.close()
            await child_sender.close()
            await source_sender.close()
            if self._orchestrator:
                await asyncio.gather(
                    *(self._orchestrator.wait_group_idle(run.group_id) for run in runs.values()),
                    return_exceptions=True,
                )
            for run in tuple(runs.values()):
                run.task.cancel()
            if runs:
                await asyncio.gather(
                    *(run.task for run in runs.values()),
                    return_exceptions=True,
                )
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("Client session ended: %s", peer)

    async def _dispatch(
        self, request: dict[str, Any], writer: asyncio.StreamWriter, *, terminal_owner: str = "",
        child_terminal_owner: str = "", private_jwt_active: bool = False, skill_controls_active: bool = False,
        compaction_active: bool = False, saved_source_active: bool = False, saved_timeline_active: bool = False,
        name_read_active: bool = False, roots_active: bool = False, sampling_active: bool = False,
        active_tools_active: bool = False, extension_compaction_active: bool = False,
        active_tools_after_run: bool = False, active_tools_after_run_generation: int = 0,
        extension_compaction_after_run: bool = False, compaction_after_run_generation: int = 0,
        extension_name_after_run: bool = False, name_after_run_generation: int = 0,
        compaction_generation: int = 0, extension_message_active: bool = False, message_generation: int = 0,
        elicitation_active: bool = False, terminal_release_active: bool = False,
        editor_write_active: bool = False, editor_write_generation: int = 0, editor_write_owner: str = "",
        command_menu_generation: int = 0,
        queued_input_active: bool = False, queued_input_generation: int = 0,
        context_message_active: bool = False, context_message_generation: int = 0,
        context_delivery_active: bool = False,
        child_queued_input_active: bool = False, child_queued_input_generation: int = 0,
        child_queued_input_owner: str = "",
        provider_auth_active: bool = False, provider_auth_generation: int = 0,
        message_images_active: bool = False, message_images_generation: int = 0,
        parent_draft_capabilities: dict[str, Any] | None = None,
        child_return_owner: str = ''
    ) -> None:
        """Route a client request to the appropriate handler."""
        msg_type = request.get("type", "")

        if msg_type == "message":
            await self._handle_chat_message(request, writer)
        elif msg_type == "reset":
            await self._handle_reset(request, writer)
        elif msg_type == "status":
            await self._handle_status(writer)
        elif msg_type == "ping":
            group_id = request.get("group_id", "main")
            session_info: dict[str, Any] = {
                **(parent_draft_capabilities or {}),
                "type": "pong", "input_controls": 1, "session_controls": 1, "session_rename": 1,
                "session_name_read": int(name_read_active),
                "session_navigation_cancel": 1, "attachment_transport": 1,
                "transcript_source": 1, "mcp_controls": 1, "mcp_browser_owner": 1,
                "mcp_context": 1, "mcp_completion": 1,
                "mcp_elicitation": int(elicitation_active), "mcp_sampling": int(sampling_active),
                "mcp_roots": int(roots_active),
                "mcp_content_types": 1, "mcp_catalog_watch": 1,
                "mcp_combined_watch": 1, "tool_inventory": 1, "tool_inventory_selection": 1,
                "mcp_resource_completion": 1, "mcp_resource_watch": 1, "extension_custom": 1,
                "extension_custom_rich": 1,
                "extension_custom_overlay": 1, "extension_custom_live_overlay": 1,
                "extension_forms": 1, "extension_multi_forms": 1, "extension_custom_nested": 1,
                "extension_command_results": 1,
                "extension_editor": 1, "extension_session": 1, "extension_navigation": 1,
                "extension_editor_write": int(editor_write_active),
                "extension_editor_write_generation": editor_write_generation,
                "extension_editor_write_owner": editor_write_owner,
                "extension_active_tools": int(active_tools_active),
                "extension_active_tools_after_run": int(active_tools_after_run),
                "active_tools_after_run_generation": active_tools_after_run_generation,
                "extension_compaction": int(extension_compaction_active),
                "extension_name_after_run": int(extension_name_after_run),
                "name_after_run_generation": name_after_run_generation,
                "extension_compaction_after_run": int(extension_compaction_after_run),
                "compaction_after_run_generation": compaction_after_run_generation,
                "extension_compaction_generation": compaction_generation,
                "extension_message": int(extension_message_active),
                "extension_message_generation": message_generation,
                "extension_message_images": int(message_images_active),
                "extension_message_images_generation": message_images_generation,
                "extension_provider_auth": int(provider_auth_active),
                "extension_provider_auth_generation": provider_auth_generation,
                "extension_queued_inputs": int(queued_input_active),
                "extension_context_messages": int(context_message_active),
                "extension_context_delivery_modes": int(context_delivery_active),
                "extension_context_messages_generation": context_message_generation,
                "extension_queued_inputs_generation": queued_input_generation,
                "child_extension_queued_inputs": int(child_queued_input_active),
                "child_extension_queued_inputs_generation": child_queued_input_generation,
                "child_extension_queued_inputs_owner": child_queued_input_owner,
                "extension_session_inspection": 1, "extension_session_name": 1, "extension_command_cancel": 1,
                "extension_command_catalog": 1, "extension_command_catalog_generation": command_menu_generation,
                "extension_argument_completion": 1,
                "extension_argument_cancel": 1, "extension_argument_context": 1,
                "extension_shortcuts": 1, "extension_models": 1, "native_models": 1, "reasoning_controls": 1,
                "terminal_child_input": 1, "terminal_input": 1,
                "terminal_release_input": int(terminal_release_active),
                "terminal_owner": terminal_owner, "child_terminal_owner": child_terminal_owner, "mcp_auth": 1,
                "saved_timeline": int(saved_timeline_active), "saved_source": int(saved_source_active),
                "session_compaction": int(compaction_active),
                "skill_controls": int(skill_controls_active),
                "mcp_registration": 1, "mcp_client_auth": 1,
                "mcp_private_key_jwt": int(private_jwt_active),
                "mcp_refresh": 1, "mcp_credentials": 1,
                "plan_controls": 1, "extension_controls": 1, "child_controls": 1, "child_source": 1, "child_queues": 1,
                "child_dialogs": 1, "child_config": 3, "child_images": 1, "child_image_recovery": 1,
                "child_task_recovery": 1, 'child_image_return': int(bool(child_return_owner)),
                'child_return_owner': child_return_owner,
            }
            history: list[dict[str, Any]] = []
            if self._orchestrator:
                session_info.update(self._orchestrator.get_session_info(group_id))
                if group_id in self._orchestrator.get_group_ids():
                    runtime = self._orchestrator._runtimes[group_id]
                    if runtime._active_session is not None:
                        session_info["extension_snapshot"] = runtime._active_session.extensions.snapshot()
                        session_info["thinking"] = runtime._active_session.agent.options.thinking or "off"
                    try:
                        result = await self._orchestrator.manage_plan(
                            group_id, "show", 0, "", self._orchestrator.get_new_session_id(group_id) or "")
                        session_info["plan"] = result["plan"]
                    except (ValueError, RuntimeError, OSError):
                        session_info["plan_controls"] = 0
                        session_info["plan_error"] = "Invalid saved plan; repair it or explicitly create a new session"
                raw_history = session_info.pop("history", [])
                if isinstance(raw_history, list):
                    history = raw_history
                notifications = self._orchestrator.pop_goal_notifications(group_id)
                if notifications:
                    session_info["goal_notifications"] = [
                        asdict(n) for n in notifications
                    ]
            session_info["history_count"] = len(history)
            await self._write_response(writer, session_info)
            for entries in _history_chunks(history):
                await self._write_response(writer, {
                    "type": "history_chunk",
                    "entries": entries,
                })
            if history:
                await self._write_response(writer, {"type": "history_end"})
        else:
            await self._write_response(writer, {
                "type": "error",
                "message": f"Unknown message type: {msg_type}",
            })

    async def _handle_chat_message(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
        *,
        write: Any = None,
        started: asyncio.Event | None = None,
    ) -> None:
        """Process a chat message from the TUI client with streaming deltas."""
        group_id = request.get("group_id", "main")
        content = request.get("content", "")
        run_id = str(request.get("run_id", f"run-{time.time_ns()}"))
        write_response = write or (lambda data: self._write_response(writer, data))

        if not content and not request.get("_images") and not request.get("_context_record"):
            await write_response({
                "type": "error",
                "message": "Empty message content",
                "run_id": run_id,
            })
            return

        if not self._orchestrator:
            await write_response({
                "type": "error",
                "message": "Orchestrator not initialized",
                "run_id": run_id,
            })
            return

        from xdog.claw.core.types import ContextContinuation, GroupInput
        message: GroupInput
        if request.get('_context_record'):
            message = ContextContinuation(group_id=group_id, record_id=request['_context_record'])
        else:
            message = UserInput(
                group_id=group_id, content=content, sender="user", channel="tui",
                images=tuple(request.get("_images", ())),
                metadata={"input_origin": "extension"} if request.get("_message_alive") is not None else {})
        if started is not None:
            started.set()

        # Stream presentation-neutral turn events to the TUI client.
        accumulated_text = ""
        streamed_any_text = False
        pending_writes: list[asyncio.Task[None]] = []

        def on_display_event(event: Any) -> None:
            nonlocal accumulated_text, streamed_any_text
            try:
                payload = event.to_wire()
                event_type = payload.get("type")
                if event_type in {"input_consumed", "input_transformed", "input_rejected", "input_handled"}:
                    scope = str(request.get("_input_scope", ""))
                    key = str(payload.get("id", ""))
                    if key or event_type == "input_consumed":
                        if not scope or not key.startswith(scope):
                            return  # Never expose another connection's input receipt/content.
                        payload["id"] = key[len(scope):]
                    accumulated_text = ""
                if event_type == "assistant_delta":
                    streamed_any_text = True
                    accumulated_text += str(payload.pop("delta", ""))
                    payload.update({
                        "type": "delta",
                        "content": accumulated_text,
                    })
                elif event_type == "tool_call":
                    accumulated_text = ""
                payload.update({"group_id": group_id, "run_id": run_id})
                pending_writes.append(asyncio.create_task(write_response(payload)))
            except (AttributeError, TypeError, ValueError):
                logger.exception("Failed to serialize TUI display event")

        try:
            result = await self._orchestrator.route_message(
                message,
                on_display_event=on_display_event,
                child_ui=request.get("_child_ui"),
                reserved=True,
                input_alive=request.get("_message_alive"),
                editor_write=request.get("_editor_write"),
                command_menu=request.get("_command_menu"),
                queued_input=request.get("_queued_input"),
                context_message_alive=request.get("_context_message_alive"),
                child_queue=request.get("_child_queue"),
                compaction_request=request.get("_after_run_compaction"),
                name_request=request.get("_after_run_name"),
                active_tools_request=request.get("_after_run_tools"),
            )
        except ValueError as exc:
            if request.get('_message_alive') is None:
                raise
            await write_response({'type': 'error', 'run_id': run_id, 'message': str(exc)})
            return
        finally:
            scope = str(request.get("_input_scope", ""))
            if scope:
                undelivered = self._orchestrator.drain_inputs(group_id, scope)
                for item in undelivered:
                    await write_response({"type": "input_returned", "run_id": run_id,
                                          "id": item.id[len(scope):], "content": item.content})
        if pending_writes:
            await asyncio.gather(*pending_writes)
        compact_run = request.get("_after_run_compaction")
        if compact_run is not None:
            compact_run.success = result is not None and not result.error

        if result is None:
            await write_response({
                "type": "queued",
                "group_id": group_id,
                "run_id": run_id,
            })
        elif result.error:
            if result.error == "aborted" and not result.retain_partial:
                await write_response({
                    "type": "aborted",
                    "group_id": group_id,
                    "run_id": run_id,
                })
            else:
                await write_response({
                    "type": "error",
                    "group_id": group_id,
                    "message": result.error,
                    "run_id": run_id,
                    "retain_partial": result.retain_partial and streamed_any_text,
                    "canceled": result.canceled,
                })
        else:
            resp: dict[str, Any]
            if result.input_handled:
                resp = {"type": "final", "group_id": group_id, "run_id": run_id, "input_handled": True}
            elif streamed_any_text:
                # Deltas already delivered content — send completion
                # signal only to avoid the TUI rendering the text twice.
                resp = {
                    "type": "final",
                    "group_id": group_id,
                    "run_id": run_id,
                }
            else:
                # No streaming happened — send the full content
                resp = {
                    "type": "response",
                    "group_id": group_id,
                    "content": result.response_text or "(no response)",
                    "run_id": run_id,
                }
            if result.usage:
                resp["usage"] = result.usage
            await write_response(resp)

    async def _handle_reset(
        self, request: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        """Reset a group's session."""
        group_id = request.get("group_id", "main")

        if not self._orchestrator:
            await self._write_response(writer, {
                "type": "error",
                "message": "Orchestrator not initialized",
            })
            return

        if self._orchestrator.reset_group(group_id):
            new_session_info: dict[str, Any] = {
                "type": "reset_ack",
                "group_id": group_id,
            }
            sid = self._orchestrator.get_new_session_id(group_id)
            if sid:
                new_session_info["session_id"] = sid
            await self._write_response(writer, new_session_info)
        else:
            await self._write_response(writer, {
                "type": "error",
                "message": f"Unknown group: {group_id}",
            })

    async def _handle_status(self, writer: asyncio.StreamWriter) -> None:
        """Return gateway status."""
        groups = self._orchestrator.get_group_ids() if self._orchestrator else []

        await self._write_response(writer, {
            "type": "status",
            "running": True,
            "pid": os.getpid(),
            "groups": groups,
        })

    async def _write_response(
        self, writer: asyncio.StreamWriter, data: dict[str, Any]
    ) -> None:
        """Write a JSON-line response to the client."""
        try:
            line = json.dumps(data) + "\n"
            writer.write(line.encode("utf-8"))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def _write_pid(self) -> None:
        """Write current PID to file."""
        self._pid_path.parent.mkdir(parents=True, exist_ok=True)
        self._pid_path.write_text(str(os.getpid()), encoding="utf-8")

    def _remove_pid(self) -> None:
        """Remove PID file."""
        try:
            if self._pid_path.exists():
                self._pid_path.unlink()
        except OSError:
            pass

    async def _scheduler_loop(self) -> None:
        """Periodically call orchestrator.tick() for scheduling and goal runner."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(self._SCHEDULER_POLL_INTERVAL)
                if self._orchestrator is not None and not self._shutdown_event.is_set():
                    await self._orchestrator.tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Scheduler loop error")

    def _start_weixin_channel(self) -> None:
        """Initialize and add the WeChat channel to the orchestrator."""
        from xdog.claw.channels.weixin import WeixinChannel
        from xdog.claw.channels.weixin.auth import (
            DEFAULT_BASE_URL,
            load_account,
        )

        config = self._config
        state_dir = Path(config.data_dir)

        account_id = config.weixin_account_id
        token = config.weixin_token
        base_url = config.weixin_base_url
        group_id = "main"

        # Try loading token from account file if not in config
        if account_id and not token:
            account_data = load_account(state_dir, account_id)
            if account_data:
                token = account_data.token
                base_url = base_url or account_data.base_url
                group_id = account_data.group_id or "main"

        if not token:
            logger.warning(
                "WeChat channel enabled but no token configured. "
                "Run 'xdog-claw channel login --weixin' first."
            )
            return

        base_url = base_url or DEFAULT_BASE_URL

        weixin = WeixinChannel(
            state_dir=state_dir,
            account_id=account_id,
            base_url=base_url,
            token=token,
            group_id=group_id,
        )
        if self._orchestrator is None:
            # Every other use in this file guards; this one did not, so adding
            # the channel before `start()` raised AttributeError instead of
            # saying what was wrong.
            logger.error("Cannot add the WeChat channel: the orchestrator is not running")
            return

        self._orchestrator.add_channel(weixin)
        # Channel was added after orchestrator.start(), so connect manually
        asyncio.get_event_loop().create_task(weixin.connect())
        logger.info(
            "WeChat channel added: account=%s group=%s base_url=%s",
            account_id,
            group_id,
            base_url,
        )

    async def stop(self) -> None:
        """Join one cleanup owner; caller cancellation is not resource settlement."""
        self._shutdown_event.set()
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop(), name="claw-gateway-stop")
        task = self._stop_task
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _stop(self) -> None:
        logger.info("Shutting down gateway...")

        # Cancel scheduler loop
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        # Stop accepting new connections
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Cancel all active client handler tasks
        for task in list(self._client_tasks):
            task.cancel()
        if self._client_tasks:
            await asyncio.gather(*self._client_tasks, return_exceptions=True)
            self._client_tasks.clear()

        if self._orchestrator:
            await self._orchestrator.stop()

        # Clean up socket and PID files
        try:
            if self._socket_path.exists():
                self._socket_path.unlink()
        except OSError:
            pass

        self._remove_pid()

        logger.info("Gateway stopped.")

    async def run_forever(self) -> None:
        """Run the gateway until interrupted."""
        await self.start()

        loop = asyncio.get_running_loop()

        def _signal_handler() -> None:
            logger.info("Received shutdown signal")
            self._shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

        try:
            await self._shutdown_event.wait()
        finally:
            try:
                # asyncio.run must not cancel the cleanup task while it owns resources.
                await self.stop()
            finally:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.remove_signal_handler(sig)


def read_pid(pid_path: Path) -> int | None:
    """Read PID from file, returning None if invalid or missing."""
    try:
        if not pid_path.exists():
            return None
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError):
        return None


def run_gateway(config: ClawConfig) -> None:
    """Entry point to run the gateway (blocking)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    server = GatewayServer(config)
    asyncio.run(server.run_forever())
