"""Legacy reset dispatch uses application-owned IO without blocking its native reader."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.claw.core.types import UserInput

from packages.claw.tests.test_gateway_inputs import gateway as gateway
from packages.claw.tests.test_gateway_inputs import receive, send


@pytest.mark.parametrize("hold", [False, True])
async def test_native_reset_commits_off_loop_and_reader_remains_responsive(gateway, monkeypatch, hold):
    server, _entered, _release, contexts, endpoint = gateway
    app = server._orchestrator
    await app.route_message(UserInput(group_id="main", content="actual previous context"))
    runtime = app._runtimes["main"]
    previous = runtime.get_new_session_id()
    owner = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    writes = []
    original = runtime.transcript_store._atomic

    def actual_commit(path, value, **kwargs):
        result = original(path, value, **kwargs)
        writes.append(threading.get_ident())
        entered.set()
        if hold and threading.get_ident() != owner:
            assert release.wait(5)
        return result

    reader, writer = await asyncio.open_unix_connection(endpoint)
    try:
        monkeypatch.setattr(runtime.transcript_store, "_atomic", actual_commit)
        await send(writer, {"type": "reset", "group_id": "main"})
        assert await asyncio.to_thread(entered.wait, 3)
        assert writes and all(thread != owner for thread in writes), "Reset persisted on the Agent loop"
        if hold:
            await send(writer, {"type": "status"})
            assert (await receive(reader, "status"))["running"]
            assert app.is_group_running("main"), "Actual reset must retain the original group fence"
        release.set()
        ack = await receive(reader, "reset_ack")
        assert ack["session_id"] != previous
        assert contexts[0] == ["actual previous context"] and len(contexts) == 3
        assert 'silent maintenance turn' in contexts[1][-1]
        assert 'Prepare a context-rotation handoff' in contexts[2][-1]
        assert runtime._active_session is None
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("stage", ["preparation", "commit"])
async def test_cancelled_reset_settles_original_owner_and_preserves_readonly(gateway, monkeypatch, stage):
    from xdog.agent.access import AccessState
    from xdog.claw.core.persistence import session_preparation

    server, _entered, _release, contexts, _endpoint = gateway
    app = server._orchestrator
    await app.route_message(UserInput(group_id="main", content="actual original context"))
    runtime = app._runtimes["main"]
    session = runtime._active_session
    previous = session.meta.session_id
    access = AccessState(read_only=True, revision=7).to_dict()
    await asyncio.to_thread(runtime.transcript_store.save_access, previous, access)
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    disposals = []
    dispose = session.dispose

    def actual_dispose():
        disposals.append(threading.get_ident())
        return dispose()

    selected = getattr(session_preparation, "check" if stage == "preparation" else "reset")

    def actual_worker(*args, **kwargs):
        value = selected(*args, **kwargs)
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(session, "dispose", actual_dispose)
    monkeypatch.setattr(session_preparation, "check" if stage == "preparation" else "reset", actual_worker)
    work = asyncio.create_task(app.navigate_session("main", "new", "", "", -1, previous))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            work.cancel()
            await asyncio.sleep(0)
        assert not work.done() and app.is_group_running("main")
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert not app.is_group_running("main")
        current = runtime.get_new_session_id()
        if stage == "preparation":
            assert current == previous and runtime._active_session is session and not disposals
        else:
            assert current != previous and runtime._active_session is None and disposals == [owner]
        assert await asyncio.to_thread(runtime.transcript_store.get_access, current) == access
        assert contexts[0] == ["actual original context"]
        assert len(contexts) == (1 if stage == 'preparation' else 3)
        if stage == 'commit':
            assert 'silent maintenance turn' in contexts[1][-1]
            assert 'Prepare a context-rotation handoff' in contexts[2][-1]
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
