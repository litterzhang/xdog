"""Session construction regressions for persisted state and model metadata."""

from __future__ import annotations

from typing import Any

import pytest
from xdog.ai.types import Model
from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session


class _Provider:
    def __init__(self, model: Model) -> None:
        self._model = model

    def models(self) -> tuple[Model, ...]:
        return (self._model,)

    def model(self, model_id: str) -> Model | None:
        short = self._model.id.split("/", 1)[-1]
        if model_id in (self._model.id, short):
            return self._model
        return None

    def stream(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("session construction must not call the model")


@pytest.mark.parametrize("provider_id", ["copilot", "antigravity"])
def test_resume_restores_thinking_and_provider_context_window(tmp_path, monkeypatch, provider_id) -> None:
    import xdog.ai as ai

    model = Model(
        id=f"{provider_id}/test-reasoning-model",
        provider=provider_id,
        reasoning=True,
        context_window=128_000,
        max_prompt_tokens=120_000,
    )
    provider = _Provider(model)
    monkeypatch.setenv("CODING_DIR", str(tmp_path / "coding-data"))
    monkeypatch.setattr(ai, "provider", lambda _name: provider)
    monkeypatch.setattr(ai, "load", lambda: provider)

    created = create_agent_session(CreateSessionOptions(
        working_dir=tmp_path,
        overrides={
            "model": model.id,
            "thinking_level": "xhigh",
        },
    ))
    session_id = created.session.session_id

    assert created.session.agent.options.thinking == "xhigh"
    assert created.session.context_window == 128_000
    assert created.session.max_prompt_tokens == 120_000
    assert created.session.context_limit == 120_000

    with pytest.raises(RuntimeError, match="owned"):
        create_agent_session(CreateSessionOptions(working_dir=tmp_path, resume_id=session_id))
    created.session.dispose()

    resumed = create_agent_session(CreateSessionOptions(
        working_dir=tmp_path,
        resume_id=session_id,
    ))

    assert resumed.session.agent.options.thinking == "xhigh"
    assert resumed.session.context_window == 128_000
    assert resumed.session.max_prompt_tokens == 120_000

    resumed.session.dispose()


def test_skill_repo_construction_failure_is_not_silently_downgraded(monkeypatch):
    from xdog.coding.core.sdk import _coding_skill_repo

    error = OSError("skill catalog cannot be read")
    def fail():
        raise error
    monkeypatch.setattr("xdog.coding.core.slash_commands.skill_repo", fail)
    with pytest.raises(OSError, match="skill catalog cannot be read") as raised:
        _coding_skill_repo()
    assert raised.value is error


def test_resume_ignores_unknown_metadata_without_deriving_access_or_rewriting_history(tmp_path):
    import json

    from xdog.agent.access import AccessState
    from xdog.ai.types import AssistantMessage, TextContent, UserMessage
    from xdog.coding.core.session_manager import SessionManager, SessionUnavailable

    manager = SessionManager(tmp_path / "sessions")
    saved = manager.create_session(model="offline/model", working_dir=tmp_path)
    saved.messages = [UserMessage(content=(TextContent(text="Keep the original request"),)),
                      AssistantMessage(content=(TextContent(text="Keep the original reply"),))]
    current_access = AccessState(read_only=True, revision=3)
    saved.access = current_access.to_dict()
    snapshot = saved.to_dict()
    snapshot.update(legacy_plan=None, plan={"mode": "not-authority"}, unrelated={"future": True})
    snapshot['access']['future_metadata'] = 'ignored'
    path = manager._session_path(saved.session_id)
    path.write_text(json.dumps(snapshot))
    original = path.read_bytes()
    resumed = manager.load_session(saved.session_id)
    assert resumed is not None and resumed.messages == saved.messages
    assert resumed.access == current_access.to_dict() and path.read_bytes() == original
    assert 'plan' not in resumed.to_dict() and 'legacy_plan' not in resumed.to_dict()
    # Unknown metadata never supplies missing current permission state.
    del snapshot['access']
    path.write_text(json.dumps(snapshot))
    with pytest.raises(SessionUnavailable) as rejected:
        manager.load_session(saved.session_id)
    assert 'Missing saved access scope' in str(rejected.value.__cause__)
    assert AccessState.saved(
        {'access': current_access.to_dict(), 'legacy_plan': {'read_only': False}}) == current_access
    with pytest.raises(ValueError, match='Invalid saved access'):
        AccessState.saved({'access': {'read_only': 'false', 'revision': 0}, 'future': True})
