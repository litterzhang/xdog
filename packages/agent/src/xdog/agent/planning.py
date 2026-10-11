"""Frozen session planning data and an owned metadata tool, not a tool-policy layer."""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from xdog.agent.core import AgentTool, AgentToolResult
from xdog.ai.types import TextContent


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        raise ValueError("Plan text must be bounded UTF-8 text")
    return value


@dataclass(frozen=True, slots=True)
class PlanStep:
    description: str
    status: str = "pending"


@dataclass(frozen=True, slots=True)
class PlanState:
    mode: str = "off"
    revision: int = 0
    objective: str = ""
    explanation: str = ""
    steps: tuple[PlanStep, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> PlanState:
        if value is None:
            return cls()
        if (not isinstance(value, dict) or not isinstance(value.get("mode"), str)
                or set(value) - {"mode", "revision", "objective", "explanation", "steps"}
                or value.get("mode") not in {"off", "planning", "executing"}
                or type(value.get("revision")) is not int or value["revision"] < 0):
            raise ValueError("Invalid saved plan; refusing to drop planning restrictions")
        objective = _text(value.get("objective", ""), 4000)
        explanation = _text(value.get("explanation", ""), 4000)
        rows = value.get("steps", [])
        if not isinstance(rows, (list, tuple)) or len(rows) > 32:
            raise ValueError("Plan must have at most 32 steps")
        steps = []
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("status"), str)
                    or set(row) - {"description", "status"}
                    or row.get("status") not in {"pending", "in_progress", "completed"}):
                raise ValueError("Invalid plan step/status")
            text = _text(row.get("description"), 2000)
            if not text.strip():
                raise ValueError("Plan steps must not be empty")
            steps.append(PlanStep(text, row["status"]))
        if sum(step.status == "in_progress" for step in steps) > 1:
            raise ValueError("Only one plan step may be in progress")
        if value["mode"] != "off" and not objective.strip():
            raise ValueError("Active plan requires an objective")
        return cls(value["mode"], value["revision"], objective, explanation, tuple(steps))

    def prompt(self) -> str:
        if self.mode == "off":
            return ""
        return ("\n\n## Session plan (host-owned mode)\n" + json.dumps(self.to_dict(), ensure_ascii=False) +
                "\nUse update_plan with the current expected_revision to maintain structured steps. "
                "The tool cannot change mode. Only the user can transition to execution. " +
                ("Planning is read-only: shell, external tools, writes and unknown tools are blocked."
                 if self.mode == "planning" else "Execution retains the host's existing tool permission policy."))


class PlanOwner:
    def __init__(self, state: PlanState, persist: Callable[[PlanState], None]) -> None:
        self._state, self._persist = state, persist
        self._lock = threading.Lock()
        self._closed = threading.Event()

    @property
    def state(self) -> PlanState:
        return self._state

    def close(self) -> None:
        self._closed.set()

    def change(self, action: str, expected_revision: int, objective: str = "",
               *, steps: Any = None, explanation: str = "") -> PlanState:
        with self._lock:
            old = self._state
            if self._closed.is_set():
                raise RuntimeError("Plan owner is closed")
            if type(expected_revision) is not int or expected_revision != old.revision:
                raise ValueError("Plan changed; inspect its latest revision before retrying")
            if action == "start":
                new = PlanState("planning", old.revision + 1, _text(objective, 4000))
            elif action == "revise":
                new = replace(old, mode="planning", revision=old.revision + 1)
            elif action == "execute":
                if old.mode != "planning" or not old.steps:
                    raise ValueError("Inspect a nonempty planning draft before executing")
                new = replace(old, mode="executing", revision=old.revision + 1)
            elif action == "stop":
                new = replace(old, mode="off", revision=old.revision + 1)
            elif action == "update":
                if old.mode == "off":
                    raise ValueError("Start a session plan explicitly before updating it")
                new = PlanState.from_dict({**old.to_dict(), "steps": steps, "explanation": explanation,
                                           "revision": old.revision + 1})
            else:
                raise ValueError("Unknown planning operation")
            new = PlanState.from_dict(new.to_dict())
            self._persist(new)  # Commit before publication; failures retain old state.
            self._state = new
            return new

    async def apply(self, action: str, revision: int, objective: str = "", *, steps: Any = None,
                    explanation: str = "") -> PlanState:
        task = asyncio.create_task(asyncio.to_thread(
            self.change, action, revision, objective, steps=steps, explanation=explanation))
        try:
            state = await asyncio.shield(task)
        except asyncio.CancelledError:
            # Settle this metadata owner before session/branch navigation can
            # reuse its persistence path. Threads cannot be forcibly canceled.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                task.exception()
            raise
        return state

    def tool(self) -> AgentTool:
        async def execute(_id: str, params: dict[str, Any], *args: Any, **kwargs: Any) -> AgentToolResult:
            if set(params) - {"expected_revision", "steps", "explanation"}:
                raise ValueError("update_plan cannot change host mode or other fields")
            revision = params.get("expected_revision")
            if type(revision) is not int:
                raise ValueError("Expected an integer plan revision")
            state = await self.apply("update", revision, steps=params.get("steps"),
                                     explanation=params.get("explanation", ""))
            return AgentToolResult(content=(TextContent(text=json.dumps(state.to_dict())),))
        return AgentTool(
            name="update_plan", description="Update the active session plan; cannot authorize execution or tools.",
            parameters={"type": "object", "required": ["expected_revision", "steps"], "additionalProperties": False,
                        "properties": {"expected_revision": {"type": "integer", "minimum": 0},
                                       "explanation": {"type": "string"}, "steps": {"type": "array", "maxItems": 32,
                                        "items": {"type": "object", "additionalProperties": False,
                                                  "required": ["description", "status"],
                                                  "properties": {"description": {"type": "string"}, "status": {
                                                      "enum": ["pending", "in_progress", "completed"]}}}}}},
            execute=execute,
        )
