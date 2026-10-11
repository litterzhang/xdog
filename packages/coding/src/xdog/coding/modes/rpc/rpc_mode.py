"""RPC mode: headless JSON-over-stdio for programmatic access."""

from __future__ import annotations

import asyncio
import json
import sys  # noqa: F401 -- Compatibility: embedding/tests patch rpc_mode.sys input/output streams.
from collections.abc import Callable
from typing import Any, cast

from xdog.agent import AgentEvent, InputHandledEvent, MessageUpdateEvent
from xdog.ai.types import AssistantMessage, ImageContent, TextContent, ThinkingContent, ToolCall
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionDecision, PermissionRequest

EmitFn = Callable[[dict[str, Any]], None]


def _prompt_images(value: Any) -> tuple[ImageContent, ...]:
    """Inline neutral image content only; never resolve a remote URI or read a supplied file path."""
    from xdog.ai.utils.image_files import MAX_REFERENCE_BYTES
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError('RPC images must be an array of at most four typed attachments')
    result = []
    for row in value:
        if (not isinstance(row, dict) or not {'data', 'mime_type'} <= set(row)
                or set(row) - {'type', 'data', 'mime_type'} or row.get('type', 'image') != 'image'
                or not isinstance(row['data'], str) or not row['data']
                or len(row['data']) > ((MAX_REFERENCE_BYTES + 2) // 3) * 4
                or not isinstance(row['mime_type'], str)
                or row['mime_type'] not in {'image/png', 'image/jpeg', 'image/webp'}):
            raise ValueError('RPC image requires bounded base64 data and PNG/JPEG/WebP mime_type only')
        result.append(ImageContent(data=row['data'], mime_type=row['mime_type']))
    return tuple(result)


async def run_rpc_mode(session: AgentSession) -> int:
    """Run the JSON-lines RPC protocol, including asynchronous approvals.

    Prompt turns run in a task while this coroutine continues reading commands.
    That is required for a client to answer ``permission_request`` while the
    corresponding tool hook is suspended.
    """
    from xdog.coding.modes.rpc.images import RpcImages
    from xdog.coding.modes.rpc.input import MAX_RPC_LINE_BYTES, RpcInput
    from xdog.coding.modes.rpc.output import MAX_OUTPUT_BYTES, RpcOutput
    inputs = RpcInput()
    await inputs.open()
    uploads = RpcImages(session)

    output = RpcOutput()
    output.open()
    _emit = output.emit

    def _permission_request(request: PermissionRequest) -> None:
        _emit({
            "type": "permission_request",
            "id": request.id,
            "tool": request.tool_name,
            "arguments": request.arguments,
            "summary": request.summary,
        })

    from xdog.coding.modes.rpc.children import CAPABILITIES as CHILD_CAPABILITIES
    from xdog.coding.modes.rpc.children import RpcChildren
    from xdog.coding.modes.rpc.events import RpcEvents
    from xdog.coding.modes.rpc.extensions import CAPABILITIES, RpcExtensions
    from xdog.coding.modes.rpc.navigation import RpcNavigation
    from xdog.coding.modes.rpc.parent_drafts import CAPABILITIES as PARENT_DRAFT_CAPABILITIES
    from xdog.coding.modes.rpc.parent_drafts import RpcParentDrafts
    from xdog.coding.modes.rpc.terminal import RpcTerminal
    from xdog.coding.modes.rpc.transcripts import RpcTranscripts
    navigation = RpcNavigation(session, _emit)
    parent_drafts = RpcParentDrafts(session, navigation, output.send)
    transcripts = RpcTranscripts(session, navigation, _emit, send=output.send)
    transient = RpcEvents(session, navigation, _emit)
    terminal = RpcTerminal(session, navigation, _emit)
    children = RpcChildren(session, navigation, transcripts, _emit, images=uploads, send=output.send)
    extensions = RpcExtensions(session, _emit, navigation=navigation, send=output.send)
    session.permissions.set_request_handler(_permission_request)
    _emit({
        "type": "ready",
        "session_id": session.session_id,
        "permission_mode": session.permissions.mode,
        "capabilities": {**CAPABILITIES, **CHILD_CAPABILITIES, **PARENT_DRAFT_CAPABILITIES,
                         "image_prompts": 1, "image_uploads": 1,
                         "session_controls": 1, "session_compaction": 1,
                         "session_checkpoint_targets": 1, "transcript_sources": 1, "transcript_canonical": 1,
                         "transient_events": 1, "terminal_input": 1, "terminal_release_input": 1,
                         "rpc_async_output": int(output.fd is not None)},
        "session_revision": navigation.revision,
        "max_input_frame_bytes": MAX_RPC_LINE_BYTES,
        "max_output_backlog_bytes": MAX_OUTPUT_BYTES,
    })

    turn_task: asyncio.Task[None] | None = None
    try:
        while True:
            transient.bind()
            try:
                if output.fd is None:
                    raw_line, frame_error = await inputs.read()
                else:
                    reading = asyncio.create_task(inputs.read())
                    try:
                        finished, _ = await asyncio.wait((reading, output.failure),
                                                         return_when=asyncio.FIRST_COMPLETED)
                        if output.failure in finished:
                            break
                        raw_line, frame_error = reading.result()
                    finally:
                        if not reading.done():
                            reading.cancel()
                        await asyncio.gather(reading, return_exceptions=True)
            except (EOFError, KeyboardInterrupt):
                break

            if turn_task is not None and turn_task.done():
                await asyncio.gather(turn_task, return_exceptions=True)
                turn_task = None

            if raw_line is None:
                break
            if frame_error:
                _emit({'type': 'error', 'message': frame_error})
                continue
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            try:
                cmd = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                _emit({"type": "error", "message": f"Invalid JSON: {exc}"})
                continue
            if not isinstance(cmd, dict):
                _emit({"type": "error", "message": "RPC command must be an object"})
                continue

            cmd_type = cmd.get('type', '')
            if not isinstance(cmd_type, str):
                _emit({'type': 'error', 'message': 'RPC command type must be text'})
                continue
            transient.bind()
            turn_running = turn_task is not None and not turn_task.done() or extensions.prompting

            if cmd_type in {'parent_draft_control', 'parent_draft_cancel'}:
                try:
                    if cmd_type == 'parent_draft_control':
                        parent_drafts.start(cmd, blocked=turn_running or extensions.busy or terminal.busy
                                            or navigation.busy or children.busy)
                    else:
                        parent_drafts.cancel(cmd)
                except (ValueError, RuntimeError, OSError) as exc:
                    _emit({'type': 'error', 'action': cmd_type, 'request_id': cmd.get('request_id'),
                           'message': str(exc)})
                continue
            if cmd_type in {'child_control', 'child_control_cancel', 'child_source_read',
                            'child_permission_response', 'child_dialog_response', 'child_custom_input',
                            'child_terminal_input'}:
                try:
                    if cmd_type == 'child_control':
                        children.start(cmd, turn_running=turn_running,
                                       extension_running=extensions.busy or terminal.busy or parent_drafts.busy)
                    elif cmd_type == 'child_control_cancel':
                        children.cancel(cmd)
                    elif cmd_type == 'child_source_read':
                        children.source(cmd)
                    elif cmd_type == 'child_terminal_input':
                        if terminal.busy or parent_drafts.busy:
                            raise RuntimeError('Parent terminal/draft work owns the input operation')
                        children.terminal_input(cmd)
                    elif cmd_type == 'child_custom_input':
                        children.custom_input(cmd)
                    elif cmd_type == 'child_permission_response':
                        children.answer_permission(cmd)
                    else:
                        children.answer_dialog(cmd)
                except (ValueError, RuntimeError, OSError) as exc:
                    _emit({'type': 'error', 'action': cmd_type, 'request_id': cmd.get('request_id'),
                           'message': str(exc)})
                continue
            if cmd_type in {'terminal_input', 'terminal_cancel'}:
                try:
                    if cmd_type == 'terminal_input':
                        terminal.start(cmd, blocked=extensions.busy or children.busy or turn_running
                                       or parent_drafts.busy)
                    else:
                        terminal.cancel(cmd)
                except (ValueError, RuntimeError, OSError) as exc:
                    _emit({'type': 'error', 'action': cmd_type, 'request_id': cmd.get('request_id'),
                           'message': str(exc)})
                continue
            if cmd_type in {'transcript_read', 'transcript_cancel'}:
                try:
                    if cmd_type == 'transcript_read':
                        transcripts.start(cmd)
                    else:
                        transcripts.cancel(cmd)
                except (ValueError, RuntimeError, OSError) as exc:
                    _emit({'type': 'error', 'action': cmd_type, 'request_id': cmd.get('request_id'),
                           'message': str(exc)})
                continue
            if cmd_type in {'session_control', 'session_control_cancel'}:
                try:
                    if cmd_type == 'session_control':
                        navigation.start(cmd, turn_running=turn_running,
                                         extension_running=extensions.busy or children.busy or terminal.busy
                                         or parent_drafts.busy)
                    else:
                        navigation.cancel(cmd)
                        _emit({'type': 'session_cancel_requested', 'request_id': cmd.get('request_id'),
                               'expected_session': cmd.get('expected_session')})
                except (ValueError, RuntimeError, OSError) as exc:
                    _emit({'type': 'error', 'action': cmd_type, 'request_id': cmd.get('request_id'),
                           'message': str(exc)})
                continue
            if cmd_type in {'rpc_hello', 'extension_control', 'extension_dialog_response', 'extension_control_cancel',
                            'extension_custom_input'}:
                try:
                    if cmd_type == 'rpc_hello':
                        # Cross-controller preflight: a later invalid child/event field
                        # must not leave earlier extension grants or revocations applied.
                        for capability in (*CAPABILITIES, *CHILD_CAPABILITIES, *PARENT_DRAFT_CAPABILITIES,
                                           'transient_events', 'terminal_input', 'terminal_release_input'):
                            if capability in cmd and (type(cmd[capability]) is not int
                                    or cmd[capability] not in (0, 1)):
                                raise ValueError('RPC capabilities require exact integer 0 or 1')
                        if any(key in cmd for key in CAPABILITIES) or not any(
                                key in cmd for key in
                                ('transcript_sources', 'transcript_canonical', 'transient_events', 'terminal_input',
                                 'terminal_release_input',
                                 *CHILD_CAPABILITIES, *PARENT_DRAFT_CAPABILITIES)):
                            extensions.negotiate(cmd)
                        parent_drafts.negotiate(cmd)
                        if any(key in cmd for key in PARENT_DRAFT_CAPABILITIES):
                            _emit({'type': 'parent_draft_capabilities', **parent_drafts.envelope(),
                                   **{key: int(value) for key, value in parent_drafts.flags.items()}})
                        terminal.negotiate(cmd)
                        transient.negotiate(cmd)
                        transcripts.negotiate(cmd)
                        children.negotiate(cmd)
                    elif cmd_type == 'extension_control':
                        if navigation.busy or children.busy or terminal.busy or parent_drafts.busy:
                            raise RuntimeError('Session navigation owns this RPC connection')
                        navigation.prompt_owner(cmd)
                        extensions.start(cmd, turn_running=turn_running)
                    elif cmd_type == 'extension_custom_input':
                        extensions.custom_input(cmd)
                    elif cmd_type == 'extension_dialog_response':
                        extensions.answer(cmd)
                    else:
                        extensions.cancel(cmd)
                except (ValueError, RuntimeError, OSError) as exc:
                    _emit({'type': 'error', 'action': cmd_type, 'message': str(exc),
                           'request_id': cmd.get('request_id')})
                continue
            if cmd_type == "quit":
                session.abort()
                session.permissions.deny_all()
                _emit({"type": "quit_ack"})
                break

            if cmd_type == "permission_response":
                request_id = cmd.get("id", "")
                decision = cmd.get("decision", "")
                valid = ("allow_once", "allow_session", "deny")
                if not isinstance(request_id, str) or decision not in valid:
                    _emit({"type": "error", "message": "Invalid permission response"})
                    continue
                resolved = session.permissions.resolve(
                    request_id,
                    cast(PermissionDecision, decision),
                )
                if resolved:
                    _emit({
                        "type": "ok",
                        "action": "permission_response",
                        "id": request_id,
                    })
                else:
                    _emit({
                        "type": "error",
                        "message": f"Unknown permission request: {request_id}",
                    })
                continue

            if cmd_type in {'image_chunk', 'image_discard'}:
                try:
                    status = uploads.chunk(cmd) if cmd_type == 'image_chunk' else 'discarded'
                    if cmd_type == 'image_discard':
                        uploads.discard(cmd)
                    _emit({'type': 'image_upload_result', 'id': cmd.get('id'), 'session_id': session.session_id,
                           'status': status, **({'index': cmd.get('index')} if cmd_type == 'image_chunk' else {})})
                except (ValueError, OSError) as exc:
                    _emit({'type': 'image_upload_result', 'id': cmd.get('id'), 'session_id': session.session_id,
                           'status': 'error', 'message': str(exc)})
                continue
            if cmd_type == "prompt":
                message = cmd.get("message", "")
                if (turn_running or extensions.busy or navigation.busy or children.starting
                        or terminal.busy or parent_drafts.busy):
                    _emit({'type': 'error', 'message': 'A prompt is already running'})
                    continue
                try:
                    navigation.prompt_owner(cmd)
                    images = _prompt_images(cmd.get('images', []))
                    if 'image_refs' in cmd:
                        if images:
                            raise ValueError('Use inline images or image_refs, not both')
                        if not isinstance(message, str):
                            raise ValueError('Prompt message must be text')
                        images = uploads.resolve(cmd)
                except ValueError as exc:
                    _emit({'type': 'error', 'action': 'prompt', 'message': str(exc)})
                    continue
                if not isinstance(message, str) or not message and not images:
                    _emit({"type": "error", "message": "Missing 'message' field"})
                elif turn_running or extensions.busy:
                    _emit({"type": "error", "message": "A prompt is already running"})
                else:
                    if extensions.ui_enabled:
                        request_id = cmd.get('request_id')
                        try:
                            request_id = extensions._identity(request_id, cmd.get('session_id'))
                        except ValueError as exc:
                            _emit({'type': 'error', 'action': 'prompt', 'message': str(exc)})
                            continue
                        if 'image_refs' in cmd:
                            images = uploads.consume(cmd)  # Lease/header accepted; single-use admission, not peek.
                        async def ui_prompt(text: str, key: str, attachments: tuple[ImageContent, ...]) -> None:
                            async with extensions.bind(key):
                                await _handle_prompt(session, text, _emit, images=attachments)
                        turn_task = asyncio.create_task(ui_prompt(message, request_id, images))
                    elif images:
                        if 'image_refs' in cmd:
                            images = uploads.consume(cmd)
                        turn_task = asyncio.create_task(_handle_prompt(session, message, _emit, images=images))
                    else:
                        turn_task = asyncio.create_task(_handle_prompt(session, message, _emit))
                continue

            if cmd_type == "abort":
                navigation.abort_compaction()
                extensions.abort_prompt()
                extensions.after_run_tools.abort()
                if turn_task is not None and not turn_task.done():
                    turn_task.cancel()  # Includes pre-turn extension/compaction work, not only Agent streaming.
                session.abort()
                session.permissions.deny_all()
                _emit({"type": "ok", "action": "abort"})
                continue

            if cmd_type in ('compact', 'set_model', 'set_thinking'):
                try:
                    navigation.prompt_owner(cmd)
                except ValueError as exc:
                    _emit({'type': 'error', 'action': cmd_type, 'message': str(exc)})
                    continue
            if cmd_type in ("compact", "set_model", "set_thinking") and session.children.supervisor.active:
                _emit({"type": "error", "message": f"Cannot {cmd_type} while child work is active"})
                continue

            if (cmd_type in ("compact", "set_model", "set_thinking")
                    and (turn_running or extensions.busy or navigation.busy or children.busy
                         or terminal.busy or parent_drafts.busy)):
                _emit({"type": "error", "message": f"Cannot {cmd_type} during a running prompt/control"})
                continue

            try:
                if cmd_type == "compact":
                    import uuid
                    if cmd.get('session_id', session.session_id) != session.session_id:
                        raise ValueError('Compaction session owner changed')
                    navigation.start({'action': 'compact',
                        'request_id': cmd.get('request_id', 'compact-' + uuid.uuid4().hex),
                        'expected_session': cmd.get('expected_session', session.session_id),
                        'session_revision': cmd.get('session_revision'),
                        'instructions': cmd.get('instructions', ''), 'target_session': cmd.get('target_session', ''),
                        'branch_id': cmd.get('branch_id', ''), 'index': cmd.get('index', -1)},
                        turn_running=turn_running, extension_running=extensions.busy, legacy_compact=True)
                elif cmd_type == "set_model":
                    model_id = cmd.get("model", "")
                    if not isinstance(model_id, str) or not model_id:
                        _emit({"type": "error", "message": "Missing 'model' field"})
                        continue
                    session.set_model(model_id)
                    _emit({"type": "ok", "action": "set_model", "model": model_id, **navigation.refresh_revision()})
                elif cmd_type == "set_thinking":
                    level = cmd.get("level", "")
                    if level in ("off", ""):
                        session.set_thinking_level(None)
                        _emit({"type": "ok", "action": "set_thinking", "level": "off", **navigation.refresh_revision()})
                    elif level in ("minimal", "low", "medium", "high", "xhigh"):
                        session.set_thinking_level(cast(Any, level))
                        _emit({"type": "ok", "action": "set_thinking", "level": level, **navigation.refresh_revision()})
                    else:
                        _emit({"type": "error", "message": f"Invalid thinking level: {level}"})
                elif cmd_type == 'status' and navigation.busy:
                    _emit({'type': 'status', **navigation.status_before, 'prompt_running': turn_running,
                           'extension_running': False, 'navigation_running': True,
                           'compaction_running': navigation.compacting,
                           'transcript_running': transcripts.active is not None or transcripts.pending is not None,
                           'transient_transport_dropped': dict(output.transient_dropped),
                           'session_revision': navigation.revision})
                elif cmd_type == "status":
                    thinking = session.agent.options.thinking or "off"
                    _emit({
                        "type": "status",
                        "session_id": session.session_id,
                        "model": session.model or "unknown",
                        "thinking": str(thinking),
                        "permission_mode": session.permissions.mode,
                        "prompt_running": turn_running,
                        "extension_running": extensions.busy,
                        "navigation_running": False, "compaction_running": False,
                        "session_revision": navigation.revision,
                        "transcript_running": transcripts.active is not None or transcripts.pending is not None,
                        "transient_transport_dropped": dict(output.transient_dropped),
                        "messages": len(session.messages),
                        "working_dir": str(session.working_dir),
                    })
                else:
                    _emit({"type": "error", "message": f"Unknown command type: {cmd_type}"})
            except (ValueError, RuntimeError, OSError) as exc:
                _emit({'type': 'error', 'action': cmd_type, 'message': str(exc)})
    finally:
        extensions.abort_prompt()
        # Stop the parent before waiting for child/source/extension ownership to
        # settle; EOF/output failure must not leave it producing more turns.
        if turn_task is not None:
            session.abort()
            if not turn_task.done():
                turn_task.cancel()
        await parent_drafts.aclose()
        await terminal.aclose()
        transient.close()
        session.permissions.deny_all()
        inputs.close()
        uploads.close()
        await children.aclose()
        await transcripts.aclose()
        await navigation.aclose()
        await extensions.aclose()
        session.permissions.set_request_handler(None)
        session.permissions.deny_all()
        if turn_task is not None:
            session.abort()
            if not turn_task.done():
                turn_task.cancel()  # EOF/connection loss owns cleanup just as explicit quit does.
            await asyncio.gather(turn_task, return_exceptions=True)
        await output.aclose()

    return 1 if output.error is not None else 0


async def _handle_prompt(
    session: AgentSession,
    message: str,
    emit: EmitFn,
    *, images: tuple[ImageContent, ...] = (), input_origin: str = 'user',
) -> None:
    """Handle one prompt while the RPC command reader remains active."""
    emit({"type": "message_start"})

    input_handled = False
    def on_event(event: AgentEvent) -> None:
        nonlocal input_handled
        if isinstance(event, InputHandledEvent):
            input_handled = True
            emit({'type': 'input_handled', 'feedback': event.feedback, 'source': event.source, 'id': event.input_id})
        elif isinstance(event, MessageUpdateEvent) and isinstance(event.message, AssistantMessage):
            for part in event.message.content:
                if isinstance(part, TextContent):
                    emit({'type': 'text_delta', 'text': part.text})
                elif isinstance(part, ThinkingContent) and not part.redacted:
                    emit({'type': 'thinking_delta', 'text': part.thinking})
                elif isinstance(part, ToolCall):
                    emit({'type': 'tool_call', 'name': part.name, 'arguments': part.arguments})
    unsubscribe = session.agent.subscribe(on_event)
    compaction_success = False
    try:
        # The SDK owns prompt rebuild/compaction/lifecycle/skill retirement and
        # persistence. This subscriber only adapts events to the existing wire format.
        last_assistant = (await session.send_message(message, images=images) if input_origin == 'user' else
                          await session.send_message(message, images=images, input_origin=input_origin))

        error = getattr(session.agent.state, "error", None)
        if last_assistant is None and isinstance(error, str) and error:
            raise RuntimeError(error)
        content_parts: list[dict[str, Any]] = []
        if last_assistant is not None:
            for part in last_assistant.content:
                if isinstance(part, TextContent):
                    content_parts.append({"type": "text", "text": part.text})
                elif isinstance(part, ThinkingContent) and not part.redacted:
                    content_parts.append({"type": "thinking", "thinking": part.thinking})
                elif isinstance(part, ToolCall):
                    content_parts.append({
                        "type": "tool_call",
                        "name": part.name,
                        "arguments": part.arguments,
                    })
        emit({'type': 'message_end', 'content': content_parts,
              **({'input_handled': True} if input_handled else {}),
              **({'is_error': True} if isinstance(error, str) and error else {})})
        if isinstance(error, str) and error:
            emit({'type': 'error', 'message': error})
        compaction_success = not bool(error)
    except asyncio.CancelledError:
        # Canceling this consumer must also settle its owned producer before the
        # RPC reader/session can admit another prompt or dispose permissions/tools.
        cleanup = asyncio.create_task(session.agent.cancel_and_wait())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()
        emit({'type': 'error', 'message': 'Interrupted'})
        raise
    except KeyboardInterrupt:
        emit({"type": "error", "message": "Interrupted"})
    except Exception as exc:
        emit({"type": "error", "message": str(exc)})
    finally:
        from xdog.coding.modes.rpc.after_run_compaction import producer_outcome
        producer_outcome(compaction_success)
        unsubscribe()
