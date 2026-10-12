"""Controlled-clock scheduler, storage and group-scoped tool contracts."""
import json
import sys
from dataclasses import asdict
from datetime import datetime

import pytest
from xdog.claw.core.planning.task_scheduler import TaskScheduler
from xdog.claw.core.tools.tool_task import create_task_tool
from xdog.claw.core.types import ScheduledTask, TaskSchedule

NOW = datetime.fromisoformat("2026-10-12T09:20:00+08:00").timestamp()


@pytest.fixture
def clock():
    return [NOW]


@pytest.fixture
def sched(tmp_path, clock):
    return TaskScheduler(tmp_path / "tasks.json", clock=lambda: clock[0])


def task(schedule, id="t1", **kwargs):
    return ScheduledTask(id=id, group_id="g1", prompt="report", schedule=schedule, **kwargs)


async def call(sched, action, group="g1", **kwargs):
    result = await create_task_tool().execute("call", {"action": action, **kwargs},
                                             ctx={"_task_scheduler": sched, "group_id": group})
    return result.content[0].text


def test_interval_first_run_waits_and_repeats(sched, clock):
    sched.add_task(task(TaskSchedule(interval_seconds=60)))
    assert sched.get_due_tasks() == []
    clock[0] += 60
    assert len(sched.get_due_tasks()) == 1
    assert sched.start_run("t1")
    assert sched.start_run("t1") is None
    assert sched.get_due_tasks() == []
    sched.mark_run("t1")
    assert sched.get_task("t1").next_run == clock[0] + 60
    clock[0] += 59
    assert sched.get_due_tasks() == []
    clock[0] += 1
    assert len(sched.get_due_tasks()) == 1


def test_one_off_and_reload(sched, clock):
    sched.add_task(task(TaskSchedule(run_at=NOW + 60)))
    assert sched.get_due_tasks() == []
    clock[0] += 60
    assert len(sched.get_due_tasks()) == 1
    sched.start_run("t1")
    sched.mark_run("t1")
    saved = sched.get_task("t1")
    assert not saved.enabled and saved.last_run == clock[0]
    reloaded = TaskScheduler(sched._tasks_file, clock=lambda: clock[0])
    assert reloaded.get_task("t1") == saved
    clock[0] += 86400
    assert reloaded.get_due_tasks() == []


@pytest.mark.parametrize("zone,delta", [("Asia/Shanghai", 240), ("UTC", 8 * 3600 + 240)])
def test_cron_first_time_and_timezone(sched, clock, zone, delta):
    sched.add_task(task(TaskSchedule(cron="24 9 12 10 *", timezone=zone)))
    assert sched.get_task("t1").next_run == NOW + delta
    clock[0] += delta - 1
    assert sched.get_due_tasks() == []
    clock[0] += 1
    assert len(sched.get_due_tasks()) == 1
    sched.mark_run("t1")
    assert sched.get_task("t1").next_run > clock[0] + 300 * 86400
    assert sched.get_due_tasks() == []


@pytest.mark.parametrize("schedule", [TaskSchedule(), TaskSchedule(cron="* * * * *"),
    TaskSchedule(cron="* * * * *", timezone="not/a-zone"),
    TaskSchedule(cron="bad", timezone="UTC"), TaskSchedule(cron="* * * * * *", timezone="UTC"),
    TaskSchedule(cron="99 9 * * *", timezone="UTC"), TaskSchedule(interval_seconds=0),
    TaskSchedule(interval_seconds=-1), TaskSchedule(run_at=float("nan")),
    TaskSchedule(run_at=NOW, interval_seconds=2)])
def test_invalid_schedule_not_persisted(sched, schedule):
    with pytest.raises(ValueError):
        sched.add_task(task(schedule))
    assert sched.list_tasks() == []
    assert not sched._tasks_file.exists()


def test_missing_croniter_fails_closed(sched, monkeypatch):
    monkeypatch.setitem(sys.modules, "croniter", None)
    with pytest.raises(ValueError, match="croniter"):
        sched.add_task(task(TaskSchedule(cron="* * * * *", timezone="UTC")))
    assert sched.get_due_tasks() == []


