"""Explicit single-channel sends use actual host bindings and durable output receipts."""
from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from acp import PROTOCOL_VERSION, connect_to_agent
from acp.schema import TextContentBlock
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.channel import NativeNoticeChannel, TuiChannel
from xdog.claw.channels.weixin.channel import WeixinChannel
from xdog.claw.channels.weixin.context_tokens import clear_context_tokens, set_context_token
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.inputs.store import InputRecord, InputStore
from xdog.claw.core.outputs.delivery import OutputDelivery
from xdog.claw.core.outputs.store import OutputStore
from xdog.claw.core.runtime.channel_adapter import ChannelReplies
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.runtime.outbound_messages import OutboundMessages
from xdog.claw.core.tools.tool_message import create_message_tool
from xdog.claw.core.types import UserInput

from packages.claw.tests.test_weixin_application_intake import ChannelApi, sent_parts


def result_text(result):
    return ''.join(part.text for part in result.content if isinstance(part, TextContent))


@dataclass
class Host:
    record: InputRecord
    inputs: InputStore
    registry: ChannelReplies
    outputs: OutputDelivery
    weixin: WeixinChannel
    api: ChannelApi
    native: NativeNoticeChannel
    wx_id: str
    tui_id: str
    valid: bool = True
    ready: bool = True
    notices: list = field(default_factory=list)

    def port(self):
        return OutboundMessages(self.record, self.registry, self.outputs, lambda: self.valid)


@pytest.fixture
async def host(tmp_path):
    """Only external Weixin IO is fake: both journals, binding and delivery are real."""
    account = 'outbound-tests'
    clear_context_tokens(account)
    inputs = await InputStore.open(tmp_path / 'inputs.sqlite3')
    source = freeze_input(UserInput(group_id='main', channel='acp', content='actual operation'),
                          tmp_path, message_id='original-input')
    await inputs.enqueue(source)
    claimed = await inputs.claim(source.scope)
    assert claimed is not None
    record = await inputs.start(claimed, session_id='original-session')
    registry = ChannelReplies()
    weixin = WeixinChannel(tmp_path / 'weixin', account, 'https://example.invalid', 'private-auth')
    await weixin._api_client.close()
    api = ChannelApi()
    weixin._api_client = api
    weixin._user_id_map['main'] = 'alice'
    set_context_token(account, 'alice', 'private-context')
    notices = []

    async def publish(frame):
        notices.append(frame)

    native = NativeNoticeChannel(publish)
    native.bind_group('main')
    wx_id, tui_id = registry.register(weixin), registry.register(native)
    store = await OutputStore.open(tmp_path / 'outputs.sqlite3')
    lane = None

    async def gate(row):
        return 'ready' if lane.ready else 'pending'

    delivery = OutputDelivery(store, registry.resolve, gate=gate)
    lane = Host(record, inputs, registry, delivery, weixin, api, native, wx_id, tui_id, notices=notices)
    try:
        yield lane
    finally:
        await delivery.close()
        await native.disconnect()
        await weixin.disconnect()
        await inputs.close()
        clear_context_tokens(account)


