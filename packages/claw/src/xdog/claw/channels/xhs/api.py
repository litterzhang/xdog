"""Optional xiaohongshu-cli SDK adapter; no HTTP backend or automatic browser login."""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from xdog.agent.async_work import run_owned
from xdog.claw.core.identity.models import label

T = TypeVar("T")


class XhsUnavailable(ValueError):
    """Safe diagnostic: SDK exception prose can contain credentials and response bodies."""


class DependencyMissing(XhsUnavailable):
    pass


class LoginRequired(XhsUnavailable):
    pass


def require_sdk() -> Any:
    try:
        return importlib.import_module('xhs_cli.client').XhsClient
    except ImportError as exc:
        raise DependencyMissing('XHS requires the optional dependency: install xdog-claw[xhs]') from exc


def credential_home(data_dir: Path, group_id: str) -> Path:
    key = hashlib.sha256(group_id.encode()).hexdigest()
    return data_dir / 'xhs' / 'accounts' / key


def cookie_path(home: Path) -> Path:
    return home / '.xiaohongshu-cli' / 'cookies.json'


def read_cookies(home: Path) -> dict[str, str]:
    path = cookie_path(home)
    try:
        if path.is_symlink() or path.stat().st_size > 64 * 1024:
            raise XhsUnavailable('Invalid XHS credentials file')
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise LoginRequired('XHS needs explicit login: xdog-claw channel login --xhs') from exc
    except (OSError, ValueError) as exc:
        raise XhsUnavailable('XHS credentials are unavailable; inspect the saved login') from exc
    if not isinstance(raw, dict):
        raise XhsUnavailable('Invalid XHS credentials file')
    cookies = {key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)}
    if not cookies.get('a1') or not cookies.get('web_session'):
        raise LoginRequired('XHS saved session is incomplete; log in explicitly')
    return cookies


@dataclass(frozen=True)
class Notifications:
    items: tuple[dict[str, Any], ...]
    truncated: bool = False


class XhsApi:
    def __init__(self, data_dir: Path, group_id: str, *, factory: Callable[..., Any] | None = None,
                 home: Path | None = None) -> None:
        self.home = home or credential_home(data_dir, group_id)
        self._factory = factory
        self._lock = asyncio.Lock()
        self._closed = False

    async def close(self) -> None:
        async with self._lock:
            self._closed = True

    async def _call(self, operation: Callable[[Any], T]) -> T:
        async with self._lock:
            if self._closed:
                raise XhsUnavailable('XHS adapter is closed')
            def invoke() -> T:
                factory = self._factory or require_sdk()
                cookies = read_cookies(self.home)
                # The SDK retries writes by default. Exactly one attempt, including
                # replies/publishing; host output receipts own outcome-unknown recovery.
                client = factory(cookies, timeout=30, max_retries=1)
                try:
                    return operation(client)
                except Exception as exc:
                    if type(exc).__name__ == 'SessionExpiredError':
                        raise LoginRequired('XHS session expired; log in explicitly') from None
                    if isinstance(exc, XhsUnavailable):
                        raise
                    kind = type(exc).__name__
                    if kind in {'NeedVerifyError', 'IpBlockedError', 'SignatureError'}:
                        raise XhsUnavailable('XHS requires manual attention: ' + kind) from None
                    raise XhsUnavailable('XHS request failed; outcome may be unknown') from None
                finally:
                    try:
                        client.close()
                    except Exception:
                        raise XhsUnavailable('XHS client cleanup failed; inspect before retrying') from None
            # Cancellation settles actual synchronous work before releasing its owner.
            return await run_owned(invoke)

    async def identity(self) -> tuple[str, str]:
        raw = await self._call(lambda client: client.get_self_info())
        if not isinstance(raw, dict) or raw.get('guest') is True:
            raise LoginRequired('XHS session is not an authenticated account')
        basic = raw.get('basic_info', raw)
        if not isinstance(basic, dict):
            raise XhsUnavailable('Invalid XHS account response')
        identifier = raw.get('user_id') or raw.get('userid') or basic.get('user_id')
        return label(identifier, maximum=256), label(basic.get('nickname', ''), empty=True)

    async def notifications(self) -> Notifications:
        def collect(client: Any) -> Notifications:
            items: list[dict[str, Any]] = []
            cursor, seen, count = '', set(), 0
            while count < 200:
                raw = client.get_notification_mentions(cursor=cursor, num=min(100, 200 - count))
                if not isinstance(raw, dict) or not isinstance(raw.get('message_list'), list):
                    raise XhsUnavailable('Invalid XHS mentions response')
                page = raw['message_list']
                if len(page) > 200 - count:
                    raise XhsUnavailable('XHS mentions page exceeds capacity')
                count += len(page)
                for item in page:
                    if not isinstance(item, dict):
                        raise XhsUnavailable('Invalid XHS mention')
                    if item.get('type') == 'mention/item':
                        continue  # A post-only mention has no exact comment target.
                    if item.get('type') not in {'comment/item', 'mention/comment', 'comment/comment'}:
                        raise XhsUnavailable('Unknown XHS mention type; no reply target was guessed')
                    user, note, comment = (item.get(key) for key in ('user_info', 'item_info', 'comment_info'))
                    if not isinstance(user, dict) or not isinstance(note, dict) or not isinstance(comment, dict):
                        raise XhsUnavailable('XHS comment lacks its exact sender/post/comment target')
                    items.append({'from': {'user_id': label(user.get('userid'), maximum=256)},
                        'feed_id': label(note.get('id'), maximum=256),
                        'comment_id': label(comment.get('id'), maximum=256),
                        'comment_text': label(comment.get('content'), maximum=64 * 1024)})
                if raw.get('has_more') is not True:
                    return Notifications(tuple(items))
                next_cursor = raw.get('cursor')
                if not page or not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
                    raise XhsUnavailable('XHS mentions cursor did not advance')
                seen.add(next_cursor)
                cursor = next_cursor
            return Notifications(tuple(items), truncated=True)
        return await self._call(collect)

    async def post(self, feed_id: str) -> dict[str, Any]:
        # This public SDK method does not use its process-global note-token cache.
        raw = await self._call(lambda client: client.get_note_from_html(feed_id))
        if not isinstance(raw, dict) or raw.get('noteId') != feed_id:
            raise XhsUnavailable('XHS post response does not match the original post')
        return {'feed_id': feed_id, 'title': label(raw.get('title', ''), empty=True, maximum=16 * 1024),
                'text': label(raw.get('desc', ''), empty=True, maximum=64 * 1024), 'untrusted': True}

    async def reply(self, feed_id: str, comment_id: str, text: str) -> None:
        await self._call(lambda client: client.reply_comment(feed_id, comment_id, text))

    async def publish(self, title: str, text: str, images: list[str]) -> None:
        def publish(client: Any) -> None:
            identifiers = []
            for path in images:
                permit = client.get_upload_permit()
                client.upload_file(permit['fileId'], permit['token'], path)
                identifiers.append(permit['fileId'])
            client.create_image_note(title, text, identifiers)
        await self._call(publish)
