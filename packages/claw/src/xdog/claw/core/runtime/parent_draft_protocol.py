"""Independent connection-local parent snapshot catalog/binary reads, never adoption."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from xdog.agent.supervision_transfer import settled_image_work
from xdog.ai.types import ImageContent
from xdog.claw.channels.tui.image_upload import ImageUploads
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.tui.attachments import attachment
from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.parent_draft_receiver import composer_receipt


class ParentDraftConnection:
    def __init__(self, orchestrator: Orchestrator, root: Path,
                 write: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self.orchestrator, self.root, self.write = orchestrator, root, write
        self.supported = False
        self.images = False
        self.save = False
        self.discard = False
        self.upload_enabled = False
        self.uploads = ImageUploads()
        self.upload_owners: dict[str, tuple[tuple[str, str, str, int, int], float]] = {}
        self.upload_used: set[str] = set()
        self.chunk_used: set[str] = set()
        self.upload_jobs: set[str] = set()
        self.upload_timers: dict[str, asyncio.TimerHandle] = {}
        self.review_timers: dict[str, asyncio.TimerHandle] = {}
        self.reviews: dict[str, tuple[dict[str, Any], float, dict[str, Any], bytes | None]] = {}
        self.owner = secrets.token_hex(16)
        self.generation = 0
        self.closed = False
        self.jobs: dict[str, asyncio.Task[None]] = {}
        self.used: set[str] = set()
        self.job_views: dict[str, tuple[str, str, str, int]] = {}
        self.revoked_views: set[tuple[str, str, str, int]] = set()
        self.view_cleanups: set[asyncio.Task[None]] = set()
        self.catalogs: dict[tuple[str, str, str, int], dict[str, str]] = {}

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
        deadline = review[1]
        remaining = deadline - time.monotonic()
        if remaining > 0:
            self.review_timers[key] = asyncio.get_running_loop().call_later(remaining, self._expire_review, key)
        else:
            self._retire_review(key)

    def _schedule_review(self, key: str) -> None:
        self.review_timers[key] = asyncio.get_running_loop().call_later(30, self._expire_review, key)

    def negotiate(self, packet: dict[str, Any]) -> None:
        for key in ('parent_saved_drafts', 'parent_saved_images', 'parent_saved_save', 'parent_saved_discard',
                    'parent_saved_uploads'):
            if key in packet and (type(packet[key]) is not int or packet[key] not in (0, 1)):
                raise ValueError('Parent draft capabilities require exact integer0/1')
        supported = packet.get('parent_saved_drafts', int(self.supported)) == 1
        images = packet.get('parent_saved_images', int(self.images)) == 1
        save = packet.get('parent_saved_save', int(self.save)) == 1
        discard = packet.get('parent_saved_discard', int(self.discard)) == 1
        upload = packet.get('parent_saved_uploads', int(self.upload_enabled)) == 1
        if (supported, images, save, discard, upload) != (
                self.supported, self.images, self.save, self.discard, self.upload_enabled):
            self.generation += 1
            self.catalogs.clear()
            self._clear_reviews()
            self.uploads = ImageUploads()
            self.upload_owners.clear()
            for timer in self.upload_timers.values():
                timer.cancel()
            self.upload_timers.clear()
            for task in self.jobs.values():
                if not task.cancelling():
                    task.cancel()
        self.supported, self.images, self.save, self.discard = supported, images, save, discard
        self.upload_enabled = upload

    def capabilities(self) -> dict[str, Any]:
        return {'parent_saved_drafts': int(self.supported), 'parent_saved_images': int(self.images),
                'parent_saved_uploads': int(self.upload_enabled),
                'parent_saved_save': int(self.save), 'parent_saved_discard': int(self.discard),
                'parent_draft_owner': self.owner, 'parent_draft_generation': self.generation}

    async def request(self, packet: dict[str, Any]) -> None:
        if packet.get('action') == 'view_cancel':
            await self.cancel_view(packet)
            return
        if packet.get('action') in {'upload_chunk', 'upload_discard'}:
            await self._upload(packet)
            return
        rid, group, session, view = (packet.get(key) for key in ('request_id', 'group_id', 'session_id', 'view'))
        action = packet.get('action')
        try:
            if (self.closed or not self.supported or any(not isinstance(value, str) or not value
                    or len(value.encode()) > 128 for value in (rid, group, session, view))
                    or not isinstance(view, str) or re.fullmatch(r'[a-f0-9]{32}', view) is None
                    or packet.get('parent_draft_owner') != self.owner
                    or type(packet.get('parent_draft_generation')) is not int
                    or packet['parent_draft_generation'] != self.generation
                    or not isinstance(action, str) or action not in {'catalog', 'read', 'save_prepare',
                        'save_confirm', 'discard_prepare', 'discard_confirm', 'review_cancel'}
                    or rid in self.used or len(self.used) >= 256
                    or len(self.jobs) >= 4):
                raise ValueError('Invalid parent draft connection/action')
            assert isinstance(rid, str) and isinstance(group, str)
            assert isinstance(session, str) and isinstance(view, str)
            if set(packet) - {'type', 'request_id', 'group_id', 'session_id', 'view',
                               'parent_draft_owner', 'parent_draft_generation', 'action', 'composer',
                               'snapshot_id', 'snapshot_sha256', 'text', 'images', 'label',
                               'review_id', 'review', 'confirmed', 'image_refs', 'image_names'}:
                raise ValueError('Unsupported parent draft fields')
            packet = json.loads(json.dumps(packet, allow_nan=False))
            if (group, session, view, self.generation) in self.revoked_views:
                raise ValueError('Parent draft view revoked')
            revision = self.orchestrator.source_lease(group)
            if revision[0] != session or revision[1] < 0:
                raise ValueError('Parent draft session changed')
            composer = packet.get('composer', {})
            if action != 'catalog':
                if (not isinstance(composer, dict) or set(composer) != {'revision', 'empty', 'sha256'}
                        or type(composer['revision']) is not int or composer['revision'] < 0
                        or type(composer['empty']) is not bool or not isinstance(composer['sha256'], str)
                        or re.fullmatch(r'[a-f0-9]{64}', composer['sha256']) is None):
                    raise ValueError('Exact client composer receipt required')
                if action == 'read' and composer != composer_receipt(ChildDraft(), composer['revision']):
                    raise ValueError('Recovery requires exact empty client composer receipt')
            if 'image_refs' in packet and self.upload_jobs:
                raise ValueError('Parent upload worker still owns its buffers')
            if action.startswith('save') and not self.save:
                raise ValueError('Parent save independently disabled')
            if action.startswith('discard') and not self.discard:
                raise ValueError('Parent discard independently disabled')
            if not self.orchestrator.try_reserve_group(group):
                raise ValueError('Parent draft read requires idle group')
        except (ValueError, TypeError, KeyError):
            await self.write({'type': 'parent_draft_result', 'request_id': rid, 'action': action,
                              'success': False, 'error': 'Parent draft owner/capability/receipt refused'})
            return
        assert isinstance(rid, str) and isinstance(group, str)
        assert isinstance(session, str) and isinstance(view, str)
        self.used.add(rid)
        generation = self.generation
        catalog_key = group, session, view, revision[1]
        scope = {'request_id': rid, 'group_id': group, 'session_id': session, 'view': view,
                 'session_revision': str(revision[1]), 'parent_draft_owner': self.owner,
                 'parent_draft_generation': generation}
        def alive() -> bool:
            runtime = self.orchestrator._runtimes.get(group)
            return (not self.closed and self.supported and self.generation == generation and runtime is not None
                    and (group, session, view, generation) not in self.revoked_views
                    and (runtime.get_new_session_id() or '', runtime._source_revision) == revision
                    and runtime._active_session is not None
                    and not runtime._active_session.children.supervisor.active
                    and not runtime._active_session._compacting
                    and not runtime._active_session.extension_dialogs.has_pending)
        reserved = True
        def release() -> None:
            nonlocal reserved
            if reserved:
                reserved = False
                self.orchestrator.release_group_reservation(group)
        async def run() -> None:
            reply: dict[str, Any] = {'type': 'parent_draft_result', **scope, 'action': action, 'success': False}
            try:
                if not alive():
                    raise ValueError('Parent draft owner changed')
                store = ChildDraftStore(self.root)
                for expired, row in tuple(self.reviews.items()):
                    if row[1] <= time.monotonic():
                        self._retire_review(expired)
                if action == 'catalog':
                    rows = await settled_image_work(store.catalog)
                    if not alive():
                        raise ValueError('Parent catalog owner changed')
                    if len(self.catalogs) >= 8 and catalog_key not in self.catalogs:
                        raise ValueError('Parent catalog view capacity exceeded')
                    self.catalogs[catalog_key] = {row['id']: row['sha256'] for row in rows}
                    reply['catalog'] = rows
                elif action == 'save_prepare':
                    if len(self.reviews) >= 4:
                        raise ValueError('Parent review capacity exhausted')
                    references = packet.get('image_refs', [])
                    uploaded: tuple[ImageContent, ...] = ()
                    if 'image_refs' in packet:
                        if not self.upload_enabled or packet.get('images'):
                            raise ValueError('Use independently uploaded references or inline images, not both')
                        expected_upload_owner = group, session, view, revision[1], generation
                        if (not isinstance(references, list) or any(not isinstance(key, str)
                                or key not in self.upload_owners or self.upload_owners[key][0] != expected_upload_owner
                                or self.upload_owners[key][1] <= time.monotonic() for key in references)):
                            raise ValueError('Parent upload reference owner/deadline changed')
                        uploaded = self.uploads.resolve(references)
                        self.uploads.discard_refs(references)
                        for key in references:
                            timer = self.upload_timers.pop(key, None)
                            if timer is not None:
                                timer.cancel()
                            self.upload_owners.pop(key, None)  # Consumed once before preparation; no retry.
                    def prepare() -> tuple[bytes, dict[str, Any]]:
                        text, sources = packet.get('text', ''), packet.get('images', [])
                        if (not isinstance(text, str) or len(text.encode()) > 65536
                                or not isinstance(sources, list) or len(sources) > 4
                                or sources and not self.images):
                            raise ValueError('Invalid parent snapshot text/images')
                        images = []
                        names = packet.get('image_names', [])
                        if uploaded:
                            if (not isinstance(names, list) or len(names) != len(uploaded)
                                    or any(not isinstance(name, str) or len(name.encode()) > 1024 for name in names)):
                                raise ValueError('Exact uploaded image names required')
                            for item, name in zip(uploaded, names, strict=True):
                                image = attachment(base64.b64decode(item.data, validate=True), name)
                                if image.mime_type != item.mime_type:
                                    raise ValueError('Uploaded parent image MIME changed')
                                images.append(image)
                        for row in sources:
                            if (not isinstance(row, dict) or set(row) != {'name', 'mime_type', 'data'}
                                    or not isinstance(row['name'], str) or not isinstance(row['data'], str)
                                    or len(row['data']) > 14 * 1024 * 1024):
                                raise ValueError('Parent save requires bounded actual bytes')
                            image = attachment(base64.b64decode(row['data'], validate=True), row['name'])
                            if image.mime_type != row['mime_type']:
                                raise ValueError('Parent saved image MIME mismatch')
                            images.append(image)
                        draft = ChildDraft(text, images=tuple(images), uncertain=True)
                        if not text and not images or composer != composer_receipt(draft, composer['revision']):
                            raise ValueError('Parent save composer digest/content mismatch')
                        label = packet.get('label', 'Claw parent snapshot')
                        data = encode_draft(draft, label=label, scope=session, action='parent', slot='socket-explicit')
                        body = {'operation': 'save', 'composer': composer, 'label': label, 'content': text,
                            'images': [{'name': image.name, 'mime_type': image.mime_type,
                                        'bytes': len(image.data), 'sha256': hashlib.sha256(image.data).hexdigest()}
                                       for image in images], 'snapshot_sha256': hashlib.sha256(data).hexdigest(),
                            'notice': 'PRIVATE PLAINTEXT snapshot only; no execution/overwrite'}
                        return data, body
                    data, review = await settled_image_work(prepare)
                    if sum(len(row[3] or b'') for row in self.reviews.values()) + len(data) > 56 * 1024 * 1024:
                        raise ValueError('Parent review byte capacity exhausted')
                    if not alive():
                        raise ValueError('Parent prepare owner changed')
                    key = secrets.token_hex(16)
                    self.reviews[key] = dict(scope), time.monotonic() + 30, review, data
                    self._schedule_review(key)
                    reply.update(review_id=key, review=json.loads(json.dumps(review)))
                elif action in {'save_confirm', 'discard_confirm', 'review_cancel'}:
                    review_key = packet.get('review_id')
                    stored = self.reviews.get(review_key) if isinstance(review_key, str) else None
                    if (stored is None or stored[1] <= time.monotonic()
                            or any(stored[0][field] != scope[field] for field in scope if field != 'request_id')):
                        raise ValueError('Parent review expired/foreign/view changed')
                    if action == 'review_cancel':
                        assert isinstance(review_key, str)
                        self._retire_review(review_key)
                    else:
                        operation = 'save' if action == 'save_confirm' else 'discard'
                        if (stored[2]['operation'] != operation or packet.get('confirmed') is not True
                                or json.dumps(packet.get('review'), sort_keys=True)
                                != json.dumps(stored[2], sort_keys=True)
                                or json.dumps(composer, sort_keys=True)
                                != json.dumps(stored[2]['composer'], sort_keys=True)):
                            raise ValueError('Exact unchanged review/composer and boolean confirmation required')
                        assert isinstance(review_key, str)
                        self._retire_review(review_key)  # Single-use before mutation, no uncertain replay.
                        if operation == 'save':
                            prepared_data = stored[3]
                            assert prepared_data is not None
                            saved = await settled_image_work(lambda: store.save(prepared_data,
                                alive=lambda: alive() and stored[1] > time.monotonic()))
                            reply.update(snapshot_id=saved.id, snapshot_sha256=saved.sha256)
                        else:
                            await settled_image_work(lambda: store.discard(stored[2]['snapshot_id'],
                                stored[2]['snapshot_sha256'], alive=lambda: alive() and stored[1] > time.monotonic()))
                            self.catalogs.get(catalog_key, {}).pop(stored[2]['snapshot_id'], None)
                else:
                    snapshot_key, digest = packet.get('snapshot_id'), packet.get('snapshot_sha256')
                    if (not isinstance(snapshot_key, str)
                            or self.catalogs.get(catalog_key, {}).get(snapshot_key) != digest):
                        raise ValueError('Original parent catalog identity/hash required')
                    saved = await settled_image_work(lambda: store.load(snapshot_key))
                    if saved.sha256 != digest or saved.source_action != 'parent' or not alive():
                        raise ValueError('Parent snapshot changed')
                    if saved.draft.images and not self.images:
                        raise ValueError('Binary parent recovery requires separate image negotiation')
                    review = {'operation': 'discard' if action == 'discard_prepare' else 'recover',
                        'composer': dict(composer), 'snapshot_id': snapshot_key,
                        'snapshot_sha256': digest, 'label': saved.label, 'content': saved.draft.text,
                        'images': [{'name': image.name, 'mime_type': image.mime_type,
                                    'bytes': len(image.data), 'sha256': hashlib.sha256(image.data).hexdigest()}
                                   for image in saved.draft.images],
                        'notice': 'Editing data only; no model/history/permission change'}
                    if action == 'discard_prepare':
                        if len(self.reviews) >= 4:
                            raise ValueError('Parent review capacity exhausted')
                        review_id = secrets.token_hex(16)
                        self.reviews[review_id] = dict(scope), time.monotonic() + 30, review, None
                        self._schedule_review(review_id)
                        reply.update(review_id=review_id, review=json.loads(json.dumps(review)))
                    else:
                        async def frame(kind: str, **fields: Any) -> None:
                            if not alive():
                                raise ValueError('Parent recovery view revoked')
                            await self.write({'type': 'parent_draft_' + kind, **scope, **fields})
                        async with asyncio.timeout(10):
                            await frame('begin', review=review)
                            for image_index, image in enumerate(saved.draft.images):
                                for index, offset in enumerate(range(0, len(image.data), 65536)):
                                    await frame('chunk', image_index=image_index, index=index,
                                        data=base64.b64encode(image.data[offset:offset + 65536]).decode())
                            await frame('end')
                        reply['recovery'] = review
                if not alive():
                    raise ValueError('Parent result owner changed')
                reply['success'] = True
            except asyncio.CancelledError:
                reply['canceled'] = True
            except (ValueError, RuntimeError, OSError, TimeoutError):
                reply['error'] = 'Parent draft read refused/failed; no adoption or replay'
            finally:
                release()
            delivered = False
            try:
                if not self.closed:
                    async with asyncio.timeout(10):
                        await self.write(json.loads(json.dumps(reply)))
                    delivered = reply['success'] is True
            except (OSError, RuntimeError, ValueError, TimeoutError):
                pass
            finally:
                if not delivered and isinstance(reply.get('review_id'), str):
                    self._retire_review(reply['review_id'])
        task = asyncio.create_task(run())
        self.jobs[rid] = task
        self.job_views[rid] = group, session, view, self.generation
        def done(completed: asyncio.Task[None]) -> None:
            release()  # Cancel-before-first-step still owns its reservation.
            self.job_views.pop(rid, None)
            if self.jobs.get(rid) is completed:
                self.jobs.pop(rid, None)
            if not completed.cancelled():
                completed.exception()
        task.add_done_callback(done)

    def _expire_upload(self, key: str, uploads: ImageUploads) -> None:
        if self.uploads is not uploads or key not in self.upload_owners:
            self.upload_timers.pop(key, None)
            return
        if self.upload_jobs:
            self.upload_timers[key] = asyncio.get_running_loop().call_later(.1, self._expire_upload, key, uploads)
            return  # Actual validation worker retains its buffer ownership.
        uploads.discard(key)
        self.upload_owners.pop(key, None)
        self.upload_timers.pop(key, None)

    async def _upload(self, packet: dict[str, Any]) -> None:
        rid = packet.get('request_id')
        action = packet.get('action')
        try:
            fields = {'type', 'action', 'request_id', 'group_id', 'session_id', 'view',
                      'parent_draft_owner', 'parent_draft_generation', 'composer',
                      'id', 'index', 'mime_type', 'data', 'final', 'sha256'}
            if (set(packet) - fields or self.closed or not self.supported or not self.images or not self.save
                    or not self.upload_enabled or packet.get('parent_draft_owner') != self.owner
                    or type(packet.get('parent_draft_generation')) is not int
                    or packet['parent_draft_generation'] != self.generation
                    or not isinstance(rid, str) or not 0 < len(rid.encode()) <= 128
                    or rid in self.chunk_used or len(self.chunk_used) >= 4096 or len(self.jobs) >= 4
                    or self.upload_jobs):
                raise ValueError('Parent upload capability/request owner unavailable')
            group, session, view = (packet.get(key) for key in ('group_id', 'session_id', 'view'))
            if (not isinstance(group, str) or not isinstance(session, str) or not isinstance(view, str)
                    or re.fullmatch(r'[a-f0-9]{32}', view) is None):
                raise ValueError('Invalid parent upload view')
            if (group, session, view, self.generation) in self.revoked_views:
                raise ValueError('Parent draft view revoked')
            revision = self.orchestrator.source_lease(group)
            if revision[0] != session or revision[1] < 0:
                raise ValueError('Parent upload session changed')
            key = packet.get('id')
            if not isinstance(key, str) or not 0 < len(key) <= 96:
                raise ValueError('Invalid parent upload identity')
            for old, old_binding in tuple(self.upload_owners.items()):
                if old_binding[1] <= time.monotonic():
                    self.uploads.discard(old)
                    self.upload_owners.pop(old, None)
            binding = group, session, view, revision[1], self.generation
            previous = self.upload_owners.get(key)
            if previous is None:
                if (action != 'upload_chunk' or packet.get('index') != 0 or key in self.upload_used
                        or len(self.upload_used) >= 256):
                    raise ValueError('Parent upload unavailable/reused')
            elif previous[0] != binding:
                raise ValueError('Foreign parent upload')
            snapshot = json.loads(json.dumps(packet, allow_nan=False))
            if not self.orchestrator.try_reserve_group(group):
                raise ValueError('Parent upload requires idle group')
        except (ValueError, TypeError, KeyError):
            await self.write({'type': 'parent_draft_result', 'request_id': rid, 'action': action,
                              'success': False, 'error': 'Parent upload refused; no replay'})
            return
        uploads = self.uploads
        self.upload_jobs.add(rid)
        self.chunk_used.add(rid)
        self.upload_used.add(key)
        self.upload_owners[key] = binding, time.monotonic() + 120
        timer = self.upload_timers.pop(key, None)
        if timer is not None:
            timer.cancel()
        self.upload_timers[key] = asyncio.get_running_loop().call_later(120, self._expire_upload, key, uploads)
        scope = {'group_id': group, 'session_id': session, 'view': view,
                 'parent_draft_owner': self.owner, 'parent_draft_generation': binding[-1]}
        reserved = True
        def release() -> None:
            nonlocal reserved
            if reserved:
                reserved = False
                self.orchestrator.release_group_reservation(group)
        async def work() -> None:
            reply: dict[str, Any] = {'type': 'parent_draft_result', **scope,
                'request_id': rid, 'action': action, 'success': False, 'upload_id': key}
            try:
                runtime = self.orchestrator._runtimes.get(group)
                if (runtime is None or runtime._active_session is None
                        or runtime._active_session.children.supervisor.active):
                    raise ValueError('Parent upload child owner unavailable')
                if action == 'upload_discard':
                    uploads.discard(key)
                    self.upload_owners.pop(key, None)
                else:
                    status = await settled_image_work(lambda: uploads.chunk(snapshot))
                    reply['upload_status'] = status
                if (self.closed or (group, session, view, binding[-1]) in self.revoked_views
                        or self.uploads is not uploads or self.generation != binding[-1]
                        or (runtime.get_new_session_id() or '', runtime._source_revision) != revision):
                    raise ValueError('Parent upload owner changed')
                reply['success'] = True
            except (asyncio.CancelledError, ValueError, RuntimeError, OSError):
                uploads.discard(key)  # settled_image_work already awaited any actual thread.
                if self.upload_owners.get(key, (None,))[0] == binding:
                    self.upload_owners.pop(key, None)
                reply['error'] = 'Parent upload failed/revoked; no replay'
            finally:
                release()
            delivered = False
            try:
                if not self.closed:
                    async with asyncio.timeout(10):
                        await self.write(reply)
                    delivered = reply['success'] is True
            except (OSError, RuntimeError, ValueError, TimeoutError):
                pass  # No retry after uncertain upload acknowledgment.
            finally:
                if not delivered:
                    uploads.discard(key)
                    if self.upload_owners.get(key, (None,))[0] == binding:
                        self.upload_owners.pop(key, None)
                    timer = self.upload_timers.pop(key, None)
                    if timer is not None:
                        timer.cancel()

        task = asyncio.create_task(work())
        self.jobs[rid] = task
        self.job_views[rid] = group, session, view, self.generation
        def done(completed: asyncio.Task[None]) -> None:
            release()
            self.jobs.pop(rid, None)
            self.upload_jobs.discard(rid)
            self.job_views.pop(rid, None)
            if key not in self.upload_owners:
                timer = self.upload_timers.pop(key, None)
                if timer is not None:
                    timer.cancel()
            if completed.cancelled():
                uploads.discard(key)
                if self.upload_owners.get(key, (None,))[0] == binding:
                    self.upload_owners.pop(key, None)
            else:
                completed.exception()
        task.add_done_callback(done)

    async def cancel_view(self, packet: dict[str, Any]) -> None:
        group, session, view = (packet.get(key) for key in ('group_id', 'session_id', 'view'))
        generation = packet.get('parent_draft_generation')
        if (self.closed or packet.get('parent_draft_owner') != self.owner
                or type(generation) is not int or generation != self.generation
                or any(not isinstance(value, str) or not 0 < len(value.encode()) <= 128
                       for value in (group, session, view))
                or not isinstance(view, str) or re.fullmatch(r'[a-f0-9]{32}', view) is None):
            await self.write({'type': 'parent_draft_result', 'action': 'view_cancel',
                'request_id': packet.get('request_id'), 'success': False, 'error': 'Foreign parent view cancellation'})
            return
        assert isinstance(group, str) and isinstance(session, str) and isinstance(view, str)
        binding = group, session, view, generation
        if binding in self.revoked_views:
            return  # Idempotent revoke, never restart cleanup.
        if len(self.revoked_views) >= 256:
            raise ValueError('Parent view revocation capacity exhausted')
        self.revoked_views.add(binding)  # Revoke admission/delivery synchronously in socket reader.
        jobs = tuple(task for rid, task in self.jobs.items() if self.job_views.get(rid) == binding)
        for task in jobs:
            if not task.cancelling():
                task.cancel()
        async def settle() -> None:
            await asyncio.gather(*jobs, return_exceptions=True)
            for key, review in tuple(self.reviews.items()):
                scope = review[0]
                if (scope['group_id'], scope['session_id'], scope['view'], scope['parent_draft_generation']) == binding:
                    self._retire_review(key)
            for catalog_key in tuple(self.catalogs):
                if catalog_key[:3] == binding[:3]:
                    self.catalogs.pop(catalog_key, None)
            # Shared assembly buffers are only mutated after all validation workers
            # settle, including a different view's worker. Never cancel that sibling.
            while self.upload_jobs:
                await asyncio.sleep(.01)
            for key, owner in tuple(self.upload_owners.items()):
                identity = owner[0]
                if (*identity[:3], identity[-1]) == binding:
                    self.uploads.discard(key)
                    self.upload_owners.pop(key, None)
                    timer = self.upload_timers.pop(key, None)
                    if timer is not None:
                        timer.cancel()
        cleanup = asyncio.create_task(settle())
        self.view_cleanups.add(cleanup)
        def done(completed: asyncio.Task[None]) -> None:
            self.view_cleanups.discard(completed)
            if not completed.cancelled():
                completed.exception()
        cleanup.add_done_callback(done)

    async def close(self) -> None:
        self.closed = True
        self.supported = False
        self.catalogs.clear()
        self._clear_reviews()
        for task in self.jobs.values():
            if not task.cancelling():
                task.cancel()
        settlement = asyncio.gather(*tuple(self.jobs.values()), *tuple(self.view_cleanups), return_exceptions=True)
        cancelled = False
        while not settlement.done():
            try:
                await asyncio.shield(settlement)
            except asyncio.CancelledError:
                cancelled = True
        settlement.result()
        self.uploads.clear()
        self.upload_owners.clear()
        for timer in self.upload_timers.values():
            timer.cancel()
        self.upload_timers.clear()
        if cancelled:
            raise asyncio.CancelledError
