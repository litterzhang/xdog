"""Modern continuations reuse exact review owners, never unsolicited client requests."""
from __future__ import annotations

import asyncio
import copy

import pytest
from xdog.agent.mcp.client_capabilities import RootCapability
from xdog.agent.mcp.elicitation import request as elicit_request
from xdog.agent.mcp.elicitation_owner import ElicitationOwner
from xdog.agent.mcp.input_requests import InputRequests
from xdog.agent.mcp.reverse_requests import ReverseRequests
from xdog.agent.mcp.sampling_owner import SamplingOwner
from xdog.agent.mcp.stdio import McpError

from packages.agent.tests.test_mcp_elicitation import form
from packages.agent.tests.test_mcp_sampling_owner import answer, request, selection, until


def driver():
    roots = RootCapability((('file:///explicit', 'Explicit'),))
    reverse = ReverseRequests()
    inputs = InputRequests(roots, reverse)
    roots.active = inputs.active = True
    return inputs


def required(*, requests=None, state=None):
    return {'resultType': 'input_required', **({'inputRequests': requests} if requests is not None else {}),
            **({'requestState': state} if state is not None else {})}


def embedded(value):
    return {key: copy.deepcopy(value[key]) for key in ('method', 'params')}


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
        assert value == {**frozen, 'requestState': 'next'}
        return {'resultType': 'complete', 'content': []}
    # Selection changes invalidate already captured disclosure authority; no
    # continuation can accidentally release an old root after withdrawal.
    with pytest.raises(McpError, match='withdrawn'):
        await inputs.run('tools/call', original, send)
    assert len(sent) == 2 and not inputs._jobs


async def test_request_state_only_paced_rounds_do_not_replay_previous_private_answers():
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
    assert inputs.reverse.advertised() == {}  # Modern is not reverse-RPC activation.


@pytest.mark.parametrize('bad', [
    {}, {'inputRequests': {}}, {'requestState': False}, {'requestState': 'x' * 65537},
    {'inputRequests': {'x': {'method': 'unknown'}}},
    {'inputRequests': {'x': {'jsonrpc': '2.0', 'id': 'injected', 'method': 'roots/list'}}},
    {'inputRequests': {'x': {'method': 'roots/list', 'params': {'path': '/private'}}}},
    {'inputRequests': {str(i): {'method': 'roots/list'} for i in range(5)}},
])
async def test_invalid_or_unadvertised_whole_round_refuses_before_callbacks(bad):
    inputs, calls = driver(), []
    async def send(value):
        calls.append(value)
        return {'resultType': 'input_required', **bad}
    with pytest.raises(McpError):
        await inputs.run('resources/read', {'uri': 'test://owned'}, send)
    assert len(calls) == 1 and not inputs._jobs


async def test_validate_all_siblings_before_private_sampling_or_elicitation_review():
    inputs, callbacks = driver(), []
    async def handle(value):
        callbacks.append(value)
        raise AssertionError('Malformed sibling must refuse entire round')
    inputs.sampling_handler = inputs.elicitation_handler = handle
    async def send(value):
        return required(requests={'first': embedded(request()), 'bad': {'method': 'roots/list', 'params': []}})
    with pytest.raises(McpError):
        await inputs.run('prompts/get', {'name': 'owned'}, send)
    assert not callbacks


@pytest.mark.parametrize('fault', ['task', 'related', 'tools', 'context', 'execution'])
async def test_modern_sampling_rejects_removed_vocabulary_and_unadvertised_subcapabilities(fault):
    inputs, callbacks = driver(), []
    async def handle(value):
        callbacks.append(value)
        raise AssertionError('Unsupported input must not enter review')
    inputs.sampling_handler = handle
    source = request()
    if fault == 'task':
        source['params']['task'] = {'ttl': 1000}
    elif fault == 'related':
        source['params']['_meta'] = {'io.modelcontextprotocol/related-task': {'taskId': 'foreign'}}
    elif fault == 'context':
        source['params']['includeContext'] = 'allServers'
    else:
        source['params']['tools'] = [{'name': 'tool', 'inputSchema': {'type': 'object'}}]
        if fault == 'execution':
            inputs.reverse.sampling_tools = True
            source['params']['tools'][0]['execution'] = {'taskSupport': 'required'}
    async def send(value):
        return required(requests={'input': embedded(source)})
    with pytest.raises(McpError):
        await inputs.run('tools/call', {'name': 'owned'}, send)
    assert not callbacks


