"""Actual CLI child loop, independent approval/source messages and disconnect ownership."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from xdog.coding.core.session_manager import SessionManager
from xdog.tui.source_receiver import SourceReceiver


class Peer:
    def __init__(self, tmp_path, *, write=False, vision=False):
        self.home = tmp_path / 'home'
        self.marker = tmp_path / 'model'
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).parent / 'fixtures/rpc_client.py')],
            cwd=tmp_path, text=True,
            env={**os.environ, 'CODING_DIR': str(self.home), 'XDOG_RPC_MARKER': str(self.marker),
                 'XDOG_RPC_CHILD_WRITE': '1' if write else '0', 'XDOG_RPC_PERMISSION': 'ask' if write else 'deny',
                 'XDOG_RPC_VISION': '1' if vision else '0'},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.events, self.incoming, self.buffered = [], queue.Queue(), []
        def collect():
            for line in self.process.stdout:
                self.incoming.put(json.loads(line))
        self.thread = threading.Thread(target=collect, daemon=True)
        self.thread.start()
        self.ready = self.read('ready')
        assert self.ready['capabilities']['child_controls'] == 1
        self.send({'type': 'rpc_hello', 'child_controls': 1, 'child_sources': 1, 'child_approvals': int(write)})
        self.caps = self.read('child_capabilities')

    def read(self, kind):
        for index, event in enumerate(self.buffered):
            if event['type'] == kind:
                return self.buffered.pop(index)
        while True:
            event = self.incoming.get(timeout=6)
            self.events.append(event)
            if event['type'] == kind:
                return event
            self.buffered.append(event)

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def packet(self, key, **fields):
        return {'type': 'child_control', 'request_id': key,
                **{name: self.caps[name] for name in ('session_id', 'session_revision', 'child_owner', 'child_epoch',
                                                      'child_return_owner')},
                **fields}

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
        self.thread.join(3)
        assert not self.thread.is_alive()


@pytest.mark.parametrize('allow', [False, True])
def test_actual_cli_child_permission_cannot_be_resolved_by_parent_and_source_never_installs_parent_context(
    tmp_path, allow,
):
    peer = Peer(tmp_path, write=True)
    receiver = None
    try:
        peer.send(peer.packet('start', action='start', task='child-write', access='inherit'))
        started = peer.read('child_result')
        assert started['success'] and started['action'] == 'start'
        question = peer.read('child_permission_request')
        assert question['child_id'] == started['child_id'] and not (tmp_path / 'cli-child-target').exists()
        peer.send({'type': 'permission_response', 'id': question['id'], 'decision': 'allow_once'})
        assert 'Unknown permission request' in peer.read('error')['message']
        peer.send(peer.packet('answer', type='child_permission_response', id=question['id'],
            child_id=question['child_id'], decision='allow_once' if allow else 'deny',
            approval_generation=question['approval_generation']))
        assert peer.read('child_permission_ack')['id'] == question['id']
        peer.send(peer.packet('wait', action='wait', child_id=started['child_id']))
        assert peer.read('child_result')['success']
        assert (tmp_path / 'cli-child-target').exists() is allow
        decoded = []
        receiver = SourceReceiver(decoded.append)
        receiver.expect('source', '', peer.caps['session_id'], target_session_id=peer.caps['session_id'],
            session_revision=peer.caps['session_revision'], child_id=started['child_id'],
            child_owner=peer.caps['child_owner'], child_epoch=peer.caps['child_epoch'])
        peer.send(peer.packet('source', type='child_source_read', child_id=started['child_id']))
        while True:
            event = peer.incoming.get(timeout=6)
            peer.events.append(event)
            if event['type'].startswith('source_'):
                receiver.feed(event)
            if event['type'] in {'source_end', 'source_error'}:
                break
        assert decoded[-1]['type'] == 'source_ready' and 'RPC-OFFLINE-ANSWER' in str(decoded[-1]['records'])
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['session_id'] == peer.ready['session_id'] and status['messages'] == 0
        assert status['permission_mode'] == 'ask'
        peer.send(peer.packet('close', action='close', child_id=started['child_id']))
        assert peer.read('child_result')['success']
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=4) == 0, peer.process.stderr.read()
    finally:
        if receiver is not None:
            receiver.close()
        peer.close()


@pytest.mark.parametrize('stop', ['cancel', 'quit', 'eof'])
def test_actual_cli_holding_child_is_settled_and_persisted_without_replay_on_disconnect(tmp_path, stop):
    peer = Peer(tmp_path)
    try:
        peer.send(peer.packet('start', action='start', task='hold'))
        started = peer.read('child_result')
        assert started['success']
        import time
        deadline = time.monotonic() + 5
        while not peer.marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert peer.marker.exists()
        if stop == 'cancel':
            peer.send(peer.packet('cancel', action='cancel', child_id=started['child_id']))
            assert peer.read('child_result')['success']
            peer.send({'type': 'quit'})
        elif stop == 'quit':
            peer.send({'type': 'quit'})
        else:
            peer.process.stdin.close()
        assert peer.process.wait(timeout=4) == 0, peer.process.stderr.read()
        assert peer.marker.with_suffix('.settled').exists()
        store = SessionManager(peer.home / 'sessions')
        path = store._dir / (peer.ready['session_id'] + '.children') / 'branch-head' / (started['child_id'] + '.json')
        record = json.loads(path.read_text())
        assert record['status'] == 'canceled' and record['settling'] is False
        assert not store.load_session(peer.ready['session_id']).messages
    finally:
        peer.close()


def test_actual_cli_revisioned_child_queue_edits_and_return_are_not_parent_prompt_submission(tmp_path):
    import time

    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'child_queues': 1})
        peer.caps = peer.read('child_capabilities')
        assert peer.caps['child_queues'] == 1
        peer.send(peer.packet('start', action='start', task='hold'))
        child = peer.read('child_result')['child_id']
        deadline = time.monotonic() + 5
        while not peer.marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert peer.marker.exists()
        for index, content in enumerate(('first', 'second')):
            peer.send(peer.packet(f'steer-{index}', action='steer', child_id=child, text=content))
            assert peer.read('child_result')['success']
        def queue_action(key, value):
            peer.send(peer.packet(key, action='queue', child_id=child, queue=value))
            return peer.read('child_result')
        original = queue_action('queue', {'action': 'list'})['queue']
        first = original['inputs'][0]
        edited = queue_action('edit', {'action': 'edit', 'id': first['id'], 'revision': original['revision'],
                                      'content': '/quit is queued child text'})['queue']
        refused = queue_action('stale', {'action': 'remove', 'id': first['id'], 'revision': original['revision']})
        assert not refused['success']
        returned = queue_action('return', {'action': 'return', 'id': first['id'], 'revision': edited['revision']})
        assert returned['success'] and returned['queue']['returned']['content'] == '/quit is queued child text'
        assert len(returned['queue']['inputs']) == 1
        peer.send({'type': 'status'})
        assert peer.read('status')['messages'] == 0 and peer.process.poll() is None
        peer.send(peer.packet('cancel', action='cancel', child_id=child))
        assert peer.read('child_result')['success']
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=4) == 0, peer.process.stderr.read()
    finally:
        peer.close()


def test_actual_cli_canceled_child_retains_input_and_recovery_does_not_restart_or_erase_it(tmp_path):
    import time

    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'child_queues': 1})
        peer.caps = peer.read('child_capabilities')
        peer.send(peer.packet('start', action='start', task='hold'))
        child = peer.read('child_result')['child_id']
        deadline = time.monotonic() + 5
        while not peer.marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert peer.marker.exists()
        peer.send(peer.packet('steer', action='steer', child_id=child, text='UNADMITTED /quit LITERAL'))
        assert peer.read('child_result')['success']
        peer.send(peer.packet('cancel', action='cancel', child_id=child))
        assert peer.read('child_result')['success']
        peer.send(peer.packet('queue', action='queue', child_id=child, queue={'action': 'list'}))
        captured = peer.read('child_result')['queue']
        item = captured['inputs'][0]
        assert captured['stopped'] and item['content'] == 'UNADMITTED /quit LITERAL'
        peer.send(peer.packet('recover', action='queue', child_id=child, queue={
            'action': 'recover', 'id': item['id'], 'revision': captured['revision']}))
        copied = peer.read('child_result')
        assert copied['success'] and copied['queue']['recovered'] == item
        assert copied['queue']['inputs'] == captured['inputs']
        peer.send({'type': 'status'})
        assert peer.read('status')['messages'] == 0
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=4) == 0, peer.process.stderr.read()
        path = peer.home / 'sessions' / (peer.ready['session_id'] + '.children') / 'branch-head' / (child + '.json')
        record = json.loads(path.read_text())
        assert record['undelivered_inputs'][0]['content'] == item['content']
        assert item['content'] not in str(record['messages']) and record['status'] == 'canceled'
    finally:
        peer.close()


def test_actual_cli_child_image_upload_and_queue_acceptance_are_not_parent_turns(tmp_path):
    import base64
    import hashlib
    import time
    peer = Peer(tmp_path, vision=True)
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')
    body = png + b'\0' * (2 * 1024 * 1024)
    try:
        peer.send({'type': 'rpc_hello', 'child_images': 1})
        peer.caps = peer.read('child_capabilities')
        assert peer.caps['child_images'] == 1
        for index, offset in enumerate(range(0, len(body), 65536)):
            final = offset + 65536 >= len(body)
            peer.send({'type': 'image_chunk', 'session_id': peer.caps['session_id'], 'id': 'child-image',
                       'index': index, 'mime_type': 'image/png', 'data': base64.b64encode(
                           body[offset:offset + 65536]).decode(), 'final': final,
                       **({'sha256': hashlib.sha256(body).hexdigest()} if final else {})})
            ack = peer.read('image_upload_result')
            assert ack['status'] in {'partial', 'ready'}
        peer.send(peer.packet('image-start', action='start', task='hold', image_refs=['child-image']))
        started = peer.read('child_result')
        assert started['success'] and started['accepted_input']['image_sha256'] == [hashlib.sha256(body).hexdigest()]
        deadline = time.monotonic() + 5
        while not peer.marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert peer.marker.exists()
        for index, offset in enumerate(range(0, len(body), 65536)):
            final = offset + 65536 >= len(body)
            peer.send({'type': 'image_chunk', 'session_id': peer.caps['session_id'], 'id': 'queue-picture',
                       'index': index, 'mime_type': 'image/png', 'data': base64.b64encode(
                           body[offset:offset + 65536]).decode(), 'final': final,
                       **({'sha256': hashlib.sha256(body).hexdigest()} if final else {})})
            assert peer.read('image_upload_result')['status'] in {'partial', 'ready'}
        peer.send(peer.packet('image-steer', action='steer', child_id=started['child_id'], text='',
                              image_refs=['queue-picture']))
        steered = peer.read('child_result')
        assert steered['success'] and steered['accepted_input']['image_sha256'] == [hashlib.sha256(body).hexdigest()]
        peer.send(peer.packet('image-cancel', action='cancel', child_id=started['child_id']))
        assert peer.read('child_result')['success']
        peer.send({'type': 'status'})
        assert peer.read('status')['messages'] == 0
        peer.send({'type': 'rpc_hello', 'child_queues': 1, 'child_image_recovery': 1})
        peer.caps = peer.read('child_capabilities')
        peer.send(peer.packet('copy-list', action='queue', child_id=started['child_id'], queue={'action': 'list'}))
        snapshot = peer.read('child_result')['queue']
        request = {'action': 'recover_images', 'id': snapshot['inputs'][0]['id'], 'revision': snapshot['revision']}
        from xdog.tui.child_recovery import ChildRecoveryReceiver
        received = []
        recovery = ChildRecoveryReceiver(received.append)
        recovery.expect({**{name: peer.caps[name] for name in (
            'session_id', 'session_revision', 'child_owner', 'child_epoch')}, 'request_id': 'image-copy',
            'child_id': started['child_id'], 'input_id': request['id'], 'queue_revision': request['revision']})
        try:
            peer.send(peer.packet('image-copy', action='queue', child_id=started['child_id'], queue=request))
            copied = peer.read('child_result')
            for event in peer.events:
                if event['type'].startswith('child_recovery_'):
                    recovery.feed(event)
            assert copied['success'] and received[-1]['images'][0].data == body
            assert copied['queue']['image_receipt'] == received[-1]['receipt']
            peer.send({'type': 'status'})
            assert peer.read('status')['messages'] == 0
        finally:
            recovery.close()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=4) == 0, peer.process.stderr.read()
        path = peer.home / 'sessions' / (peer.ready['session_id'] + '.children') / 'branch-head'
        record = json.loads((path / (started['child_id'] + '.json')).read_text())
        assert base64.b64decode(record['messages'][0]['content'][1]['data']) == body
        assert base64.b64decode(record['undelivered_inputs'][0]['images'][0]['data']) == body
        assert record['settling'] is False and peer.marker.with_suffix('.settled').exists()
    finally:
        peer.close()


def test_actual_cli_live_image_return_stream_and_separate_commit_never_enter_parent_history(tmp_path):
    import base64
    import hashlib
    import time

    from xdog.tui.child_recovery import ChildRecoveryReceiver
    peer = Peer(tmp_path, vision=True)
    receiver = None
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')
    body = png + b'\0' * (2 * 1024 * 1024)
    try:
        peer.send({'type': 'rpc_hello', 'child_queues': 1, 'child_image_return': 1, 'child_images': 1})
        peer.caps = peer.read('child_capabilities')
        assert peer.caps['child_image_return'] == 1 and peer.caps['child_return_owner']
        peer.send(peer.packet('return-start', action='start', task='hold'))
        child = peer.read('child_result')['child_id']
        deadline = time.monotonic() + 5
        while not peer.marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert peer.marker.exists()
        for index, offset in enumerate(range(0, len(body), 65536)):
            final = offset + 65536 >= len(body)
            peer.send({'type': 'image_chunk', 'session_id': peer.caps['session_id'], 'id': 'return-photo',
                       'index': index, 'mime_type': 'image/png', 'data': base64.b64encode(
                           body[offset:offset + 65536]).decode(), 'final': final,
                       **({'sha256': hashlib.sha256(body).hexdigest()} if final else {})})
            assert peer.read('image_upload_result')['status'] in {'partial', 'ready'}
        peer.send(peer.packet('return-input', action='steer', child_id=child, text='RETURN-LITERAL /quit',
                              image_refs=['return-photo']))
        assert peer.read('child_result')['success']
        peer.send(peer.packet('return-list', action='queue', child_id=child, queue={'action': 'list'}))
        before = peer.read('child_result')['queue']
        row = before['inputs'][0]
        decoded = []
        receiver = ChildRecoveryReceiver(decoded.append)
        receiver.expect({**{name: peer.caps[name] for name in (
            'session_id', 'session_revision', 'child_owner', 'child_epoch', 'child_return_owner')},
            'request_id': 'return-prepare', 'child_id': child, 'input_id': row['id'],
            'queue_revision': before['revision']})
        peer.send(peer.packet('return-prepare', action='queue', child_id=child, queue={
            'action': 'return_prepare', 'id': row['id'], 'revision': before['revision']}))
        prepared = peer.read('child_result')
        for event in peer.events:
            if event['type'].startswith('child_recovery_'):
                receiver.feed(event)
        assert prepared['success'] and decoded[-1]['images'][0].data == body
        peer.send(peer.packet('still-queued', action='queue', child_id=child, queue={'action': 'list'}))
        assert peer.read('child_result')['queue'] == before  # Transfer receipt is NOT removal consent.
        command = {'action': 'return_commit', 'id': row['id'], 'revision': before['revision'],
                   'token': prepared['queue']['return_token'], 'receipt': decoded[-1]['receipt']}
        peer.send(peer.packet('return-commit', action='queue', child_id=child, queue=command))
        committed = peer.read('child_result')
        assert committed['success'] and committed['queue']['returned'] == row
        assert not committed['queue']['inputs']
        peer.send(peer.packet('return-retry', action='queue', child_id=child, queue=command))
        retry = peer.read('child_result')
        assert retry['success'] and retry['queue'] == committed['queue']
        peer.send({'type': 'status'})
        assert peer.read('status')['messages'] == 0
        peer.send(peer.packet('return-stop', action='cancel', child_id=child))
        assert peer.read('child_result')['success']
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=4) == 0, peer.process.stderr.read()
        path = peer.home / 'sessions' / (peer.ready['session_id'] + '.children') / 'branch-head'
        saved = json.loads((path / (child + '.json')).read_text())
        assert not saved.get('undelivered_inputs') and 'RETURN-LITERAL' not in str(saved['messages'])
    finally:
        if receiver is not None:
            receiver.close()
        peer.close()


def test_actual_cli_saved_draft_catalog_and_verified_image_read_never_submit_work(tmp_path):
    import base64

    from xdog.tui.attachments import attachment
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    from xdog.tui.child_drafts import ChildDraft
    from xdog.tui.child_recovery import ChildRecoveryReceiver
    peer = Peer(tmp_path)
    receiver = None
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')
    body = png + b'\0' * (2 * 1024 * 1024)
    store = ChildDraftStore(peer.home / 'sessions' / 'saved-child-drafts')
    saved = store.save(encode_draft(ChildDraft('SAVED UNSENT /quit', 'inherit', {'tools': ['bash']},
                                             (attachment(body, 'snapshot.png'),)),
                                   label='old workspace/session', scope='old', action='steer', slot='old-child'))
    try:
        peer.send({'type': 'rpc_hello', 'child_saved_drafts': 1, 'child_saved_images': 1})
        peer.caps = peer.read('child_capabilities')
        assert peer.caps['child_saved_drafts'] == 1 and peer.caps['child_saved_images'] == 1
        peer.send(peer.packet('saved-catalog', action='saved_catalog'))
        catalog = peer.read('child_result')
        assert catalog['success'] and catalog['saved_catalog'][0]['id'] == saved.id
        assert catalog['saved_catalog'][0]['sha256'] == saved.sha256
        assert not peer.marker.exists()
        decoded = []
        receiver = ChildRecoveryReceiver(decoded.append)
        receiver.expect({**{key: peer.caps[key] for key in ('session_id', 'session_revision', 'child_owner',
                                                          'child_epoch', 'child_return_owner')},
                         'request_id': 'snapshot-read', 'child_id': 'saved-draft', 'input_id': saved.id,
                         'queue_revision': saved.sha256, 'snapshot_id': saved.id, 'snapshot_sha256': saved.sha256})
        peer.send(peer.packet('snapshot-read', action='saved_read', snapshot_id=saved.id, snapshot_sha256=saved.sha256))
        read = peer.read('child_result')
        for event in peer.events:
            if event['type'].startswith('child_recovery_'):
                receiver.feed(event)
        assert read['success'] and decoded[-1]['images'][0].data == body
        assert decoded[-1]['receipt'] == read['image_receipt']
        assert read['saved_draft']['access'] == 'read-only'
        assert read['saved_draft']['config'] == {'inherit_context': False}
        assert read['saved_draft']['text'] == 'SAVED UNSENT /quit' and read['saved_draft']['uncertain'] is True
        assert not peer.marker.exists() and store.load(saved.id).sha256 == saved.sha256
        peer.send({'type': 'status'})
        assert peer.read('status')['messages'] == 0
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=4) == 0, peer.process.stderr.read()
    finally:
        if receiver is not None:
            receiver.close()
        peer.close()


def test_actual_cli_saved_snapshot_discard_needs_separate_exact_confirmation(tmp_path):
    from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
    from xdog.tui.child_drafts import ChildDraft
    peer = Peer(tmp_path)
    store = ChildDraftStore(peer.home / 'sessions' / 'saved-child-drafts')
    saved = store.save(encode_draft(ChildDraft('LOCAL SNAPSHOT DELETE TEST'), label='source',
                                   scope='old', action='start', slot=''))
    try:
        peer.send({'type': 'rpc_hello', 'child_saved_drafts': 1, 'child_saved_discard': 1})
        peer.caps = peer.read('child_capabilities')
        assert peer.caps['child_saved_discard'] == 1
        peer.send(peer.packet('delete-prepare', action='saved_discard_prepare',
                              snapshot_id=saved.id, snapshot_sha256=saved.sha256))
        prepared = peer.read('child_result')
        assert prepared['success'] and store.load(saved.id).sha256 == saved.sha256
        peer.send(peer.packet('bad-consent', action='saved_discard_confirm',
                              review_id=prepared['review_id'], review=prepared['review'], confirmed='true'))
        assert peer.read('child_result')['success'] is False
        assert store.load(saved.id).sha256 == saved.sha256
        peer.send(peer.packet('delete-confirm', action='saved_discard_confirm',
                              review_id=prepared['review_id'], review=prepared['review'], confirmed=True))
        result = peer.read('child_result')
        assert result['success'] and result['discarded_snapshot_id'] == saved.id
        assert not store.catalog() and not peer.marker.exists()
        peer.send(peer.packet('reused-review', action='saved_discard_confirm',
                              review_id=prepared['review_id'], review=prepared['review'], confirmed=True))
        assert peer.read('child_result')['success'] is False
        peer.send({'type': 'status'})
        assert peer.read('status')['messages'] == 0
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=4) == 0, peer.process.stderr.read()
    finally:
        peer.close()


def test_actual_cli_two_phase_saved_image_creation_does_not_submit_to_agent(tmp_path):
    import base64
    import hashlib

    from xdog.tui.child_draft_store import ChildDraftStore
    peer = Peer(tmp_path)
    body = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')
    body += b'\0' * (2 * 1024 * 1024)
    store = ChildDraftStore(peer.home / 'sessions' / 'saved-child-drafts')
    try:
        peer.send({'type': 'rpc_hello', 'child_saved_drafts': 1, 'child_saved_save': 1, 'child_saved_images': 1})
        peer.caps = peer.read('child_capabilities')
        for index, offset in enumerate(range(0, len(body), 65536)):
            final = offset + 65536 >= len(body)
            peer.send({'type': 'image_chunk', 'session_id': peer.caps['session_id'], 'id': 'saved-image',
                       'index': index, 'mime_type': 'image/png',
                       'data': base64.b64encode(body[offset:offset + 65536]).decode(), 'final': final,
                       **({'sha256': hashlib.sha256(body).hexdigest()} if final else {})})
            assert peer.read('image_upload_result')['status'] in {'partial', 'ready'}
        peer.send(peer.packet('save-image-prepare', action='saved_save_prepare', text='UNSENT RPC IMAGE /quit',
                              label='explicit snapshot', image_refs=['saved-image']))
        prepared = peer.read('child_result')
        assert prepared['success'] and not store.catalog() and not peer.marker.exists()
        assert prepared['review']['image_receipt']['image_sha256'] == [hashlib.sha256(body).hexdigest()]
        peer.send(peer.packet('save-image-confirm', action='saved_save_confirm', review_id=prepared['review_id'],
                              review=prepared['review'], confirmed=True))
        result = peer.read('child_result')
        assert result['success']
        loaded = store.load(result['snapshot_id'])
        assert loaded.draft.images[0].data == body and loaded.draft.text == 'UNSENT RPC IMAGE /quit'
        assert loaded.sha256 == prepared['review']['snapshot_sha256'] == result['snapshot_sha256']
        assert loaded.draft.access == 'read-only' and loaded.draft.config == {'inherit_context': False}
        peer.send({'type': 'status'})
        assert peer.read('status')['messages'] == 0 and not peer.marker.exists()
        peer.send(peer.packet('save-image-repeat', action='saved_save_confirm', review_id=prepared['review_id'],
                              review=prepared['review'], confirmed=True))
        assert not peer.read('child_result')['success'] and len(store.catalog()) == 1
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=4) == 0, peer.process.stderr.read()
    finally:
        peer.close()
