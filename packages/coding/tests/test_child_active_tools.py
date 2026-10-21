"""Native host child factories retain parent explicit active subset through extension hooks."""
from __future__ import annotations

import inspect

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
        with pytest.raises(ValueError, match=r'exceeds (captured )?parent'):
            admission = parent.children.capture(spec)
            if inspect.isawaitable(admission):
                await admission
        assert not parent.children.snapshots and not parent.children.tool_limits
        assert not parent.children.extensions.owners
    finally:
        if server is not None:
            await server.stop()
        else:
            await parent.children.supervisor.aclose()
            await parent.extensions.aclose()
            parent.dispose()