async def test_modern_sampling_exact_input_and_output_approval_precede_resume():
    inputs, events, calls, sent = driver(), [], [], []
    async def execute(value, chosen, cancel):
        calls.append(copy.deepcopy(value))
        # Modern supports an array even without tools, unlike legacy result.
        return {'model': chosen['model'], 'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'FIRST'}, {'type': 'text', 'text': 'PRIVATE SECOND'}]}
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    inputs.sampling_handler = owner.handle_input
    async def send(value):
        sent.append(copy.deepcopy(value))
        if len(sent) == 1:
            return required(requests={'sample': embedded(request())}, state='preserve')
        return {'content': []}
    work = asyncio.create_task(inputs.run('tools/call', {'name': 'owned'}, send))
    try:
        await until(lambda: events)
        assert not calls and len(sent) == 1 and events[0]['protocol'] == '2026-07-28'
        answer(owner, events[0], selection=selection())
        await until(lambda: len(events) == 2)
        assert len(calls) == 1 and len(sent) == 1
        assert events[1]['body']['content'][1]['text'] == 'PRIVATE SECOND'
        answer(owner, events[1])
        assert await work == {'content': []}
        assert sent[1]['inputResponses']['sample'] == events[1]['body']
        assert sent[1]['requestState'] == 'preserve'
    finally:
        await inputs.close()
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('mode', ['form', 'url'])
async def test_modern_elicitation_uses_two_reviews_and_no_legacy_url_completion_pending(mode):
    inputs, events, sent = driver(), [], []
    source = form()
    if mode == 'url':
        source['params'] = {'mode': 'url', 'url': 'https://inert.example/flow', 'message': 'External review'}
    assert elicit_request(source, modern=True) == source
    owner = ElicitationOwner('view', events.append, lambda: True)
    inputs.elicitation_handler = owner.handle_input
    async def send(value):
        sent.append(copy.deepcopy(value))
        return required(requests={'elicit': embedded(source)}, state='state') if len(sent) == 1 else {'contents': []}
    work = asyncio.create_task(inputs.run('resources/read', {'uri': 'test://owned'}, send))
    async def approve(event, response):
        await owner.answer(event['id'], lease='view', phase=event['phase'], receipt=event['receipt'],
                           sha256=event['sha256'], response=response)
    try:
        await until(lambda: events)
        assert events[0]['protocol'] == '2026-07-28'
        proposed = {'action': 'accept', **({'content': {'name': 'PRIVATE'}} if mode == 'form' else {})}
        await approve(events[0], proposed)
        await until(lambda: len(events) == 2)
        assert len(sent) == 1
        await approve(events[1], {'action': 'accept'})
        assert await work == {'contents': []}
        assert sent[1]['inputResponses']['elicit'] == proposed
        assert not owner._urls and not owner._url_ids
        assert not any(event['type'].startswith('elicitation_url') for event in events)
    finally:
        await inputs.close()
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('failure', ['raise', 'cancel', 'close', 'withdraw'])
async def test_input_cleanup_withholds_partial_round_and_waits_actual_handler_finally(failure):
    inputs, sent = driver(), []
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def handle(value):
        entered.set()
        try:
            if failure == 'raise':
                raise McpError('private callback failure')
            await asyncio.Future()
        finally:
            cleaning.set()
            await release.wait()
    inputs.sampling_handler = handle
    async def send(value):
        sent.append(value)
        return required(requests={'roots': {'method': 'roots/list'}, 'sample': embedded(request())})
    work = asyncio.create_task(inputs.run('tools/call', {'name': 'owned'}, send))
    closing = None
    try:
        await entered.wait()
        if failure == 'cancel':
            work.cancel()
        elif failure in ('close', 'withdraw'):
            closing = asyncio.create_task(inputs.close() if failure == 'close' else
                                          inputs.withdraw('sampling/createMessage'))
        await cleaning.wait()
        assert len(sent) == 1 and not work.done() and inputs._jobs
        if closing is not None:
            closing.cancel()
            await asyncio.sleep(.01)
            assert not closing.done()
        release.set()
        await asyncio.gather(work, *([closing] if closing else []), return_exceptions=True)
        assert not inputs._jobs and len(sent) == 1
    finally:
        release.set()
        await inputs.close()
        await asyncio.gather(work, *([closing] if closing else []), return_exceptions=True)


async def test_round_ceiling_terminal_unknown_result_and_transport_failure_never_retry():
    inputs, calls = driver(), []
    inputs.max_rounds = 2
    async def send(value):
        calls.append(value)
        return required(requests={'roots': {'method': 'roots/list'}})
    with pytest.raises(McpError, match='round ceiling'):
        await inputs.run('tools/call', {'name': 'owned'}, send)
    assert len(calls) == 3  # Original request plus two explicit continuations only.
    for outcome in ('foreign-result', 'network-failed'):
        calls.clear()
        async def fail(value):
            calls.append(value)
            if outcome == 'network-failed':
                raise OSError('uncertain POST outcome')
            return {'resultType': 'future-unknown', 'content': []}
        with pytest.raises((McpError, OSError)):
            await inputs.run('tools/call', {'name': 'owned'}, fail)
        assert len(calls) == 1


@pytest.mark.parametrize('modern', [False, True])
def test_official_normalized_form_schema_dialect_annotations_preserved_and_enforced(modern):
    source = form()
    source['params']['requestedSchema'].update({
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        '$id': 'https://never-fetch.invalid/form', '$comment': 'Inert annotation',
        'title': 'Exact requested form', 'description': 'Full disclosure'})
    assert elicit_request(source, modern=modern) == source
    source['params']['requestedSchema']['$schema'] = 'https://unsupported.invalid/schema'
    with pytest.raises(McpError, match='dialect'):
        elicit_request(source, modern=modern)


async def test_repeated_caller_cancel_does_not_interrupt_suppressed_handler_finally_or_send_private_data():
    inputs, sent = driver(), []
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def handle(value):
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            cleanup.set()
            await release.wait()
        return {'model': 'explicit-model', 'role': 'assistant', 'content': {'type': 'text', 'text': 'LATE PRIVATE'}}
    inputs.sampling_handler = handle
    async def send(value):
        sent.append(value)
        return required(requests={'sample': embedded(request())})
    work = asyncio.create_task(inputs.run('tools/call', {'name': 'owned'}, send))
    try:
        await entered.wait()
        work.cancel()
        await cleanup.wait()
        work.cancel()
        await asyncio.sleep(.01)
        assert not work.done() and inputs._jobs and len(sent) == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert len(sent) == 1 and not inputs._jobs
    finally:
        release.set()
        await inputs.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_withdrawn_completed_sibling_disclosure_not_sent_while_next_review_finishes():
    inputs, sent, alive = driver(), [], True
    first_done, release = asyncio.Event(), asyncio.Event()
    async def sample(value):
        first_done.set()
        return {'model': 'model', 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE'}}
    async def elicit(value):
        await release.wait()
        return {'action': 'decline'}
    inputs.sampling_handler, inputs.elicitation_handler = sample, elicit
    inputs.sampling_alive = lambda: alive
    async def send(value):
        sent.append(value)
        return required(requests={'sample': embedded(request()), 'elicit': embedded(form())})
    work = asyncio.create_task(inputs.run('tools/call', {'name': 'owned'}, send))
    try:
        await first_done.wait()
        await until(lambda: 'elicitation/create' in next(iter(inputs._jobs.values())))
        alive = False  # Synchronous host withdrawal precedes asynchronous detach.
        assert 'sampling' not in inputs.advertised()
        release.set()
        with pytest.raises(McpError, match='withdrawn'):
            await work
        assert len(sent) == 1
    finally:
        release.set()
        await inputs.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_four_owned_flows_cap_then_close_settles_every_original_worker():
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


@pytest.mark.parametrize('action', ['accept', 'decline', 'cancel'])
def test_modern_elicitation_null_content_not_silently_treated_as_legacy_compatibility(action):
    from xdog.agent.mcp.elicitation import result
    source = {'params': {'mode': 'url'}}
    value = {'action': action, 'content': None}
    assert result(value, source) == value  # Existing legacy compatibility retained.
    with pytest.raises(McpError, match='cannot be null'):
        result(value, source, modern=True)


@pytest.mark.parametrize('family', ['sampling', 'elicitation'])
@pytest.mark.parametrize('augmentation', [{'task': {}}, {'_meta': {
    'io.modelcontextprotocol/related-task': {'taskId': 'foreign'}}}])
def test_direct_modern_codec_cannot_bypass_removed_task_vocabulary(family, augmentation):
    from xdog.agent.mcp.sampling import request as sampling_request
    source = request() if family == 'sampling' else form()
    source['params'].update(augmentation)
    validate = sampling_request if family == 'sampling' else elicit_request
    with pytest.raises(McpError, match='task augmentation'):
        validate(source, modern=True)
