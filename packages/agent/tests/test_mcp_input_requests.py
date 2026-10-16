"""Root-only modern continuation ownership, bounded work and no private replay."""
from __future__ import annotations

import asyncio
import copy
import threading

import pytest
from xdog.agent.mcp.client_capabilities import RootCapability
from xdog.agent.mcp.input_requests import InputRequests
from xdog.agent.mcp.stdio import McpError

from packages.agent.tests.mcp_helpers import until


def driver():
    roots = RootCapability((('file:///explicit', 'Explicit'),))
    inputs = InputRequests(roots)
    roots.active = inputs.active = True
    return inputs


def required(*, requests=None, state=None):
    return {'resultType': 'input_required', **({'inputRequests': requests} if requests is not None else {}),
            **({'requestState': state} if state is not None else {})}


async def test_modern_roots_exact_opaque_resume_same_arguments_and_no_earlier_input_replay():
    inputs, sent = driver(), []
    original = {'name': 'tool', 'arguments': {'nested': [False, None, 'ORIGINAL']}}
    frozen = copy.deepcopy(original)
    state = '  opaque\x00state/汉字\n  '
    async def send(value):
        sent.append(copy.deepcopy(value))
        if len(sent) == 1:
            original['arguments']['nested'][2] = 'MUTATION'
            return required(requests={'key': {'method': 'roots/list'}}, state=state)
        if len(sent) == 2:
            assert value == {**frozen, 'inputResponses': {'key': {'roots': [
                {'uri': 'file:///explicit', 'name': 'Explicit'}]}}, 'requestState': state}
            inputs.roots.replace((), inputs.roots.revision)
            return required(state='next')
        raise AssertionError('Withdrawn disclosure must never resume')
    with pytest.raises(McpError, match='withdrawn'):
        await inputs.run('tools/call', original, send)
    assert len(sent) == 2 and not inputs._jobs


async def test_request_state_only_round_does_not_replay_previous_root_answers():
    inputs, sent = driver(), []
    async def send(value):
        sent.append(copy.deepcopy(value))
        if len(sent) == 1:
            return required(requests={'key': {'method': 'roots/list'}}, state='first')
        if len(sent) == 2:
            return required(state=' second ')
        return {'content': []}
    assert await inputs.run('tools/call', {'name': 'tool'}, send) == {'content': []}
    assert sent[1]['inputResponses']['key']['roots']
    assert sent[2] == {'name': 'tool', 'requestState': ' second '}
    assert inputs.advertised() == {'roots': {}}
    assert not hasattr(inputs, 'reverse') and not hasattr(inputs, 'elicitation_handler')


@pytest.mark.parametrize('bad', [
    {}, {'inputRequests': {}}, {'requestState': False}, {'requestState': 'x' * 65537},
    {'inputRequests': {'x': {'method': 'unknown'}}},
    {'inputRequests': {'x': {'jsonrpc': '2.0', 'id': 'injected', 'method': 'roots/list'}}},
    {'inputRequests': {'x': {'method': 'roots/list', 'params': {'path': '/private'}}}},
    {'inputRequests': {str(i): {'method': 'roots/list'} for i in range(5)}},
])
async def test_invalid_whole_round_refuses_before_root_disclosure(bad):
    inputs, calls = driver(), []
    async def send(value):
        calls.append(value)
        return {'resultType': 'input_required', **bad}
    with pytest.raises(McpError):
        await inputs.run('resources/read', {'uri': 'test://owned'}, send)
    assert len(calls) == 1 and not inputs._jobs


@pytest.mark.parametrize('family', ['sampling/createMessage', 'elicitation/create', 'tasks/list',
                                    'tasks/get', 'tasks/result', 'tasks/cancel'])
@pytest.mark.parametrize('params', [{}, {'task': {}}, {'_meta': {
    'io.modelcontextprotocol/related-task': {'taskId': 'foreign'}}}])
async def test_removed_sibling_refuses_entire_round_without_root_callback_or_resume(family, params, monkeypatch):
    inputs, sends, calls = driver(), [], []
    monkeypatch.setattr(inputs.roots, 'response', lambda value: calls.append(value))
    async def send(value):
        sends.append(value)
        return required(requests={'root': {'method': 'roots/list'}, 'removed': {'method': family, 'params': params}})
    with pytest.raises(McpError, match='not declared|task augmentation'):
        await inputs.run('prompts/get', {'name': 'owned'}, send)
    assert len(sends) == 1 and not calls and not inputs._jobs


