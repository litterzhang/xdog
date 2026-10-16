"""Batch limits are strict, layered owner settings rather than client permissions."""
import json

import pytest


@pytest.mark.parametrize('value', [True, False, 0, -1, 129, '10', 1.5, None])
def test_invalid_explicit_batch_limits_are_not_coerced_or_silently_defaulted(tmp_path, value):
    from xdog.coding.config import GlobalConfig, ProjectConfig, RuntimeConfig

    with pytest.raises(ValueError):
        GlobalConfig(max_messages_per_turn=value)
    with pytest.raises(ValueError):
        ProjectConfig(max_messages_per_turn=value)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'max_messages_per_turn': value}))
    with pytest.raises(ValueError):
        GlobalConfig.load(settings)
    (tmp_path / '.coding').mkdir()
    (tmp_path / '.coding' / 'settings.json').write_text(settings.read_text())
    with pytest.raises(ValueError):
        ProjectConfig.load(tmp_path)
    with pytest.raises(ValueError):
        RuntimeConfig.resolve(global_cfg=GlobalConfig(), overrides={'max_messages_per_turn': value})


def test_default_and_layered_settings_are_saved_and_restored(tmp_path):
    from xdog.coding.config import GlobalConfig, ProjectConfig, RuntimeConfig
    from xdog.coding.core.settings_manager import SettingsManager

    assert GlobalConfig().max_messages_per_turn == 10
    global_path = tmp_path / 'settings.json'
    GlobalConfig(max_messages_per_turn=8).save(global_path)
    (tmp_path / '.coding').mkdir()
    (tmp_path / '.coding' / 'settings.json').write_text(json.dumps({'max_messages_per_turn': 5}))
    manager = SettingsManager(global_path=global_path, project_dir=tmp_path)
    assert manager.max_messages_per_turn == 5
    manager.set_session_max_messages_per_turn(3)
    assert manager.max_messages_per_turn == 3
    saved = manager.session_to_dict()
    restored = SettingsManager(global_path=global_path, project_dir=tmp_path)
    restored.load_session_settings(saved)
    assert restored.max_messages_per_turn == 3
    assert RuntimeConfig.resolve(global_cfg=GlobalConfig(max_messages_per_turn=8),
                                 project_cfg=ProjectConfig(max_messages_per_turn=5),
                                 overrides={'max_messages_per_turn': 1}).max_messages_per_turn == 1


@pytest.mark.parametrize('value', [0, True, '3'])
def test_invalid_sdk_override_fails_before_provider_bootstrap(tmp_path, monkeypatch, value):
    import xdog.ai as ai
    from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session

    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))

    def forbidden():
        raise AssertionError('invalid configuration must not bootstrap a provider')

    monkeypatch.setattr(ai, 'load', forbidden)
    with pytest.raises(ValueError):
        create_agent_session(CreateSessionOptions(working_dir=tmp_path, overrides={'max_messages_per_turn': value}))


def test_invalid_saved_limit_fails_before_provider_bootstrap(tmp_path, monkeypatch):
    import xdog.ai as ai
    from xdog.coding.config import get_sessions_dir
    from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session
    from xdog.coding.core.session_manager import SessionManager

    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    manager = SessionManager(get_sessions_dir())
    session = manager.create_session(working_dir=tmp_path)
    session.settings = {'max_messages_per_turn': 0}
    manager.save_session(session)

    def forbidden():
        raise AssertionError('invalid saved setting must not bootstrap a provider')

    monkeypatch.setattr(ai, 'load', forbidden)
    with pytest.raises(ValueError):
        create_agent_session(CreateSessionOptions(working_dir=tmp_path, resume_id=session.session_id))


def test_sdk_saved_value_survives_project_change_but_explicit_override_wins(tmp_path, monkeypatch):
    import xdog.ai as ai
    from xdog.ai.types import Model
    from xdog.coding.config import GlobalConfig
    from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session

    from packages.coding.tests.test_sdk_session_state import _Provider

    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    GlobalConfig(default_model='copilot/test-batch', max_messages_per_turn=8).save()
    (tmp_path / '.coding').mkdir()
    project = tmp_path / '.coding' / 'settings.json'
    project.write_text(json.dumps({'max_messages_per_turn': 5}))
    provider = _Provider(Model(id='copilot/test-batch', provider='copilot', context_window=32000))
    monkeypatch.setattr(ai, 'load', lambda: provider)
    original = create_agent_session(CreateSessionOptions(working_dir=tmp_path,
                                                        overrides={'max_messages_per_turn': 3})).session
    sid = original.session_id
    assert original.settings.max_messages_per_turn == 3
    assert original.session_manager.load_session(sid).settings['max_messages_per_turn'] == 3
    original.dispose()
    project.write_text(json.dumps({'max_messages_per_turn': 9}))
    resumed = create_agent_session(CreateSessionOptions(working_dir=tmp_path, resume_id=sid)).session
    assert resumed.settings.max_messages_per_turn == 3
    resumed.dispose()
    overridden = create_agent_session(CreateSessionOptions(working_dir=tmp_path, resume_id=sid,
                                                          overrides={'max_messages_per_turn': 1})).session
    assert overridden.settings.max_messages_per_turn == 1
    overridden.dispose()
