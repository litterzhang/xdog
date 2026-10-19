"""Cancellation must settle readonly worker work before releasing actual group operations."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group


@pytest.mark.parametrize("kind", ["skill", "navigation", "access"])
async def test_cancelled_auxiliary_read_keeps_actual_group_owner_until_worker_exit(tmp_path, monkeypatch, kind):
    def no_model(*args):
        raise AssertionError("Readonly metadata/skill/navigation must not wake a model")

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=no_model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
    runtime = app._runtimes["main"]
    directory = runtime.workspace_dir / "skills" / "focus"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: Focus\ndescription: Actual IO\n---\nKeep the original owner.")
    entered, release, exited = (threading.Event() for _ in range(3))
    work = None
    try:
        await app.start()
        session = await runtime.prepare_session(allow_reset=False)
        if kind == "skill":
            original = runtime.skill_manager.load_skill

            def actual_read(*args):
                value = original(*args)
                entered.set()
                try:
                    assert release.wait(5)
                    return value
                finally:
                    exited.set()

            monkeypatch.setattr(runtime.skill_manager, "load_skill", actual_read)
            operation = app.skills_control("main", session.meta.session_id, "load", "focus")
        elif kind == "navigation":
            original = runtime.navigation_entries

            def actual_read(*args):
                value = original(*args)
                entered.set()
                try:
                    assert release.wait(5)
                    return value
                finally:
                    exited.set()

            monkeypatch.setattr(runtime, "navigation_entries", actual_read)
            operation = app.navigation_entries("main", f"tree|{session.meta.session_id}|")
        else:
            runtime._drop_session()  # Actual current metadata, no cached Agent/access shortcut.
            original = runtime.transcript_store.get_access
            owner = threading.get_ident()

            def actual_read(*args):
                value = original(*args)
                entered.set()
                try:
                    if threading.get_ident() != owner:
                        assert release.wait(5)
                    return value
                finally:
                    exited.set()

            monkeypatch.setattr(runtime.transcript_store, "get_access", actual_read)
            operation = app.manage_access("main", "status", session.meta.session_id)
        reservation = app.try_reserve_group("main")
        assert reservation is not None
        work = app.start_group_operation(reservation, operation)
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            work.cancel()
            await asyncio.sleep(0)
        assert not work.done() and app.is_group_running("main") and not exited.is_set(), (
            "Cancelled metadata read released its real group before the actual worker settled"
        )
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert exited.is_set() and not app.is_group_running("main")
        assert session.agent.active_skills == ()
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await app.stop()
