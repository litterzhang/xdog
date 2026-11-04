"""Identity/routing labels are evidence, not authority or delivery guesses."""
from dataclasses import replace

from xdog.claw.channels.tui.message_metadata import clean_metadata, label
from xdog.claw.channels.tui.tui_app import ChatApp, ChatLog
from xdog.claw.core.identity import IdentityResolver, MessageSource
from xdog.claw.core.inputs.store import InputRecord
from xdog.claw.core.outputs.store import OutputRecord
from xdog.claw.core.runtime.group import _display_history_entries
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.runtime.message_metadata import enrich_history, envelope_metadata
from xdog.claw.core.types import UserInput
from xdog.tui.utils import string_width, strip_ansi

from packages.claw.tests.test_gateway_inputs import gateway as gateway
from packages.claw.tests.test_gateway_inputs import receive, send


def rendered(log):
    return '\n'.join(strip_ansi(row) for row in log.render(180))


def test_identity_markers_follow_turn_not_history_frame_or_legacy_user(tmp_path):
    identity = IdentityResolver.local(MessageSource('tui'))
    marker = {'role': 'custom', 'type': 'xdog.claw.actor.v1',
              'data': {'identity': identity.to_dict(), 'input_id': 'native:one'}}
    rows = _display_history_entries([marker, {'role': 'user', 'content': 'new'},
        {'role': 'assistant', 'content': 'answer'}, {'role': 'user', 'content': 'legacy'},
        {'role': 'assistant', 'content': 'old answer'}], 'cats')
    assert rows[0]['message_metadata']['role'] == 'admin'
    assert rows[1]['message_metadata']['input_id'] == 'native:one'
    assert rows[2]['message_metadata'] == rows[3]['message_metadata'] == {}
    malformed = {**marker, 'data': {'identity': {'role': 'admin'}, 'input_id': 'forged'}}
    assert _display_history_entries([malformed, {'role': 'user', 'content': 'bad'}])[0]['message_metadata'] == {}


def test_output_labels_join_original_input_attempt_and_exact_message(tmp_path):
    message = UserInput(group_id='cats', channel='xhs', sender='commenter', content='hello')
    identity = IdentityResolver.local(MessageSource('tui'))
    envelope = freeze_input(message, tmp_path, message_id='one', identity=identity, reply_mode='final')
    record = InputRecord(envelope, 'succeeded', attempt_id='attempt', session_id='session')
    output = OutputRecord('output', envelope, 'attempt', 0, 'binding', 'answer', 'unknown', '', '')
    history = [{'role': 'assistant', 'content': text, 'message_metadata': {'input_id': 'one'}}
               for text in ['progress', 'answer']]
    enrich_history(history, [record], [output], 'session')
    assert history[0]['message_metadata']['destinations'] == [{'channel': 'xhs', 'status': 'not_recorded'}]
    assert history[1]['message_metadata']['destinations'][0]['status'] == 'unknown'
    enrich_history(history, [record], [replace(output, attempt_id='earlier', status='delivered')], 'session')
    assert history[1]['message_metadata']['destinations'][0]['status'] == 'not_recorded'


def test_acp_has_no_invented_recipient_and_disabled_route_is_not_delivery(tmp_path):
    from xdog.agent.session_input import SessionInput
    from xdog.claw.core.inputs.models import InputEnvelope
    envelope = freeze_input(UserInput(group_id='cats', channel='xhs', sender='person', content='hello'),
                            tmp_path, message_id='comment', reply_mode='none')
    assert envelope_metadata(envelope)['destinations'] == [{'channel': 'xhs', 'status': 'disabled'}]
    automatic = InputEnvelope(envelope.scope, SessionInput('event', 'acp', 'eyes', 'update', (), {}))
    assert envelope_metadata(automatic)['destinations'] == []


def test_rendered_labels_keep_source_text_markers_and_user_background():
    metadata = {'role': 'external_user', 'sender': 'commenter-A', 'source': 'xhs', 'group': 'cats',
                'destinations': [{'channel': 'xhs', 'status': 'unknown'}]}
    log = ChatLog()
    log.add_user('original comment', metadata=metadata)
    log.add_assistant('reply', metadata=metadata, thinking='reason')
    output = rendered(log)
    assert 'external_user:commenter-A · xhs' in output
    assert 'agent · xhs → xhs [未确认] · external_user:commenter-A' in output
    assert 'thinking · xhs' in output
    assert ' › original comment' in output and ' • reply' in output
    assert [r.body for r in log.transcript_records()] == ['original comment', 'reason', 'reply']
    for width in (1, 3, 10, 40):
        assert all(string_width(row) <= width for row in log.render(width))


