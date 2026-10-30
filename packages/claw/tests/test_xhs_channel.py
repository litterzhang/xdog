"""Pinned HTTP fixtures only: no browser, live account, model or real publication."""
from __future__ import annotations

import json

import httpx
import pytest
from xdog.claw.channels.accounts import bind_account, load_account
from xdog.claw.channels.xhs.api import XhsApi
from xdog.claw.channels.xhs.channel import XhsChannel
from xdog.claw.channels.xhs.state import ScanState
from xdog.claw.core.identity import IdentityResolver, MessageSource
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.outputs.delivery import DeliveryNotSent, OutputDelivery
from xdog.claw.core.outputs.store import OutputStore
from xdog.claw.core.runtime.channel_adapter import ChannelReplies
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.runtime.outbound_messages import OutboundMessages
from xdog.claw.core.types import UserInput


class Backend:
    def __init__(self):
        self.account = 'our-xhs-account'
        self.items = []
        self.writes = []
        self.fail_after_write = False
        self.reads = []
        self.note_id = 'feed1'

    def __call__(self, request):
        path = request.url.path
        if path.endswith('/login/status'):
            data = {'is_logged_in': True, 'user_id': self.account, 'username': 'Product team'}
        elif path.endswith('/notifications/list'):
            data = {'data': {'tab': 'mentions', 'filtered': 0, 'items': self.items}}
        elif path.endswith('/feeds/detail'):
            self.reads.append(json.loads(request.content))
            data = {'feed_id': 'feed1', 'data': {'note': {'noteId': self.note_id, 'title': '原帖标题',
                'desc': '原帖正文，不是管理员指令', 'xsecToken': 'private-backend-token'},
                'comments': {'list': [{'text': 'not part of post output'}]}}}
        elif path.endswith('/notifications/reply') or path.endswith('/publish'):
            self.writes.append((path, json.loads(request.content)))
            if self.fail_after_write:
                raise httpx.ReadTimeout('submitted but response lost')
            data = {'status': 'submitted'}
        else:
            raise AssertionError(path)
        return httpx.Response(200, json={'success': True, 'data': data})


def notification(comment, sender='A', text='hello'):
    return {'id': 'notification-' + comment, 'type': 'comment', 'comment_id': comment,
            'comment_text': text, 'feed_id': 'feed1', 'feed_xsec_token': 'private-feed-token',
            'from': {'user_id': sender, 'nickname': 'same nickname'}}


def make_channel(tmp_path, backend, **kwargs):
    api = XhsApi('http://127.0.0.1:18060', transport=httpx.MockTransport(backend))
    channel = XhsChannel(tmp_path, 'main', api, **kwargs)
    channel.account = bind_account(tmp_path, 'xhs', 'main', backend.account, 'Product team')
    channel._state = ScanState(tmp_path / 'scan.sqlite3')
    channel._connected = True
    return channel


def test_channel_account_is_not_persona_or_credentials_and_relogin_rotates_binding(tmp_path):
    first = bind_account(tmp_path, 'xhs', 'main', 'ours', 'Product team')
    assert load_account(tmp_path, 'xhs', 'main') == first
    assert first.public() == {'channel': 'xhs', 'account_id': 'ours', 'username': 'Product team'}
    assert load_account(tmp_path, 'xhs', 'another') is None
    second = bind_account(tmp_path, 'xhs', 'main', 'ours', 'Product team')
    assert first.revision != second.revision
    assert not list(tmp_path.glob('**/IDENTITY.md'))
    raw = next(tmp_path.glob('channel-accounts/*.json')).read_text()
    assert 'token' not in raw and 'cookie' not in raw


@pytest.mark.parametrize('url', ['https://example.com', 'http://10.0.0.1', 'http://127.0.0.1/a',
                                'http://user:pass@localhost', 'http://localhost?token=secret'])
def test_backend_location_is_explicit_local_service(url):
    with pytest.raises(ValueError):
        XhsApi(url)


