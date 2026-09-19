"""Tests for claw YAML configuration loading."""

from pathlib import Path

import pytest
from xdog.claw.config import ClawConfig, GroupDef, ToolConfigError, load_config, save_config


def test_default_config():
    config = ClawConfig()
    assert config.max_concurrent_agents == 3
    assert config.model == ""
    assert config.image_model == ""
    assert config.enabled_tools is None
    assert config.groups == ()
    # Frozen
    with pytest.raises(AttributeError):
        config.model = "changed"

def test_load_config_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: copilot/claude-opus-4\n"
        "data_dir: /tmp/test-data\n"
        "gateway:\n"
        "  max_concurrent_agents: 5\n"
        "  daily_reset_hour: 6\n"
        "groups:\n"
        "  main:\n"
        "    name: TestClaw\n"
        "    is_main: true\n"
        "  helper:\n"
        "    name: Helper\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.model == "copilot/claude-opus-4"
    assert config.data_dir == "/tmp/test-data"
    assert config.max_concurrent_agents == 5
    assert len(config.groups) == 2
    assert config.groups[0] == GroupDef(id="main", name="TestClaw", is_main=True)

def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "nested" / "config.yaml"
    original = ClawConfig(
        model="test-model",
        data_dir="/custom/data",
        max_concurrent_agents=5,
        enabled_tools=("filesystem", "generate_image"),
        image_model="antigravity/image",
        groups=(GroupDef(id="main", name="Main", is_main=True),),
    )
    save_config(original, path)
    assert path.exists()

    loaded = load_config(path)
    assert loaded.model == original.model
    assert loaded.data_dir == original.data_dir
    assert loaded.max_concurrent_agents == original.max_concurrent_agents
    assert len(loaded.groups) == 1
    assert loaded.enabled_tools == original.enabled_tools
    assert loaded.image_model == original.image_model
    assert "!!python" not in path.read_text()


def test_explicit_empty_tool_set_survives_roundtrip(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(ClawConfig(enabled_tools=()), path)
    assert load_config(path).enabled_tools == ()


@pytest.mark.parametrize("value", ["bash", "12", "[bash, 12]", "[null]"])
def test_malformed_tool_list_does_not_fall_back_to_enabled_defaults(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(f"enabled_tools: {value}\n")
    with pytest.raises(ToolConfigError, match="enabled_tools"):
        load_config(path)


# -- status has to say what is actually carrying messages --------------------


def test_status_names_the_enabled_channels() -> None:
    """A channel enabled in the file and absent from the process looks the same
    from outside: "running", no reply, no explanation."""
    from xdog.claw.cli.cli import _channel_lines
    from xdog.claw.config import ClawConfig

    on = _channel_lines(ClawConfig(weixin_enabled=True, weixin_account_id="acct-1"))
    assert any("weixin: enabled (account=acct-1)" in line for line in on)

    off = _channel_lines(ClawConfig())
    assert any("(none enabled)" in line for line in off)


def test_a_config_edited_after_startup_is_reported(tmp_path: Path) -> None:
    """The exact failure that cost an afternoon: the channel was enabled while
    the gateway was already running, so it read `weixin_enabled: false`, started
    nothing, and said "running" the whole time."""
    import os

    from xdog.claw.cli.cli import _config_newer_than_process

    pid = tmp_path / "gateway.pid"
    pid.write_text("123")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("weixin_enabled: true\n")
    os.utime(cfg, (pid.stat().st_mtime + 900, pid.stat().st_mtime + 900))

    warning = _config_newer_than_process(str(cfg), pid)

    assert "restart to apply" in warning
    assert "15m" in warning


def test_no_warning_when_the_process_read_this_config(tmp_path: Path) -> None:
    """It must stay quiet in the normal case, or it becomes noise people learn
    to skip — and then it is not there when it matters."""
    import os

    from xdog.claw.cli.cli import _config_newer_than_process

    cfg = tmp_path / "config.yaml"
    cfg.write_text("x: 1\n")
    pid = tmp_path / "gateway.pid"
    pid.write_text("123")
    os.utime(pid, (cfg.stat().st_mtime + 60, cfg.stat().st_mtime + 60))

    assert _config_newer_than_process(str(cfg), pid) == ""


def test_a_missing_file_is_not_a_warning(tmp_path: Path) -> None:
    from xdog.claw.cli.cli import _config_newer_than_process

    assert _config_newer_than_process(str(tmp_path / "nope.yaml"), tmp_path / "no.pid") == ""
