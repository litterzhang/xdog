"""Native client defaults use local models and preserve user configuration."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from xdog.ai import cli, cli_switch
from xdog.ai.types import Model

MODEL = Model(id="copilot/gpt-6-astra", context_window=1_178_000)


@pytest.fixture
def native_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("CODING_DIR", "CLAW_DIR", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("xdog.ai.load", lambda: SimpleNamespace(models=lambda: (MODEL,)))

    def no_proxy(*args):
        pytest.fail("Native clients must not fetch models from the proxy")

    monkeypatch.setattr(cli_switch, "fetch_models", no_proxy)
    # Native clients must not depend on even a valid proxy-switch config.
    proxy_config = tmp_path / ".config" / "xdog" / "switch-cli.json"
    proxy_config.parent.mkdir(parents=True)
    proxy_config.write_text("invalid proxy settings")
    return tmp_path


@pytest.mark.parametrize("agent", ["coding", "claw"])
@pytest.mark.parametrize("location", ["default", "xdg", "app_override"])
def test_native_switch_sets_user_default_preserves_settings(native_home, monkeypatch, agent, location):
    if location == "app_override":
        monkeypatch.setenv(f"{agent.upper()}_DIR", str(native_home / f"custom-{agent}"))
        root = native_home / f"custom-{agent}"
    elif location == "xdg":
        monkeypatch.setenv("XDG_CONFIG_HOME", str(native_home / "xdg-config"))
        root = native_home / "xdg-config" / "xdog" / agent
    else:
        root = native_home / ".config" / "xdog" / agent
    root.mkdir(parents=True)
    if agent == "coding":
        path = root / "settings.json"
        original = '{"default_model":"old","permission_mode":"deny","extensions":["mine"],"custom":true}'
    else:
        path = root / "config.yaml"
        original = (
            'model: old\napi_key: keep-me\nenabled_tools: []\n'
            'groups:\n  main:\n    model_id: copilot/group-model\n'
        )
    path.write_text(original)
    monkeypatch.setattr("sys.argv", ["xdog-ai", "switch-cli", agent, "copilot", "--model", "gpt-6-astra"])
    cli.main()

    if agent == "coding":
        from xdog.coding.config import GlobalConfig, get_settings_path

        assert get_settings_path() == path
        assert GlobalConfig.load().default_model == MODEL.id
        result = json.loads(path.read_text())
        assert result["permission_mode"] == "deny"
        assert result["extensions"] == ["mine"]
        assert result["custom"] is True
    else:
        from xdog.claw.config import get_config_path, load_config

        assert get_config_path() == path
        monkeypatch.setattr("xdog.claw.config._cached_config", None)
        loaded = load_config(path)
        assert loaded.model == MODEL.id
        assert loaded.groups[0].model_id == "copilot/group-model"
        assert loaded.enabled_tools == ()
        assert yaml.safe_load(path.read_text())["api_key"] == "keep-me"
    assert path.with_name(path.name + ".bak").read_text() == original


@pytest.mark.parametrize("agent", ["coding", "claw"])
def test_native_interactive_skip_makes_no_client_config(native_home, monkeypatch, agent):
    monkeypatch.setattr("builtins.input", lambda _: "0")
    monkeypatch.setattr("sys.argv", ["xdog-ai", "switch-cli", agent, "copilot"])
    cli.main()
    assert not (native_home / ".config" / "xdog" / agent).exists()


@pytest.mark.parametrize("agent", ["coding", "claw"])
def test_native_list_does_not_write_client_config(native_home, monkeypatch, capsys, agent):
    monkeypatch.setattr("sys.argv", ["xdog-ai", "switch-cli", agent, "list"])
    cli.main()
    assert "copilot" in capsys.readouterr().out
    assert not (native_home / ".config" / "xdog" / agent).exists()


def test_claw_invalid_yaml_is_not_overwritten(native_home, monkeypatch, capsys):
    path = native_home / ".config" / "xdog" / "claw" / "config.yaml"
    path.parent.mkdir(parents=True)
    original = 'groups: [broken\n'
    path.write_text(original)
    monkeypatch.setattr("sys.argv", ["xdog-ai", "switch-cli", "claw", "copilot", "--model", MODEL.id])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "YAML" in capsys.readouterr().err
    assert path.read_text() == original
