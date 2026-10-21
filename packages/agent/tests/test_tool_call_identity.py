"""Ambiguous provider IDs never reach policy hooks or effects, in either mode."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream


@pytest.mark.parametrize('execution', ['parallel', 'sequential'])
@pytest.mark.parametrize('streamed', [False, True])
async def test_duplicate_batch_ids_refuse_before_any_validation_approval_or_effect(execution, streamed):
    effects, hooks, validations = [], [], []
    async def execute(*args, **kwargs):
        effects.append(args)
        return AgentToolResult.from_text('MUST NOT RUN')
    def validate(args):
        validations.append(args)
    async def before(ctx, cancel=None):
        hooks.append(ctx.tool_call)
    malformed = AssistantMessage(response_id='UNTRUSTED', content=(
        TextContent(text='provider proposed effects'),
        ToolCall(id='same', name='mutation', arguments={'action': 'write'}),
        ToolCall(id='same', name='mutation', arguments={'action': 'read'})))
    def model(*args):
        if not streamed:
            return EventStream.empty(malformed)
        async def events():
            yield StartEvent(partial=malformed)
            yield DoneEvent(message=malformed)
        future = asyncio.get_running_loop().create_future()
        future.set_result(malformed)
        return EventStream.from_async_generator(events(), result_future=future)
    tool = AgentTool('mutation', parameters={'type':'object'}, execute=execute, validate_arguments=validate)
    agent = Agent(model, config=AgentConfig(model='test/fake', tool_execution=execution), tools=[tool],
                  before_tool_call=before)
    try:
        async for _ in await agent.prompt('task'):
            pass
        assert not effects and not hooks and not validations
        assert 'duplicate tool-call IDs' in agent.state.error
        final = agent.messages[-1]
        assert final.stop_reason == 'error' and final.response_id is None
        assert not any(isinstance(part, ToolCall) for part in final.content)
        assert not any(message.role == 'toolResult' for message in agent.messages)
        restored = Agent(model)
        restored.restore(agent.dump())
        assert restored.messages == agent.messages
        await restored.aclose()
    finally:
        await agent.aclose()
