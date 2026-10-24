"""Actual Coding/Claw child factories validate default-route history/tool/worker images."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, ImageContent, Model, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


class Adapter:
    def __init__(self, vision):
        self.vision = vision
        self.calls = []

    def resolve_model(self, name):
        if self.vision is None:
            return None
        return Model(id='offline/child', context_window=32000,
                     input=('text', 'image') if self.vision else ('text',))

    def __call__(self, model, context, options):
        self.calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='CHILD-IMAGE-RESULT'),)))


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_actual_host_child_checkpoint_is_durable_before_provider_and_cancel_waits_writer(tmp_path, kind):
    import asyncio
    import threading

    from xdog.coding.core.child_store import ChildStore
    entered, release = threading.Event(), threading.Event()
    adapter = Adapter(True)
    server = None
    if kind == 'coding':
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='offline/child')),
            session_data=store.create_session(model='offline/child', working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_repo=None, bash=None, permissions=PermissionManager('deny'))
    else:
        config = ClawConfig(model='offline/child', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/child', adapter)):
            await server.start()
        session = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
    cancellation = None
    try:
        children = session.children
        assert children.runtime.persist_admission == children.store.save_admission
        original = children.runtime.persist_admission
        def checkpoint(record):
            original(record)
            entered.set()
            assert release.wait(5)
        children.runtime.persist_admission = checkpoint
        identifier = await children.runtime.start('NATIVE HOST CHECKPOINT',
            images=(ImageContent(data=PNG, mime_type='image/png'),))
        assert await asyncio.to_thread(entered.wait, 3)
        saved = ChildStore(children.store.root).load()[0]
        assert saved['initial_state'] == 'admitted' and 'NATIVE HOST CHECKPOINT' in str(saved['messages'])
        assert PNG in str(saved['messages']) and saved['status'] == 'interrupted'
        assert not adapter.calls and not session.agent.messages
        cancellation = asyncio.create_task(children.runtime.control('cancel', identifier))
        await asyncio.sleep(0.05)
        assert not cancellation.done()  # Actual factory cleanup cannot pass the owned disk writer.
        release.set()
        result = await asyncio.wait_for(cancellation, 3)
        assert result['status'] == 'canceled' and not adapter.calls
        assert not children.runtime.active
        assert not children.store._path(identifier, '.admission').exists()
    finally:
        release.set()
        if cancellation is not None:
            await asyncio.gather(cancellation, return_exceptions=True)
        if server is not None:
            await server.stop()
        else:
            await session.children.runtime.aclose()
            await session.extensions.aclose()
            session.dispose()
