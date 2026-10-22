"""Application ownership for the two CLI hosts, not another execution runtime."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from xdog.agent.agent import Agent
from xdog.agent.async_work import run_owned, settle_cleanup
from xdog.agent.capabilities import ApplicationPort, CapabilityAssembly, CapabilitySet
from xdog.agent.core import StreamFn
from xdog.agent.skills import SkillManager, SkillPlacement
from xdog.agent.tools import ToolRegistry


@asynccontextmanager
async def application_agents(
    stream_fn: StreamFn, *, no_tools: bool = False,
) -> AsyncIterator[Callable[..., Awaitable[Agent]]]:
    """Own application bindings and close every Agent before its providers.

    No-tools skips discovery/open entirely (including installed Eyes startup).
    Managers still scan packaged skills for the optional discovery index.
    """
    application = None if no_tools else CapabilitySet('application')
    assembly = CapabilityAssembly() if application is None else CapabilityAssembly(application)
    agents: list[tuple[Agent, ToolRegistry]] = []

    async def create(cwd: Path, **kwargs: Any) -> Agent:
        manager = await run_owned(SkillManager, Path.home() / '.xdog' / 'skills', cwd / 'skills')
        selected = tuple(sorted(assembly.active_skills(kwargs.pop('active_skills', ()))))
        placement = await run_owned(SkillPlacement.read, manager, selected)
        agent = assembly.create_agent(stream_fn, skills=manager, active_skills=selected,
                                      prepared_skills=placement, **kwargs)
        registry = ToolRegistry(builtins=False)
        registry.replace_source('inventory', agent.local_tools)
        registry.publish(agent)
        agents.append((agent, registry))
        return agent

    async def close() -> None:
        steps = [agent.aclose for agent, _registry in reversed(agents)]
        if application is not None:
            steps.append(application.aclose)
        await settle_cleanup(*steps, message='CLI application cleanup failed')

    try:
        if application is not None:
            await application.start(ApplicationPort())
        yield create
    except BaseException as error:
        try:
            await close()
        except BaseException as cleanup:
            raise BaseExceptionGroup('CLI application and cleanup failed', [error, cleanup]) from None
        raise
    else:
        await close()
