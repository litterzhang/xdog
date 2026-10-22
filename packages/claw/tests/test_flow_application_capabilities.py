"""Installed Flow runs via the CLI in both actual hosts under their original gates."""
from __future__ import annotations

import asyncio
import json
from importlib.metadata import entry_points
from unittest.mock import Mock

import pytest
from xdog.agent.capabilities.runtime import ApplicationPort, CapabilitySet
from xdog.ai.types import AssistantMessage, Model, TextContent, ToolCall, ToolResultMessage, system_prompt_text
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.types import UserInput
from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session

from packages.coding.tests.test_sdk_session_state import _Provider


@pytest.fixture
def installed_flow_only(monkeypatch):
    # Exercise the real installed entry point, not a synthetic host adapter. Do
    # not open unrelated providers (in particular Eyes' application daemon).
    entries = tuple(entry for entry in entry_points(group='xdog.capabilities') if entry.name == 'flow')
    assert len(entries) == 1
    monkeypatch.setattr('xdog.agent.capabilities.runtime.entry_points', lambda **_: entries)
    return entries[0]


async def test_installed_flow_discovery_and_open_start_no_process_or_model(installed_flow_only, monkeypatch):
    forbidden = Mock(side_effect=AssertionError('Registration/open cannot execute work'))
    monkeypatch.setattr('subprocess.Popen', forbidden)
    monkeypatch.setattr('asyncio.create_subprocess_exec', forbidden)
    monkeypatch.setattr('xdog.agent.agent.Agent.__init__', forbidden)
    selected = CapabilitySet('application')
    try:
        assert selected.declared_tools == {'flow'} and not selected.default_skills
        assert not selected.tools and selected.get('flow') is None
        await selected.start(ApplicationPort())
        assert not selected.default_skills
        tool, = selected.tools
        assert tool.name == 'flow'
        assert tool.parameters['required'] == ['arguments']
        assert selected.get('flow') is not None
        assert not CapabilitySet('session').providers
        assert not CapabilitySet('group').providers
        forbidden.assert_not_called()
    finally:
        await selected.aclose()
    assert not selected.default_skills


