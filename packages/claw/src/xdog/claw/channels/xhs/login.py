"""Explicit interactive login; never called by discovery or gateway startup."""
from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Callable
from pathlib import Path

from xdog.agent.async_work import run_owned
from xdog.claw.channels.accounts import ChannelAccount, bind_account

from .api import XhsApi


async def login(api: XhsApi, data_dir: Path, group_id: str, show_qr: Callable[[Path], None],
                *, timeout: float = 480) -> ChannelAccount:
    data = await api.request('GET', 'login/status')
    if data.get('is_logged_in') is not True:
        qr = await api.request('GET', 'login/qrcode')
        image = qr.get('img')
        if qr.get('is_logged_in') is not True:
            if not isinstance(image, str) or len(image) > 2 * 1024 * 1024:
                raise ValueError('XHS backend did not supply a bounded QR image')
            raw = base64.b64decode(image.split(',', 1)[-1], validate=True)
            if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('XHS QR image is not PNG')
            def save() -> Path:
                directory = data_dir / 'xhs'
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / 'login-qr.png'
                fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                return path
            path = await run_owned(save)
            try:
                show_qr(path)
                async with asyncio.timeout(timeout):
                    while True:
                        await asyncio.sleep(2)
                        status = await api.request('GET', 'login/status')
                        if status.get('is_logged_in') is True:
                            break
            finally:
                await run_owned(lambda: path.unlink(missing_ok=True))
    account_id, username = await api.identity()
    return await run_owned(bind_account, data_dir, 'xhs', group_id, account_id, username)
