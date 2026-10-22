"""Real owner process with a deterministic model; test-only, no provider credentials."""

import asyncio
import json
import sys
from pathlib import Path

from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import SkillManager
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.acp.server import serve_local
from xdog.coding.config import get_sessions_dir
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.sessions.runtime import SessionRuntime
from xdog.coding.core.settings_manager import SettingsManager


async def main():
    root = Path(sys.argv[1]).resolve()
    manager = SessionManager(sessions_dir=get_sessions_dir())
    session = AgentSession(
        agent=Agent(
            lambda *_: EventStream.empty(AssistantMessage(content=(TextContent(text="owner reviewed"),))),
            config=AgentConfig(model="test"),
            skills=SkillManager(root / "skills"),
        ),
        session_data=manager.create_session(model="test", working_dir=root),
        session_manager=manager,
        settings=SettingsManager(),
        tool_registry=None,
        bash=None,
        working_dir=root,
    )
    runtime = SessionRuntime(session, bound=False)
    runtime.start()
    server = await serve_local(runtime)
    print(json.dumps({"session_id": session.session_id}), flush=True)
    try:
        await asyncio.to_thread(sys.stdin.buffer.read, 1)
    finally:
        await server.close()
        await asyncio.wrap_future(runtime.close())


if __name__ == "__main__":
    asyncio.run(main())
