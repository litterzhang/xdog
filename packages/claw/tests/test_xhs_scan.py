"""Public fork API, isolated export and credential-free diagnostics; no browser policy here."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from xdog.claw.channels.xhs import scan


@pytest.fixture
def sdk(monkeypatch):
    previous_logging = logging.root.manager.disable
    class ApiError(Exception):
        pass
    class LoginError(ApiError):
        pass
    class BrowserError(ApiError):
        pass
    saved = []
    modules = {
        'xhs_cli.qr_login': SimpleNamespace(QrLoginError=LoginError),
        'xhs_cli.browser': SimpleNamespace(BrowserUnavailable=BrowserError),
        'xhs_cli.cookies': SimpleNamespace(save_cookies=saved.append),
        'xhs_cli.exceptions': SimpleNamespace(XhsApiError=ApiError),
    }
    original = scan.importlib.import_module
    monkeypatch.setattr(scan.importlib, 'import_module', lambda name: modules.get(name) or original(name))
    yield SimpleNamespace(modules=modules, saved=saved, error=LoginError, browser_error=BrowserError)
    assert logging.root.manager.disable == previous_logging


def test_scan_calls_public_login_then_saves_only_returned_credentials(sdk, capsys):
    values = {'a1': 'secret-a1', 'web_session': 'secret-session'}
    def login(**kwargs):
        assert kwargs == {'timeout_s': 240}
        return SimpleNamespace(cookies=values)
    sdk.modules['xhs_cli.qr_login'].qrcode_login = login
    assert scan.main() == 0
    assert sdk.saved == [values]
    output = capsys.readouterr()
    assert 'session verified' in output.out
    assert 'secret' not in output.out + output.err


@pytest.mark.parametrize('kind', ['login', 'browser', 'unexpected'])
def test_failed_login_does_not_save_or_leak_unexpected_exception_prose(sdk, capsys, kind):
    error = {'login': sdk.error('QR login failed during session completion: HTTP 403'),
             'browser': sdk.browser_error('CloakBrowser needs an authorized graphical session'),
             'unexpected': RuntimeError('secret-session raw-response=private')}
    def fail(**kwargs):
        raise error[kind]
    sdk.modules['xhs_cli.qr_login'].qrcode_login = fail
    assert scan.main() == 1
    assert sdk.saved == []
    output = capsys.readouterr()
    assert 'secret' not in output.out + output.err and 'private' not in output.out + output.err
    expected = 'HTTP 403' if kind == 'login' else 'graphical' if kind == 'browser' else 'login/export failed'
    assert expected in output.err


@pytest.mark.parametrize('cookies', [None, {}, {'a1': 'fixture'}, {'web_session': 'fixture'}])
def test_incomplete_result_is_not_exported(sdk, capsys, cookies):
    sdk.modules['xhs_cli.qr_login'].qrcode_login = lambda **kwargs: SimpleNamespace(cookies=cookies)
    assert scan.main() == 1
    assert sdk.saved == []
    assert 'incomplete authenticated' in capsys.readouterr().err


def test_missing_modified_dependency_has_an_actionable_error(monkeypatch, capsys):
    def missing(name):
        raise ImportError('private-install-path')
    monkeypatch.setattr(scan.importlib, 'import_module', missing)
    assert scan.main() == 1
    output = capsys.readouterr()
    assert 'modified xiaohongshu-cli[browser]' in output.err
    assert 'private' not in output.err
