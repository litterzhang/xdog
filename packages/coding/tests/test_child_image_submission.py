"""Native child editors submit actual file bytes through Coding and the owned Claw gateway."""
from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, ImageContent, Model, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.attachments import attachment
from xdog.tui.keys import KeyEvent

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')


class HeldVision:
    def __init__(self):
        self.calls = []

    def resolve_model(self, name):
        return Model(id=name, context_window=128000, input=('text', 'image'))

    def __call__(self, model, context, options):
        self.calls.append(context)
        async def events():
            yield StartEvent(partial=AssistantMessage(content=(TextContent(text='CHILD-HELD'),)))
            await asyncio.sleep(60)
        return EventStream.from_async_generator(events())


async def wait(ui, predicate):
    try:
        async with asyncio.timeout(10):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(0.003)
    except TimeoutError:
        panel = ui._child_panel
        raise AssertionError(f"UI wait failed: mode={panel._mode if panel else None}, "
            f"error={panel._error if panel else None}, queue={panel.queue._error if panel and panel.queue else None}, "
            f"pending={list(getattr(ui, '_children_pending', {}))}") from None


def review(panel):
    panel.render(120)
    panel.handle_input(KeyEvent('enter'))
    assert panel._mode == 'confirm'
    panel.handle_input(KeyEvent('enter'))  # No unpainted commitment.
    assert panel._mode == 'confirm'
    for _ in range(30):
        panel.render(120)
        if panel._visible:
            panel.handle_input(KeyEvent('enter'))
            return
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Child image review never became fully painted')


