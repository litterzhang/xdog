"""Goal-owned readonly display projection; previews never replace domain truth."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from xdog.agent.extensions.views import answer
from xdog.goal.models import Goal, identity
from xdog.goal.service import GoalService


def _preview(value: str, maximum: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value, False
    return encoded[:maximum - 3].decode("utf-8", errors="ignore") + "…", True


def _row(goal: Goal) -> dict[str, Any]:
    result: dict[str, Any] = {"id": goal.id, "status": goal.status, "version": goal.version,
        "criteria_count": len(goal.criteria), "review": goal.review.state if goal.review is not None else None,
        "notifications_enabled": goal.notifications_enabled, "preview_fields": []}
    for key, maximum in (("title", 160), ("outcome", 256), ("summary", 256)):
        result[key], truncated = _preview(getattr(goal, key), maximum)
        if truncated:
            result["preview_fields"].append(key)
    counts = Counter(task.state for task in goal.tasks)
    result["tasks"] = {"total": len(goal.tasks), **{state: counts[state] for state in (
        "pending", "starting", "running", "submitted", "accepted", "blocked", "interrupted", "cancelled")}}
    return result


class GoalView:
    def __init__(self, service: GoalService) -> None:
        self.service = service

    async def read(self, query: dict[str, Any]) -> dict[str, Any]:
        if type(query) is not dict or set(query) - {"goal_id", "offset", "limit"}:
            raise ValueError("Goal view allows only goal_id/offset/limit, never owner or mutation")
        offset, limit = query.get("offset", 0), query.get("limit", 16)
        if type(offset) is not int or not 0 <= offset < 2**53 or type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("Goal view offset/limit must be strict bounded integers")
        selected = identity(query["goal_id"]) if "goal_id" in query else None
        goals = ((await self.service.status(selected),) if selected is not None else await self.service.list())
        ordered = sorted(goals, key=lambda goal: goal.id)
        normalized = {"offset": offset, "limit": limit, **({"goal_id": selected} if selected is not None else {})}
        revision = hashlib.sha256(json.dumps({"owner": self.service.store.owner.key,
            "goals": [(goal.id, goal.version) for goal in ordered], "query": normalized}, sort_keys=True).encode()
        ).hexdigest()
        result = answer({"format": "xdog.goal_view.v1", "revision": revision, "total": len(ordered),
            "offset": offset, "items": [_row(goal) for goal in ordered[offset:offset + limit]],
            "next_offset": offset + limit if offset + limit < len(ordered) else None})
        assert result is not None
        return result
