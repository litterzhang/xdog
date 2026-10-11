"""Live own-Agent boolean inspection is independent of queued-input and UI grants."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import validate_dialog
from xdog.agent.extensions.runtime import silent_extension_ui

from packages.agent.tests.test_extension_queued_input import setup


async def test_live_boolean_is_not_an_invocation_snapshot_and_needs_no_mutation_or_ui_grant(tmp_path):
    runtime = setup(tmp_path)
    reads = []
    async def read():
        reads.append(True)
        return len(reads) == 2
    runtime.pending_messages = read
    async def forbidden(*args):
        pytest.fail('Boolean inspection opened an ordinary dialog or queue proposal')
    runtime.dialog_handler = forbidden
    runtime.queued_input_handler = forbidden
    try:
        with silent_extension_ui():
            assert json.loads(await runtime.command('ext-queuedrequest-pending', '')) == [False, True]
        assert len(reads) == 2
        runtime.pending_messages = None
        assert json.loads(await runtime.command('ext-queuedrequest-pending', '')) == [None, None]
        assert await ExtensionAPI({}).has_pending_messages() is None
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('change', ['reader', 'session'])
async def test_live_boolean_cannot_adopt_replacement_handler_or_session_after_await(tmp_path, change):
    runtime = setup(tmp_path)
    entered, finish = threading.Event(), threading.Event()
    calls = []
    async def read():
        calls.append(True)
        entered.set()
        while not finish.is_set():
            await asyncio.sleep(.003)
        return True
    runtime.pending_messages = read
    task = asyncio.create_task(runtime.command('ext-queuedrequest-pending', ''))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(.003)
        if change == 'reader':
            runtime.pending_messages = lambda: pytest.fail('Cannot borrow replacement reader')
        else:
            runtime.context = lambda: {'session_id': 'replacement'}
        finish.set()
        values = json.loads(await task)
        assert values == [None, None] and len(calls) == 1
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


@pytest.mark.parametrize('field,value', [('target', 'other'), ('child_id', 'child'), ('session_id', 'other'),
                                       ('text', 'body'), ('images', []), ('offset', 0)])
def test_pending_read_schema_cannot_request_queue_bodies_or_choose_an_owner(field, value):
    with pytest.raises(ValueError):
        validate_dialog({'id': 'pending', 'kind': 'session', 'operation': 'pending_messages',
                         'title': 'Read boolean', field: value})


@pytest.mark.parametrize('value', [1, 'true', {'messages': ['PRIVATE']}, ['PRIVATE']])
async def test_invalid_host_read_values_never_become_callback_queue_data(tmp_path, value):
    runtime = setup(tmp_path)
    runtime.pending_messages = lambda: value
    try:
        assert json.loads(await runtime.command('ext-queuedrequest-pending', '')) == [None, None]
        assert not runtime._failed
    finally:
        await runtime.aclose()
