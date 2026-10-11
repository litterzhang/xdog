from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream


async def test_current_tools_exclude_failed_workers_and_closed_owners_but_historical_guards_survive(tmp_path):
    root = tmp_path / 'extensions'
    for name in ('faultytools', 'toolinspector'):
        shutil.copytree(Path(__file__).parent / 'fixtures/extensions' / name, root / name)
    runtime = ExtensionRuntime(['faultytools', 'toolinspector'], root, tmp_path)
    try:
        await runtime.initialize()
        old = next(tool for tool in runtime.tools() if tool.name.startswith('ext_faultytools_'))
        with pytest.raises(Exception):
            await runtime.command('ext-faultytools-fail', '')
        assert old.name in runtime.aliases
        assert not any(tool.name == old.name for tool in runtime.tools())
        assert any(tool.name.startswith('ext_toolinspector_') for tool in runtime.tools())
        with pytest.raises(ValueError, match='registration changed'):
            await old.validate_arguments({})
        await runtime.aclose()
        assert not runtime.tools() and old.name in runtime.aliases
    finally:
        await runtime.aclose()


async def test_scoped_child_projection_never_readds_omitted_registered_tools_after_refresh_or_failure(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/faultytools', root / 'faultytools')
    runtime = ExtensionRuntime(['faultytools'], root, tmp_path)
    async def execute(*args):
        return AgentToolResult()
    native = AgentTool(name='native_probe', description='Retained read-only definition', parameters={}, execute=execute)
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model='offline/model'),
                  tools=[native])
    try:
        await runtime.initialize()
        runtime.sync_tools(agent, allowed=('native_probe',))
        assert agent.state.tools == (native,)
        runtime.sync_tools(agent, allowed=())
        assert not agent.state.tools
        agent.set_tools([native])
        with pytest.raises(Exception):
            await runtime.command('ext-faultytools-fail', '')
        runtime.sync_tools(agent, allowed=('native_probe',))
        assert agent.state.tools == (native,)
    finally:
        await runtime.aclose()