@pytest.mark.parametrize('selected', ['weixin', 'tui'])
async def test_catalog_only_exposes_current_bound_channel_ids_and_send_selects_one(host, tmp_path, selected):
    other = WeixinChannel(tmp_path / 'other', 'other-account', 'https://example.invalid', '', group_id='other')
    await other._api_client.close()
    other._user_id_map['other'] = 'bob'
    other_id = host.registry.register(other)
    stdout_id = host.registry.register(TuiChannel())
    port = host.port()
    catalog = await port.channels()
    assert catalog['channels'] == [{'id': host.wx_id, 'name': 'weixin'}, {'id': host.tui_id, 'name': 'tui'}]
    assert all(set(row) == {'id', 'name'} for row in catalog['channels'])
    assert all(secret not in json.dumps(catalog) for secret in (
        'private-auth', 'private-context', 'alice', 'bob', other_id, stdout_id, 'native_bound_target',
    ))
    channel = host.wx_id if selected == 'weixin' else host.tui_id
    receipt = await port.send('one-call', channel, 'selected only')
    assert receipt['status'] == 'delivered' and receipt['channel'] == channel
    assert receipt['default_reply_route_changed'] is False
    rows = await host.outputs.store.records()
    assert len(rows) == 1 and rows[0].binding == channel
    assert receipt['outputs'] == [{'id': rows[0].id, 'status': 'delivered'}]
    assert rows[0].envelope.scope == host.record.envelope.scope
    assert rows[0].envelope.human_authorized is False
    if selected == 'weixin':
        sent = host.api.sent.get_nowait()
        assert sent_parts(sent) == ('alice', 'private-context', 'selected only')
        assert sent.client_id == rows[0].id and host.notices == []
    else:
        assert host.notices == [{'type': 'notification', 'group_id': 'main', 'notification_id': rows[0].id,
                                 'content': 'selected only'}]
    assert host.api.sent.empty()
    assert (await host.inputs.get(host.record.envelope.scope, 'original-input')) == host.record


@pytest.mark.parametrize('selected', ['weixin', 'tui'])
@pytest.mark.parametrize('scope', ['another-port', 'child'])
async def test_new_send_cannot_reuse_a_channel_id_listed_in_another_scope(host, selected, scope):
    original = host.port()
    await original.channels()
    port = original.for_child('new-child') if scope == 'child' else host.port()
    channel = host.wx_id if selected == 'weixin' else host.tui_id
    with pytest.raises(ValueError, match='list|channels'):
        await port.send('history-or-event-channel-id', channel, 'not yet authorized by a fresh listing')
    assert await host.outputs.store.records() == [] and host.api.sent.empty() and host.notices == []
    await port.channels()
    assert (await port.send('fresh-scope-call', channel, 'listed here'))['status'] == 'delivered'
    assert len(await host.outputs.store.records()) == 1


async def test_new_channel_registered_after_listing_is_not_an_offered_destination(host):
    port = host.port()
    await port.channels()
    notices = []

    async def publish(frame):
        notices.append(frame)

    later = NativeNoticeChannel(publish)
    later.bind_group('main')
    binding = host.registry.register(later)
    try:
        with pytest.raises(ValueError, match='list|channels'):
            await port.send('unlisted-channel', binding, 'not offered')
        assert await host.outputs.store.records() == [] and notices == []
        await port.channels()
        assert (await port.send('listed-channel', binding, 'now offered'))['status'] == 'delivered'
        assert len(notices) == 1 and host.notices == [] and host.api.sent.empty()
    finally:
        await later.disconnect()


async def test_stable_call_id_is_idempotent_in_parallel_and_across_port_reconstruction(host):
    port = host.port()
    await port.channels()
    first, second = await asyncio.gather(
        port.send('same-call', host.wx_id, 'once'), port.send('same-call', host.wx_id, 'once'))
    assert first == second == await host.port().send('same-call', host.wx_id, 'once')
    assert host.api.sent.qsize() == 1 and len(await host.outputs.store.records()) == 1
    await host.outputs.resume()
    assert host.api.sent.qsize() == 1 and host.notices == []


@pytest.mark.parametrize('change', ['channel', 'text', 'reference'])
async def test_stable_call_id_cannot_change_channel_text_or_reference(host, change):
    port = host.port()
    await port.channels()
    await port.send('same-call', host.wx_id, 'original', reference='issue-1')
    before = await host.outputs.store.records()
    with pytest.raises(ValueError, match='another channel or content'):
        await port.send('same-call', host.tui_id if change == 'channel' else host.wx_id,
                        'changed' if change == 'text' else 'original',
                        reference='issue-2' if change == 'reference' else 'issue-1')
    assert await host.outputs.store.records() == before
    assert host.api.sent.qsize() == 1 and host.notices == []


