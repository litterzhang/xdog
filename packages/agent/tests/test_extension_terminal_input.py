"""UI036 removes terminal observation and editing authority, not submitted-input hooks."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.runtime import ExtensionRuntime, validate_snapshot
from xdog.agent.extensions.worker import dispatch
from xdog.agent.supervision_extensions import ChildExtensions


@pytest.mark.parametrize('name', ['on_terminal_input', 'terminal_hooks', 'terminal_events', 'terminal_events_for'])
def test_terminal_registration_and_observation_apis_are_absent(name):
    assert not hasattr(ExtensionAPI({}), name)


def test_terminal_runtime_and_child_capture_apis_are_absent():
    assert importlib.util.find_spec('xdog.agent.extensions.terminal_input') is None
    for name in ('terminal_input', 'terminal_input_available', 'terminal_events', 'accept_terminal_registration'):
        assert not hasattr(ExtensionRuntime, name)
    for name in ('terminal_metadata', 'capture_terminal', '_publish_terminal'):
        assert not hasattr(ChildExtensions, name)


@pytest.mark.parametrize('kind', ['key', 'paste', 'release'])
async def test_worker_refuses_old_terminal_protocol_without_calling_submitted_input_hooks(kind):
    api = ExtensionAPI({})
    calls = []
    api.on_input(lambda *args: calls.append(args))
    with pytest.raises(ValueError, match='Unknown extension method'):
        await dispatch(api, 'xdog/terminal_input', {'data': 'secret draft',
                                                 'metadata': {'kind': kind, 'surface': 'prompt'}})
    assert not calls
    info = await dispatch(api, 'xdog/info', {})
    assert not {'terminal_input', 'terminal_events', 'terminal_generation'} & info.keys()


@pytest.mark.parametrize('field', ['terminal_input', 'terminal_generation', 'terminal_events'])
def test_removed_terminal_snapshot_fields_are_refused_even_when_well_typed(field):
    value = {'terminal_input': False, 'terminal_generation': 0, 'terminal_events': []}[field]
    with pytest.raises(ValueError, match='removed'):
        validate_snapshot({'extensions': [], 'commands': {}, 'ui': {}, field: value})


async def test_real_worker_keeps_commands_and_input_hooks_without_terminal_capabilities(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/inputter', root / 'inputter')
    runtime = ExtensionRuntime(['inputter'], root, tmp_path)
    try:
        await runtime.initialize()
        snapshot = runtime.snapshot()
        assert snapshot['extensions'] == [{'name': 'inputter', 'state': 'active'}]
        assert not {'terminal_input', 'terminal_generation', 'terminal_events'} & snapshot.keys()
        assert validate_snapshot(snapshot) == snapshot
    finally:
        await runtime.aclose()


async def test_legacy_terminal_registration_fails_activation_without_publishing_commands(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/terminal', root / 'terminal')
    runtime = ExtensionRuntime(['terminal'], root, tmp_path)
    try:
        await runtime.initialize()
        assert runtime.snapshot()['extensions'] == [{'name': 'terminal', 'state': 'failed'}]
        assert runtime.commands() == {}
        assert not hasattr(runtime, 'terminal_input')
    finally:
        await runtime.aclose()
