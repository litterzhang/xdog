import json
from pathlib import Path

from xdog.coding.core.settings_manager import SettingsManager


def test_settings_manager_hierarchy(tmp_path: Path):
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({
        "default_model": "global_model",
        "thinking_level": "medium",
        "custom_instructions": "Global instruct"
    }))

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".coding").mkdir()
    (project_dir / ".coding" / "settings.json").write_text(json.dumps({
        "model": "project_model",
        "custom_instructions": "Project instruct"
    }))

    manager = SettingsManager(global_path=global_path, project_dir=project_dir)

    # Project overrides global
    assert manager.model == "project_model"
    # Fallback to global
    assert manager.thinking_level == "medium"
    # Instructions concatenated
    assert "Global instruct" in manager.custom_instructions
    assert "Project instruct" in manager.custom_instructions

    # Session overrides
    manager.set_session_model("session_model")
    manager.set_session_thinking("high")
    manager.set_session_instructions("Session instruct")

    assert manager.model == "session_model"
    assert manager.thinking_level == "high"
    assert "Session instruct" in manager.custom_instructions


def test_session_serialization():
    manager = SettingsManager()
    manager.set_session_model("test_model")
    manager.set_session_thinking("high")

    data = manager.session_to_dict()
    assert data["model"] == "test_model"
    assert data["thinking_level"] == "high"

    manager2 = SettingsManager()
    manager2.load_session_settings(data)
    assert manager2.model == "test_model"
    assert manager2.thinking_level == "high"


def test_sdk_thinking_levels_have_no_legacy_aliases():
    import pytest
    from xdog.coding.config import GlobalConfig
    from xdog.coding.core.defaults import DEFAULT_THINKING_LEVEL
    from xdog.coding.core.sdk import _as_thinking_level

    assert GlobalConfig().thinking_level == DEFAULT_THINKING_LEVEL == 'medium'
    for level in ('minimal', 'low', 'medium', 'high', 'xhigh'):
        assert _as_thinking_level(level) == level
    assert _as_thinking_level('off') is None
    for retired in ('normal', 'deep', 'ultrathink', 'none', 'future'):
        with pytest.raises(ValueError, match='Invalid thinking level'):
            _as_thinking_level(retired)