@pytest.mark.parametrize('change', ['peer', 'context'])
async def test_listed_weixin_binding_change_requires_fresh_catalog_before_commit(host, change):
    port = host.port()
    await port.channels()
    if change == 'peer':
        host.weixin._user_id_map['main'] = 'bob'
    else:
        set_context_token(host.weixin._account_id, 'alice', 'changed-context')
    with pytest.raises(ValueError, match='recipient changed'):
        await port.send('stale-catalog', host.wx_id, 'no')
    assert await host.outputs.store.records() == [] and host.api.sent.empty()
    await port.channels()
    assert (await port.send('fresh-catalog', host.wx_id, 'yes'))['status'] == 'delivered'
    sent = host.api.sent.get_nowait()
    assert (sent.to_user_id, sent.context_token) == (
        ('bob', '') if change == 'peer' else ('alice', 'changed-context'))


@pytest.mark.parametrize('retire', ['switch', 'disconnect', 'new-connection'])
async def test_listed_native_target_cannot_follow_a_group_or_peer_change(host, retire):
    port = host.port()
    await port.channels()
    if retire == 'switch':
        host.native.bind_group('other')
        host.native.bind_group('main')
    else:
        await host.native.disconnect()
    if retire == 'new-connection':
        host.registry.unregister(host.tui_id)
        replacement = NativeNoticeChannel(host.native._publish)
        replacement.bind_group('main')
        assert host.registry.register(replacement) != host.tui_id
    with pytest.raises(ValueError, match='recipient changed|not currently bound'):
        await port.send('old-target', host.tui_id, 'never forward')
    assert await host.outputs.store.records() == [] and host.notices == [] and host.api.sent.empty()


@pytest.mark.parametrize('retire', ['switch', 'disconnect', 'new-connection'])
async def test_committed_native_output_stays_pending_after_original_target_retires(host, retire):
    host.ready = False
    port = host.port()
    await port.channels()
    receipt = await port.send('committed', host.tui_id, 'original peer only')
    assert receipt['status'] == 'pending' and len(receipt['outputs']) == 1
    if retire == 'switch':
        host.native.bind_group('other')
        host.native.bind_group('main')
    else:
        await host.native.disconnect()
    if retire == 'new-connection':
        host.registry.unregister(host.tui_id)
        replacement = NativeNoticeChannel(host.native._publish)
        replacement.bind_group('main')
        host.registry.register(replacement)
    host.ready = True
    await host.outputs.resume()
    row, = await host.outputs.store.records()
    assert row.status == 'pending' and row.id == receipt['outputs'][0]['id']
    if retire != 'new-connection':
        assert row.error and row.send_id  # Actual transport proved not-sent, not outcome-unknown.
    assert (await port.send('committed', host.tui_id, 'original peer only'))['status'] == 'pending'
    assert host.notices == [] and host.api.sent.empty()


async def test_committed_weixin_output_freezes_original_peer_account_and_context(host):
    host.ready = False
    port = host.port()
    await port.channels()
    receipt = await port.send('committed', host.wx_id, 'for original')
    assert receipt['status'] == 'pending'
    host.weixin._user_id_map['main'] = 'bob'
    set_context_token(host.weixin._account_id, 'alice', 'later-context')
    set_context_token(host.weixin._account_id, 'bob', 'bobs-context')
    host.ready = True
    await host.outputs.resume()
    sent = host.api.sent.get_nowait()
    assert sent_parts(sent) == ('alice', 'private-context', 'for original')
    assert sent.client_id == receipt['outputs'][0]['id']
    row, = await host.outputs.store.records()
    assert row.status == 'delivered' and row.envelope.context['metadata']['account_id'] == host.weixin._account_id
    repeated = await host.port().send('committed', host.wx_id, 'for original')
    assert repeated['status'] == 'delivered' and repeated['message_id'] == receipt['message_id']
    assert host.api.sent.empty() and host.notices == []


