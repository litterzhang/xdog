"""Actual Claw group-owned Goal data is displayed by the same packaged worker."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.agent.mcp.stdio import McpError
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group
from xdog.skills.goal.host import OperationContext
from xdog.tui.components.extension_view import ExtensionView

from packages.claw.tests.test_goal_host import Model, activate


def application_with_display(tmp_path):
    model = Model()
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data"), extensions=("goal_ui",),
        extension_dir=str(tmp_path / "extensions")), stream_fn=model, data_dir=tmp_path / "data")
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace")))
    return app, app._runtimes["main"], model


async def test_actual_group_goal_grants_headless_display_and_never_starts_work_for_queries(tmp_path):
    app, runtime, model = application_with_display(tmp_path)
    try:
        await app.start()
        await activate(app)
        host, session = runtime.goal_host, runtime._active_session
        goal = await host.service.create("Claw private goal", "Actual group state", ("Check",),
                                         context=OperationContext(True))
        before = len(model.calls)
        result = await session.extensions.command("ext-goal_ui-goals", "")
        assert goal.id in str(result) and "Claw private goal" in str(result)
        assert "unavailable" not in str(result).lower()
        rendered = ExtensionView(lambda: session.extensions.snapshot()).render(80)
        assert rendered and any("Goals: 1" in line for line in rendered)
        assert len(model.calls) == before and not session.children.supervisor.active
        await session.extensions.manage("unload", "goal_ui")
        assert not session.extensions.snapshot()["ui"]
        assert (await host.service.status(goal.id)).status == "active"
    finally:
        await app.stop()


async def test_claw_display_rebinds_current_context_without_moving_the_group_goal(tmp_path):
    app, runtime, _ = application_with_display(tmp_path)
    try:
        await app.start()
        await activate(app)
        host, old_session = runtime.goal_host, runtime._active_session
        goal = await host.service.create("Stable group goal", "Across chat rotation", ("Check",),
                                         context=OperationContext(True))
        await old_session.extensions.command("ext-goal_ui-goals", "")
        runtime.reset_session()
        await activate(app)
        fresh = runtime._active_session
        assert fresh is not old_session and old_session.extensions._closed
        assert await old_session.extensions.views.read("goal_ui", "goals", {}) is None
        assert runtime.goal_host is host
        result = await fresh.extensions.command("ext-goal_ui-goals", "")
        assert goal.id in str(result) and "Stable group goal" in str(result)
        assert (await host.service.status(goal.id)).status == "active"
    finally:
        await app.stop()


@pytest.mark.parametrize("action", ["unload", "crash"])
async def test_display_loss_never_cancels_or_blocks_real_group_worker_reports_and_notices(tmp_path, action):
    app, runtime, model = application_with_display(tmp_path)
    try:
        await app.start()
        await activate(app)
        host, session = runtime.goal_host, runtime._active_session
        runtime.workspace_dir.joinpath("result.txt").write_text("actual worker output")
        model.worker_gate = threading.Event()
        goal = await host.service.create("Background output", "Read actual result", ("Result exists",),
                                         context=OperationContext(True))
        goal = await host.service.add_task(goal.id, "Read and report")
        goal = await host.service.assign(goal.id, goal.tasks[0].id, "Read result.txt and submit evidence")
        reference = goal.tasks[0].attempts[-1].worker
        async with asyncio.timeout(5):
            while not any(role == "worker" for role, _ in model.calls):
                await asyncio.sleep(.005)
        child_extensions = session.children.extensions.owners[reference.id]
        assert await child_extensions.views.read("goal_ui", "goals", {}) is None
        child_output = await child_extensions.command("ext-goal_ui-goals", "")
        assert "unavailable" in str(child_output).lower() and goal.id not in str(child_output)
        if action == "unload":
            await session.extensions.manage("unload", "goal_ui")
        else:
            async def crash():
                process = session.extensions._manager._clients["goal_ui"].process
                assert process is not None
                process.kill()
                await process.wait()
            await session.extensions._manager._bridge(crash())
            with pytest.raises((ValueError, McpError)):
                await session.extensions.command("ext-goal_ui-goals", "")
        assert session.children.supervisor.active
        model.worker_gate.set()
        await session.children.supervisor.control("wait", reference.id)
        await host.flush()
        actual = await host.service.status(goal.id)
        assert actual.tasks[0].state == "submitted" and actual.status == "active"
        from xdog.claw.core.inputs.models import InputScope
        scope = InputScope("main", runtime.workspace_dir)
        async with asyncio.timeout(5):
            while True:
                rows = [row for row in await host.inputs.store.records(scope) if row.envelope.message.source == "goal"]
                if rows and all(row.status == "succeeded" for row in rows):
                    break
                await asyncio.sleep(.005)
        assert not session.extensions.snapshot()["ui"]
    finally:
        if model.worker_gate is not None:
            model.worker_gate.set()
        await app.stop()


async def test_claw_shutdown_keeps_goal_lease_until_actual_display_disk_read_settles(tmp_path, monkeypatch):
    from xdog.skills.goal.store import GoalStore

    app, runtime, _ = application_with_display(tmp_path)
    entered, release = threading.Event(), threading.Event()
    reading = closing = None
    try:
        await app.start()
        await activate(app)
        host, session = runtime.goal_host, runtime._active_session
        await host.flush()
        async with asyncio.timeout(5):
            while session.extensions._view_jobs or host._display._reads:
                await asyncio.sleep(.005)
        database = host.service.store._database
        original = database.transaction

        def delayed(operation):
            result = original(operation)
            entered.set()
            release.wait()
            return result

        monkeypatch.setattr(database, "transaction", delayed)
        reading = asyncio.create_task(session.extensions.command("ext-goal_ui-goals", ""))
        assert await asyncio.to_thread(entered.wait, 5)
        path, owner = host.service.store.path, host.service.store.owner
        closing = asyncio.create_task(app.stop())
        async with asyncio.timeout(5):
            while not host._closing:
                await asyncio.sleep(.005)
        assert not closing.done()
        with pytest.raises(BlockingIOError):
            await GoalStore.open(path, owner)
    finally:
        release.set()
        if closing is not None:
            await asyncio.wait_for(closing, 5)
        else:
            await app.stop()
        if reading is not None:
            await asyncio.gather(reading, return_exceptions=True)
    reopened = await GoalStore.open(path, owner)
    await reopened.close()


async def test_rotation_settles_and_releases_retired_display_bindings(tmp_path):
    import gc
    import weakref

    app, runtime, _ = application_with_display(tmp_path)
    try:
        await app.start()
        await activate(app)
        host = runtime.goal_host
        original = weakref.ref(host._display)
        previous_extensions = runtime._active_session.extensions
        runtime.reset_session()
        await activate(app)
        await previous_extensions.aclose()
        await host.flush()
        gc.collect()
        assert original() is None, "A closed view must not retain an old conversation until application shutdown"
        assert host._display is not None
    finally:
        await app.stop()