async def test_baseline_dedup_attribution_and_core_ack_loss(tmp_path):
    backend = Backend()
    backend.items = [notification('old')]
    channel = make_channel(tmp_path, backend)
    accepted = []
    lose_ack = True

    async def accept(value):
        nonlocal lose_ack
        accepted.append(value)
        if lose_ack:
            lose_ack = False
            raise OSError('ACK lost after durable intake')
    channel.set_on_accept(accept)
    try:
        await channel.poll_once()
        assert accepted == []
        backend.items = [notification('c1'), notification('old')]
        with pytest.raises(OSError):
            await channel.poll_once()
        await channel.poll_once()
        assert len(accepted) == 2
        assert accepted[0] == accepted[1]  # Stable ID and frozen body permit Core Inbox dedup.
        await channel.poll_once()
        assert len(accepted) == 2
        backend.items = [notification('c2', sender='B'), *backend.items]
        await channel.poll_once()
        a, b = channel.input_source(accepted[0]), channel.input_source(accepted[-1])
        assert a.sender_id == 'A' and b.sender_id == 'B' and a.account_id == b.account_id
        resolver = IdentityResolver()
        assert resolver.channel('main', a).principal != resolver.channel('main', b).principal
        assert 'nickname' not in a.__dataclass_fields__
        assert backend.writes == []
    finally:
        await channel.disconnect()


async def test_edited_self_and_incomplete_notifications_do_not_create_new_reply(tmp_path):
    backend = Backend()
    backend.items = [notification('old')]
    channel = make_channel(tmp_path, backend)
    seen = []
    async def accept(value):
        seen.append(value)
    channel.set_on_accept(accept)
    try:
        await channel.poll_once()
        backend.items = [notification('old', text='edited'), notification('self', sender=backend.account),
                         {'id': 'mention-only', 'from': {'user_id': 'A'}}]
        await channel.poll_once()
        assert seen == [] and channel.scan_gap
        bind_account(tmp_path, 'xhs', 'main', 'another-account', 'Other')
        with pytest.raises(DeliveryNotSent, match='binding changed'):
            await channel.poll_once()
    finally:
        await channel.disconnect()


async def test_reply_never_reports_http_success_as_delivered_and_no_ambiguous_retry(tmp_path):
    backend = Backend()
    backend.items = [notification('c1')]
    channel = make_channel(tmp_path, backend, replies_enabled=True)
    original = channel._comment(backend.items[0], channel.account)
    try:
        with pytest.raises(RuntimeError, match='delivery is unverified'):
            await channel.send_output(original, 'out1', 'reply')
        assert len(backend.writes) == 1
        backend.items = [notification('c1', sender='B')]
        with pytest.raises(DeliveryNotSent, match='revalidated'):
            await channel.send_output(original, 'out2', 'must not send')
        assert len(backend.writes) == 1
    finally:
        await channel.disconnect()


async def test_default_no_writes_and_wrong_backend_account_fail_closed(tmp_path):
    backend = Backend()
    backend.items = [notification('c1')]
    channel = make_channel(tmp_path, backend)
    original = channel._comment(backend.items[0], channel.account)
    try:
        assert channel.bound_publication('main') is None
        with pytest.raises(DeliveryNotSent, match='disabled'):
            await channel.send_output(original, 'out1', 'reply')
        backend.account = 'unexpected'
        with pytest.raises(DeliveryNotSent, match='differs'):
            await channel._verified()
        assert backend.writes == []
    finally:
        await channel.disconnect()


async def test_publish_uses_same_output_journal_unknown_not_replayed_and_not_a_chat_send(tmp_path):
    backend = Backend()
    channel = make_channel(tmp_path / 'data', backend, publish_enabled=True)
    registry = ChannelReplies()
    channel_id = registry.register(channel)
    inputs = await InputStore.open(tmp_path / 'inputs.sqlite3')
    outputs = OutputDelivery(await OutputStore.open(tmp_path / 'outputs.sqlite3'), registry.resolve)
    source = freeze_input(UserInput(group_id='main', content='publish this', channel='tui'),
                          tmp_path, message_id='input1')
    await inputs.enqueue(source)
    claimed = await inputs.claim(source.scope)
    record = await inputs.start(claimed, session_id='session1')
    port = OutboundMessages(record, registry, outputs, lambda: True)
    image = tmp_path / 'poster.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic-image')
    try:
        catalog = await port.channels()
        assert catalog['channels'][0]['operations'] == ['publish']
        assert catalog['channels'][0]['account_id'] == backend.account
        with pytest.raises(ValueError, match='message.publish'):
            await port.send('bad', channel_id, 'not a post')
        result = await port.publish('call1', channel_id, 'Title', 'Body', ['poster.png'])
        assert result['status'] == 'unknown'
        assert len(backend.writes) == 1 and backend.writes[0][0].endswith('/publish')
        assert backend.writes[0][1]['title'] == 'Title'
        assert (await port.publish('call1', channel_id, 'Title', 'Body', ['poster.png']))['status'] == 'unknown'
        await outputs.resume()
        assert len(backend.writes) == 1
        with pytest.raises(ValueError):
            await port.publish('call2', channel_id, 'Title', 'Body', ['https://internal.invalid/secret'])
    finally:
        await outputs.close()
        await inputs.close()
        await channel.disconnect()


