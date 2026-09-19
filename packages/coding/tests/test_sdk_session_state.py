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

    resumed = create_agent_session(CreateSessionOptions(
        working_dir=tmp_path,
        resume_id=session_id,
    ))

    assert resumed.session.agent.options.thinking == "xhigh"
    assert resumed.session.context_window == 128_000
    assert resumed.session.max_prompt_tokens == 120_000