def test_claw_malformed_success_receipt_unlocks_but_never_clears_child_image_draft(tmp_path):
    ui = ChatApp(str(tmp_path / 'absent-owned.sock'))
    ui._connection_ready = True
    ui._children_supported = ui._child_config_supported = ui._child_images_supported = True
    ui._gateway_session_id = 'owned-session'
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())  # Settle the initial roster before this isolated receipt test.
        panel.handle_input(KeyEvent('n'))
        panel._input.set_text('retain malformed receipt task')
        image = attachment(PNG, 'retained.png')
        panel.composer.restore((image,))
        review(panel)
        key = next(key for key, value in ui._children_pending.items() if value[0] == 'start')
        ui._handle_response({'type': 'child_result', 'request_id': key, 'group_id': ui.group_id,
                             'session_id': 'owned-session', 'success': True, 'data': []})
        assert panel._mode == 'form' and panel._image_pending is None
        assert tuple(panel.composer.items.values()) == (image,)
        assert panel._input.get_text() == 'retain malformed receipt task' and 'uncertain' in panel._error
        assert not ui._composer.items
    finally:
        ui._request_exit()


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_actual_app_recovery_rejects_old_context_and_reopens_only_current_image_input(
    tmp_path, kind, monkeypatch,
):
    from xdog.tui.child_drafts import ChildDraft
    from xdog.tui.components.child_draft_panel import DraftEntry

    from packages.coding.tests.test_saved_child_drafts import (
        forbid_disk,
        saved_child_fixture,
        saved_files,
    )
    from packages.coding.tests.test_saved_child_drafts import (
        review as recovery_review,
    )
    adapter = HeldVision()
    if kind == 'coding':
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='offline/vision')),
            session_data=store.create_session(model='offline/vision', working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        import xdog.claw.channels.tui.tui_app as module
        monkeypatch.setattr(module, 'get_data_dir', lambda: tmp_path)
        tmp_path.chmod(0o700)
        session = None
        ui = ChatApp(str(tmp_path / 'not-running.sock'))
        ui._connection_ready = True
        ui._children_supported = True
        ui._gateway_session_id = 'current-session'
    image = attachment(PNG, 'cached-photo.png')
    old = ChildDraft('CROSS-CONTEXT LOCAL TASK', 'inherit', {'model': 'old/model', 'tools': ['bash']},
                     (image,), True, 6)
    ui._child_drafts['previous-owned-context'] = {('start', 'recovered/past/input'): old}
    ui._editor.set_text('UNTOUCHED-PARENT-DRAFT')
    store, _saved = saved_child_fixture(ui)
    stored = saved_files(store)
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel.open_drafts()
        assert not panel.draft_panel.source()
        assert set(ui._child_drafts) == {ui._child_context}
        foreign = DraftEntry('previous-owned-context', ('start', 'recovered/past/input'), old, old, 'old', False)
        with pytest.raises(ValueError):
            panel.adopt_draft(foreign, reopen=True)
        assert ui._editor.get_text() == 'UNTOUCHED-PARENT-DRAFT' and not adapter.calls
        panel.back()
        panel._form('start')
        panel._input.set_text('CURRENT UNSENT IMAGE INPUT')
        panel._input._cursor = 5
        panel._access, panel._config = 'inherit', {'tools': []}
        panel.composer.restore((image,))
        blocked = forbid_disk(monkeypatch)
        panel.open_drafts()
        browser = panel.draft_panel
        value = browser.source()[0]
        assert value.current and value.scope == panel.owner_token
        assert value.value.images[0] is image and value.value.text != old.text
        for key in ('s', 'x', 'delete', 'r', '/'):
            browser.render(100)
            browser.handle_input(KeyEvent(key))
            assert browser._review is None
        recovery_review(browser, 'enter')
        assert panel._mode == 'form' and panel._input.get_text() == 'CURRENT UNSENT IMAGE INPUT'
        assert panel._input._cursor == 5 and panel._access == 'inherit' and panel._config == {'tools': []}
        assert panel.composer.items[image.id] is image
        assert ui._child_draft_bytes() == len(image.data)
        assert set(ui._child_drafts[ui._child_context]) == {('start', '')}
        assert ui._editor.get_text() == 'UNTOUCHED-PARENT-DRAFT' and not adapter.calls and not blocked
        assert saved_files(store) == stored
        if session is not None:
            assert not session.agent.messages and session.permissions.mode == 'deny'
    finally:
        ui._request_exit()
        if session is not None:
            await session.children.runtime.aclose()
            await session.extensions.aclose()
            session.dispose()


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('with_image', [False, True])
async def test_initial_factory_failure_is_inspection_only_and_backend_backup_remains_recoverable(
        tmp_path, kind, with_image):
    adapter = HeldVision()
    server = io = writer = None
    if kind == 'coding':
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='offline/vision')),
            session_data=store.create_session(model='offline/vision', working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='offline/vision', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'initial.sock'), pid_file=str(tmp_path / 'initial.pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/vision', adapter)):
            await server.start()
        session = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
        ui = ChatApp(config.socket_path)
        ui._io_generation = 1
        ui._handle_response({'type': '_connection_opened', 'generation': 1})
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=1024 * 1024)
        io = asyncio.create_task(ui._run_connection(reader, writer))
        await wait(ui, lambda: ui._connection_ready and ui._child_queues_supported)
    def fail(spec, publish):
        raise ValueError('Owned failure before child agent initialization')
    session.children.runtime.factory = fail
    image = ImageContent(data=base64.b64encode(PNG).decode(), mime_type='image/png')
    ui._editor.set_text('PARENT-INITIAL-RECOVERY-DRAFT')
    try:
        child = await session.children.runtime.start('ORIGINAL INITIAL /quit', images=(image,) if with_image else ())
        await session.children.runtime.control('wait', child)
        saved = session.children.store.load()[0]
        ui._open_children()
        panel = ui._child_panel
        panel._selected_id = child
        panel.open_queue()
        await wait(ui, lambda: panel.queue is not None and panel.queue._rows and not panel.queue._busy)
        row = panel.queue._rows[0]
        assert row['source'] == 'initial-task' and row['uncertain'] is False
        queue = panel.queue
        queue.render(120)
        for key in ('u', 'e', 'delete', 'enter'):
            queue.handle_input(KeyEvent(key))
        assert panel.queue is queue and panel._mode != 'form'
        assert not hasattr(queue, '_confirm') and (panel.composer is None or not panel.composer.items)
        # Explicit recovery remains an Agent/SDK operation, never a user UI callback.
        recovered = await session.children.runtime.control('queue', child, json.dumps({
            'action': 'recover_task', 'id': row['id'], 'revision': queue._revision}))
        assert recovered['queue']['recovered']['content'] == 'ORIGINAL INITIAL /quit'
        assert ('image_recovery' in recovered) is with_image
        if with_image:
            assert recovered['image_recovery']['receipt']
        assert session.children.store.load()[0] == saved and not adapter.calls
        assert ui._editor.get_text() == 'PARENT-INITIAL-RECOVERY-DRAFT' and not session.agent.messages
    finally:
        ui._request_exit()
        if io is not None:
            io.cancel()
            await asyncio.gather(io, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
        if server is not None:
            await server.stop()
        else:
            await session.children.runtime.aclose()
            await session.extensions.aclose()
            session.dispose()
