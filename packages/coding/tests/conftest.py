"""Shared real coding session fixture with a deterministic model."""

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager


@pytest.fixture
def coding_session(tmp_path):
    manager = SessionManager(sessions_dir=tmp_path / "sessions")
    data = manager.create_session(model="test", working_dir=tmp_path)
    value = AgentSession(
        agent=Agent(lambda *_: EventStream.empty(AssistantMessage(content=())), config=AgentConfig(model="test")),
        session_data=data,
        session_manager=manager,
        settings=SettingsManager(),
        tool_registry=None,
        bash=None,
        working_dir=tmp_path,
    )
    yield value
    value.dispose()
