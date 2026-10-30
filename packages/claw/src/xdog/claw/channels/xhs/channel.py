"""XHS comments and explicit publishing on the existing Claw input/output lanes."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from xdog.agent.async_work import run_owned
from xdog.claw.channels.accounts import ChannelAccount, load_account
from xdog.claw.channels.base import Channel
from xdog.claw.core.identity import MessageSource
from xdog.claw.core.identity.models import label
from xdog.claw.core.outputs.delivery import DeliveryNotSent
from xdog.claw.core.types import UserInput

from .api import XhsApi
from .state import ScanState


class XhsChannel(Channel):
    def __init__(self, data_dir: Path, group_id: str, api: XhsApi, *, poll_seconds: float = 60,
                 replies_enabled: bool = False, publish_enabled: bool = False) -> None:
        if type(poll_seconds) not in {int, float} or not 30 <= poll_seconds <= 86400:
            raise ValueError('XHS polling must be between 30 seconds and one day')
        self.data_dir, self.group_id, self.api = data_dir, group_id, api
        self.poll_seconds = poll_seconds
        self.replies_enabled, self.publish_enabled = replies_enabled, publish_enabled
        self.account: ChannelAccount | None = None
        self._state: ScanState | None = None
        self._task: asyncio.Task[None] | None = None
        self._connected = False
        self.status = 'disconnected'
        self.scan_gap = False

    @property
    def name(self) -> str:
        return 'xhs'

    @property
    def reply_mode(self) -> str:
        return 'final' if self.replies_enabled else 'none'

    @property
    def input_roles(self) -> frozenset[str]:
        return frozenset({'external_user'})  # Public comments are never a management ingress.

    def accepts_group(self, group_id: str) -> bool:
        return group_id == self.group_id

    async def connect(self) -> None:
        if self._connected:
            return
        self.account = await run_owned(load_account, self.data_dir, self.name, self.group_id)
        if self.account is None:
            raise ValueError('XHS group needs explicit channel login before it can connect')
        identity = [self.group_id, self.account.account_id, self.account.revision]
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        self._state = await run_owned(ScanState, self.data_dir / 'xhs' / (key + '.sqlite3'))
        self._connected = True
        self.status = 'starting'
        # Browser recovery is independent of gateway readiness; no model starts here.
        self._task = asyncio.create_task(self._poll_loop(), name='claw-xhs-poll')

    async def disconnect(self) -> None:
        self._connected = False
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self.api.close()
        self.status = 'disconnected'

    async def _verified(self) -> ChannelAccount:
        account = self.account
        if not self._connected or account is None:
            raise DeliveryNotSent('XHS channel is not connected')
        saved = await run_owned(load_account, self.data_dir, self.name, self.group_id)
        if saved != account:
            raise DeliveryNotSent('XHS account binding changed; reconnect explicitly')
        try:
            current, _ = await self.api.identity()
        except Exception as exc:
            raise DeliveryNotSent('XHS login identity could not be verified') from exc
        if current != account.account_id:
            raise DeliveryNotSent('XHS backend account differs from the original login binding')
        return account

    def _comment(self, raw: dict[str, Any], account: ChannelAccount) -> UserInput | None:
        sender = raw.get('from')
        if not isinstance(sender, dict) or not raw.get('comment_id') or not raw.get('feed_id'):
            return None  # An @ without an exact comment target cannot be auto-replied.
        author = label(sender.get('user_id'), maximum=256)
        if author == account.account_id:
            return None
        comment, feed = label(raw['comment_id'], maximum=256), label(raw['feed_id'], maximum=256)
        content = label(raw.get('comment_text'), maximum=64 * 1024)
        key = json.dumps([account.account_id, comment]).encode()
        return UserInput(group_id=self.group_id, sender=author, content=content, channel=self.name,
            metadata={'account_id': account.account_id, 'account_revision': account.revision,
                      'comment_id': comment, 'feed_id': feed, 'author_id': author,
                      'channel_message_id': 'xhs-' + hashlib.sha256(key).hexdigest()})

    async def read_post(self, original: UserInput) -> dict[str, Any]:
        """Explicit read, never called by polling or used to change the reply target."""
        if not self.accepts_saved_reply(original):
            raise ValueError('Original XHS account binding is unavailable')
        try:
            account = await self._verified()
            raw_items = await self.api.notifications()
            candidates = []
            for raw in raw_items:
                try:
                    comment = self._comment(raw, account)
                except ValueError:
                    continue
                if (comment is not None and comment.sender == original.sender
                        and comment.metadata['comment_id'] == original.metadata.get('comment_id')
                        and comment.metadata['feed_id'] == original.metadata.get('feed_id')):
                    candidates.append(raw)
            if len(candidates) != 1:
                raise ValueError('Original XHS comment/post could not be verified')
            token = label(candidates[0].get('feed_xsec_token'), maximum=4096)
            result = await self.api.post(original.metadata['feed_id'], token)
            await self._verified()
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Never expose backend URLs, cookies, tokens or response bodies to a public model.
            raise ValueError('Original XHS post is unavailable; no post content was obtained') from exc

    async def poll_once(self) -> None:
        account = await self._verified()
        state = self._state
        if state is None:
            raise ValueError('XHS scan state is unavailable')
        raw_items = await self.api.notifications()
        messages = []
        malformed = False
        for raw in raw_items:
            try:
                message = self._comment(raw, account)
            except ValueError:
                malformed = True
                continue  # Isolate a malformed event without hiding the scan gap.
            if message is not None:
                messages.append(message)
        baseline, seen = await run_owned(state.snapshot, account.account_id,
                                          tuple(str(m.metadata['comment_id']) for m in messages))
        items = [(str(m.metadata['comment_id']), self.reply_context(m) | {'content': m.content}) for m in messages]
        if not baseline:
            await run_owned(lambda: state.accept(account.account_id, items, baseline=True))
            self.scan_gap = await run_owned(lambda: state.gap(
                account.account_id, found=malformed or len(raw_items) >= 200))
            self.status = 'watching_with_gap' if self.scan_gap else 'watching'
            # Initial history is never automatically answered.
            return
        # No cursor/complete flag exists upstream. An overlap is useful, not proof
        # of completeness; missing anchors/limit saturation are explicitly visible.
        found_gap = (malformed or bool(messages and not any(comment in seen for comment, _ in items))
                     or len(raw_items) >= 200)
        self.scan_gap = await run_owned(lambda: state.gap(account.account_id, found=found_gap))
        for message, (comment, payload) in reversed(list(zip(messages, items, strict=True))):
            previous = seen.get(comment)
            if previous is not None:
                if previous != json.dumps(payload, sort_keys=True, ensure_ascii=False):
                    self.scan_gap = await run_owned(lambda: state.gap(account.account_id, found=True))
                continue
            if self._on_accept is None:
                raise ValueError('XHS durable input callback is unavailable')
            await self._on_accept(message)  # Only Core Inbox commit permits connector advancement.
            await run_owned(lambda: state.accept(account.account_id, [(comment, payload)]))
        self.status = 'watching_with_gap' if self.scan_gap else 'watching'

    async def _poll_loop(self) -> None:
        while self._connected:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status = 'unavailable'  # Do not log account tokens or arbitrary backend response bodies.
            await asyncio.sleep(self.poll_seconds)

    def input_source(self, original: UserInput) -> MessageSource:
        if not self.accepts_saved_reply(original):
            raise ValueError('XHS input account/group binding is not current')
        return MessageSource(self.name, original.metadata['account_id'], original.sender,
            original.metadata['comment_id'], original.metadata['comment_id'],
            resource_id=original.metadata['feed_id'])

    def reply_context(self, original: UserInput) -> dict[str, object]:
        return {key: original.metadata[key] for key in (
            'account_id', 'account_revision', 'comment_id', 'feed_id', 'author_id',
            'channel_message_id', 'publication') if key in original.metadata}

    def accepts_saved_reply(self, original: UserInput) -> bool:
        account = self.account
        return bool(self._connected and account and original.channel == self.name
                    and original.group_id == self.group_id
                    and original.metadata.get('account_id') == account.account_id
                    and original.metadata.get('account_revision') == account.revision)

    def account_profile(self, group_id: str) -> dict[str, str] | None:
        return (self.account.public() | {'reply_mode': self.reply_mode, 'reply_max_chars': '1000'}
                if self.accepts_group(group_id) and self.account is not None else None)

    def bound_publication(self, group_id: str) -> UserInput | None:
        if not self.publish_enabled or not self.accepts_group(group_id) or self.account is None or not self._connected:
            return None
        return UserInput(group_id=group_id, channel=self.name, sender=self.account.account_id,
                         metadata={'account_id': self.account.account_id, 'account_revision': self.account.revision,
                                   'publication': True})

    async def prepare_publication(self, original: UserInput, payload: dict[str, Any], workspace: Path) -> str:
        if not self.publish_enabled or not self.accepts_saved_reply(original):
            raise ValueError('XHS publication binding is unavailable')
        if set(payload) != {'title', 'text', 'images'}:
            raise ValueError('XHS publish requires title, text, images')
        title = label(payload['title'], maximum=256)
        content = label(payload['text'], maximum=16 * 1024)
        if len(title) > 20 or len(content) > 1000:
            raise ValueError('XHS title/body exceed this adapter limit (20/1000 characters)')
        images = payload['images']
        if not isinstance(images, list) or not 1 <= len(images) <= 9:
            raise ValueError('XHS publishing requires one to nine workspace image files')
        from .media import freeze_images
        frozen = await run_owned(freeze_images, images, workspace, self.data_dir / 'xhs' / 'publications')
        return json.dumps({'title': title, 'content': content, 'images': frozen}, ensure_ascii=False)

    async def send_message(self, group_id: str, text: str) -> None:
        raise DeliveryNotSent('XHS needs a frozen comment target or explicit publication')

    async def send_reply(self, original: UserInput, text: str) -> None:
        await self.send_output(original, '', text)

    async def send_output(self, original: UserInput, output_id: str, text: str) -> None:
        if not self.accepts_saved_reply(original):
            raise DeliveryNotSent('XHS original destination/account changed')
        if original.metadata.get('publication') is True:
            if not self.publish_enabled:
                raise DeliveryNotSent('XHS publishing is disabled')
            payload = json.loads(text)
            from .media import verified_paths
            try:
                paths = await run_owned(verified_paths, payload['images'], self.data_dir / 'xhs' / 'publications')
            except Exception as exc:
                raise DeliveryNotSent('Frozen publication resources are unavailable or changed') from exc
            await self._verified()
            await self.api.request('POST', 'publish', json={**payload, 'images': paths})
        else:
            if len(text) > 1000:
                raise DeliveryNotSent('XHS reply exceeds 1000 characters; no split comments were submitted')
            if not self.replies_enabled:
                raise DeliveryNotSent('XHS automatic replies are disabled; no comment was submitted')
            account = await self._verified()
            try:
                raw_items = await self.api.notifications()
            except Exception as exc:
                raise DeliveryNotSent('XHS original comment could not be fetched before submission') from exc
            candidates = []
            for raw in raw_items:
                try:
                    candidate = self._comment(raw, account)
                except ValueError:
                    continue
                if (candidate is not None
                        and candidate.metadata.get('comment_id') == original.metadata.get('comment_id')):
                    candidates.append(candidate)
            if len(candidates) != 1 or self.reply_context(candidates[0]) != self.reply_context(original):
                raise DeliveryNotSent('XHS comment author/feed/account could not be revalidated')
            await self.api.request('POST', 'notifications/reply',
                json={'comment_id': original.metadata['comment_id'], 'content': text})
        # The pinned backend supplies no independently verifiable post/reply ID.
        # HTTP success is submission evidence, not delivery. OutputDelivery journals
        # this as unknown, never automatically retries an ambiguous platform effect.
        raise RuntimeError('XHS request submitted; platform delivery is unverified. Inspect before any new send.')
