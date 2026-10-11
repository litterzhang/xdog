"""Configured keymap host actions stay outside parent/child terminal hook data."""
from __future__ import annotations

import json

import pytest
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.terminal import RpcTerminal
from xdog.coding.modes.rpc.terminal_data import editable_data
from xdog.tui.prompt_keymap import PromptKeymap

from packages.coding.tests.test_rpc_child_terminal import started_terminal, terminal_packet
from packages.coding.tests.test_rpc_children import close as close_child
from packages.coding.tests.test_rpc_children import setup
from packages.coding.tests.test_rpc_extensions import close as close_parent
from packages.coding.tests.test_rpc_extensions import session
from packages.coding.tests.test_rpc_parent_terminal import request


@pytest.mark.parametrize('action', ['send', 'external_editor', 'save_draft', 'ext-terminal-count'])
def test_remapped_edit_key_cannot_be_hook_input_or_output(action):
    overrides = {'left': [], action: 'left'}
    # Extension registration requires modified/function keys; use Ctrl+A for that case.
    if action.startswith('ext-'):
        overrides = {'start': [], action: 'ctrl+a'}
    keymap = PromptKeymap.from_overrides(overrides)
    value = '\x01' if action.startswith('ext-') else '\x1b[D'
    with pytest.raises(ValueError):
        editable_data(value, 'key', keymap=keymap)
    assert editable_data(value, 'paste', keymap=keymap) == value
    assert editable_data('ab', 'key', keymap=keymap) == 'ab'


@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child'])
@pytest.mark.parametrize('invalid', [False, True], ids=['remapped', 'invalid-config'])
async def test_actual_rpc_owners_use_configured_bindings_before_worker_hook(tmp_path, monkeypatch, child, invalid):
    monkeypatch.setenv('CODING_DIR', str(tmp_path))
    path = tmp_path / 'keybindings.json'
    path.write_text('invalid json' if invalid else json.dumps({'left': [], 'send': 'left'}))
    if child:
        owner, _, nav, sources, controller, events = setup(tmp_path, extension='terminal')
    else:
        owner, calls = session(tmp_path, extra=('terminal',))
        events = []
        controller = RpcTerminal(owner, RpcNavigation(owner, events.append), events.append)
    try:
        if child:
            identifier = await started_terminal(controller, events)
            controller.negotiate({'child_terminal_input': 1})
            packet = terminal_packet(controller, identifier, data='ab' if invalid else '\x1b[D')
            with pytest.raises(ValueError):
                controller.terminal_input(packet)
            runtime = owner.children.extensions.owners[identifier]
            assert not controller.jobs
        else:
            await owner.initialize_extensions()
            controller.negotiate({'terminal_input': 1})
            with pytest.raises(ValueError):
                controller.start(request(controller, data='ab' if invalid else '\x1b[D'))
            runtime = owner.extensions
            assert controller.task is None and not calls
        assert await runtime.command('ext-terminal-count', '') == '0'
        assert not owner.messages
    finally:
        if child:
            await close_child(owner, nav, sources, controller)
        else:
            await controller.aclose()
            await close_parent(owner)


@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child'])
async def test_actual_post_hook_remapped_action_is_refused_without_result_delivery(tmp_path, monkeypatch, child):
    monkeypatch.setenv('CODING_DIR', str(tmp_path))
    (tmp_path / 'keybindings.json').write_text(json.dumps({'left': [], 'send': 'left'}))
    if child:
        owner, _, nav, sources, controller, events = setup(tmp_path, extension='terminal')
    else:
        owner, _ = session(tmp_path, extra=('terminal',))
        events = []
        controller = RpcTerminal(owner, RpcNavigation(owner, events.append), events.append)
    source = tmp_path / 'extensions/terminal/main.py'
    source.write_text(source.read_text().replace("        if data == 'bad':",
        "        if data == 'mapped':\n            return {'data': '\\x1b[D', 'consume': False}\n"
        "        if data == 'bad':"))
    try:
        if child:
            identifier = await started_terminal(controller, events)
            controller.negotiate({'child_terminal_input': 1})
            controller.terminal_input(terminal_packet(controller, identifier, data='mapped'))
            await controller.jobs['terminal'][0]
            result_kind = 'child_terminal_result'
            runtime = owner.children.extensions.owners[identifier]
        else:
            await owner.initialize_extensions()
            controller.negotiate({'terminal_input': 1})
            controller.start(request(controller, data='mapped'))
            await controller.task
            result_kind = 'terminal_result'
            runtime = owner.extensions
        result = next(event for event in events if event['type'] == result_kind)
        assert not result['success'] and 'data' not in result and 'consume' not in result
        assert await runtime.command('ext-terminal-count', '') == '1'
        assert not owner.messages
    finally:
        if child:
            await close_child(owner, nav, sources, controller)
        else:
            await controller.aclose()
            await close_parent(owner)


