"""Native connection protocol and peer-owned jobs, independent of gateway lifecycle."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from xdog.agent.async_work import run_owned, wait_owned
from xdog.agent.child_config import ChildConfig
from xdog.agent.mcp.completion import McpCompletion
from xdog.agent.mcp.context import McpDocument, McpEntry
from xdog.ai.types import ImageContent
from xdog.claw.channels.tui.channel import NativeNoticeChannel
from xdog.claw.channels.tui.image_upload import ImageUploads
from xdog.claw.core.runtime.group_controller import GroupReservation, InputExecution
from xdog.claw.core.runtime.input_queries import NativeInputReceipts
from xdog.claw.core.runtime.mcp_protocol import McpSender
from xdog.claw.core.runtime.native_controls import access_control, extension_model_control, reasoning_control
from xdog.claw.core.runtime.native_requests import NativeRequests, _history_chunks
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.runtime.source_protocol import SourceSender
from xdog.claw.core.runtime.transcript_source import SourceCapture
from xdog.claw.core.types import UserInput

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_HISTORY_FRAME_LIMIT = 512 * 1024


@dataclass(frozen=True, slots=True)
class _ClientRun:
    group_id: str
    task: asyncio.Task[None]
    started: asyncio.Event
    execution: asyncio.Future[InputExecution]
    binding: Callable[[InputExecution], None]


class NativeConnection:
    """One native peer's protocol work; application execution outlives this peer."""

    def __init__(self, application: Orchestrator | None, receipts: NativeInputReceipts | None,
                 viewers: dict[int, tuple[str, Callable[[dict[str, Any]], Awaitable[None]]]],
                 write_response: Callable[[asyncio.StreamWriter, dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        self._orchestrator = application
        self._acp_viewers = viewers
        self._write_response = write_response
        self._requests = NativeRequests(application, receipts, write_response)

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        client_id: int = 0,
    ) -> None:
        """Handle a single TUI client connection."""
        peer = f"client-{client_id}"
        logger.info("Client connected: %s", peer)

        write_lock = asyncio.Lock()
        runs: dict[str, _ClientRun] = {}
        reserved: GroupReservation | None = None
        receipts: dict[str, tuple[str, dict[str, Any]]] = {}
        native_reads: set[asyncio.Task[None]] = set()
        native_aborts: dict[str, asyncio.Task[None]] = {}
        extension_jobs: set[asyncio.Task[None]] = set()
        status_jobs: set[asyncio.Task[None]] = set()
        status_sequence = asyncio.Lock()
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
        auth_closing: set[str] = set()
        auth_views: dict[str, tuple[str, tuple[str, int], str, threading.Event]] = {}
        child_receipts: dict[str, tuple[str, dict[str, Any] | None]] = {}
        dialog_owners: dict[str, tuple[str, str]] = {}
        child_dialog_owners: dict[str, tuple[str, str, str, str]] = {}
        child_ui_token = uuid.uuid4().hex
        child_ui_connected = True
        session_supported = False
        inspection_supported = False
        provider_auth_supported = False
        provider_auth_generation = 0
        provider_auth_ids: set[str] = set()
        # Only extension additions on this connection/run are eligible for withdrawal.
        # One-shot grants contain only host-produced text and the original command owner.
        command_catalog_supported = False
        command_catalog_generation = 0
        command_menu_dialogs: dict[str, tuple[str, str, int]] = {}
        mcp_content_types_supported = False
        skills_supported = False
        tool_inventory_supported = False
        tool_inventory_jobs: dict[str, asyncio.Task[None]] = {}
        command_results_supported = False
        reasoning_supported = False
        child_ui_loop = asyncio.get_running_loop()
        child_ui_writes: set[asyncio.Task[None]] = set()
        uploads = ImageUploads()

        async def write(data: dict[str, Any], *, strict: bool = False) -> None:
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
            if data.get("type") == "extension_result" and "command_content" in data and not command_results_supported:
                data = {key: value for key, value in data.items() if key != "command_content"}
                data["output"] = str(data.get("output", "")) + "; updated rich-command viewer required"
            if data.get("type") in {"extension_dialog", "child_extension_dialog"}:
                from xdog.agent.extensions.dialogs import validate_sdk_dialog
                dialog = data.get("dialog", {})
                rejected = False
                if not ("provider_auth_host" in dialog or "provider_auth_menu" in dialog):
                    try:
                        validate_sdk_dialog(dialog)
                        if dialog.get("kind") == "session":
                            operation = dialog.get("operation")
                            rejected = (not session_supported
                                or operation in {"model", "usage", "name", "active_tools"}
                                and not inspection_supported
                                or operation == "commands" and not command_catalog_supported)
                    except (ValueError, TypeError):
                        rejected = True
                if rejected:
                    if self._orchestrator is not None:
                        key, group = str(dialog.get("id", "")), str(data.get("group_id", ""))
                        if data["type"] == "child_extension_dialog":
                            self._orchestrator.answer_child_dialog(group, str(data.get("session_id", "")),
                                str(data.get("child_id", "")), key, child_ui_token, None)
                        else:
                            self._orchestrator.answer_extension_dialog(group,
                                self._orchestrator.get_new_session_id(group) or "", key, None)
                    return
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
                    data["session_id"] = dialog_owners[key][1]
            elif data.get("type") == "extension_dialog_closed":
                command_menu_dialogs.pop(str(data.get("id", "")), None)
                dialog_owners.pop(str(data.get("id", "")), None)
            if data.get("type") == "child_extension_dialog":
                key = data.get("dialog", {}).get("id")
                if isinstance(key, str):
                    child_dialog_owners[key] = (str(data.get("group_id", "")), str(data.get("session_id", "")),
                                                str(data.get("child_id", "")), child_ui_token)
            elif data.get("type") == "child_extension_dialog_closed":
                child_dialog_owners.pop(str(data.get("id", "")), None)
            async with write_lock:
                kind = str(data.get("type", ""))
                if kind in {'extension_custom_frame', 'extension_custom_error', 'extension_custom_control',
                            'child_extension_custom_frame', 'child_extension_custom_error',
                            'child_extension_custom_control'}:
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


        async def deliver_notice(data: dict[str, Any]) -> None:
            # Durable application output must observe transport errors. The
            # best-effort display writer cannot certify successful delivery.
            await write(data, strict=True)

        assert self._orchestrator is not None
        notice_channel = NativeNoticeChannel(deliver_notice)
        notice_binding = self._orchestrator.register_reply_channel(notice_channel)

        def start_operation(lease: GroupReservation | None, coroutine: Coroutine[Any, Any, None]
                            ) -> asyncio.Task[None]:
            assert self._orchestrator is not None
            return self._orchestrator.start_group_operation(lease, coroutine)

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


        def command_menu_host() -> tuple[Callable[[], bool], int] | None:
            if not command_catalog_supported:
                return None
            generation = command_catalog_generation
            return (lambda: child_ui_connected and command_catalog_supported
                    and command_catalog_generation == generation and not writer.is_closing()), generation


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
                    or runtime._active_session is None or self._orchestrator.context_changing(group)):
                raise ValueError("Child source session changed")
            captured = runtime._active_session.children.runtime.capture_source(child)
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
                if msg_type == 'ping':
                    selected_group = request.get('group_id', 'main')
                    if (isinstance(selected_group, str) and self._orchestrator is not None
                            and self._orchestrator.get_group_workspace(selected_group) is not None):
                        notice_channel.bind_group(selected_group)
                if msg_type == "ping" and "acp_updates" in request:
                    value, group = request["acp_updates"], request.get("group_id", "main")
                    if type(value) is not int or value not in (0, 1) or not isinstance(group, str):
                        await write({"type": "protocol_error", "message": "Invalid ACP display subscription"})
                        continue
                    self._acp_viewers.pop(client_id, None)
                    if value == 1:
                        self._acp_viewers[client_id] = group, write
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
                if msg_type == 'session_compact_cancel':
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
                        if 'extension_compaction_generation' in request:
                            raise ValueError('Extension compaction control has been removed; no replay')
                        reserved = self._orchestrator.try_reserve_group(group)
                        if reserved is None:
                            raise ValueError('Compaction requires an idle group')
                    except (ValueError, RuntimeError) as exc:
                        await write({'type': 'session_compact_result', 'request_id': rid, 'group_id': group,
                                     'success': False, 'error': str(exc)})
                        continue
                    compact_ids.add(rid)
                    async def compact_job(identifier: str, gid: str, expected: str, instructions: str) -> None:
                        compact_started.add(identifier)
                        result: dict[str, Any] = {'type': 'session_compact_result', 'request_id': identifier,
                                                  'group_id': gid, 'session_id': expected, 'success': False}
                        try:
                            if identifier in compact_canceled:
                                raise asyncio.CancelledError
                            assert self._orchestrator is not None
                            result.update(success=True, **await self._orchestrator.compact_session(
                                gid, expected, instructions, lambda: compaction_supported and child_ui_connected))
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
                    job = start_operation(reserved, compact_job(rid, group, sid, focus))
                    extension_jobs.add(job)
                    compact_jobs[rid] = job, group, sid
                    def compact_done(completed: asyncio.Task[None], identifier: str = str(rid),
                                     gid: str = str(group)) -> None:
                        extension_jobs.discard(completed)
                        compact_jobs.pop(identifier, None)
                        compact_started.discard(identifier)
                        compact_canceled.discard(identifier)
                        if not completed.cancelled():
                            completed.exception()
                    job.add_done_callback(compact_done)
                    continue
                if msg_type == 'ping' and 'skill_controls' in request:
                    skills_supported = type(request['skill_controls']) is int and request['skill_controls'] == 1
                if msg_type == 'skills_control':
                    rid, group, sid = (request.get(key) for key in ('request_id', 'group_id', 'session_id'))
                    action, slug = request.get('action', 'list'), request.get('slug', '')
                    reserved = None
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
                                         name: str) -> None:
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
                    job = start_operation(reserved, skills_job(rid, group, sid, action, slug))
                    extension_jobs.add(job)
                    def skills_done(completed: asyncio.Task[None]) -> None:
                        extension_jobs.discard(completed)
                        if not completed.cancelled():
                            completed.exception()
                    job.add_done_callback(skills_done)
                    continue
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
                    except (ValueError, RuntimeError):
                        await write({'type': 'tool_inventory_result', 'request_id': rid, 'group_id': group,
                                     'session_id': session_id, 'success': False})
                        continue
                    async def inventory_job(identifier: str, gid: str, requested_session: str,
                                            runtime_owner: Any, include_selection: bool) -> None:
                        sid = requested_session
                        try:
                            # Cold preparation is part of the cancellable job, not
                            # the protocol reader (which must still read ping/cancel).
                            owner = await runtime_owner.prepare_session(allow_reset=False)
                            sid = owner.meta.session_id
                            if requested_session and sid != requested_session:
                                raise ValueError('Tool inventory owner changed')
                            snapshot = await run_owned(owner._extension_tools)
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
                    job = asyncio.create_task(inventory_job(rid, group, session_id, runtime,
                                                            request.get('include_selection', False)))
                    tool_inventory_jobs[rid] = job
                    def inventory_done(done: asyncio.Task[None], identifier: str = str(rid)) -> None:
                        if tool_inventory_jobs.get(identifier) is done:
                            tool_inventory_jobs.pop(identifier, None)
                    job.add_done_callback(inventory_done)
                    extension_jobs.add(job)
                    job.add_done_callback(extension_jobs.discard)
                    continue
                if msg_type == "ping" and "mcp_content_types" in request:
                    mcp_content_types_supported = (type(request["mcp_content_types"]) is int
                                                   and request["mcp_content_types"] == 1)
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
                if msg_type == "ping" and "reasoning_controls" in request:
                    reasoning_supported = (type(request["reasoning_controls"]) is int
                                           and request["reasoning_controls"] == 1)
                if msg_type == "ping" and "extension_command_results" in request:
                    command_results_supported = (type(request["extension_command_results"]) is int
                                                 and request["extension_command_results"] == 1)
                if not isinstance(msg_type, str):
                    await write({"type": "protocol_error" if runs else "error", "message": "Invalid request type"})
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
                if msg_type == 'reasoning_control':
                    await reasoning_control(self._orchestrator, request, write, extension_jobs,
                                  reasoning_supported=reasoning_supported)
                    continue
                if msg_type == 'extension_model_control':
                    await extension_model_control(self._orchestrator, request, write, extension_jobs)
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
                    reserved = None
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
                        reserved = self._orchestrator.try_reserve_group(gid)
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
                            await write(response)
                        argument_task = start_operation(reserved, argument_job(rid, gid, sid, command,
                                                                        prefix, identity, argument_reply, metadata))
                        argument_jobs[rid] = argument_task, gid, sid
                        if metadata is not None:
                            argument_context_jobs.add(rid)
                        def argument_done(done: asyncio.Task[None], key: str = str(rid), group: str = str(gid)) -> None:
                            argument_jobs.pop(key, None)
                            argument_started.discard(key)
                            argument_canceling.discard(key)
                            argument_context_jobs.discard(key)
                            if not done.cancelled():
                                done.exception()
                        argument_task.add_done_callback(argument_done)
                    except (ValueError, RuntimeError, OSError):
                        if reserved and self._orchestrator is not None:
                            self._orchestrator.release_group_reservation(reserved)
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
                        reserved = self._orchestrator.try_reserve_group(group)
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
                        auth_generation = provider_auth_generation
                        def auth_alive() -> bool:
                            return (op == "auth" and child_ui_connected and provider_auth_supported
                                    and provider_auth_generation == auth_generation and not writer.is_closing())
                        def publish(event: dict[str, Any]) -> None:
                            if op == 'auth' and event.get('type') == 'extension_dialog':
                                event = {**event, 'dialog': {**event['dialog'],
                                                            'provider_auth_generation': auth_generation}}
                            async def deliver(payload: dict[str, Any]) -> None:
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
                                writes.append(asyncio.create_task(deliver(payload)))
                            control_loop.call_soon_threadsafe(send)
                        try:
                            assert self._orchestrator is not None
                            result = await self._orchestrator.manage_extensions(gid, op, key, text, sid, publish,
                                command_menu=command_menu_host(), provider_auth_alive=auth_alive)
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
                        if writes:
                            try:
                                await asyncio.gather(*writes)
                            except (ConnectionError, OSError, ValueError, RuntimeError):
                                reply.update(success=False, error='Extension UI delivery failed; no replay')
                        await write(reply)
                    task = start_operation(reserved, extension_job(identifier, group, action, session, name, args))
                    extension_jobs.add(task)
                    extension_controls[identifier] = task, group, session
                    if action == "auth":
                        provider_auth_ids.add(identifier)
                    def control_done(done: asyncio.Task[None], rid: str = str(identifier),
                                     gid: str = str(group)) -> None:
                        extension_jobs.discard(done)
                        extension_controls.pop(rid, None)
                        provider_auth_ids.discard(rid)
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
                    reserved = None
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
                        if action == 'queue':
                            if json.loads(text) != {"action": "list"}:
                                raise ValueError("Native child queue is read-only; only exact list is accepted")
                        if 'child_return_owner' in request or 'queue_view' in request:
                            raise ValueError("Native child recovery protocol has been removed")
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
                            reserved = self._orchestrator.try_reserve_group(group)
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
                                        refs: object) -> None:
                        reply = {"type": "child_result", "request_id": rid, "group_id": gid, "success": False}
                        try:
                            assert self._orchestrator is not None
                            result = await self._orchestrator.manage_children(
                                gid, op, key, content, policy, sid,
                                child_ui(gid, request_id=rid) if op == "start" else None,
                                config=configuration,
                                images=input_images)
                            data = result.get('data')
                            if isinstance(data, dict) and any(key in data for key in ('messages', 'task_images')):
                                # Canonical image-bearing records cannot fit a socket line. The
                                # separately owned child-source reader provides complete DISPLAY
                                # history; controls carry only their actual receipt/metadata.
                                from xdog.agent.child_images import image_parts
                                from xdog.agent.messages import dicts_to_messages
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
                        if op in {"list", "presets", "inspect", "wait"} or len(child_receipts) > 128:
                            child_receipts.pop(rid, None)  # Reads are safe to repeat; avoid caching large transcripts.
                        else:
                            child_receipts[rid] = fingerprint, reply
                        await write(reply)
                    task = start_operation(reserved, child_job(identifier, group, action, session, child, text, access,
                                                         bool(reserved), signature, selected_config, images,
                                                         request.get('image_refs')))
                    child_jobs.add(task)
                    task.add_done_callback(child_jobs.discard)
                    continue
                if msg_type == 'access_control':
                    await access_control(self._orchestrator, request, write)
                    continue
                if msg_type == "mcp_auth_control":
                    rid, group, view = request.get("request_id"), request.get("group_id"), request.get("lease_id")
                    action, server = request.get("action"), request.get("server", "")
                    session, receipt = request.get("session_id"), request.get("review_id", "")
                    reserved = None
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
                        if (request.get('token_auth_method', 'none') != 'none'
                                or request.get('client_secret_env', '')
                                or request.get('client_private_key_env', '')
                                or request.get('registration', 'pre-registered') != 'pre-registered'):
                            raise ValueError('Only pre-registered public PKCE authorization is supported')
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
                        if action in {"detach", "cancel"}:
                            if view in auth_closing:
                                await write({"type": "mcp_auth_result", "request_id": rid, "lease_id": view,
                                             "group_id": group, "success": True, "data": {"state": "detaching"}})
                                continue
                            auth_closing.add(view)  # At most one deallocation job per bounded view.
                        if action.startswith("credentials_") or action in {"prepare", "start", "refresh_prepare",
                                                                          "refresh", "forget"}:
                            reserved = self._orchestrator.try_reserve_group(group)
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
                                       own_event: threading.Event) -> None:
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
                                         and not active.children.runtime.active))
                        try:
                            assert self._orchestrator is not None and runtime is not None
                            if op == "detach":
                                await runtime.mcp.detach_auth(binding[2])
                                auth_views.pop(key, None)
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
                        await write(reply)
                    task = start_operation(reserved, auth_job(rid, group, view, action, server, session, receipt,
                                                         existing, auth_owned))
                    auth_jobs.add(task)
                    task.add_done_callback(auth_jobs.discard)
                    continue
                if msg_type == "mcp_control":
                    identifier, group = request.get("request_id"), request.get("group_id")
                    action, server = request.get("action"), request.get("server", "")
                    mcp_reply = {"type": "mcp_result", "request_id": identifier, "group_id": group, "success": False}
                    reserved = None
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
                            reserved = self._orchestrator.try_reserve_group(group)
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
                            selected = await runtime.prepare_session(allow_reset=False)
                            mcp_reply['session_id'] = selected.meta.session_id
                        async with self._orchestrator.operation_scope(reserved):
                            statuses = await self._orchestrator.manage_mcp(
                                group, 'list' if action == 'owner' else action, server)
                        mcp_reply.update(success=True, servers=statuses)
                    except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
                        mcp_reply["error"] = str(exc)
                    finally:
                        if reserved and self._orchestrator is not None:
                            assert isinstance(group, str)
                            self._orchestrator.release_group_reservation(reserved)
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
                        if isinstance(session_action, str) and session_action in {"resume", "fork", "branch", "goto"}:
                            raise ValueError("Claw history is read-only; historical route switching is not supported")
                        if not isinstance(session_action, str) or session_action not in {
                            "list", "tree", "new", "rename", "name",
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
                        if session_action == 'new' and (
                                request.get('session_id') or request.get('branch_id')
                                or request.get('index', -1) != -1):
                            raise ValueError('New context cannot select historical routes')
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
                        reserved = self._orchestrator.try_reserve_group(group_id)
                        if not reserved:
                            raise ValueError("Session changes require an idle group")
                        index = request.get("index", -1)
                        if (type(index) is not int or len(session_jobs) >= 8
                                or session_request_id in session_jobs | name_read_jobs):
                            self._orchestrator.release_group_reservation(reserved)
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
                                info = await self._orchestrator.read_session_info(gid)
                                history = info.pop('history', [])
                                await write({'type': 'session_changed', 'request_id': rid,
                                             **info, 'history_count': len(history)})
                                for rows in _history_chunks(history):
                                    await write({'type': 'history_chunk', 'entries': rows})
                                if history:
                                    await write({'type': 'history_end'})
                        task = start_operation(reserved, session_job(group_id, session_request_id, session_action,
                            str(request.get('session_id', '')), str(request.get('branch_id', '')), index,
                            str(request.get('expected_session', '')), reply, name, expected_name))
                        session_jobs[session_request_id] = task, group_id, str(request.get('expected_session', ''))
                        def session_done(completed: asyncio.Task[None], rid: str = session_request_id,
                                         gid: str = group_id) -> None:
                            session_jobs.pop(rid, None)
                            session_started.discard(rid)
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
                    if (valid and run is not None and run.group_id == group_id and not run.task.done()
                            and run.execution.done() and not run.execution.cancelled()):
                        assert isinstance(action, str) and isinstance(external_id, str)
                        assert self._orchestrator is not None
                        prefix = hashlib.sha256(f"{client_id}:{run_id}".encode()).hexdigest()[:16] + ":"
                        try:
                            images = uploads.resolve(request.get("image_refs")) if action == "add" else ()
                            if images:
                                await run_owned(self._orchestrator.validate_images, group_id, images)
                            # Validation yields; a settled run must not enqueue into
                            # a successor session that now owns the same group.
                            success = not run.task.done() and runs.get(run_id) is run and (
                                self._orchestrator.control_input(
                                    group_id, action, prefix + external_id, content, delivery, direction, images,
                                    images_validated=True, expected_execution=run.execution.result(),
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
                                for item in self._orchestrator.pending_inputs(group_id, prefix,
                                                                              expected_execution=run.execution.result())
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
                    if any(key in request for key in ('extension_context_token', 'extension_context_generation',
                            'extension_message_token', 'extension_message_generation')):
                        await write({'type': 'protocol_error' if runs else 'error',
                                     'message': 'Extension host mutation transport has been removed; no replay'})
                        continue
                    run_id = str(request.get("run_id") or f"run-{time.time_ns()}")
                    prefix = hashlib.sha256(f"{client_id}:{run_id}".encode()).hexdigest()[:16] + ":"
                    request = {**request, "run_id": run_id, "_input_scope": prefix,
                               "_message_alive": None, "_context_record": "",
                               "_child_ui": child_ui(str(request.get("group_id", "main")), run_id=run_id),
                               "_command_menu": command_menu_host()}
                    group_id = str(request.get("group_id", "main"))
                    try:
                        images = uploads.resolve(request.get("image_refs"))
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
                            await run_owned(self._orchestrator.validate_images, group_id, images)
                        request = {**request, "_images": images}
                    except ValueError as exc:
                        await write({"type": "error", "run_id": run_id, "message": str(exc)})
                        continue
                    if not isinstance(content, str) or not (content.strip() or images):
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
                    assert self._orchestrator is not None
                    if ("session_id" in request and request["session_id"]
                            != (self._orchestrator.get_new_session_id(group_id) or "")):
                        await write({"type": "error", "run_id": run_id,
                                     "message": "Active session changed; refresh and resume before sending"})
                        continue
                    started = asyncio.Event()
                    execution: asyncio.Future[InputExecution] = asyncio.get_running_loop().create_future()
                    def admitted(identity: InputExecution, future: asyncio.Future[InputExecution] = execution) -> None:
                        if not future.done():
                            future.set_result(identity)
                    request['_on_execution_started'] = admitted
                    notice_channel.bind_group(group_id)
                    request['_reply_reference'] = self._orchestrator.capture_reply(notice_channel,
                        UserInput(group_id=group_id, sender='user', channel='tui'))
                    task = asyncio.create_task(
                        self._requests._handle_chat_message(
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
                        started=started, execution=execution, binding=admitted,
                    )
                    # The handler publishes run_ack only after durable intake.
                    # Do not block the socket reader on a filesystem commit.

                    def _remove_run(done: asyncio.Task[None], rid: str = run_id) -> None:
                        runs.pop(rid, None)
                        if not done.cancelled():
                            done.exception()

                    task.add_done_callback(_remove_run)
                    continue
                if msg_type == "reset":
                    reset_group = str(request.get("group_id", "main"))
                    if bool(
                        self._orchestrator
                        and self._orchestrator.is_group_running(reset_group)
                    ):
                        await write({
                            "type": "error",
                            "message": "Cannot reset while a run is active",
                        })
                        continue
                    if len(extension_jobs) >= 8:
                        await write({'type': 'error', 'message': 'Too many pending control requests'})
                        continue
                    reset_task = asyncio.create_task(
                        self._requests._dispatch(dict(request), writer), name='claw-native-reset')
                    extension_jobs.add(reset_task)

                    def reset_done(done: asyncio.Task[None]) -> None:
                        extension_jobs.discard(done)
                        if not done.cancelled() and done.exception() is not None:
                            logger.debug('Detached reset request failed', exc_info=done.exception())

                    reset_task.add_done_callback(reset_done)
                    continue
                if msg_type == "abort":
                    run_id = str(request.get("run_id", ""))
                    group_id = str(request.get("group_id", "main"))
                    run = runs.get(run_id)
                    if run is None or run.group_id != group_id or self._orchestrator is None:
                        await write({'type': 'abort_ack', 'group_id': group_id, 'run_id': run_id,
                                     'status': 'not_active'})
                        continue
                    if run_id in native_aborts:
                        continue  # One original cancellation receipt, not another producer/approval.
                    async def abort_original(original: _ClientRun, rid: str) -> None:
                        assert self._orchestrator is not None
                        detached = not original.started.is_set()
                        if detached:
                            original.task.cancel()
                            await asyncio.gather(original.task, return_exceptions=True)
                        identity = (original.execution.result() if original.execution.done()
                                    and not original.execution.cancelled() else None)
                        requested = await self._orchestrator.abort_native_input(
                            original.group_id, 'native:' + rid, original.binding, identity)
                        await write({'type': 'abort_ack', 'group_id': original.group_id, 'run_id': rid,
                                     'status': 'requested' if requested else 'not_active'})
                        if requested and detached:
                            await write({'type': 'aborted', 'group_id': original.group_id, 'run_id': rid})
                    job = asyncio.create_task(abort_original(run, run_id), name='claw-native-abort')
                    native_aborts[run_id] = job
                    def abort_done(done: asyncio.Task[None], rid: str = run_id) -> None:
                        if native_aborts.get(rid) is done:
                            native_aborts.pop(rid, None)
                        if not done.cancelled():
                            done.exception()
                    job.add_done_callback(abort_done)
                    continue
                if msg_type == 'input_status':
                    if len(native_reads) >= 16:
                        await write({'type': 'input_status_result', 'request_id': request.get('request_id', ''),
                                     'success': False, 'error': 'Too many outstanding native receipt reads'})
                        continue
                    job = asyncio.create_task(self._requests._read_native_receipt(dict(request), write),
                                              name='claw-native-receipt')
                    native_reads.add(job)
                    def finished_read(done: asyncio.Task[None]) -> None:
                        native_reads.discard(done)
                        if not done.cancelled() and done.exception() is not None:
                            logger.debug('Detached native receipt view failed', exc_info=done.exception())
                    job.add_done_callback(finished_read)
                    continue
                capabilities: dict[str, Any] = dict(skill_controls_active=skills_supported,
                    compaction_active=compaction_supported, saved_source_active=saved_source_supported,
                    saved_timeline_active=saved_timeline_supported, name_read_active=name_read_supported,
                    command_menu_generation=command_catalog_generation,
                    provider_auth_active=provider_auth_supported, provider_auth_generation=provider_auth_generation)
                if msg_type == 'ping':
                    if len(status_jobs) >= 8:
                        await write({'type': 'error', 'message': 'Too many outstanding status reads'})
                        continue

                    async def status_job(payload: dict[str, Any], flags: dict[str, Any]) -> None:
                        async with status_sequence:
                            await self._requests._dispatch(payload, writer, **flags)

                    status_task = asyncio.create_task(status_job(dict(request), capabilities),
                                                      name='claw-native-status')
                    status_jobs.add(status_task)

                    def status_done(done: asyncio.Task[None]) -> None:
                        status_jobs.discard(done)
                        if not done.cancelled() and done.exception() is not None:
                            logger.debug('Detached status read failed', exc_info=done.exception())

                    status_task.add_done_callback(status_done)
                    continue
                await self._requests._dispatch(request, writer, **capabilities)

        except (ConnectionResetError, BrokenPipeError):
            logger.info("Client disconnected: %s", peer)
        except Exception:
            logger.exception("Error handling client %s", peer)
        finally:
            self._acp_viewers.pop(client_id, None)
            async def cleanup_client() -> None:
                nonlocal child_ui_connected
                if self._orchestrator is not None:
                    self._orchestrator.unregister_reply_channel(notice_binding)
                await notice_channel.disconnect()
                try:
                    child_ui_connected = False
                    for status_task in tuple(status_jobs):
                        status_task.cancel()
                    await asyncio.gather(*tuple(status_jobs), return_exceptions=True)
                    for argument_task, _, _ in tuple(argument_jobs.values()):
                        argument_task.cancel()
                    await asyncio.gather(*(row[0] for row in tuple(argument_jobs.values())), return_exceptions=True)
                    for read_task, _, _ in tuple(name_read_jobs.values()):
                        read_task.cancel()
                    for session_task, _, _ in tuple(session_jobs.values()):
                        session_task.cancel()
                    # Disconnect owns readers/dialogs, not already accepted
                    # application work. Explicit abort/shutdown have separate owners.
                    await asyncio.gather(
                        *(binding[0] for binding in tuple(session_jobs.values())), return_exceptions=True)
                    await asyncio.gather(
                        *(binding[0] for binding in tuple(name_read_jobs.values())), return_exceptions=True)
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
                    if self._orchestrator is not None:
                        self._orchestrator.detach_child_ui(child_ui_token)
                    for task in tuple(child_ui_writes):
                        task.cancel()
                    await asyncio.gather(*child_ui_writes, return_exceptions=True)
                    for job in tuple(child_jobs):
                        job.cancel()
                    await asyncio.gather(*child_jobs, return_exceptions=True)
                    for job in tuple(native_aborts.values()):
                        job.cancel()
                    await asyncio.gather(*native_aborts.values(), return_exceptions=True)
                    for job in tuple(native_reads):
                        job.cancel()
                    await asyncio.gather(*native_reads, return_exceptions=True)
                    for job in tuple(extension_jobs):
                        job.cancel()
                    await asyncio.gather(*extension_jobs, return_exceptions=True)
                    await mcp_sender.close()
                    await child_sender.close()
                    await source_sender.close()
                    for run in tuple(runs.values()):
                        run.task.cancel()
                    if runs:
                        await asyncio.gather(
                            *(run.task for run in runs.values()),
                            return_exceptions=True,
                        )
                finally:
                    # Even a failed cleanup step must release the accepted
                    # transport before the server's wait_closed() can settle.
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except (ConnectionError, OSError):
                        pass
                logger.info("Client session ended: %s", peer)

            # EOF can admit cleanup before stop() cancels this handler. Keep one
            # cleanup owner and join it even when cancellation lands in finally;
            # otherwise an interrupted detach skips writer.close() and deadlocks
            # server.wait_closed(). Never detach resource cleanup in the background.
            cleanup = asyncio.create_task(cleanup_client(), name=f"claw-client-{client_id}-cleanup")
            await wait_owned(cleanup)