def test_streamed_metadata_is_owner_bound_and_final_updates_without_duplicate_text():
    log = ChatLog()
    a = {'source': 'tui', 'role': 'admin', 'destinations': [{'channel': 'tui', 'status': 'local'}]}
    b = {'source': 'xhs', 'role': 'external_user', 'destinations': [{'channel': 'xhs', 'status': 'unknown'}]}
    log.update_assistant('first', 'a', metadata=a)
    log.update_assistant('second', 'b', metadata=b)
    log.finalize_assistant('final first', 'a', metadata=a)
    assert rendered(log).count('final first') == 1
    assert '→ tui [本地显示]' in rendered(log) and '→ xhs [未确认]' in rendered(log)
    assert log._streaming_runs['b'].normal_message.metadata['role'] == 'external_user'


def test_external_channel_live_display_is_not_steering_or_editor_history():
    app = ChatApp('/tmp/unused')
    app._gateway_session_id = 'session'
    app._editor.set_text('draft')
    event = {'type': 'channel_input_completed', 'group_id': 'main', 'session_id': 'session',
             'message_id': 'comment', 'source': 'xhs', 'instruction': 'public comment',
             'response': 'public answer', 'error': '', 'status': 'succeeded',
             'message_metadata': {'role': 'external_user', 'source': 'xhs', 'sender': 'person-A',
                                  'destinations': [{'channel': 'xhs', 'status': 'unknown'}]}}
    app._handle_response(event)
    app._handle_response(event)
    assert rendered(app._chat_log).count('public answer') == 1
    assert 'external_user' in rendered(app._chat_log) and '→ xhs [未确认]' in rendered(app._chat_log)
    assert app._editor.get_text() == 'draft' and app._active_run_id is None
    assert 'public comment' not in str(app._editor._history)


def test_message_tool_receipt_displays_named_channel_not_changes_default_reply():
    log = ChatLog()
    metadata = {'source': 'tui', 'role': 'admin', 'destinations': [{'channel': 'tui', 'status': 'local'}]}
    log.add_tool('message', {'action': 'publish'}, tool_call_id='post', metadata=metadata)
    log.finish_tool('post', 'message', '{"channel":"opaque-binding","channel_name":"xhs","status":"unknown"}')
    log.add_assistant('post summary', metadata=metadata)
    output = rendered(log)
    assert 'tool · tui → xhs [未确认] · admin' in output
    assert 'agent · tui → tui [本地显示] · admin' in output


def test_metadata_excludes_raw_private_fields_and_terminal_controls():
    value = clean_metadata({'role': 'admin\x1b[2J', 'sender': 'A\nB', 'token': 'private',
                            'destinations': [{'channel': 'xhs', 'status': []}]})
    assert value['sender'] == 'A B' and 'token' not in value
    assert '\x1b' not in label(value, 'agent')
    assert '已送达' not in label(value, 'agent')


def test_history_sync_completion_merges_without_losing_or_duplicating_reply():
    app = ChatApp('/tmp/unused')
    app._gateway_session_id = 'session'
    app._history_loading = True
    metadata = {'input_id': 'comment', 'role': 'external_user', 'source': 'xhs', 'sender': 'A'}
    app._replay_history_entries([{'role': 'user', 'channel': 'xhs', 'content': 'comment',
                                  'message_metadata': metadata}])
    event = {'type': 'channel_input_completed', 'group_id': 'main', 'session_id': 'session',
             'message_id': 'comment', 'source': 'xhs', 'instruction': 'comment', 'response': 'progress\n\nfinal',
             'error': '', 'status': 'succeeded', 'message_metadata': metadata}
    app._handle_response(event)
    app._replay_history_entries([{'role': 'assistant', 'content': 'progress', 'message_metadata': metadata}])
    app._handle_response({'type': 'history_end'})
    bodies = [record.body for record in app._chat_log.transcript_records()]
    assert bodies == ['comment', 'progress', 'final']
    assert app._active_run_id is None and not app._pending_input_views




async def test_live_channel_view_and_history_use_actual_identity_and_delivery(gateway):
    import asyncio

    from xdog.claw.channels.base import Channel

    class TestChannel(Channel):
        name = 'test-channel'

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def send_message(self, group_id, text):
            pass

    server, _entered, _release, _contexts, endpoint = gateway
    channel = TestChannel()
    app = server._orchestrator
    app.add_channel(channel)
    reader, writer = await asyncio.open_unix_connection(endpoint)
    try:
        await send(writer, {'type': 'ping', 'group_id': 'main', 'input_updates': 1})
        await receive(reader, 'pong')
        await channel._on_accept(UserInput(group_id='main', channel=channel.name, sender='actual-A', content='hello',
            metadata={'account_id': 'account', 'channel_message_id': 'new-comment'}))
        notice = await receive(reader, 'channel_input_completed')
        metadata = notice['message_metadata']
        assert metadata['role'] == 'external_user' and metadata['sender'] == 'actual-A'
        assert metadata['source'] == 'test-channel' and metadata['group'] == 'main'
        assert metadata['destinations'][0]['channel'] == 'test-channel'
        assert metadata['destinations'][0]['status'] == 'delivered'
        history = [row for row in (await app.read_session_info('main'))['history']
                   if row['role'] != 'system']
        assert history[0]['message_metadata']['input_id'] == 'new-comment'
        assert history[1]['message_metadata']['destinations'] == metadata['destinations']
        assert 'recipient' not in str(metadata) and 'metadata' not in metadata
    finally:
        writer.close()
        await writer.wait_closed()


