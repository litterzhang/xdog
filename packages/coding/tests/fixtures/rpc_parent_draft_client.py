"""Physical offline RPC parent recovery frontend using shipped receiver/review/editor."""
from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import termios
import threading
from pathlib import Path

from xdog.tui import TUI
from xdog.tui.attachments import attachment
from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.parent_draft_receiver import ParentDraftReceiver, composer_receipt
from xdog.tui.tui import Component


class Theme:
    accent = bold = dim = border = staticmethod(lambda value: value)


class Frontend(Component):
    def __init__(self, fault: bool, images: bool) -> None:
        self.fault, self.has_images = fault, images
        self.tui, self.editor = TUI(), InputPanel(Theme())
        self.incoming: queue.Queue[dict] = queue.Queue()
        self.receiver = ParentDraftReceiver()
        self.review = None
        self.caps = None
        self.scope = None
        self.request = ''
        self.status = 'RPC parent connecting'
        self.revision = 0
        self.images = ()
        self.decoder = None
        self.editor.on_change = self.edited
        self.editor.on_submit = self.submit
        home = Path.cwd() / 'rpc-home'
        home.mkdir(mode=0o700)
        sessions = home / 'sessions'
        sessions.mkdir(mode=0o700)
        self.store = ChildDraftStore(sessions / 'saved-parent-drafts')
        image = attachment(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='),
            'physical.png')
        self.saved = self.store.save(encode_draft(ChildDraft('RPC-PHYSICAL-RECOVERED',
            images=(image,) if images else ()),
            label='physical', scope='saved-original', action='parent', slot=''))
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('rpc_client.py'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, 'CODING_DIR': str(home), 'XDOG_RPC_MARKER': str(Path.cwd() / 'rpc-model')})
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        self.tui.add_child(self)
        self.tui.set_focus(self.editor)
        self.tui.add_input_listener(self.input)
        self.tui.on_tick(self.poll)

    def edited(self, text):
        self.revision += 1

    def composer(self):
        return composer_receipt(ChildDraft(self.editor.get_text(), images=self.images), self.revision)

    def read(self):
        for line in self.process.stdout:
            self.incoming.put(json.loads(line))

    def send(self, packet):
        with Path('rpc-parent-wire.jsonl').open('a') as output:
            output.write(json.dumps(packet) + '\n')
        self.process.stdin.write(json.dumps(packet) + '\n')
        self.process.stdin.flush()

    def notice(self, text):
        self.status = text
        self.tui.request_render()

    def submit(self, text):
        if text == '/quit':
            self.send({'type': 'quit'})
            self.tui.stop()
        else:
            self.notice('Draft remains unsent; no model calls')
            self.editor.set_text(text)

    def cancel(self):
        self.receiver.close()
        self.review = None
        self.tui.set_focus(self.editor)
        self.notice('RPC parent recovery canceled; composer unchanged')

    def input(self, event: KeyEvent):
        if self.review is not None:
            return None  # Actual focused shipped review owns Enter/Escape.
        if event.matches('ctrl+c'):
            self.editor.set_text('')
            return {'consume': True}
        if event.matches('f8') and self.caps is not None:
            if self.editor.get_text() or self.images:
                self.notice('Empty composer required')
                return {'consume': True}
            self.scope = {'request_id': 'read', **{key: self.caps[key] for key in (
                'session_id', 'session_revision', 'parent_draft_owner', 'parent_draft_generation')}}
            self.request = 'read'
            self.receiver.expect(self.scope, self.composer(), self.saved.id, self.saved.sha256)
            self.send({'type': 'parent_draft_control', **self.scope, 'action': 'read',
                       'composer': self.composer(), 'snapshot_id': self.saved.id,
                       'snapshot_sha256': self.saved.sha256})
            self.notice('RPC parent reading; no adoption')
            return {'consume': True}
        return None

    def poll(self):
        while not self.incoming.empty():
            event = self.incoming.get_nowait()
            kind = event['type']
            if kind == 'ready':
                self.send({'type': 'rpc_hello', 'parent_saved_drafts': 1, 'parent_saved_images': int(self.has_images)})
            elif kind == 'parent_draft_capabilities':
                self.caps = event
                self.send({'type': 'parent_draft_control', **{key: event[key] for key in (
                    'session_id', 'session_revision', 'parent_draft_owner', 'parent_draft_generation')},
                    'request_id': 'catalog', 'action': 'catalog'})
            elif kind == 'parent_draft_result' and event['action'] == 'catalog':
                assert event['success'] and event['catalog'][0]['id'] == self.saved.id
                self.notice('RPC PARENT READY · F8 reads for review')
            elif kind in {'parent_draft_begin', 'parent_draft_chunk', 'parent_draft_end'}:
                if self.fault and kind == 'parent_draft_end':
                    continue  # Inject transport truncation, never modify production acceptance.
                self.receiver.feed(event)
            elif kind == 'parent_draft_result' and event['action'] == 'read':
                result = dict(event)
                def decode():
                    try:
                        recovery = self.receiver.finish('read', result)
                        self.incoming.put({'type': 'decoded', 'recovery': recovery})
                    except ValueError:
                        self.incoming.put({'type': 'decode_failed'})
                self.decoder = threading.Thread(target=decode, daemon=True)
                self.decoder.start()
            elif kind == 'decoded':
                recovery = event['recovery']
                def adopt(draft, expected):
                    if self.composer() != expected or not expected['empty']:
                        raise ValueError('Composer changed')
                    self.images = draft.images
                    self.editor.set_text(draft.text)
                    self.review = None
                    self.tui.set_focus(self.editor)
                    self.notice(f'RPC parent recovered · {len(draft.images)} images · NOT SENT')
                self.review = recovery.panel(lambda: self.scope, self.composer, adopt, self.cancel)
                self.tui.set_focus(self.review)
                self.notice('RPC parent full receipt review')
            elif kind == 'decode_failed':
                self.receiver.close()
                self.tui.set_focus(self.editor)
                self.notice('RPC parent incomplete receipt refused; empty composer preserved')
            elif kind == 'error':
                self.notice('RPC error: ' + event.get('message', ''))
        self.tui.request_render()

    def render(self, width):
        if self.review is not None:
            self.review.set_render_budget(10)
            return [self.status[:width], *self.review.render(width)]
        return [self.status[:width], *self.editor.render(width)]

    def close(self):
        if self.decoder is not None:
            self.decoder.join(3)
            assert not self.decoder.is_alive()
        self.receiver.close()
        if self.process.poll() is None:
            self.send({'type': 'quit'})
            self.process.wait(timeout=6)
        self.reader.join(3)
        assert not self.reader.is_alive()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


if __name__ == '__main__':
    before = termios.tcgetattr(0)
    frontend = Frontend(sys.argv[1] == 'incomplete', len(sys.argv) > 2 and sys.argv[2] == 'image')
    try:
        frontend.tui.start()
    finally:
        frontend.close()
        restored = before == termios.tcgetattr(0)
        Path('termios-result.txt').write_text(str(restored))
        print(f'TERMIOS-RESTORED={restored}', flush=True)
