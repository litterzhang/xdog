"""Preparation IO belongs to workers; actual Agent execution keeps its owner loop."""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput

from packages.claw.tests.test_gateway_inputs import gateway as gateway


@pytest.mark.parametrize("mode", ["cold", "restore", "automatic-reset"])
async def test_actual_cold_context_metadata_fsync_runs_off_the_event_loop(tmp_path, monkeypatch, mode):
    owner = threading.get_ident()
    capture, gate = threading.Event(), threading.Event()
    entered, release = asyncio.Event(), asyncio.Event()
    observed, calls = [], []
    actual_fsync = os.fsync

    def fsync(descriptor):
        path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        actual_fsync(descriptor)
        if capture.is_set() and path.is_relative_to(tmp_path):
            observed.append((path, threading.get_ident()))

    def model(*args):
        assert threading.get_ident() == owner, "Do not move Agent execution to another loop to hide blocking IO"
        if (isinstance(args[1].system_prompt, str)
                and args[1].system_prompt.startswith('You are a conversation summarizer.')):
            # Rotation now has a tool-free handoff request before persistence.
            # The original probe gates the actual incoming user turn, not that summary.
            return EventStream.empty(AssistantMessage(content=(TextContent(text='Original context retained.'),)))
        calls.append(args)
        if gate.is_set():
            capture.clear()  # Preparation only; durable turn commit is a separate required gate.
            entered.set()
        message = AssistantMessage(content=(TextContent(text="actual owned preparation"),))

        async def events():
            if gate.is_set():
                await release.wait()
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)

    def application():
        app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
        app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
        return app

    app = application()
    previous = None
    work = None
    try:
        await app.start()
        if mode != "cold":
            await app.route_message(UserInput(group_id="main", content="seed context"))
            runtime = app._runtimes["main"]
            previous = runtime._active_session.meta.session_id
            if mode == "restore":
                from xdog.agent.access import AccessState
                runtime.transcript_store.save_access(previous, AccessState(True, 7).to_dict())
                runtime.transcript_store.save_active_tools(previous, ())
                await app.stop()
                app = application()
                await app.start()
            else:
                import datetime
                old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=4)
                runtime.transcript_store._index["main"]["created_at"] = old.isoformat()
                runtime.transcript_store._save_index()
        calls.clear()
        monkeypatch.setattr(os, "fsync", fsync)
        gate.set()
        capture.set()
        work = asyncio.create_task(app.route_message(UserInput(group_id="main", content="actual cold input")))
        await asyncio.wait_for(entered.wait(), 3)
        if mode != "restore":
            assert observed, "Deleting fsync is not an off-loop implementation"
        assert all(thread != owner for _path, thread in observed), (
            "Actual current-context preparation fsync still blocks the Agent/event-loop owner", observed
        )
        session = app._runtimes["main"]._active_session
        assert session._host_loop is asyncio.get_running_loop()
        if mode == "restore":
            assert session.meta.session_id == previous and session.access.state.read_only
            assert session.agent.active_tool_selection == ()
            from xdog.claw.core.persistence.transcript_convert import entry_text, messages_to_transcript
            rows = messages_to_transcript(list(session.agent.messages))
            assert any(entry_text(row) == "seed context" for row in rows)
        elif mode == "automatic-reset":
            assert session.meta.session_id != previous
        release.set()
        result = await work
        assert result is not None and result.response_text == "actual owned preparation"
        assert len(calls) == 1
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await app.stop()


