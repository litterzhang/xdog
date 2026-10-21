"""Actual Coding owner, packaged Python display and the shared generic renderer."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.agent.mcp.stdio import McpError
from xdog.skills.goal.host import OperationContext
from xdog.tui.components.extension_view import ExtensionView

from packages.coding.tests.test_child_agents import create
from packages.coding.tests.test_goal_lifecycle import dispose, owned


def session_with_display(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({
        "extensions": ["goal_ui"], "extension_dir": str(tmp_path / "extensions")}))
    return create(tmp_path)


async def test_actual_coding_goal_owner_grants_headless_display_and_reading_never_calls_a_model(tmp_path):
    session, calls = session_with_display(tmp_path)
    try:
        await session.send_message("Ready")
        host, controller = session.capabilities.get("goal"), session._sdk_controller
        goal = await owned(controller, lambda: host.service.create(
            "Coding private goal", "Read actual Coding state", ("Check",), context=OperationContext(True)))
        before = len(calls)
        result = await owned(controller, lambda: session.extensions.command("goals", ""))
        assert goal.id in str(result) and "Coding private goal" in str(result)
        assert "unavailable" not in str(result).lower()
        rendered = ExtensionView(lambda: session.extensions.snapshot()).render(80)
        assert rendered and any("Goals: 1" in line for line in rendered)
        assert len(calls) == before and not session.children.runtime.active
        assert (await owned(controller, lambda: host.service.status(goal.id)))["current"]["id"] == goal.id
        await owned(controller, lambda: session.extensions.manage("unload", "goal_ui"))
        assert not session.extensions.snapshot()["ui"]
        assert (await owned(controller, lambda: host.service.status(goal.id)))["current"]["status"] == "active"
    finally:
        await dispose(session)


async def test_coding_display_rotation_cannot_read_or_render_the_original_exact_owner(tmp_path):
    session, _ = session_with_display(tmp_path)
    try:
        await session.send_message("Ready")
        host, controller = session.capabilities.get("goal"), session._sdk_controller
        goal = await owned(controller, lambda: host.service.create(
            "Never transfer this", "Original exact session", ("Check",), context=OperationContext(True)))
        await owned(controller, lambda: session.extensions.command("goals", ""))
        previous = session.extensions
        await asyncio.to_thread(session.new_session)
        assert session.extensions is not previous and previous._closed
        assert await previous.views.read("goal_ui", "goals", {}) is None
        assert "Never transfer this" not in str(session.extensions.snapshot())
        await session.send_message("New exact owner")
        current = session.capabilities.get("goal")
        assert current.owner != host.service.store.owner
        assert await owned(session._sdk_controller, current.service.current) is None
        result = await owned(session._sdk_controller, lambda: session.extensions.command("goals", ""))
        assert "no goals" in str(result).lower() and goal.id not in str(result)
    finally:
        await dispose(session)


async def test_coding_actual_branch_gets_a_new_view_not_the_original_goal(tmp_path):
    session, _ = session_with_display(tmp_path)
    try:
        await session.send_message("Ready")
        controller, host = session._sdk_controller, session.capabilities.get("goal")
        branch = await asyncio.to_thread(session.create_branch, label="saved")
        goal = await owned(controller, lambda: host.service.create(
            "Branch private", "Do not transfer", ("Check",), context=OperationContext(True)))
        await owned(controller, lambda: session.extensions.command("goals", ""))
        previous = session.extensions
        assert await asyncio.to_thread(session.restore_branch, branch)
        assert previous._closed and session.extensions is not previous
        assert await previous.views.read("goal_ui", "goals", {}) is None
        await session.send_message("On the selected branch")
        current = session.capabilities.get("goal")
        assert current.owner != host.service.store.owner
        result = await owned(session._sdk_controller, lambda: session.extensions.command("goals", ""))
        assert "no goals" in str(result).lower() and goal.id not in str(result)
    finally:
        await dispose(session)


@pytest.mark.parametrize("action", ["unload", "crash"])
async def test_main_task_records_and_completion_survive_display_loss_without_children(tmp_path, action):
    session, calls = session_with_display(tmp_path)
    try:
        await session.send_message("Ready")
        host, controller = session.capabilities.get("goal"), session._sdk_controller
        goal = await owned(controller, lambda: host.service.create(
            "Main output", "Read original output", ("Result exists",), context=OperationContext(True)))
        await owned(controller, lambda: host.service.task({"id": "work", "title": "Main task", "expected_version": 0}))
        if action == "unload":
            await owned(controller, lambda: session.extensions.manage("unload", "goal_ui"))
        else:
            async def crash():
                process = session.extensions._manager._clients["goal_ui"].process
                assert process is not None
                process.kill()
                await process.wait()
            await session.extensions._manager._bridge(crash())
            with pytest.raises((ValueError, McpError)):
                await owned(controller, lambda: session.extensions.command("goals", ""))
        before = len(calls)
        await owned(controller, lambda: host.service.task({"id": "work", "expected_version": 1,
            "status": "completed", "summary": "Main verified"}))
        await owned(controller, lambda: host.service.change("complete", summary="Main verified the task"))
        status = await owned(controller, lambda: host.service.status(goal.id))
        assert status["current"]["status"] == "completed" and status["tasks"][0]["status"] == "completed"
        assert len(calls) == before and not session.children.runtime._children
        assert not session.extensions.snapshot()["ui"]
    finally:
        await dispose(session)


async def test_coding_shutdown_keeps_goal_lease_until_actual_display_disk_read_settles(tmp_path, monkeypatch):
    from xdog.skills.goal.store import GoalStore

    session, _ = session_with_display(tmp_path)
    entered, release = threading.Event(), threading.Event()
    reading = closing = None
    try:
        await session.send_message("Ready")
        host, controller = session.capabilities.get("goal"), session._sdk_controller
        await owned(controller, host.flush)
        async with asyncio.timeout(5):
            while session.extensions._view_jobs or host._display._reads:
                await asyncio.sleep(.005)
        database = host.service.store.database
        original = database.transaction

        def delayed(operation):
            result = original(operation)  # The real SQLite read/connection cleanup has run.
            entered.set()
            release.wait()
            return result

        monkeypatch.setattr(database, "transaction", delayed)
        reading = asyncio.create_task(session.extensions.command("goals", ""))
        assert await asyncio.to_thread(entered.wait, 5)
        path, owner = host.service.store.path, host.service.store.owner
        closing = asyncio.create_task(dispose(session))
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
            await dispose(session)
        if reading is not None:
            await asyncio.gather(reading, return_exceptions=True)
    reopened = await GoalStore.open(path, owner)
    await reopened.close()
