"""Real context retirement must release closed generic extension owners."""
from __future__ import annotations

import gc
import weakref

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


@pytest.mark.parametrize("installed", [False, True])
async def test_context_rotation_releases_settled_extension_owner_before_application_shutdown(
    tmp_path, monkeypatch, installed,
):
    if not installed:
        monkeypatch.setattr("xdog.claw.core.runtime.group.find_spec", lambda _name: None)
    calls = []

    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="actual ordinary reply"),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
    runtime = app._runtimes["main"]
    previous = []
    try:
        await app.start()
        for number in range(3):
            await app.route_message(UserInput(group_id="main", content=f"ordinary {number}"))
            extensions = runtime._active_session.extensions
            previous.append(weakref.ref(extensions))
            identifier = runtime._active_session.meta.session_id
            await app.navigate_session("main", "new", "", "", -1, identifier)
            # Settle the actual old owner, not a fake close or a timed sleep.
            await extensions.aclose()
            del extensions
        # A live successor rebinds optional capabilities away from the last parent.
        await app.route_message(UserInput(group_id="main", content="fresh live parent"))
        gc.collect()
        assert all(reference() is None for reference in previous), (
            "Retired generic extension owners retain old conversations until application shutdown"
        )
        assert len(calls) == 4
    finally:
        await app.stop()


@pytest.mark.parametrize("retired", [False, True])
async def test_close_cancellation_joins_actual_extension_persistence_before_releasing_owner(
    tmp_path, monkeypatch, retired,
):
    import asyncio
    import json
    import threading

    from packages.claw.tests.test_active_tool_persistence import close, create

    runtime, calls = create(tmp_path, extensions=("contextual",))
    session = runtime.get_or_create_session(allow_reset=False)
    extension = session.extensions
    identifier = session.meta.session_id
    entered, release, exited, joining = (threading.Event() for _ in range(4))
    original = runtime.transcript_store._atomic
    commits = []

    def persist(path, value, **kwargs):
        result = original(path, value, **kwargs)
        if path.name.endswith(".extensions.json"):
            commits.append(path)
            entered.set()
            try:
                assert release.wait(10)
            finally:
                exited.set()
        return result

    work = closing = None
    try:
        await session.initialize_extensions()
        thread = extension._manager._thread
        assert thread is not None
        join = thread.join

        def actual_join(*args, **kwargs):
            joining.set()
            return join(*args, **kwargs)

        monkeypatch.setattr(runtime.transcript_store, "_atomic", persist)
        monkeypatch.setattr(thread, "join", actual_join)
        work = asyncio.create_task(extension.command("ext-contextual-remember", "actual durable note"))
        assert await asyncio.to_thread(entered.wait, 3)
        if retired:
            runtime._drop_session()
            gc.collect()
            assert extension in runtime._retired_extensions and thread.is_alive()
        closing = asyncio.create_task(extension.aclose())
        assert await asyncio.to_thread(joining.wait, 3)
        for _ in range(3):
            closing.cancel()
            await asyncio.sleep(0)
        assert not closing.done() and not exited.is_set() and thread.is_alive(), (
            "Close released its actual owner while the durable persistence worker was still settling"
        )
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert exited.is_set() and not thread.is_alive()
        assert len(commits) == 1 and not calls
        assert json.loads(commits[0].read_text())["contextual"][0]["data"] == {"text": "actual durable note"}
        assert runtime.transcript_store.get_extension_entries(identifier)["contextual"][0]["data"] == {
            "text": "actual durable note"
        }
    finally:
        release.set()
        await asyncio.gather(*(task for task in (work, closing) if task is not None), return_exceptions=True)
        await extension.aclose()
        await close(runtime)


async def test_repeated_close_reports_actual_cleanup_error_without_replaying_the_close(tmp_path, monkeypatch):
    from packages.claw.tests.test_active_tool_persistence import create

    runtime, _ = create(tmp_path, extensions=("contextual",))
    session = runtime.get_or_create_session(allow_reset=False)
    extension = session.extensions
    completed = []
    try:
        await session.initialize_extensions()
        client = extension._manager._clients["contextual"]
        original = client.close

        async def actual_close_then_error():
            await original()  # The actual stdio worker/process is closed, not a fake close.
            completed.append(True)
            raise OSError("actual cleanup boundary failed after reaping worker")

        monkeypatch.setattr(client, "close", actual_close_then_error)
        extension.close()
        for _ in range(2):
            with pytest.raises(OSError, match="actual cleanup boundary"):
                await extension.aclose()
        assert completed == [True]
        assert client.process is None and not extension._manager._thread.is_alive()
    finally:
        try:
            await extension.aclose()
        except OSError:
            pass
        await session.children.supervisor.aclose()
        session.dispose()
        await runtime.mcp.aclose()
        if runtime.memory is not None:
            await runtime.memory.close()
