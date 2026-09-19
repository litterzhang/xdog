"""Regression tests for the onboarding model picker."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import xdog.ai as ai
from click.testing import CliRunner
from xdog.ai.types import Model
from xdog.claw.cli.cli import cli
from xdog.claw.config import ClawConfig, GroupDef, load_config, save_config


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

    result = CliRunner().invoke(cli, ["onboard"], input="n\n13\nnone\nClaw\n")

    assert result.exit_code == 0, result.output
    assert "13. copilot/gpt-6-astra" in result.output
    assert "text-embedding-test" not in result.output
    assert load_config(tmp_path / "config.yaml").model == "copilot/gpt-6-astra"
    runtime.sync_models.assert_awaited_once_with(force=True)


def test_onboard_reprompts_for_out_of_range_choice(runtime: Mock, tmp_path: Path) -> None:
    runtime.sync_models.return_value = (_model("gpt-6-astra"),)

    result = CliRunner().invoke(cli, ["onboard"], input="n\n0\n2\n1\nnone\nClaw\n")

    assert result.exit_code == 0, result.output
    assert result.output.count("Error:") == 2
    assert load_config(tmp_path / "config.yaml").model == "copilot/gpt-6-astra"


def test_onboard_falls_back_to_cached_models(runtime: Mock, tmp_path: Path) -> None:
    runtime.sync_models.side_effect = RuntimeError("Provider unavailable")
    runtime.models.return_value = (_model("gpt-6-astra"),)

    result = CliRunner().invoke(cli, ["onboard"], input="n\n1\nnone\nClaw\n")

    assert result.exit_code == 0, result.output
    assert load_config(tmp_path / "config.yaml").model == "copilot/gpt-6-astra"
    runtime.models.assert_called_once_with()


def test_onboard_allows_manual_entry_without_chat_models(runtime: Mock, tmp_path: Path) -> None:
    runtime.sync_models.return_value = (_model("embedding", model_type="embeddings"),)

    result = CliRunner().invoke(cli, ["onboard"], input="n\ncopilot/gpt-6-astra\nnone\nClaw\n")

    assert result.exit_code == 0, result.output
    assert "Enter model name" in result.output
    assert load_config(tmp_path / "config.yaml").model == "copilot/gpt-6-astra"


def test_onboard_configures_selected_tools_and_confirmed_image_model(runtime, tmp_path):
    chat = _model("chat")
    unknown = _model("unknown-image")
    vision_only = replace(_model("vision-only"), input=("text", "image"), output=("text",))
    image = replace(_model("image"), id="antigravity/image", provider="antigravity", output=("image",))
    runtime.active_providers.return_value = ["copilot", "antigravity"]
    runtime.sync_models.return_value = (chat, unknown, vision_only, image)

    result = CliRunner().invoke(
        cli, ["onboard"], input="n\n1\nfilesystem,generate_image\n1\nClaw\n",
    )

    assert result.exit_code == 0, result.output
    config = load_config(tmp_path / "config.yaml")
    assert config.enabled_tools == ("filesystem", "generate_image")
    assert config.image_model == "antigravity/image"
    primary_step = result.output.split("Step 2:", 1)[1].split("Step 3:", 1)[0]
    assert "antigravity/image" not in primary_step
    image_step = result.output.split("Image-generation models:", 1)[1].split("Step 4:", 1)[0]
    assert "antigravity/image" in image_step
    assert "copilot/unknown-image" not in image_step
    assert "copilot/vision-only" not in image_step
    assert "Select image model:" in image_step


def test_onboard_rejects_image_tool_without_supported_models(runtime, tmp_path):
    runtime.sync_models.return_value = (_model("chat"), _model("image-but-unknown"))
    result = CliRunner().invoke(cli, ["onboard"], input="n\n1\ngenerate_image\nnone\nClaw\n")
    assert result.exit_code == 0, result.output
    assert "No model has confirmed image_generation support" in result.output
    config = load_config(tmp_path / "config.yaml")
    assert config.enabled_tools == ()
    assert config.image_model == ""


def test_onboard_default_tool_selection_does_not_enable_images(runtime, tmp_path):
    from xdog.claw.core.tools import default_enabled_tools

    runtime.sync_models.return_value = (_model("chat"),)
    result = CliRunner().invoke(cli, ["onboard"], input="n\n1\n\nClaw\n")
    assert result.exit_code == 0, result.output
    config = load_config(tmp_path / "config.yaml")
    assert config.enabled_tools == default_enabled_tools()
    assert "generate_image" not in config.enabled_tools
    assert config.image_model == ""


def test_onboard_reprompts_for_invalid_tools_and_image_choice(runtime, tmp_path):
    runtime.sync_models.return_value = (_model("chat"), replace(_model("image"), output=("image",)))
    result = CliRunner().invoke(
        cli, ["onboard"], input="n\n1\nnot_a_tool\ngenerate_image\n0\n2\n1\nClaw\n",
    )
    assert result.exit_code == 0, result.output
    assert "Unknown tool selection" in result.output
    assert load_config(tmp_path / "config.yaml").image_model == "copilot/image"


def test_onboard_all_tools_and_numeric_selection(runtime, tmp_path):
    from xdog.claw.core.tools import registered_names

    runtime.sync_models.return_value = (_model("chat"), replace(_model("image"), output=("image",)))
    result = CliRunner().invoke(cli, ["onboard"], input="n\n1\nall\n1\nClaw\n")
    assert result.exit_code == 0, result.output
    assert set(load_config(tmp_path / "config.yaml").enabled_tools) == registered_names()
    names = sorted(registered_names())
    selected = names.index("filesystem") + 1
    result = CliRunner().invoke(cli, ["onboard"], input=f"n\n1\n{selected}\nClaw\n")
    assert result.exit_code == 0, result.output
    config = load_config(tmp_path / "config.yaml")
    assert config.enabled_tools == ("filesystem",)
    assert config.image_model == ""


def test_onboard_preserves_channels_paths_and_other_groups(runtime, tmp_path):
    runtime.sync_models.return_value = (_model("chat"),)
    path = tmp_path / "config.yaml"
    original = ClawConfig(
        model="copilot/chat", enabled_tools=("filesystem",),
        data_dir=str(tmp_path / "custom-data"), weixin_enabled=True, weixin_account_id="account",
        max_concurrent_agents=7,
        groups=(
            GroupDef(id="work", name="Existing", is_main=True, workspace=str(tmp_path / "workspace")),
            GroupDef(id="helper", name="Helper", model_id="copilot/helper"),
        ),
    )
    save_config(original, path)
    result = CliRunner().invoke(cli, ["onboard"], input="n\n\n\nRenamed\n")
    assert result.exit_code == 0, result.output
    config = load_config(path)
    assert config.weixin_enabled and config.weixin_account_id == "account"
    assert config.data_dir == original.data_dir and config.max_concurrent_agents == 7
    assert config.groups[0].id == "work" and config.groups[0].name == "Renamed"
    assert config.groups[0].workspace == str(tmp_path / "workspace")
    assert config.groups[1] == original.groups[1]


def test_onboard_abort_before_image_choice_does_not_write_config(runtime, tmp_path):
    runtime.sync_models.return_value = (_model("chat"), replace(_model("image"), output=("image",)))
    result = CliRunner().invoke(cli, ["onboard"], input="n\n1\ngenerate_image\n")
    assert result.exit_code != 0
    assert not (tmp_path / "config.yaml").exists()
