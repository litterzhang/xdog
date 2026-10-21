"""Repeated compaction keeps complete named return checkpoints, not only the latest context."""
from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.messages import messages_to_dicts
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager


async def test_multiple_compactions_retain_full_history_links_and_return_heads(tmp_path):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='summary'),)))
    manager = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model', context_window=4000)),
        session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_registry=None, bash=None)
    original = []
    for index in range(6):
        original.extend([UserMessage(content=f'original user {index} ' + 'x' * 2000),
                         AssistantMessage(content=(TextContent(text=f'original answer {index}'),))])
    session.agent.replace_messages(original)
    session._persist()
    try:
        await session.compact()
        first_context = session.messages
        first_id = session.session_data.compaction['recovery_branch_id']
        await session.compact()
        second_id = session.session_data.compaction['recovery_branch_id']
        assert second_id != first_id and len(calls) == 2
        saved = manager.load_session(session.session_id)
        recovered = {entry['branch_id']: entry for entry in saved.branches}
        assert recovered[first_id]['messages'] == messages_to_dicts(original)
        assert recovered[second_id]['messages'] == messages_to_dicts(first_context)
        assert recovered[second_id]['compaction']['recovery_branch_id'] == first_id
        assert recovered[first_id]['label'].startswith('Before compaction #1')
        assert recovered[second_id]['label'].startswith('Before compaction #2')
        assert not saved.active_branch  # Creating checkpoints never auto-activates an old context.
        assert session.restore_branch(first_id)
        assert messages_to_dicts(session.messages) == messages_to_dicts(original)
        returns = [entry for entry in session.session_data.branches if entry['label'].startswith('Return checkpoint')]
        assert len(returns) == 1
        assert session.restore_branch(returns[0]['branch_id'])
        assert session.session_data.compaction['recovery_branch_id'] == second_id
        assert len(calls) == 2
    finally:
        await session.children.runtime.aclose()
        await session.extensions.aclose()
        session.dispose()


async def test_unsafe_recovery_tail_retained_but_not_automatically_resumed(tmp_path):
    manager = SessionManager(tmp_path / 'sessions')
    def stream(*args):
        return EventStream.empty(AssistantMessage(content=(TextContent(text='summary'),)))
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model', context_window=4000)),
        session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_registry=None, bash=None)
    original = [UserMessage(content='old ' + 'x' * 6000), AssistantMessage(content=(TextContent(text='old answer'),)),
                UserMessage(content='latest'), AssistantMessage(content=(ToolCall(id='pending', name='read'),),
                                                               stop_reason='aborted')]
    session.agent.replace_messages(original)
    try:
        await session.compact()
        recovery = session.session_data.compaction['recovery_branch_id']
        checkpoint = next(entry for entry in session.session_data.branches if entry['branch_id'] == recovery)
        assert checkpoint['messages'] == messages_to_dicts(original)
        before = session.messages
        with pytest.raises(ValueError, match='batch'):
            session.restore_branch(recovery)
        assert session.messages == before  # Use the tree's valid earlier checkpoints instead.
    finally:
        await session.children.runtime.aclose()
        await session.extensions.aclose()
        session.dispose()