async def test_workspace_skill_and_saved_context_reads_happen_before_owner_loop_adoption(tmp_path, monkeypatch):
    import builtins
    import io

    owner = threading.get_ident()
    capture = threading.Event()
    reads = []
    calls = []
    actual_open, actual_io_open = builtins.open, io.open

    def observe(actual, file, *args, **kwargs):
        if capture.is_set() and isinstance(file, (str, os.PathLike)):
            path = Path(file)
            if path.is_relative_to(tmp_path):
                reads.append((path, threading.get_ident()))
        return actual(file, *args, **kwargs)

    def model(*args):
        capture.clear()
        calls.append(args)
        assert threading.get_ident() == owner
        from xdog.ai.types import system_prompt_text
        assert "memory from actual workspace" in system_prompt_text(args[1].system_prompt)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="actual read probe"),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
    workspace = app._runtimes["main"].workspace_dir
    (workspace / "MEMORY.md").write_text("memory from actual workspace")
    skill = workspace / "skills" / "focus"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: Focus\ndescription: Actual local skill index\n---\nCheck invariants.")
    try:
        await app.start()
        monkeypatch.setattr(builtins, "open", lambda file, *a, **kw: observe(actual_open, file, *a, **kw))
        monkeypatch.setattr(io, "open", lambda file, *a, **kw: observe(actual_io_open, file, *a, **kw))
        capture.set()
        result = await app.route_message(UserInput(group_id="main", content="actual reads"))
        assert result is not None and result.response_text == "actual read probe" and len(calls) == 1
        assert reads and any(path.name == "SKILL.md" for path, _thread in reads)
        assert all(thread != owner for _path, thread in reads), (
            "Actual workspace/skill/history reads still block the Agent owner", reads
        )
    finally:
        capture.clear()
        await app.stop()


@pytest.mark.parametrize("withdraw", [False, True])
async def test_held_real_context_commit_preserves_admission_and_other_group_progress(tmp_path, monkeypatch, withdraw):
    from xdog.claw.core.inputs.models import InputScope

    owner = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def model(_model, context, options):
        assert threading.get_ident() == owner
        content = context.messages[-1].content
        calls.append(content if isinstance(content, str) else "".join(part.text for part in content))
        return EventStream.empty(AssistantMessage(content=(TextContent(text="actual group reply"),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    for name in ("main", "other"):
        app.register_group(Group(id=name, workspace=str(tmp_path / name), enabled_tools=()))
    runtime = app._runtimes["main"]
    original = runtime.transcript_store._atomic
    blocked = False

    def actual_commit(path, value, **kwargs):
        nonlocal blocked
        result = original(path, value, **kwargs)
        if not blocked:
            blocked = True
            assert threading.get_ident() != owner
            entered.set()
            assert release.wait(5)
        return result

    work = None
    try:
        await app.start()
        monkeypatch.setattr(runtime.transcript_store, "_atomic", actual_commit)
        work = asyncio.create_task(app.route_message(UserInput(group_id="main", content="main input"),
                                                     message_id="original"))
        assert await asyncio.to_thread(entered.wait, 3)
        service = await app.input_service()
        scope = InputScope("main", runtime.workspace_dir)
        assert (await service.store.get(scope, "original")).status == "claimed"
        assert runtime._active_session is None and app.is_group_running("main")
        other = await asyncio.wait_for(app.route_message(UserInput(group_id="other", content="other input")), 3)
        assert other.response_text == "actual group reply" and calls == ["other input"]
        if withdraw:
            assert (await service.withdraw(scope, "original")).status == "cancelled"
            await app.route_message(UserInput(group_id="main", content="next main"),
                                    message_id="successor", wait_for_result=False)
            assert app.is_group_running("main") and calls == ["other input"]
        release.set()
        await asyncio.wait_for(work, 3)
        if withdraw:
            assert (await asyncio.wait_for(service.wait(scope, "successor"), 3)).status == "succeeded"
            assert (await service.store.get(scope, "original")).status == "cancelled"
        assert calls == ["other input", "next main" if withdraw else "main input"]
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await app.stop()


async def test_repeated_preparation_cancel_settles_real_disk_before_group_owner_releases(tmp_path, monkeypatch):
    entered, release, exited = (threading.Event() for _ in range(3))
    calls = []

    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="only actual user turn"),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
    runtime = app._runtimes["main"]
    original = runtime.transcript_store._atomic
    held = False

    def actual_commit(path, value, **kwargs):
        nonlocal held
        result = original(path, value, **kwargs)
        if not held:
            held = True
            entered.set()
            try:
                assert release.wait(5)
            finally:
                exited.set()
        return result

    work = None
    try:
        await app.start()
        monkeypatch.setattr(runtime.transcript_store, "_atomic", actual_commit)
        reservation = app.try_reserve_group("main")
        assert reservation is not None
        work = app.start_group_operation(reservation, runtime.prepare_session())
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            work.cancel()
            await asyncio.sleep(0)
        assert not work.done() and app.is_group_running("main") and not exited.is_set()
        assert runtime._active_session is None and not calls
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert exited.is_set() and not app.is_group_running("main") and runtime._active_session is None
        committed = runtime.get_new_session_id()
        assert committed
        await app.route_message(UserInput(group_id="main", content="fresh actual input"))
        assert len(calls) == 1 and runtime._active_session.meta.session_id == committed
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await app.stop()


async def test_cached_preparation_refuses_a_different_committed_current_context(tmp_path):
    calls = []

    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="actual original"),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
    try:
        await app.start()
        await app.route_message(UserInput(group_id="main", content="original"))
        runtime = app._runtimes["main"]
        original = runtime._active_session
        # Simulate a real committed binding change, not a fake frame/store.
        changed = await asyncio.to_thread(runtime.transcript_store.reset_session, "main")
        assert changed.session_id != original.meta.session_id
        with pytest.raises(ValueError, match="owner changed"):
            await runtime.prepare_session(allow_reset=False)
        assert runtime._active_session is original and len(calls) == 1
    finally:
        await app.stop()


async def test_unsorted_native_skill_selection_keeps_original_normalized_prompt_order(tmp_path):
    calls = []

    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="actual ordered skills"),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
    runtime = app._runtimes["main"]
    for slug in ("a", "z"):
        directory = runtime.workspace_dir / "skills" / slug
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"---\nname: {slug}\ndescription: Actual order test\n---\nBODY {slug}\n")
    meta = runtime.transcript_store.create_session("main")
    runtime.transcript_store.save_active_skills(meta.session_id, ("z", "a"))
    sidecar = runtime.transcript_store._sessions_dir / f"{meta.session_id}.skills.json"
    original = sidecar.read_bytes()
    try:
        await app.start()
        result = await app.route_message(UserInput(group_id="main", content="actual selected skills"))
        assert result is not None and result.error is None and len(calls) == 1
        from xdog.ai.types import system_prompt_text
        prefix = system_prompt_text(calls[0][1].system_prompt)
        assert prefix.index("BODY a") < prefix.index("BODY z")
        expected = tuple(sorted({'a', 'z'} | runtime.capabilities.default_skills))
        assert runtime._active_session.agent.active_skills == expected
        assert runtime._active_session._active_skills == frozenset({'a', 'z'})
        assert sidecar.read_bytes() == original, "Preparing native data must not migrate/rewrite saved ordering"
    finally:
        await app.stop()