async def test_external_actor_cannot_publish_even_with_a_forged_source_label(tmp_path):
    identity = IdentityResolver().channel('main', MessageSource('xhs', 'ours', 'A'))
    envelope = freeze_input(UserInput(group_id='main', content='publish', metadata={'role': 'admin'}),
                            tmp_path, message_id='external', identity=identity, identity_binding='channel')
    inputs = await InputStore.open(tmp_path / 'inputs.sqlite3')
    outputs = OutputDelivery(await OutputStore.open(tmp_path / 'outputs.sqlite3'), lambda row: None)
    await inputs.enqueue(envelope)
    record = await inputs.start(await inputs.claim(envelope.scope), session_id='s')
    port = OutboundMessages(record, ChannelReplies(), outputs, lambda: True)
    try:
        with pytest.raises(ValueError, match='Only group administrator'):
            await port.publish('call', 'forged', 'title', 'body', ['image.png'])
        assert await outputs.store.records() == []
    finally:
        await outputs.close()
        await inputs.close()


async def test_login_binds_verified_account_without_publishing_or_changing_persona(tmp_path):
    from xdog.claw.channels.xhs.login import login
    backend = Backend()
    api = XhsApi('http://127.0.0.1:18060', transport=httpx.MockTransport(backend))
    try:
        account = await login(api, tmp_path, 'main', lambda path: pytest.fail('Already logged in needs no QR'))
        assert account == load_account(tmp_path, 'xhs', 'main')
        assert account.account_id == backend.account and account.username == 'Product team'
        assert backend.writes == [] and not list(tmp_path.glob('**/PUBLIC.md'))
    finally:
        await api.close()


def test_channel_cli_requires_one_selection_and_xhs_login_preserves_unrelated_config(tmp_path, monkeypatch):
    import xdog.claw.channels.xhs.login as login_module
    import yaml
    from click.testing import CliRunner
    from xdog.claw.cli.cli import cli
    from xdog.claw.cli.xhs_login import run
    from xdog.claw.config import ClawConfig
    async def fake_login(api, data_dir, group_id, show_qr):
        return bind_account(data_dir, 'xhs', group_id, 'verified-account', 'Product team')
    monkeypatch.setattr(login_module, 'login', fake_login)
    path = tmp_path / 'config.yaml'
    path.write_text('custom-unrelated-key: keep\n')
    config = ClawConfig(data_dir=str(tmp_path / 'data'))
    run(config, path, 'main', '')
    saved = yaml.safe_load(path.read_text())
    assert saved['custom-unrelated-key'] == 'keep' and saved['xhs_enabled'] is True
    assert 'xhs_publish_enabled' not in saved and 'xhs_replies_enabled' not in saved
    result = CliRunner().invoke(cli, ['channel', 'login', '--xhs', '--weixin'])
    assert result.exit_code != 0 and 'exactly one' in result.output


async def test_frozen_post_media_is_not_a_mutable_workspace_reference(tmp_path):
    from xdog.claw.channels.xhs.media import freeze_images, verified_paths
    workspace, spool = tmp_path / 'workspace', tmp_path / 'spool'
    workspace.mkdir()
    source = workspace / 'poster.png'
    source.write_bytes(b'\x89PNG\r\n\x1a\noriginal')
    resources = freeze_images(['poster.png'], workspace, spool)
    source.write_bytes(b'\x89PNG\r\n\x1a\nchanged')
    paths = verified_paths(resources, spool)
    from pathlib import Path
    assert Path(paths[0]).read_bytes().endswith(b'original')
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'\x89PNG\r\n\x1a\nsecret')
    (workspace / 'link.png').symlink_to(outside)
    with pytest.raises(ValueError, match='outside'):
        freeze_images(['link.png'], workspace, spool)


async def test_public_xhs_adapter_rejects_even_explicit_admin_mapping(tmp_path):
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.identity import IdentityBinding
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group
    backend = Backend()
    channel = make_channel(tmp_path / 'data', backend)
    resolver = IdentityResolver((IdentityBinding('main', 'xhs', backend.account, 'A', 'owner', 'admin'),))
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'app'), identity_bindings=resolver.describe()))
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=()))
    app.add_channel(channel)
    try:
        original = channel._comment(notification('c1'), channel.account)
        with pytest.raises(ValueError, match='ceiling'):
            await channel._on_accept(original)
        inputs = await app.input_service()
        from xdog.claw.core.inputs.models import InputScope
        assert not await inputs.store.records(InputScope('main', tmp_path / 'workspace'))
        assert backend.writes == []
    finally:
        await app.stop()


