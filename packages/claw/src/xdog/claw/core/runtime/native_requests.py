"""Native request execution and projections, separate from peer/gateway lifetime."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any

from xdog.claw.core.inputs.models import ReplyTarget
from xdog.claw.core.runtime.input_queries import NativeInputReceipts
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import UserInput

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_HISTORY_FRAME_LIMIT = 512 * 1024


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


class NativeRequests:
    """Protocol operations using explicit application, receipt and transport ports."""

    def __init__(self, application: Orchestrator | None, receipts: NativeInputReceipts | None,
                 write_response: Callable[[asyncio.StreamWriter, dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
        self._orchestrator = application
        self._native_inputs = receipts
        self._write_response = write_response

    async def _read_native_receipt(self, request: dict[str, Any],
                                   write: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        from xdog.claw.core.runtime.input_queries import _label
        payload: dict[str, Any] = {'type': 'input_status_result', 'request_id': request.get('request_id', ''),
                                   'success': False}
        try:
            _label(request.get('request_id'), maximum=128)
            if self._native_inputs is None:
                raise ValueError('Native input receipt service is unavailable')
            namespace = _label(request.get('namespace'))
            group, message_id = _label(request.get('group_id')), _label(request.get('message_id'))
            wait = request.get('wait', False)
            if not isinstance(wait, bool):
                raise ValueError('Invalid native receipt wait flag')
            result = await self._native_inputs.read(group, namespace, message_id, wait=wait)
            payload.update(success=True, namespace=namespace, message=result)
        except Exception as exc:
            payload['error'] = str(exc)[:1024]
        await write(payload)

    async def _dispatch(
        self, request: dict[str, Any], writer: asyncio.StreamWriter, *,
        skill_controls_active: bool = False,
        compaction_active: bool = False,
        saved_source_active: bool = False,
        saved_timeline_active: bool = False,
        name_read_active: bool = False,
        command_menu_generation: int = 0,
        provider_auth_active: bool = False,
        provider_auth_generation: int = 0,
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
                "type": "pong", "input_controls": 1, "session_controls": 1, "session_rename": 1,
                "session_name_read": int(name_read_active),
                "session_navigation_cancel": 1, "attachment_transport": 1,
                "transcript_source": 1, "mcp_controls": 1, "mcp_browser_owner": 1,
                "mcp_context": 1, "mcp_completion": 1,
                "mcp_content_types": 1,
                "tool_inventory": 1, "tool_inventory_selection": 1,
                "mcp_resource_completion": 1,
                "extension_command_results": 1,
                "extension_session": 1,
                "extension_provider_auth": int(provider_auth_active),
                "extension_provider_auth_generation": provider_auth_generation,
                "extension_session_inspection": 1, "extension_command_cancel": 1,
                "extension_command_catalog": 1, "extension_command_catalog_generation": command_menu_generation,
                "extension_argument_completion": 1,
                "extension_argument_cancel": 1, "extension_argument_context": 1,
                "extension_shortcuts": 1, "extension_models": 1, "native_models": 1, "reasoning_controls": 1,
                "mcp_auth": 1,
                "saved_timeline": int(saved_timeline_active), "saved_source": int(saved_source_active),
                "session_compaction": int(compaction_active),
                "skill_controls": int(skill_controls_active),
                "mcp_refresh": 1, "mcp_credentials": 1,
                "access_controls": 1, "extension_controls": 1, "child_controls": 1,
                "child_source": 1, "child_queues": 1,
                "child_dialogs": 1, "child_config": 3, "child_images": 1,
            }
            session_info['input_receipts'] = 0
            if self._native_inputs is not None:
                try:
                    session_info['inbox_namespace'] = await self._native_inputs.namespace(group_id)
                    session_info['input_receipts'] = 1
                except ValueError:
                    pass
            history: list[dict[str, Any]] = []
            if self._orchestrator:
                try:
                    session_info.update(await self._orchestrator.read_session_info(group_id))
                except (ValueError, RuntimeError, OSError) as exc:
                    await self._write_response(writer, {'type': 'error',
                                                        'message': 'Status unavailable: ' + str(exc)[:512]})
                    return
                if group_id in self._orchestrator.get_group_ids():
                    runtime = self._orchestrator._runtimes[group_id]
                    if runtime._active_session is not None:
                        session_info["extension_snapshot"] = runtime._active_session.extensions.snapshot()
                        session_info["thinking"] = runtime._active_session.agent.options.thinking or "off"
                    try:
                        result = await self._orchestrator.manage_access(
                            group_id, "status", self._orchestrator.get_new_session_id(group_id) or "")
                        session_info["access"] = result["access"]
                    except (ValueError, RuntimeError, OSError):
                        session_info["access_controls"] = 0
                        session_info["access_error"] = "Invalid saved access; repair or explicitly create a new session"
                raw_history = session_info.pop("history", [])
                if isinstance(raw_history, list):
                    history = raw_history
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
        self, request: dict[str, Any], writer: asyncio.StreamWriter, *,
        write: Any = None, started: asyncio.Event | None = None,
    ) -> None:
        try:
            await self._run_chat_message(request, writer, write=write, started=started)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            publish = write or (lambda data: self._write_response(writer, data))
            await publish({'type': 'error', 'run_id': str(request.get('run_id', '')),
                           'message': str(exc) or type(exc).__name__})
        finally:
            if started is not None:
                started.set()

    async def _run_chat_message(
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
        # Stream presentation-neutral turn events to the TUI client.
        accumulated_text = ""
        streamed_any_text = False
        pending_writes: list[asyncio.Task[None]] = []
        orchestrator = self._orchestrator

        async def publish_accepted() -> None:
            namespace = await self._native_inputs.namespace(group_id) if self._native_inputs is not None else ''
            await write_response({'type': 'run_ack', 'group_id': group_id, 'run_id': run_id,
                'message_id': 'native:' + run_id, 'inbox_namespace': namespace,
                'session_id': orchestrator.get_new_session_id(group_id) or ''})

        def accepted() -> None:
            if started is not None:
                started.set()
                pending_writes.append(asyncio.create_task(publish_accepted()))

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
                command_menu=request.get("_command_menu"),
                message_id='native:' + run_id,
                on_accepted=accepted, on_execution_started=request.get('_on_execution_started'),
                reply_reference=request.get('_reply_reference'),
                reply_target=ReplyTarget('tui', run_id),
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

        if result is None:
            await write_response({
                "type": "queued",
                "group_id": group_id,
                "run_id": run_id,
            })
        elif result.error:
            if (result.error == "aborted" or result.canceled) and not result.retain_partial:
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

        try:
            reset = group_id in self._orchestrator.get_group_ids()
            if reset:
                expected = self._orchestrator.get_new_session_id(group_id) or ''
                await self._orchestrator.navigate_session(group_id, 'new', '', '', -1, expected)
        except (ValueError, RuntimeError) as exc:
            await self._write_response(writer, {"type": "error", "message": str(exc)})
            return
        if reset:
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
