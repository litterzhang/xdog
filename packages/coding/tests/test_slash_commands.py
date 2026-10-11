"""Tests for slash commands module."""

import pytest
from xdog.agent import AgentConfig
from xdog.ai.types import AssistantMessage as _AM
from xdog.ai.utils.event_stream import EventStream as _ES
from xdog.coding.core.slash_commands import (
    execute_command,
    parse_slash_command,
)


def _noop_stream_fn(m, c, o): return _ES.empty(_AM(content=()))


def test_parse_slash_command():
    assert parse_slash_command("/help") == ("help", "")
    assert parse_slash_command("/model sonnet") == ("model", "sonnet")
    assert parse_slash_command("/thinking high") == ("thinking", "high")
    assert parse_slash_command("hello") is None
    assert parse_slash_command("") is None

@pytest.mark.asyncio
async def test_cmd_quit(agent_session):
    result = await execute_command("quit", "", agent_session)
    assert result.exit_requested
    assert "Goodbye" in result.output

@pytest.mark.asyncio
async def test_cmd_model_switch(agent_session):
    """Test switching model via /model <name>."""
    result = await execute_command("model", "opus", agent_session)
    assert "Switched to model" in result.output
    assert "opus" in result.output

@pytest.mark.asyncio
async def test_cmd_clear(agent_session):
    result = await execute_command("clear", "", agent_session)
    assert "cleared" in result.output.lower()


@pytest.mark.asyncio
async def test_cmd_thinking_sets_level(agent_session):
    result = await execute_command("thinking", "high", agent_session)
    assert result.output == "Thinking level set to: high"
    assert agent_session.agent.options.thinking == "high"


@pytest.mark.asyncio
async def test_cmd_thinking_turns_level_off(agent_session):
    await execute_command("thinking", "xhigh", agent_session)
    result = await execute_command("thinking", "off", agent_session)
    assert result.output == "Thinking level set to: off"
    assert agent_session.agent.options.thinking is None


@pytest.mark.asyncio
async def test_cmd_thinking_rejects_invalid_level(agent_session):
    result = await execute_command("thinking", "deep", agent_session)
    assert "Invalid thinking level" in result.output
    assert agent_session.agent.options.thinking is None


@pytest.mark.asyncio
async def test_cmd_fork(agent_session):
    # Add some messages first
    from xdog.ai.types import AssistantMessage, TextContent, UserMessage
    msgs = [
        UserMessage(content="Hello"),
        AssistantMessage(content=(TextContent(text="Hi"),)),
    ]
    agent_session.agent.replace_messages(msgs)
    agent_session.session_data.messages = list(msgs)

    result = await execute_command("fork", "", agent_session)
    assert "Branch created" in result.output

@pytest.mark.asyncio
async def test_cmd_compact_empty_history_reports_noop_without_model_request(agent_session):
    def unexpected(*args):
        pytest.fail('Empty context compaction must not request a summary')
    agent_session.agent.set_stream_fn(unexpected)
    original = agent_session.agent.state
    result = await execute_command("compact", "", agent_session)
    assert result.output == "No older context to compact; no summary request needed"
    assert agent_session.agent.state is original and not agent_session.messages

# -- Fixtures --

@pytest.fixture
def agent_session(tmp_path):
    """Create a minimal AgentSession for testing."""
    from xdog.agent.agent import Agent
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.settings_manager import SettingsManager

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_mgr = SessionManager(sessions_dir=session_dir)
    session_data = session_mgr.create_session(model="test")

    agent = Agent(_noop_stream_fn, config=AgentConfig(system_prompt="test"))

    return AgentSession(
        agent=agent,
        session_data=session_data,
        session_manager=session_mgr,
        settings=SettingsManager(),
        tool_registry=None,
        bash=None,
        working_dir=tmp_path,
    )

@pytest.fixture
def agent_session_with_models(tmp_path):
    """Create an AgentSession with a model name."""
    from xdog.agent.agent import Agent
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.settings_manager import SettingsManager

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_mgr = SessionManager(sessions_dir=session_dir)
    session_data = session_mgr.create_session(model="sonnet")

    agent = Agent(_noop_stream_fn, config=AgentConfig(system_prompt="test", model="sonnet"))

    return AgentSession(
        agent=agent,
        session_data=session_data,
        session_manager=session_mgr,
        settings=SettingsManager(),
        tool_registry=None,
        bash=None,
        working_dir=tmp_path,
    )