async def test_malformed_notification_does_not_block_independent_valid_comment(tmp_path):
    backend = Backend()
    channel = make_channel(tmp_path, backend)
    accepted = []
    async def accept(value):
        accepted.append(value)
    channel.set_on_accept(accept)
    try:
        await channel.poll_once()
        backend.items = [notification('bad', text=''), notification('c1', text='valid')]
        await channel.poll_once()
        assert len(accepted) == 1 and accepted[0].content == 'valid'
        assert channel.scan_gap
    finally:
        await channel.disconnect()


async def test_public_comment_sends_only_one_final_reply_not_tool_progress(tmp_path):
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.inputs.models import InputScope
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group
    backend = Backend()
    backend.items = [notification('c1')]
    channel = make_channel(tmp_path / 'data', backend, replies_enabled=True)
    calls = []
    def model(name, context, options):
        calls.append(context)
        content = ((TextContent(text='internal progress'), ToolCall(id='denied', name='not-offered', arguments={}))
                   if len(calls) == 1 else (TextContent(text='Final public answer'),))
        return EventStream.empty(AssistantMessage(content=content,
                                                  stop_reason='toolUse' if len(calls) == 1 else 'stop'))
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'app')), model='test/dummy', stream_fn=model)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=()))
    app.add_channel(channel)
    try:
        message = channel._comment(backend.items[0], channel.account)
        await channel._on_accept(message)
        inputs = await app.input_service()
        receipt = await inputs.wait(InputScope('main', tmp_path / 'workspace'), message.metadata['channel_message_id'])
        assert receipt.status == 'succeeded' and len(calls) == 2
        assert backend.writes == [('/api/v1/notifications/reply',
                                   {'comment_id': 'c1', 'content': 'Final public answer'})]
        outputs = await app.output_service()
        rows = await outputs.store.records()
        assert len(rows) == 1 and rows[0].status == 'unknown'
    finally:
        await app.stop()


async def test_comments_received_while_replies_disabled_never_become_a_future_send_backlog(tmp_path):
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.inputs.models import InputScope
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group

    from packages.claw.tests.eyes_helpers import ScriptedModel
    backend = Backend()
    backend.items = [notification('c1')]
    channel = make_channel(tmp_path / 'data', backend)
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'app')), model='test/dummy', stream_fn=ScriptedModel())
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=()))
    app.add_channel(channel)
    try:
        message = channel._comment(backend.items[0], channel.account)
        await channel._on_accept(message)
        service = await app.input_service()
        receipt = await service.wait(InputScope('main', tmp_path / 'workspace'), message.metadata['channel_message_id'])
        assert receipt.status == 'succeeded' and receipt.envelope.context['reply_mode'] == 'none'
        outputs = await app.output_service()
        assert await outputs.store.records() == [] and backend.writes == []
        channel.replies_enabled = True
        await outputs.resume()
        assert await outputs.store.records() == [] and backend.writes == []
    finally:
        await app.stop()


async def test_ack_loss_then_reply_policy_change_keeps_original_no_reply_admission(tmp_path):
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.inputs.models import InputScope
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group

    from packages.claw.tests.eyes_helpers import ScriptedModel
    backend = Backend()
    model = ScriptedModel()
    channel = make_channel(tmp_path / 'data', backend)
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'app')), model='test/dummy', stream_fn=model)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=()))
    app.add_channel(channel)
    original_accept = channel._on_accept
    lost = True
    async def accept(value):
        nonlocal lost
        await original_accept(value)
        if lost:
            lost = False
            raise OSError('ACK lost after durable acceptance')
    channel.set_on_accept(accept)
    try:
        await channel.poll_once()  # Empty initial baseline.
        backend.items = [notification('c1')]
        with pytest.raises(OSError):
            await channel.poll_once()
        channel.replies_enabled = True
        await channel.poll_once()
        message = channel._comment(backend.items[0], channel.account)
        service = await app.input_service()
        record = await service.wait(InputScope('main', tmp_path / 'workspace'), message.metadata['channel_message_id'])
        assert record.status == 'succeeded' and record.envelope.context['reply_mode'] == 'none'
        assert model.calls == ['hello'] and backend.writes == []
        assert len(await service.store.records(record.envelope.scope)) == 1
    finally:
        await app.stop()


