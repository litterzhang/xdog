"""Host-bound read-only session inspection, independent of TUI and ACP."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from xdog.agent import AgentContext, BeforeToolCallContext
from xdog.ai.types import AssistantMessage, ToolCall
from xdog.coding.core.slash_commands import execute_command

NAME = 'ext_coding_session'


def tool(session):
    return next(item for item in session.agent.active_tools if item.name == NAME)


def call_context(session, selected):
    return BeforeToolCallContext(AssistantMessage(), ToolCall(id='inspect', name=NAME, arguments={}), {},
                                AgentContext(tools=[selected]))


async def test_tool_and_session_command_share_the_current_readonly_snapshot(coding_session):
    session = coding_session
    try:
        selected = tool(session)
        result = await selected.execute('inspect', {})
        data = json.loads(result.content[0].text)
        assert data == result.details
        assert data['session_id'] == session.session_id
        assert data['model']['id'] == session.model
        assert data['model']['thinking'] == (session.agent.options.thinking or 'off')
        assert data['cwd'] == str(session.working_dir)
        assert data['usage']['source'] == 'unavailable'
        output = (await execute_command('session', '', session)).output
        assert session.model in output and session.session_id in output
        assert 'credentials' not in data and 'messages' not in data and 'settings' not in data
        session.permissions.set_mode('deny')
        data = (await selected.execute('inspect', {})).details
        assert data['permission_mode'] == 'deny'
        assert 'deny' in (await execute_command('session', '', session)).output
    finally:
        await session.aclose()


@pytest.mark.parametrize('mode', ['ask', 'ask-all', 'deny'])
async def test_exact_bound_session_tool_needs_no_tui_or_approval_even_readonly(coding_session, mode):
    session = coding_session
    try:
        session.permissions.set_mode(mode)
        session.access_control(True)
        selected = tool(session)
        assert await session._before_tool_call(call_context(session, selected)) is None
        assert not session.permissions._pending
        fake = replace(selected)
        session.agent.set_tools([fake])
        decision = await session._before_tool_call(call_context(session, fake))
        assert decision is not None and decision.block
        with pytest.raises(RuntimeError, match='binding|owner'):
            await selected.execute('inspect', {})
    finally:
        await session.aclose()


async def test_session_tool_rejects_other_targets_and_stale_navigation_handles(coding_session):
    session = coding_session
    try:
        selected = tool(session)
        with pytest.raises(ValueError):
            await selected.execute('inspect', {'session_id': 'someone-else'})
        await asyncio.to_thread(session.new_session)
        with pytest.raises(RuntimeError, match='binding|owner'):
            await selected.execute('inspect', {})
        assert (await tool(session).execute('inspect', {})).details['session_id'] == session.session_id
        current = tool(session)
        await session.aclose()
        with pytest.raises(RuntimeError, match='binding|owner'):
            await current.execute('inspect', {})
    finally:
        await session.aclose()


async def test_disabled_session_tool_is_not_advertised_or_implicitly_reenabled(coding_session):
    session = coding_session
    try:
        session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
        await session._sync_extension_tools()
        session._rebuild_system_prompt()
        assert not session.agent.active_tools
        prompt = session.agent.state.system_prompt
        assert 'Call `ext_coding_session`' not in prompt
        assert 'not available' in prompt
    finally:
        await session.aclose()


@pytest.mark.parametrize('mode', ['ask', 'ask-all', 'deny'])
async def test_actual_agent_turn_executes_session_tool_without_frontend(coding_session, mode):
    from xdog.ai.types import DoneEvent, TextContent
    from xdog.ai.utils.event_stream import EventStream

    session, calls = coding_session, []
    session.permissions.set_mode(mode)
    session.access_control(True)

    def stream(model, context, options):
        calls.append(context)
        message = (AssistantMessage(content=(ToolCall(id='actual-inspect', name=NAME, arguments={}),),
                                    stop_reason='toolUse') if len(calls) == 1
                   else AssistantMessage(content=(TextContent(text='inspection completed'),)))

        async def generate():
            yield DoneEvent(message=message, stop_reason=message.stop_reason)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)

    session.agent.set_stream_fn(stream)
    try:
        await session.send_message('Inspect the session')
        assert len(calls) == 2
        result = next(message for message in calls[1].messages if message.role == 'toolResult')
        assert not result.is_error
        data = json.loads(result.content[0].text)
        assert data['session_id'] == session.session_id
        assert data['model']['id'] == session.model and data['read_only'] is True
        assert data['permission_mode'] == mode
        assert not session.permissions._pending
    finally:
        await session.aclose()


async def test_child_does_not_inherit_the_parent_bound_inspection_tool(coding_session):
    session = coding_session
    calls = []

    async def capture(spec, publish):
        child = await session.children.create(spec, publish)
        calls.append({item.name for item in child.state.tools})
        return child

    session.children.supervisor.factory = capture
    try:
        identifier = await session.children.supervisor.start('Inspect only')
        await session.children.supervisor.control('wait', identifier)
        assert calls and all(NAME not in names for names in calls)
    finally:
        await session.aclose()
