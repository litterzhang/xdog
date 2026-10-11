"""Source checksums never admit records into another requested saved scope."""
import base64
import hashlib
import json

from xdog.claw.channels.tui.source_reader import SourceReceiver


def test_scope_is_preserved_in_receipts_and_midstream_change_fails_closed():
    events = []
    receiver = SourceReceiver(events.append)
    envelope = {'request_id': 'read', 'group_id': 'main', 'session_id': 'current',
                'target_session_id': 'saved', 'branch_id': 'archive', 'revision': 1}
    data = (json.dumps({'id': 'old', 'role': 'user', 'title': 'input', 'body': 'original saved body'}) + '\n').encode()
    def begin():
        receiver.expect('read', 'main', 'current', target_session_id='saved', branch_id='archive')
        receiver.feed({**envelope, 'type': 'source_begin', 'bytes': len(data),
                       'sha256': hashlib.sha256(data).hexdigest()})
    begin()
    receiver.feed({**envelope, 'type': 'source_chunk', 'index': 0, 'data': base64.b64encode(data).decode()})
    receiver.feed({**envelope, 'type': 'source_end', 'chunks': 1})
    assert events[-1]['type'] == 'source_ready' and events[-1]['target_session_id'] == 'saved'
    assert events[-1]['branch_id'] == 'archive'
    begin()
    receiver.feed({**envelope, 'branch_id': 'other', 'type': 'source_chunk', 'index': 0,
                   'data': base64.b64encode(data).decode()})
    assert events[-1]['type'] == 'source_failed' and 'scope changed' in events[-1]['error']
    receiver.feed({**envelope, 'type': 'source_end', 'chunks': 1})
    assert events[-1]['type'] == 'source_failed'
    receiver.close()


def test_timeline_without_its_independent_capability_is_unavailable_not_live_fallback():
    from xdog.claw.channels.tui.tui_app import ChatApp
    from xdog.tui.transcript_transfer import TransferOptions
    ui = ChatApp('/tmp/unconnected-timeline-test.sock')
    ui._connection_ready = True
    ui._source_supported = True
    ui._saved_source_supported = True
    ui._saved_timeline_supported = False
    ui._gateway_session_id = 'owner'
    try:
        ui._chat_log.add_user('LIVE-MUST-NOT-BECOME-TIMELINE')
        ui._transcript_command('timeline')
        assert ui._source_request_id is None and not ui._transcript_panel.settled
        assert not ui._transcript_panel.records
        captured = []
        ui._outputs.start = lambda *args: captured.append(args)
        ui._begin_output(TransferOptions('export', 'conversation', 'md'))
        assert not captured
        assert 'LIVE-MUST-NOT-BECOME-TIMELINE' not in str(ui._transcript_panel.records)
    finally:
        ui._request_exit()
