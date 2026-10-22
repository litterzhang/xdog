"""Real Gateway extension-model restarts restore only complete, enabled inventories."""
from __future__ import annotations

from dataclasses import replace
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.runtime.session import AgentSession
from xdog.claw.core.types import UserInput

from packages.coding.tests.test_extension_provider_startup import ALIAS


@pytest.mark.parametrize('saved,enabled,expected', [
    (('filesystem', 'eyes', 'flow'), None, ('filesystem', 'eyes', 'flow')),
    (('filesystem', 'flow'), None, ('filesystem', 'flow')),
    ((), None, ()),
    (None, None, None),
    (('filesystem', 'eyes', 'flow'), ('filesystem',), ('filesystem',)),
])
async def test_gateway_cold_extension_restore_waits_for_application_inventory(
        tmp_path, monkeypatch, saved, enabled, expected):
    entries = tuple(entry for entry in entry_points(group='xdog.capabilities') if entry.name in {'flow', 'eyes'})
    assert {entry.name for entry in entries} == {'flow', 'eyes'}
    monkeypatch.setattr('xdog.agent.capabilities.runtime.entry_points', lambda **_: entries)
    def no_fallback(*_args):
        raise AssertionError('Extension model must not call the base adapter')
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options', lambda _: (ALIAS, no_fallback))
    config = ClawConfig(model=ALIAS, data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
        extensions=('providermock',),
        extension_dir=str(Path(__file__).resolve().parents[2] / 'agent/tests/fixtures/extensions'),
        groups=(GroupDef(id='main', workspace=str(tmp_path)),))
    first = GatewayServer(config)
    await first.start()
    try:
        runtime = first._orchestrator._runtimes['main']
        session = runtime._active_session
        assert session is not None
        identifier = session.meta.session_id
        await session.select_active_tools(saved, expected_revision=session.agent.tool_selection_revision)
        assert runtime.transcript_store.get_active_tools(identifier) == saved
    finally:
        await first.stop()

    # Observe the actual early bootstrap, not a separately assembled SDK Agent.
    bootstrap = []
    original = AgentSession.initialize_extensions
    async def observe(self, **kwargs):
        await original(self, **kwargs)
        if kwargs.get('require_model') and not self._runtime.application_capabilities.started:
            bootstrap.append((self.agent.active_tool_selection, self._saved_active_tools,
                              self._store.get_active_tools(identifier)))
    monkeypatch.setattr(AgentSession, 'initialize_extensions', observe)
    second = GatewayServer(replace(config, enabled_tools=enabled))
    await second.start()
    try:
        runtime = second._orchestrator._runtimes['main']
        session = runtime._active_session
        assert session.meta.session_id == identifier
        assert bootstrap == [(None if saved is None else (), saved, saved)]
        assert not session._tool_selection_restore_pending
        assert session.agent.active_tool_selection == expected
        assert runtime.transcript_store.get_active_tools(identifier) == expected
        assert not {'flow', 'eyes'} & set(session.agent.active_skills)
        if enabled is not None:
            assert not {'flow', 'eyes'} & {tool.name for tool in session.agent.state.tools}
        result = await second._orchestrator.route_message(UserInput(
            group_id='main', content='continue on the extension model', sender='test'))
        assert result.error is None and 'EXT-PROVIDER-ANSWER' in result.response_text
        await second._orchestrator.refresh_application_capabilities()
        assert session.agent.active_tool_selection == expected
        assert runtime.transcript_store.get_active_tools(identifier) == expected
    finally:
        await second.stop()
