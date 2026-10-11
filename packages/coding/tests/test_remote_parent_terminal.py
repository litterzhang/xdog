"""Actual ChatApp remote parent recovery on private Linux terminal/socket."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from xdog.tui.attachments import attachment
from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
from xdog.tui.child_drafts import ChildDraft

from packages.claw.tests.test_mcp_sampling_gateway import setup
from packages.coding.tests.test_terminal_acceptance import Terminal
from packages.tui.tests.test_parent_draft_receiver import PNG

pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('tmux'), reason='Private Linux tmux required')


@pytest.mark.parametrize('images', [False, True])
def test_physical_claw_remote_parent_review_resize_recover_without_submission(tmp_path, images):
    ready, stop = threading.Event(), threading.Event()
    owned, errors = {}, []
    def serve():
        async def run():
            server = None
            try:
                server, config, runtime, session, adapter, _ = await setup(tmp_path)
                Path(config.data_dir).chmod(0o700)
                store = ChildDraftStore(Path(config.data_dir) / 'saved-parent-drafts')
                draft = ChildDraft('REMOTE-PARENT-PHYSICAL', images=(attachment(PNG, 'remote.png'),) if images else ())
                saved = store.save(encode_draft(draft, label='physical', scope=session.meta.session_id,
                                              action='parent', slot=''))
                owned.update(config=config, adapter=adapter, session=session, store=store, saved=saved)
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(.01)
            except BaseException as exc:
                errors.append(exc)
                ready.set()
            finally:
                if server is not None:
                    await server.stop()
        asyncio.run(run())
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    terminal = Terminal(tmp_path)
    try:
        assert ready.wait(6) and not errors
        terminal.start('claw', owned['config'].socket_path)
        terminal.text('/draft remote')
        terminal.keys('Enter')
        terminal.wait('Select snapshot; empty composer required')
        terminal.keys('Enter')
        terminal.wait('Review saved parent snapshot')
        assert not owned['adapter'].calls
        terminal.resize(60, 18)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if 'Enter approves exact output' in terminal.visible():
                break
            terminal.keys('NPage')
            time.sleep(.04)
        else:
            raise AssertionError(terminal.visible())
        assert not owned['adapter'].calls
        terminal.keys('Enter')
        terminal.wait_editor('REMOTE-PARENT-PHYSICAL')
        if images:
            terminal.wait('Images: 1')
        assert not owned['adapter'].calls and not owned['session'].agent.messages
        assert owned['store'].load(owned['saved'].id).sha256 == owned['saved'].sha256
        terminal.wait_cursor(True)
        terminal.finish()
    finally:
        terminal.call('kill-server')
        stop.set()
        worker.join(6)
        assert not worker.is_alive() and not errors


@pytest.mark.parametrize('operation', ['save', 'save-image', 'discard'])
def test_physical_claw_remote_parent_disk_review_before_confirmation(tmp_path, operation):
    ready, stop = threading.Event(), threading.Event()
    owned, errors = {}, []
    def serve():
        async def run():
            server = None
            try:
                server, config, runtime, session, adapter, _ = await setup(tmp_path)
                Path(config.data_dir).chmod(0o700)
                store = ChildDraftStore(Path(config.data_dir) / 'saved-parent-drafts')
                if operation == 'discard':
                    store.save(encode_draft(ChildDraft('REMOTE-DISK-DELETE'), label='delete',
                                           scope=session.meta.session_id, action='parent', slot=''))
                owned.update(config=config, adapter=adapter, session=session, store=store)
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(.01)
            except BaseException as exc:
                errors.append(exc)
                ready.set()
            finally:
                if server is not None:
                    await server.stop()
        asyncio.run(run())
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    terminal = Terminal(tmp_path)
    try:
        assert ready.wait(6) and not errors
        home = tmp_path / 'client-home'
        home.mkdir(mode=0o700)
        (home / 'keybindings.json').write_text(json.dumps({'remote_save_draft': 'f8'}))
        terminal.child_env['CLAW_DIR'] = str(home)
        terminal.start('claw', owned['config'].socket_path)
        if operation in {'save', 'save-image'}:
            if operation == 'save-image':
                source = tmp_path / 'large-parent.png'
                source.write_bytes(PNG + b'X' * (1024 * 1024))
                terminal.text('/attach ' + str(source))
                terminal.keys('Enter')
                terminal.wait('Images: 1')
            terminal.text('REMOTE-UNSENT-SAVE')
            terminal.wait_editor('REMOTE-UNSENT-SAVE')
            terminal.keys('F8')
            terminal.wait('Review full remote save')
            terminal.resize(60, 18)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if 'Enter approves exact output' in terminal.visible():
                    break
                terminal.keys('NPage')
                time.sleep(.04)
            else:
                raise AssertionError(terminal.visible())
            assert not owned['store'].catalog() and not owned['adapter'].calls
            terminal.keys('Enter')
            terminal.wait('Remote parent disk save_confirm completed')
            terminal.wait_editor('REMOTE-UNSENT-SAVE')
            rows = owned['store'].catalog()
            assert len(rows) == 1 and owned['store'].load(rows[0]['id']).draft.text == 'REMOTE-UNSENT-SAVE'
            if operation == 'save-image':
                assert owned['store'].load(rows[0]['id']).draft.images[0].data == source.read_bytes()
                terminal.wait('Images: 1')
        else:
            terminal.text('/draft remote')
            terminal.keys('Enter')
            terminal.wait('Select snapshot; empty composer required')
            terminal.text('x')
            terminal.wait('Review full remote discard')
            terminal.resize(60, 18)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if 'Enter approves exact output' in terminal.visible():
                    break
                terminal.keys('NPage')
                time.sleep(.04)
            else:
                raise AssertionError(terminal.visible())
            assert len(owned['store'].catalog()) == 1 and not owned['adapter'].calls
            terminal.keys('Enter')
            terminal.wait('Remote parent disk discard_confirm completed')
            assert not owned['store'].catalog()
        terminal.wait_cursor(True)
        terminal.finish()
        assert not owned['adapter'].calls and not owned['session'].agent.messages
    finally:
        terminal.call('kill-server')
        stop.set()
        worker.join(6)
        assert not worker.is_alive() and not errors
