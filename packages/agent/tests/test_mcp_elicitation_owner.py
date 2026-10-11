"""Request and reply consent are distinct; invalid/private replies never bypass review."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent.mcp.elicitation_owner import ElicitationOwner
from xdog.agent.mcp.stdio import McpError

from packages.agent.tests.test_mcp_elicitation import form


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.002)


async def answer(owner, event, response, lease='view'):
    await owner.answer(event['id'], lease=lease, phase=event['phase'], receipt=event['receipt'],
                       sha256=event['sha256'], response=response)


@pytest.mark.parametrize('release', [True, False])
async def test_request_and_exact_reply_need_distinct_single_use_original_owner_receipts(release):
    events = []
    owner = ElicitationOwner('view', events.append, lambda: True)
    work = asyncio.create_task(owner.handle(form()))
    try:
        await until(lambda: events)
        first = events[0]
        assert first['phase'] == 'request' and not work.done()
        with pytest.raises(McpError, match='owner'):
            await answer(owner, first, {'action': 'accept', 'content': {'name': 'USER'}}, lease='foreign')
        with pytest.raises(McpError):
            await answer(owner, first, {'action': 'accept', 'content': {'name': 'a'}})
        assert not work.done() and len(events) == 2
        corrected = events[1]
        assert corrected['phase'] == 'request' and corrected['receipt'] != first['receipt']
        assert corrected['sha256'] == first['sha256'] and corrected['form_draft'] == {'name': 'a'}
        with pytest.raises(McpError):
            await answer(owner, first, {'action': 'accept', 'content': {'name': 'USER'}})
        await answer(owner, corrected, {'action': 'accept', 'content': {'name': 'PRIVATE REVIEWED'}})
        with pytest.raises(McpError):
            await answer(owner, corrected, {'action': 'accept', 'content': {'name': 'USER'}})
        await until(lambda: len(events) == 3)
        second = events[2]
        assert second['phase'] == 'response' and 'PRIVATE REVIEWED' in str(second['body'])
        assert not work.done()
        with pytest.raises(McpError, match='exact proposed'):
            await answer(owner, second, {'action': 'accept', 'content': {'name': 'changed'}})
        await answer(owner, second, {'action': 'accept' if release else 'cancel'})
        returned = await asyncio.wait_for(work, 2)
        assert returned == ({'action': 'accept', 'content': {'name': 'PRIVATE REVIEWED'}} if release else
                            {'action': 'cancel'})
        assert not owner._jobs and 'PRIVATE' not in str(events[-1])
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('action', ['decline', 'cancel'])
async def test_decline_cancel_ends_without_reply_data_or_external_action(action):
    events = []
    owner = ElicitationOwner('view', events.append, lambda: True)
    work = asyncio.create_task(owner.handle(form()))
    try:
        await until(lambda: events)
        await answer(owner, events[0], {'action': action})
        assert await work == {'action': action}
        assert len(events) == 2 and events[1]['type'] == 'elicitation_closed'
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_pending_review_close_and_timeout_release_capacity_without_data_delivery():
    events = []
    owner = ElicitationOwner('view', events.append, lambda: True, timeout=.1)
    with pytest.raises(TimeoutError):
        await owner.handle(form())
    assert not owner._jobs
    await owner.close()
    owner = ElicitationOwner('view', events.append, lambda: True)
    work = asyncio.create_task(owner.handle(form()))
    try:
        await until(lambda: owner._jobs)
        await owner.close()
        assert not owner._jobs
        with pytest.raises(asyncio.CancelledError):
            await work
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_url_request_accept_requires_exact_response_review_without_browser_or_form_data():
    events = []
    source = {'jsonrpc': '2.0', 'id': 'url', 'method': 'elicitation/create', 'params': {
        'mode': 'url', 'message': 'Review external interaction', 'elicitationId': 'owned',
        'url': 'https://operator.example/flow'}}
    owner = ElicitationOwner('view', events.append, lambda: True)
    work = asyncio.create_task(owner.handle(source))
    try:
        await until(lambda: events)
        assert events[0]['body'] == source
        await answer(owner, events[0], {'action': 'accept'})
        await until(lambda: len(events) == 2)
        assert events[1]['body']['response'] == {'action': 'accept'} and not work.done()
        await answer(owner, events[1], {'action': 'accept'})
        assert await work == {'action': 'accept'}  # No browser helper or URL request was invoked.
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('phase', ['admission', 'answer'])
async def test_close_waits_owned_validation_thread_before_releasing_owner_capacity(monkeypatch, phase):
    import threading

    import xdog.agent.mcp.elicitation_owner as module
    events = []
    entered, release = threading.Event(), threading.Event()
    owner = ElicitationOwner('view', events.append, lambda: True)
    work = submitting = closing = None
    original = module.request if phase == 'admission' else module.result
    def held(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    try:
        if phase == 'admission':
            monkeypatch.setattr(module, 'request', held)
            work = asyncio.create_task(owner.handle(form()))
        else:
            work = asyncio.create_task(owner.handle(form()))
            await until(lambda: events)
            monkeypatch.setattr(module, 'result', held)
            submitting = asyncio.create_task(answer(owner, events[0],
                                                   {'action': 'accept', 'content': {'name': 'USER'}}))
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(owner.close())
        await asyncio.sleep(.03)
        assert not closing.done() and owner._validation_tasks
        release.set()
        await asyncio.wait_for(closing, 2)
        assert not owner._validation_tasks and not owner._jobs and owner._admitting == 0
        assert not any(event.get('phase') == 'response' for event in events)
    finally:
        release.set()
        await asyncio.gather(*(task for task in (work, submitting, closing) if task is not None),
                             return_exceptions=True)
        await owner.close()


@pytest.mark.parametrize('fault', ['none', 'foreign', 'early', 'expired', 'duplicate', 'closed'])
async def test_url_completion_requires_original_reviewed_binding_and_is_notice_only(fault):
    import time
    events = []
    source = {'jsonrpc': '2.0', 'id': 'url', 'method': 'elicitation/create', 'params': {
        'mode': 'url', 'message': 'Review external interaction', 'elicitationId': 'owned',
        'url': 'https://operator.example/flow'}}
    owner = ElicitationOwner('view', events.append, lambda: True)
    work = asyncio.create_task(owner.handle(source))
    notice = {'jsonrpc': '2.0', 'method': 'notifications/elicitation/complete', 'params': {'elicitationId': 'owned'}}
    try:
        await until(lambda: events)
        if fault == 'early':
            owner.completion(notice)
            assert not any(event['type'] == 'elicitation_url_complete' for event in events)
        await answer(owner, events[0], {'action': 'accept'})
        await until(lambda: len(events) == 2)
        await answer(owner, events[1], {'action': 'accept'})
        assert await work == {'action': 'accept'}
        assert events[2]['type'] == 'elicitation_url_pending' and owner._urls
        if fault == 'foreign':
            notice['params']['elicitationId'] = 'another'
        elif fault == 'expired':
            owner._urls['owned'] = time.monotonic() - 1
        elif fault == 'closed':
            await owner.close()
        owner.completion(notice)
        if fault == 'duplicate':
            owner.completion(notice)
        completed = [event for event in events if event['type'] == 'elicitation_url_complete']
        assert len(completed) == (0 if fault in {'foreign', 'expired', 'closed'} else 1)
        assert all(set(event) == {'type', 'elicitation_id', 'notice'} for event in completed)
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)