async def test_unknown_delivery_is_never_resent_by_repeat_resume_or_journal_reopen(host):
    async def ack_lost(request):
        host.api.sent.put_nowait(request.msg)
        raise ConnectionError('upstream accepted; acknowledgement lost')

    host.api.send_message = ack_lost
    port = host.port()
    await port.channels()
    receipt = await port.send('unknown-call', host.wx_id, 'possibly delivered')
    assert receipt['status'] == 'unknown'
    assert await port.send('unknown-call', host.wx_id, 'possibly delivered') == receipt
    await host.outputs.resume()
    assert host.api.sent.qsize() == 1 and host.notices == []
    path = host.outputs.store.path
    await host.outputs.close()
    reopened = await OutputStore.open(path)
    host.outputs = OutputDelivery(reopened, host.registry.resolve)
    assert await host.port().send('unknown-call', host.wx_id, 'possibly delivered') == receipt
    await host.outputs.resume()
    row, = await reopened.records()
    assert row.status == 'unknown' and host.api.sent.qsize() == 1 and host.notices == []


@pytest.mark.parametrize('already_sent', [False, True])
async def test_invalid_actual_owner_refuses_catalog_and_send_including_idempotent_repeat(host, already_sent):
    port = host.port()
    await port.channels()
    if already_sent:
        await port.send('call', host.wx_id, 'text')
    before = await host.outputs.store.records()
    host.valid = False
    with pytest.raises(ValueError, match='owner or permissions'):
        await port.channels()
    with pytest.raises(ValueError, match='owner or permissions'):
        await port.send('call', host.wx_id, 'text')
    assert await host.outputs.store.records() == before
    assert host.api.sent.qsize() == int(already_sent) and host.notices == []


async def test_cancelled_send_never_creates_an_output(host):
    cancel = asyncio.Event()
    cancel.set()
    port = host.port()
    await port.channels()
    with pytest.raises(asyncio.CancelledError):
        await port.send('cancelled', host.wx_id, 'no', cancel)
    assert await host.outputs.store.records() == [] and host.api.sent.empty() and host.notices == []


@pytest.mark.parametrize('extra', [{'group_id': 'other'}, {'recipient': 'bob'}, {'multicast': True}])
async def test_message_tool_rejects_legacy_or_model_supplied_addresses(host, extra):
    tool = create_message_tool()
    assert not {'group_id', 'recipient', 'multicast'} & set(tool.parameters['properties'])
    result = await tool.execute('bad-address', {'action': 'send', 'channel': host.wx_id, 'text': 'no', **extra},
                                ctx={'_message_port': host.port()})
    assert result.is_error and 'not allowed' in result_text(result)
    assert await host.outputs.store.records() == [] and host.api.sent.empty() and host.notices == []


async def test_message_reference_is_only_output_provenance_not_routing_authority(host):
    tool = create_message_tool()
    port = host.port()
    catalog = await tool.execute('catalog', {'action': 'channels'}, ctx={'_message_port': port})
    assert not catalog.is_error
    channel, = [row['id'] for row in json.loads(result_text(catalog))['channels'] if row['name'] == 'weixin']
    reference = 'group:other/recipient:bob'
    params = {'action': 'send', 'channel': channel, 'text': 'business-linked message', 'reference': reference}
    result = await tool.execute('linked-call', params, ctx={'_message_port': port})
    assert not result.is_error, result_text(result)
    receipt = json.loads(result_text(result))
    assert receipt['status'] == 'delivered'
    row, = await host.outputs.store.records()
    assert row.envelope.message.provenance['reference'] == reference
    assert row.envelope.scope == host.record.envelope.scope
    assert row.envelope.reply.channel == 'weixin' and row.envelope.reply.recipient == 'alice'
    assert 'reference' not in row.envelope.context and 'reference' not in row.envelope.context['metadata']
    assert sent_parts(host.api.sent.get_nowait()) == ('alice', 'private-context', 'business-linked message')
    # Same-call receipt lookup is allowed without listing, even through a reconstructed tool scope.
    duplicate = await tool.execute('linked-call', params, ctx={'_message_port': host.port()})
    assert not duplicate.is_error and json.loads(result_text(duplicate)) == receipt
    assert host.api.sent.empty() and host.notices == []


