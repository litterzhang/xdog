"""Independent Agent backend; no coding or other harness imports."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from acp import PromptResponse, RequestError
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
try:
    from acp.schema import AgentMessageChunk, TextContentBlock
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
from xdog.agent.acp.contracts import JsonValue, Peer, SessionRef, UpdateSink, invalid
from xdog.agent.acp.events import EventProjector
from xdog.agent.agent import Agent
from xdog.ai.types import AssistantMessage, TextContent


class AgentBackend:
    extensions: dict[str, int] = {}

    def __init__(self, factory: Callable[[Path], Agent]) -> None:
        self.factory = factory
        self.agents: dict[str, tuple[SessionRef, Agent]] = {}
        self.running: dict[str, tuple[str, asyncio.Task[Any]]] = {}
        self.owners: dict[str, str] = {}

    async def new_session(self, cwd: Path, peer: Peer) -> SessionRef:
        ref = SessionRef(uuid.uuid4().hex, cwd, uuid.uuid4().hex)
        self.agents[ref.id] = ref, self.factory(cwd)
        self.owners[ref.id] = peer.id
        return ref

    async def load_session(self, session_id: str, cwd: Path, peer: Peer, emit: UpdateSink) -> SessionRef:
        if session_id not in self.agents:
            raise invalid("Unknown session")
        ref, agent = self.agents[session_id]
        if ref.cwd != cwd:
            raise invalid("Session workspace mismatch")
        for message in agent.messages:
            if isinstance(message, AssistantMessage):
                text = "".join(item.text for item in message.content if isinstance(item, TextContent))
                if text:
                    await emit(
                        AgentMessageChunk.model_validate(
                            {
                                "sessionUpdate": "agent_message_chunk",
                                "content": TextContentBlock(type="text", text=text),
                            }
                        )
                    )
        return ref

    async def prompt(
        self, ref: SessionRef, peer: Peer, text: str, metadata: dict[str, JsonValue], emit: UpdateSink
    ) -> PromptResponse:
        if ref.id in self.running:
            raise invalid("A session prompt is already running")
        current = asyncio.current_task()
        assert current is not None
        self.running[ref.id] = peer.id, current
        agent = self.agents[ref.id][1]
        projector = EventProjector()
        try:
            stream = await agent.prompt(text)
            async for event in stream:
                for update in projector.project(event):
                    await emit(update)
            if agent.state.error:
                raise RequestError(-32000, agent.state.error)
            return PromptResponse(stop_reason="end_turn")
        except asyncio.CancelledError:
            await agent.cancel_and_wait()
            return PromptResponse(stop_reason="cancelled")
        finally:
            if not agent.is_idle:
                await agent.cancel_and_wait()
            await agent.wait_for_idle()
            self.running.pop(ref.id, None)

    async def cancel(self, ref: SessionRef, peer: Peer) -> None:
        running = self.running.get(ref.id)
        if running is not None and running[0] == peer.id:
            running[1].cancel()

    async def disconnect(self, peer: Peer) -> None:
        tasks = []
        for identifier, (owner, task) in tuple(self.running.items()):
            if owner == peer.id:
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for identifier, owner in tuple(self.owners.items()):
            if owner == peer.id and identifier not in self.running:
                _, agent = self.agents.pop(identifier)
                await agent.aclose()
                del self.owners[identifier]

    async def extension(self, peer: Peer, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError(-32601, "Unsupported extension")