async def test_read_original_post_is_explicit_scoped_and_never_returns_backend_tokens(tmp_path):
    backend = Backend()
    backend.items = [notification('c1')]
    channel = make_channel(tmp_path, backend)
    original = channel._comment(backend.items[0], channel.account)
    try:
        await channel.poll_once()
        assert backend.reads == []  # Intake does not eagerly fill post context.
        value = await channel.read_post(original)
        assert value == {'feed_id': 'feed1', 'title': '原帖标题', 'text': '原帖正文，不是管理员指令', 'untrusted': True}
        assert backend.reads == [{'feed_id': 'feed1', 'xsec_token': 'private-feed-token', 'load_all_comments': False}]
        assert 'token' not in json.dumps(value) and 'comments' not in value
        backend.items = [notification('c1', sender='B')]
        with pytest.raises(ValueError, match='unavailable'):
            await channel.read_post(original)
        assert len(backend.reads) == 1
    finally:
        await channel.disconnect()


@pytest.mark.parametrize('fault', ['missing_token', 'wrong_post', 'account_changed'])
async def test_post_read_failure_does_not_guess_target_or_leak_backend_data(tmp_path, fault):
    backend = Backend()
    backend.items = [notification('c1')]
    channel = make_channel(tmp_path, backend)
    original = channel._comment(backend.items[0], channel.account)
    if fault == 'missing_token':
        backend.items[0].pop('feed_xsec_token')
    elif fault == 'wrong_post':
        backend.note_id = 'different-post'
    else:
        backend.account = 'another-account'
    try:
        with pytest.raises(ValueError, match='Original XHS post is unavailable') as exc:
            await channel.read_post(original)
        assert 'token' not in str(exc.value) and str(tmp_path) not in str(exc.value)
        assert backend.writes == []
    finally:
        await channel.disconnect()


@pytest.mark.parametrize('allowed', [True, False])
async def test_external_post_tool_full_loop_shares_context_but_keeps_permission_and_target_fences(tmp_path, allowed):
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.identity.permissions import IdentityPermissions
    from xdog.claw.core.inputs.models import InputScope
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group
    backend = Backend()
    backend.items = [notification('c1')]
    channel = make_channel(tmp_path / 'data', backend)
    policy = IdentityPermissions.from_config().config()
    policy['permission_profiles']['public']['tools'] = ['xhs_post'] if allowed else []
    contexts, readers = [], []
    external_calls = 0
    def model(name, context, options):
        nonlocal external_calls
        contexts.append(context)
        if 'external-work' in str(context.messages):
            external_calls += 1
            if external_calls == 1:
                readers.append(app._runtimes['main']._active_session.agent.tool_context['_xhs_post_reader'])
                return EventStream.empty(AssistantMessage(content=(
                    ToolCall(id='post', name='xhs_post', arguments={}),), stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='answer'),)))
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'app')), model='test/dummy', stream_fn=model)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=('xhs_post',),
        permissions=IdentityPermissions.from_config(policy['permission_profiles'], defaults=policy['defaults'])))
    app.add_channel(channel)
    try:
        await app.route_message(UserInput(group_id='main', content='admin-private-secret'))
        if allowed:
            app._runtimes['main']._active_session.access.set_read_only(True)
        backend.items[0]['comment_text'] = 'external-work'
        message = channel._comment(backend.items[0], channel.account)
        await channel._on_accept(message)
        inputs = await app.input_service()
        receipt = await inputs.wait(InputScope('main', tmp_path / 'workspace'), message.metadata['channel_message_id'])
        assert receipt.status == 'succeeded' and external_calls == 2
        assert len(backend.reads) == int(allowed)
        for context in contexts[1:]:
            assert {tool.name for tool in context.tools} == ({'xhs_post'} if allowed else set())
            assert 'admin-private-secret' in str(context)
            assert 'private-feed-token' not in str(context) and 'private-backend-token' not in str(context)
        results = [row for row in contexts[-1].messages if isinstance(row, ToolResultMessage)]
        assert len(results) == 1 and results[0].is_error is not allowed
        assert ('原帖正文' in str(results[0].content)) is allowed
        other = IdentityResolver().channel('main', MessageSource('xhs', backend.account, 'B', 'c2',
                                                                  resource_id='different-post'))
        await app.route_message(UserInput(group_id='main', content='other thread'), host_identity=other,
                                identity_binding='channel')
        assert ('原帖正文' in str(contexts[-1])) is allowed
        assert 'external-work' in str(contexts[-1])
        assert any(isinstance(row, ToolResultMessage) for row in contexts[-1].messages)
        assert backend.writes == []
        with pytest.raises(ValueError, match='owner or permissions changed'):
            await readers[0]()  # An old reader cannot be reused after its input settles.
    finally:
        await app.stop()
