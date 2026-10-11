from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent


def review(panel):
    panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))
    for _ in range(200):
        panel.render(90)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter'))


@pytest.mark.parametrize('ending', ['apply', 'withdraw', 'producer-error', 'legacy-off'])
async def test_claw_real_socket_review_waits_for_original_gateway_producer_and_canonical_compaction(tmp_path, ending):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/aftercompact', root / 'aftercompact')
    calls = []
    def stream(model, context, options):
        calls.append(context)
        text = 'ANSWER' if len(calls) == 1 else 'SUMMARY'
        return EventStream.empty(AssistantMessage(content=(TextContent(text=text),)))
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('aftercompact',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    runtime.flush_runner = None
    history = []
    for index in range(6):
        history.extend([UserMessage(content='old history ' + str(index) + ' x' * 1000),
                        AssistantMessage(content=(TextContent(text='old answer'),))])
    session.agent.replace_messages(history)
    session._restored = True
    session._persisted_count = 0
    held, release = asyncio.Event(), asyncio.Event()
    original = session.extensions.event
    async def event(name, *args, **kwargs):
        await original(name, *args, **kwargs)
        if name == 'turn_end':
            held.set()
            await release.wait()
            if ending == 'producer-error':
                raise ValueError('Producer finalization failed')
    session.extensions.event = event
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(12):
            while not predicate():
                ui._poll()
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._extension_compaction_after_run_supported
        if ending == 'legacy-off':
            ui._queue_request({'type': 'ping', 'extension_compaction': 0})
            await wait(lambda: not ui._extension_compaction_supported)
            assert ui._extension_compaction_after_run_supported
        ui._handle_submit('original producer')
        await wait(lambda: ui._extension_dialog_panel is not None)
        assert ui._extension_dialog_panel.dialog['after_run'] is True and len(calls) == 1
        review(ui._extension_dialog_panel)
        await asyncio.wait_for(held.wait(), 8)
        assert len(calls) == 1 and not session._compacting
        if ending == 'withdraw':
            ui._queue_request({'type': 'ping', 'extension_compaction_after_run': 0})
            await wait(lambda: not ui._extension_compaction_after_run_supported)
        release.set()
        await wait(lambda: ui._active_run_id is None and not ui._waiting)
        if ending in {'apply', 'legacy-off'}:
            await wait(lambda: len(calls) == 2 and ui._compact_pending is None
                       and session._compaction_metadata is not None)
            assert not calls[1].tools and 'Compacted:' in '\n'.join(ui._chat_log.render(120))
        else:
            await asyncio.sleep(.1)
            ui._poll()
            assert len(calls) == 1 and session._compaction_metadata is None
    finally:
        release.set()
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
