"""Explicit candidate login, credential ownership and configuration preservation."""
from __future__ import annotations

import asyncio
import importlib
import json
from unittest.mock import AsyncMock

import pytest
import yaml
from click.testing import CliRunner
from xdog.claw.channels.accounts import bind_account, load_account
from xdog.claw.channels.xhs.api import LoginRequired, cookie_path, credential_home
from xdog.claw.cli.cli import cli

login_module = importlib.import_module('xdog.claw.channels.xhs.login')


def save(home, token='new'):
    path = cookie_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'a1': 'fixture', 'web_session': token}))


@pytest.mark.parametrize('failure', [None, 'scan', 'verification', 'commit'])
async def test_candidate_login_preserves_failed_attempts(tmp_path, monkeypatch, failure):
    from xdog.claw.channels.xhs.api import read_cookies

    old = bind_account(tmp_path, 'xhs', 'main', 'old-account', 'Old')
    home = credential_home(tmp_path, 'main')
    save(home, 'old')
    class Api:
        def __init__(self, _data, _group, *, home=None):
            self.candidate = home
        async def identity(self):
            if self.candidate is None:
                raise LoginRequired('expired')
            if failure == 'verification':
                raise ValueError('not verified')
            assert read_cookies(self.candidate)['web_session'] == 'new'
            return 'new-account', 'New'
        async def close(self):
            pass
    monkeypatch.setattr(login_module, 'XhsApi', Api)
    monkeypatch.setattr(login_module, 'require_sdk', lambda: None)
    async def scan(candidate, timeout):
        assert timeout == 270
        save(candidate)
        if failure == 'scan':
            raise TimeoutError('scan expired')
    if failure == 'commit':
        def fail(*_args):
            raise OSError('binding failed')
        monkeypatch.setattr(login_module, 'bind_account', fail)
    if failure:
        with pytest.raises((ValueError, TimeoutError, OSError)):
            await login_module.login(tmp_path, 'main', scan=scan)
        assert load_account(tmp_path, 'xhs', 'main') == old
        assert read_cookies(home)['web_session'] == 'old'
    else:
        account = await login_module.login(tmp_path, 'main', scan=scan)
        assert account.account_id == 'new-account' and account.revision != old.revision
        assert read_cookies(home)['web_session'] == 'new'
        assert cookie_path(home).stat().st_mode & 0o777 == 0o600
    assert not list(home.glob('login-*'))


async def test_valid_existing_login_does_not_start_a_new_scan(tmp_path, monkeypatch):
    api = type('Api', (), {'identity': AsyncMock(return_value=('ours', 'Profile')), 'close': AsyncMock()})()
    monkeypatch.setattr(login_module, 'XhsApi', lambda *_args, **_kwargs: api)
    monkeypatch.setattr(login_module, 'require_sdk', lambda: None)
    scan = AsyncMock(side_effect=AssertionError('No new QR'))
    account = await login_module.login(tmp_path, 'main', scan=scan)
    assert account.account_id == 'ours'
    scan.assert_not_called()


def test_cli_login_preserves_unrelated_config_and_reply_policy(tmp_path, monkeypatch):
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump({
        'data_dir': str(tmp_path / 'data'), 'xhs_replies_enabled': False, 'model': 'test-model'}))
    async def login(data, group):
        return bind_account(data, 'xhs', group, 'ours', 'Profile')
    monkeypatch.setattr(login_module, 'login', login)
    result = CliRunner().invoke(cli, ['channel', 'login', '--xhs', '--config', str(path)])
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load(path.read_text())
    assert raw['xhs_enabled'] and not raw['xhs_replies_enabled']
    assert raw['model'] == 'test-model'
    rejected = CliRunner().invoke(cli, ['channel', 'login', '--xhs', '--config', str(path),
                                      '--base-url', 'https://example.invalid'])
    assert rejected.exit_code != 0 and '--base-url requires --weixin' in rejected.output


