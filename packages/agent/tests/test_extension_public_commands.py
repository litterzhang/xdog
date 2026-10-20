from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.commands import declaration
from xdog.agent.extensions.runtime import ExtensionRuntime, validate_snapshot

FIXTURE = Path(__file__).parent / 'fixtures/extensions/publiccommands'


def test_public_command_declaration_and_owned_disposal():
    api = ExtensionAPI({})
    dispose = api.command('inspect', 'Inspect state', lambda *args: 'text', public_name='inspect-state')
    rows = declaration(api.command_snapshot())['commands']
    assert rows == [{'name': 'inspect', 'description': 'Inspect state', 'public_name': 'inspect-state'}]
    with pytest.raises(ValueError, match='duplicate|Duplicate'):
        api.command('other', 'Other', lambda *args: '', public_name='inspect-state')
    dispose()
    assert not api.command_snapshot()['commands']


@pytest.mark.parametrize('name', ['/goals', 'has space', 'ext-mine-hidden', '', 'x' * 33])
def test_invalid_public_names_are_rejected(name):
    with pytest.raises(ValueError):
        ExtensionAPI({}).command('inspect', 'Inspect', lambda *args: '', public_name=name)


async def test_real_worker_public_command_completion_shortcut_and_reload_are_one_catalog(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root / 'publiccommands')
    runtime = ExtensionRuntime(('publiccommands',), root, tmp_path)
    try:
        await runtime.initialize()
        assert 'inspect-state' in runtime.commands()
        assert 'ext-publiccommands-inspect' not in runtime.commands()
        assert runtime.command_owner('inspect-state') == 'publiccommands'
        assert await runtime.command('inspect-state', 'hello') == 'INSPECT:hello'
        assert await runtime.command('ext-publiccommands-inspect', 'internal') == 'INSPECT:internal'
        before = runtime.command_completions()['inspect-state']
        completion = await runtime.complete_command('inspect-state', 'item', before)
        assert completion['items'][0]['value'] == 'item-ready'
        snapshot = validate_snapshot(runtime.snapshot())
        assert snapshot['command_owners']['inspect-state'] == 'publiccommands'
        assert snapshot['shortcuts'][0]['command'] == 'quick-state'
        assert snapshot['shortcuts'][0]['action'] == 'ext-publiccommands-quick'
        await runtime.command('ext-publiccommands-rename', 'new-state')
        assert 'inspect-state' not in runtime.commands() and 'new-state' in runtime.commands()
        assert (await runtime.complete_command('inspect-state', 'item', before))['status'] == 'stale'
        assert await runtime.command('new-state', 'after') == 'INSPECT:after'
        await runtime.manage('unload', 'publiccommands')
        assert not runtime.commands() and runtime.command_owner('new-state') is None
    finally:
        await runtime.aclose()


async def test_public_names_cannot_shadow_host_reservations_or_other_extensions(tmp_path):
    root = tmp_path / 'extensions'
    for name in ('first', 'second'):
        shutil.copytree(FIXTURE, root / name)
        (root / name / 'extension.yaml').write_text(f'name: {name}\nentry_point: main\n')
    runtime = ExtensionRuntime(('first', 'second'), root, tmp_path)
    try:
        await runtime.initialize()
        assert runtime.command_owner('inspect-state') == 'first'
        assert 'second' in runtime._failed
        assert await runtime.command('inspect-state', '') == 'INSPECT:'
        runtime.reserve_commands({'inspect-state'})
        assert runtime.command_conflicts() == {'inspect-state': 'first'}
        assert 'inspect-state' not in runtime.commands()
        assert validate_snapshot(runtime.snapshot())
        with pytest.raises(ValueError, match='[Cc]onflict|reserved'):
            await runtime.command('inspect-state', '')
    finally:
        await runtime.aclose()
    runtime = ExtensionRuntime(('first',), root, tmp_path, settings={'first': {'public_name': 'quit'}})
    try:
        runtime.reserve_commands({'quit', 'goal'})
        await runtime.initialize()
        assert not runtime.commands() and 'first' in runtime._failed
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('commands,owners', [
    ({'ext-first-inspect': 'Inspect'}, {'ext-first-inspect': 'second'}),
    ({'not a command': 'Inspect'}, {'not a command': 'first'}),
    ({'ext-ambiguous': 'Inspect'}, {'ext-ambiguous': 'first'}),
])
def test_remote_public_catalog_cannot_forge_qualified_ownership_or_invalid_names(commands, owners):
    with pytest.raises(ValueError):
        validate_snapshot({'extensions': [{'name': 'first', 'state': 'active'},
                                          {'name': 'second', 'state': 'active'}],
                           'commands': commands, 'command_owners': owners, 'ui': {}, 'shortcuts': []})


def test_existing_uppercase_extension_identity_keeps_qualified_shortcuts():
    result = validate_snapshot({'extensions': [{'name': 'First', 'state': 'active'}],
        'commands': {'ext-first-inspect': 'Inspect'}, 'ui': {},
        'shortcuts': [{'extension': 'First', 'command': 'ext-first-inspect', 'key': 'f6', 'description': 'Inspect'}]})
    assert result['shortcuts'][0]['extension'] == 'First'


async def test_completion_uses_live_owner_after_failed_extension_alias_takeover(tmp_path):
    from xdog.agent.mcp.stdio import McpError
    root = tmp_path / 'extensions'
    for name in ('first', 'second'):
        shutil.copytree(FIXTURE, root / name)
        (root / name / 'extension.yaml').write_text(f'name: {name}\nentry_point: main\n')
    runtime = ExtensionRuntime(('first', 'second'), root, tmp_path, settings={
        'first': {'public_name': 'inspect-state', 'quick_public_name': 'quick-first'},
        'second': {'public_name': 'second-state', 'quick_public_name': 'quick-second'}})
    try:
        await runtime.initialize()
        assert runtime.command_owner('second-state') == 'second'
        old_identity = runtime.command_completions()['inspect-state']
        runtime._manager._clients['first'].process.kill()
        with pytest.raises(McpError):
            await runtime.command('inspect-state', '')
        await runtime.command('ext-second-rename', 'inspect-state')
        assert runtime.command_owner('inspect-state') == 'second'
        assert await runtime.command('inspect-state', 'works') == 'INSPECT:works'
        identity = runtime.command_completions()['inspect-state']
        assert identity != old_identity
        assert (await runtime.complete_command('inspect-state', '', old_identity))['status'] == 'stale'
        assert (await runtime.complete_command('inspect-state', 'item', identity))['items'][0]['value'] == 'item-ready'
    finally:
        await runtime.aclose()


async def test_closed_public_completion_is_stale_without_reopening_a_worker(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root / 'publiccommands')
    runtime = ExtensionRuntime(('publiccommands',), root, tmp_path)
    try:
        await runtime.initialize()
        identity = runtime.command_completions()['inspect-state']
        await runtime.aclose()
        assert not runtime.command_completions()
        assert (await runtime.complete_command('inspect-state', '', identity))['status'] == 'stale'
        assert not runtime._manager._thread.is_alive()
    finally:
        await runtime.aclose()
