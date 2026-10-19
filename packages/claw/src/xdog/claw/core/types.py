"""Core types for claw orchestration layer. Uses frozen dataclasses for immutability."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from xdog.agent import AgentConfig
from xdog.agent.session_input import SessionInput
from xdog.ai.types import ImageContent

# ---------------------------------------------------------------------------
# Queue mode
# ---------------------------------------------------------------------------

class QueueMode(StrEnum):
    COLLECT = "collect"
    STEER = "steer"
    STEER_BACKLOG = "steer-backlog"


# ---------------------------------------------------------------------------
# Group configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GroupConfig:
    """Operational config for message handling and session lifecycle."""
    queue_mode: QueueMode = QueueMode.COLLECT
    max_concurrent: int = 1
    daily_reset_hour: int = 4
    idle_reset_seconds: int = 0
    trigger_pattern: str | None = None
    debounce_ms: int = 500
    max_queued_messages: int = 50


@dataclass(frozen=True)
class Group:
    """A group definition — owns a workspace, agent config, and tool set."""
    id: str = ""
    name: str = ""
    is_main: bool = False
    workspace: str = ""
    agent_config: AgentConfig = field(default_factory=AgentConfig)
    config: GroupConfig = field(default_factory=GroupConfig)
    # None inherits defaults; an empty tuple explicitly disables all tools.
    enabled_tools: tuple[str, ...] | None = None
    image_model: str = ""


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionMeta:
    """Metadata for an active session.

    ``created_at`` and ``last_active`` are ISO-8601 strings so they
    round-trip cleanly through JSON without precision loss.
    """
    session_id: str = ""
    group_id: str = ""
    created_at: str = ""
    last_active: str = ""
    turn_count: int = 0
    label: str = ""


@dataclass
class SessionState:
    """Mutable runtime state for an active session.

    Tracks token usage, cost, and context utilization across turns.
    Updated by AgentSession after each turn.
    """
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    turn_count: int = 0
    context_tokens_used: int = 0
    context_window: int = 200_000


# ---------------------------------------------------------------------------
# Input types — user vs system
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UserInput:
    """Message from an external user (TUI, WeChat, etc.)."""
    group_id: str = ""
    content: str = ""
    sender: str = "user"
    channel: str = ""
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    images: tuple[ImageContent, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ExternalInput(UserInput):
    """Source-neutral routed input; canonical provenance survives transcript replay."""
    canonical: SessionInput


class SystemInputKind(StrEnum):
    """Discriminator for system-generated inputs."""
    SCHEDULER = "scheduler"
    CONTEXT_CONTINUATION = "context_continuation"
    FLUSH = "flush"
    COMPACTION = "compaction"


@dataclass(frozen=True)
class SystemInput:
    """Internal directive from a system component (scheduler, compaction, etc.)."""
    group_id: str = ""
    content: str = ""
    kind: SystemInputKind = SystemInputKind.SCHEDULER
    metadata: dict[str, Any] = field(default_factory=dict)


# Union for routing — callers use isinstance() to distinguish
@dataclass(frozen=True)
class ContextContinuation(SystemInput):
    """Host-reviewed canonical continuation; never an external user/scheduler input."""
    record_id: str = ''
    kind: SystemInputKind = SystemInputKind.CONTEXT_CONTINUATION


GroupInput = UserInput | SystemInput
