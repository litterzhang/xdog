"""Installed capabilities consume public ports, not a Coding session or role labels."""
from __future__ import annotations

from dataclasses import replace

import pytest
from xdog.agent.capabilities.runtime import ParentPort
from xdog.agent.core import AgentTool, AgentToolResult

from packages.coding.tests.test_child_agents import create


class Binding:
    def __init__(self, host):
        self.host = host
        self.parents = []
        self.closed = False
        self.flushed = 0
        self.tools = (AgentTool(name='private_port', description='Owner tool', parameters={'type': 'object'},
                                execute=self.execute),)

    async def execute(self, *args, **kwargs):
        return AgentToolResult.from_text('owner')

    def attach(self, parent):
        assert isinstance(parent, ParentPort)
        self.parents.append(parent)

    async def flush(self):
        self.flushed += 1

    async def aclose(self):
        self.closed = True


class Provider:
    name = 'arbitrary'
    scopes = ('session',)
    tool_names = ('private_port',)
    requirements = ()

    async def open(self, host):
        self.binding = Binding(host)
        return self.binding


async def test_generic_provider_gets_owner_ports_and_normal_children_do_not_inherit_parent_tools(tmp_path, monkeypatch):
    provider = Provider()
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: (provider,))
    session, contexts = create(tmp_path)
    try:
        await session.initialize_capabilities()
        binding = provider.binding
        assert binding.host is not session
        parent = binding.parents[-1]
        assert parent.agent is session.agent and parent.children is session.children.capabilities
        assert parent.valid() and not parent.user_authorized()
        assert binding.host.owns_parent(parent)
        assert not binding.host.owns_parent(replace(parent, agent=object()))
        assert session.capabilities.tools == binding.tools
        assert binding.flushed == 1
        child = await session.children.supervisor.start('ordinary', config={'label': 'arbitrary-worker'})
        row = await session.children.supervisor.control('wait', child)
        assert row['status'] == 'completed'
        assert contexts and all('private_port' not in {tool.name for tool in context.tools} for context in contexts)
        assert not session.children.capabilities.permits(child, 'private_port', binding.tools)
        assert not session.children.capabilities.inherited(binding.tools[0])
        original = binding.tools[0]
        binding.tools = (replace(original),)
        assert not session.children.capabilities.inherited(original)  # Admission pinned the old actual object.
        # A same-name copy never acquires the exact parent's exclusion binding.
        assert session.children.capabilities.inherited(replace(binding.tools[0]))
    finally:
        await session.aclose()
    assert binding.closed


async def test_owner_navigation_invalidates_frozen_public_parent_handle(tmp_path, monkeypatch):
    provider = Provider()
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: (provider,))
    session, _ = create(tmp_path)
    try:
        await session.initialize_capabilities()
        host = provider.binding.host
        parent = provider.binding.parents[-1]
        session.session_data.active_branch = 'other'
        assert not host.valid() and not parent.valid()
        with pytest.raises(ValueError, match='owner'):
            await host.prepare_parent()
    finally:
        await session.aclose()
