"""Independent accounts on the real gateway/owner/inbox/output path; no live Weixin."""
from __future__ import annotations

import asyncio
import io
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner
from PIL import Image
from xdog.claw.application.gateway import GatewayServer
from xdog.claw.channels.weixin import media
from xdog.claw.channels.weixin.auth import WeixinAccountData, save_account
from xdog.claw.channels.weixin.context_tokens import clear_context_tokens, get_context_token
from xdog.claw.channels.weixin.monitor import _load_get_updates_buf
from xdog.claw.channels.weixin.types import GetUpdatesResp, MessageItem, MessageItemType
from xdog.claw.cli.cli import _channel_lines, _live_channels, _update_config_weixin, cli
from xdog.claw.config import ClawConfig, GroupDef, ToolConfigError, load_config, save_config
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.outbound_messages import OutboundMessages

from packages.claw.tests.eyes_helpers import ScriptedModel
from packages.claw.tests.test_weixin_application_intake import ChannelApi, sent_parts, text
from packages.claw.tests.test_weixin_images import image_item
from packages.claw.tests.test_weixin_monitor import wait


@pytest.mark.parametrize('value', ['bot', None, False, [1], [''], ['../bot'], ['a/b'], ['a', 'a']])
def test_invalid_account_selection_fails_closed(value):
    with pytest.raises(ToolConfigError, match='weixin_accounts'):
        ClawConfig(weixin_accounts=value)


@pytest.mark.parametrize('field', ['weixin_enabled', 'weixin_account_id', 'weixin_token', 'weixin_base_url'])
def test_retired_global_settings_are_actionable_errors_not_silent_fallbacks(tmp_path, field):
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump({field: 'fixture-private-value'}))
    with pytest.raises(ToolConfigError, match='Use weixin_accounts') as error:
        load_config(path)
    assert 'fixture-private-value' not in str(error.value)


def test_selection_roundtrip_and_parallel_confirmed_logins_do_not_lose_accounts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path/'config.yaml'
    save_config(ClawConfig(weixin_accounts=('first', 'second')), path)
    assert load_config(path).weixin_accounts == ('first', 'second')
    raw = yaml.safe_load(path.read_text())
    raw['custom'] = {'preserved': True}
    path.write_text(yaml.safe_dump(raw))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda account: _update_config_weixin(path, account), ['third', 'fourth']))
    _update_config_weixin(path, 'third')
    saved = yaml.safe_load(path.read_text())
    assert set(saved['weixin_accounts']) == {'first', 'second', 'third', 'fourth'}
    assert len(saved['weixin_accounts']) == 4 and saved['custom'] == {'preserved': True}
    assert path.stat().st_mode & 0o777 == 0o600
    assert not {'weixin_token', 'weixin_account_id', 'weixin_enabled'} & saved.keys()


