"""Canonical queue ordering, durability and revoked producer regression tests."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.core import CustomAgentMessage
from xdog.agent.custom_delivery import bind
from xdog.agent.extensions.context_message import proposal
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream

from packages.agent.tests.test_context_messages import queue, row


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('ending', ['success', 'persist-fails', 'revoked', 'cancel'])
async def test_existing_tool_boundary_canonical_checkpoint_and_no_producer_replay(tmp_path, delivery, ending):
    calls, checkpoints, executed = [], [], []
    alive = [True]
    async def persist(history):
        if ending == 'persist-fails':
            raise OSError('Injected canonical checkpoint failure')
        checkpoints.append(tuple(history))
    store = queue(tmp_path, persist=persist)
    async def validate(message):
        assert isinstance(message, CustomAgentMessage)
    async def first(*args, **kwargs):
        executed.append('first')
        await store.deliver(row(delivery=delivery, display=False), lambda: alive[0], None)
        if ending == 'revoked':
            alive[0] = False
        if ending == 'cancel':
            agent.abort()
        return AgentToolResult(content=(TextContent(text='ORIGINAL TOOL RESULT'),))
    async def second(*args, **kwargs):
        executed.append('second')
        return AgentToolResult(content=(TextContent(text='SECOND TOOL RESULT'),))
    tools = tuple(AgentTool(name=name, label=name, description=name, parameters={'type': 'object'}, execute=fn)
                  for name, fn in [('first', first), ('second', second)])
    def model(model, context, options):
        calls.append(context)
        if 'EXPLICIT CUSTOM CONTENT' in str(context.messages):
            assert checkpoints, 'Custom must be durable before any model observes it'
            assert 'NOT MODEL INPUT' not in str(context.messages)
        result = (AssistantMessage(content=(ToolCall(id='first', name='first', arguments={}),
                                           ToolCall(id='second', name='second', arguments={})))
                  if len(calls) == 1 else AssistantMessage(content=(TextContent(text='DONE'),)))
        return EventStream.empty(result)
    agent = Agent(model, tools=tools, config=AgentConfig(model='test/model', tool_execution='sequential'))
    bind(store, agent, validate)
    stream = await agent.prompt('ONLY ORIGINAL USER')
    async with asyncio.timeout(5):
        _ = [event async for event in stream]
    assert not agent._custom_queue and not agent._steering_queue and not agent._follow_up_queue
    custom = [item for item in agent.messages if isinstance(item, CustomAgentMessage)]
    assert len(custom) == int(ending in ('success', 'persist-fails'))
    assert sum(isinstance(item, UserMessage) for item in agent.messages) == 1
    observed = ['EXPLICIT CUSTOM CONTENT' in str(context.messages) for context in calls]
    if ending == 'success':
        assert executed == ['first', 'second']  # Existing Agent steering waits for the tool batch.
        assert observed == ([False, True] if delivery == 'steer' else [False, False, True])
    else:
        assert not any(observed)
    assert store.uncertain is (ending == 'persist-fails')


def test_next_turn_true_stays_nontriggering_and_strict_bool():
    assert proposal('note', 'NEXT', delivery='next_turn', trigger_turn=True)['trigger_turn'] is False
    with pytest.raises(ValueError):
        proposal('note', 'NEXT', delivery='next_turn', trigger_turn=1)
