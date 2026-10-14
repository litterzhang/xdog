"""Reader-responsive staged navigation using actual group, socket, app and target-entry owners."""
from __future__ import annotations

import asyncio
import threading

import pytest

ALIAS = 'ext-stagedprovider-local/choice'


def entries(**flags):
    return {'stagedprovider': [{'id': 'flags', 'kind': 'note', 'data': flags}]}


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancel'])
async def test_sync_stage_runner_owns_loop_and_settles_cleanup_inside_async_caller(outcome):
    from xdog.claw.core.runtime.session_staging import owned_run

    caller_loop = asyncio.get_running_loop()
    caller_thread = threading.get_ident()
    observed = []

    async def initialize():
        observed.append((asyncio.get_running_loop(), threading.get_ident()))
        try:
            await asyncio.sleep(0)
            if outcome == 'failure':
                raise ValueError('target initialization failed')
            if outcome == 'cancel':
                raise asyncio.CancelledError
            return 'prepared'
        finally:
            await asyncio.sleep(0)
            observed.append('settled')

    if outcome == 'success':
        assert owned_run(initialize) == 'prepared'
    else:
        error = ValueError if outcome == 'failure' else asyncio.CancelledError
        with pytest.raises(error):
            owned_run(initialize)
    stage_loop, stage_thread = observed[0]
    assert stage_loop is not caller_loop and stage_loop.is_closed()
    assert stage_thread != caller_thread and observed[-1] == 'settled'
    assert asyncio.get_running_loop() is caller_loop and not caller_loop.is_closed()
    assert not any(thread.name.startswith('xdog-claw-stage') for thread in threading.enumerate())


async def test_sync_prepared_session_close_owns_cleanup_inside_async_caller():
    from types import SimpleNamespace

    from xdog.claw.core.runtime.session_staging import PreparedSession

    caller_loop = asyncio.get_running_loop()
    observed = []

    async def close_children():
        assert asyncio.get_running_loop() is not caller_loop
        await asyncio.sleep(0)
        observed.append('children')

    async def close_extensions():
        await asyncio.sleep(0)
        observed.append('extensions')

    session = SimpleNamespace(children=SimpleNamespace(supervisor=SimpleNamespace(aclose=close_children)),
                              extensions=SimpleNamespace(aclose=close_extensions),
                              dispose=lambda: observed.append('disposed'))
    PreparedSession(session, None, None).close()
    assert observed == ['children', 'extensions', 'disposed']
    assert asyncio.get_running_loop() is caller_loop and not caller_loop.is_closed()
    assert not any(thread.name.startswith('xdog-claw-stage') for thread in threading.enumerate())