def test_invalid_group_login_is_rejected_before_fetching_qr(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    start = AsyncMock(side_effect=AssertionError('Must not create a QR'))
    monkeypatch.setattr('xdog.claw.channels.weixin.auth.start_qr_login', start)
    path = tmp_path/'config.yaml'
    save_config(ClawConfig(groups=(GroupDef(id='main'),)), path)
    result = CliRunner().invoke(cli, ['channel', 'login', '--weixin', '--group', 'unknown', '--config', str(path)])
    assert result.exit_code != 0 and 'Configure the target group' in result.output
    start.assert_not_awaited()


def configuration(tmp_path, accounts=('main-bot', 'cat-bot')):
    root = tmp_path/'data'
    groups = (GroupDef(id='main', workspace=str(tmp_path/'main'), is_main=True),
              GroupDef(id='maotao', workspace=str(tmp_path/'cat')))
    for account, group in [('main-bot', 'main'), ('cat-bot', 'maotao'), ('inactive-bot', 'main')]:
        save_account(root, account, WeixinAccountData(token='private-'+account, base_url='https://example.invalid',
                                                    user_id='same-owner', group_id=group))
    return ClawConfig(data_dir=str(root), socket_path=str(tmp_path/'g.sock'), pid_file=str(tmp_path/'g.pid'),
                      weixin_accounts=accounts, enabled_tools=(), groups=groups)


def apis():
    return {'main-bot': ChannelApi(), 'cat-bot': ChannelApi()}


def factory(pool):
    def create(base_url, token):
        assert base_url == 'https://example.invalid'
        return pool[token.removeprefix('private-')]
    return create


async def test_two_bots_same_owner_interleave_without_cross_group_or_reply_state(tmp_path):
    config = configuration(tmp_path)
    model, pool = ScriptedModel(), apis()
    entered, release = asyncio.Event(), asyncio.Event()
    model.gates['hold-main'] = entered, release
    server = GatewayServer(config)
    for account in pool:
        clear_context_tokens(account)
    with patch('xdog.claw.application.gateway._build_model_and_options', return_value=('test/dummy', model)), \
         patch('xdog.claw.channels.weixin.channel.WeixinApiClient', side_effect=factory(pool)):
        try:
            await server.start()
            app = server._orchestrator
            assert {channel._account_id for channel in app._channels} == {'main-bot', 'cat-bot'}
            same = 'same-owner'
            main_msg = replace(text(1, same, 'hold-main'), context_token='main-context')
            cat_msg = replace(text(1, same, 'cat-work'), context_token='cat-context')
            pool['main-bot'].updates.put_nowait(GetUpdatesResp(msgs=(main_msg,), get_updates_buf='main-cursor'))
            await wait(entered.wait())
            pool['cat-bot'].updates.put_nowait(GetUpdatesResp(msgs=(cat_msg,), get_updates_buf='cat-cursor'))
            cat_reply = await wait(pool['cat-bot'].sent.get())
            assert sent_parts(cat_reply) == (same, 'cat-context', 'Scheduled report')
            assert pool['main-bot'].sent.empty()
            release.set()
            main_reply = await wait(pool['main-bot'].sent.get())
            assert sent_parts(main_reply) == (same, 'main-context', 'Scheduled report')
            assert get_context_token('main-bot', same) == 'main-context'
            assert get_context_token('cat-bot', same) == 'cat-context'
            assert _load_get_updates_buf(Path(config.data_dir), 'main-bot') == 'main-cursor'
            assert _load_get_updates_buf(Path(config.data_dir), 'cat-bot') == 'cat-cursor'
            service = await app.input_service()
            for group, workspace, account, body in [('main', tmp_path/'main', 'main-bot', 'hold-main'),
                                                    ('maotao', tmp_path/'cat', 'cat-bot', 'cat-work')]:
                rows = await service.store.records(InputScope(group, workspace))
                assert len(rows) == 1 and rows[0].envelope.message.instruction == body
                record = await wait(service.wait(rows[0].envelope.scope, rows[0].envelope.message.id))
                identity = record.envelope.context['identity']
                assert identity['source']['account_id'] == account and identity['principal']['role'] == 'admin'
                refs = app._channel_replies.bound(record.envelope.scope)
                catalog = [OutboundMessages._channel_row(key, ref) for key, ref in refs.items()]
                assert len(catalog) == 1 and catalog[0]['account_id'] == account
            live = await asyncio.to_thread(_live_channels, Path(config.socket_path))
            assert {row['account_id'] for row in live} == {'main-bot', 'cat-bot'}
            assert all(row['status'] == 'watching' for row in live)
            assert 'private-' not in str(live) and 'context' not in str(live)
            lines = '\n'.join(_channel_lines(config, live))
            assert 'group=main' in lines and 'group=maotao' in lines
        finally:
            release.set()
            await server.stop()
            for account in pool:
                clear_context_tokens(account)


async def test_session_expiry_and_missing_account_do_not_stop_the_other_bot(tmp_path):
    config = configuration(tmp_path, accounts=('missing-bot', 'main-bot', 'cat-bot'))
    model, pool = ScriptedModel(), apis()
    pool['cat-bot'].updates.put_nowait(GetUpdatesResp(ret=-14, errcode=-14))
    server = GatewayServer(config)
    with patch('xdog.claw.application.gateway._build_model_and_options', return_value=('test/dummy', model)), \
         patch('xdog.claw.channels.weixin.channel.WeixinApiClient', side_effect=factory(pool)):
        try:
            await server.start()
            await wait(pool['cat-bot'].polls.get())
            pool['main-bot'].updates.put_nowait(GetUpdatesResp(msgs=(text(2, 'same-owner', 'still works'),),
                                                              get_updates_buf='main-next'))
            await wait(pool['main-bot'].sent.get())
            live = await asyncio.to_thread(_live_channels, Path(config.socket_path))
            states = {row['account_id']: row['status'] for row in live}
            assert states == {'main-bot': 'watching', 'cat-bot': 'session_expired', 'missing-bot': 'unavailable'}
            assert model.calls == ['still works']
        finally:
            await server.stop()


async def test_same_image_and_sender_go_to_their_own_group_history(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    config = configuration(tmp_path)
    stream = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(stream, format='PNG')
    monkeypatch.setattr(media, 'download_image', AsyncMock(return_value=stream.getvalue()))
    model, pool = ScriptedModel(), apis()
    server = GatewayServer(config)
    with patch('xdog.claw.application.gateway._build_model_and_options', return_value=('test/dummy', model)), \
         patch('xdog.claw.channels.weixin.channel.WeixinApiClient', side_effect=factory(pool)):
        try:
            await server.start()
            image = MessageItem(type=MessageItemType.IMAGE, image_item=image_item())
            for account in pool:
                message = replace(text(3, 'same-owner', 'image'), item_list=(image,), context_token=account+'-ctx')
                pool[account].updates.put_nowait(GetUpdatesResp(msgs=(message,), get_updates_buf=account+'-cursor'))
            for account in pool:
                reply = await wait(pool[account].sent.get())
                assert reply.context_token == account+'-ctx'
            filenames = []
            for group, workspace in [('main', tmp_path/'main'), ('maotao', tmp_path/'cat')]:
                rows = await (await server._orchestrator.input_service()).store.records(InputScope(group, workspace))
                path = Path(rows[0].envelope.message.instruction.split('saved at: ', 1)[1])
                assert path.parent == Path(config.data_dir)/'groups'/group/'sessions'/'images'
                assert path.read_bytes() == stream.getvalue()
                filenames.append(path.name)
            assert filenames[0] == filenames[1]
        finally:
            await server.stop()


def test_login_second_group_adds_account_and_retains_first_binding(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from xdog.claw.channels.weixin import auth

    config = configuration(tmp_path, accounts=('main-bot',))
    path = tmp_path/'config.yaml'
    save_config(config, path)
    before = (Path(config.data_dir)/'weixin'/'accounts'/'main-bot.json').read_bytes()
    monkeypatch.setattr(auth, 'start_qr_login', AsyncMock(return_value=auth.QrStartResult(
        qrcode_url='https://example.invalid/synthetic-login', qrcode='fixture-ticket')))
    monkeypatch.setattr(auth, 'wait_qr_login', AsyncMock(return_value=auth.QrWaitResult(
        connected=True, bot_token='private-cat-new', account_id='cat-bot', user_id='same-owner')))
    result = CliRunner().invoke(cli, ['channel', 'login', '--weixin', '--group', 'maotao', '--config', str(path)])
    assert result.exit_code == 0, result.output
    assert load_config(path).weixin_accounts == ('main-bot', 'cat-bot')
    assert auth.load_account(Path(config.data_dir), 'cat-bot').group_id == 'maotao'
    assert (Path(config.data_dir)/'weixin'/'accounts'/'main-bot.json').read_bytes() == before
    assert 'private-cat-new' not in result.output


async def test_failed_connection_is_unregistered_without_breaking_other_account(tmp_path, monkeypatch):
    from xdog.claw.channels.weixin.channel import WeixinChannel

    config = configuration(tmp_path)
    model, pool = ScriptedModel(), apis()
    original = WeixinChannel.connect
    async def connect(channel):
        if channel._account_id == 'main-bot':
            raise OSError('fixture unavailable')
        await original(channel)
    monkeypatch.setattr(WeixinChannel, 'connect', connect)
    server = GatewayServer(config)
    with patch('xdog.claw.application.gateway._build_model_and_options', return_value=('test/dummy', model)), \
         patch('xdog.claw.channels.weixin.channel.WeixinApiClient', side_effect=factory(pool)):
        try:
            await server.start()
            app = server._orchestrator
            assert [channel._account_id for channel in app._channels] == ['cat-bot']
            assert all(channel._account_id != 'main-bot' for channel in app._channel_replies._channels.values())
            assert app._channel_replies.bound(InputScope('main', tmp_path/'main')) == {}
            pool['cat-bot'].updates.put_nowait(GetUpdatesResp(msgs=(text(4, 'same-owner', 'cat still works'),)))
            assert sent_parts(await wait(pool['cat-bot'].sent.get()))[0] == 'same-owner'
            live = await asyncio.to_thread(_live_channels, Path(config.socket_path))
            assert {row['account_id']: row['status'] for row in live} == {
                'cat-bot': 'watching', 'main-bot': 'unavailable'}
        finally:
            await server.stop()
        monkeypatch.setattr(WeixinChannel, 'connect', original)
        try:
            await server.start()
            live = await asyncio.to_thread(_live_channels, Path(config.socket_path))
            assert len(live) == 2
            assert {row['account_id'] for row in live} == {'main-bot', 'cat-bot'}
            assert all(row['status'] != 'unavailable' for row in live)
        finally:
            await server.stop()


async def test_restart_keeps_account_cursors_and_deduplicates_both_original_inputs(tmp_path):
    config = configuration(tmp_path)
    model = ScriptedModel()
    messages = {'main-bot': text(7, 'same-owner', 'main-original'),
                'cat-bot': text(7, 'same-owner', 'cat-original')}
    first_pool = apis()
    first = GatewayServer(config)
    with patch('xdog.claw.application.gateway._build_model_and_options', return_value=('test/dummy', model)), \
         patch('xdog.claw.channels.weixin.channel.WeixinApiClient', side_effect=factory(first_pool)):
        try:
            await first.start()
            for account, message in messages.items():
                first_pool[account].updates.put_nowait(
                    GetUpdatesResp(msgs=(message,), get_updates_buf=account+'-first')
                )
            for account in first_pool:
                await wait(first_pool[account].sent.get())
            for group, workspace in [('main', tmp_path/'main'), ('maotao', tmp_path/'cat')]:
                service = await first._orchestrator.input_service()
                rows = await service.store.records(InputScope(group, workspace))
                await wait(service.wait(rows[0].envelope.scope, rows[0].envelope.message.id))
        finally:
            await first.stop()
    assert sorted(model.calls) == ['cat-original', 'main-original']
    next_pool = apis()
    second = GatewayServer(config)
    with patch('xdog.claw.application.gateway._build_model_and_options', return_value=('test/dummy', model)), \
         patch('xdog.claw.channels.weixin.channel.WeixinApiClient', side_effect=factory(next_pool)):
        try:
            await second.start()
            for account, message in messages.items():
                assert (await wait(next_pool[account].polls.get()))[0] == account+'-first'
                next_pool[account].updates.put_nowait(GetUpdatesResp(msgs=(message,), get_updates_buf=account+'-again'))
                assert (await wait(next_pool[account].polls.get()))[0] == account+'-again'
            await asyncio.sleep(.02)
            assert len(model.calls) == 2 and all(api.sent.empty() for api in next_pool.values())
            for group, workspace in [('main', tmp_path/'main'), ('maotao', tmp_path/'cat')]:
                rows = await (await second._orchestrator.input_service()).store.records(InputScope(group, workspace))
                assert len(rows) == 1
        finally:
            await second.stop()


async def test_two_accounts_in_one_group_keep_input_ids_and_original_reply_routes(tmp_path):
    config = configuration(tmp_path)
    save_account(Path(config.data_dir), 'cat-bot', WeixinAccountData(
        token='private-cat-bot', base_url='https://example.invalid', user_id='same-owner', group_id='main'))
    model, pool = ScriptedModel(), apis()
    server = GatewayServer(config)
    with patch('xdog.claw.application.gateway._build_model_and_options', return_value=('test/dummy', model)), \
         patch('xdog.claw.channels.weixin.channel.WeixinApiClient', side_effect=factory(pool)):
        try:
            await server.start()
            for account, api in pool.items():
                original = replace(text(9, 'same-owner', account), context_token=account+'-context')
                api.updates.put_nowait(GetUpdatesResp(msgs=(original,)))
            for account, api in pool.items():
                assert sent_parts(await wait(api.sent.get())) == (
                    'same-owner', account+'-context', 'Scheduled report')
            scope = InputScope('main', tmp_path/'main')
            rows = await (await server._orchestrator.input_service()).store.records(scope)
            assert len(rows) == 2 and len({row.envelope.message.id for row in rows}) == 2
            assert sorted(model.calls) == ['cat-bot', 'main-bot']
            references = server._orchestrator._channel_replies.bound(scope)
            catalog = [OutboundMessages._channel_row(key, ref) for key, ref in references.items()]
            assert {row['account_id'] for row in catalog} == {'main-bot', 'cat-bot'}
            assert all(api.sent.empty() for api in pool.values())
        finally:
            await server.stop()
