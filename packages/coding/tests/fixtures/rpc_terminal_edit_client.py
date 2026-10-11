"""Owned physical RPC editing-data frontend using shipped prompt arbitration."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import termios
import threading
from pathlib import Path

from xdog.tui import TUI
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.prompt_keymap import PromptKeymap
from xdog.tui.prompt_terminal import PromptTerminalInterceptor, prompt_key_policy
from xdog.tui.tui import Component


class Theme:
    accent = bold = dim = border = staticmethod(lambda value: value)


class Frontend(Component):
    def __init__(self, child: bool) -> None:
        self.child = child
        self.release_observation = os.environ.get('XDOG_RPC_RELEASE') == '1'
        self.tui, self.editor = TUI(), InputPanel(Theme())
        self.status = 'RPC editor connecting'
        self.incoming: queue.Queue[dict] = queue.Queue()
        self.caps = None
        self.child_id = ''
        self.pending = None
        self.generation = 0
        self.keymap = PromptKeymap()
        self.env = {**os.environ, 'CODING_DIR': str(Path.cwd() / 'rpc-home'),
                    'XDOG_RPC_MARKER': str(Path.cwd() / 'rpc-model')}
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('rpc_client.py'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        self.tui.add_child(self)
        self.tui.set_focus(self.editor)
        self.editor.on_submit = self.submit
        self.hooks = PromptTerminalInterceptor(self.tui, lambda: self.tui._focused is self.editor,
            lambda: str(self.generation), lambda: self.caps is not None,
            lambda event: prompt_key_policy(event, self.keymap.resolve(event)),
            self.transform, self.cancel, self.notice, surface=lambda: 'child-prompt' if child else 'prompt',
            release_available=self.release_available)
        self.tui.add_input_listener(self.host_input)
        self.tui.on_tick(self.poll)

    def release_available(self) -> bool:
        if not self.release_observation or self.caps is None:
            return False
        if self.child:
            return (self.caps.get('child_terminal_release_input') == 1 and 'release' in
                    self.caps['child_terminal_registry'].get(self.child_id, {}).get('events', []))
        return self.caps.get('terminal_release_input') == 1 and 'release' in self.caps.get('terminal_events', [])

    def read(self) -> None:
        for line in self.process.stdout:
            self.incoming.put(json.loads(line))

    def send(self, event: dict) -> None:
        with Path('rpc-edit-receipts.jsonl').open('a') as output:
            output.write(json.dumps(event, ensure_ascii=False) + '\n')
        self.process.stdin.write(json.dumps(event) + '\n')
        self.process.stdin.flush()

    def notice(self, message: str) -> None:
        self.status = message
        self.tui.request_render()

    def host_input(self, event):
        if event.ctrl and event.key == 'c':
            self.editor.set_text('')
            self.tui.request_render()
            return {'consume': True}
        return None

    def submit(self, text: str) -> None:
        if text == '/quit':
            self.send({'type': 'quit'})
            self.tui.stop()
        else:
            self.notice('Only /quit is submitted by this test frontend; no model turn')

    def transform(self, held) -> bool:
        if self.pending is not None or self.caps is None:
            return False
        if self.child:
            envelope = {key: self.caps[key] for key in ('session_id', 'session_revision', 'child_owner',
                                                        'child_epoch', 'child_return_owner')}
            registry = self.caps['child_terminal_registry'][self.child_id]
            event = {'type': 'child_terminal_input', **envelope, 'child_id': self.child_id,
                     'terminal_generation': self.caps['terminal_generation'],
                     'worker_generation': registry['generation']}
        else:
            event = {'type': 'terminal_input', **{key: self.caps[key] for key in (
                'session_id', 'session_revision', 'terminal_owner', 'worker_generation')}}
        event.update(request_id=f'edit-{held.id}', kind=held.kind, data=held.data)
        self.pending = held, event
        self.send(event)
        return True

    def cancel(self) -> None:
        pending, self.pending = self.pending, None
        if pending is None:
            return
        _held, packet = pending
        event = {key: value for key, value in packet.items() if key not in {'kind', 'data'}}
        event['type'] = 'child_control_cancel' if self.child else 'terminal_cancel'
        self.send(event)

    def poll(self) -> None:
        while not self.incoming.empty():
            event = self.incoming.get_nowait()
            kind = event['type']
            if kind == 'ready':
                self.ready = event
                if self.child:
                    self.send({'type': 'rpc_hello', 'child_controls': 1, 'child_terminal_input': 1,
                               'child_terminal_release_input': int(self.release_observation)})
                else:
                    self.send({'type': 'extension_control', 'request_id': 'load', 'session_id': event['session_id'],
                               'action': 'list', 'name': '', 'args': ''})
            elif kind == 'extension_result' and not self.child:
                self.send({'type': 'rpc_hello', 'terminal_input': 1,
                           'terminal_release_input': int(self.release_observation)})
            elif kind == 'terminal_capabilities' and not self.child and event['active']:
                self.caps = event
                self.notice('RPC EDIT READY parent')
            elif kind == 'child_capabilities' and self.child and not self.child_id:
                self.start_caps = event
                self.send({'type': 'child_control', 'request_id': 'start', 'action': 'start', 'task': 'hold',
                    **{key: event[key] for key in ('session_id', 'session_revision', 'child_owner',
                                                 'child_epoch', 'child_return_owner')}})
            elif kind == 'child_result' and event.get('action') == 'start':
                assert event['success']
                self.child_id = event['child_id']
            elif kind == 'child_capabilities' and self.child and self.child_id:
                if event['child_terminal_registry'].get(self.child_id, {}).get('active'):
                    self.caps = event
                    self.notice('RPC EDIT READY child')
            elif kind in ('terminal_result', 'child_terminal_result') and self.pending is not None:
                held, sent = self.pending
                if event['request_id'] != sent['request_id']:
                    continue
                self.pending = None
                result = ({'consume': event['consume'], **({'data': event['data']} if 'data' in event else {})}
                          if event['success'] else {'error': True})
                self.hooks.queue.accept(held.id, result)
            elif kind == 'error':
                self.notice('RPC ERROR ' + event.get('message', ''))
        if self.child_id and self.caps is None:
            self.send({'type': 'rpc_hello', 'child_terminal_input': 1,
                       'child_terminal_release_input': int(self.release_observation)})
        if self.hooks.poll():
            self.tui.request_render()

    def render(self, width: int) -> list[str]:
        return [self.status[:width], *self.editor.render(width)]

    def close(self) -> None:
        self.hooks.close()
        if self.process.poll() is None:
            self.send({'type': 'quit'})
            self.process.wait(timeout=8)
        self.reader.join(3)
        assert not self.reader.is_alive()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


if __name__ == '__main__':
    before = termios.tcgetattr(0)
    app = Frontend(sys.argv[1] == 'child')
    try:
        app.tui.start()
    finally:
        app.close()
        restored = before == termios.tcgetattr(0)
        Path('termios-result.txt').write_text(str(restored))
        print(f'TERMIOS-RESTORED={restored}', flush=True)
