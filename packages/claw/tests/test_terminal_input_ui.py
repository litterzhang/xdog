from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.stdin_buffer import KeyBytes, Paste


async def start(tmp_path, *, hold=False):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    source = root / 'terminal/main.py'
    source.write_text(source.read_text().replace("        if data == 'bad':", "        if data == '!':\n"
        "            return {'data': '\\r'}\n        if data == '~':\n"
        "            from pathlib import Path\n"
        f"            Path({str(tmp_path / 'terminal-hook-entered')!r}).write_text('entered')\n"
        "            await asyncio.Event().wait()\n        if data == 'bad':"))
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if hold:
            from xdog.ai.types import StartEvent
            async def generate():
                yield StartEvent(partial=AssistantMessage())
                await asyncio.Event().wait()
            return EventStream(generate())
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'terminal.sock'), pid_file=str(tmp_path / 'terminal.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('terminal',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    ui._io_generation = 1
    ui._recv_queue.put({"type": "_connection_opened", "generation": 1})
    task = asyncio.create_task(ui._run_connection(reader, writer))
    await wait(ui, lambda: ui._connection_ready)
    ui._extension_command('extensions', '')
    await wait(ui, lambda: ui._extension_pending is None)
    assert ui._terminal_supported and ui._extension_snapshot['terminal_input'] is True
    return server, ui, reader, writer, task, calls


async def wait(ui, predicate):
    async with asyncio.timeout(8):
        while not predicate():
            ui._poll()
            await asyncio.sleep(0.003)


async def cleanup(server, ui, writer, task):
    ui._request_exit()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    writer.close()
    await writer.wait_closed()
    await server.stop()


async def read_type(reader, kind):
    async with asyncio.timeout(6):
        while True:
            value = json.loads(await reader.readline())
            if value['type'] == kind:
                return value


async def test_real_claw_terminal_key_paste_consume_fifo_one_model_and_host_pause_recovery(tmp_path):
    server, ui, _, writer, task, calls = await start(tmp_path)
    try:
        ui._tui._handle_frame(KeyBytes(b'ab'))
        ui._tui._handle_frame(Paste('drop'))
        ui._tui._handle_frame(Paste('multi\nline'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert ui._editor.get_text() == '[a][b][multi\nline]' and not calls
        runtime = server._orchestrator._runtimes['main']._active_session
        assert await runtime.extensions.command('ext-terminal-count', '') == '3'
        assert not runtime.agent.state.messages and not ui._extension_dialog_queue
        ui._tui._handle_frame(KeyBytes(b'c\r'))
        await wait(ui, lambda: len(calls) == 1 and ui._active_run_id is None)
        assert calls[0].messages[-1].content[0].text == '[a][b][multi\nline][c]'
        assert await runtime.extensions.command('ext-terminal-count', '') == '4'
        ui._tui._handle_frame(KeyBytes(b'!\r'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert ui._terminal_input.paused and len(calls) == 1
        retained = ui._terminal_input.queue.recovery
        assert [item.data for item in retained] == ['!', '\r']
        ui._handle_submit('/terminal-input restore ' + str(retained[0].id))
        assert ui._editor.get_text() == '!' and len(calls) == 1
    finally:
        await cleanup(server, ui, writer, task)


@pytest.mark.parametrize('interrupt', ['escape', 'pause', 'resize', 'overlay'])
async def test_real_claw_owned_pending_callback_cancel_and_no_late_replay(tmp_path, interrupt, monkeypatch):
    from xdog.tui.components.text import Text
    server, ui, _, writer, task, calls = await start(tmp_path)
    runtime = server._orchestrator._runtimes['main']._active_session.extensions
    process = runtime._manager._clients['terminal'].process
    try:
        ui._editor.set_text('draft')
        ui._tui._handle_frame(KeyBytes(b'~'))
        ui._tui._handle_frame(KeyBytes(b'x\r'))
        await wait(ui, lambda: ui._terminal_request is not None)
        old = dict(ui._terminal_request)
        await wait(ui, lambda: (tmp_path / 'terminal-hook-entered').exists())
        if interrupt == 'escape':
            ui._tui._handle_frame(KeyBytes(b'\x1b'))
        elif interrupt == 'pause':
            ui._tui._handle_frame(KeyBytes(b'\x1bi'))
        elif interrupt == 'overlay':
            handle = ui._tui.show_overlay(Text('foreign owner'))
            ui._poll()
            handle.hide()
        else:
            import xdog.tui.tui as module
            monkeypatch.setattr(module, '_terminal_width', lambda: 81)
            ui._poll()
        await wait(ui, lambda: process.returncode is not None)
        ui._handle_response({**old, 'type': 'terminal_input_result', 'error': False, 'data': 'late'})
        ui._poll()
        assert ui._terminal_input.paused and ui._editor.get_text() == 'draft' and not calls
        assert [item.data for item in ui._terminal_input.queue.recovery] == ['~', 'x', '\r']
    finally:
        await cleanup(server, ui, writer, task)


async def test_real_claw_foreign_socket_owner_capability_withdrawal_and_ping_responsiveness(tmp_path):
    server, ui, _, writer, task, calls = await start(tmp_path)
    reader2, writer2 = await asyncio.open_unix_connection(ui.socket_path)
    runtime = server._orchestrator._runtimes['main']._active_session.extensions
    process = runtime._manager._clients['terminal'].process
    try:
        ui._tui._handle_frame(Paste('hang'))
        await wait(ui, lambda: ui._terminal_request is not None)
        await asyncio.sleep(0.1)
        original = dict(ui._terminal_request)
        writer2.write((json.dumps({'type': 'ping', 'group_id': 'main', 'terminal_input': 1}) + '\n').encode())
        await writer2.drain()
        pong = await read_type(reader2, 'pong')
        assert pong['terminal_owner'] != original['terminal_owner']
        writer2.write((json.dumps(original) + '\n').encode())
        writer2.write((json.dumps({**original, 'type': 'terminal_input_cancel'}) + '\n').encode())
        writer2.write(b'{"type":"ping","group_id":"main"}\n')
        await writer2.drain()
        refused = await read_type(reader2, 'terminal_input_result')
        assert refused['error'] is True
        await read_type(reader2, 'pong')
        assert process.returncode is None and not calls
        ui._queue_request({'type': 'ping', 'group_id': 'main', 'terminal_input': 0})
        await wait(ui, lambda: not ui._terminal_supported)
        await wait(ui, lambda: process.returncode is not None)
        assert ui._terminal_input.queue.pending == 0 and ui._terminal_input.queue.recovery[0].data == 'hang'
        ui._queue_request({'type': 'ping', 'group_id': 'main', 'terminal_input': 1})
        await wait(ui, lambda: ui._terminal_supported)
        assert ui._terminal_owner != original['terminal_owner']
        assert not calls
    finally:
        writer2.close()
        await writer2.wait_closed()
        await cleanup(server, ui, writer, task)


async def test_real_claw_reload_refuses_old_generation_before_invoking_new_worker(tmp_path):
    server, ui, _, writer, task, calls = await start(tmp_path)
    reader2, writer2 = await asyncio.open_unix_connection(ui.socket_path)
    try:
        writer2.write(b'{"type":"ping","group_id":"main","terminal_input":1}\n')
        await writer2.drain()
        pong = await read_type(reader2, 'pong')
        packet = {'type': 'terminal_input', 'request_id': 'stale-worker', 'group_id': 'main',
                  'session_id': ui._gateway_session_id, 'terminal_owner': pong['terminal_owner'],
                  'input_owner': 'original-ui', 'input_sequence': 1,
                  'extension_generation': ui._extension_snapshot['terminal_generation'],
                  'kind': 'key', 'surface': 'prompt', 'data': 'x'}
        ui._extension_command('extensions', 'reload terminal')
        await wait(ui, lambda: ui._extension_pending is None)
        assert ui._extension_snapshot['terminal_generation'] > packet['extension_generation']
        writer2.write((json.dumps(packet) + '\n').encode())
        await writer2.drain()
        response = await read_type(reader2, 'terminal_input_result')
        assert response['error'] is True
        runtime = server._orchestrator._runtimes['main']._active_session.extensions
        assert await runtime.command('ext-terminal-count', '') == '0'
        packet['extension_generation'] = ui._extension_snapshot['terminal_generation']
        packet['request_id'] = 'fresh-worker'
        writer2.write((json.dumps(packet) + '\n').encode())
        await writer2.drain()
        response = await read_type(reader2, 'terminal_input_result')
        assert response['data'] == '[x]' and response['error'] is False
        assert await runtime.command('ext-terminal-count', '') == '1' and not calls
    finally:
        writer2.close()
        await writer2.wait_closed()
        await cleanup(server, ui, writer, task)


@pytest.mark.parametrize('change', ['timeout', 'session', 'disconnect'])
async def test_real_claw_pending_hook_deadline_session_handoff_and_reconnect_never_adopt_old_input(tmp_path, change):
    server, ui, _, writer, task, calls = await start(tmp_path)
    runtime = server._orchestrator._runtimes['main']._active_session.extensions
    process = runtime._manager._clients['terminal'].process
    try:
        ui._editor.set_text('draft')
        ui._tui._handle_frame(KeyBytes(b'~'))
        ui._tui._handle_frame(KeyBytes(b'x\r'))
        await wait(ui, lambda: (tmp_path / 'terminal-hook-entered').exists())
        original = dict(ui._terminal_request)
        old_generation, old_session = ui._connection_generation, ui._gateway_session_id
        if change == 'session':
            ui._navigate_session('new')
            await wait(ui, lambda: ui._gateway_session_id != old_session and ui._connection_ready)
        elif change == 'disconnect':
            writer.close()
            await writer.wait_closed()
            await wait(ui, lambda: not ui._connection_ready)
            await task
            reader, writer = await asyncio.open_unix_connection(ui.socket_path)
            ui._io_generation += 1
            ui._recv_queue.put({'type': '_connection_opened', 'generation': ui._io_generation})
            task = asyncio.create_task(ui._run_connection(reader, writer))
            await wait(ui, lambda: ui._connection_ready and ui._connection_generation > old_generation)
        await wait(ui, lambda: process.returncode is not None and ui._terminal_input.queue.pending == 0)
        ui._handle_response({**original, 'type': 'terminal_input_result', 'error': False, 'data': 'late',
                             '_connection_generation': old_generation})
        ui._poll()
        assert ui._terminal_input.paused and ui._editor.get_text() == 'draft' and not calls
        assert [item.data for item in ui._terminal_input.queue.recovery] == ['~', 'x', '\r']
    finally:
        await cleanup(server, ui, writer, task)


@pytest.mark.parametrize('source', ['pong', 'command', 'run-ui'])
async def test_invalid_terminal_snapshot_disables_hooks_and_cancels_held_input_without_replay(tmp_path, source):
    server, ui, _, writer, task, calls = await start(tmp_path)
    try:
        ui._editor.set_text('draft')
        ui._tui._handle_frame(KeyBytes(b'x\r'))
        ui._poll()
        original = dict(ui._terminal_request)
        invalid = {**ui._extension_snapshot, 'terminal_generation': True}
        if source == 'pong':
            ui._handle_response({'type': 'pong', 'terminal_input': 1, 'terminal_owner': ui._terminal_owner,
                'session_id': ui._gateway_session_id, 'session_controls': 1, 'extension_controls': 1,
                'extension_snapshot': invalid})
        elif source == 'command':
            ui._extension_pending = ('invalid-receipt', ui._gateway_session_id)
            ui._handle_response({'type': 'extension_result', 'request_id': 'invalid-receipt', 'success': True,
                'group_id': 'main', 'session_id': ui._gateway_session_id, 'snapshot': invalid})
        else:
            ui._active_run_id = 'validation'
            ui._handle_response({'type': 'extension_ui', 'group_id': 'main', 'run_id': 'validation',
                                 'snapshot': invalid})
            ui._active_run_id = None
        assert not ui._terminal_supported and ui._terminal_input.paused
        assert [item.data for item in ui._terminal_input.queue.recovery] == ['x', '\r']
        ui._handle_response({**original, 'type': 'terminal_input_result', 'error': False, 'data': 'late'})
        ui._poll()
        assert ui._editor.get_text() == 'draft' and not calls
    finally:
        await cleanup(server, ui, writer, task)


async def test_long_unicode_host_lease_has_bounded_wire_owner_without_losing_local_ownership(tmp_path):
    server, ui, _, writer, task, calls = await start(tmp_path)
    try:
        ui._terminal_input.host_owner = lambda: '组/会话/几何:' * 1000
        ui._tui._handle_frame(KeyBytes(b'x'))
        ui._poll()
        assert len(ui._terminal_request['input_owner']) == 64
        assert len(ui._terminal_held.owner.encode()) > 16000
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert ui._editor.get_text() == '[x]' and not calls and not ui._terminal_input.queue.recovery
    finally:
        await cleanup(server, ui, writer, task)


async def test_claw_real_burst_last_unsubscribe_and_command_reregistration_are_wired_without_reconnect(tmp_path):
    server, ui, _, writer, task, calls = await start(tmp_path)
    try:
        runtime = server._orchestrator._runtimes['main']._active_session.extensions
        text = 'burst-' * 128
        ui._tui._handle_frame(KeyBytes(text.encode()))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert ui._editor.get_text() == ''.join('[' + char + ']' for char in text)
        assert await runtime.command('ext-terminal-count', '') == '1'
        ui._editor.set_text('')
        ui._tui._handle_frame(KeyBytes(b'quiet'))
        ui._tui._handle_frame(KeyBytes(b'after'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert ui._editor.get_text() == 'after' and ui._extension_snapshot['terminal_input'] is False
        assert await runtime.command('ext-terminal-count', '') == '2'
        ui._tui._handle_frame(KeyBytes(b' raw'))
        assert ui._editor.get_text() == 'after raw' and ui._terminal_input.queue.pending == 0
        ui._extension_command('ext-terminal-enable', '')
        await wait(ui, lambda: ui._extension_pending is None)
        assert ui._extension_snapshot['terminal_input'] is True
        ui._tui._handle_frame(KeyBytes(b'x'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert ui._editor.get_text() == 'after raw[x]' and not calls and not ui._terminal_input.paused
    finally:
        await cleanup(server, ui, writer, task)


@pytest.mark.parametrize('action', ['steer', 'follow-up'])
async def test_claw_selected_child_editor_uses_owned_independent_worker_and_negotiated_metadata(tmp_path, action):
    server, ui, _, writer, task, calls = await start(tmp_path, hold=True)
    children = server._orchestrator._runtimes['main']._active_session.children
    try:
        assert ui._terminal_child_supported
        ui._agents_command('start first child')
        await wait(ui, lambda: len(calls) == 1 and not ui._children_pending)
        ui._agents_command('start other child')
        await wait(ui, lambda: len(calls) == 2 and not ui._children_pending)
        ids = list(children.extensions.owners)
        first, sibling = ids
        ui._editor.set_text('parent draft')
        ui._open_children()
        await wait(ui, lambda: ui._terminal_children.get(first, {}).get('active') is True
                   and not ui._child_panel._busy and bool(ui._child_panel._entries))
        panel = ui._child_panel
        panel._selected_id = first
        panel._form(action)
        panel.render(80)
        from xdog.tui.tui import _terminal_height, _terminal_width
        ui._tui._previous_width, ui._tui._previous_height = _terminal_width(), _terminal_height()
        assert ui._terminal_child() == first, (panel._mode, panel._busy, panel.focused, panel._form_child)
        assert ui._terminal_prompt_eligible(), vars(ui)
        assert ui._terminal_available(), ui._terminal_prompt_owner()
        ui._tui._handle_frame(KeyBytes(b'ab'))
        ui._tui._handle_frame(Paste('drop'))
        ui._tui._handle_frame(Paste('line\ntext'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert panel._input.get_text() == '[a][b][line\ntext]' and len(calls) == 2
        assert ui._editor.get_text() == 'parent draft'
        assert await children.extensions.owners[first].command('ext-terminal-count', '') == '3'
        assert await children.extensions.owners[sibling].command('ext-terminal-count', '') == '0'
        parent = server._orchestrator._runtimes['main']._active_session.extensions
        assert await parent.command('ext-terminal-count', '') == '0'
        panel._input.set_text('')
        ui._tui._handle_frame(KeyBytes(b'quiet'))
        ui._tui._handle_frame(KeyBytes(b'after'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert panel._input.get_text() == 'after' and not ui._terminal_input.paused
        await children.extensions.owners[first].command('ext-terminal-enable', '')
        await wait(ui, lambda: ui._terminal_available())
        ui._tui._handle_frame(KeyBytes(b'x'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert panel._input.get_text() == 'after[x]' and len(calls) == 2
        panel._form('start')
        ui._tui._handle_frame(KeyBytes(b'new task'))
        assert panel._input.get_text() == 'new task' and not ui._terminal_input.queue.pending
        panel._selected_id = first
        panel._form(action)
        panel.render(80)
        ui._tui._handle_frame(KeyBytes(b'!\r'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        # Switching back restores the owned draft; invalid interception must
        # preserve it rather than erase the earlier text to satisfy this test.
        assert panel._input.get_text() == 'after[x]' and panel._mode == 'form' and len(calls) == 2
        assert ui._terminal_input.paused
    finally:
        await children.supervisor.aclose()
        await cleanup(server, ui, writer, task)


async def test_claw_child_terminal_foreign_socket_cannot_invoke_cancel_or_adopt_owned_worker(tmp_path):
    server, ui, _, writer, task, calls = await start(tmp_path, hold=True)
    children = server._orchestrator._runtimes['main']._active_session.children
    reader2, writer2 = await asyncio.open_unix_connection(ui.socket_path)
    try:
        ui._agents_command('start first child')
        await wait(ui, lambda: len(calls) == 1 and not ui._children_pending)
        first = next(iter(children.extensions.owners))
        ui._open_children()
        await wait(ui, lambda: not ui._child_panel._busy and ui._terminal_children.get(first, {}).get('active'))
        panel = ui._child_panel
        panel._selected_id = first
        panel._form('steer')
        panel.render(80)
        ui._tui._handle_frame(KeyBytes(b'~'))
        ui._tui._handle_frame(KeyBytes(b'old\r'))
        await wait(ui, lambda: (tmp_path / 'terminal-hook-entered').exists())
        packet = dict(ui._terminal_request)
        worker = children.extensions.owners[first]._manager._clients['terminal'].process
        writer2.write(b'{"type":"ping","group_id":"main","terminal_input":1,"terminal_child_input":1}\n')
        await writer2.drain()
        pong = await read_type(reader2, 'pong')
        stolen = {**packet, 'terminal_owner': pong['terminal_owner']}
        writer2.write((json.dumps(stolen) + '\n').encode())
        writer2.write((json.dumps({**packet, 'type': 'terminal_input_cancel'}) + '\n').encode())
        writer2.write(b'{"type":"ping","group_id":"main"}\n')
        await writer2.drain()
        refusal = await read_type(reader2, 'terminal_input_result')
        assert refusal['error'] is True and refusal['child_id'] == first
        await read_type(reader2, 'pong')
        assert worker.returncode is None and len(calls) == 1
        panel.back()
        panel._selected_id = first
        panel._form('steer')
        ui._poll()
        await wait(ui, lambda: worker.returncode is not None)
        ui._handle_response({**packet, 'type': 'terminal_input_result', 'error': False, 'data': 'late'})
        ui._poll()
        assert not panel._input.get_text() and ui._terminal_input.paused and len(calls) == 1
        assert [row.data for row in ui._terminal_input.queue.recovery] == ['~', 'old', '\r']
    finally:
        writer2.close()
        await writer2.wait_closed()
        await children.supervisor.aclose()
        await cleanup(server, ui, writer, task)


async def test_child_terminal_capability_withdrawal_cancels_only_owned_child_and_keeps_parent_hooks(tmp_path):
    server, ui, _, writer, task, calls = await start(tmp_path, hold=True)
    children = server._orchestrator._runtimes['main']._active_session.children
    try:
        ui._agents_command('start first child')
        await wait(ui, lambda: len(calls) == 1 and not ui._children_pending)
        child = next(iter(children.extensions.owners))
        ui._open_children()
        await wait(ui, lambda: not ui._child_panel._busy and ui._terminal_children.get(child, {}).get('active'))
        panel = ui._child_panel
        panel._selected_id = child
        panel._form('steer')
        panel.render(80)
        worker = children.extensions.owners[child]._manager._clients['terminal'].process
        ui._tui._handle_frame(KeyBytes(b'~'))
        ui._tui._handle_frame(KeyBytes(b'old\r'))
        await wait(ui, lambda: (tmp_path / 'terminal-hook-entered').exists())
        packet = dict(ui._terminal_request)
        ui._queue_request({'type': 'ping', 'group_id': 'main', 'terminal_child_input': 0})
        await wait(ui, lambda: not ui._terminal_child_supported)
        await wait(ui, lambda: worker.returncode is not None)
        assert ui._terminal_supported and not ui._terminal_children and ui._terminal_input.paused
        ui._handle_response({**packet, 'type': 'terminal_input_result', 'error': False, 'data': 'late'})
        ui._poll()
        assert not panel._input.get_text() and len(calls) == 1
        parent = server._orchestrator._runtimes['main']._active_session.extensions
        assert parent.terminal_input_available and await parent.command('ext-terminal-count', '') == '0'
    finally:
        await children.supervisor.aclose()
        await cleanup(server, ui, writer, task)


async def test_actual_claw_prompt_release_never_becomes_transformed_edit(tmp_path):
    server, ui, _, writer, task, calls = await start(tmp_path)
    try:
        ui._tui._handle_frame(KeyBytes(b'\x1b[97;1:3u'))
        ui._poll()
        assert not ui._terminal_input.queue.pending and not ui._editor.get_text()
        runtime = server._orchestrator._runtimes['main']._active_session.extensions
        assert await runtime.command('ext-terminal-count', '') == '0'
        assert not calls
    finally:
        await cleanup(server, ui, writer, task)


@pytest.mark.parametrize('surface', ['prompt', 'steer', 'follow-up'])
async def test_negotiated_native_release_is_owned_observation_not_parent_child_edit(tmp_path, surface):
    from packages.agent.tests.test_extension_terminal_input import add_release_observer
    server, ui, _, writer, task, calls = await start(tmp_path, hold=True)
    log = tmp_path / 'release.jsonl'
    try:
        add_release_observer(tmp_path / 'extensions/terminal/main.py', log)
        ui._extension_command('extensions', 'reload terminal')
        await wait(ui, lambda: ui._extension_pending is None and ui._terminal_release_available())
        runtime = server._orchestrator._runtimes['main']._active_session
        owner, target = runtime.extensions, ui._editor
        if surface != 'prompt':
            ui._agents_command('start first child')
            await wait(ui, lambda: len(calls) == 1 and not ui._children_pending)
            first = next(iter(runtime.children.extensions.owners))
            ui._open_children()
            await wait(ui, lambda: ui._terminal_children.get(first, {}).get('active') is True
                       and not ui._child_panel._busy and bool(ui._child_panel._entries))
            panel = ui._child_panel
            panel._selected_id = first
            panel._form(surface)
            panel.render(80)
            owner, target = runtime.children.extensions.owners[first], panel._input
        from xdog.tui.tui import _terminal_height, _terminal_width
        ui._tui._previous_width, ui._tui._previous_height = _terminal_width(), _terminal_height()
        assert ui._terminal_release_available()
        target.set_text('draft')
        ui._tui._handle_frame(KeyBytes(b'\x1b[97;1:3u'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert json.loads(log.read_text()) == {'data': '\x1b[97;1:3u', 'kind': 'release',
                                             'surface': 'prompt' if surface == 'prompt' else 'child-prompt'}
        assert target.get_text() == 'draft' and len(calls) == int(surface != 'prompt')
        assert await owner.command('ext-terminal-count', '') == '0'
        assert not runtime.agent.state.messages and not ui._terminal_input.paused
    finally:
        await cleanup(server, ui, writer, task)


@pytest.mark.parametrize('surface', ['prompt', 'child-prompt'])
@pytest.mark.parametrize('change', ['escape', 'resize', 'focus', 'withdraw'])
async def test_gateway_held_release_revocation_cancels_worker_without_replay(tmp_path, surface, change, monkeypatch):
    from packages.agent.tests.test_extension_terminal_input import add_release_observer
    server, ui, _, writer, task, calls = await start(tmp_path, hold=True)
    log = tmp_path / 'release.jsonl'
    try:
        add_release_observer(tmp_path / 'extensions/terminal/main.py', log, behavior='hang')
        ui._extension_command('extensions', 'reload terminal')
        await wait(ui, lambda: ui._extension_pending is None and ui._terminal_release_available())
        runtime = server._orchestrator._runtimes['main']._active_session
        owner, target = runtime.extensions, ui._editor
        if surface == 'child-prompt':
            ui._agents_command('start first child')
            await wait(ui, lambda: len(calls) == 1 and not ui._children_pending)
            first = next(iter(runtime.children.extensions.owners))
            ui._open_children()
            await wait(ui, lambda: ui._terminal_children.get(first, {}).get('active') is True
                       and not ui._child_panel._busy and bool(ui._child_panel._entries))
            panel = ui._child_panel
            panel._selected_id = first
            panel._form('steer')
            panel.render(80)
            owner, target = runtime.children.extensions.owners[first], panel._input
        from xdog.tui.tui import _terminal_height, _terminal_width
        ui._tui._previous_width, ui._tui._previous_height = _terminal_width(), _terminal_height()
        process = owner._manager._clients['terminal'].process
        target.set_text('draft')
        ui._tui._handle_frame(KeyBytes(b'\x1b[97;1:3u'))
        await wait(ui, log.exists)
        identifier = ui._terminal_input.queue.held[0].id
        if change == 'escape':
            ui._tui._handle_frame(KeyBytes(b'\x1b'))
        elif change == 'resize':
            import xdog.tui.tui as module
            monkeypatch.setattr(module, '_terminal_width', lambda: 91)
        elif change == 'focus':
            focused = ui._tui._focused
            ui._tui.set_focus(None)
            ui._tui.set_focus(focused)
        else:
            ui._queue_request({'type': 'ping', 'group_id': ui.group_id, 'terminal_release_input': 0})
        await wait(ui, lambda: process.returncode is not None and ui._terminal_input.queue.pending == 0)
        assert target.get_text() == 'draft' and len(calls) == int(surface == 'child-prompt')
        assert not ui._terminal_input.queue.accept(identifier, {'data': 'late'})
        assert [item.kind for item in ui._terminal_input.queue.recovery] == ['release']
    finally:
        await cleanup(server, ui, writer, task)
