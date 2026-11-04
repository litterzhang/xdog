"""Explicit SDK login; verified candidate cookies replace credentials only on success."""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from xdog.agent.async_work import run_owned, wait_owned
from xdog.claw.channels.accounts import ChannelAccount, bind_account

from .api import LoginRequired, XhsApi, cookie_path, credential_home, read_cookies, require_sdk
from .login_access import login_access


async def scan_qr(home: Path, timeout: float) -> None:
    # Isolate XHS credentials, but use the caller's prepared CloakBrowser cache
    # and license. Never copy a license into group credentials or put it on argv.
    configured_cache = os.environ.get('CLOAKBROWSER_CACHE_DIR') or str(Path.home() / '.cloakbrowser')
    cache = Path(configured_cache).expanduser().resolve()
    environment = {**os.environ, 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
                   'XDG_DATA_HOME': str(home / '.local/share'), 'XDG_STATE_HOME': str(home / '.local/state'),
                   'CLOAKBROWSER_CACHE_DIR': str(cache)}
    starting = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable, '-m', 'xdog.claw.channels.xhs.scan', env=environment,
        stdin=asyncio.subprocess.DEVNULL, start_new_session=True))
    try:
        process = await wait_owned(starting)
        async with asyncio.timeout(timeout):
            if await process.wait() != 0:
                raise ValueError(
                    'XHS QR login failed (see diagnostic above); '
                    'previous credentials and account binding were retained')
    finally:
        async def stop() -> None:
            if not starting.done() or starting.cancelled() or starting.exception() is not None:
                return
            process = starting.result()
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    if process.returncode is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    await process.wait()
        await wait_owned(asyncio.create_task(stop()))


async def login(data_dir: Path, group_id: str, *,
                scan: Callable[[Path, float], Awaitable[None]] = scan_qr) -> ChannelAccount:
    require_sdk()
    async with login_access(data_dir, exclusive=True):
        api = XhsApi(data_dir, group_id)
        try:
            try:
                account_id, username = await api.identity()
            except LoginRequired:
                account_id = ''
            if account_id:
                return await run_owned(bind_account, data_dir, 'xhs', group_id, account_id, username)
        finally:
            await api.close()
        home = credential_home(data_dir, group_id)
        await run_owned(lambda: home.mkdir(parents=True, exist_ok=True, mode=0o700))
        candidate = Path(await run_owned(tempfile.mkdtemp, prefix='login-', dir=home))
        try:
            await scan(candidate, 270)
            api = XhsApi(data_dir, group_id, home=candidate)
            try:
                account_id, username = await api.identity()
            finally:
                await api.close()
            def commit() -> ChannelAccount:
                read_cookies(candidate)  # Validate the exact candidate before publishing any credentials.
                destination = cookie_path(home)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = cookie_path(candidate)
                source.chmod(0o600)
                previous = candidate / 'previous-cookies.json'
                had_previous = destination.exists()
                if had_previous:
                    previous.write_bytes(destination.read_bytes())
                    previous.chmod(0o600)
                os.replace(source, destination)
                try:
                    return bind_account(data_dir, 'xhs', group_id, account_id, username)
                except BaseException:
                    if had_previous:
                        os.replace(previous, destination)
                    else:
                        destination.unlink(missing_ok=True)
                    raise
            return await run_owned(commit)
        finally:
            await run_owned(shutil.rmtree, candidate)