@pytest.mark.parametrize('reference', ['', '  ', 'bad\x00reference', '中' * 86, 42, {'group_id': 'other'}])
async def test_message_tool_rejects_invalid_reference_without_writing_outputs(host, reference):
    port = host.port()
    await port.channels()
    result = await create_message_tool().execute('invalid-reference', {
        'action': 'send', 'channel': host.wx_id, 'text': 'no', 'reference': reference,
    }, ctx={'_message_port': port})
    assert result.is_error and 'reference must be bounded nonempty text' in result_text(result)
    assert await host.outputs.store.records() == [] and host.api.sent.empty() and host.notices == []


async def test_child_port_call_namespace_cannot_consume_parent_or_another_child_send(host):
    parent = host.port()
    first, second = parent.for_child('child-one'), parent.for_child('child-two')
    for port in (parent, first, second):
        await port.channels()
    receipts = await asyncio.gather(*(port.send('same-call', host.wx_id, 'one effect per owner')
                                      for port in (parent, first, second)))
    assert len({value['message_id'] for value in receipts}) == 3
    assert host.api.sent.qsize() == 3 and len(await host.outputs.store.records()) == 3
    assert await first.send('same-call', host.wx_id, 'one effect per owner') == receipts[1]
    assert host.api.sent.qsize() == 3 and host.notices == []


