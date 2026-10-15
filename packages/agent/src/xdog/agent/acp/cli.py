"""Standard stdio ACP entry point; provider configuration stays in ai."""

from __future__ import annotations

from pathlib import Path

from xdog.agent import Agent, AgentConfig
from xdog.agent.acp.adapter import AcpAdapter
from xdog.agent.acp.backend import AgentBackend
from xdog.agent.acp.transport import serve


async def run(model: str | None = None) -> None:
    import xdog.ai as ai
    from xdog.agent.helpers import stream_fn_from_provider

    runtime = ai.load()
    if not runtime.active_providers():
        raise RuntimeError("No active providers. Run 'xdog-agent login' first.")

    def factory(cwd: Path) -> Agent:
        return Agent(
            stream_fn_from_provider(runtime),
            config=AgentConfig(model=model or "claude-sonnet-4.5"),
            tool_ctx={"fs_workspace": str(cwd)},
        )

    await serve(AcpAdapter(AgentBackend(factory)))
