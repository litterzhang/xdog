"""Deterministic real stdio server used only by the ACP integration test."""

import asyncio

from xdog.agent import Agent, AgentConfig
from xdog.agent.acp.adapter import AcpAdapter
from xdog.agent.acp.backend import AgentBackend
from xdog.agent.acp.transport import serve
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream


def factory(cwd):
    return Agent(
        lambda *_: EventStream.empty(AssistantMessage(content=(TextContent(text="stdio reviewed"),))),
        config=AgentConfig(model="test"),
    )


if __name__ == "__main__":
    asyncio.run(serve(AcpAdapter(AgentBackend(factory))))
