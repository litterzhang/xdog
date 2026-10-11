"""Actual CLI cancellation/disconnect during a child terminal hook reaps its owner."""
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

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Owned Linux pipe/worker lifecycle proof')


@pytest.mark.parametrize('child_mode', [False, True], ids=['parent', 'child'])
@pytest.mark.parametrize('stop', ['quit', 'eof', 'closed-output', 'timeout'])
def test_actual_cli_terminal_hook_owner_settles_on_disconnect_or_deadline(tmp_path, stop, child_mode):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/terminal', root / 'terminal')
    marker, pid_marker = tmp_path / 'terminal-entered', tmp_path / 'terminal-worker'
    main = root / 'terminal/main.py'
    text = main.read_text().replace('def setup(api):',
        'def setup(api):\n    import os\n    from pathlib import Path\n'
        f'    Path({str(pid_marker)!r}).write_text(str(os.getpid()))')
    text = text.replace("        if data == 'hang':", "        if data == 'hang':\n"
                        f"            Path({str(marker)!r}).write_text('entered')")
    main.write_text(text)
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['terminal'], 'extension_dir': str(root)}))
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
                assert byte, 'CLI ended before owned receipt: ' + kind
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
        if child_mode:
            peer.send({'type': 'rpc_hello', 'child_controls': 1, 'child_terminal_input': 1})
            caps = read('child_capabilities')
            while not caps['child_terminal_input']:
                caps = read('child_capabilities')
            envelope = {key: caps[key] for key in ('session_id', 'session_revision', 'child_owner',
                                                  'child_epoch', 'child_return_owner')}
            peer.send({'type': 'child_control', 'request_id': 'start', 'action': 'start', 'task': 'hold', **envelope})
            child = read('child_result')
            assert child['success']
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                peer.send({'type': 'rpc_hello', 'child_terminal_input': 1})
                caps = read('child_capabilities')
                registry = caps['child_terminal_registry'].get(child['child_id'])
                if registry and registry['active'] and peer.marker.exists():
                    break
                time.sleep(0.01)
            else:
                raise AssertionError('Actual child worker/model admission unavailable')
            hook_request = {'type': 'child_terminal_input', 'request_id': 'hook', **envelope,
                   'child_id': child['child_id'], 'terminal_generation': caps['terminal_generation'],
                   'worker_generation': registry['generation'], 'kind': 'key', 'data': 'hang'}
        else:
            peer.send({'type': 'extension_control', 'request_id': 'load', 'session_id': peer.ready['session_id'],
                       'action': 'list', 'name': '', 'args': ''})
            assert read('extension_result')['success']
            peer.send({'type': 'rpc_hello', 'terminal_input': 1})
            caps = read('terminal_capabilities')
            assert caps['active'] and caps['terminal_input'] == 1
            hook_request = {'type': 'terminal_input', 'request_id': 'hook', 'kind': 'key', 'data': 'hang',
                **{key: caps[key] for key in ('session_id', 'session_revision', 'terminal_owner', 'worker_generation')}}
        pid = int(pid_marker.read_text())
        argv = Path(f'/proc/{pid}/cmdline').read_bytes()
        assert b'extensions/worker.py' in argv and str(root / 'terminal').encode() in argv
        peer.send(hook_request)
        deadline = time.monotonic() + 6
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists() and peer.process.poll() is None
        if stop == 'timeout':
            receipt = read('child_terminal_result' if child_mode else 'terminal_result')
            assert not receipt['success'] and 'data' not in receipt and 'consume' not in receipt
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            peer.send({'type': 'status'})
            status = read('status')
            assert status['messages'] == 0 and status['permission_mode'] == 'deny'
            peer.send({'type': 'quit'})
        elif stop == 'quit':
            peer.send({'type': 'quit'})
        elif stop == 'eof':
            peer.process.stdin.close()
        else:
            peer.process.stdout.close()
            # Wake the ordinary output path while leaving stdin open. A hanging
            # hook emits nothing until deadline, so peer close alone is not an output write.
            peer.send({'type': 'status'})
        assert peer.process.wait(timeout=8) == (1 if stop == 'closed-output' else 0), peer.process.stderr.read()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        if child_mode:
            assert peer.marker.with_suffix('.settled').exists()
        else:
            assert not peer.marker.exists()  # Parent editing hook never invokes the model.
        assert not (tmp_path / 'cli-child-target').exists()
    finally:
        peer.close()
