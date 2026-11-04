"""Run against the real optional dependency when installed, with no platform traffic."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from xdog.claw.channels.xhs.api import XhsApi, XhsUnavailable, cookie_path, credential_home

sdk = pytest.importorskip('xhs_cli.client')


def test_real_sdk_methods_and_single_write_attempt(tmp_path, monkeypatch):
    home = credential_home(tmp_path, 'main')
    path = cookie_path(home)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'a1': 'a' * 52, 'webId': 'b' * 32, 'web_session': 'offline-fixture'}))
    calls = []
    def transport(request):
        calls.append(request)
        if request.method == 'POST':
            return httpx.Response(500, json={'success': False, 'private': 'do-not-expose'})
        return httpx.Response(200, json={'success': True, 'data': {
            'user_id': 'ours', 'basic_info': {'nickname': 'Profile'}, 'guest': False}})
    def factory(cookies, **kwargs):
        assert kwargs['max_retries'] == 1
        client = sdk.XhsClient(cookies, request_delay=0, **kwargs)
        client._http.close()
        client._http = httpx.Client(transport=httpx.MockTransport(transport))
        return client
    # Signing is exercised by the upstream SDK; no requests leave MockTransport.
    async def scenario():
        adapter = XhsApi(tmp_path, 'main', factory=factory)
        try:
            assert await adapter.identity() == ('ours', 'Profile')
            with pytest.raises(XhsUnavailable) as caught:
                await adapter.reply('feed1', 'comment1', 'answer')
            assert 'do-not-expose' not in str(caught.value)
        finally:
            await adapter.close()
    asyncio.run(scenario())
    posts = [request for request in calls if request.method == 'POST']
    assert len(posts) == 1
    assert json.loads(posts[0].content)['target_comment_id'] == 'comment1'
    assert json.loads(posts[0].content)['note_id'] == 'feed1'
    assert Path(sdk.__file__).is_file()



@pytest.mark.parametrize('outcome', ['complete', 'guest', 'http_error'])
def test_public_sdk_login_returns_verified_credentials_to_isolated_export(tmp_path, monkeypatch, capsys, outcome):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from xdog.claw.channels.xhs import scan
    from xhs_cli import qr_login

    assert hasattr(qr_login, 'QrLoginResult'), 'The modified local SDK is required, not the PyPI 0.6.4 API'
    monkeypatch.setenv('HOME', str(tmp_path))
    closed = []
    def response(path, payload, method='GET', status=200):
        return SimpleNamespace(url='https://edith.xiaohongshu.com' + path,
                               request=SimpleNamespace(method=method), status=status,
                               json=lambda: {'success': True, 'data': payload})
    events = [response(qr_login.QR_CREATE_ENDPOINT, {'url': 'secret-qr'}, 'POST'),
              response(qr_login.QR_USERINFO_ENDPOINT, {'codeStatus': 2, 'userId': 'ours'}),
              response(qr_login.QR_STATUS_ENDPOINT, {'login_info': {'user_id': 'ours', 'session': 'secret-session'}},
                       status=403 if outcome == 'http_error' else 200)]
    class Page:
        context = SimpleNamespace(cookies=lambda: [
            {'name': name, 'value': 'fixture', 'domain': '.xiaohongshu.com'} for name in ('a1', 'webId')])
        def on(self, event, callback):
            self.callback = callback
        def goto(self, *args, **kwargs):
            while events:  # Includes completion before goto returns, with real public SDK logic.
                self.callback(events.pop(0))
        def wait_for_timeout(self, _timeout):
            raise AssertionError('all events already delivered')
    @contextmanager
    def browser():
        try:
            yield Page()
        finally:
            closed.append(True)
    def client(cookies, **kwargs):
        actual = sdk.XhsClient(cookies, **kwargs)
        actual._http.close()
        actual._http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            'success': True, 'data': {'user_id': 'ours', 'guest': outcome == 'guest'}})))
        return actual
    monkeypatch.setattr(qr_login, 'browser_page', browser)
    monkeypatch.setattr(qr_login, '_render_qr', lambda url: None)
    monkeypatch.setattr(qr_login, 'XhsClient', client)
    assert scan.main() == (0 if outcome == 'complete' else 1)
    assert closed == [True]
    output = capsys.readouterr()
    assert 'secret' not in output.out + output.err
    path = cookie_path(tmp_path)
    if outcome == 'complete':
        assert json.loads(path.read_text())['web_session'] == 'secret-session'
        assert path.stat().st_mode & 0o777 == 0o600
    else:
        assert not path.exists()
        assert ('HTTP 403' if outcome == 'http_error' else 'session does not match') in output.err


async def test_real_child_resolves_caller_cloak_license_without_copying_into_candidate(tmp_path, monkeypatch):
    import os
    import sys

    from xdog.claw.channels.xhs import login

    pytest.importorskip('cloakbrowser.license')
    caller = tmp_path / 'caller'
    candidate = tmp_path / 'candidate'
    cache = caller / '.cloakbrowser'
    cache.mkdir(parents=True)
    candidate.mkdir()
    license_file = cache / 'license.key'
    license_file.write_text('fixture-license-not-a-real-key')
    license_file.chmod(0o600)
    monkeypatch.setenv('HOME', str(caller))
    monkeypatch.delenv('CLOAKBROWSER_CACHE_DIR', raising=False)
    monkeypatch.delenv('CLOAKBROWSER_LICENSE_KEY', raising=False)
    original_spawn = asyncio.create_subprocess_exec
    before = dict(os.environ)
    script = """
from pathlib import Path
from cloakbrowser.config import get_cache_dir
from cloakbrowser.license import resolve_license_key
home = Path.home()
assert resolve_license_key() == 'fixture-license-not-a-real-key'
assert get_cache_dir() != home / '.cloakbrowser'
(home / 'cache-check').write_text('verified')
"""
    async def spawn(*args, **kwargs):
        assert args[-1] == 'xdog.claw.channels.xhs.scan'
        return await original_spawn(sys.executable, '-c', script, **kwargs)
    monkeypatch.setattr(login.asyncio, 'create_subprocess_exec', spawn)
    await login.scan_qr(candidate, 15)
    assert (candidate / 'cache-check').read_text() == 'verified'
    assert not (candidate / '.cloakbrowser' / 'license.key').exists()
    assert license_file.read_text() == 'fixture-license-not-a-real-key'
    assert dict(os.environ) == before
