"""One actual Agent run receives independent ordered inputs, not joined text."""
import pytest
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream


def text(message):
    return message.content if isinstance(message.content, str) else ''.join(
        part.text for part in message.content if isinstance(part, TextContent))


def member(identifier, *, origin='human', images=()):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.batches import TurnInput

    return TurnInput(SessionInput(identifier, origin, 'test', identifier, (), {'request': identifier}), images=images)


def deterministic(session, calls):
    def stream(model, context, options):
        calls.append(tuple(context.messages))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='batch answer'),)))
    session.agent.set_stream_fn(stream)


async def test_five_inputs_are_five_history_messages_in_one_turn(coding_session):
    calls = []
    deterministic(coding_session, calls)
    await coding_session._execute_messages(tuple(member(f'message{i}') for i in range(1, 6)), batch_id='batch-five')
    assert len(calls) == 1
    expected = ['message1', 'message2', 'message3', 'message4', 'message5']
    assert [text(m) for m in calls[0] if isinstance(m, UserMessage)] == expected
    saved = coding_session.session_manager.load_session(coding_session.session_id)
    assert [text(m) for m in saved.messages if isinstance(m, UserMessage)] == expected
    assert [text(m) for m in saved.messages if isinstance(m, AssistantMessage)] == ['batch answer']


async def test_batch_lifecycle_is_once_and_inputs_are_structured_not_concatenated(coding_session, monkeypatch):
    calls, events, compactions = [], [], []
    deterministic(coding_session, calls)
    original = coding_session.extensions.event

    async def event(name, payload=None):
        events.append((name, payload))
        return await original(name, payload)

    async def compact():
        compactions.append(True)

    monkeypatch.setattr(coding_session.extensions, 'event', event)
    monkeypatch.setattr(coding_session, '_maybe_compact', compact)
    await coding_session._execute_messages((member('one'), member('two')), batch_id='batch-two')
    starts = [payload for name, payload in events if name == 'turn_start']
    assert len(starts) == 1 and starts[0]['batch_id'] == 'batch-two'
    assert [item['id'] for item in starts[0]['inputs']] == ['one', 'two']
    assert 'user_input' not in starts[0]
    assert [name for name, _ in events].count('turn_end') == 1
    assert compactions == [True]


async def test_mixed_batch_keeps_external_canonical_identity_and_untrusted_projection(coding_session):
    from xdog.agent.core import CustomAgentMessage
    from xdog.agent.session_input import decode_input

    calls = []
    deterministic(coding_session, calls)
    await coding_session._execute_messages((member('human'), member('callback', origin='acp')), batch_id='mixed')
    assert len(calls) == 1
    assert text(calls[0][-2]) == 'human'
    assert 'not a human message' in text(calls[0][-1]) and 'callback' in text(calls[0][-1])
    external = [m for m in coding_session.messages if isinstance(m, CustomAgentMessage)]
    value = decode_input(external[0].data)
    assert value.id == 'callback' and value.origin == 'acp' and value.provenance == {'request': 'callback'}


