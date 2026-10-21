"""Official ACP interface; execution and policy are injected by the backend."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

try:
    from acp import (
        PROTOCOL_VERSION,
        InitializeResponse,
        LoadSessionResponse,
        NewSessionResponse,
        PromptResponse,
        RequestError,
    )
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
try:
    from acp import Agent as SdkAgent
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
try:
    from acp.interfaces import Client
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
try:
    from acp.schema import AgentCapabilities, Implementation, TextContentBlock
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
from xdog.agent.acp.contracts import MAX_INPUT_BYTES, Backend, Peer, SessionRef, invalid, metadata
from xdog.agent.async_work import run_owned


class AcpAdapter(SdkAgent):
    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self.peer: Peer | None = None
        self.sessions: dict[str, SessionRef] = {}
        self._closed = False

    def on_connect(self, conn: Client) -> None:
        self.peer = Peer(uuid.uuid4().hex, conn)

    def _peer(self) -> Peer:
        if self.peer is None or self._closed:
            raise invalid("ACP connection is not active")
        return self.peer

    def _session(self, identifier: str) -> SessionRef:
        if identifier not in self.sessions:
            raise invalid("Unknown or unbound session")
        return self.sessions[identifier]

    @staticmethod
    def _cwd(cwd: str, mcp_servers: object, additional_directories: object) -> Path:
        if mcp_servers:
            raise invalid("MCP configuration is not supported by this adapter")
        if additional_directories:
            raise invalid("Additional directories are not supported")
        path = Path(cwd)
        if not path.is_absolute() or not path.is_dir():
            raise invalid("Workspace must be an absolute existing directory")
        return path.resolve()

    async def _emit(self, session_id: str, update: Any) -> None:
        await asyncio.wait_for(self._peer().client.session_update(session_id=session_id, update=update), 10)

    async def initialize(
        self, protocol_version: int, client_capabilities: Any = None, client_info: Any = None, **kwargs: Any
    ) -> InitializeResponse:
        if type(protocol_version) is not int:
            raise invalid("Protocol version must be an integer")
        return InitializeResponse.model_validate(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "agentCapabilities": AgentCapabilities(load_session=True),
                "agentInfo": Implementation(name="xdog-agent", version="2.4.6"),
                "_meta": {"xdog": {"extensions": self.backend.extensions}},
            }
        )

    async def new_session(
        self, cwd: str, additional_directories: Any = None, mcp_servers: Any = None, **kwargs: Any
    ) -> NewSessionResponse:
        peer = self._peer()
        path = await run_owned(self._cwd, cwd, mcp_servers, additional_directories)
        if self._peer() is not peer:
            raise invalid("ACP peer changed during workspace validation")
        ref = await self.backend.new_session(path, peer)
        if self._peer() is not peer:
            raise invalid("ACP peer changed during session creation")
        self.sessions[ref.id] = ref
        return NewSessionResponse.model_validate({"sessionId": ref.id})

    async def load_session(
        self, cwd: str, session_id: str, mcp_servers: Any = None, additional_directories: Any = None, **kwargs: Any
    ) -> LoadSessionResponse:
        peer = self._peer()
        path = await run_owned(self._cwd, cwd, mcp_servers, additional_directories)
        if self._peer() is not peer:
            raise invalid("ACP peer changed during workspace validation")
        ref = await self.backend.load_session(session_id, path, peer, lambda update: self._emit(session_id, update))
        if self._peer() is not peer:
            raise invalid("ACP peer changed during session loading")
        self.sessions[ref.id] = ref
        return LoadSessionResponse()

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
        ref = self._session(session_id)
        if not prompt or any(not isinstance(block, TextContentBlock) for block in prompt):
            raise invalid("Only nonempty text prompts are supported")
        text = "\n".join(block.text for block in prompt)
        try:
            meta = metadata(kwargs.get("field_meta", kwargs.get("_meta", kwargs)))
            if not text.strip() or len(text.encode()) + len(str(meta).encode()) > MAX_INPUT_BYTES:
                raise ValueError("Prompt exceeds capacity or is empty")
        except (ValueError, UnicodeError) as exc:
            raise invalid(str(exc)) from exc
        return await self.backend.prompt(ref, self._peer(), text, meta, lambda update: self._emit(session_id, update))

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        await self.backend.cancel(self._session(session_id), self._peer())

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self.backend.extension(self._peer(), method, params)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        raise RequestError(-32601, "Unsupported extension notification")

    async def set_session_mode(self, session_id: str, mode_id: str, **kwargs: Any) -> Any:
        raise RequestError(-32601, "Session modes are not supported")

    async def set_config_option(self, config_id: str, session_id: str, value: str | bool, **kwargs: Any) -> Any:
        raise RequestError(-32601, "Session configuration is not supported")

    async def authenticate(self, method_id: str, **kwargs: Any) -> Any:
        raise RequestError(-32601, "Configure provider authentication outside ACP")

    async def list_sessions(self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any) -> Any:
        raise RequestError(-32601, "Session listing is not supported")

    async def close_session(self, session_id: str, **kwargs: Any) -> Any:
        raise RequestError(-32601, "Session closing is not supported")

    async def fork_session(
        self, session_id: str, cwd: str, additional_directories: Any = None, mcp_servers: Any = None, **kwargs: Any
    ) -> Any:
        raise RequestError(-32601, "Session forking is not supported")

    async def resume_session(
        self, session_id: str, cwd: str, additional_directories: Any = None, mcp_servers: Any = None, **kwargs: Any
    ) -> Any:
        raise RequestError(-32601, "Session resume is not supported")

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            if self.peer is not None:
                await self.backend.disconnect(self.peer)
