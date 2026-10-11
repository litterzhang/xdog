from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream


@pytest.mark.parametrize('capability', [False, True, None])
async def test_context_hook_registration_from_empty_inventory_preserves_model_api_capability_gate(capability):
    contexts, executions = [], []
    async def execute(*args, **kwargs):
        executions.append('unexpected')
        return AgentToolResult(content=(TextContent(text='data'),))
    tool = AgentTool(name='live', description='retained local inventory',
                     parameters={'type': 'object'}, execute=execute)
    async def transform(messages):
        owner.set_tools((tool,))
        return messages
    def stream(model, context, options):
        contexts.append(context)
        if capability is False:
            assert context.tools is None
        else:
            assert context.tools is not None and [item.name for item in context.tools] == ['live']
        return EventStream.empty(AssistantMessage(content=(TextContent(text='finished'),)))
    owner = Agent(stream, config=AgentConfig(model='test/model', supports_tool_calls=capability),
                  transform_context=transform)
    async for _event in await owner.prompt('go'):
        pass
    assert owner.state.tools == (tool,) and not executions and len(contexts) == 1


async def test_unsupported_model_cannot_execute_fabricated_native_tool_call_after_inventory_refresh():
    contexts, executions = [], []
    async def execute(*args, **kwargs):
        executions.append('must not execute')
        return AgentToolResult(content=(TextContent(text='unsafe'),))
    tool = AgentTool(name='live', parameters={'type': 'object'}, execute=execute)
    async def transform(messages):
        owner.set_tools((tool,))
        return messages
    def stream(model, context, options):
        contexts.append(context)
        assert context.tools is None
        message = (AssistantMessage(content=(ToolCall(id='fabricated', name='live', arguments={}),))
                   if len(contexts) == 1 else AssistantMessage(content=(TextContent(text='stopped'),)))
        return EventStream.empty(message)
    owner = Agent(stream, config=AgentConfig(model='test/model', supports_tool_calls=False),
                  transform_context=transform, tools=(tool,))
    async for _event in await owner.prompt('go'):
        pass
    result = next(message for message in contexts[-1].messages if message.role == 'toolResult')
    assert result.is_error and not executions and owner.state.tools == (tool,)
