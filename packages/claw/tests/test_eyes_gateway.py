"""ACP is a normal gateway input; Eyes registration only ensures its own daemon."""
import json
from pathlib import Path

import pytest
from xdog.agent.capabilities.runtime import discover
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


@pytest.mark.parametrize('installed', [False, True])
async def test_gateway_default_acp_is_independent_from_eyes_and_goal(tmp_path, monkeypatch, installed):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
        lambda scope: tuple(item for item in discover(scope) if installed and item.name == 'eyes'))
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options', lambda _: ('test/dummy', None))
    config = ClawConfig(data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'g.sock'),
                       pid_file=str(tmp_path / 'g.pid'), groups=(GroupDef(id='main', workspace=str(tmp_path)),))
    gateway = GatewayServer(config)
    try:
        await gateway.start()
        assert gateway._acp_server is not None
        assert gateway._acp_server.path == tmp_path / 'data/acp/agent.sock'
        assert not (tmp_path / 'data/observers').exists()
        assert not hasattr(gateway._orchestrator, 'register_output_source')
        binding = gateway._application_capabilities.get('eyes')
        if installed:
            assert binding is not None
            tool = next(tool for tool in gateway._application_capabilities.tools if tool.name == 'eyes')
            result = await tool.execute('list-empty', {'action': 'list', 'owner': 'gateway-empty'})
            assert not result.is_error
            envelope = json.loads(''.join(part.text for part in result.content))
            assert envelope['exit_code'] == 0, envelope
            assert json.loads(envelope['stdout'])['registrations'] == []
        else:
            assert binding is None
        runtime = gateway._orchestrator._runtimes['main']
        assert runtime.input_address()['session_id'] == 'group:main'
        assert runtime._active_session is None
    finally:
        await gateway.stop()
    assert not Path(config.socket_path).exists()


async def test_main_prompt_has_its_normal_acp_address_and_enqueue_contract(tmp_path, monkeypatch):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda _: ())
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options', lambda _: ('test/dummy', None))
    gateway = GatewayServer(ClawConfig(data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'g.sock'),
        pid_file=str(tmp_path / 'g.pid'), groups=(GroupDef(id='main', workspace=str(tmp_path)),)))
    try:
        await gateway.start()
        runtime = gateway._orchestrator._runtimes['main']
        session = runtime.get_or_create_session()
        blocks = await session._prompt_blocks()
        text = '\n'.join(item.text for item in blocks)
        assert str(gateway._acp_server.path) in text
        assert 'group:main' in text and 'xdog/enqueue' in text
        assert 'message_id' in text and 'not completion' in text
        assert 'Eyes' not in text.split('# ACP incoming messages', 1)[1]
        assert not blocks[-1].cache
    finally:
        await gateway.stop()
