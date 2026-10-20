"""Explicit parent actions and separately bound data-only worker capabilities."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from xdog.agent.async_work import run_owned
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.skills.goal.codec import json_text
from xdog.skills.goal.data_pages import Page, response
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.models import Goal, Report, report_digest
from xdog.skills.goal.service import GoalService

_ACTIONS: dict[str, set[str]] = {
    "create": {"title", "outcome", "criteria"},
    "list": {"offset", "limit", "revision"},
    "read": {"goal_id", "version", "offset", "limit"},
    "status": {"goal_id"},
    "add_task": {"goal_id", "description", "depends_on"},
    "assign": {"goal_id", "task_id", "assignment", "access"},
    "accept_task": {"goal_id", "task_id", "attempt_id", "report_revision", "report_digest", "summary"},
    "wait": {"goal_id", "reason"},
    "pause": {"goal_id", "reason"},
    "resume": {"goal_id"},
    "revise": {"goal_id", "outcome", "criteria", "reason"},
    "abandon": {"goal_id", "reason"},
    "cancel_task": {"goal_id", "task_id", "reason"},
    "cancel_notifications": {"goal_id"},
    "request_complete": {"goal_id", "paths", "summary"},
    "cancel_review": {"goal_id", "reason"},
    "reconcile": {"goal_id"},
    "export_views": {"goal_id"},
}
_ARRAYS = {"criteria", "depends_on", "paths", "evidence", "blockers"}


def _result(value: object, *, error: bool = False) -> AgentToolResult:
    return AgentToolResult.from_text(json_text(value, maximum=256 * 1024), is_error=error)


def _array(params: dict[str, Any], name: str, default: tuple[str, ...] | None = None) -> tuple[str, ...]:
    if name not in params and default is not None:
        return default
    value = params.get(name)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be an array of strings")
    return tuple(value)


def _status(goal: Goal) -> dict[str, object]:
    return {
        **asdict(goal),
        "report_digests": [
            {"task_id": task.id, "attempt_id": attempt.id, "revision": report.revision, "digest": report_digest(report)}
            for task in goal.tasks
            for attempt in task.attempts
            for report in attempt.reports
        ],
    }


async def _data_result(service: GoalService, build: Callable[[], object], selector: dict[str, object],
                       continuation: dict[str, object], page: Page, *, committed: bool = False) -> AgentToolResult:
    rendered = await run_owned(response, build, selector, continuation, page, committed=committed)
    service._owner()  # A yield in data encoding cannot transfer a reader to another host.
    return AgentToolResult.from_text(rendered)


def create_goal_tool(service: GoalService, context: Callable[[], OperationContext]) -> AgentTool:
    properties: dict[str, Any] = {"action": {"type": "string", "enum": list(_ACTIONS)}}
    for key in set().union(*_ACTIONS.values()):
        properties[key] = (
            {"type": "array", "items": {"type": "string"}}
            if key in _ARRAYS
            else {"type": "integer"}
            if key in {"report_revision", "version", "offset", "limit"}
            else {"type": "string"}
        )

    async def execute(
        call_id: str,
        params: dict[str, Any],
        cancel: asyncio.Event | None = None,
        on_update: Any = None,
        ctx: Any = None,
    ) -> AgentToolResult:
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        try:
            action = params.get("action")
            if not isinstance(action, str) or action not in _ACTIONS or set(params) - {"action"} - _ACTIONS[action]:
                raise ValueError("Unsupported parent Goal action/fields; no worker report or raw verdict authority")
            identifier = params.get("goal_id", "")
            if action == "create":
                value = await service.create(
                    params["title"], params["outcome"], _array(params, "criteria"), context=context()
                )
            elif action == "list":
                page = Page.read(params, force="revision" in params)
                goals = await service.list()
                owner_key = service.store.owner.key
                revision = await run_owned(lambda: hashlib.sha256(json_text(
                    {"owner": owner_key, "versions": [(goal.id, goal.version) for goal in goals]},
                    maximum=None).encode()).hexdigest())
                if "revision" in params and params["revision"] != revision:
                    raise ValueError("Goal collection changed; restart list rather than splice different snapshots")
                return await _data_result(service,
                    lambda: [{"id": goal.id, "title": goal.title, "status": goal.status, "version": goal.version}
                             for goal in goals],
                    {"kind": "goal_list", "revision": revision}, {"action": "list", "revision": revision}, page)
            elif action == "read":
                page = Page.read(params, force=True)
                value = await service.snapshot(identifier, params["version"])
                return await _data_result(service, lambda: _status(value),
                    {"kind": "goal_status", "goal_id": value.id, "version": value.version},
                    {"action": "read", "goal_id": value.id, "version": value.version}, page)
            elif action == "status":
                value = await service.status(identifier)
            elif action == "add_task":
                value = await service.add_task(
                    identifier, params["description"], depends_on=_array(params, "depends_on", ())
                )
            elif action == "assign":
                value = await service.assign(
                    identifier, params["task_id"], params["assignment"], access=params.get("access", "read-only")
                )
            elif action == "accept_task":
                value = await service.accept_task(
                    identifier,
                    params["task_id"],
                    params["attempt_id"],
                    params["report_revision"],
                    params["report_digest"],
                    params["summary"],
                )
            elif action == "revise":
                value = await service.revise(
                    identifier, params["outcome"], _array(params, "criteria"), params["reason"], context=context()
                )
            elif action == "pause":
                value = await service.pause(identifier, params["reason"], context=context())
            elif action == "resume":
                value = await service.resume(identifier, context=context())
            elif action == "wait":
                value = await service.wait(identifier, params["reason"])
            elif action == "abandon":
                value = await service.abandon(identifier, params["reason"])
            elif action == "cancel_task":
                value = await service.cancel_task(identifier, params["task_id"], params["reason"])
            elif action == "cancel_notifications":
                value = await service.cancel_notifications(identifier)
            elif action == "request_complete":
                value = await service.request_complete(
                    identifier, paths=_array(params, "paths", ()), summary=params.get("summary", "")
                )
            elif action == "cancel_review":
                value = await service.cancel_review(identifier, params["reason"])
            elif action == "export_views":
                return _result({"derived_path": str(await service.export_views(identifier)), "authority": "GoalStore"})
            else:
                value = await service.reconcile(identifier)
            return await _data_result(service, lambda: _status(value),
                {"kind": "goal_status", "goal_id": value.id, "version": value.version},
                {"action": "read", "goal_id": value.id, "version": value.version}, Page(),
                committed=action != "status")
        except Exception as exc:
            return _result({"error": str(exc)}, error=True)

    return AgentTool(
        "goal",
        "Explicit optional Goal contracts/tasks/acceptance/independent completion. "
        "Capability availability is not user delegation. No turn-end reviewer or source cleanup. "
        "Large results are explicit exact data pages: follow next_query with read/list; "
        "never repeat a committed action.",
        {"type": "object", "required": ["action"], "additionalProperties": False, "properties": properties},
        execute=execute,
    )


def create_report_tool(service: GoalService, goal_id: str, task_id: str, attempt_id: str) -> AgentTool:
    reporter = service.bound_reporter(goal_id, task_id, attempt_id)
    names = {"revision", "state", "summary", "evidence", "blockers"}

    async def execute(
        call_id: str,
        params: dict[str, Any],
        cancel: asyncio.Event | None = None,
        on_update: Any = None,
        ctx: Any = None,
    ) -> AgentToolResult:
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        try:
            if set(params) != names:
                raise ValueError("Bound task_report cannot choose another Goal/task/attempt or parent action")
            report = Report(
                task_id,
                attempt_id,
                params["revision"],
                params["state"],
                params["summary"],
                _array(params, "evidence"),
                _array(params, "blockers"),
            )
            goal = await reporter.report(report)
            return _result(
                {
                    "goal_id": goal.id,
                    "task_id": task_id,
                    "attempt_id": attempt_id,
                    "report_revision": report.revision,
                    "report_digest": report_digest(report),
                    "accepted": False,
                    "goal_completed": False,
                }
            )
        except Exception as exc:
            return _result({"error": str(exc)}, error=True)

    return AgentTool(
        "task_report",
        "Submit data only for this bound worker attempt; never accept/complete.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": sorted(names),
            "properties": {
                "revision": {"type": "integer", "minimum": 1},
                "state": {"type": "string", "enum": ["running", "submitted", "blocked"]},
                "summary": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "blockers": {"type": "array", "items": {"type": "string"}},
            },
        },
        execute=execute,
    )


def create_context_tool(
    service: GoalService, goal_id: str, *, task_id: str = "", attempt_id: str = "", review_id: str = ""
) -> AgentTool:
    if bool(review_id) == bool(task_id or attempt_id):
        raise ValueError("Context must bind exactly one worker or independent review")

    async def execute(
        call_id: str,
        params: dict[str, Any],
        cancel: asyncio.Event | None = None,
        on_update: Any = None,
        ctx: Any = None,
    ) -> AgentToolResult:
        try:
            if set(params) - {"section", "offset", "limit"}:
                raise ValueError("Bound context cannot select another owner or Goal/version")
            page = Page.read(params)
            section = params.get("section", "goal")
            goal = (
                await service.review_context(goal_id, review_id)
                if review_id
                else await service.worker_context(goal_id, task_id, attempt_id)
            )
            def build() -> object:
                if section == "criteria":
                    return goal.criteria
                if section == "task" and task_id:
                    return asdict(GoalService._task(goal, task_id))
                if section == "review" and goal.review is not None:
                    return asdict(goal.review)
                if section != "goal":
                    raise ValueError("Unknown bound context section")
                return asdict(goal)
            return await _data_result(service, build,
                {"kind": "goal_context", "goal_id": goal.id, "version": goal.version, "section": section,
                 "task_id": task_id, "attempt_id": attempt_id, "review_id": review_id},
                {"section": section}, page)
        except Exception as exc:
            return _result({"error": str(exc)}, error=True)

    return AgentTool(
        "goal_context",
        "Read this role's original canonical Goal contract; files are derived views only. "
        "Large sections return exact pages; follow next_query on this same bound tool until next_offset is null.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"section": {"type": "string", "enum": ["goal", "criteria", "task", "review"]},
                           "offset": {"type": "integer", "minimum": 0},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 65536}},
        },
        execute=execute,
    )
