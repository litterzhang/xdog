"""Core types for claw orchestration layer. Uses frozen dataclasses for immutability."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from xdog.agent import AgentConfig

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
# Task scheduling
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskSchedule:
    cron: str | None = None
    interval_seconds: int | None = None
    run_at: float | None = None


@dataclass(frozen=True)
class ScheduledTask:
    id: str = ""
    group_id: str = ""
    prompt: str = ""
    schedule: TaskSchedule = field(default_factory=TaskSchedule)
    enabled: bool = True
    last_run: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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


class SystemInputKind(StrEnum):
    """Discriminator for system-generated inputs."""
    GOAL_RUNNER = "goal_runner"
    SCHEDULER = "scheduler"
    FLUSH = "flush"
    COMPACTION = "compaction"


@dataclass(frozen=True)
class SystemInput:
    """Internal directive from a system component (goal runner, scheduler, etc.)."""
    group_id: str = ""
    content: str = ""
    kind: SystemInputKind = SystemInputKind.SCHEDULER
    metadata: dict[str, Any] = field(default_factory=dict)


# Union for routing — callers use isinstance() to distinguish
GroupInput = UserInput | SystemInput


# ---------------------------------------------------------------------------
# Goal types
# ---------------------------------------------------------------------------

class GoalStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

class VerificationMethod(StrEnum):
    SCRIPT = "script"           # bash command, exit 0 = pass
    CONDITIONS = "conditions"   # list of conditions, LLM evaluates all-true


class VerificationResult(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


@dataclass(frozen=True)
class Verification:
    """How to verify a goal or task is actually done."""
    method: VerificationMethod = VerificationMethod.CONDITIONS
    script: str = ""                        # bash command (when method=script)
    conditions: tuple[str, ...] = ()        # checklist (when method=conditions)


@dataclass(frozen=True)
class VerificationRun:
    """Result of running a verification check."""
    result: VerificationResult = VerificationResult.FAILED
    output: str = ""                        # script stdout/stderr or LLM reasoning
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Goal types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GoalTask:
    id: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    notes: str = ""
    summary: str = ""
    depends_on: tuple[str, ...] = ()                          # task IDs that must complete first
    verification: Verification | None = None
    last_verification_run: VerificationRun | None = None


@dataclass(frozen=True)
class Goal:
    id: str = ""
    group_id: str = ""
    title: str = ""
    description: str = ""
    tasks: tuple[GoalTask, ...] = ()
    status: GoalStatus = GoalStatus.ACTIVE
    summary: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    verification: Verification = field(default_factory=Verification)
    last_verification_run: VerificationRun | None = None


@dataclass(frozen=True)
class GoalNotification:
    goal_id: str = ""
    title: str = ""
    summary: str = ""
    status: str = ""
    timestamp: float = 0.0
