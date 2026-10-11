"""Physical Claw image-only requested messages require full descriptor review before model delivery."""
from __future__ import annotations

import asyncio
import shutil
import sys
import threading
import time

import pytest
from xdog.ai.types import ImageContent

from packages.claw.tests.test_extension_message_images import image_server
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned current-Linux terminal required')


@pytest.mark.parametrize('approve', [False, True])
def test_physical_claw_image_only_message_whole_review_and_no_draft_capture(tmp_path, approve):
    ready, stop = threading.Event(), threading.Event()
    owned, errors = {}, []
    def serve():
        async def run():
            async with image_server(tmp_path) as (server, session, config, _model, calls):
                owned.update(server=server, session=session, config=config, calls=calls)
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(.01)
        try:
            asyncio.run(run())
        except BaseException as exc:
            errors.append(exc)
            ready.set()
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    assert ready.wait(6) and not errors
    terminal = Terminal(tmp_path)
    try:
        terminal.start('claw', owned['config'].socket_path)
        terminal.resize(100, 30)
        terminal.text('/ext-messagerequest-images {"text":""}')
        terminal.keys('Enter')
        terminal.wait('Send this extension message')
        assert not owned['calls']
        if approve:
            terminal.keys('Right', 'Enter')
            terminal.wait('form review')
            terminal.resize(60, 18)
            deadline = time.monotonic() + 10
            reviewed = ''
            while time.monotonic() < deadline:
                frame = terminal.visible()
                reviewed += frame
                if 'Enter submits reviewed form' in frame:
                    break
                terminal.keys('NPage')
                time.sleep(.04)
            else:
                raise AssertionError(terminal.visible())
            assert 'image/png' in reviewed and 'sha256' in reviewed and not owned['calls']
            terminal.keys('Enter')
            terminal.wait('IMAGE-MESSAGE-DONE')
            assert len(owned['calls']) == 1
            assert any(isinstance(part, ImageContent) for message in owned['calls'][0].messages
                       for part in message.content if not isinstance(message.content, str))
        else:
            terminal.keys('Escape')
            terminal.wait('IMAGE-MESSAGE-False')
            assert not owned['calls'] and not owned['session'].agent.messages
        terminal.wait_cursor(True)
        terminal.text('UNSENT AFTER IMAGE MESSAGE REVIEW')
        terminal.wait_editor('UNSENT AFTER IMAGE MESSAGE REVIEW')
        terminal.finish()
    finally:
        terminal.call('kill-server')
        stop.set()
        worker.join(12)
        assert not worker.is_alive() and not errors
