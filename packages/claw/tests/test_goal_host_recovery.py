"""Group lifetime and original actual execution survive conversation rotation."""

from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.types import Group
from xdog.goal.host import OperationContext
from xdog.goal.models import report_digest

from packages.claw.tests.test_goal_host import activate, application, submitted


async def test_idle_rotation_preserves_group_goal_and_original_terminal_child_without_replay(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        host = runtime.goal_host
        original_owner = host.owner
        old_session = runtime._active_session
        goal = await submitted(host, old_session)
        task = goal.tasks[0]
        attempt = task.attempts[-1]
        count = len([role for role, _ in model.calls if role != "parent"])
        runtime.reset_session()
        await activate(app)
        assert runtime._active_session is not old_session
        assert runtime.goal_host is host and host.owner == original_owner
        assert (await host.child_status(attempt.worker)).state == "succeeded"
        goal = await host.service.accept_task(
            goal.id, task.id, attempt.id, 1, report_digest(attempt.reports[-1]), "Read saved report"
        )
        assert goal.tasks[0].state == "accepted"
        assert len([role for role, _ in model.calls if role != "parent"]) == count


async def test_real_running_child_blocks_rotation_without_unfinished_goal_policy(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        host = runtime.goal_host
        model.worker_gate = threading.Event()
        goal = await host.service.create("Running", "Inspect", ("Result exists",), context=OperationContext(True))
        goal = await host.service.add_task(goal.id, "Wait")
        goal = await host.service.assign(goal.id, goal.tasks[0].id, "Wait")
        with pytest.raises(RuntimeError, match="Child work"):
            runtime.reset_session()
        model.worker_gate.set()
        await runtime._active_session.children.supervisor.control("wait", goal.tasks[0].attempts[-1].worker.id)


async def test_another_group_never_reads_or_accepts_this_group_goal(tmp_path):
    async with application(tmp_path) as (app, runtime, _):
        app.register_group(Group(id="other", workspace=str(tmp_path / "other")))
        await activate(app)
        host = runtime.goal_host
        goal = await host.service.create("Private", "Only original group", ("test",), context=OperationContext(True))
        await activate(app, "other")
        other = app._runtimes["other"].goal_host
        assert other.owner != host.owner and await other.service.list() == ()
        with pytest.raises(ValueError, match="Unknown Goal"):
            await other.service.status(goal.id)


async def test_legacy_goal_truth_is_refused_byte_preserving_without_empty_replacement(tmp_path):
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.runtime.orchestrator import Orchestrator

    from packages.claw.tests.test_goal_host import Model

    model = Model()
    app = Orchestrator(
        ClawConfig(data_dir=str(tmp_path / "data")), stream_fn=model, data_dir=tmp_path / "data"
    )
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace")))
    legacy = tmp_path / "data/groups/main/goals.json"
    original = b'{"legacy":"do not upgrade"}\n'
    legacy.write_bytes(original)
    try:
        with pytest.raises(ValueError, match="Legacy|unsupported"):
            await app.start()
        assert legacy.read_bytes() == original and not (tmp_path / "data/groups/main/goals").exists()
        assert not model.calls
    finally:
        await app.stop()


async def test_process_lifetime_reopen_restores_original_terminal_reference_without_worker_replay(tmp_path):
    async with application(tmp_path) as (app, runtime, _):
        await activate(app)
        original_owner = runtime.goal_host.owner
        goal = await submitted(runtime.goal_host, runtime._active_session)
        task = goal.tasks[0]
        attempt = task.attempts[-1]
        reference = attempt.worker
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        host = runtime.goal_host
        assert host.owner == original_owner
        assert (await host.child_status(reference)).state == "succeeded"
        goal = await host.service.accept_task(
            goal.id, task.id, attempt.id, 1, report_digest(attempt.reports[-1]), "Inspect original report"
        )
        assert goal.tasks[0].state == "accepted"
        assert goal.tasks[0].attempts[-1].worker == reference
        assert not runtime._active_session.children.supervisor.active
        assert all(role == "parent" for role, _ in model.calls)


@pytest.mark.parametrize("daily,idle", [(True, False), (False, True)])
async def test_unfinished_goal_never_vetoes_idle_automatic_context_rotation(tmp_path, monkeypatch, daily, idle):
    async with application(tmp_path) as (app, runtime, _):
        await activate(app)
        host = runtime.goal_host
        goal = await host.service.create("Unfinished", "Wait", ("test",), context=OperationContext(True))
        old = runtime._active_session
        monkeypatch.setattr(runtime.transcript_store, "needs_daily_reset", lambda *args: daily)
        monkeypatch.setattr(runtime.transcript_store, "needs_idle_reset", lambda *args: idle)
        fresh = runtime.get_or_create_session()
        assert fresh is not old
        assert (await host.service.status(goal.id)).status == "active"
        assert runtime.goal_host is host


async def test_goal_state_notification_and_observer_controls_are_independent(tmp_path):
    import tempfile
    from pathlib import Path

    from xdog.claw.acp.server import ClawAcpServer
    from xdog.claw.core.observers.manager import ManagedObservers

    from packages.claw.tests.eyes_helpers import ManualClock, scheduler

    async with application(tmp_path) as (app, runtime, _):
        await activate(app)
        host = runtime.goal_host
        clock = ManualClock()
        with tempfile.TemporaryDirectory(prefix="goal-eyes-") as directory:
            server = await ClawAcpServer.start(app, Path(directory) / "acp.sock")
            observers = ManagedObservers(app, server.path, clocks=clock.clocks())
            try:
                await observers.start()
                source = await observers.create("main", "independent-source", scheduler(every_seconds=60), "Notify me")
                goal = await host.service.create(
                    "Independent", "Do not cancel source", ("test",), context=OperationContext(True)
                )
                await host.service.cancel_notifications(goal.id)
                await host.service.abandon(goal.id, "Stop this Goal only")
                assert (await observers.status("main", source["id"]))["enabled"]
                other = await host.service.create("Other", "Still active", ("test",), context=OperationContext(True))
                await observers.cancel("main", source["id"])
                assert (await host.service.status(other.id)).status == "active"
                assert (await host.service.status(other.id)).notifications_enabled
                assert (await host.service.status(goal.id)).status == "abandoned"
            finally:
                await observers.close()
                await server.close()


async def test_relative_configured_workspace_binds_canonical_group_goal_owner(tmp_path):
    import os
    from pathlib import Path

    from xdog.claw.config import ClawConfig
    from xdog.claw.core.runtime.orchestrator import Orchestrator

    from packages.claw.tests.test_goal_host import Model

    workspace = tmp_path / "relative-workspace"
    config = ClawConfig(data_dir=str(tmp_path / "data"))
    app = Orchestrator(config, stream_fn=Model(), data_dir=tmp_path / "data")
    app.register_group(Group(id="main", workspace=os.path.relpath(workspace, Path.cwd())))
    try:
        await app.start()
        await activate(app)
        runtime = app._runtimes["main"]
        assert runtime.goal_host.owner.workspace == workspace
        assert runtime.workspace_dir == workspace
        assert runtime._active_session.agent.tool_context["fs_workspace"] == str(workspace)
    finally:
        await app.stop()


async def test_installed_application_owner_opens_capability_without_constructing_or_polling_agent(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        assert runtime.goal_host is not None
        assert runtime._active_session is None and not model.calls
        goal = await runtime.goal_host.service.create(
            "Active only", "No wake-up obligation", ("test",), context=OperationContext(True)
        )
        assert goal.status == "active"
        await runtime.goal_host.flush()
        assert runtime._active_session is None and not model.calls


async def test_restart_drains_persisted_notice_without_waiting_for_tui_or_another_human_input(tmp_path):
    async with application(tmp_path) as (app, runtime, _):
        await activate(app)
        host = runtime.goal_host
        original = host.inputs.submit

        async def offline(envelope, **kwargs):
            if envelope.message.source == "goal":
                raise ConnectionError("Original port unavailable before intake")
            return await original(envelope, **kwargs)

        host.inputs.submit = offline
        goal = await submitted(host, runtime._active_session)
        assert goal.tasks[0].state == "submitted"
        assert any(notice.status in {"pending", "unknown"} for notice in await host.service.store.notices())
        # Keep the port offline until actual host shutdown has settled jobs.
    async with application(tmp_path) as (app, runtime, model):
        host = runtime.goal_host
        assert host is not None
        scope = InputScope("main", runtime.workspace_dir)
        async with asyncio.timeout(5):
            while True:
                notices = [
                    row for row in await host.inputs.store.records(scope) if row.envelope.message.source == "goal"
                ]
                if notices and all(row.status == "succeeded" for row in notices):
                    break
                await asyncio.sleep(0.005)
        assert model.calls and all(role == "parent" for role, _ in model.calls)
        assert not runtime._active_session.children.supervisor.active


@pytest.mark.parametrize("group_ids", [("main",), ("main", "later")])
async def test_shutdown_joins_admitted_capability_open_before_releasing_goal_lease(tmp_path, monkeypatch, group_ids):
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.goal.store import GoalStore

    from packages.claw.tests.test_goal_host import Model

    original = GoalStore.open
    entered, release = asyncio.Event(), asyncio.Event()
    opened = []

    async def delayed(path, owner):
        store = await original(path, owner)
        opened.append(store)
        entered.set()
        await release.wait()
        return store

    monkeypatch.setattr(GoalStore, "open", delayed)
    model = Model()
    app = Orchestrator(
        ClawConfig(data_dir=str(tmp_path / "data")), stream_fn=model, data_dir=tmp_path / "data"
    )
    for group_id in group_ids:
        app.register_group(Group(id=group_id, workspace=str(tmp_path / group_id)))
    opening = asyncio.create_task(app.start())
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        closing = asyncio.create_task(app.stop())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(closing), 0.1)
    finally:
        release.set()
        await asyncio.gather(opening, return_exceptions=True)
        if closing is not None:
            await closing
        else:
            await app.stop()
    assert len(opened) == 1  # Shutdown fences later group capability admissions.
    assert not (tmp_path / "data" / "groups" / "later" / "goals").exists()
    # Inspect the real independently leased store, not a mocked close callback.
    reopened = await original(opened[0].path, opened[0].owner)
    await reopened.close()
    assert not model.calls
