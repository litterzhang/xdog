"""Regression tests for the onboarding model picker."""
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import xdog.ai as ai
from click.testing import CliRunner
from xdog.ai.types import Model
from xdog.claw.cli.cli import cli
from xdog.claw.config import load_config


def _model(name: str, *, model_type: str = "chat") -> Model:
    return Model(
        id=f"copilot/{name}",
        name=name,
        provider="copilot",
        api="openai-responses",
        base_url="https://example.invalid",
        model_type=model_type,
    )


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Mock:
    runtime = Mock()
    runtime.active_providers.return_value = ["copilot"]
    runtime.sync_models = AsyncMock(return_value=())
    runtime.models.return_value = ()
    monkeypatch.setattr(ai, "load", lambda: runtime)
    monkeypatch.setenv("CLAW_DIR", str(tmp_path))
    return runtime


@pytest.mark.parametrize("include_sonnet", [False, True])
def test_onboard_lists_models_beyond_old_limits(
    runtime: Mock, tmp_path: Path, include_sonnet: bool,
) -> None:
    models = [_model(f"test-model-{i}") for i in range(12)]
    if include_sonnet:
        models[0] = _model("claude-sonnet-4.5")
    models.append(_model("gpt-6-astra"))
    models.insert(0, _model("text-embedding-test", model_type="embeddings"))
    runtime.sync_models.return_value = tuple(models)

    result = CliRunner().invoke(cli, ["onboard"], input="n\n13\nClaw\n")

    assert result.exit_code == 0, result.output
    assert "13. copilot/gpt-6-astra" in result.output
    assert "text-embedding-test" not in result.output
    assert load_config(tmp_path / "config.yaml").model == "copilot/gpt-6-astra"
    runtime.sync_models.assert_awaited_once_with(force=True)


def test_onboard_reprompts_for_out_of_range_choice(runtime: Mock, tmp_path: Path) -> None:
    runtime.sync_models.return_value = (_model("gpt-6-astra"),)

    result = CliRunner().invoke(cli, ["onboard"], input="n\n0\n2\n1\nClaw\n")

    assert result.exit_code == 0, result.output
    assert result.output.count("Error:") == 2
    assert load_config(tmp_path / "config.yaml").model == "copilot/gpt-6-astra"


def test_onboard_falls_back_to_cached_models(runtime: Mock, tmp_path: Path) -> None:
    runtime.sync_models.side_effect = RuntimeError("Provider unavailable")
    runtime.models.return_value = (_model("gpt-6-astra"),)

    result = CliRunner().invoke(cli, ["onboard"], input="n\n1\nClaw\n")

    assert result.exit_code == 0, result.output
    assert load_config(tmp_path / "config.yaml").model == "copilot/gpt-6-astra"
    runtime.models.assert_called_once_with()


def test_onboard_allows_manual_entry_without_chat_models(runtime: Mock, tmp_path: Path) -> None:
    runtime.sync_models.return_value = (_model("embedding", model_type="embeddings"),)

    result = CliRunner().invoke(cli, ["onboard"], input="n\ncopilot/gpt-6-astra\nClaw\n")

    assert result.exit_code == 0, result.output
    assert "Enter model name" in result.output
    assert load_config(tmp_path / "config.yaml").model == "copilot/gpt-6-astra"
