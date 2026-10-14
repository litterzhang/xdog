from __future__ import annotations

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.tools import declaration
from xdog.agent.mcp.stdio import McpError


def test_owned_tool_schema_callback_replacement_disposal_identity_and_validation():
    api = ExtensionAPI({})
    def callback(*args):
        return 'data'
    first = api.tool('target', 'original', {'type': 'object'}, callback)
    before = api._tool_ids['target']
    newer = api.tool('target', 'replacement', {'type': 'object'}, callback, replace=True)
    assert api._tool_ids['target'] != before
    first()
    assert api.tools['target'][0] == 'replacement'
    newer()
    newer()
    assert not api.tools and not api._tool_ids
    with pytest.raises(McpError):
        api.tool('target', 'invalid schema', {'type': 'unknown'}, callback)
    assert not api.tools


@pytest.mark.parametrize('changes', [{'revision': True}, {'tools': [{'name': 'x', 'registration_id': 'bad'}]},
    {'callback': 'not wire data'}, {'tools': [{'name': 'x', 'registration_id': 'a' * 32},
                                           {'name': 'X', 'registration_id': 'b' * 32}]}])
def test_invalid_tool_metadata_cannot_create_or_coerce_registration_identity(changes):
    with pytest.raises(ValueError):
        declaration({**ExtensionAPI({}).tool_snapshot(), **changes})


async def test_worker_definitively_refuses_stale_registration_without_invoking_replacement_callback():
    from xdog.agent.extensions.worker import dispatch
    api = ExtensionAPI({})
    calls = []
    async def callback(args, context):
        calls.append(args)
        return 'must not execute'
    api.tool('target', 'old', {'type': 'object'}, callback)
    old = api._tool_ids['target']
    api.tool('target', 'new', {'type': 'object'}, callback, replace=True)
    result = await dispatch(api, 'tools/call', {'name': 'target', 'arguments': {}, '_xdog_tool_registration': old})
    assert result['isError'] is True and not calls and 'callback not invoked' in result['content'][0]['text']


async def test_same_agent_loop_reads_current_inventory_after_tool_registration_changes():
    from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    contexts, executed = [], []
    async def first(*args, **kwargs):
        executed.append('first')
        owner.set_tools((next_tool,))
        return AgentToolResult(content=(TextContent(text='new tool registered'),))
    async def second(*args, **kwargs):
        executed.append('second')
        return AgentToolResult(content=(TextContent(text='done'),))
    next_tool = AgentTool(name='second', parameters={'type': 'object'}, execute=second)
    def stream(model, context, options):
        contexts.append(context)
        names = {tool.name for tool in context.tools or ()}
        if len(contexts) == 1:
            assert names == {'first'}
            message = AssistantMessage(content=(ToolCall(id='one', name='first', arguments={}),))
        elif len(contexts) == 2:
            assert names == {'second'}
            message = AssistantMessage(content=(ToolCall(id='two', name='second', arguments={}),))
        else:
            message = AssistantMessage(content=(TextContent(text='complete'),))
        return EventStream.empty(message)
    owner = Agent(stream, config=AgentConfig(model='test/model'),
                  tools=(AgentTool(name='first', parameters={'type': 'object'}, execute=first),))
    async for _event in await owner.prompt('run'):
        pass
    assert executed == ['first', 'second'] and len(contexts) == 3
