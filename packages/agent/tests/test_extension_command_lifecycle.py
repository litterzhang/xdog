from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.commands import declaration
from xdog.agent.extensions.runtime import ExtensionRuntime


def test_owned_command_shortcut_disposers_replacement_identity_and_case_capacity_validation():
    api = ExtensionAPI({})
    def callback(*args):
        return 'original'
    old = api.command('target', 'original', callback)
    new = api.command('target', 'new', lambda *args: 'new', replace=True)
    old()
    assert api.commands['target'][0] == 'new'
    new()
    new()
    assert 'target' not in api.commands
    binding = api.shortcut('target', 'f6', 'keyed', callback)
    newer = api.command('target', 'replacement', callback, replace=True)
    binding()
    assert not api.shortcuts and api.commands['target'][0] == 'replacement'
    newer()
    for index in range(32):
        api.command('cmd' + str(index), 'description', callback)
    with pytest.raises(ValueError):
        api.command('extra', 'description', callback)
    with pytest.raises(ValueError):
        api.command('CMD0', 'different spelling', callback)
    with pytest.raises(ValueError):
        api.command('cmd0', 'bad', callback, replace='yes')


@pytest.mark.parametrize('changes', [{'revision': True}, {'commands': [{'name': 'x', 'description': 3}]},
    {'shortcuts': [{'name': 'missing', 'key': 'f6', 'description': 'missing'}]},
    {'commands': [{'name': 'x', 'description': 'x'}, {'name': 'X', 'description': 'x'}]},
    {'callback': 'not wire data'}])
def test_bad_live_command_declarations_cannot_add_callbacks_dangling_shortcuts_or_duplicate_aliases(changes):
    with pytest.raises(ValueError):
        declaration({**ExtensionAPI({}).command_snapshot(), **changes})


async def test_real_worker_live_catalog_replace_dispose_old_receipts_and_process_identity(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['commandmock'], root, tmp_path)
    events = []
    runtime.publish = events.append
    try:
        await runtime.initialize()
        client = runtime._manager._clients['commandmock']
        process = client.process
        assert 'ext-commandmock-target' not in runtime.commands()
        await runtime.command('ext-commandmock-enable', '')
        assert runtime.shortcuts()[0]['command'] == 'ext-commandmock-target'
        first = dict(client._command_snapshot)
        assert await runtime.command('ext-commandmock-target', '') == 'LIVE-COMMAND-ORIGINAL-1'
        await runtime.command('ext-commandmock-enable', '')
        await runtime.command('ext-commandmock-old', '')
        assert await runtime.command('ext-commandmock-target', '') == 'LIVE-COMMAND-ORIGINAL-2'
        await runtime.command('ext-commandmock-replace', '')
        assert await runtime.command('ext-commandmock-target', '') == 'LIVE-COMMAND-REPLACED-3'
        await runtime.command('ext-commandmock-disable', '')
        assert 'ext-commandmock-target' not in runtime.commands() and not runtime.shortcuts()
        runtime.accept_command_registration('commandmock', client, first)
        assert 'ext-commandmock-target' not in runtime.commands()
        with pytest.raises(ValueError, match='Unknown extension command'):
            await runtime.command('ext-commandmock-target', '')
        assert runtime.snapshot()['extensions'][0]['state'] == 'active'
        assert runtime._manager._clients['commandmock'].process is process and process.returncode is None
        assert events[-1]['type'] == 'extension_ui'
        current = dict(client._command_snapshot)
        with pytest.raises(ValueError, match='Conflicting'):
            runtime.accept_command_registration('commandmock', client, {**current, 'commands': []})
        await runtime.manage('reload', 'commandmock')
        runtime.accept_command_registration('commandmock', client, {**first, 'revision': 999})
        assert 'ext-commandmock-target' not in runtime.commands()
    finally:
        await runtime.aclose()