@pytest.mark.parametrize('host,policy,allowed', [
    ('coding', 'allow-all', True), ('coding', 'ask', False), ('coding', 'deny', False),
    ('coding', 'read-only', False), ('coding', 'unselected', False),
    ('claw', 'writable', True), ('claw', 'read-only', False), ('claw', 'unselected', False),
    ('claw', 'disabled', False),
])
async def test_installed_flow_tool_and_skill_in_actual_hosts_keep_original_permissions(
        tmp_path, monkeypatch, installed_flow_only, host, policy, allowed):
    contexts, spawns = [], []
    invoke = False
    # A script-only workflow needs no model/provider. Its explicit cwd and
    # relative workflow path also prove argv/cwd reach the actual CLI.
    (tmp_path / 'workflow.json').write_text(json.dumps({
        'name': 'host-flow', 'entry': 'answer',
        'nodes': [{'id': 'answer', 'type': 'script',
                   'code': 'def answer(ctx):\n    return 42',
                   'outputs': [{'name': 'value', 'schema': {'type': 'integer'}}]}],
        'edges': [{'from': 'answer', 'to': '$output', 'map': {'value': 'answer'}}],
    }))
    params = {'arguments': ['run', 'workflow.json'], 'cwd': str(tmp_path)}
    def stream(_model, context, _options):
        contexts.append(context)
        response = (AssistantMessage(content=(ToolCall(id='flow-call', name='flow', arguments=params),),
                                     stop_reason='toolUse')
                    if invoke and context.messages[-1].role == 'user'
                    else AssistantMessage(content=(TextContent(text='done'),)))
        return EventStream.empty(response)
    provider = _Provider(Model(id='offline/model', context_window=200_000))
    provider.stream = stream
    monkeypatch.setattr('xdog.ai.load', lambda: provider)
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options',
                        lambda _: ('offline/model', stream))
    original_spawn = asyncio.create_subprocess_exec
    async def spawn(*args, **kwargs):
        spawns.append((args, kwargs))
        return await original_spawn(*args, **kwargs)
    monkeypatch.setattr('asyncio.create_subprocess_exec', spawn)
    server = session = None
    try:
        if host == 'coding':
            monkeypatch.setenv('CODING_DIR', str(tmp_path / 'coding-data'))
            session = create_agent_session(CreateSessionOptions(working_dir=tmp_path,
                overrides={'model': 'offline/model', 'permission_mode': policy
                           if policy in {'allow-all', 'ask', 'deny'} else 'allow-all'})).session
            assert not contexts and not spawns
            assert session.application_capabilities.get('flow') is None
            async def send(text):
                await session.send_message(text)
        else:
            server = GatewayServer(ClawConfig(data_dir=str(tmp_path / 'claw-data'),
                socket_path=str(tmp_path / 'native.sock'), pid_file=str(tmp_path / 'native.pid'),
                enabled_tools=() if policy == 'disabled' else None,
                groups=(GroupDef(id='main', workspace=str(tmp_path)),)))
            await server.start()
            app = server._orchestrator
            runtime = app._runtimes['main']
            assert runtime._active_session is None
            assert not contexts and not spawns
            session = await runtime.prepare_session(allow_reset=False)
            async def send(text):
                result = await app.route_message(UserInput(group_id='main', content=text, sender='test'))
                assert result is not None and result.error is None
        await send('inspect default instructions')
        assert not spawns and len(contexts) == 1
        owner = session.application_capabilities if host == 'coding' else app.application_capabilities
        binding = owner.get('flow')
        assert binding is not None
        assert (session.capabilities if host == 'coding' else runtime.capabilities).get('flow') is None
        assert 'flow' not in session.agent.active_skills
        prompt = system_prompt_text(contexts[0].system_prompt) or ''
        # Installed discovery is still visible, but registration injects no body.
        assert 'flow' in prompt
        assert '# flow — author & run workflows' not in prompt
        assert '**Prefer the single `flow` tool.**' not in prompt
        if host == 'coding':
            await asyncio.to_thread(session.activate_skill, 'flow')
        else:
            await session.skills_control('load', 'flow')
        await send('inspect explicitly loaded instructions')
        assert not spawns and len(contexts) == 2
        assert 'flow' in session.agent.active_skills
        loaded = system_prompt_text(contexts[-1].system_prompt) or ''
        assert '# flow — author & run workflows' in loaded
        assert '**Prefer the single `flow` tool.**' in loaded
        assert any(tool.name == 'flow' for tool in session.agent.state.tools) is (policy != 'disabled')
        if policy == 'read-only':
            session.access.set_read_only(True)
        if policy == 'unselected':
            session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
        invoke = True
        await send('invoke flow explicitly')
        result = next(message for message in session.agent.messages
                      if isinstance(message, ToolResultMessage) and message.tool_call_id == 'flow-call')
        assert result.is_error is not allowed
        assert len(spawns) == int(allowed)
        if allowed:
            saved = json.loads(''.join(part.text for part in result.content))
            assert saved['exit_code'] == 0, saved
            output = json.loads(saved['stdout'])
            assert output['success'] is True and output['output'] == {'answer': 42}
            argv, options = spawns[0]
            assert argv[-4:] == ('-m', 'xdog.flow', 'run', 'workflow.json')
            assert str(options['cwd']) == str(tmp_path)
        elif policy == 'read-only':
            assert 'read-only' in str(result.content).lower()
            assert session.access.state.read_only
        elif policy in {'ask', 'deny'}:
            assert session.permissions.mode == policy
        assert owner.get('flow') is binding  # No replacement/retry or second execution owner.
    finally:
        if server is not None:
            await server.stop()
        elif session is not None:
            await session.aclose()
