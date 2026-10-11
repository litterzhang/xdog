"""Both actual clients' owned callback inspection; model adapters are offline peers."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, Model, TextContent, ToolCall, Usage, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('allowed', [False, True])
@pytest.mark.parametrize('headless', [False, True], ids=['ui', 'headless'])
async def test_actual_callback_owner_model_usage_child_boundaries_policy_and_ui_recovery(
    tmp_path, kind, child, allowed, headless,
):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    models = {name: Model(id='owned/' + name, name=name, provider='owned',
        context_window=32000 if name == 'child' else 64000,
        max_prompt_tokens=24000 if name == 'child' else 48000,
        headers={'Authorization': 'PRIVATE-HEADER'}, base_url='https://PRIVATE-ENDPOINT.invalid')
        for name in ('parent', 'child')}
    calls = []
    class Adapter:
        def resolve_model(self, name):
            return models.get(name.removeprefix('owned/'))
        def __call__(self, model, context, options):
            calls.append((model, context))
            if context.messages[-1].role == 'user':
                tool = next(tool for tool in context.tools if tool.name.startswith('ext_inspector_'))
                message = AssistantMessage(model=model, content=(ToolCall(id='inspect', name=tool.name, arguments={}),),
                                           usage=Usage(input=11, output=2), stop_reason='toolUse')
            else:
                message = AssistantMessage(model=model, content=(TextContent(text='INSPECTION-TURN-DONE'),),
                                           usage=Usage(input=21, output=3))
            return EventStream.empty(message)
    server = None
    task = None
    writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['inspector'], 'extension_dir': str(root)}))
        catalog = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(Adapter(), config=AgentConfig(model='owned/parent',
            context_window=64000, max_prompt_tokens=48000)),
            session_data=catalog.create_session(working_dir=tmp_path), session_manager=catalog,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            model_info=models['parent'], context_window=64000, max_prompt_tokens=48000,
            permissions=PermissionManager('allow-all' if allowed else 'deny'))
        agent = session.agent
    else:
        config = ClawConfig(model='owned/parent', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'inspect.sock'), pid_file=str(tmp_path / 'inspect.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('inspector',), extension_dir=str(root),
            extension_allow_tools=('inspector',) if allowed else ())
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('owned/parent', Adapter())):
            await server.start()
        runtime = server._orchestrator._runtimes['main']
        session = runtime.get_or_create_session(allow_reset=False)
        agent = session.agent
    agent.append_message(UserMessage(content='inherited parent context'))
    agent.append_message(AssistantMessage(model='owned/parent', content=(TextContent(text='inherited response'),),
                                        usage=Usage(input=7777)))
    await session.initialize_extensions()
    ui = None
    if not headless and kind == 'coding':
        ui = InteractiveMode(session)
    elif not headless:
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(12):
            while not predicate():
                if ui is not None:
                    ui._poll()
                await asyncio.sleep(0.003)
    try:
        if ui is not None:
            if kind == 'claw':
                await wait(lambda: ui._connection_ready)
            ui._editor.set_text('retained parent draft')
        if child:
            identifier = await session.children.supervisor.start('Inspect callback owner', access='inherit',
                config={'model': 'owned/child'})
            record = await session.children.supervisor.control('wait', identifier)
            assert record['status'] == 'completed'
            rows = [row['data'] for row in session.children.store.entries(identifier)['inspector']
                    if row['data']['child_id'] == identifier]
            assert {row['child_id'] for row in rows} == {identifier}
            assert {row['model']['id'] for row in rows} == {'owned/child'}
            assert all(row['usage']['context_limit'] == 24000 for row in rows)
            initial = [row for row in rows if row['scope'] == 'event']
            assert initial and all(row['usage']['source'] == 'unavailable' for row in initial)
            assert all(row['usage']['tokens'] != 7777 for row in rows)
            parent_rows = [row['data'] for row in session.extensions.entries.for_extension('inspector')]
            assert all(row['model']['id'] == 'owned/parent' for row in parent_rows)
            assert len(agent.state.messages) == 2
        else:
            if headless:
                if kind == 'coding':
                    await session.send_message('Inspect callback owner')
                else:
                    from xdog.claw.core.types import UserInput
                    await session.run_turn(UserInput(group_id='main', content='Inspect callback owner'))
            else:
                ui._handle_submit('Inspect callback owner')
                if kind == 'coding':
                    await wait(lambda: len(calls) == 2 and not ui._worker_active)
                else:
                    await wait(lambda: len(calls) == 2 and ui._active_run_id is None and not ui._waiting)
            rows = [row['data'] for row in session.extensions.entries.for_extension('inspector')]
            assert {row['child_id'] for row in rows} == {''}
            assert {row['model']['id'] for row in rows} == {'owned/parent'}
            assert all(row['usage']['context_limit'] == 48000 for row in rows)
        assert {'event', 'context', 'pre'} <= {row['scope'] for row in rows}
        assert ('tool' in {row['scope'] for row in rows}) is allowed
        if allowed:
            tool = next(row for row in rows if row['scope'] == 'tool')
            assert tool['usage']['tokens'] == 13
        assert len(calls) == 2 and {model for model, _context in calls} == {'owned/child' if child else 'owned/parent'}
        assert 'PRIVATE-' not in json.dumps(rows)
        if ui is not None:
            assert ui._extension_dialog_panel is None
            assert ui._editor.get_text() == 'retained parent draft' and ui._tui._focused is ui._editor
        if kind == 'coding':
            assert session.permissions.mode == ('allow-all' if allowed else 'deny')
        else:
            assert config.extension_allow_tools == (('inspector',) if allowed else ())
    finally:
        if ui is not None:
            ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        if kind == 'coding':
            session.dispose()
        else:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await server.stop()