@pytest.mark.parametrize('bad', [None, [], False, 2, 'not an object'])
def test_root_required_validation_rejects_nonobjects_without_type_confusion(bad):
    inputs = driver()
    with pytest.raises(McpError, match='object'):
        inputs._validate(bad, inputs.advertised())


@pytest.mark.parametrize('params', [{'inputResponses': {}}, {'requestState': 'foreign'}])
async def test_caller_cannot_inject_continuation_authority_before_first_wire_leg(params):
    inputs, calls = driver(), []
    async def send(value):
        calls.append(value)
        return {}
    with pytest.raises(McpError, match='original request ownership'):
        await inputs.run('tools/call', {'name': 'owned', **params}, send)
    assert not calls and not inputs._jobs


async def test_round_ceiling_unknown_result_and_transport_failure_never_retry():
    inputs, calls = driver(), []
    inputs.max_rounds = 2
    async def send(value):
        calls.append(value)
        return required(requests={'roots': {'method': 'roots/list'}})
    with pytest.raises(McpError, match='round ceiling'):
        await inputs.run('tools/call', {'name': 'owned'}, send)
    assert len(calls) == 3
    for outcome in ('task', 'future-unknown', 'network-failed'):
        calls.clear()
        async def fail(value):
            calls.append(value)
            if outcome == 'network-failed':
                raise OSError('uncertain POST outcome')
            return {'resultType': outcome, 'content': []}
        with pytest.raises((McpError, OSError)):
            await inputs.run('tools/call', {'name': 'owned'}, fail)
        assert len(calls) == 1 and not inputs._jobs


async def test_close_waits_actual_validation_worker_despite_repeated_cancellation(monkeypatch):
    inputs, sent = driver(), []
    entered, release = threading.Event(), threading.Event()
    original = inputs._validate
    def held(value, declared):
        entered.set()
        assert release.wait(5)
        return original(value, declared)
    monkeypatch.setattr(inputs, '_validate', held)
    async def send(value):
        sent.append(value)
        return required(requests={'root': {'method': 'roots/list'}})
    work = asyncio.create_task(inputs.run('tools/call', {'name': 'owned'}, send))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(inputs.close())
        await until(lambda: not inputs.active)
        closing.cancel()
        await asyncio.sleep(.01)
        assert not closing.done() and inputs._jobs and len(sent) == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        await asyncio.gather(work, return_exceptions=True)
        assert not inputs._jobs and len(sent) == 1
    finally:
        release.set()
        await inputs.close()
        await asyncio.gather(work, *([closing] if closing else []), return_exceptions=True)


@pytest.mark.parametrize('failed', [False, True])
async def test_disconnect_waits_suppressed_transport_finally_and_never_resumes(failed):
    inputs, sent = driver(), []
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def send(value):
        sent.append(value)
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            cleanup.set()
            await release.wait()
        return required(requests={'root': {'method': 'roots/list'}})
    work = asyncio.create_task(inputs.run('tools/call', {'name': 'owned'}, send))
    closing = None
    try:
        await entered.wait()
        closing = asyncio.create_task(inputs.close(failed=failed))
        await cleanup.wait()
        closing.cancel()
        await asyncio.sleep(.01)
        assert not closing.done() and not work.done() and inputs._jobs
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        with pytest.raises(McpError if failed else asyncio.CancelledError):
            await work
        assert len(sent) == 1 and not inputs._jobs
    finally:
        release.set()
        await inputs.close()
        await asyncio.gather(work, *([closing] if closing else []), return_exceptions=True)


async def test_four_owned_root_flows_cap_then_close_settles_every_original_worker():
    inputs, entered = driver(), []
    async def send(value):
        entered.append(value)
        await asyncio.Future()
    jobs = [asyncio.create_task(inputs.run('resources/read', {'uri': f'test://{i}'}, send)) for i in range(4)]
    try:
        await until(lambda: len(entered) == 4)
        with pytest.raises(McpError, match='capacity'):
            await inputs.run('resources/read', {'uri': 'test://overflow'}, send)
        await inputs.close()
        assert all(job.done() for job in jobs) and not inputs._jobs
    finally:
        await inputs.close()
        await asyncio.gather(*jobs, return_exceptions=True)
