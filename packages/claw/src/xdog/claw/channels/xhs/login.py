"""Explicit interactive login; never called by discovery or gateway startup."""
from __future__ import annotations

import asyncio
import base64
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from xdog.agent.async_work import run_owned, wait_owned
from xdog.claw.channels.accounts import ChannelAccount, bind_account

from .api import XhsApi
from .login_access import login_access


def _scan_timeout(advertised: object, limit: float) -> float:
    """The backend advertises a Go duration for its scan browser lifetime."""
    if (isinstance(advertised, str) and len(advertised) <= 64
            and re.fullmatch(r'(?:[0-9]+(?:\.[0-9]+)?[hms])+', advertised)):
        seconds = sum(float(number) * {'h': 3600, 'm': 60, 's': 1}[unit]
                      for number, unit in re.findall(r'([0-9]+(?:\.[0-9]+)?)([hms])', advertised))
        if seconds > 0:
            return min(limit, seconds)
    return min(limit, 240)  # v2.5.5 fallback; never keep displaying a dead scan browser.


async def login(api: XhsApi, data_dir: Path, group_id: str, show_qr: Callable[[Path], None],
                *, confirm_scan: Callable[[], Awaitable[None]], timeout: float = 240) -> ChannelAccount:
    async with login_access(data_dir, exclusive=True):
        return await _login(api, data_dir, group_id, show_qr, confirm_scan, timeout)


async def _login(api: XhsApi, data_dir: Path, group_id: str, show_qr: Callable[[Path], None],
                 confirm_scan: Callable[[], Awaitable[None]], timeout: float) -> ChannelAccount:
    data = await api.login_status()
    if data.get('is_logged_in') is not True:
        issued = asyncio.get_running_loop().time()
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
                fd, name = tempfile.mkstemp(dir=directory, prefix='login-qr-', suffix='.png')
                path = Path(name)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(raw)
                except BaseException:
                    path.unlink(missing_ok=True)
                    raise
                return path
            saving = asyncio.create_task(asyncio.to_thread(save))
            try:
                path = await wait_owned(saving)
            except BaseException:
                # Ctrl+C must settle the actual write and remove only its file,
                # even if cancellation wins before the path reaches this owner.
                if saving.done() and not saving.cancelled() and saving.exception() is None:
                    await run_owned(saving.result().unlink, missing_ok=True)
                raise
            try:
                deadline = issued + _scan_timeout(qr.get('timeout'), timeout)
                if deadline <= asyncio.get_running_loop().time():
                    raise TimeoutError('Backend scan window elapsed before the QR was ready')
                show_qr(path)
                async with asyncio.timeout_at(deadline):
                    # v2.5.5 status opens another browser and creates a website
                    # QR. Never poll it while the user is scanning this challenge.
                    await confirm_scan()
                    status = await api.login_status()
                    if status.get('is_logged_in') is not True:
                        raise ValueError('XHS backend did not confirm login after your confirmation. '
                                         'Keep existing cookies; retry explicitly for a fresh QR.')
            except TimeoutError as exc:
                raise TimeoutError(
                    'XHS scan wait ended; the QR may have expired. Run '
                    'channel login --xhs again for a fresh QR. Do not run parallel logins: '
                    'a new backend QR replaces the previous scan session. Existing cookies '
                    'and account binding were retained.'
                ) from exc
            finally:
                await run_owned(lambda: path.unlink(missing_ok=True))
    account_id, username = await api.identity()
    return await run_owned(bind_account, data_dir, 'xhs', group_id, account_id, username)