async def test_native_cold_inventory_preparation_does_not_block_its_protocol_reader(tmp_path, monkeypatch, gateway):
    from packages.claw.tests.test_gateway_inputs import receive, send

    server, _entered, _release, contexts, endpoint = gateway
    runtime = server._orchestrator._runtimes["main"]
    entered, release = threading.Event(), threading.Event()
    original = runtime.transcript_store._atomic
    held = False

    def actual_commit(path, value, **kwargs):
        nonlocal held
        result = original(path, value, **kwargs)
        if not held:
            held = True
            entered.set()
            assert release.wait(5)
        return result

    reader, writer = await asyncio.open_unix_connection(endpoint)
    try:
        await send(writer, {"type": "ping", "group_id": "main", "tool_inventory": 1})
        assert (await receive(reader, "pong"))["tool_inventory"] == 1
        assert runtime._active_session is None
        monkeypatch.setattr(runtime.transcript_store, "_atomic", actual_commit)
        await send(writer, {"type": "tool_inventory_read", "group_id": "main", "session_id": "",
                            "request_id": "cold"})
        assert await asyncio.to_thread(entered.wait, 3)
        await send(writer, {"type": "ping", "group_id": "main"})
        assert (await receive(reader, "pong"))["tool_inventory"] == 1
        assert runtime._active_session is None and not contexts
        release.set()
        assert (await receive(reader, "tool_inventory_result", request_id="cold"))["success"]
        assert not contexts
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()
