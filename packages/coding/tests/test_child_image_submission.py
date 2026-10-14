"""Native child editors submit actual file bytes through Coding and the owned Claw gateway."""
from __future__ import annotations

import asyncio
import base64
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
async def test_actual_app_old_context_draft_copy_and_discard_are_local_reviewed_and_no_parent_execution(tmp_path, kind):
    from xdog.tui.child_drafts import ChildDraft
    adapter = HeldVision()
    if kind == 'coding':
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='offline/vision')),
            session_data=store.create_session(model='offline/vision', working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        session = None
        ui = ChatApp(str(tmp_path / 'not-running.sock'))
        ui._connection_ready = True
        ui._children_supported = True
        ui._gateway_session_id = 'current-session'
    image = attachment(PNG, 'cached-photo.png')
    old = ChildDraft('CROSS-CONTEXT LOCAL TASK', 'inherit', {'model': 'old/model', 'tools': ['bash']},
                     (image,), True, 6)
    ui._child_drafts['previous-owned-context'] = {('start', 'recovered/past/input'): old}
    ui._child_draft_labels['previous-owned-context'] = '/previous/workspace · old session'
    ui._editor.set_text('UNTOUCHED-PARENT-DRAFT')
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(())
        panel.open_drafts()
        browser = panel.draft_panel
        browser.render(100)
        browser.handle_input(KeyEvent('enter'))
        for _ in range(40):
            browser.render(100)
            if browser._visible:
                browser.handle_input(KeyEvent('enter'))
                break
            browser.handle_input(KeyEvent('pagedown'))
        assert panel._mode == 'form' and panel._input.get_text() == old.text
        assert panel._access == 'read-only' and panel._config == {'inherit_context': False}
        assert tuple(panel.composer.items.values()) == old.images
        assert 'uncertain' in panel._error
        assert ui._child_drafts['previous-owned-context'][('start', 'recovered/past/input')] is old
        assert ui._editor.get_text() == 'UNTOUCHED-PARENT-DRAFT' and not adapter.calls
        panel.open_drafts()
        browser = panel.draft_panel
        browser.render(100)
        browser.handle_input(KeyEvent('x'))
        for _ in range(40):
            browser.render(100)
            if browser._visible:
                browser.handle_input(KeyEvent('enter'))
                break
            browser.handle_input(KeyEvent('pagedown'))
        assert not ui._child_drafts['previous-owned-context']
        assert ui._child_draft_bytes() == len(image.data)  # Explicit fresh copy still owns its exact immutable image.
        assert ui._editor.get_text() == 'UNTOUCHED-PARENT-DRAFT' and not adapter.calls
    finally:
        ui._request_exit()
        if session is not None:
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            session.dispose()


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('with_image', [False, True])
async def test_initial_factory_failure_recovers_fresh_without_restarting_or_rewriting(tmp_path, kind, with_image):
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
        await wait(ui, lambda: ui._connection_ready and ui._child_task_recovery_supported)
    def fail(spec, publish):
        raise ValueError('Owned failure before child agent initialization')
    session.children.supervisor.factory = fail
    image = ImageContent(data=base64.b64encode(PNG).decode(), mime_type='image/png')
    ui._editor.set_text('PARENT-INITIAL-RECOVERY-DRAFT')
    try:
        child = await session.children.supervisor.start('ORIGINAL INITIAL /quit', images=(image,) if with_image else ())
        await session.children.supervisor.control('wait', child)
        saved = session.children.store.load()[0]
        ui._open_children()
        panel = ui._child_panel
        panel._selected_id = child
        panel.open_queue()
        await wait(ui, lambda: panel.queue is not None and panel.queue._rows and not panel.queue._busy)
        row = panel.queue._rows[0]
        assert row['source'] == 'initial-task' and row['uncertain'] is False
        panel.queue.render(120)
        panel.queue.handle_input(KeyEvent('u'))
        assert panel.queue._confirm['action'] == 'recover_task'
        for _ in range(40):
            panel.queue.render(120)
            if panel.queue._visible:
                panel.queue.handle_input(KeyEvent('enter'))
                break
            panel.queue.handle_input(KeyEvent('pagedown'))
        await wait(ui, lambda: panel._mode == 'form' and panel.queue is None)
        assert panel._input.get_text() == 'ORIGINAL INITIAL /quit'
        assert panel._access == 'read-only' and panel._config == {'inherit_context': False}
        assert bool(panel.composer.items) is with_image
        if with_image:
            assert next(iter(panel.composer.items.values())).data == PNG
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
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            session.dispose()
