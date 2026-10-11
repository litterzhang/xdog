from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize("scope", ["sessions", "tree", "fail"])
async def test_claw_command_browser_uses_existing_session_protocol_after_callback_settles(tmp_path, scope):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "navigator.sock"),
        pid_file=str(tmp_path / "navigator.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        extensions=("navigator",),
        extension_dir=str(root),
    )
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))

    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)

    try:
        await wait(lambda: ui._connection_ready)
        assert ui._extension_session_supported
        ui._editor.set_text("parent draft")
        ui._extension_command("ext-navigator-browse", "tree" if scope == "tree" else scope if scope == "fail" else "")
        await wait(lambda: ui._extension_pending is None)
        before = ui._gateway_session_id
        if scope == "fail":
            assert ui._navigation_panel is None and ui._extension_browser is None
        else:
            assert ui._navigation_panel is not None and ui._tui._focused is ui._navigation_panel
            await wait(lambda: not ui._navigation_provider.snapshot().busy)
            if scope == "sessions":
                assert ui._navigation_provider.snapshot().entries
            else:
                assert ui._navigation_provider.snapshot().scope == f"tree|{before}|"
            ui._close_navigation()
            assert ui._tui._focused is ui._editor
        assert not calls and ui._editor.get_text() == "parent draft"
        assert server._orchestrator.get_new_session_id("main") == before
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_older_socket_viewer_cancels_session_host_requests_without_unknown_dialog(tmp_path):
    import json
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'oldnav.sock'), pid_file=str(tmp_path / 'oldnav.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('navigator',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('test/model', lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        writer.write(b'{"type":"ping","group_id":"main"}\n')
        await writer.drain()
        state = json.loads(await reader.readline())
        writer.write((json.dumps({'type': 'extension_control', 'request_id': 'old', 'group_id': 'main',
            'session_id': state.get('session_id', ''), 'action': 'command', 'name': 'ext-navigator-browse', 'args': ''})
                      + '\n').encode())
        await writer.drain()
        events = []
        async with asyncio.timeout(8):
            while True:
                response = json.loads(await reader.readline())
                events.append(response)
                if response['type'] == 'extension_result':
                    break
        assert response['success'] and response['output'] == 'NAVIGATION-UNAVAILABLE'
        assert not any(event['type'] == 'extension_dialog' for event in events)
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('measured', [False, True])
async def test_claw_inspection_command_reads_gateway_owner_not_stale_viewer_model_or_usage(tmp_path, measured):
    import json

    from xdog.ai.types import Model, Usage, UserMessage

    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    calls = []
    model = Model(id='test/model', name='Actual model', provider='owned', context_window=64000,
                  max_prompt_tokens=48000, reasoning=True, supported_efforts=('low', 'high'), input=('text', 'image'),
                  headers={'Authorization': 'PRIVATE-HEADER'}, base_url='https://PRIVATE-ENDPOINT.invalid')
    class Adapter:
        def resolve_model(self, selected):
            return model if selected == model.id else None
        def __call__(self, *args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'inspect.sock'), pid_file=str(tmp_path / 'inspect.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('navigator',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', Adapter())):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    owner = runtime.get_or_create_session(allow_reset=False)
    if measured:
        owner.agent.append_message(AssistantMessage(model='test/model', usage=Usage(input=100, output=20,
                                                                                  cache_read=30, cache_write=40)))
        owner.agent.append_message(UserMessage(content='later input'))
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._extension_session_inspection_supported
        ui._model_context_window = 7
        ui._usage['input'] = 999999
        ui._last_turn_usage['input'] = 888888
        ui._editor.set_text('unchanged parent draft')
        ui._extension_command('ext-navigator-inspect', '')
        await wait(lambda: ui._extension_pending is None)
        value = owner.extensions.entries.for_extension('navigator')[-1]['data']
        assert value['model']['id'] == 'test/model' and value['model']['name'] == 'Actual model'
        assert value['model']['supported_efforts'] == ['low', 'high']
        assert value['usage']['context_limit'] == 48000
        assert value['usage']['tokens'] == (190 if measured else None)
        assert value['usage']['source'] == ('last-response' if measured else 'unavailable')
        assert value['usage']['messages_since_usage'] == (1 if measured else None)
        assert 'PRIVATE-' not in json.dumps(value)
        assert value['model']['session_id'] == owner.meta.session_id == value['usage']['session_id']
        saved = runtime.transcript_store.get_extension_entries(owner.meta.session_id)
        assert saved['navigator'][-1]['data'] == value
        assert 'SESSION-INSPECTION' in '\n'.join(ui._chat_log.render(200))
        assert ui._editor.get_text() == 'unchanged parent draft' and ui._tui._focused is ui._editor
        assert not calls and not owner.extension_dialogs.has_pending
        assert server._orchestrator.inspect_extension_session('main', 'foreign', 'missing', 'model') is None
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('capability', [None, 0, True, 1])
async def test_session_capable_legacy_viewer_cannot_silently_adopt_new_inspection_requests(tmp_path, capability):
    import json

    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'legacy-inspect.sock'), pid_file=str(tmp_path / 'legacy-inspect.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('navigator',), extension_dir=str(root))
    server = GatewayServer(config)
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        request = {'type': 'ping', 'group_id': 'main', 'extension_session': 1}
        if capability is not None:
            request['extension_session_inspection'] = capability
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        state = json.loads(await reader.readline())
        assert state['extension_session_inspection'] == 1
        writer.write((json.dumps({'type': 'extension_control', 'request_id': 'inspection', 'group_id': 'main',
            'session_id': state.get('session_id', ''), 'action': 'command',
            'name': 'ext-navigator-inspect', 'args': ''}) + '\n').encode())
        await writer.drain()
        seen = []
        async with asyncio.timeout(8):
            while True:
                reply = json.loads(await reader.readline())
                seen.append(reply)
                if reply['type'] == 'extension_result':
                    break
        assert reply['success'] and not calls
        assert not any(row['type'] == 'extension_dialog' for row in seen)
        if type(capability) is int and capability == 1:
            value = json.loads(reply['output'].split('SESSION-INSPECTION ', 1)[1])
            assert value['model']['id'] == 'test/model' and value['usage']['source'] == 'unavailable'
            assert value['usage']['tokens'] is None
        else:
            assert reply['output'] == 'INSPECTION-UNAVAILABLE'
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
