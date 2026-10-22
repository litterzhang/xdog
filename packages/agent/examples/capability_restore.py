"""Offline public SDK reassembly probe; no installed provider or daemon is opened.

    uv run --no-sync python packages/agent/examples/capability_restore.py

Real hosts supply their own trusted installed providers, SkillManager, permission
hooks and already-opened owner sets. They can choose restore_agent or
restore_agent_parts instead of bare Agent.restore when current session defaults
must also apply to old snapshots. Tools and live bindings remain host-owned.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from xdog.agent import Agent, AgentConfig
from xdog.agent.capabilities import ApplicationPort, CapabilityAssembly, CapabilitySet
from xdog.agent.skills import SkillManager


async def demonstrate(root: Path) -> dict[str, Any]:
    directory = root / 'guard'
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text(
        '---\nname: guard\ndescription: offline instructions\n---\nCURRENT_DEFAULT_BODY', encoding='utf-8')
    manager = SkillManager(root, packaged={})
    closed: list[str] = []

    class Binding:
        tools = ()

        async def aclose(self) -> None:
            closed.append('application')

    class Provider:
        name = 'instructions'
        scopes = ('application',)
        requirements = ()
        tool_names = ()
        skill_names = ('guard',)  # Optional discovery metadata, not activation.
        default_skills = ('guard',)

        async def open(self, host: object) -> Binding:
            return Binding()

    def no_model(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError('Construction/restore must never call a model')

    # Limit discovery to the synthetic trusted entry point, even in a full
    # workspace installation with Flow/Eyes. No daemon/process is started.
    entry = SimpleNamespace(name='instructions', load=lambda: Provider())
    with patch('xdog.agent.capabilities.runtime.entry_points', return_value=(entry,)):
        application = CapabilitySet('application')
    assembly = CapabilityAssembly(application)
    await application.start(ApplicationPort())
    try:
        async with Agent(no_model, config=AgentConfig(model='offline/probe'), skills=manager) as original:
            snapshot = original.dump()  # A legal older snapshot without guard.
            async with assembly.create_agent(no_model, skills=manager) as restored:
                assembly.restore_agent(restored, snapshot, allowed_models=['offline/probe'])
                assert restored.active_skills == ('guard',) and not restored.state.tools
                assert str(restored.state.system_prompt).count('CURRENT_DEFAULT_BODY') == 1
                # Same API contract for host-owned separately persisted history.
                assembly.restore_agent_parts(restored, original.dump_metadata(), original.messages,
                                             allowed_models=['offline/probe'])
                exported = restored.dump()
                json.dumps(exported)  # Data only: no runtimes/executors/callables.
                result = {'active_skills': list(restored.active_skills),
                          'declared_skills': sorted(assembly.declared_skills),
                          'tools': len(restored.state.tools), 'model_calls': 0,
                          'snapshot_skill': exported['skills'][0]['slug']}
    finally:
        await application.aclose()
    result['closed'] = closed
    return result


if __name__ == '__main__':
    with TemporaryDirectory(prefix='xdog-agent-restore-') as temporary:
        print(json.dumps(asyncio.run(demonstrate(Path(temporary))), sort_keys=True))
