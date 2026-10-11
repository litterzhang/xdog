"""Owned CLI large custom-frame output must not prevent control/worker teardown."""
from __future__ import annotations

import json
import os
import selectors
import shutil
import sys
import time
from pathlib import Path

import pytest

from packages.coding.tests.test_rpc_transcript_cli import Peer

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Owned Linux pipe/worker reaping proof')


@pytest.mark.parametrize('child,stop', [
    pytest.param(child, stop, id=stop + ('-child' if child else '-parent'))
    for stop in ('quit', 'eof', 'closed-output') for child in (False, True)
] + [pytest.param(False, 'cancel-quit', id='cancel-quit-parent'),
     pytest.param(False, 'cancel-eof', id='cancel-eof-parent')])
def test_actual_cli_unread_large_custom_frame_settles_view_and_owned_worker(tmp_path, monkeypatch, child, stop):
    root = tmp_path / 'extensions'
    source = Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/custom'
    shutil.copytree(source, root / 'custom')
    worker_pid = tmp_path / 'worker-pid'
    rendered = tmp_path / 'rendered'
    main = root / 'custom/main.py'
    text = main.read_text().replace('def setup(api):',
        f"def setup(api):\n    import os\n    from pathlib import Path\n"
        f"    Path({str(worker_pid)!r}).write_text(str(os.getpid()))")
    text = text.replace("            return {'lines': [title, json.dumps(inputs)], "
                        "'cursor': [0, 0] if height else None}",
        f"            Path({str(rendered)!r}).write_text('rendered')\n"
        "            return {'lines': ['LARGE-CUSTOM-FRAME-' + 'x' * 8000 for _ in range(15)], 'cursor': None}")
    main.write_text(text)
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['custom'], 'extension_dir': str(root)}))
    canceled_send = tmp_path / 'canceled-send'
    if stop.startswith('cancel-'):
        monkeypatch.setenv('XDOG_RPC_CANCELED_SEND_MARKER', str(canceled_send))
    if child:
        monkeypatch.setenv('XDOG_RPC_CHILD_CUSTOM', '1')
        monkeypatch.setenv('XDOG_RPC_PERMISSION', 'ask')
    peer = Peer(tmp_path)

    def read(kind):
        line = bytearray()
        deadline = time.monotonic() + 8
        with selectors.DefaultSelector() as selector:
            selector.register(peer.process.stdout.fileno(), selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(max(0, deadline - time.monotonic())):
                    continue
                byte = os.read(peer.process.stdout.fileno(), 1)
                assert byte, 'Owned CLI ended before receipt'
                line.extend(byte)
                if byte != b'\n':
                    continue
                event = json.loads(line)
                line.clear()
                if event['type'] == kind:
                    return event
        raise AssertionError('Missing bounded CLI receipt: ' + kind)

    try:
        peer.initial()
        if child:
            peer.send({'type': 'rpc_hello', 'child_controls': 1, 'child_custom_views': 1, 'child_approvals': 1})
            caps = read('child_capabilities')
            while not caps['child_custom_views']:
                caps = read('child_capabilities')
            envelope = {key: caps[key] for key in ('session_id', 'session_revision', 'child_owner',
                                                  'child_epoch', 'child_return_owner')}
            peer.send({'type': 'child_control', 'request_id': 'start', 'action': 'start', 'task': 'custom',
                       'access': 'inherit', **envelope})
            started = read('child_result')
            assert started['success']
            question = read('child_permission_request')
            peer.send({'type': 'child_permission_response', **envelope, 'id': question['id'],
                       'child_id': started['child_id'], 'decision': 'allow_once',
                       'approval_generation': question['approval_generation']})
            read('child_permission_ack')
            dialog = read('child_extension_dialog')
            command = {'type': 'child_custom_input', **envelope, 'child_id': started['child_id'],
                       'id': dialog['dialog']['id'], 'custom_generation': dialog['custom_generation'], 'sequence': 1}
        else:
            peer.send({'type': 'rpc_hello', 'extension_custom_views': 1})
            read('extension_custom_capabilities')
            peer.send({'type': 'extension_control', 'request_id': 'view', 'session_id': peer.ready['session_id'],
                       'action': 'command', 'name': 'ext-custom-view', 'args': ''})
            dialog = read('extension_dialog')
            command = {'type': 'extension_custom_input', 'session_id': dialog['session_id'],
                       'request_id': dialog['request_id'], 'scope': dialog['scope'], 'id': dialog['dialog']['id'],
                       'custom_generation': dialog['custom_generation'], 'sequence': 1}
        peer.send({**command, 'event': {'kind': 'resize', 'width': 10000, 'height': 20,
                                      'painted': -1, 'generation': 1}})
        # Stop draining BEFORE the 120KiB frame, not merely after its receipt.
        deadline = time.monotonic() + 8
        while not rendered.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert rendered.exists() and peer.process.poll() is None
        # Observe only a prefix of the real frame; leave the rest undrained so
        # a renderer marker alone cannot stand in for actual output backpressure.
        prefix = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(peer.process.stdout.fileno(), selectors.EVENT_READ)
            deadline = time.monotonic() + 5
            while b'LARGE-CUSTOM-FRAME-' not in prefix and time.monotonic() < deadline:
                if selector.select(max(0, deadline - time.monotonic())):
                    prefix.extend(os.read(peer.process.stdout.fileno(), 1024))
        assert b'LARGE-CUSTOM-FRAME-' in prefix and len(prefix) < 16 * 1024
        pid = int(worker_pid.read_text())
        argv = Path(f'/proc/{pid}/cmdline').read_bytes()
        assert b'extensions/worker.py' in argv and str(root / 'custom').encode() in argv
        if stop.startswith('cancel-'):
            peer.send({'type': 'extension_control_cancel', 'request_id': dialog['request_id'],
                       'session_id': dialog['session_id']})
            deadline = time.monotonic() + 5
            while not canceled_send.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert canceled_send.read_text() == 'canceled-send-entered'
            # The callback's first cancellation has been caught and its reply is
            # now waiting behind the actual undrained frame. Close must signal again.
            stop = stop.removeprefix('cancel-')
        if stop == 'quit':
            peer.send({'type': 'quit'})
        elif stop == 'eof':
            peer.process.stdin.close()
        else:
            peer.process.stdout.close()
        assert peer.process.wait(timeout=8) == (1 if stop == 'closed-output' else 0), peer.process.stderr.read()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)  # Existence check only, never a signal to a user service.
        assert not (tmp_path / 'cli-child-target').exists()
    finally:
        peer.close()
