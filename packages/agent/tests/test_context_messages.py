"""Non-triggering custom context: provenance, watermark, callback and disk ownership."""
from __future__ import annotations

import asyncio
import json
import os
import threading
from dataclasses import replace

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.context_messages import ContextMessages
from xdog.agent.core import CustomAgentMessage
from xdog.agent.extensions.context_message import TYPE, display_text, llm_message, proposal
from xdog.agent.extensions.invocation import InvocationLease, _current
from xdog.agent.messages import dicts_to_messages, messages_to_dicts
from xdog.agent.types import HandledInput
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream


def row(identifier='a', **kwargs):
    return {'id': identifier * 32, 'extension': 'fixture', 'session_id': 'original', 'branch_id': '',
            'invocation': 'b' * 32, 'method': 'xdog/command', 'custom_type': 'review-note',
            'content': [{'type': 'text', 'text': 'EXPLICIT CUSTOM CONTENT'}], 'display': True,
            'details': {'secret_metadata': 'NOT MODEL INPUT'}, 'delivery': 'next_turn', **kwargs}


def queue(tmp_path, *, current=None, history=None, persist=None, events=None):
    current = current if current is not None else ['original', '']
    history = history if history is not None else []
    async def save(value):
        history[:] = value
        (tmp_path / 'history.json').write_text(json.dumps(messages_to_dicts(list(value))))
    return ContextMessages(tmp_path / 'pending', scope=lambda: tuple(current), history=lambda: tuple(history),
                           persist=persist or save, publish=(events if events is not None else []).append)


async def drain(agent, text='USER', **kwargs):
    stream = await agent.prompt(text, **kwargs)
    return [event async for event in stream]


def test_custom_roundtrip_is_canonical_and_only_content_reaches_model():
    item = CustomAgentMessage(TYPE, row())
    restored = dicts_to_messages(messages_to_dicts([item]))[0]
    assert restored == item and messages_to_dicts([item])[0]['role'] == 'custom'
    wire = llm_message(restored)
    assert 'fixture / review-note' in str(wire) and 'EXPLICIT CUSTOM CONTENT' in str(wire)
    assert 'NOT MODEL INPUT' not in str(wire) and 'secret_metadata' not in str(wire)
    assert llm_message(CustomAgentMessage('unrelated', {'content': 'do not convert metadata'})) is None
    assert display_text(row(display=False)) == ''
    assert 'NOT MODEL INPUT' not in display_text(row())


@pytest.mark.parametrize('change', [{'display': 1}, {'delivery': 'unsupported'}, {'custom_type': ''},
                                  {'details': object()}, {'content': [{'type': 'toolCall', 'name': 'bash'}]}])
def test_malformed_custom_metadata_or_content_is_refused(change):
    from xdog.agent.extensions.context_message import validate
    with pytest.raises((ValueError, TypeError, RuntimeError)):
        validate({**proposal('note', 'text'), **change})


@pytest.mark.parametrize('success,child', [(True, ''), (False, ''), (True, 'foreign-child')])
async def test_review_stages_only_successful_original_callback_and_never_borrows_parent(tmp_path, success, child):
    owner, calls = queue(tmp_path), []
    async def ask(extension, dialog):
        calls.append(dialog)
        return True
    lease = InvocationLease('fixture', 'original', child, 'xdog/command', lambda: True, object())
    token = _current.set(lease)
    try:
        accepted = await owner.review('fixture', {'id': 'dialog', 'kind': 'session', 'operation': 'context_message',
            'title': 'Review', 'message': '', **proposal('note', 'TEXT')}, ask=ask, alive=lambda: True)
        assert accepted is (not child)
        assert (await owner.capture())[1] == ()
        await lease.complete(success)
        assert bool((await owner.capture())[1]) is (success and not child)
        assert bool(calls) is (not child)
    finally:
        _current.reset(token)