@pytest.mark.parametrize('transport', ['prompt', 'enqueue'])
@pytest.mark.parametrize('explicit_send', [False, True])
async def test_main_agent_real_acp_message_tool_sends_weixin_but_plain_reply_stays_on_display(
    tmp_path, monkeypatch, transport, explicit_send,
):
    """Actual ACP Unix peer -> Core Inbox -> main Agent -> tool -> output journal."""
    contexts, tool_results = [], []
    ordinary = 'Ordinary ACP/display answer, not a Weixin message'
    proactive = 'Explicit Weixin message, not the ordinary reply'

    def model(model_id, context, options=None):
        contexts.append(context)
        last = context.messages[-1]
        if last.role == 'user' and explicit_send:
            message = AssistantMessage(content=(
                ToolCall(id='catalog', name='message', arguments={'action': 'channels'}),))
        elif isinstance(last, ToolResultMessage):
            assert not last.is_error, result_text(last)
            result = json.loads(result_text(last))
            tool_results.append(result)
            if last.tool_call_id == 'catalog':
                channel, = [row['id'] for row in result['channels'] if row['name'] == 'weixin']
                message = AssistantMessage(content=(ToolCall(id='explicit-send', name='message', arguments={
                    'action': 'send', 'channel': channel, 'text': proactive, 'reference': 'issue:acp-42'}),))
            else:
                message = AssistantMessage(content=(TextContent(text=ordinary),))
        else:
            message = AssistantMessage(content=(TextContent(text=ordinary),))
        return EventStream.empty(message)

    class Client:
        def __init__(self):
            self.updates = []

        async def session_update(self, session_id, update, **kwargs):
            self.updates.append((session_id, update))

    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options', lambda config: ('test/dummy', model))
    clear_context_tokens('acp-outbound')
    with tempfile.TemporaryDirectory(prefix='claw-outbound-') as private:
        endpoint = Path(private) / 'acp.sock'
        config = ClawConfig(model='test/dummy', data_dir=str(tmp_path / 'data'), enabled_tools=('message',),
                            socket_path=str(tmp_path / 'native.sock'), pid_file=str(tmp_path / 'native.pid'),
                            acp_socket_path=str(endpoint), groups=(GroupDef(id='main', workspace=str(tmp_path)),))
        gateway = GatewayServer(config)
        await gateway.start()
        wx = WeixinChannel(tmp_path / 'weixin', 'acp-outbound', 'https://example.invalid', '')
        await wx._api_client.close()
        api = ChannelApi()
        wx._api_client = api
        wx._user_id_map['main'] = 'bound-weixin-peer'
        set_context_token('acp-outbound', 'bound-weixin-peer', 'original-acp-context')
        gateway._orchestrator.add_channel(wx)
        writer = tui = None
        try:
            tui_reader, tui = await asyncio.open_unix_connection(config.socket_path)
            tui.write(b'{"type":"ping","group_id":"main","acp_updates":1}\n')
            await tui.drain()
            assert json.loads(await asyncio.wait_for(tui_reader.readline(), 5))['type'] == 'pong'
            reader, writer = await asyncio.open_unix_connection(str(endpoint))
            client = Client()
            conn = connect_to_agent(client, writer, reader)
            await conn.initialize(protocol_version=PROTOCOL_VERSION)
            await conn.load_session(session_id='group:main', cwd=str(tmp_path), mcp_servers=[])
            if transport == 'prompt':
                reply = await asyncio.wait_for(conn.prompt(session_id='group:main', prompt=[
                    TextContentBlock(type='text', text='Perform this admitted outbound test')]), 8)
                assert reply.stop_reason == 'end_turn'
                assert client.updates == []  # ACP ingress has no default chat reply.
                input_id = reply.field_meta['xdog']['message_id']
            else:
                input_id = 'acp-outbound-input'
                receipt = await conn.ext_method('xdog/enqueue', {
                    'session_id': 'group:main', 'cwd': str(tmp_path), 'message_id': input_id,
                    'source': 'Test', 'instruction': 'Perform this admitted outbound test',
                    'evidence': [], 'provenance': {},
                })
                assert receipt['status'] == 'queued'
            notice = json.loads(await asyncio.wait_for(tui_reader.readline(), 8))
            assert notice['type'] == 'acp_input_completed' and notice['message_id'] == input_id
            assert notice['response'] == ordinary and notice['group_id'] == 'main'
            if transport == 'enqueue':
                assert client.updates == []  # Unrelated enqueue is not an ACP prompt reply.
            app = gateway._orchestrator
            record, = await (await app.input_service()).store.records(InputScope("main", tmp_path))
            assert record.status == 'succeeded' and not record.envelope.human_authorized
            assert record.envelope.message.origin == 'acp' and record.response == ordinary
            rows = await (await app.output_service()).store.records()
            assert len(rows) == int(explicit_send)
            if explicit_send:
                sent = api.sent.get_nowait()
                assert sent_parts(sent) == ('bound-weixin-peer', 'original-acp-context', proactive)
                assert sent.client_id == rows[0].id and rows[0].status == 'delivered'
                assert rows[0].envelope.message.provenance['input_id'] == input_id
                assert rows[0].envelope.message.provenance['reference'] == 'issue:acp-42'
                assert tool_results[-1]['outputs'] == [{'id': rows[0].id, 'status': 'delivered'}]
                assert tool_results[-1]['default_reply_route_changed'] is False
            assert api.sent.empty()
            assert len(contexts) == (3 if explicit_send else 1)
            session = app._runtimes['main']._active_session
            assert session.agent.tool_context['_message_port'] is None
            assert session.agent.tool_context['_reply_reference'] is None
            # Flush the Native stream: no explicit-send notification or broadcast ordinary reply preceded pong.
            tui.write(b'{"type":"ping","group_id":"main"}\n')
            await tui.drain()
            assert json.loads(await asyncio.wait_for(tui_reader.readline(), 5))['type'] == 'pong'
        finally:
            for stream in (writer, tui):
                if stream is not None:
                    stream.close()
                    await stream.wait_closed()
            await gateway.stop()
            clear_context_tokens('acp-outbound')
