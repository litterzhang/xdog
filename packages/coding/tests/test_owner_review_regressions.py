"""Independent whole-change review: real-owner race and exhaustive shutdown regressions."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.agent.acp.contracts import Peer
from xdog.coding.acp.backend import CodingBackend
from xdog.coding.core.session_history import HistorySnapshot
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.ownership import SessionLease, lease_directory
from xdog.coding.core.slash_commands import execute_command

from packages.coding.tests.extension_session_helpers import session as extension_session


async def no_endpoint():
    pass


async def test_snapshot_baseline_cannot_adopt_a_newer_committed_metadata_version(tmp_path, monkeypatch):
    session, _ = extension_session(tmp_path)
    runtime = session.enable_runtime(bound=True)
    runtime.start(own_thread=True, setup_pending=True)
    await asyncio.wrap_future(runtime.controller._async(runtime.activate_inputs))
    captured, appended = threading.Event(), threading.Event()
    actual_capture = HistorySnapshot.capture
    used = False
    def capture(cls, data):
        nonlocal used
        result = actual_capture(data)
        if threading.current_thread() is runtime.controller._thread and len(data.messages) >= 2 and not used:
            used = True
            captured.set()
            assert appended.wait(6)
        return result
    def append():
        assert captured.wait(6)
        try:
            session.extensions.entries.append('dialogue', [{'kind': 'note', 'data': 'ACKNOWLEDGED_METADATA'}])
        finally:
            appended.set()
    writer = threading.Thread(target=append)
    writer.start()
    monkeypatch.setattr(HistorySnapshot, 'capture', classmethod(capture))
    try:
        with pytest.raises(RuntimeError, match='history writer changed'):
            await session.send_message('capture a normal history tail')
        writer.join()
        saved = session.session_manager.load_session(session.session_id)
        assert saved.extension_entries['dialogue'][0]['data'] == 'ACKNOWLEDGED_METADATA'
        assert saved.extension_entries == session.session_data.extension_entries
        assert runtime.controller._broken is not None
    finally:
        appended.set()
        await asyncio.to_thread(writer.join)
        await session.aclose()


async def test_repeated_current_branch_navigation_still_rotates_and_settles_resources(tmp_path, monkeypatch):
    session, _ = extension_session(tmp_path, extra=('contextual',))
    runtime = session.enable_runtime(bound=True)
    runtime.start(own_thread=True, setup_pending=True)
    monkeypatch.setattr(runtime, 'open_endpoint', no_endpoint)
    await asyncio.wrap_future(runtime.controller.control(session.initialize_extensions, name='initialize'))
    try:
        branch = await asyncio.to_thread(session.create_branch)
        await asyncio.to_thread(session.restore_branch, branch)
        generation, extension = runtime.controller.generation, session.extensions
        assert await asyncio.to_thread(session.restore_branch, branch)
        assert runtime.controller.generation != generation
        assert not session._retired_resources and not extension._manager._thread.is_alive()
        result = await execute_command('thinking', 'off', session)
        assert 'off' in result.output.lower()
    finally:
        await session.aclose()


async def test_all_retired_groups_join_before_error_receipt_and_writer_release(tmp_path, monkeypatch):
    session, _ = extension_session(tmp_path / 'current')
    first, _ = extension_session(tmp_path / 'first', extra=('contextual',))
    second, _ = extension_session(tmp_path / 'second', extra=('contextual',))
    await first.initialize_extensions()
    await second.initialize_extensions()
    runtime = session.enable_runtime(bound=True)
    runtime.start(own_thread=True, setup_pending=True)
    session._retired_resources.extend((owner.children, owner.extensions, owner.mcp) for owner in (first, second))
    actual_first = first.children.supervisor.aclose
    failure = OSError('first retired owner drained but failed')
    async def fail():
        await actual_first()
        raise failure
    monkeypatch.setattr(first.children.supervisor, 'aclose', fail)
    client = second.extensions._manager._clients['contextual']
    actual_second = client.close
    entered, release = threading.Event(), threading.Event()
    async def gated():
        entered.set()
        assert await asyncio.to_thread(release.wait, 8)
        await actual_second()
    monkeypatch.setattr(client, 'close', gated)
    closing = asyncio.create_task(session.aclose())
    try:
        assert await asyncio.to_thread(entered.wait, 3), 'A first-group failure skipped a later real worker'
        assert not closing.done() and second.extensions._manager._thread.is_alive()
        with pytest.raises(RuntimeError, match='owned'):
            SessionLease.acquire(lease_directory(session.session_manager._dir), session.session_id)
        release.set()
        with pytest.raises(OSError) as error:
            await closing
        assert error.value is failure
        assert not second.extensions._manager._thread.is_alive() and not session._retired_resources
        assert session._lease is None
        replacement = SessionLease.acquire(lease_directory(session.session_manager._dir), session.session_id)
        replacement.close()
    finally:
        release.set()
        await asyncio.gather(closing, return_exceptions=True)
        await asyncio.gather(first.aclose(), second.aclose(), return_exceptions=True)


async def test_acp_repeated_cancel_during_factory_joins_and_closes_late_session(tmp_path, monkeypatch):
    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'ambient'))
    session, _ = extension_session(tmp_path)
    entered, release, constructed = threading.Event(), threading.Event(), threading.Event()
    def factory(cwd):
        session._lease = SessionLease.acquire(lease_directory(session.session_manager._dir), session.session_id)
        entered.set()
        assert release.wait(6)
        constructed.set()
        return session
    backend = CodingBackend(factory=factory, inbox_store=InboxStore(session.session_manager._dir, tmp_path / 'inbox'))
    creating = asyncio.create_task(backend.new_session(tmp_path, Peer('peer', None)))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            creating.cancel()
            await asyncio.sleep(.02)
        assert not creating.done(), 'Canceled creation abandoned the underlying constructor'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await creating
        assert constructed.is_set() and session.agent.closed and session._lease is None
        assert not backend._owned
    finally:
        release.set()
        await asyncio.to_thread(constructed.wait, 6)
        await asyncio.gather(creating, return_exceptions=True)
        await session.aclose()


async def test_acp_disconnect_error_still_closes_all_owned_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'ambient'))
    sessions = [extension_session(tmp_path / name, extra=('contextual',))[0] for name in ('first', 'second')]
    backend = CodingBackend(inbox_store=InboxStore(tmp_path / 'sessions', tmp_path / 'inbox'))
    peer = Peer('peer', None)
    class FailedEndpoint:
        async def close(self):
            raise OSError('endpoint already settled with error')
    try:
        for index, session in enumerate(sessions):
            runtime = session.enable_runtime(bound=True)
            runtime.start(own_thread=True, setup_pending=True)
            await asyncio.wrap_future(runtime.controller.control(session.initialize_extensions, name='initialize'))
            runtime.server = FailedEndpoint() if index == 0 else None
            backend._owned[session.session_id] = peer.id, runtime, runtime.server
        with pytest.raises(OSError, match='endpoint already settled with error'):
            await backend.disconnect(peer)
        assert all(session.agent.closed and session._lease is None for session in sessions)
        assert all(not session.extensions._manager._thread.is_alive() for session in sessions)
        assert not backend._owned
    finally:
        await asyncio.gather(*(session.aclose() for session in sessions), return_exceptions=True)


async def test_actual_settlement_error_wins_over_cancel_after_join(coding_session):
    runtime = coding_session.enable_runtime(bound=True)
    runtime.start(own_thread=True, setup_pending=True)
    entered, release = threading.Event(), threading.Event()
    failure = OSError('actual writer settlement failed')
    async def fail():
        entered.set()
        assert await asyncio.to_thread(release.wait, 6)
        raise failure
    async def operation():
        runtime.controller.settle_before_release(fail())
        return 'done'
    receipt = runtime.controller.control(operation, name='settlement')
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        runtime.controller.cancel_control(receipt)
        await asyncio.wrap_future(runtime.controller._async(lambda: asyncio.sleep(.02)))
        assert not receipt.done()
        release.set()
        with pytest.raises(OSError) as error:
            await asyncio.wrap_future(receipt)
        assert error.value is failure
    finally:
        release.set()
        await asyncio.gather(asyncio.wrap_future(receipt), return_exceptions=True)
        await coding_session.aclose()