async def test_scan_cancellation_during_spawn_still_stops_the_owned_process(tmp_path, monkeypatch):
    from types import SimpleNamespace

    entered, release = asyncio.Event(), asyncio.Event()
    process = SimpleNamespace(pid=12345, returncode=None)
    async def wait():
        await asyncio.Event().wait()
    process.wait = wait
    async def spawn(*args, **kwargs):
        assert kwargs['env']['HOME'] == str(tmp_path)
        assert kwargs['env']['XDG_CONFIG_HOME'] == str(tmp_path / '.config')
        assert kwargs['env']['CLOAKBROWSER_CACHE_DIR'] == str(tmp_path / 'prepared-cloak')
        assert 'xhs.scan' in args[-1]
        entered.set()
        await release.wait()
        return process
    def terminate(pid, _signal):
        assert pid == process.pid
        process.returncode = -15
    monkeypatch.setenv('CLOAKBROWSER_CACHE_DIR', str(tmp_path / 'prepared-cloak'))
    monkeypatch.setattr(login_module.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(login_module.os, 'killpg', terminate)
    # wait() must acknowledge process termination as a real child does.
    async def wait_closed():
        while process.returncode is None:
            await asyncio.sleep(.001)
        return process.returncode
    process.wait = wait_closed
    task = asyncio.create_task(login_module.scan_qr(tmp_path, 10))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.returncode == -15


async def test_parallel_login_is_rejected_and_cancellation_removes_only_candidate(tmp_path, monkeypatch):
    from xdog.claw.channels.xhs.login_access import LoginBusy

    old = bind_account(tmp_path, 'xhs', 'main', 'ours', 'Profile')
    save(credential_home(tmp_path, 'main'), 'old')
    api = type('Api', (), {'identity': AsyncMock(side_effect=LoginRequired('expired')), 'close': AsyncMock()})()
    monkeypatch.setattr(login_module, 'XhsApi', lambda *_args, **_kwargs: api)
    monkeypatch.setattr(login_module, 'require_sdk', lambda: None)
    entered = asyncio.Event()
    calls = []
    async def scan(candidate, _timeout):
        calls.append(candidate)
        save(candidate)
        entered.set()
        await asyncio.Event().wait()
    first = asyncio.create_task(login_module.login(tmp_path, 'main', scan=scan))
    try:
        await entered.wait()
        with pytest.raises(LoginBusy):
            await login_module.login(tmp_path, 'main', scan=scan)
    finally:
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    assert len(calls) == 1 and not calls[0].exists()
    assert load_account(tmp_path, 'xhs', 'main') == old
    assert json.loads(cookie_path(credential_home(tmp_path, 'main')).read_text())['web_session'] == 'old'



@pytest.mark.parametrize('cache_value', [None, ''])
async def test_scan_preserves_prepared_vendor_cache_but_isolates_xhs_home(tmp_path, monkeypatch, cache_value):
    import os
    from types import SimpleNamespace

    real_home = tmp_path / 'caller'
    candidate = tmp_path / 'candidate'
    monkeypatch.setenv('HOME', str(real_home))
    if cache_value is None:
        monkeypatch.delenv('CLOAKBROWSER_CACHE_DIR', raising=False)
    else:
        monkeypatch.setenv('CLOAKBROWSER_CACHE_DIR', cache_value)
    monkeypatch.setenv('CLOAKBROWSER_LICENSE_KEY', 'private-license')
    before = dict(os.environ)
    async def spawn(*args, **kwargs):
        environment = kwargs['env']
        assert environment['HOME'] == str(candidate)
        assert environment['CLOAKBROWSER_CACHE_DIR'] == str(real_home / '.cloakbrowser')
        assert environment['CLOAKBROWSER_LICENSE_KEY'] == 'private-license'
        assert not any('private-license' in str(value) for value in args)
        return SimpleNamespace(returncode=0, wait=AsyncMock(return_value=0))
    monkeypatch.setattr(login_module.asyncio, 'create_subprocess_exec', spawn)
    await login_module.scan_qr(candidate, 10)
    assert dict(os.environ) == before
