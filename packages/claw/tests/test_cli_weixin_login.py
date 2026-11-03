"""Weixin login renders a real terminal QR without opening a browser or live login."""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner
from xdog.claw.channels.weixin import auth
from xdog.claw.cli.cli import cli


@pytest.mark.parametrize('connected', [True, False])
def test_weixin_login_prints_terminal_qr_before_polling_and_preserves_failed_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connected: bool,
) -> None:
    import yaml

    data_dir = tmp_path / 'data'
    config_file = tmp_path / 'config.yaml'
    config_file.write_text(yaml.safe_dump({'data_dir': str(data_dir),
                                         'weixin_accounts': ['existing-bot'], 'model': 'test/model'}))
    before = config_file.read_bytes()
    url = 'https://example.invalid/weixin-login?challenge=synthetic'
    start = AsyncMock(return_value=auth.QrStartResult(qrcode_url=url, qrcode='synthetic-qr-ticket'))
    wait = AsyncMock(return_value=auth.QrWaitResult(
        connected=connected, bot_token='synthetic-bot-token' if connected else '',
        account_id='new-bot' if connected else '', user_id='owner' if connected else '',
        message='QR expired'))
    monkeypatch.setattr(auth, 'start_qr_login', start)
    monkeypatch.setattr(auth, 'wait_qr_login', wait)

    result = CliRunner().invoke(cli, ['channel', 'login', '--weixin', '--config', str(config_file)])

    assert result.exit_code == (0 if connected else 1), result.output
    qr_start = min(result.output.index(char) for char in ('█', '▀', '▄') if char in result.output)
    assert result.output.index('Scan this QR code') < qr_start < result.output.index('Waiting...')
    assert 'Or open: ' + url in result.output
    assert 'synthetic-bot-token' not in result.output and 'synthetic-qr-ticket' not in result.output
    start.assert_awaited_once()
    wait.assert_awaited_once_with(api_base_url=auth.DEFAULT_BASE_URL, qrcode='synthetic-qr-ticket', timeout_s=480)
    if connected:
        saved = auth.load_account(data_dir, 'new-bot')
        assert saved is not None and saved.group_id == 'main' and saved.user_id == 'owner'
        config = yaml.safe_load(config_file.read_text())
        assert config['model'] == 'test/model' and config['weixin_accounts'] == ['existing-bot', 'new-bot']
    else:
        assert 'QR expired' in result.output
        assert config_file.read_bytes() == before and not (data_dir / 'weixin').exists()
