from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.stdin_buffer import KeyBytes, Paste


async def create(tmp_path, *, hold=False):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    source = root / 'terminal/main.py'
    source.write_text(source.read_text().replace("        if data == 'bad':", "        if data == '!':\n"
        "            return {'data': '\\r'}\n        if data == '~':\n"
        "            from pathlib import Path\n"
        f"            Path({str(tmp_path / 'terminal-hook-entered')!r}).write_text('entered')\n"
        "            await asyncio.Event().wait()\n        if data == 'bad':"))
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['terminal'], 'extension_dir': str(root)}))
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
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('ask'))
    await session.extensions.initialize()
    return session, InteractiveMode(session), calls


async def wait(ui, predicate):
    async with asyncio.timeout(6):
        while not predicate():
            ui._poll()
            await asyncio.sleep(0.003)


async def cleanup(session, ui):
    ui._request_exit()
    await session.extensions.aclose()
    await session.children.supervisor.aclose()


async def test_real_coding_keys_paste_consume_once_and_host_submit_remain_one_model_loop(tmp_path):
    session, ui, calls = await create(tmp_path)
    try:
        ui._tui._handle_frame(KeyBytes(b'ab'))
        ui._tui._handle_frame(Paste('drop'))
        ui._tui._handle_frame(Paste('multi\nline'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert ui._editor.get_text() == '[a][b][multi\nline]'
        assert await session.extensions.command('ext-terminal-count', '') == '3'
        assert not calls and not session.agent.messages and not ui._extension_dialog_queue
        ui._tui._handle_frame(KeyBytes(b'c\r'))
        await wait(ui, lambda: len(calls) == 1 and not ui._worker_active)
        assert ui._terminal_input.queue.pending == 0
        assert len(calls) == 1 and calls[0].messages[-1].role == 'user'
        assert '[a][b][multi\nline][c]' in calls[0].messages[-1].content[0].text
        assert await session.extensions.command('ext-terminal-count', '') == '4'
    finally:
        await cleanup(session, ui)


async def test_real_coding_synthetic_submit_is_retained_with_original_enter_and_explicit_restore(tmp_path):
    session, ui, calls = await create(tmp_path)
    try:
        ui._editor.set_text('draft')
        ui._tui._handle_frame(KeyBytes(b'!\r'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert ui._editor.get_text() == 'draft' and not calls
        assert [item.data for item in ui._terminal_input.queue.recovery] == ['!', '\r']
        ui._handle_slash_command('terminal-input', 'restore 1')
        assert ui._editor.get_text() == 'draft!' and not calls
        assert await session.extensions.command('ext-terminal-count', '') == '1'
    finally:
        await cleanup(session, ui)


@pytest.mark.parametrize('interrupt', ['escape', 'overlay', 'resize', 'pause'])
async def test_real_coding_pending_hook_canceled_for_interrupt_overlay_resize_no_late_replay(tmp_path, interrupt,
                                                                                        monkeypatch):
    from xdog.tui.components.text import Text
    session, ui, calls = await create(tmp_path)
    process = session.extensions._manager._clients['terminal'].process
    try:
        ui._editor.set_text('draft')
        ui._tui._handle_frame(KeyBytes(b'~'))
        ui._tui._handle_frame(KeyBytes(b'x\r'))
        await wait(ui, lambda: (tmp_path / 'terminal-hook-entered').exists())
        old = ui._terminal_input.queue.held[0].id
        if interrupt == 'escape':
            ui._tui._handle_frame(KeyBytes(b'\x1b'))
        elif interrupt == 'pause':
            ui._tui._handle_frame(KeyBytes(b'\x1bi'))
            assert ui._terminal_input.paused
        elif interrupt == 'overlay':
            handle = ui._tui.show_overlay(Text('foreign input owner'))
            ui._poll()
            handle.hide()
        else:
            import xdog.tui.tui as module
            monkeypatch.setattr(module, '_terminal_width', lambda: 81)
            ui._poll()
        await wait(ui, lambda: process.returncode is not None)
        assert not ui._terminal_input.queue.accept(old, {'data': 'late'})
        ui._poll()
        assert not calls and ui._editor.get_text() == 'draft'
        assert [item.data for item in ui._terminal_input.queue.recovery] == ['~', 'x', '\r']
    finally:
        await cleanup(session, ui)


async def test_terminal_dispatch_captures_original_runtime_before_background_thread_can_start(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, Mock
    session, ui, calls = await create(tmp_path)
    original, held = session.extensions, []
    def defer(item, run):
        held.append((item, run))
        return True
    monkeypatch.setattr(ui._terminal_worker, 'send', defer)
    try:
        ui._tui._handle_frame(KeyBytes(b'x'))
        ui._poll()
        replacement = Mock()
        replacement.terminal_events = ()
        replacement.terminal_input = AsyncMock(return_value={'data': 'foreign'})
        session.extensions = replacement
        item, callback = held[0]
        assert await callback(item) == {'consume': False, 'data': '[x]'}
        replacement.terminal_input.assert_not_called()
        ui._terminal_input.queue.accept(item.id, {'data': '[x]'})
        ui._poll()
        assert not ui._editor.get_text() and not calls
        assert ui._terminal_input.queue.recovery[0].data == 'x'
    finally:
        session.extensions = original
        await cleanup(session, ui)


async def test_coding_real_burst_and_last_unsubscribe_preserve_queued_input_and_resume_after_command(tmp_path):
    session, ui, calls = await create(tmp_path)
    try:
        text = 'burst-' * 128
        ui._tui._handle_frame(KeyBytes(text.encode()))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert ui._editor.get_text() == ''.join('[' + char + ']' for char in text)
        assert await session.extensions.command('ext-terminal-count', '') == '1'
        ui._editor.set_text('')
        ui._tui._handle_frame(KeyBytes(b'quiet'))
        ui._tui._handle_frame(KeyBytes(b'after'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert ui._editor.get_text() == 'after' and not session.extensions.terminal_input_available
        assert await session.extensions.command('ext-terminal-count', '') == '2'
        ui._tui._handle_frame(KeyBytes(b' raw'))
        assert ui._editor.get_text() == 'after raw' and ui._terminal_input.queue.pending == 0
        ui._handle_slash_command('ext-terminal-enable', '')
        await wait(ui, lambda: ui._command is None)
        ui._tui._handle_frame(KeyBytes(b'x'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert ui._editor.get_text() == 'after raw[x]' and not calls and not ui._terminal_input.paused
    finally:
        await cleanup(session, ui)


@pytest.mark.parametrize('action', ['steer', 'follow-up'])
async def test_coding_selected_child_editor_uses_its_own_worker_not_parent_or_sibling(tmp_path, action):
    session, ui, calls = await create(tmp_path, hold=True)
    try:
        first = await session.children.supervisor.start('first child')
        sibling = await session.children.supervisor.start('other child')
        await wait(ui, lambda: len(calls) == 2 and ui._session.children.extensions.terminal_metadata(
            ui._child_ui_lease).get(first, {}).get('active') is True)
        ui._editor.set_text('parent draft')
        ui._open_children()
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
        assert panel._input.get_text() == '[a][b][line\ntext]'
        assert ui._editor.get_text() == 'parent draft' and len(calls) == 2
        owners = session.children.extensions.owners
        assert await owners[first].command('ext-terminal-count', '') == '3'
        assert await owners[sibling].command('ext-terminal-count', '') == '0'
        assert await session.extensions.command('ext-terminal-count', '') == '0'
        # Start/config/confirmation are host controls, not a borrowed parent hook.
        panel._input.set_text('')
        ui._tui._handle_frame(KeyBytes(b'quiet'))
        ui._tui._handle_frame(KeyBytes(b'after'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert panel._input.get_text() == 'after' and not ui._terminal_input.paused
        await owners[first].command('ext-terminal-enable', '')
        await wait(ui, lambda: ui._terminal_available())
        ui._tui._handle_frame(KeyBytes(b'x'))
        await wait(ui, lambda: ui._terminal_input.queue.pending == 0)
        assert panel._input.get_text() == 'after[x]' and len(calls) == 2
        panel._form('start')
        ui._tui._handle_frame(KeyBytes(b'new task'))
        assert panel._input.get_text() == 'new task' and not ui._terminal_input.queue.pending
        assert await session.extensions.command('ext-terminal-count', '') == '0'
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
        await cleanup(session, ui)


@pytest.mark.parametrize('change', ['sibling', 'resize', 'back'])
async def test_coding_child_held_input_cannot_migrate_to_sibling_parent_or_new_form(tmp_path, change, monkeypatch):
    session, ui, calls = await create(tmp_path, hold=True)
    try:
        first = await session.children.supervisor.start('first child')
        sibling = await session.children.supervisor.start('other child')
        await wait(ui, lambda: len(calls) == 2)
        ui._editor.set_text('parent draft')
        ui._open_children()
        panel = ui._child_panel
        panel._selected_id = first
        panel._form('steer')
        panel.render(80)
        owner = session.children.extensions.owners[first]
        process = owner._manager._clients['terminal'].process
        ui._tui._handle_frame(KeyBytes(b'~'))
        ui._tui._handle_frame(KeyBytes(b'old\r'))
        await wait(ui, lambda: (tmp_path / 'terminal-hook-entered').exists())
        old_id = ui._terminal_input.queue.held[0].id
        if change == 'sibling':
            panel._selected_id = sibling
            panel._form('steer')
        elif change == 'back':
            panel.back()
            panel._selected_id = first
            panel._form('steer')
        else:
            import xdog.tui.tui as module
            monkeypatch.setattr(module, '_terminal_width', lambda: 81)
        ui._poll()
        await wait(ui, lambda: process.returncode is not None)
        assert not ui._terminal_input.queue.accept(old_id, {'data': 'late'})
        assert not panel._input.get_text() and ui._editor.get_text() == 'parent draft' and len(calls) == 2
        assert [row.data for row in ui._terminal_input.queue.recovery] == ['~', 'old', '\r']
        assert await session.children.extensions.owners[sibling].command('ext-terminal-count', '') == '0'
    finally:
        await cleanup(session, ui)


async def test_actual_coding_prompt_release_never_becomes_transformed_edit(tmp_path):
    session, ui, calls = await create(tmp_path)
    try:
        ui._tui._handle_frame(KeyBytes(b'\x1b[97;1:3u'))
        ui._poll()
        assert not ui._terminal_input.queue.pending and not ui._editor.get_text()
        assert await session.extensions.command('ext-terminal-count', '') == '0'
        assert not calls
    finally:
        await cleanup(session, ui)


@pytest.mark.parametrize('surface', ['prompt', 'steer', 'follow-up'])
async def test_native_release_observation_uses_exact_parent_or_child_worker_without_edit(tmp_path, surface):
    from packages.agent.tests.test_extension_terminal_input import add_release_observer
    session, ui, calls = await create(tmp_path, hold=True)
    log = tmp_path / 'release.jsonl'
    try:
        add_release_observer(tmp_path / 'extensions/terminal/main.py', log)
        await session.extensions.manage('reload', 'terminal')
        target = ui._editor
        owner = session.extensions
        if surface != 'prompt':
            first = await session.children.supervisor.start('child')
            await wait(ui, lambda: len(calls) == 1 and ui._session.children.extensions.terminal_metadata(
                ui._child_ui_lease).get(first, {}).get('active') is True)
            ui._open_children()
            panel = ui._child_panel
            panel._selected_id = first
            panel._form(surface)
            panel.render(80)
            target = panel._input
            owner = session.children.extensions.owners[first]
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
        assert not session.agent.messages and not ui._terminal_input.paused
    finally:
        await cleanup(session, ui)


@pytest.mark.parametrize('surface', ['prompt', 'child-prompt'])
@pytest.mark.parametrize('change', ['escape', 'resize', 'focus', 'withdraw'])
async def test_native_held_release_revocation_reaps_exact_worker_never_replays(tmp_path, surface, change, monkeypatch):
    from packages.agent.tests.test_extension_terminal_input import add_release_observer
    session, ui, calls = await create(tmp_path, hold=True)
    log = tmp_path / 'held-release.jsonl'
    try:
        add_release_observer(tmp_path / 'extensions/terminal/main.py', log, behavior='hang')
        await session.extensions.manage('reload', 'terminal')
        target = ui._editor
        owner = session.extensions
        if surface == 'child-prompt':
            child = await session.children.supervisor.start('child')
            await wait(ui, lambda: len(calls) == 1 and child in session.children.extensions.owners)
            ui._open_children()
            ui._child_panel._selected_id = child
            ui._child_panel._form('steer')
            ui._child_panel.render(80)
            target = ui._child_panel._input
            owner = session.children.extensions.owners[child]
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
            # Native registration/capability withdrawal invalidates the same lease.
            ui._terminal_input.release_available = lambda: False
        await wait(ui, lambda: process.returncode is not None and ui._terminal_input.queue.pending == 0)
        assert not ui._terminal_input.queue.accept(identifier, {'data': 'late'})
        assert target.get_text() == 'draft' and len(calls) == int(surface == 'child-prompt')
        assert [item.kind for item in ui._terminal_input.queue.recovery] == ['release']
    finally:
        await cleanup(session, ui)
