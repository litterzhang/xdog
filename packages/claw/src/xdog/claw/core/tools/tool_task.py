"""Group-scoped task management through the running scheduler service."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from xdog.agent.core import AgentTool
from xdog.agent.tool_def import Param, ToolDef, action
from xdog.claw.core.planning.task_scheduler import TaskScheduler
from xdog.claw.core.types import ScheduledTask, TaskSchedule


def _service(ctx: dict[str, Any]) -> tuple[TaskScheduler, str]:
    scheduler = ctx.get("_task_scheduler")
    group = ctx.get("group_id")
    if not isinstance(scheduler, TaskScheduler) or not isinstance(group, str) or not group.strip():
        raise ValueError("Task tool requires a running scheduler and current group context")
    return scheduler, group


class TaskTool(ToolDef):
    name = "task"
    description = "Manage this group's scheduled tasks. One-off tasks use run_at, not annual cron."

    @action("schedule", description="Schedule one future run or a recurring task; choose exactly one schedule",
            group_id=Param("string", description="Defaults to current group; other groups are not authorized"),
            cron=Param("string", description="Recurring five-field cron; requires timezone"),
            timezone=Param("string", description="Explicit IANA timezone for cron, e.g. Asia/Shanghai"),
            run_at=Param("string", description="One-off ISO-8601 with offset, e.g. 2026-10-12T09:24:00+08:00"),
            interval_seconds=Param("integer", description="Positive interval; first run after one interval"),
            prompt=Param("string", required=True, description="What to do and report"))
    async def schedule(self, ctx: dict[str, Any], prompt: str, group_id: str = "", cron: str | None = None,
                       run_at: str | None = None, interval_seconds: int | None = None, timezone: str = "") -> str:
        scheduler, owner = _service(ctx)
        if group_id and group_id != owner:
            raise ValueError("Tasks may only target the current group")
        timestamp = None
        if run_at is not None:
            dt = datetime.fromisoformat(run_at)
            if dt.utcoffset() is None:
                raise ValueError("run_at requires an explicit UTC offset")
            timestamp = dt.timestamp()
            if timestamp <= scheduler.now():
                raise ValueError("run_at must be in the future")
        if timezone and cron is None:
            raise ValueError("timezone is only used with cron; run_at includes its own offset")
        task = ScheduledTask(id=f"task-{uuid.uuid4().hex[:8]}", group_id=owner, prompt=prompt,
                             schedule=TaskSchedule(cron=cron, run_at=timestamp,
                                                   interval_seconds=interval_seconds, timezone=timezone))
        scheduler.add_task(task)
        saved = scheduler.get_task(task.id)
        assert saved is not None
        return (f"Task scheduled: {task.id}; next_run={saved.next_run}; "
                f"timezone={timezone or 'run_at offset / interval'}")

    @action("cancel", description="Cancel a task; an already executing turn cannot be undone",
            task_id=Param("string", required=True, description="Task ID to cancel"))
    async def cancel(self, ctx: dict[str, Any], task_id: str) -> str:
        scheduler, owner = _service(ctx)
        task = scheduler.get_task(task_id)
        if task is None or task.group_id != owner:
            return f"No task found with id {task_id}."
        scheduler.remove_task(task_id)
        return f"Task {task_id} cancelled. Already-started side effects cannot be undone."

    @action("list", description="List this group's tasks, next run and execution/delivery results")
    async def list(self, ctx: dict[str, Any]) -> str:
        scheduler, owner = _service(ctx)
        tasks = [task for task in scheduler.list_tasks() if task.group_id == owner]
        if not tasks:
            return "No scheduled tasks."
        return "\n".join(
            f"- {t.id} enabled={t.enabled} status={t.status} delivery={t.delivery_status} "
            f"schedule={t.schedule} next_run={t.next_run} last_run={t.last_run} "
            f"error={t.last_error} prompt={t.prompt!r}" for t in tasks
        )


def create_task_tool() -> AgentTool:
    return TaskTool().build()
