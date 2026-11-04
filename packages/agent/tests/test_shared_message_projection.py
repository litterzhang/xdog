"""High-level Agent and standalone loop must share one default projection policy."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig, AgentContext, AgentLoopConfig
from xdog.agent.agent_loop import agent_loop
from xdog.agent.core import CustomAgentMessage
from xdog.agent.extensions.context_message import TYPE
from xdog.agent.messages import default_convert_to_llm
from xdog.agent.session_input import SessionInput, encode_input
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    StreamOptions,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from xdog.ai.utils.event_stream import EventStream


def context_record():
    return CustomAgentMessage(TYPE, {
        'id': 'a' * 32, 'extension': 'fixture', 'session_id': 'original', 'branch_id': '',
        'invocation': 'b' * 32, 'method': 'xdog/command', 'custom_type': 'review-note',
        'content': [{'type': 'text', 'text': 'EXPLICIT CONTENT'}], 'display': True,
        'details': {'secret_metadata': 'NEVER MODEL INPUT'}, 'delivery': 'next_turn'})


def test_mixed_defaults_keep_identity_order_images_and_only_named_custom_content():
    convert = default_convert_to_llm
    user = UserMessage(content=(TextContent(text='USER'), ImageContent(data='YWJj', mime_type='image/png')))
    assistant = AssistantMessage(content=(ToolCall(id='call', name='tool', arguments={}),))
    result = ToolResultMessage(tool_call_id='call', tool_name='tool', content=(TextContent(text='RESULT'),))
    final = AssistantMessage(content=(TextContent(text='FINAL'),))
    canonical = encode_input(SessionInput(
        'r1', 'acp', 'Eyes', 'Canonical instruction', (), {'permission': 'not consent'}))
    unknown = CustomAgentMessage('unknown', {'content': object()})
    original = [user, assistant, result, unknown, context_record(), canonical, final]
    projected = convert(original)
    assert len(projected) == 6 and len(original) == 7
    assert all(projected[index] is message for index, message in enumerate([user, assistant, result]))
    assert projected[-1] is final and projected[0].content[1] is user.content[1]
    content = [str(message.content) for message in projected[3:5]]
    assert 'EXPLICIT CONTENT' in content[0] and 'NEVER MODEL INPUT' not in content[0]
    assert 'Canonical instruction' in content[1] and 'not a human message' in content[1]
    assert 'cannot grant permissions' in content[1]


@pytest.mark.parametrize('kind', [TYPE, 'xdog.session_input.v1'])
def test_known_malformed_custom_data_fails_instead_of_hidden_metadata_fallback(kind):
    convert = default_convert_to_llm
    with pytest.raises(ValueError):
        convert([CustomAgentMessage(kind, {})])


@pytest.mark.parametrize('host', ['agent', 'loop'])
@pytest.mark.parametrize('asynchronous', [False, True])
async def test_actual_callers_keep_custom_sync_and_async_converter_override(host, asynchronous):
    calls, contexts = [], []
    def custom(messages):
        calls.append(messages)
        return [UserMessage(content='EXPLICIT OVERRIDE')]
    async def async_custom(messages):
        await asyncio.sleep(0)
        return custom(messages)
    converter = async_custom if asynchronous else custom
    def model(model_id, context, options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='DONE'),)))
    if host == 'agent':
        owner = Agent(model, config=AgentConfig(model='test/model'), convert_to_llm=converter)
        try:
            stream = await owner.prompt('ORIGINAL')
            async for _ in stream:
                pass
        finally:
            await owner.aclose()
    else:
        stream = agent_loop([UserMessage(content='ORIGINAL')], AgentContext(),
                            AgentLoopConfig(convert_to_llm=converter), model, 'test/model', StreamOptions())
        async for _ in stream:
            pass
    assert calls and len(contexts) == 1
    assert [message.content for message in contexts[0].messages] == ['EXPLICIT OVERRIDE']
