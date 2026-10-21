"""Reviewed Coding selection persists without restoring permissions or broad defaults."""
from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionData, SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.tools import get_default_tools


async def create(tmp_path, data=None):
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    store = SessionManager(tmp_path / 'sessions')
    data = data or store.create_session(model='offline/model', working_dir=tmp_path)
    agent = Agent(stream, config=AgentConfig(model='offline/model'), tools=get_default_tools(tmp_path))
    owner = AgentSession(agent=agent,
        session_data=data, session_manager=store, settings=SettingsManager(global_path=tmp_path / 'settings.json'),
        working_dir=tmp_path, tool_registry=None, bash=None, permissions=PermissionManager('deny'))
    return owner, calls


async def close(owner):
    await owner.children.runtime.aclose()
    await owner.extensions.aclose()
    owner.dispose()


def test_invalid_persisted_selection_refuses_instead_of_becoming_defaults() -> None:

    def _check_case(bad):
        with pytest.raises(ValueError):
            SessionData.from_dict({"session_id": "saved", "active_tools": bad})

    for bad in [False, {}, ["same", "same"], [""], [1]]:
        _check_case(bad)