async def test_rpc_schedule_defers_disk_until_full_result_and_rechecks_scope(tmp_path):
    current, commits = ['original', ''], []
    owner = queue(tmp_path, current=current)
    async def ask(*args):
        return True
    async def schedule(commit):
        commits.append(commit)
    lease = InvocationLease('fixture', 'original', '', 'xdog/command', lambda: True, object())
    token = _current.set(lease)
    try:
        assert await owner.review('fixture', {'id': 'd', 'kind': 'session', 'operation': 'context_message',
            'title': 'Review', **proposal('note', 'TEXT')}, ask=ask, alive=lambda: True, schedule=schedule)
        await lease.complete(True)
        assert len(commits) == 1 and not (await owner.capture())[1]
        current[1] = 'another-branch'
        await commits[0]()
        assert not (await owner.capture())[1]
        current[1] = ''
        await commits[0]()  # A discarded scheduled commit cannot later adopt the restored scope.
        assert not (await owner.capture())[1]
    finally:
        _current.reset(token)


@pytest.mark.parametrize('outcome', ['valid', 'handled', 'refused', 'canceled'])
async def test_agent_initial_context_hook_is_after_successful_transform_and_cancel_gate(outcome):
    calls, hooks, order = [], [], []
    def model(_model, context, _options):
        calls.append(context)
        return EventStream.empty(AssistantMessage())
    gate = asyncio.Event()
    async def transform(message, source, cancel):
        order.append(source)
        if outcome == 'handled':
            return HandledInput('Handled without prompt')
        if outcome == 'refused':
            raise ValueError('Refused')
        if outcome == 'canceled':
            gate.set()
            await asyncio.Event().wait()
        return replace(message, content='TRANSFORMED')
    agent = Agent(model, config=AgentConfig(model='test/model'), input_transform=transform)
    async def hook():
        hooks.append('admitted')
        agent.append_message(CustomAgentMessage(TYPE, row()))
    work = asyncio.create_task(drain(agent, initial_prompt_context=hook))
    if outcome == 'canceled':
        await gate.wait()
        agent.abort()
    await work
    assert order == ['prompt']
    assert bool(hooks) is (outcome == 'valid') and bool(calls) is (outcome == 'valid')
    if outcome == 'valid':
        assert isinstance(agent.messages[0], CustomAgentMessage)
        assert 'TRANSFORMED' in str(calls[0].messages) and 'NOT MODEL INPUT' not in str(calls[0].messages)
        agent.steer("LATER EXPLICIT QUEUED INPUT")
        stream = await agent.continue_()
        _ = [event async for event in stream]
        assert hooks == ['admitted']


async def test_watermark_holds_current_input_hook_proposal_until_next_explicit_prompt(tmp_path):
    history, calls = [], []
    owner = queue(tmp_path, history=history)
    def model(_model, context, _options):
        calls.append(context)
        return EventStream.empty(AssistantMessage())
    async def transform(message, source, cancel):
        await owner.enqueue(row(), lambda: True)
        return message
    agent = Agent(model, config=AgentConfig(model='test/model'), input_transform=transform)
    async def persist(value):
        history[:] = value
        agent.replace_messages(list(value))
    owner.history = lambda: agent.messages
    owner.persist = persist
    first = await owner.capture()
    await drain(agent, initial_prompt_context=lambda: owner.admit(first))
    assert not any(isinstance(value, CustomAgentMessage) for value in agent.messages)
    assert 'EXPLICIT CUSTOM CONTENT' not in str(calls[0].messages)
    second = await owner.capture()
    await drain(agent, initial_prompt_context=lambda: owner.admit(second))
    assert sum(isinstance(value, CustomAgentMessage) for value in agent.messages) == 1
    assert 'EXPLICIT CUSTOM CONTENT' in str(calls[1].messages)


