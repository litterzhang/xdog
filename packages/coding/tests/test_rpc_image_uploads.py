"""Bounded framing and connection/session-scoped image refs, including actual CLI pipe transport."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from unittest.mock import patch

import pytest
from xdog.ai.utils.image_uploads import ImageUploads
from xdog.coding.modes.rpc.images import RpcImages
from xdog.coding.modes.rpc.input import MAX_RPC_LINE_BYTES, RpcInput

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')


class Owner:
    session_id = 'session'


def chunk(owner='session', key='image', index=0, data=PNG, *, final=True):
    return {'type': 'image_chunk', 'session_id': owner, 'id': key, 'index': index, 'mime_type': 'image/png',
            'data': base64.b64encode(data).decode(), 'final': final,
            **({'sha256': hashlib.sha256(PNG).hexdigest()} if final else {})}


def test_upload_peek_consume_discard_and_replay_are_owned_and_bytes_are_released():
    owner = Owner()
    store = RpcImages(owner)
    assert store.chunk(chunk(data=PNG[:20], final=False)) == 'partial'
    with pytest.raises(ValueError):
        store.resolve({'session_id': 'session', 'image_refs': ['image']})
    assert store.chunk(chunk(index=1, data=PNG[20:])) == 'ready'
    refs = {'session_id': 'session', 'image_refs': ['image']}
    assert store.resolve(refs)[0].data == base64.b64encode(PNG).decode()
    assert store.uploads._bytes == len(PNG)
    assert store.consume(refs)[0].mime_type == 'image/png'
    assert store.uploads._bytes == 0 and store.uploads.count == 0
    with pytest.raises(ValueError):
        store.resolve(refs)
    with pytest.raises(ValueError):
        store.chunk(chunk())
    store.discard({'session_id': 'session', 'id': 'image'})  # Explicit discard is idempotent, not replay.
    store.close()


def test_failed_hash_nonboolean_final_stale_owner_and_capacity_do_not_release_other_image():
    owner, other = Owner(), Owner()
    store = RpcImages(owner)
    assert store.chunk(chunk(key='kept')) == 'ready'
    with pytest.raises(ValueError):
        store.chunk({**chunk(key='bad'), 'sha256': 'not a digest'})
    assert store.uploads._bytes == len(PNG)
    with pytest.raises(ValueError):
        store.chunk({**chunk(key='wrong-final'), 'final': 'true'})
    with pytest.raises(ValueError):
        store.consume({'session_id': 'foreign', 'image_refs': ['kept']})
    with pytest.raises(ValueError):
        RpcImages(other).consume({'session_id': 'session', 'image_refs': ['kept']})
    owner.session_id = 'new-session'
    with pytest.raises(ValueError):
        store.consume({'session_id': 'new-session', 'image_refs': ['kept']})
    assert store.uploads._bytes == 0
    store.retired = {f'retired-{index}' for index in range(4095)}
    assert store.chunk(chunk(owner='new-session', key='last', data=PNG[:20], final=False)) == 'partial'
    with pytest.raises(ValueError):
        store.chunk(chunk(owner='new-session', key='no-capacity'))
    assert store.chunk(chunk(owner='new-session', key='last', index=1, data=PNG[20:])) == 'ready'
    store.discard({'session_id': 'new-session', 'id': 'last'})
    assert store.uploads._bytes == 0 and len(store.retired) == 4096


@pytest.mark.parametrize('async_reader', [False, True])
async def test_oversized_records_are_discarded_whole_and_following_record_is_not_swallowed(async_reader):
    maximum = 64
    raw = b'x' * 100 + b'{"type":"quit"}\n{"type":"status"}\n'
    inputs = RpcInput(maximum=maximum)
    if async_reader:
        inputs.reader = asyncio.StreamReader(limit=maximum + 1)
        inputs.reader.feed_data(raw)
        inputs.reader.feed_eof()
        first = await inputs.read()
        next_line, error = await inputs.read()
    else:
        class Source:
            buffer = io.BytesIO(raw)
        with patch('xdog.coding.modes.rpc.input.sys.stdin', Source()):
            first = await inputs.read()
            next_line, error = await inputs.read()
    assert first[0] == '' and 'frame limit' in first[1]
    assert not error and json.loads(next_line) == {'type': 'status'}


async def test_invalid_utf8_eof_tail_and_maximum_boundary_are_framed_without_execution():
    inputs = RpcInput(maximum=32)
    inputs.reader = asyncio.StreamReader(limit=33)
    inputs.reader.feed_data(b'\xff\n' + b'a' * 31 + b'\n' + b'{"type":"status"}')
    inputs.reader.feed_eof()
    assert 'UTF-8' in (await inputs.read())[1]
    assert await inputs.read() == ('a' * 31 + '\n', '')
    assert json.loads((await inputs.read())[0]) == {'type': 'status'}
    assert await inputs.read() == (None, '')
    assert MAX_RPC_LINE_BYTES >= ((10 * 1024 * 1024 + 2) // 3) * 4 * 4 + 65536


async def test_shared_assembler_still_has_no_files_or_implicit_model_actions():
    source = ImageUploads()
    assert source.chunk(chunk()) == 'ready'
    assert source.resolve(['image'])[0].mime_type == 'image/png'
    source.clear()
    assert not source._pending and not source._ready and source._bytes == 0


@pytest.mark.parametrize('discarded', [False, True])
def test_actual_cli_chunk_references_are_single_use_and_large_input_does_not_fetch_or_drop_bytes(tmp_path, discarded):
    import os
    import queue
    import subprocess
    import sys
    import threading
    import time
    from pathlib import Path

    fixture = Path(__file__).parent / 'fixtures/rpc_client.py'
    home, marker = tmp_path / 'home', tmp_path / 'model'
    env = {**os.environ, 'CODING_DIR': str(home), 'XDOG_RPC_MARKER': str(marker), 'XDOG_RPC_VISION': '1'}
    process = subprocess.Popen([sys.executable, str(fixture)], cwd=tmp_path, env=env, text=True,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    incoming = queue.Queue()
    outputs = []
    def collect():
        for line in process.stdout:
            value = json.loads(line)
            incoming.put(value)
            outputs.append(value)
    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    def read(kind):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            event = incoming.get(timeout=max(0.1, deadline - time.monotonic()))
            if event['type'] == kind:
                return event
        raise AssertionError('Missing ' + kind)
    def send(packet):
        process.stdin.write(json.dumps(packet) + '\n')
        process.stdin.flush()
    try:
        ready = read('ready')
        assert ready['capabilities']['image_uploads'] == 1 and ready['max_input_frame_bytes'] == MAX_RPC_LINE_BYTES
        sid = ready['session_id']
        send(chunk(owner=sid, data=PNG[:20], final=False))
        assert read('image_upload_result')['status'] == 'partial'
        send({'type': 'status'})
        assert not read('status')['prompt_running'] and not marker.exists()
        send(chunk(owner=sid, index=1, data=PNG[20:]))
        assert read('image_upload_result')['status'] == 'ready'
        if discarded:
            send({'type': 'image_discard', 'session_id': sid, 'id': 'image'})
            assert read('image_upload_result')['status'] == 'discarded'
        send({'type': 'prompt', 'session_id': sid, 'image_refs': ['image']})
        if discarded:
            assert 'missing' in read('error')['message'] and not marker.exists()
        else:
            assert read('message_end')['content'] == [{'type': 'text', 'text': 'RPC-IMAGES-1'}]
            send({'type': 'prompt', 'session_id': sid, 'image_refs': ['image']})
            assert 'missing' in read('error')['message']
        send({'type': 'quit'})
        assert process.wait(timeout=6) == 0, process.stderr.read()
        thread.join(3)
        assert not thread.is_alive() and base64.b64encode(PNG).decode() not in json.dumps(outputs)
        if not discarded:
            from xdog.ai.types import ImageContent
            from xdog.coding.core.session_manager import SessionManager
            saved = SessionManager(home / 'sessions').get_most_recent()
            assert sum(isinstance(part, ImageContent) for msg in saved.messages
                       for part in (() if isinstance(msg.content, str) else msg.content)) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        thread.join(3)


def test_actual_posix_rpc_sigint_closes_owned_async_input_pipe_without_waiting_for_peer_eof(tmp_path):
    import os
    import queue
    import signal
    import subprocess
    import sys
    import threading
    from pathlib import Path

    if os.name != 'posix':
        pytest.skip('POSIX signal/read-pipe ownership evidence only')
    fixture = Path(__file__).parent / 'fixtures/rpc_client.py'
    process = subprocess.Popen([sys.executable, str(fixture)], cwd=tmp_path, text=True,
        env={**os.environ, 'CODING_DIR': str(tmp_path / 'home'), 'XDOG_RPC_MARKER': str(tmp_path / 'model')},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ready = queue.Queue()
    def collect():
        for line in process.stdout:
            ready.put(json.loads(line))
    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    try:
        assert ready.get(timeout=6)['type'] == 'ready'
        assert not process.stdin.closed
        process.send_signal(signal.SIGINT)
        process.wait(timeout=6)
        thread.join(3)
        assert not thread.is_alive() and not (tmp_path / 'model').exists()
        # Exit is owned without closing the caller's writing end first.
        assert not process.stdin.closed
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        thread.join(3)