def test_current_extension_shortcuts_use_native_conflict_and_disable_rules():
    from xdog.tui.extension_shortcuts import ExtensionShortcuts
    from xdog.tui.keys import KeyEvent

    rows = [{'extension': 'terminal', 'command': 'ext-terminal-count', 'key': 'ctrl+a', 'description': 'Count'}]
    unbound = PromptKeymap.from_overrides({'start': []})
    assert ExtensionShortcuts(rows, unbound).command(KeyEvent('a', ctrl=True)) == 'ext-terminal-count'
    with pytest.raises(ValueError):
        editable_data('\x01', 'key', keymap=unbound, shortcuts=rows)
    # Active native editor binding wins; the conflicting extension is disabled.
    assert ExtensionShortcuts(rows, PromptKeymap()).command(KeyEvent('a', ctrl=True)) is None
    assert editable_data('\x01', 'key', keymap=PromptKeymap(), shortcuts=rows) == '\x01'
    assert editable_data('text', 'key', keymap=unbound, shortcuts=rows) == 'text'
    assert editable_data('\x01', 'paste', keymap=unbound, shortcuts=rows) == '\x01'


@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child'])
async def test_current_owned_worker_shortcut_inventory_checked_on_input_and_transform(tmp_path, monkeypatch, child):
    monkeypatch.setenv('CODING_DIR', str(tmp_path))
    (tmp_path / 'keybindings.json').write_text(json.dumps({'start': []}))
    if child:
        owner, _, nav, sources, controller, events = setup(tmp_path, extension='terminal')
    else:
        owner, _ = session(tmp_path, extra=('terminal',))
        events = []
        controller = RpcTerminal(owner, RpcNavigation(owner, events.append), events.append)
    source = tmp_path / 'extensions/terminal/main.py'
    source.write_text(source.read_text().replace("        if data == 'bad':",
        "        if data == 'shortcut':\n            return {'data': '\\x01', 'consume': False}\n"
        "        if data == 'bad':").replace('    unsubscribe = api.on_terminal_input(hook)',
        "    api.shortcut('registered', 'ctrl+a', 'Runtime registered action', lambda *args: 'NOT-EXECUTED')\n"
        '    unsubscribe = api.on_terminal_input(hook)', 1))
    try:
        if child:
            identifier = await started_terminal(controller, events)
            controller.negotiate({'child_terminal_input': 1})
            runtime = owner.children.extensions.owners[identifier]
            denied = terminal_packet(controller, identifier, data='\x01')
            with pytest.raises(ValueError):
                controller.terminal_input(denied)
        else:
            await owner.initialize_extensions()
            controller.negotiate({'terminal_input': 1})
            runtime = owner.extensions
            with pytest.raises(ValueError):
                controller.start(request(controller, data='\x01'))
        assert any(row['command'] == 'ext-terminal-registered' for row in runtime.shortcuts())
        assert await runtime.command('ext-terminal-count', '') == '0'
        if child:
            controller.terminal_input(terminal_packet(controller, identifier, 'transform', data='shortcut'))
            await controller.jobs['transform'][0]
            result_kind = 'child_terminal_result'
        else:
            controller.start(request(controller, 'transform', data='shortcut'))
            await controller.task
            result_kind = 'terminal_result'
        result = next(event for event in events if event['type'] == result_kind)
        assert not result['success'] and 'data' not in result and 'consume' not in result
        assert await runtime.command('ext-terminal-count', '') == '1'
        assert not owner.messages
    finally:
        if child:
            await close_child(owner, nav, sources, controller)
        else:
            await controller.aclose()
            await close_parent(owner)
