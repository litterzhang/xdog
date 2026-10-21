"""Restored native child records are detached IO, never replayed Agents or parent-loop reads."""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest
from xdog.agent.children import ChildSpec
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


def application(tmp_path, calls):
    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Actual completed work'),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/children', stream_fn=model)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=()))
    return app


@pytest.mark.parametrize('corrupt', [False, True])
async def test_existing_child_catalog_restore_reads_off_loop_without_replay_or_repair(tmp_path, monkeypatch, corrupt):
    calls = []
    previous = application(tmp_path, calls)
    try:
        await previous.route_message(UserInput(group_id='main', content='Parent ready'))
        runtime = previous._runtimes['main']
        session = runtime._active_session
        identifier = await session.children.runtime.start('Actual ordinary child task')
        await session.children.runtime.control('wait', identifier)
        records_root = session.children.store.root
        path = records_root / f'{identifier}.json'
        old_session = session.meta.session_id
        assert len(calls) == 2 and path.exists()
    finally:
        await previous.stop()
    if corrupt:
        path.write_bytes(b'Unknown corrupt child record; never repair or replace\n')
    before = path.read_bytes()
    recovered_calls, opened = [], []
    recovered = application(tmp_path, recovered_calls)
    owner = threading.get_ident()
    original = os.open

    def open_file(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).is_relative_to(records_root):
            opened.append(threading.get_ident())
        return original(file, *args, **kwargs)

    try:
        with monkeypatch.context() as trace:
            trace.setattr(os, 'open', open_file)
            parent = await recovered._runtimes['main'].prepare_session(allow_reset=False)
        assert parent.meta.session_id == old_session and not recovered_calls
        if corrupt:
            assert 'unavailable' in parent.children.runtime.diagnostic.lower()
            with pytest.raises(ValueError, match="unavailable"):
                await parent.children.runtime.start('Cannot delegate over unknown catalog')
        else:
            rows = parent.children.runtime.snapshot()['children']
            assert len(rows) == 1 and rows[0]['id'] == identifier and rows[0]['status'] == 'completed'
        assert path.read_bytes() == before
        assert opened and all(thread != owner for thread in opened), (
            'Actual saved child records were read on the parent loop')
    finally:
        await recovered.stop()


async def test_cancelled_child_metadata_preparation_keeps_original_group_owner(tmp_path, monkeypatch):
    from xdog.claw.core.runtime import child_agents

    calls = []
    app = application(tmp_path, calls)
    runtime = app._runtimes['main']
    parent = await runtime.prepare_session(allow_reset=False)
    spec = ChildSpec('a' * 12, 'No child model may start')
    await parent.children.capture(spec)
    entered, release = threading.Event(), threading.Event()
    original = child_agents.resolve_child_model

    def actual_metadata(*args, **kwargs):
        value = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(child_agents, 'resolve_child_model', actual_metadata)
    reservation = app.try_reserve_group('main')
    assert reservation is not None
    work = app.start_group_operation(reservation, parent.children.create(spec, lambda event: None))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            work.cancel()
            await asyncio.sleep(0)
        assert not work.done() and app.is_group_running('main'), 'Child metadata IO outlived the original group owner'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert not app.is_group_running('main') and not calls
        assert parent.children.runtime.snapshot()['children'] == []
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        await app.stop()
