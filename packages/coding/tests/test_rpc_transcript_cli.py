"""Actual offline Coding CLI: full-source framing, peer backpressure and producer cleanup."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.coding.core.messages import messages_to_dicts
from xdog.coding.core.session_manager import SessionManager
from xdog.tui.source_receiver import SourceReceiver


class Peer:
    def __init__(self, tmp_path):
        self.home = tmp_path / 'home'
        self.marker = tmp_path / 'model'
        self.manager = SessionManager(self.home / 'sessions')
        self.target = self.manager.create_session(model='rpc-fixture', working_dir=tmp_path)
        self.target.messages = [UserMessage(content='CLI-ARCHIVE ' + '🙂x' * 200000 + ' CLI-END-NEEDLE'),
                                AssistantMessage(content=(TextContent(text='CLI-ARCHIVE-ANSWER'),))]
        self.target.branches = [{'branch_id': 'archived', 'label': 'CLI-ARCHIVED',
                                'messages': messages_to_dicts(self.target.messages)}]
        self.target.active_branch = 'archived'
        self.manager.save_session(self.target)
        fixture = Path(__file__).parent / 'fixtures/rpc_client.py'
        self.process = subprocess.Popen([sys.executable, str(fixture)], cwd=tmp_path, text=True,
            env={**os.environ, 'CODING_DIR': str(self.home), 'XDOG_RPC_MARKER': str(self.marker)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.incoming = queue.Queue()
        self.thread = None

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def initial(self):
        ready = json.loads(self.process.stdout.readline())
        assert ready['type'] == 'ready' and ready['capabilities']['transcript_sources'] == 1
        self.ready = ready
        self.send({'type': 'rpc_hello', 'transcript_sources': 1})
        while True:
            reply = json.loads(self.process.stdout.readline())
            if reply['type'] == 'transcript_capabilities':
                assert reply['transcript_sources'] == 1
                return

    def command(self, scope='saved', **fields):
        return {'type': 'transcript_read', 'request_id': 'read', 'scope': scope,
                'target_session': self.target.session_id, 'session_id': self.ready['session_id'],
                'session_revision': self.ready['session_revision'], **fields}

    def collect(self):
        def read():
            for line in self.process.stdout:
                self.incoming.put(json.loads(line))
        self.thread = threading.Thread(target=read, daemon=True)
        self.thread.start()

    def next(self):
        return self.incoming.get(timeout=6)

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
        if self.thread is not None:
            self.thread.join(3)
            assert not self.thread.is_alive()


@pytest.mark.parametrize('scope', ['saved', 'branch', 'timeline'])
def test_actual_cli_full_unicode_saved_branch_timeline_replay_no_navigation_or_model(tmp_path, scope):
    peer = Peer(tmp_path)
    received = []
    receiver = SourceReceiver(received.append, max_bytes=128 * 1024 * 1024)
    try:
        peer.initial()
        expected_branch = 'archived' if scope == 'branch' else '@timeline' if scope == 'timeline' else ''
        receiver.expect('read', '', peer.ready['session_id'], target_session_id=peer.target.session_id,
                        branch_id=expected_branch, session_revision=peer.ready['session_revision'])
        peer.collect()
        peer.send(peer.command('saved' if scope == 'branch' else scope,
                               **({'branch_id': 'archived'} if scope == 'branch' else {})))
        packets = []
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            packet = peer.next()
            packets.append(packet)
            if packet['type'].startswith('source_'):
                receiver.feed(packet)
            if packet['type'] in {'source_error', 'source_end'}:
                break
        assert received[-1]['type'] == 'source_ready', received
        assert any('CLI-END-NEEDLE' in record.body for record in received[-1]['records'])
        assert 'CLI-ARCHIVE-ANSWER' in str(received[-1]['records'])
        assert sum(packet['type'] == 'source_chunk' for packet in packets) > 8
        peer.send({'type': 'status'})
        while True:
            status = peer.next()
            if status['type'] == 'status':
                break
        assert status['session_id'] == peer.ready['session_id'] and status['messages'] == 0
        assert status['permission_mode'] == 'deny' and not peer.marker.exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=4) == 0, peer.process.stderr.read()
    finally:
        receiver.close()
        peer.close()


@pytest.mark.skipif(os.name != 'posix', reason='Native POSIX output/backpressure proof')
@pytest.mark.parametrize('stop', ['quit', 'eof', 'closed-output'])
@pytest.mark.parametrize('format', ['display', 'canonical'])
def test_actual_cli_slow_or_closed_output_peer_cannot_block_control_reader_or_shutdown(tmp_path, stop, format):
    peer = Peer(tmp_path)
    try:
        peer.initial()
        if format == 'canonical':
            peer.send({'type': 'rpc_hello', 'transcript_canonical': 1})
        peer.send(peer.command(format=format))
        # Deliberately do not read a >1MiB source. The pipe fills while the
        # control reader remains independently available; no draining workaround.
        time.sleep(0.2)
        assert peer.process.poll() is None
        if stop == 'quit':
            peer.send({'type': 'quit'})
        elif stop == 'eof':
            peer.process.stdin.close()
        else:
            peer.process.stdout.close()  # Leave stdin open to prove output failure wakes its owner.
        assert peer.process.wait(timeout=4) == (1 if stop == 'closed-output' else 0), peer.process.stderr.read()
        assert not peer.marker.exists()
    finally:
        peer.close()
