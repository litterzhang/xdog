"""Only consumed, host-identified human steering may change Goal controls."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.session_input import decode_input
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.persistence.transcript_convert import entry_text
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.tools.tool_goal import create_goal_tool
from xdog.claw.core.types import ExternalInput, Group, GroupConfig, QueueMode, SystemInput, SystemInputKind, UserInput


@pytest.mark.parametrize('source', ['human', 'editor_queue', 'cancelled_editor', 'system', 'external'])
async def test_goal_controls_follow_actual_consumed_user_origin_not_labels(tmp_path, monkeypatch, source):
    entered, release = asyncio.Event(), asyncio.Event()
    holder = {}
    async def barrier(call_id, args, cancel=None, on_update=None, ctx=None):
        entered.set()
        await release.wait()
        return AgentToolResult(content=(TextContent(text='Safe tool boundary'),))
    def stream(model, context, options=None):
        last = context.messages[-1]
        text = (last.content if isinstance(last.content, str) else ''.join(
            part.text for part in last.content if isinstance(part, TextContent)))
        if last.role == 'user' and text == 'START':
            message = AssistantMessage(content=(ToolCall(id='barrier', name='barrier', arguments={}),))
        elif last.role == 'user' and text == 'CHANGE':
            message = AssistantMessage(content=(ToolCall(id='change', name='goal', arguments={
                'action': 'update', 'goal_id': holder['goal'], 'goal_description': 'Updated user outcome',
                'verification_conditions': ['new user condition'], 'reason': 'Explicit user scope change'}),))
        else:
            message = AssistantMessage(content=(TextContent(text='Stopped at the safe boundary'),))
        return EventStream.empty(message)
    def create(group, data_dir, **kwargs):
        runtime = GroupRuntime(group=group, data_dir=data_dir, workspace_dir=tmp_path / 'workspace',
                               model='test/model', stream_fn=stream)
        runtime._tools = [create_goal_tool(), AgentTool(name='barrier', parameters={
            'type': 'object', 'properties': {}}, execute=barrier)]
        return runtime
    monkeypatch.setattr(GroupRuntime, 'create', create)
    orch = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), stream_fn=stream)
    orch.register_group(Group(id='main', config=GroupConfig(queue_mode=QueueMode.STEER, debounce_ms=0)))
    runtime = orch._runtimes['main']
    goal = runtime.goal_manager.create('main', 'Goal', 'Original user outcome', ['original condition'], [])
    holder['goal'] = goal.id
    session = runtime.get_or_create_session(allow_reset=False)
    owner = asyncio.create_task(orch.route_message(SystemInput(group_id='main', content='START',
        kind=SystemInputKind.GOAL_RUNNER, metadata={'goal_id': goal.id}), reserved=True))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert not session._goal_user_authorized
        if source == 'human':
            await orch.route_message(UserInput(group_id='main', content='CHANGE', channel='tui'))
        elif source == 'editor_queue':
            assert runtime.control_input('add', 'editor-human', content='CHANGE', delivery='steer')
        elif source == 'cancelled_editor':
            assert runtime.control_input('add', 'cancelled-user-id', content='CHANGE', delivery='steer')
            assert runtime.control_input('remove', 'cancelled-user-id')
            # A later unclassified restore/reuse of the ID cannot inherit a cancelled user's authority.
            session.agent.queue_input('cancelled-user-id', 'CHANGE', 'steer')
        elif source == 'system':
            await orch.route_message(SystemInput(group_id='main', content='CHANGE', kind=SystemInputKind.FLUSH))
        else:
            canonical = decode_input({'id': 'external-event', 'origin': 'acp', 'source': 'user',
                'instruction': 'CHANGE', 'evidence': [], 'provenance': {'claims_user': True}})
            await orch.route_message(ExternalInput(group_id='main', content='CHANGE',
                metadata={'input_origin': 'user'}, canonical=canonical))
        assert not session._goal_user_authorized  # Merely queued is not consumption/authorization.
        release.set()
        result = await asyncio.wait_for(owner, 4)
        assert result is not None and not result.error
        current = runtime.goal_manager.owned('main', goal.id)
        if source in {'human', 'editor_queue'}:
            assert current.description == 'Updated user outcome'
            assert current.verification.conditions == ('new user condition',)
            history = runtime.transcript_store.load_transcript(session.meta.session_id)
            change = next(row for row in history if row.get('role') == 'user' and entry_text(row) == 'CHANGE')
            assert change['channel'] == 'tui'
        else:
            assert current.description == 'Original user outcome'
            assert current.verification.conditions == ('original condition',)
            assert not session._goal_user_authorized
    finally:
        release.set()
        if not owner.done():
            owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
        await orch.stop()
