"""Native host child factories retain parent explicit active subset through extension hooks."""
from __future__ import annotations

import pytest
from xdog.agent.supervision import ChildSpec

from packages.claw.tests.test_child_agents import setup as claw_setup
from packages.coding.tests.test_child_agents import create as coding_setup


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('selection', [[], ['current_time']])
async def test_child_model_and_executor_never_regain_parent_unselected_tools(tmp_path, host, selection):
    server = None
    contexts = []
    if host == 'coding':
        parent, contexts = coding_setup(tmp_path, mode='allow-all')
    else:
        server, _ = await claw_setup(tmp_path, contexts)
        parent = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
    try:
        parent.agent.set_active_tools(selection, expected_revision=parent.agent.tool_selection_revision)
        child = await parent.children.supervisor.start('write', access='inherit')
        result = await parent.children.supervisor.control('wait', child)
        assert result['status'] == 'completed'
        assert contexts and all([tool.name for tool in context.tools or ()] == selection for context in contexts)
        assert not (tmp_path / 'target').exists() and not (tmp_path / 'workspace/target').exists()
        assert not parent.agent.messages
        assert 'filesystem' in {tool.name for tool in parent.agent.state.tools}
        assert parent.agent.active_tool_selection == tuple(selection)
        assert not parent.children.snapshots and not parent.children.tool_limits
    finally:
        if server is not None:
            await server.stop()
        else:
            await parent.children.supervisor.aclose()
            await parent.extensions.aclose()
            parent.dispose()


@pytest.mark.parametrize('host', ['coding', 'claw'])
async def test_child_capture_rejects_profile_outside_parent_selection_before_worker_start(tmp_path, host):
    server = None
    if host == 'coding':
        parent, _ = coding_setup(tmp_path)
    else:
        server, _ = await claw_setup(tmp_path, [])
        parent = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
    try:
        parent.agent.set_active_tools(['current_time'], expected_revision=parent.agent.tool_selection_revision)
        spec = ChildSpec(id='a' * 12, task='work', tools=('filesystem',))
        with pytest.raises(ValueError, match='exceeds captured parent'):
            parent.children.capture(spec)
        assert not parent.children.snapshots and not parent.children.tool_limits
        assert not parent.children.extensions.owners
    finally:
        if server is not None:
            await server.stop()
        else:
            await parent.children.supervisor.aclose()
            await parent.extensions.aclose()
            parent.dispose()


@pytest.mark.parametrize('host', ['coding', 'claw'])
async def test_child_extension_dynamic_registration_cannot_expand_captured_parent_selection(tmp_path, host):
    import shutil
    from pathlib import Path
    from unittest.mock import patch

    from xdog.agent import AgentTool
    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    from packages.coding.tests.test_rpc_extensions import session as coding_extensions

    server = None
    contexts = []
    def stream(model, context, options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage())
    if host == 'coding':
        parent, _ = coding_extensions(tmp_path, extra=('toolmock',))
        parent.agent.set_stream_fn(stream)
        parent.agent.set_tools((*parent.agent.state.tools, AgentTool(name='current_time', parameters={})))
    else:
        root = tmp_path / 'extensions'
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/toolmock', root / 'toolmock')
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
            extensions=('toolmock',), extension_dir=str(root))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
            await server.start()
        parent = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
    spec = ChildSpec(id='a' * 12, task='inspect', inherit_context=False)
    try:
        await parent.initialize_extensions()
        parent.agent.set_active_tools(['current_time'], expected_revision=parent.agent.tool_selection_revision)
        parent.children.capture(spec)
        # Later parent expansion cannot change already-admitted child's ceiling.
        parent.agent.set_active_tools(None, expected_revision=parent.agent.tool_selection_revision)
        child = await parent.children.create(spec, lambda _: None)
        extension = parent.children.extensions.owners[spec.id]
        await extension.command('ext-toolmock-enable', '')
        assert any('/target' in tool.label for tool in extension.tools())
        events = await child.prompt('do not expand tools')
        async for _ in events:
            pass
        await child.wait_for_idle()
        assert contexts and [tool.name for tool in contexts[-1].tools or ()] == ['current_time']
        assert [tool.name for tool in child.state.tools] == ['current_time']
        assert extension.context()['child_config']['tools'] == ['current_time']
    finally:
        await parent.children.finish(spec)
        assert not parent.children.tool_limits and not parent.children.snapshots
        if server is not None:
            await server.stop()
        else:
            await parent.children.supervisor.aclose()
            await parent.extensions.aclose()
            parent.dispose()