async def test_mixed_batch_preserves_images_and_input_transform_results(coding_session):
    from xdog.agent import HandledInput
    from xdog.ai.types import ImageContent, Model

    image = ImageContent(data=('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE'
                               'QVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), mime_type='image/png')
    coding_session.model_info = Model(id='test', input=('text', 'image'))
    calls, transformed = [], []
    deterministic(coding_session, calls)

    async def transform(message, source, cancel=None):
        transformed.append(text(message))
        return HandledInput(feedback='locally handled') if text(message) == 'handled' else message

    coding_session.agent.set_input_transform(transform)
    await coding_session._execute_messages((member('handled'), member('image', images=(image,)),
                                            member('external', origin='acp')), batch_id='mixed-images')
    assert transformed == ['handled', 'image']
    assert len(calls) == 1 and len(calls[0]) == 2
    assert calls[0][0].content == (TextContent(text='image'), image)
    assert coding_session.handled_feedback == ['locally handled']
    assert 'External input' in text(calls[0][1])


async def test_all_handled_batch_does_not_invent_a_model_call(coding_session):
    from xdog.agent import HandledInput

    calls = []
    deterministic(coding_session, calls)

    async def handle(message, source, cancel=None):
        return HandledInput(feedback='handled ' + text(message))

    coding_session.agent.set_input_transform(handle)
    response = await coding_session._execute_messages((member('one'), member('two')), batch_id='handled')
    assert response is None and calls == [] and coding_session.messages == []
    assert coding_session.handled_feedback == ['handled one', 'handled two']


async def test_rejected_batch_does_not_partially_admit_earlier_input(coding_session):
    calls = []
    deterministic(coding_session, calls)

    async def transform(message, source, cancel=None):
        if text(message) == 'rejected':
            raise ValueError('rejected input')
        return message

    coding_session.agent.set_input_transform(transform)
    await coding_session._execute_messages((member('one'), member('rejected')), batch_id='rejected')
    assert calls == [] and coding_session.messages == []
    assert 'rejected input' in coding_session.agent.state.error


async def test_singleton_session_input_hook_keeps_legacy_canonical_payload(coding_session, monkeypatch):
    import json

    from xdog.agent.session_input import input_data

    calls, starts = [], []
    deterministic(coding_session, calls)
    original = coding_session.extensions.event

    async def event(name, payload=None):
        if name == 'turn_start':
            starts.append(payload)
        return await original(name, payload)

    monkeypatch.setattr(coding_session.extensions, 'event', event)
    value = member('callback', origin='acp').value
    await coding_session._execute_message(json.dumps(input_data(value)), input_origin='session_input')
    assert json.loads(starts[0]['user_input']) == input_data(value)
    assert starts[0]['inputs'] == [input_data(value)]


async def test_batch_captures_human_context_once_not_once_per_input(coding_session, monkeypatch):
    calls, captures = [], []
    deterministic(coding_session, calls)
    original = coding_session.context_messages.capture

    async def capture():
        captures.append(True)
        return await original()

    monkeypatch.setattr(coding_session.context_messages, 'capture', capture)
    await coding_session._execute_messages((member('one'), member('two'), member('external', origin='acp')),
                                           batch_id='context-once')
    assert captures == [True] and len(calls) == 1


async def test_batch_cancellation_settles_compaction_preparation_before_return(coding_session, monkeypatch):
    import asyncio

    calls = []
    deterministic(coding_session, calls)
    entered, cleaning, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def compact():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await settled.wait()
            raise

    monkeypatch.setattr(coding_session, '_maybe_compact', compact)
    task = asyncio.create_task(coding_session._execute_messages((member('one'), member('two')), batch_id='cancelled'))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        await asyncio.wait_for(cleaning.wait(), 3)
        assert not task.done() and calls == [] and coding_session.messages == []
        settled.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        settled.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_batch_revalidates_images_after_turn_start_before_compaction(coding_session, monkeypatch):
    from xdog.ai.types import ImageContent, Model

    image = ImageContent(data=('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE'
                               'QVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), mime_type='image/png')
    coding_session.model_info = Model(id='vision', input=('text', 'image'))
    calls, compactions = [], []
    deterministic(coding_session, calls)
    original = coding_session.extensions.event

    async def event(name, payload=None):
        if name == 'turn_start':
            coding_session.model_info = Model(id='text', input=('text',))
        return await original(name, payload)

    async def compact():
        compactions.append(True)

    monkeypatch.setattr(coding_session.extensions, 'event', event)
    monkeypatch.setattr(coding_session, '_maybe_compact', compact)
    with pytest.raises(ValueError, match='image input'):
        await coding_session._execute_messages((member('one'), member('image', images=(image,))), batch_id='vision')
    assert calls == [] and compactions == [] and coding_session.messages == []


@pytest.mark.parametrize('mode,executes', [('ask', False), ('allow-all', True)])
async def test_mixed_batch_does_not_bypass_tool_permissions(coding_session, tmp_path, mode, executes):
    from dataclasses import replace

    from xdog.agent import AgentTool, AgentToolResult
    from xdog.ai.types import ToolCall

    target = tmp_path / 'tool-effect'
    requests = []

    async def execute(tool_call_id, params, cancel=None, on_update=None, ctx=None):
        target.write_text('executed')
        return AgentToolResult(content=(TextContent(text='written'),))

    tool = AgentTool(name='batch_write', description='mutating batch test tool',
                     parameters={'type': 'object', 'properties': {}}, execute=execute)
    coding_session.agent.set_tools([tool])
    coding_session.permissions.set_mode(mode)

    def stream(model, context, options):
        requests.append(tuple(context.messages))
        message = (AssistantMessage(content=(ToolCall(id='write', name='batch_write', arguments={}),))
                   if len(requests) == 1 else AssistantMessage(content=(TextContent(text='finished'),)))
        return EventStream.empty(message)

    coding_session.agent.set_stream_fn(stream)
    external = member('callback', origin='acp')
    external = replace(external, value=replace(external.value, provenance={'allow_all': True, 'origin': 'human'}))
    await coding_session._execute_messages((member('human'), external), batch_id='permission-test')
    assert len(requests) == 2 and target.exists() is executes
