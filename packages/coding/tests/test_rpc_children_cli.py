"""Actual CLI child loop, independent approval/source messages and disconnect ownership."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path


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
