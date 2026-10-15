"""Source/harness-neutral ACP backend boundary."""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeAlias, cast

try:
    from acp import PromptResponse, RequestError
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
try:
    from acp.interfaces import Client
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
try:
    from acp.schema import (
        AgentMessageChunk,
        AgentThoughtChunk,
        SessionInfoUpdate,
        ToolCallProgress,
        ToolCallStart,
        UserMessageChunk,
    )
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
AcpUpdate: TypeAlias = (
    UserMessageChunk | AgentMessageChunk | AgentThoughtChunk | ToolCallStart | ToolCallProgress | SessionInfoUpdate
)
UpdateSink: TypeAlias = Callable[[AcpUpdate], Awaitable[None]]
MAX_INPUT_BYTES = 1024 * 1024


def finite_json(value: object, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("JSON nesting exceeds capacity")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            finite_json(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            finite_json(item, depth + 1)
        return
    raise ValueError("Expected finite JSON")


def metadata(value: object) -> dict[str, JsonValue]:
    if value is None:
        return {}
    finite_json(value)
    if not isinstance(value, dict):
        raise ValueError("Expected JSON metadata object")
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False)
    if len(encoded.encode()) > MAX_INPUT_BYTES:
        raise ValueError("Metadata exceeds capacity")
    return cast(dict[str, JsonValue], json.loads(encoded))


def invalid(message: str) -> RequestError:
    return RequestError(-32602, message)


@dataclass(frozen=True)
class SessionRef:
    id: str
    cwd: Path
    generation: str


@dataclass(frozen=True)
class Peer:
    id: str
    client: Client


class Backend(Protocol):
    extensions: dict[str, int]

    async def new_session(self, cwd: Path, peer: Peer) -> SessionRef: ...
    async def load_session(self, session_id: str, cwd: Path, peer: Peer, emit: UpdateSink) -> SessionRef: ...
    async def prompt(
        self, ref: SessionRef, peer: Peer, text: str, metadata: dict[str, JsonValue], emit: UpdateSink
    ) -> PromptResponse: ...
    async def cancel(self, ref: SessionRef, peer: Peer) -> None: ...
    async def disconnect(self, peer: Peer) -> None: ...
    async def extension(self, peer: Peer, method: str, params: dict[str, Any]) -> dict[str, Any]: ...
