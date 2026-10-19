"""ACP addressing/peer waits over application-owned input receipts, with no business policy."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

try:
    from acp import PromptResponse, RequestError
    from acp.schema import AgentMessageChunk, TextContentBlock
except ImportError as exc:
    raise ImportError('Claw ACP requires Python3.12-3.14 and agent-client-protocol>=0.12.1,<0.13') from exc

from xdog.agent.acp.contracts import JsonValue, Peer, SessionRef, UpdateSink, finite_json, invalid
from xdog.agent.async_work import run_owned, wait_owned
from xdog.agent.session_input import decode_input
from xdog.claw.core.inputs.consumer import InputService
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.orchestrator import Orchestrator


class ClawBackend:
    extensions = {'enqueue': 1, 'inbox_status': 1, 'session_info': 1}

    def __init__(self, orchestrator: Orchestrator, inputs: InputService) -> None:
        self.orchestrator, self.inputs = orchestrator, inputs
        self.generation = uuid.uuid4().hex
        self._bindings: dict[tuple[str, str], SessionRef] = {}
        self._closed_peers: set[str] = set()
        self._prompts: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._cancelled_requests: set[tuple[str, str]] = set()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    @classmethod
    async def open(cls, orchestrator: Orchestrator) -> ClawBackend:
        return cls(orchestrator, await orchestrator.input_service())

    def _active(self, peer: Peer) -> None:
        if self._closing or peer.id in self._closed_peers:
            raise invalid("ACP ingress/peer is closed")

    def _target(self, session_id: object, cwd: object | None = None) -> tuple[str, Path]:
        if not isinstance(session_id, str) or not session_id.startswith("group:"):
            raise ValueError("Claw ACP requires an explicit group:<id> address")
        group = session_id.removeprefix("group:")
        workspace = self.orchestrator.get_group_workspace(group)
        if workspace is None:
            raise ValueError("Unknown Claw group")
        if cwd is not None:
            if not isinstance(cwd, (str, Path)):
                raise ValueError("Expected absolute workspace path")
            path = Path(cwd)
            if not path.is_absolute() or path != workspace:
                raise ValueError("Group workspace mismatch")
        return group, workspace

    def _binding(self, ref: SessionRef, peer: Peer) -> tuple[str, Path]:
        self._active(peer)
        if self._bindings.get((peer.id, ref.id)) != ref:
            raise invalid("Unknown group binding")
        try:
            return self._target(ref.id, ref.cwd)
        except ValueError as exc:
            raise invalid(str(exc)) from exc

    async def _resolved_target(self, session_id: object, cwd: object, peer: Peer) -> tuple[str, Path]:
        self._active(peer)
        expected = self._target(session_id)  # Capture original registry identity on its loop.
        if not isinstance(cwd, (str, Path)) or not Path(cwd).is_absolute():
            raise ValueError("Expected absolute workspace path")
        resolved = await run_owned(Path(cwd).resolve)
        self._active(peer)
        target = self._target(session_id, resolved)
        if target != expected:
            raise ValueError("Group workspace changed during validation")
        return target

    async def _bound(self, ref: SessionRef, peer: Peer) -> tuple[str, Path]:
        expected = self._binding(ref, peer)
        try:
            target = await self._resolved_target(ref.id, ref.cwd, peer)
            if self._binding(ref, peer) != expected or target != expected:
                raise ValueError("Group binding changed during validation")
            return target
        except ValueError as exc:
            raise invalid(str(exc)) from exc

    async def new_session(self, cwd: Path, peer: Peer) -> SessionRef:
        raise invalid("Claw ACP never creates groups; load an explicit group:<id>")

    async def load_session(self, session_id: str, cwd: Path, peer: Peer, emit: UpdateSink) -> SessionRef:
        try:
            _group, workspace = await self._resolved_target(session_id, cwd, peer)
        except ValueError as exc:
            raise invalid(str(exc)) from exc
        ref = SessionRef(session_id, workspace, self.generation)
        self._bindings[peer.id, session_id] = ref
        return ref

    async def prompt(self, ref: SessionRef, peer: Peer, text: str,
                     metadata: dict[str, JsonValue], emit: UpdateSink) -> PromptResponse:
        group, cwd = self._binding(ref, peer)
        scope, key = InputScope(group, cwd), (peer.id, ref.id)
        if key in self._prompts:
            raise invalid('A prompt is already pending for this peer/group')
        task = asyncio.current_task()
        assert task is not None
        self._prompts[key] = task
        identifier = uuid.uuid4().hex
        try:
            await self._bound(ref, peer)
            details = metadata.get('xdog', {})
            if not isinstance(details, dict):
                raise ValueError('Invalid external input metadata')
            message = decode_input({'id': identifier, 'origin': 'acp', 'source': details.get('source', 'ACP'),
                'instruction': text, 'evidence': details.get('evidence', []),
                'provenance': details.get('provenance', {})}, transport_envelope=False)
            await self.orchestrator.accept_external(scope, message)
            record = await self.inputs.wait(scope, identifier)
            if record.status != 'succeeded':
                raise RequestError(-32000, record.error or 'Input did not complete; inspect its durable receipt')
            self._active(peer)
            for offset in range(0, len(record.response), 16_384):
                await emit(AgentMessageChunk(session_update='agent_message_chunk', content=TextContentBlock(
                    type='text', text=record.response[offset:offset + 16_384])))
            return PromptResponse.model_validate({'stopReason': 'end_turn', '_meta': {'xdog': {
                'message_id': identifier, 'execution_session_id': record.session_id, 'status': record.status}}})
        except asyncio.CancelledError:
            if key in self._cancelled_requests and not self._closing and peer.id not in self._closed_peers:
                existing = await self.inputs.store.get(scope, identifier)
                return PromptResponse.model_validate({'stopReason': 'cancelled', '_meta': {'xdog': {
                    'message_id': identifier, 'execution_continues': existing is not None}}})
            raise
        except (ValueError, TypeError) as exc:
            raise invalid(str(exc)) from exc
        finally:
            self._cancelled_requests.discard(key)
            self._prompts.pop(key, None)

    async def cancel(self, ref: SessionRef, peer: Peer) -> None:
        self._binding(ref, peer)  # Cancel the original waiter even while path validation is pending.
        task = self._prompts.get((peer.id, ref.id))
        if task is not None:
            self._cancelled_requests.add((peer.id, ref.id))
            task.cancel()  # Detach waiter only; accepted durable work is not retracted.

    async def disconnect(self, peer: Peer) -> None:
        self._closed_peers.add(peer.id)
        tasks = [task for (owner, _), task in self._prompts.items() if owner == peer.id]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for key in tuple(self._bindings):
            if key[0] == peer.id:
                del self._bindings[key]

    async def extension(self, peer: Peer, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._active(peer)
        try:
            finite_json(params)
            if method == 'xdog/session_info':
                if set(params) != {'session_id'}:
                    raise ValueError('Expected session_id')
                ref = self._bindings.get((peer.id, params['session_id']))
                if ref is None:
                    raise ValueError('Unknown bound group')
                await self._bound(ref, peer)
                return {'session_id': ref.id, 'cwd': str(ref.cwd), 'generation': ref.generation}
            if method not in {'xdog/enqueue', 'xdog/inbox_status'}:
                raise RequestError(-32601, 'Unsupported Claw ACP extension')
            group, cwd = await self._resolved_target(params.get('session_id'), params.get('cwd', ''), peer)
            scope = InputScope(group, cwd)
            if method == 'xdog/enqueue':
                fields = {'session_id', 'cwd', 'message_id', 'instruction', 'source', 'evidence', 'provenance'}
                if set(params) != fields:
                    raise ValueError('Invalid enqueue fields')
                message = decode_input({'id': params['message_id'], 'origin': 'acp', 'source': params['source'],
                    'instruction': params['instruction'], 'evidence': params['evidence'],
                    'provenance': params['provenance']}, transport_envelope=False)
                record, duplicate = await self.orchestrator.accept_external(scope, message)
                return {'session_id': params['session_id'], 'message_id': message.id, 'branch_id': '',
                        'status': 'duplicate' if duplicate else 'queued', 'message_state': record.status}
            if set(params) - {'session_id', 'cwd', 'message_id', 'cursor'}:
                raise ValueError('Invalid inbox status fields')
            selected, cursor = params.get('message_id'), params.get('cursor', 0)
            if selected is not None and 'cursor' in params:
                raise ValueError('Select a message or a cursor, not both')
            namespace = await self.inputs.store.namespace(scope)
            messages, next_cursor = await self.inputs.store.status(scope, selected=selected, cursor=cursor)
            return {'session_id': params['session_id'], 'cwd': str(cwd), 'branch_id': '', 'inbox_id': namespace,
                    'messages': [{**row, 'branch_id': ''} for row in messages], 'next_cursor': next_cursor}
        except (ValueError, TypeError, UnicodeError) as exc:
            raise invalid(str(exc)) from exc
        except RuntimeError as exc:
            raise RequestError(-32000, 'Application input service unavailable; inspect receipts') from exc

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        prompts = tuple(self._prompts.values())
        for task in prompts:
            task.cancel()
        await asyncio.gather(*prompts, return_exceptions=True)
        self._bindings.clear()
        # Endpoint/peer ownership ends here; application IO/execution remains alive.
