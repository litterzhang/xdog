from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, ToolExecutionEndEvent
from xdog.agent.extensions.tool_result import host_result, normalize_result, worker_result
from xdog.ai.types import AssistantMessage, ImageContent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream


@pytest.mark.parametrize('mode', ['parallel', 'sequential'])
async def test_rich_error_result_preserves_content_details_and_hook_error_status(mode):
    contexts, hooks = [], []
    data = (TextContent(text='error text'), ImageContent(data='aGVsbG8=', mime_type='image/png'))

    async def execute(*args, **kwargs):
        return AgentToolResult(content=data, details={'diagnostic': [False, None]}, is_error=True)

    async def after(context, cancel):
        hooks.append(context)
        return None

    def stream(*args):
        contexts.append(args[1])
        return EventStream.empty(AssistantMessage(content=(ToolCall(id='one', name='diagnostic', arguments={}),),
            stop_reason='toolUse') if len(contexts) == 1 else AssistantMessage(content=(TextContent(text='done'),)))

    agent = Agent(stream, config=AgentConfig(model='test/model', tool_execution=mode),
        tools=[AgentTool(name='diagnostic', parameters={'type': 'object'}, execute=execute)], after_tool_call=after)
    events = [event async for event in await agent.prompt('run')]
    result = next(message for message in contexts[-1].messages if message.role == 'toolResult')
    assert result.is_error and result.content == data and result.details == {'diagnostic': [False, None]}
    assert hooks[0].is_error and hooks[0].result.is_error
    end = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
    assert end.is_error and end.result.is_error and end.result.content == data


@pytest.mark.parametrize('flag', [1, 'true', None])
def test_extension_error_wire_requires_explicit_boolean_not_coercion(flag):
    with pytest.raises(ValueError):
        normalize_result({'content': [{'type': 'text', 'text': 'private'}], 'isError': flag})


def test_typed_error_and_false_error_wire_survive_both_extension_boundaries():
    result = AgentToolResult(content=(TextContent(text='failed'),), details={'recovery': False}, is_error=True)
    assert normalize_result(result)['isError'] is True
    host = host_result(worker_result(result))
    assert host['isError'] is True and host['xdog_result_details'] == {'recovery': False}
    assert normalize_result({'content': [{'type': 'text', 'text': 'successful'}], 'isError': False})['isError'] is False


def test_tool_error_flag_is_not_a_successful_command_or_queued_host_action_receipt():
    from xdog.agent.extensions.command_result import ExtensionCommandResult, normalize_command_result
    result = {'content': [{'type': 'text', 'text': 'failed'}], 'isError': True}
    with pytest.raises(ValueError, match='command artifacts'):
        normalize_command_result(result)
    with pytest.raises(ValueError, match='command artifacts'):
        ExtensionCommandResult.from_dict({'extension': 'rich', 'command': 'ext-rich-show', 'result': result})
