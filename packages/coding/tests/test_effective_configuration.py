"""Actual SDK uses one configuration snapshot, with absent and empty distinct."""
from __future__ import annotations

import json

import pytest
from xdog.ai.types import Model
from xdog.coding.config import GlobalConfig, ProjectConfig
from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session

from packages.coding.tests.test_sdk_session_state import _Provider


@pytest.mark.parametrize('level', ['off', 'high'])
def test_sdk_thinking_option_takes_precedence(tmp_path, monkeypatch, level):
    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr('xdog.ai.load', lambda: _Provider(
        Model(id='offline/model', reasoning=True, context_window=100_000)))
    result = create_agent_session(CreateSessionOptions(working_dir=tmp_path, thinking_level=level,
        overrides={'thinking_level': 'low'}))
    try:
        assert result.session.agent.options.thinking == (None if level == 'off' else level)
    finally:
        result.session.dispose().result()


@pytest.mark.parametrize('selection', [None, [], ['filesystem']])
def test_initial_tool_selection_and_saved_explicit_override(tmp_path, monkeypatch, selection):
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'settings.json').write_text(json.dumps({'allowed_tools': ['bash']}))
    project = tmp_path / '.coding'
    project.mkdir()
    (project / 'settings.json').write_text(json.dumps({'allowed_tools': selection}))
    monkeypatch.setenv('CODING_DIR', str(data))
    monkeypatch.setattr('xdog.ai.load', lambda: _Provider(Model(id='offline/model', context_window=100_000)))
    result = create_agent_session(CreateSessionOptions(working_dir=tmp_path))
    session = result.session
    try:
        expected = ('bash',) if selection is None else tuple(selection)
        assert session.session_data.active_tools == expected
        # An explicit empty caller selection wins over both saved and configured values.
        identifier = session.session_id
    finally:
        session.dispose().result()
    resumed = create_agent_session(CreateSessionOptions(working_dir=tmp_path, resume_id=identifier,
        overrides={'allowed_tools': []})).session
    try:
        assert resumed.session_data.active_tools == ()
        assert resumed.agent.active_tool_selection == ()
        assert resumed.permissions.mode == 'ask', 'Selection is not permission authorization'
    finally:
        resumed.dispose().result()


def test_factory_loads_each_configuration_layer_once(tmp_path, monkeypatch):
    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr('xdog.ai.load', lambda: _Provider(Model(id='offline/model', context_window=100_000)))
    calls = []
    for cls in (GlobalConfig, ProjectConfig):
        original = cls.load
        def tracked(*args, _load=original, _name=cls.__name__, **kwargs):
            calls.append(_name)
            return _load(*args, **kwargs)
        monkeypatch.setattr(cls, 'load', tracked)
    session = create_agent_session(CreateSessionOptions(working_dir=tmp_path)).session
    try:
        assert calls == ['GlobalConfig', 'ProjectConfig']
    finally:
        session.dispose().result()


def test_saved_empty_tool_selection_wins_over_changed_project(tmp_path, monkeypatch):
    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr('xdog.ai.load', lambda: _Provider(Model(id='offline/model', context_window=100_000)))
    session = create_agent_session(CreateSessionOptions(working_dir=tmp_path,
                                                       overrides={'allowed_tools': []})).session
    identifier = session.session_id
    session.dispose().result()
    project = tmp_path / '.coding'
    project.mkdir()
    (project / 'settings.json').write_text(json.dumps({'allowed_tools': ['bash']}))
    resumed = create_agent_session(CreateSessionOptions(working_dir=tmp_path, resume_id=identifier)).session
    try:
        assert resumed.agent.active_tool_selection == ()
        assert resumed.session_data.active_tools == ()
    finally:
        resumed.dispose().result()
