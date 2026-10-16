"""Removed frontend protocols cannot bootstrap a model, disk action or controller."""
from __future__ import annotations

import importlib
import inspect
import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from xdog.coding import main
from xdog.coding.cli.args import cli

REMOVED_MODULES = (
    'after_run_compaction', 'after_run_name', 'after_run_tools', 'children', 'context_messages',
    'custom_delivery', 'editor_write', 'events', 'extensions', 'images', 'inbox', 'input',
    'navigation', 'output', 'parent_drafts', 'provider_auth', 'queued_inputs', 'rpc_mode',
    'session_events', 'terminal', 'terminal_data', 'transcripts',
)


@pytest.mark.parametrize('module', ('', *REMOVED_MODULES))
def test_dedicated_rpc_modules_and_handlers_are_absent(module):
    name = 'xdog.coding.modes.rpc' + ('.' + module if module else '')
    with pytest.raises(ModuleNotFoundError, match=r'xdog\.coding\.modes\.rpc'):
        importlib.import_module(name)


def test_rpc_not_advertised_by_cli_or_public_entrypoint():
    result = CliRunner().invoke(cli, ['--help'])
    assert result.exit_code == 0
    assert '--rpc' not in result.output
    assert '--acp' in result.output and '--print' in result.output and '--enqueue' in result.output
    assert 'rpc' not in inspect.signature(main.run_agent).parameters
    assert not hasattr(main, '_run_rpc')


@pytest.mark.parametrize('enabled', [False, True])
def test_removed_public_parameter_is_rejected_before_startup(monkeypatch, enabled):
    def forbidden(*args, **kwargs):
        pytest.fail('removed RPC argument must not reach session construction')

    monkeypatch.setattr('xdog.coding.core.sdk.create_agent_session', forbidden)
    with pytest.raises(TypeError, match="unexpected keyword argument 'rpc'"):
        main.run_agent(rpc=enabled)


@pytest.mark.parametrize('packet', [
    {'type': 'child_control', 'action': 'saved_save_confirm'},
    {'type': 'parent_draft', 'action': 'save_confirm'},
    {'type': 'transcript_source', 'action': 'read'},
    {'type': 'rpc_hello', 'transient_events': 1, 'transcript_canonical': 1},
    {'type': 'extension_control', 'action': 'command'},
    {'type': 'extension_custom_input', 'data': 'not executable'},
])
def test_old_packets_cannot_start_removed_mode_or_write_drafts(tmp_path, monkeypatch, packet):
    def forbidden(*args, **kwargs):
        pytest.fail('unknown --rpc must be rejected before runtime or initial-input work')

    monkeypatch.setattr(main, 'run_agent', forbidden)
    monkeypatch.setattr(main, '_build_initial_message', forbidden)
    monkeypatch.chdir(tmp_path)
    sentinel = tmp_path / 'existing-draft.json'
    sentinel.write_text('KEEP EXISTING USER DATA')
    result = CliRunner().invoke(cli, ['--rpc'], input=json.dumps(packet) + '\n')
    assert result.exit_code == 2 and 'No such option: --rpc' in result.output
    assert sentinel.read_text() == 'KEEP EXISTING USER DATA'
    assert list(tmp_path.iterdir()) == [sentinel]


@pytest.mark.parametrize('prompt,print_mode,expected', [
    (None, False, 'native'), ('one input', False, 'print'), ('one input', True, 'print'),
])
def test_retained_entry_modes_route_without_rpc(monkeypatch, prompt, print_mode, expected):
    owner = object()
    routed = []
    monkeypatch.setattr('xdog.coding.core.sdk.create_agent_session',
                        lambda options: SimpleNamespace(session=owner, model_fallback_message=None))
    monkeypatch.setattr(main, '_load_extensions', lambda *args: None)
    monkeypatch.setattr(main, '_build_initial_message', lambda *args, **kwargs: prompt)
    monkeypatch.setattr(main, '_run_interactive', lambda session, verbose: routed.append(('native', session)))
    monkeypatch.setattr(main, '_run_print', lambda session, *args, **kwargs: routed.append(('print', session)))
    main.run_agent(prompt=prompt, print_mode=print_mode)
    assert routed == [(expected, owner)]
