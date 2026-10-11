"""Real filesystem barriers keep Claw's async selection owner until settlement."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.agent import AgentTool
from xdog.claw.core.types import UserInput

from packages.claw.tests.test_active_tool_persistence import close, create


async def wait(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(.003)


@pytest.mark.parametrize('ending', ['apply', 'cancel', 'withdraw', 'changed', 'failure', 'committed-cancel'])
async def test_owned_commit_responsive_and_no_unadmitted_disk_or_runtime_change(tmp_path, monkeypatch, ending):
    runtime, calls = create(tmp_path)
    entered, release = threading.Event(), threading.Event()
    host_thread = threading.get_ident()
    allowed = [True]
    work = None
    try:
        session = runtime.get_or_create_session(allow_reset=False)
        await session.initialize_extensions()
        await session.select_active_tools(['current_time'], expected_revision=session.agent.tool_selection_revision)
        store = runtime.transcript_store
        path = store._path(session.meta.session_id).with_suffix('.tools.json')
        before = path.read_bytes()
        original = store.save_active_tools
        def blocked(identifier, selected, *, before_replace=None):
            assert threading.get_ident() != host_thread, 'Disk write blocked the gateway loop'
            def barrier():
                entered.set()
                assert release.wait(8), 'Test disk gate was not released'
                if ending == 'failure':
                    raise OSError('Injected storage failure')
                before_replace()
            if ending == 'committed-cancel':
                original(identifier, selected, before_replace=before_replace)
                entered.set()
                assert release.wait(8), 'Post-commit gate was not released'
            else:
                original(identifier, selected, before_replace=barrier)
        monkeypatch.setattr(store, 'save_active_tools', blocked)
        work = asyncio.create_task(session.select_active_tools([],
            expected_revision=session.agent.tool_selection_revision, alive=lambda: allowed[0]))
        await wait(entered.is_set)
        assert session._tool_selection_changing and session.agent.active_tool_selection == ('current_time',)
        with pytest.raises(RuntimeError, match='persistence owns'):
            await session.select_active_tools(None, expected_revision=session.agent.tool_selection_revision)
        with pytest.raises(RuntimeError, match='persistence owns'):
            await session.run_turn(UserInput(content='must not run', group_id='main'))
        if ending in {'cancel', 'committed-cancel'}:
            work.cancel()
        elif ending == 'withdraw':
            allowed[0] = False
        elif ending == 'changed':
            session.agent.set_tools((*session.agent.state.tools, AgentTool(name='new', parameters={})))
        await asyncio.sleep(.02)
        assert not work.done() and session._tool_selection_changing
        release.set()
        outcome = (await asyncio.gather(work, return_exceptions=True))[0]
        assert isinstance(outcome, asyncio.CancelledError) if ending in {'cancel', 'committed-cancel'} else (
            outcome is None if ending == 'apply' else isinstance(outcome, (OSError, ValueError)))
        applied = ending in {'apply', 'committed-cancel'}
        expected = () if applied else ('current_time',)
        assert session.agent.active_tool_selection == expected
        assert store.get_active_tools(session.meta.session_id) == expected
        if not applied:
            assert path.read_bytes() == before
        assert not session._tool_selection_changing and not calls
        assert not list(path.parent.glob('.session-*.tmp'))
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await close(runtime)
