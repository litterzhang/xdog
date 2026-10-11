"""Tests for AgentSession wrapping agent.Agent."""

import pytest
from xdog.agent import AgentConfig
from xdog.agent.agent import Agent
from xdog.ai.types import (
    AssistantMessage,
    TextContent,
    UserMessage,
)
from xdog.ai.types import AssistantMessage as _AM
from xdog.ai.utils.event_stream import EventStream as _ES
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager


def _noop_stream_fn(m, c, o): return _ES.empty(_AM(content=()))


@pytest.fixture
def tmp_session_dir(tmp_path):
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def session_mgr(tmp_session_dir):
    return SessionManager(sessions_dir=tmp_session_dir)


def test_agent_session_branching(session_mgr, tmp_path):
    session_data = session_mgr.create_session(model="test")

    agent = Agent(_noop_stream_fn, config=AgentConfig(model="test", system_prompt="test"))
    msgs = [
        UserMessage(content="Hello"),
        AssistantMessage(content=(TextContent(text="Hi there"),)),
        UserMessage(content="How are you?"),
        AssistantMessage(content=(TextContent(text="Good"),)),
    ]
    agent.replace_messages(msgs)
    session_data.messages = list(msgs)

    session = AgentSession(
        agent=agent,
        session_data=session_data,
        session_manager=session_mgr,
        settings=SettingsManager(),
        tool_registry=None,
        bash=None,
        working_dir=tmp_path,
    )

    # Create branch at index 1 (after first assistant response)
    branch_id = session.create_branch(at_index=1)
    assert branch_id is not None
    assert len(session.session_data.branches) == 1
    assert session.session_data.branches[0]["model"] == "test"

    # Restore the branch
    assert session.restore_branch(branch_id) is True
    assert len(session.messages) == 2

    # Nonexistent branch
    assert session.restore_branch("fake") is False
