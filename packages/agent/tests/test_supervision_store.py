from __future__ import annotations

import asyncio
import os
import threading

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.supervision import ChildSpec, ChildSupervisor
from xdog.agent.supervision_store import ChildStore
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream


async def test_interrupted_catalog_never_restarts_and_closed_results_remain_archived(tmp_path):
    store = ChildStore.scoped(tmp_path, "session", "head")
    spec = ChildSpec("0123456789ab", "task", "read-only")
    store.begin(spec)
    def factory(spec, publish):
        raise AssertionError("Archived work must never restart")
    owner = ChildSupervisor(factory, records=store.load(), forget=store.forget)
    try:
        assert not owner.active
        record = await owner.control("inspect", spec.id)
        assert record["status"] == "interrupted" and "effects may have occurred" in record["error"]
        await owner.control("close", spec.id)
        assert not store.load() and (store.root / f"{spec.id}.json").exists()
    finally:
        await owner.aclose()


@pytest.mark.parametrize("scope", [("../session", "head"), ("session", "../../outside"), ("session", "")])
def test_scope_cannot_escape_parent_session_directory(tmp_path, scope):
    with pytest.raises(ValueError, match="scope"):
        ChildStore.scoped(tmp_path, *scope)


def test_records_reject_links_and_entry_state_is_isolated(tmp_path):
    store = ChildStore(tmp_path / "records")
    one, two = ChildSpec("0123456789ab", "one"), ChildSpec("abcdef012345", "two")
    store.begin(one)
    store.begin(two)
    store.save_entries(one.id, {"plugin": [{"id": "entry", "kind": "note", "data": "one"}]})
    assert store.entries(two.id) == {}
    path = store.root / f"{one.id}.json"
    path.unlink()
    path.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symbolic"):
        store.begin(one)
    with pytest.raises(ValueError):
        store.load()


async def test_async_factory_has_absolute_deadline_and_finalizer_settles_before_persistence():
    finalized, records = [], []
    async def factory(spec, publish):
        await asyncio.sleep(60)
        return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))
    async def finalize(spec):
        await asyncio.sleep(0)
        finalized.append(spec.id)
    def persist(record):
        assert finalized == [record["id"]]
        records.append(record)
    owner = ChildSupervisor(factory, persist, finalize=finalize)
    try:
        identifier = await owner.start("task", timeout=1)
        record = await owner.control("wait", identifier)
        assert record["status"] == "timed-out" and record["calls"] == 0 and len(records) == 1
    finally:
        await owner.aclose()


async def test_cancel_during_begin_cannot_overwrite_final_record_later():
    entered, release = threading.Event(), threading.Event()
    writes = []
    def begin(spec):
        entered.set()
        assert release.wait(5)
        writes.append("begin")
    def persist(record):
        writes.append(record["status"])
    def factory(spec, publish):
        return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))
    owner = ChildSupervisor(factory, persist, begin=begin)
    try:
        identifier = await owner.start("task")
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.003)
        canceling = asyncio.create_task(owner.control("cancel", identifier))
        await asyncio.sleep(0.02)
        assert not canceling.done()
        release.set()
        record = await canceling
        assert record["status"] == "canceled" and writes == ["begin", "canceled"]
    finally:
        release.set()
        await owner.aclose()


async def test_shared_mcp_request_cancellation_waits_for_actual_owner_settlement(tmp_path):
    from xdog.agent.mcp import McpManager
    entered, release = threading.Event(), threading.Event()
    async def request():
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            while not release.is_set():
                try:
                    await asyncio.sleep(0.002)
                except asyncio.CancelledError:
                    continue
    manager = McpManager({}, tmp_path)
    task = asyncio.create_task(manager._bridge(request()))
    try:
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.003)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await manager.aclose()


async def test_idle_scope_freezes_admission_until_the_entire_host_change_finishes():
    def factory(spec, publish):
        return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))
    owner = ChildSupervisor(factory)
    try:
        with owner.idle_scope():
            with pytest.raises(RuntimeError, match="context/policy is changing"):
                await owner.start("race")
            assert not owner.active and not owner.snapshot()["children"]
        identifier = await owner.start("later")
        await owner.control("wait", identifier)
    finally:
        await owner.aclose()


async def test_cancel_during_close_archive_settles_metadata_before_navigation_can_admit():
    entered, release = threading.Event(), threading.Event()
    writes = []
    def factory(spec, publish):
        return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))
    def forget(identifier):
        entered.set()
        assert release.wait(5)
        writes.append(identifier)
    owner = ChildSupervisor(factory, forget=forget)
    try:
        identifier = await owner.start("task")
        await owner.control("wait", identifier)
        closing = asyncio.create_task(owner.control("close", identifier))
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.002)
        closing.cancel()
        await asyncio.sleep(0.02)
        assert owner.active and not closing.done()
        with pytest.raises(RuntimeError, match="Child work"):
            with owner.idle_scope():
                pass
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert writes == [identifier] and not owner.active and not owner.snapshot()["children"]
    finally:
        release.set()
        await owner.aclose()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX directory-fd durability primitive')
def test_child_record_flushes_file_then_rename_then_directory_and_new_parent_entries(tmp_path, monkeypatch):
    import os
    import stat

    import xdog.agent.supervision_store as module
    store = ChildStore(tmp_path / 'new-parent' / 'records')
    calls = []
    fsync, replace = module.os.fsync, module.os.replace
    def sync(fd):
        calls.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
        fsync(fd)
    def rename(source, target):
        calls.append('replace')
        replace(source, target)
    monkeypatch.setattr(module.os, 'fsync', sync)
    monkeypatch.setattr(module.os, 'replace', rename)
    store.begin(ChildSpec('0123456789ab', 'retained initial task'))
    assert calls == ['directory', 'directory', 'file', 'replace', 'directory']
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    calls.clear()
    record = store.load()[0]
    record['error'] = 'second actual record'
    store.save(record)
    assert calls == ['file', 'replace', 'directory']
    assert store.load()[0]['error'] == 'second actual record'
    assert not list(store.root.glob('.child-*'))


def test_directory_flush_failure_reports_uncertain_replacement_without_rollback(tmp_path, monkeypatch):
    store = ChildStore(tmp_path / 'records')
    store.begin(ChildSpec('0123456789ab', 'task'))
    record = store.load()[0]
    record['error'] = 'replacement actually happened'
    def fail(directory):
        raise OSError('Owned directory flush failure')
    monkeypatch.setattr(store, '_sync_directory', fail)
    with pytest.raises(OSError, match='flush failure'):
        store.save(record)
    # The failure is after rename, not proof the old state survived. No automatic
    # rollback or second write: callers must inspect actual persisted state.
    assert store.load()[0]['error'] == 'replacement actually happened'
    assert not list(store.root.glob('.child-*'))


@pytest.mark.skipif(os.name != 'posix', reason='POSIX directory-fd durability primitive')
def test_directory_flush_failure_closes_its_owned_fd(tmp_path, monkeypatch):
    import os

    import xdog.agent.supervision_store as module
    directory = tmp_path / 'owned'
    directory.mkdir()
    original = module.os.open
    fds = []
    def opened(*args, **kwargs):
        fd = original(*args, **kwargs)
        fds.append(fd)
        return fd
    def fail(fd):
        raise OSError('Owned fsync failure')
    monkeypatch.setattr(module.os, 'open', opened)
    monkeypatch.setattr(module.os, 'fsync', fail)
    with pytest.raises(OSError, match='fsync failure'):
        ChildStore._sync_directory(directory)
    assert len(fds) == 1
    with pytest.raises(OSError):
        os.fstat(fds[0])
