from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from xdog.claw.core.runtime.terminal_protocol import TerminalInputJobs, TerminalTarget


def create(*, timeout=4):
    sent, alive = [], [True]
    runtime = Mock()
    runtime.terminal_input_available = True
    runtime.terminal_events = ('key', 'paste')
    runtime.terminal_input = AsyncMock(return_value={'data': 'transformed'})
    def capture(group, session, generation):
        assert (group, session, generation) == ('main', 'session', 1)
        return TerminalTarget(runtime, lambda: alive[0])
    async def write(value):
        sent.append(value)
    jobs = TerminalInputJobs(capture, write, timeout=timeout)
    jobs.negotiate(1)
    request = {'type': 'terminal_input', 'request_id': 'one', 'group_id': 'main', 'session_id': 'session',
               'terminal_owner': jobs.owner, 'input_owner': 'focus:geometry:epoch', 'extension_generation': 1,
               'kind': 'key', 'surface': 'prompt', 'data': 'original', 'input_sequence': 1}
    return jobs, runtime, request, sent, alive


async def settle(jobs):
    if jobs._task is not None:
        await jobs._task


async def test_negotiation_owner_strict_metadata_capacity_and_once_only_receipts():
    jobs, runtime, request, sent, _ = create()
    try:
        jobs.negotiate(True)
        await jobs.request(request)
        assert sent[-1]['error'] and not runtime.terminal_input.called
        jobs.negotiate(1)
        request['terminal_owner'] = jobs.owner
        await jobs.request(request)
        await settle(jobs)
        assert sent[-1]['data'] == 'transformed' and sent[-1]['error'] is False
        assert sent[-1]['input_owner'] == request['input_owner']
        await jobs.request(request)
        assert sent[-1]['error'] and runtime.terminal_input.call_count == 1
        for index, override in enumerate([
            {'terminal_owner': 'foreign'}, {'input_owner': 'x' * 257}, {'data': 'x' * 16001},
            {'data': '\ud800'}, {'kind': []}, {'surface': 'approval'}, {'extension_generation': True},
            {'group_id': {}}, {'request_id': []}, {'extension_generation': 2**54},
        ]):
            await jobs.request({**request, 'request_id': str(index), 'input_sequence': index + 2, **override})
            assert sent[-1]['error'] and runtime.terminal_input.call_count == 1
    finally:
        await jobs.close()


async def test_reader_responsive_single_job_foreign_cancel_refusal_and_no_replay():
    jobs, runtime, request, sent, _ = create()
    entered, release = asyncio.Event(), asyncio.Event()
    async def callback(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'data': 'late'}
    runtime.terminal_input.side_effect = callback
    try:
        await jobs.request(request)
        await entered.wait()
        await jobs.request({**request, 'request_id': 'second', 'input_sequence': 2})
        assert sent[-1]['error'] and runtime.terminal_input.call_count == 1
        assert not jobs.cancel({**request, 'terminal_owner': 'foreign'})
        assert not jobs.cancel({**request, 'input_owner': 'another-focus'})
        assert not jobs.cancel({**request, 'input_sequence': True})
        assert jobs.cancel(request)
        await settle(jobs)
        assert len(sent) == 1  # No callback replay or late result/cancellation receipt.
        await jobs.request(request)
        assert sent[-1]['error'] and runtime.terminal_input.call_count == 1
    finally:
        await jobs.close()


@pytest.mark.parametrize('change', ['session', 'withdraw', 'close', 'timeout'])
async def test_session_connection_capability_and_deadline_ownership(change):
    jobs, runtime, request, sent, alive = create(timeout=0.02 if change == 'timeout' else 4)
    entered, release = asyncio.Event(), asyncio.Event()
    async def callback(*args, **kwargs):
        entered.set()
        await release.wait()
        return {'data': 'old-session-result'}
    runtime.terminal_input.side_effect = callback
    try:
        await jobs.request(request)
        await entered.wait()
        if change == 'session':
            alive[0] = False
            release.set()
        elif change == 'withdraw':
            jobs.negotiate(0)
        elif change == 'close':
            await jobs.close()
        await settle(jobs)
        if change in {'session', 'timeout'}:
            assert len(sent) == 1 and sent[0]['error'] is True and 'data' not in sent[0]
        else:
            assert not sent
        assert runtime.terminal_input.call_count == 1
    finally:
        await jobs.close()


async def test_receipt_eviction_never_allows_old_input_sequences_to_replay():
    jobs, runtime, request, sent, _ = create()
    try:
        for sequence in range(1, 131):
            await jobs.request({**request, 'request_id': str(sequence), 'input_sequence': sequence})
            await settle(jobs)
        assert len(jobs._retired) == 128 and runtime.terminal_input.call_count == 130
        await jobs.request({**request, 'request_id': '1'})
        assert sent[-1]['error'] and runtime.terminal_input.call_count == 130
    finally:
        await jobs.close()


async def test_release_event_requires_explicit_transport_and_runtime_capability_and_real_phase():
    jobs, runtime, request, sent, _ = create()
    runtime.terminal_events = ('key', 'paste', 'release')
    runtime.terminal_input.return_value = {'consume': True}
    request.update(kind='release', data='\x1b[97;1:3u')
    try:
        await jobs.request(request)
        assert sent[-1]['error'] and not runtime.terminal_input.called
        jobs.negotiate_release(1)
        request['terminal_owner'] = jobs.owner
        for data in ('a', '\x1b[97u', '\x1b[99;5:3u', '\x1b[27;1:3u'):
            await jobs.request({**request, 'data': data})
            assert sent[-1]['error'] and not runtime.terminal_input.called
        await jobs.request(request)
        await settle(jobs)
        assert not sent[-1]['error'] and sent[-1]['consume'] and 'data' not in sent[-1]
        assert sent[-1]['terminal_events'] == ['key', 'paste', 'release']
    finally:
        await jobs.close()


@pytest.mark.parametrize('change', ['withdraw', 'unsafe', 'registration'])
async def test_release_protocol_result_cannot_edit_or_outlive_observer_registration(change):
    jobs, runtime, request, sent, _ = create()
    runtime.terminal_events = ('release',)
    entered, finish = asyncio.Event(), asyncio.Event()
    async def observe(*args, **kwargs):
        entered.set()
        await finish.wait()
        return {'data': 'forged', 'consume': True} if change == 'unsafe' else {'consume': True}
    runtime.terminal_input.side_effect = observe
    jobs.negotiate_release(1)
    request.update(terminal_owner=jobs.owner, kind='release', data='\x1b[97;1:3u')
    try:
        await jobs.request(request)
        await entered.wait()
        if change == 'withdraw':
            jobs.negotiate_release(0)
        elif change == 'registration':
            runtime.terminal_events = ()
        finish.set()
        await settle(jobs)
        assert not sent if change == 'withdraw' else sent[-1]['error'] and 'data' not in sent[-1]
    finally:
        await jobs.close()
