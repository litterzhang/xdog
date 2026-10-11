"""Opt-in remote parent snapshot controls; never the parent prompt or host composer.

The RPC client owns its composer. Receipt-bound recovery returns editing data only;
clients must apply it through their own unchanged/empty-composer admission gate.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from xdog.agent.supervision_transfer import settled_image_work
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.tui.attachments import attachment
from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
from xdog.tui.child_drafts import ChildDraft, draft_receipt

CAPABILITIES = {'parent_saved_drafts': 1, 'parent_saved_images': 1,
                'parent_saved_save': 1, 'parent_saved_discard': 1}


@dataclass(frozen=True)
class _Review:
    operation: str
    deadline: float
    body: dict[str, Any]
    owner: dict[str, Any]
    data: bytes | None = None


class RpcParentDrafts:
    def __init__(self, session: AgentSession, navigation: RpcNavigation,
                 send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self.session, self.navigation, self.send = session, navigation, send
        self.flags = dict.fromkeys(CAPABILITIES, False)
        self.lease = secrets.token_hex(16)
        self.generation = 0
        self.closed = False
        self.used: set[str] = set()
        self.review_timers: dict[str, asyncio.TimerHandle] = {}
        self.reviews: dict[str, _Review] = {}
        self.task: asyncio.Task[None] | None = None
        self.cancel_event: threading.Event | None = None
        self.active_request = ''
        self._catalog: dict[str, str] = {}
        self._catalog_owner: dict[str, Any] = {}

    def _retire_review(self, key: str) -> None:
        self.reviews.pop(key, None)
        timer = self.review_timers.pop(key, None)
        if timer is not None:
            timer.cancel()

    def _clear_reviews(self) -> None:
        for key in tuple(self.reviews):
            self._retire_review(key)

    def _expire_review(self, key: str) -> None:
        self.review_timers.pop(key, None)
        review = self.reviews.get(key)
        if review is None:
            return
        deadline = review.deadline
        remaining = deadline - time.monotonic()
        if remaining > 0:
            self.review_timers[key] = asyncio.get_running_loop().call_later(remaining, self._expire_review, key)
        else:
            self._retire_review(key)

    def _schedule_review(self, key: str) -> None:
        self.review_timers[key] = asyncio.get_running_loop().call_later(30, self._expire_review, key)

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def envelope(self) -> dict[str, Any]:
        return {'session_id': self.session.session_id, 'session_revision': self.navigation.revision,
                'parent_draft_owner': self.lease, 'parent_draft_generation': self.generation}

    def negotiate(self, packet: dict[str, Any]) -> None:
        for key in CAPABILITIES:
            if key in packet and (type(packet[key]) is not int or packet[key] not in (0, 1)):
                raise ValueError('Parent draft capabilities require integer 0 or 1')
        if any(key in packet and self.flags[key] != (packet[key] == 1) for key in CAPABILITIES):
            self.generation += 1
            self._clear_reviews()
            self._catalog.clear()
            if self.cancel_event is not None:
                self.cancel_event.set()
            if self.busy and self.task is not None and not self.task.cancelling():
                self.task.cancel()
        for key in CAPABILITIES:
            if key in packet:
                self.flags[key] = packet[key] == 1

    def _owner(self, packet: dict[str, Any]) -> None:
        if self.closed or any(type(packet.get(key)) is not type(value) or packet.get(key) != value
                              for key, value in self.envelope().items()):
            raise ValueError('Parent draft connection/session/revision changed; no replay')
        if not self.flags['parent_saved_drafts']:
            raise ValueError('Negotiate parent_saved_drafts independently')

    @staticmethod
    def _composer(packet: dict[str, Any]) -> dict[str, Any]:
        value = packet.get('composer')
        if (not isinstance(value, dict) or set(value) != {'revision', 'sha256', 'empty'}
                or type(value['revision']) is not int or value['revision'] < 0
                or type(value['empty']) is not bool or not isinstance(value['sha256'], str)
                or re.fullmatch(r'[a-f0-9]{64}', value['sha256']) is None):
            raise ValueError('Explicit client composer revision/digest/empty state required')
        return dict(value)

    @staticmethod
    def _draft(packet: dict[str, Any]) -> ChildDraft:
        text, images = packet.get('text', ''), packet.get('images', [])
        if (not isinstance(text, str) or len(text.encode()) > 65536
                or not isinstance(images, list) or len(images) > 4):
            raise ValueError('Invalid bounded parent text/image snapshot')
        parts = []
        for row in images:
            if (not isinstance(row, dict) or set(row) != {'name', 'mime_type', 'data'}
                    or not isinstance(row['data'], str) or len(row['data']) > 14 * 1024 * 1024
                    or not isinstance(row['name'], str) or not isinstance(row['mime_type'], str)):
                raise ValueError('Parent snapshot requires actual bounded image bytes')
            part = attachment(base64.b64decode(row['data'], validate=True), row['name'])
            if part.mime_type != row['mime_type']:
                raise ValueError('Parent snapshot MIME mismatch')
            parts.append(part)
        draft = ChildDraft(text, 'read-only', {'inherit_context': False}, tuple(parts), True)
        if not text and not parts:
            raise ValueError('Empty parent snapshot cannot be saved')
        return draft

    def start(self, packet: dict[str, Any], *, blocked: bool) -> None:
        self._owner(packet)
        if set(packet) - {'type', 'request_id', 'action', *self.envelope(), 'composer', 'text', 'images',
                          'label', 'snapshot_id', 'snapshot_sha256', 'review_id', 'review', 'confirmed'}:
            raise ValueError('Unsupported parent draft fields; no file/path or policy authority')
        action, rid = packet.get('action'), packet.get('request_id')
        if (not isinstance(action, str) or action not in {
                'catalog', 'read', 'save_prepare', 'save_confirm', 'discard_prepare',
                           'discard_confirm', 'review_cancel'}
                or not isinstance(rid, str) or not 0 < len(rid.encode()) <= 128
                or rid in self.used or len(self.used) >= 256):
            raise ValueError('Invalid/reused parent draft action or request identity')
        if blocked or self.busy or self.session.agent.is_streaming or self.session.children.supervisor.active:
            raise ValueError('Parent draft controls require idle original session')
        if action.startswith('save') and not self.flags['parent_saved_save']:
            raise ValueError('Negotiate parent_saved_save independently')
        if action.startswith('discard') and not self.flags['parent_saved_discard']:
            raise ValueError('Negotiate parent_saved_discard independently')
        composer = self._composer(packet) if action != 'catalog' else {}
        if action == 'read' and not composer['empty']:
            raise ValueError('Client composer must be empty for recovery')
        # Private copy before scheduling: caller edits cannot change admitted bytes.
        from xdog.agent.mcp.schema import json_value
        snapshot = json.loads(json_value(packet, maximum=56 * 1024 * 1024))
        captured = self.envelope()
        self.used.add(rid)
        self.active_request = rid
        self.cancel_event = canceled = threading.Event()
        def alive() -> bool:
            return not canceled.is_set() and not self.closed and captured == self.envelope() and self.flags[
                'parent_saved_drafts']
        async def run() -> None:
            reply: dict[str, Any] = {'type': 'parent_draft_result', **captured,
                'request_id': rid, 'action': action, 'success': False}
            try:
                store = ChildDraftStore(self.session.session_manager._dir / 'saved-parent-drafts')
                now = time.monotonic()
                for expired, row in tuple(self.reviews.items()):
                    if row.deadline <= now:
                        self._retire_review(expired)
                if action == 'catalog':
                    rows = await settled_image_work(store.catalog)
                    if not alive():
                        raise asyncio.CancelledError
                    self._catalog = {row['id']: row['sha256'] for row in rows}
                    self._catalog_owner = captured
                    reply['catalog'] = rows
                elif action == 'save_prepare':
                    if len(self.reviews) >= 4:
                        raise ValueError('Parent draft review capacity exhausted')
                    def encode() -> tuple[bytes, dict[str, Any]]:
                        draft = self._draft(snapshot)
                        if draft.images and not self.flags['parent_saved_images']:
                            raise ValueError('Negotiate parent_saved_images independently')
                        receipt = draft_receipt(draft.text, draft.images)
                        digest = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode())
                        if composer['empty'] or composer['sha256'] != digest.hexdigest():
                            raise ValueError('Parent save content differs from client composer receipt')
                        label = snapshot.get('label', 'RPC parent draft')
                        data = encode_draft(draft, label=label, scope=captured['session_id'],
                                            action='parent', slot='rpc-explicit')
                        body = {'operation': 'save', 'composer': composer, 'content': draft.text,
                            'images': [{'name': part.name, 'mime_type': part.mime_type,
                                        'sha256': hashlib.sha256(part.data).hexdigest(), 'bytes': len(part.data)}
                                       for part in draft.images], 'label': label,
                            'snapshot_sha256': hashlib.sha256(data).hexdigest(),
                            'notice': 'PRIVATE PLAINTEXT snapshot only; no execution or overwrite'}
                        return data, body
                    data, body = await settled_image_work(encode)
                    if sum(len(row.data or b'') for row in self.reviews.values()) + len(data) > 56 * 1024 * 1024:
                        raise ValueError('Parent prepared snapshot byte budget exceeded')
                    if not alive():
                        raise asyncio.CancelledError
                    key = secrets.token_hex(16)
                    self.reviews[key] = _Review('save', time.monotonic() + 30, body, captured, data)
                    self._schedule_review(key)
                    reply.update(review_id=key, review=body)
                elif action in {'read', 'discard_prepare'}:
                    key, digest = snapshot.get('snapshot_id'), snapshot.get('snapshot_sha256')
                    if (not isinstance(key, str) or self._catalog.get(key) != digest
                            or self._catalog_owner != captured):
                        raise ValueError('Original parent catalog identity/hash required')
                    saved = await settled_image_work(lambda: store.load(key))
                    if saved.sha256 != digest or saved.source_action != 'parent':
                        raise ValueError('Parent snapshot changed or wrong source action')
                    if not alive():
                        raise asyncio.CancelledError
                    body = {'operation': 'discard' if action == 'discard_prepare' else 'recover',
                        'composer': composer, 'snapshot_id': saved.id, 'snapshot_sha256': saved.sha256,
                        'label': saved.label, 'content': saved.draft.text,
                        'images': [{'name': part.name, 'mime_type': part.mime_type,
                                    'sha256': hashlib.sha256(part.data).hexdigest(), 'bytes': len(part.data)}
                                   for part in saved.draft.images],
                        'notice': 'Editing data only; no model/history/permission change'}
                    if action == 'discard_prepare':
                        if len(self.reviews) >= 4:
                            raise ValueError('Parent review capacity exhausted')
                        key = secrets.token_hex(16)
                        self.reviews[key] = _Review('discard', time.monotonic() + 30, body, captured)
                        self._schedule_review(key)
                        reply.update(review_id=key, review=body)
                    else:
                        if saved.draft.images and not self.flags['parent_saved_images']:
                            raise ValueError('Negotiate parent_saved_images before binary recovery')
                        # Independent binary frames; never inline a large snapshot in a receipt.
                        async with asyncio.timeout(10):
                            await self.send({'type': 'parent_draft_begin', **captured, 'request_id': rid,
                                             'review': json.loads(json.dumps(body))})
                            for image_index, part in enumerate(saved.draft.images):
                                for index, offset in enumerate(range(0, len(part.data), 64 * 1024)):
                                    if not alive():
                                        raise asyncio.CancelledError
                                    await self.send({'type': 'parent_draft_chunk', **captured, 'request_id': rid,
                                        'image_index': image_index, 'index': index,
                                        'data': base64.b64encode(part.data[offset:offset + 64 * 1024]).decode()})
                            if not alive():
                                raise asyncio.CancelledError
                            await self.send({'type': 'parent_draft_end', **captured, 'request_id': rid})
                        reply['recovery'] = body
                else:
                    key = snapshot.get('review_id')
                    review = self.reviews.get(key) if isinstance(key, str) else None
                    if review is None or review.deadline <= time.monotonic() or review.owner != captured:
                        raise ValueError('Parent review unavailable/expired; no replay')
                    if action == 'review_cancel':
                        self._retire_review(key)
                    else:
                        expected = 'save' if action == 'save_confirm' else 'discard'
                        if (review.operation != expected
                                or json.dumps(snapshot.get('review'), sort_keys=True, allow_nan=False)
                                != json.dumps(review.body, sort_keys=True, allow_nan=False)
                                or snapshot.get('confirmed') is not True
                                or json.dumps(composer, sort_keys=True)
                                != json.dumps(review.body['composer'], sort_keys=True)):
                            raise ValueError('Exact unchanged composer/review and boolean confirmation required')
                        self._retire_review(key)  # Single-use before admission; no automatic retry.
                        if expected == 'save':
                            prepared_data = review.data
                            assert prepared_data is not None
                            saved = await settled_image_work(lambda: store.save(prepared_data,
                                alive=lambda: alive() and review.deadline > time.monotonic()))
                            reply.update(snapshot_id=saved.id, snapshot_sha256=saved.sha256)
                        else:
                            await settled_image_work(lambda: store.discard(review.body['snapshot_id'],
                                review.body['snapshot_sha256'],
                                alive=lambda: alive() and review.deadline > time.monotonic()))
                            self._catalog.pop(review.body['snapshot_id'], None)
                if not alive():
                    raise asyncio.CancelledError
                reply['success'] = True
            except asyncio.CancelledError:
                reply['canceled'] = True
            except Exception:
                reply['error'] = 'Parent draft request refused/failed; no replay'
            delivered = False
            try:
                if not self.closed:
                    async with asyncio.timeout(10):
                        await self.send(json.loads(json.dumps(reply)))
                    delivered = reply['success'] is True
            except (OSError, RuntimeError, ValueError, TimeoutError):
                pass
            finally:
                if not delivered and isinstance(reply.get('review_id'), str):
                    self._retire_review(reply['review_id'])
        self.task = asyncio.create_task(run())

    def cancel(self, packet: dict[str, Any]) -> None:
        self._owner(packet)
        if packet.get('request_id') != self.active_request:
            raise ValueError('Foreign parent draft cancellation')
        if self.cancel_event is not None:
            self.cancel_event.set()
        if self.busy and self.task is not None and not self.task.cancelling():
            self.task.cancel()

    async def aclose(self) -> None:
        self.closed = True
        self._clear_reviews()
        self._catalog.clear()
        if self.cancel_event is not None:
            self.cancel_event.set()
        if self.task is not None:
            if not self.task.done() and not self.task.cancelling():
                self.task.cancel()
            settlement = asyncio.gather(self.task, return_exceptions=True)
            cancelled = False
            while not settlement.done():
                try:
                    await asyncio.shield(settlement)
                except asyncio.CancelledError:
                    cancelled = True
            settlement.result()
            if cancelled:
                raise asyncio.CancelledError