@pytest.mark.parametrize('ending', ['cancel', 'owner-switch'])
async def test_blocked_fsync_cancellation_owner_switch_and_second_adapter_cannot_late_commit(
    tmp_path, monkeypatch, ending,
):
    current, events = ['original', ''], []
    owner = queue(tmp_path, current=current, events=events)
    other = queue(tmp_path, current=current)
    entered, release = threading.Event(), threading.Event()
    fsync = os.fsync
    def blocked(fd):
        entered.set()
        assert release.wait(5)
        return fsync(fd)
    monkeypatch.setattr(os, 'fsync', blocked)
    task = asyncio.create_task(owner.enqueue(row(), lambda: True))
    while not entered.is_set():
        await asyncio.sleep(.002)
    with pytest.raises(RuntimeError, match='transaction'):
        await other.enqueue(row('c'), lambda: True)
    if ending == 'cancel':
        task.cancel()
    else:
        current[1] = 'changed'
    await asyncio.sleep(.02)
    assert not task.done()  # Capacity/owner cannot be released while its real disk thread is held.
    release.set()
    with pytest.raises(asyncio.CancelledError if ending == 'cancel' else ValueError):
        await task
    current[1] = ''
    assert not (await other.capture())[1]
    assert not any(event['type'] == 'extension_context_pending' for event in events)
    calls = []
    def model(_model, context, _options):
        calls.append(context)
        return EventStream.empty(AssistantMessage())
    agent = Agent(model, config=AgentConfig(model='test/model'))
    snapshot = await other.capture()
    await drain(agent, initial_prompt_context=lambda: other.admit(snapshot))
    assert calls and 'EXPLICIT CUSTOM CONTENT' not in str(calls[0].messages)


async def test_persisted_restart_dedupes_history_before_pending_retirement(tmp_path, monkeypatch):
    history = []
    owner = queue(tmp_path, history=history)
    await owner.enqueue(row(), lambda: True)
    snapshot = await owner.capture()
    write = owner._write
    def fail_retire(scope, rows, current, replaced):
        if not rows:
            raise OSError('Crash boundary after history, before pending retirement')
        return write(scope, rows, current, replaced)
    monkeypatch.setattr(owner, '_write', fail_retire)
    with pytest.raises(OSError):
        await owner.admit(snapshot)
    restored = dicts_to_messages(json.loads((tmp_path / 'history.json').read_text()))
    saves = []
    async def must_not_repeat(value):
        saves.append(value)
    restarted = queue(tmp_path, history=restored, persist=must_not_repeat)
    await restarted.admit(await restarted.capture())
    assert not saves and len(restored) == 1 and not (await restarted.capture())[1]


async def test_abort_during_initial_context_hook_owns_and_joins_hook_task():
    entered, settled = asyncio.Event(), asyncio.Event()
    calls = []
    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    agent = Agent(model, config=AgentConfig(model='test/model'))
    async def hook():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()
    work = asyncio.create_task(drain(agent, initial_prompt_context=hook))
    await entered.wait()
    agent.abort()
    await asyncio.wait_for(work, 2)
    assert settled.is_set() and not calls and not agent.messages


async def test_cancellation_after_replace_reports_uncertain_and_never_rolls_back(tmp_path, monkeypatch):
    events = []
    owner = queue(tmp_path, events=events)
    entered, release = threading.Event(), threading.Event()
    fsync, count = os.fsync, 0
    def blocked_directory(fd):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
            assert release.wait(5)
        return fsync(fd)
    monkeypatch.setattr(os, 'fsync', blocked_directory)
    task = asyncio.create_task(owner.enqueue(row(), lambda: True))
    while not entered.is_set():
        await asyncio.sleep(.002)
    task.cancel()
    await asyncio.sleep(.02)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner._path(('original', '')).exists()  # Already admitted replacement is not rolled back.
    assert owner.uncertain and events[-1]['status'] == 'uncertain'
    assert not any(event['type'] == 'extension_context_pending' for event in events)
    with pytest.raises(RuntimeError, match='uncertain'):
        await owner.capture()


def test_custom_typed_images_are_content_not_metadata_and_review_exposes_descriptors():
    from dataclasses import asdict

    from packages.agent.tests.test_extension_history_images import IMAGE
    value = proposal('image-note', (IMAGE,), details={'image': 'OPAQUE-NOT-IMAGE'})
    canonical = CustomAgentMessage(TYPE, row(content=value['content'], details=value['details']))
    assert value['content'] == [asdict(IMAGE)]
    projected = llm_message(canonical)
    assert projected.content[-1] == IMAGE and 'OPAQUE-NOT-IMAGE' not in str(projected)