async def test_native_final_has_host_identity_and_separate_local_reply_target(gateway):
    import asyncio

    server, _entered, _release, _contexts, endpoint = gateway
    reader, writer = await asyncio.open_unix_connection(endpoint)
    try:
        await send(writer, {'type': 'message', 'group_id': 'main', 'run_id': 'metadata-test', 'content': 'hello'})
        result = await receive(reader, 'response')
        metadata = result['message_metadata']
        assert metadata['role'] == 'admin' and metadata['principal'] == 'owner'
        assert metadata['source'] == 'tui' and metadata['input_id'] == 'native:metadata-test'
        assert metadata['destinations'] == [{'channel': 'tui', 'status': 'local'}]
    finally:
        writer.close()
        await writer.wait_closed()


def test_system_input_uses_dot_and_is_not_human_message_or_tool_authority():
    from xdog.claw.channels.tui.tui_app import ExternalMessage, UserMessage

    log = ChatLog()
    log.add_user('inspect event', metadata={'role': 'system', 'source': 'acp', 'input_id': 'event'})
    assert isinstance(log.children[0], ExternalMessage) and not isinstance(log.children[0], UserMessage)
    assert ' • inspect event' in rendered(log) and ' › ' not in rendered(log)
    assert log.transcript_records()[0].role == 'system'
    assert log.transcript_records()[0].body == 'inspect event'


def test_group_timeline_enriches_old_receipts_only_on_their_original_session(tmp_path):
    envelope = freeze_input(UserInput(group_id='cats', channel='xhs', sender='person', content='hello'),
                            tmp_path, message_id='old-comment', reply_mode='final')
    record = InputRecord(envelope, 'succeeded', attempt_id='attempt', session_id='old')
    output = OutputRecord('output', envelope, 'attempt', 0, 'binding', 'answer', 'unknown', '', '')
    history = [{'role': 'assistant', 'session_id': session, 'content': 'answer',
                'message_metadata': {'input_id': 'old-comment'}} for session in ('old', 'current')]
    enrich_history(history, [record], [output], 'current')
    assert history[0]['message_metadata']['destinations'][0]['status'] == 'unknown'
    assert history[1]['message_metadata'] == {'input_id': 'old-comment'}


def test_compact_headers_hide_full_identifiers_but_details_retain_original_facts():
    sender = 'speaker-full-original-identity-123456'
    metadata = {'role': 'external_user', 'sender': sender, 'source': 'xhs', 'session_id': 'full-session-id',
                'input_id': 'full-input-id', 'token': 'must-not-show',
                'destinations': [{'channel': 'xhs', 'status': 'unknown', 'output_id': 'full-output-id'}]}
    log = ChatLog()
    log.add_user('comment body', metadata=metadata)
    log.add_assistant('answer body', thinking='reason body', metadata=metadata)
    log.add_tool('message', {}, tool_call_id='full-call-id', metadata=metadata)
    log.finish_tool('full-call-id', 'message', 'output body')
    output = rendered(log)
    for private in (sender, 'full-session-id', 'full-input-id', 'full-output-id', 'full-call-id', 'must-not-show'):
        assert private not in output
    records = log.detail_records()
    assert [r.kind for r in records] == ['user', 'reasoning', 'assistant', 'tool']
    for record in records:
        assert sender in record.body and 'full-session-id' in record.body and 'full-output-id' in record.body
        assert 'must-not-show' not in record.body
    assert records[-1].body.startswith('output body') and 'full-call-id' in records[-1].body
    assert [r.body for r in log.transcript_records()][0] == 'comment body'


def test_handoff_projection_keeps_provenance_without_growing_preview():
    from xdog.claw.channels.tui.message_metadata import details

    metadata = {'role': 'system', 'source': 'history', 'handoff': {
        'source_session_id': 'previous-original-session', 'target_session_id': 'next-original-session',
        'source_digest': 'digest-evidence', 'source_message_count': 500, 'status': 'incomplete',
        'memory_status': 'failed', 'token': 'private',
    }}
    label_text = label(clean_metadata(metadata), 'system')
    assert label_text == 'system · history'
    body = details(metadata)
    assert 'previous-original-session' in body and 'digest-evidence' in body and 'incomplete' in body
    assert 'private' not in body
