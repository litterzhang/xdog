"""Owned offline RPC frontend for physical terminal acceptance, not a production client."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import termios
import threading
from pathlib import Path

from xdog.tui import TUI, Component
from xdog.tui.components.extension_custom import ExtensionCustom
from xdog.tui.components.input_panel import InputPanel


class Theme:
    accent = bold = dim = border = staticmethod(lambda value: value)


class Frontend(Component):
    def __init__(self, child: bool) -> None:
        self.child = child
        self.tui = TUI()
        self.incoming: queue.Queue[dict] = queue.Queue()
        env = {**os.environ, 'CODING_DIR': str(Path.cwd() / 'rpc-home'),
               'XDOG_RPC_MARKER': str(Path.cwd() / 'rpc-model'),
               'XDOG_RPC_PERMISSION': 'ask' if child else 'deny', 'XDOG_RPC_CHILD_LAYOUT': str(int(child))}
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('rpc_client.py'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        self.status = 'RPC connecting'
        self.dialog = None
        self.custom = None
        self.caps = None
        self.sequence = 0
        self.approval = None
        self.custom_pending = False
        self.editor = InputPanel(Theme())
        self.editor.on_submit = self.submit
        self.active = self.editor
        self.tui.add_child(self)
        self.tui.set_focus(self.editor)
        self.tui.on_tick(self.poll)
        self.tui.add_input_listener(self.host_input)

    def host_input(self, event):
        if self.active is self.editor and event.ctrl and event.key == 'c':
            self.editor.set_text('')
            self.tui.request_render()
            return {'consume': True}
        return None

    def read(self) -> None:
        for line in self.process.stdout:
            self.incoming.put(json.loads(line))

    def send(self, packet: dict) -> None:
        with Path('rpc-terminal-receipts.jsonl').open('a') as log:
            log.write(json.dumps(packet, ensure_ascii=False) + '\n')
        self.process.stdin.write(json.dumps(packet) + '\n')
        self.process.stdin.flush()

    def child_packet(self, **fields) -> dict:
        return {**{key: self.caps[key] for key in ('session_id', 'session_revision', 'child_owner',
                                                   'child_epoch', 'child_return_owner')}, **fields}

    def switch(self, panel) -> None:
        self.active = panel
        self.tui.set_focus(panel)
        self.tui.request_render()

    def custom_request(self, event: dict) -> bool:
        if self.custom_pending and event['kind'] != 'resize':
            return False
        self.custom_pending = True
        if self.custom is not None:
            self.custom._frame = None
            self.custom._painted = -1
        self.sequence += 1
        fields = {'id': self.dialog['dialog']['id'], 'custom_generation': self.dialog['custom_generation'],
                  'sequence': self.sequence, 'event': event}
        if self.child:
            packet = self.child_packet(type='child_custom_input', child_id=self.dialog['child_id'], **fields)
        else:
            packet = {'type': 'extension_custom_input', 'session_id': self.dialog['session_id'],
                      'request_id': self.dialog['request_id'], 'scope': self.dialog['scope'], **fields}
        self.send(packet)
        return True

    def answer(self, dialog: dict, value) -> None:
        if self.child:
            self.send(self.child_packet(type='child_dialog_response', child_id=dialog['child_id'],
                                        id=dialog['dialog']['id'], value=value))
        else:
            self.send({'type': 'extension_dialog_response', 'session_id': dialog['session_id'],
                'request_id': dialog['request_id'], 'scope': dialog['scope'],
                'id': dialog['dialog']['id'], 'value': value})

    def submit(self, text: str) -> None:
        if self.approval is not None:
            if text != 'y':
                self.status = 'TOOL APPROVAL requires y; no view input grant'
                return
            question = self.approval
            self.send(self.child_packet(type='child_permission_response', id=question['id'],
                child_id=question['child_id'], decision='allow_once',
                approval_generation=question['approval_generation']))
            self.approval = None
            self.editor.set_text('')
            self.status = 'RPC child tool approved separately'
        elif text == '/quit':
            self.send({'type': 'quit'})
            self.tui.stop()

    def poll(self) -> None:
        while not self.incoming.empty():
            event = self.incoming.get_nowait()
            kind = event['type']
            if kind == 'ready':
                self.ready = event
                self.send({'type': 'rpc_hello', **({'child_controls': 1, 'child_custom_views': 1,
                    'child_dialogs': 1, 'child_approvals': 1} if self.child else
                    {'extension_custom_views': 1, 'extension_dialogs': 1})})
            elif kind == 'child_capabilities' and self.child and event['child_custom_views']:
                if self.caps is None:
                    self.caps = event
                    self.send(self.child_packet(type='child_control', request_id='start', action='start',
                                                task='layout', access='inherit'))
            elif kind == 'extension_custom_capabilities' and not self.child:
                self.send({'type': 'extension_control', 'request_id': 'layout', 'session_id': self.ready['session_id'],
                           'action': 'command', 'name': 'ext-layout-view', 'args': 'scroll'})
            elif kind == 'child_permission_request':
                self.approval = event
                self.status = 'TOOL APPROVAL · type y then Enter · custom input unavailable'
                self.switch(self.editor)
            elif kind in ('extension_dialog', 'child_extension_dialog'):
                dialog = event['dialog']
                if dialog['kind'] == 'custom':
                    self.dialog = event
                    self.status = ('RPC ' + ('child' if self.child else 'parent')
                                   + ' custom frame · Linux physical client')
                    self.custom = ExtensionCustom(dialog['extension'], dialog,
                                                  lambda value, captured=event: self.answer(captured, value),
                                                  self.custom_request)
                    self.switch(self.custom)
                elif dialog['kind'] == 'input':
                    question = InputPanel(Theme())
                    question.set_text(dialog.get('default', ''))
                    def answer(text, captured=event):
                        self.answer(captured, text)
                    question.on_submit = answer
                    self.status = dialog['title']
                    self.switch(question)
            elif kind in ('extension_custom_frame', 'child_extension_custom_frame'):
                if self.custom is not None and event['id'] == self.custom.dialog['id']:
                    self.custom.accept(event)
                    if self.custom._frame is not None:
                        self.custom_pending = False
                        self.custom._inflight = 0  # Latest geometry supersedes earlier fixture resize requests.
                    self.tui.request_render()
            elif kind in ('extension_dialog_closed', 'child_extension_dialog_closed'):
                if self.custom is not None and event['id'] != self.custom.dialog['id']:
                    self.custom_pending = False
                    self.status = 'RPC nested returned · fresh frame required'
                    self.switch(self.custom)
            elif kind == 'extension_result' or kind == 'child_status' and any(
                    row['status'] == 'completed' for row in event['catalog']['children']):
                Path('rpc-terminal-result.json').write_text(json.dumps(event))
                self.custom = None
                self.status = 'RPC COMPLETE · parent editor recovered'
                self.editor.set_text('')
                self.switch(self.editor)
            elif kind == 'error':
                self.status = 'RPC ERROR ' + event.get('message', '')
                self.tui.request_render()
        if self.custom is not None and self.custom.poll():
            self.tui.request_render()

    def render(self, width: int) -> list[str]:
        if self.custom is not None:
            self.custom.set_render_budget(min(18, max(6, os.get_terminal_size().lines - 4)))
        return [self.status[:width], *self.active.render(width)]

    def close(self) -> None:
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
