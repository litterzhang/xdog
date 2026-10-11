"""Actual CLI flood with undrained output must retain independent control/cleanup."""
from __future__ import annotations

import json
import os
import selectors
import time

import pytest

from packages.coding.tests.test_rpc_transcript_cli import Peer


@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child'])
@pytest.mark.parametrize('stop', ['quit', 'eof', 'closed-output'])
def test_actual_cli_transient_flood_undrained_peer_retains_reader_and_settles_producer(
    tmp_path, monkeypatch, child, stop,
):
    monkeypatch.setenv('XDOG_RPC_TRANSIENT_FLOOD', '1')
    peer = Peer(tmp_path)
    def read(kind):
        line = bytearray()
        deadline = time.monotonic() + 6
        with selectors.DefaultSelector() as selector:
            selector.register(peer.process.stdout.fileno(), selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(max(0, deadline - time.monotonic())):
                    continue
                byte = os.read(peer.process.stdout.fileno(), 1)
                assert byte, 'CLI ended before the owned receipt'
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
            peer.send({'type': 'rpc_hello', 'child_controls': 1, 'child_transient_events': 1})
            caps = read('child_capabilities')
            while not caps['child_transient_events']:
                caps = read('child_capabilities')
            peer.send({'type': 'child_control', 'request_id': 'start', 'action': 'start', 'task': 'hold',
                       **{key: caps[key] for key in ('session_id', 'session_revision', 'child_owner',
                                                    'child_epoch', 'child_return_owner')}})
        else:
            peer.send({'type': 'rpc_hello', 'transient_events': 1})
            assert read('transient_capabilities')['transient_events'] == 1
            peer.send({'type': 'prompt', 'message': 'hold'})
        # Do not drain progress. Marker proves all 128 actual provider events were
        # admitted despite a full pipe, not merely that the process remained alive.
        deadline = time.monotonic() + 8
        while not peer.marker.with_suffix('.flood').exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert peer.marker.with_suffix('.flood').exists()
        assert peer.process.poll() is None
        if stop == 'quit':
            peer.send({'type': 'quit'})
        elif stop == 'eof':
            peer.process.stdin.close()
        else:
            peer.process.stdout.close()
        assert peer.process.wait(timeout=5) == (1 if stop == 'closed-output' else 0), peer.process.stderr.read()
        assert peer.marker.with_suffix('.settled').exists()
    finally:
        peer.close()
