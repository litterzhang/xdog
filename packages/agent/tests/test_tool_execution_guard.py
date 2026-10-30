"""Host policy is checked at actual entry, after all parallel preparation."""
import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, ToolExecutionGuard
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from xdog.ai.utils.event_stream import EventStream


@pytest.mark.parametrize('mode', ['sequential', 'parallel'])
async def test_guard_rechecks_prepared_calls_without_repeating_async_preparation(mode):
    allowed = True
    prepared, guarded, executed = [], [], []
    requests = 0
    def model(name, context, options):
        nonlocal requests
        requests += 1
        if requests == 1:
            return EventStream.empty(AssistantMessage(content=(
                ToolCall(id='first', name='tool', arguments={}),
                ToolCall(id='second', name='tool', arguments={}),), stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    async def before(ctx, cancel):
        nonlocal allowed
        prepared.append(ctx.tool_call.id)
        if ctx.tool_call.id == 'second':
            allowed = False
    def guard(ctx):
        guarded.append(ctx.tool_call.id)
        assert ctx.args is ctx.tool_call.arguments or ctx.args == ctx.tool_call.arguments
        if not allowed:
            raise ValueError('Host grant revoked')
    callback: ToolExecutionGuard = guard
    async def execute(call_id, args, *rest, **kwargs):
        executed.append(call_id)
        return AgentToolResult.from_text('executed')
    agent = Agent(model, config=AgentConfig(model='test/model', tool_execution=mode),
                  tools=[AgentTool('tool', '', {'type': 'object'}, execute=execute)],
                  before_tool_call=before, tool_execution_guard=callback)
    await (await agent.prompt('run')).result()
    assert prepared == guarded == ['first', 'second']
    assert executed == ([] if mode == 'parallel' else ['first'])
    results = [row for row in agent.messages if isinstance(row, ToolResultMessage)]
    assert results[-1].is_error and 'Host grant revoked' in str(results[-1].content)


def test_async_guard_is_rejected_at_construction():
    async def guard(ctx):
        pass
    with pytest.raises(ValueError, match='synchronous'):
        Agent(lambda *args: None, tool_execution_guard=guard)


async def test_guard_cannot_silently_return_false():
    executed = []
    requests = 0
    def model(name, context, options):
        nonlocal requests
        requests += 1
        if requests == 1:
            return EventStream.empty(AssistantMessage(content=(
                ToolCall(id='first', name='tool', arguments={}),), stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    async def execute(*args, **kwargs):
        executed.append(True)
        return AgentToolResult.from_text('must not execute')
    def guard(ctx):
        return False
    agent = Agent(model, config=AgentConfig(model='test/model'),
                  tools=[AgentTool('tool', '', {'type': 'object'}, execute=execute)], tool_execution_guard=guard)
    await (await agent.prompt('run')).result()
    assert not executed
    results = [row for row in agent.messages if isinstance(row, ToolResultMessage)]
    assert results[0].is_error and 'must return None or raise' in str(results[0].content)
