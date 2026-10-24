"""The system prompt gets rebuilt before every turn — so it must not crash.

`xdog-coding` 0.57.6 could not process a single message: `_rebuild_system_prompt`
read `agent.state.model.id` where `state.model` is a `str`, and
`agent.state.thinking_level`, which `AgentState` does not have. Both raise
AttributeError, and both sit on the path every turn takes.

Nothing caught it because nothing built a session. The unit tests exercise slash
commands, the skills registry, the settings loader — every part except the one
line of glue that assembles them, which is where the mistake was. So this file
builds a real `AgentSession` around a real `Agent` and calls the method, which is
a cheap thing to do and would have failed loudly on the first run.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from xdog.agent.agent import Agent
from xdog.agent.core import AgentConfig
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionData, SessionManager
from xdog.coding.core.settings_manager import SettingsManager


@pytest.fixture
def session(tmp_path: Path) -> AgentSession:
    async def _never_called(*args: object, **kwargs: object) -> object:  # pragma: no cover
        raise AssertionError("these tests must not reach the model")

    agent = Agent(
        _never_called,
        config=AgentConfig(model="gpt-5.6-sol", system_prompt="placeholder"),
    )
    return AgentSession(
        agent=agent,
        session_data=SessionData(
            session_id="t",
            created_at=0.0,
            updated_at=0.0,
            summary="",
            model="gpt-5.6-sol",
            messages=[],
            settings={},
            branches=[],
        ),
        session_manager=SessionManager(sessions_dir=tmp_path / "sessions"),
        settings=SettingsManager(global_path=tmp_path / "settings.json", project_dir=tmp_path),
        tool_repo=None,
        bash=None,
        working_dir=tmp_path,
    )


def test_rebuilding_the_system_prompt_does_not_raise(session: AgentSession) -> None:
    """The regression. It runs before every turn; an exception here is total."""
    session._rebuild_system_prompt()

    assert session.agent.state.system_prompt
    assert session.agent.state.system_prompt != "placeholder"
    assert "coding agent" in session.agent.state.system_prompt
    assert "Available Tools" in session.agent.state.system_prompt


def test_rebuilding_survives_switching_model_and_thinking_level(
    session: AgentSession,
) -> None:
    """Both fields the broken code read wrongly, exercised after a change.

    `set_thinking_level` writes to `agent.options`, which is where the rebuild
    now reads it from — the crash was reading it off `agent.state`, which never
    had it.
    """
    session.set_model("claude-opus-4-6")
    session.set_thinking_level("high")

    session._rebuild_system_prompt()

    assert session.agent.state.model == "claude-opus-4-6"
    assert session.agent.options.thinking == "high"
    assert session.agent.state.system_prompt

    restored = session.session_manager.load_session(session.session_id)
    assert restored is not None
    assert restored.settings["thinking_level"] == "high"


def test_disabling_thinking_is_persisted(session: AgentSession) -> None:
    session.set_thinking_level("xhigh")
    session.set_thinking_level(None)

    restored = session.session_manager.load_session(session.session_id)
    assert restored is not None
    assert restored.settings["thinking_level"] == "off"


def test_cancel_clears_queued_agent_messages(session: AgentSession) -> None:
    session.agent.follow_up("later")
    session.agent.steer("interrupt")
    assert session.agent.has_queued_messages()

    session.cancel()

    assert not session.agent.has_queued_messages()


def test_activating_a_skill_rebuilds_without_raising(session: AgentSession) -> None:
    """Activation triggers a rebuild from a different entry point."""
    session.activate_skill("does-not-exist")

    assert session.active_skills == {"does-not-exist"}
    assert session.agent.state.system_prompt


def test_expiring_skills_on_an_empty_set_is_a_no_op(session: AgentSession) -> None:
    session._expire_turn_scoped_skills()

    assert session.active_skills == frozenset()