async def test_tool_immediate_visibility_scope_and_cancel(sched, clock):
    text = await call(sched, "schedule", prompt="check", run_at="2026-10-12T09:24:00+08:00")
    created = sched.list_tasks()[0]
    assert created.id in text and created.group_id == "g1"
    assert sched.get_due_tasks() == []
    assert "No scheduled" in await call(sched, "list", group="other")
    assert "No task" in await call(sched, "cancel", group="other", task_id=created.id)
    assert sched.get_task(created.id)
    assert "cancelled" in await call(sched, "cancel", task_id=created.id)
    clock[0] += 240
    assert sched.get_due_tasks() == []
    assert TaskScheduler(sched._tasks_file).list_tasks() == []


@pytest.mark.parametrize("args", [{}, {"run_at": "2026-10-12T09:24:00"},
    {"run_at": "2026-10-12T09:19:00+08:00"}, {"run_at": "invalid"},
    {"cron": "* * * * *"}, {"interval_seconds": 60, "group_id": "other"},
    {"interval_seconds": 60, "prompt": " "},
    {"run_at": "2026-10-12T09:24:00+08:00", "cron": "* * * * *", "timezone": "UTC"}])
async def test_tool_errors_are_explicit(sched, args):
    text = await call(sched, "schedule", **{"prompt": "report", **args})
    assert text.startswith("Error:")
    assert sched.list_tasks() == []


async def test_tool_requires_live_service_and_owner():
    tool = create_task_tool()
    for ctx in ({}, {"group_id": "g1", "data_dir": "/must/not/write"}):
        result = await tool.execute("x", {"action": "schedule", "prompt": "check", "interval_seconds": 60}, ctx=ctx)
        assert "running scheduler" in result.content[0].text


def test_persisted_running_is_not_blindly_replayed(sched, clock):
    sched.add_task(task(TaskSchedule(run_at=NOW)))
    sched.start_run("t1")
    reloaded = TaskScheduler(sched._tasks_file, clock=lambda: clock[0])
    interrupted = reloaded.get_task("t1")
    assert interrupted.status == "interrupted" and interrupted.delivery_status == "unknown"
    assert interrupted.last_run is None and not interrupted.enabled
    assert reloaded.get_due_tasks() == []


def test_legacy_tool_envelope_migrates_but_ambiguous_cron_is_disabled(tmp_path):
    path = tmp_path / "tasks.json"
    legacy = asdict(task(TaskSchedule(cron="24 9 12 10 *"), created_at=NOW))
    path.write_text(json.dumps({"tasks": [legacy]}))
    service = TaskScheduler(path)
    assert service.get_due_tasks() == []
    assert "timezone" in service.get_task("t1").last_error
    service.add_task(task(TaskSchedule(interval_seconds=60), id="t2"))
    assert set(json.loads(path.read_text())) == {"t1", "t2"}


def test_corrupt_store_is_not_silently_erased(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text("{broken")
    with pytest.raises(ValueError, match="Invalid task store"):
        TaskScheduler(path)
    assert path.read_text() == "{broken"


def test_failed_save_does_not_publish_task(sched, monkeypatch):
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(type(sched._tasks_file), "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        sched.add_task(task(TaskSchedule(run_at=NOW)))
    assert sched.list_tasks() == []


def test_missing_dependency_on_reload_disables_and_persists_error(sched, monkeypatch):
    sched.add_task(task(TaskSchedule(cron="* * * * *", timezone="UTC")))
    monkeypatch.setitem(sys.modules, "croniter", None)
    reloaded = TaskScheduler(sched._tasks_file)
    saved = reloaded.get_task("t1")
    assert not saved.enabled and "croniter" in saved.last_error
    assert json.loads(sched._tasks_file.read_text())["t1"]["status"] == "failed"
    assert TaskScheduler(sched._tasks_file).get_task("t1") == saved


def test_late_recurring_tick_coalesces_occurrences(sched, clock):
    sched.add_task(task(TaskSchedule(cron="* * * * *", timezone="UTC")))
    clock[0] += 600
    assert len(sched.get_due_tasks()) == 1
    sched.start_run("t1")
    clock[0] += 75  # A slow run crosses another boundary.
    sched.finish_run("t1", error="failed", delivered=False)
    saved = sched.get_task("t1")
    assert saved.next_run > clock[0] and saved.next_run <= clock[0] + 60
    assert saved.last_run is None and sched.get_due_tasks() == []
