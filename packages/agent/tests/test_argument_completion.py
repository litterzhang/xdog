from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.argument_completion import items, plain
from xdog.agent.extensions.commands import declaration
from xdog.agent.extensions.runtime import ExtensionRuntime


def test_completion_registration_replacement_old_disposer_and_exact_wire_identity():
    api = ExtensionAPI({})
    first = api.command('deploy', 'First', lambda *args: 'command', complete=lambda *args: [])
    original = api.command_snapshot()['commands'][0]['completion_id']
    second = api.command('deploy', 'Second', lambda *args: 'command', replace=True, complete=lambda *args: [])
    assert api.command_snapshot()['commands'][0]['completion_id'] != original
    first()
    assert api.commands['deploy'][0] == 'Second' and declaration(api.command_snapshot())['commands']
    second()
    assert not api.commands and not api._command_completions
    with pytest.raises(ValueError):
        api.command('invalid', 'Invalid', lambda *args: None, complete=3)


@pytest.mark.parametrize('bad', [None, {}, ['text'], [{'value': ''}], [{'value': 'newline\n'}],
    [{'value': 'bidi\u202e'}], [{'value': 'ok', 'execute': 'no'}], [{'value': 'a'}, {'value': 'a'}],
    [{'value': 'x' * 2049}], [{'value': str(index)} for index in range(33)]])
def test_invalid_completion_data_is_refused_instead_of_running_or_reinterpreting_it(bad):
    with pytest.raises(ValueError):
        items(bad)


def test_completion_prefix_is_strict_plain_text() -> None:

    def _check_case(bad):
        with pytest.raises(ValueError):
            plain(bad, 4096)

    for bad in ["\x1bcontrol", "\n", "\u2066", "x" * 4097, False]:
        _check_case(bad)


async def test_actual_owned_worker_completion_never_executes_command_and_revalidates_replacements(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/argcomplete', root / 'argcomplete')
    runtime = ExtensionRuntime(['argcomplete'], root, tmp_path)
    try:
        await runtime.initialize()
        key = 'ext-argcomplete-deploy'
        original = runtime.command_completions()[key]
        result = await runtime.complete_command(key, '', original)
        assert result['status'] == 'ok' and [row['value'] for row in result['items']] == [
            'alpha', 'beta', 'release candidate']
        counts = json.loads(await runtime.command('ext-argcomplete-counts', ''))
        assert counts == {'executed': 0, 'completed': 1}
        await runtime.command('ext-argcomplete-replace', '')
        current = runtime.command_completions()[key]
        assert current != original
        assert (await runtime.complete_command(key, '', original)) == {'status': 'stale', 'items': []}
        await runtime.command('ext-argcomplete-old', '')
        assert (await runtime.complete_command(key, '', current))['items'][0]['value'] == 'new value'
        await runtime.command('ext-argcomplete-disable', '')
        assert key not in runtime.command_completions()
        assert (await runtime.complete_command(key, '', current))['status'] == 'stale'
    finally:
        await runtime.aclose()


async def test_registration_change_inside_completion_refuses_inflight_data_without_replay(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/argcomplete', root / 'argcomplete')
    runtime = ExtensionRuntime(['argcomplete'], root, tmp_path)
    try:
        await runtime.initialize()
        identity = runtime.command_completions()['ext-argcomplete-deploy']
        result = await runtime.complete_command('ext-argcomplete-deploy', 'change', identity)
        assert result == {'status': 'stale', 'items': []}
        assert json.loads(await runtime.command('ext-argcomplete-counts', '')) == {'executed': 0, 'completed': 1}
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('failure', ['timeout', 'bad'])
async def test_uncertain_completion_deadline_or_invalid_data_reaps_its_worker_and_withdraws_registration(
    tmp_path, failure,
):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/argcomplete', root / 'argcomplete')
    marker = tmp_path / 'entered'
    runtime = ExtensionRuntime(['argcomplete'], root, tmp_path,
                               settings={'argcomplete': {'marker': str(marker)}})
    try:
        await runtime.initialize()
        client = runtime._manager._clients['argcomplete']
        process = client.process
        identity = runtime.command_completions()['ext-argcomplete-deploy']
        with pytest.raises((TimeoutError, ValueError, RuntimeError)):
            await runtime.complete_command('ext-argcomplete-deploy', 'hold' if failure == 'timeout' else 'bad',
                                           identity, timeout=0.1 if failure == 'timeout' else 1)
        assert not runtime.command_completions() and process.returncode is not None
        assert runtime.snapshot()['extensions'][0]['state'] == 'failed'
    finally:
        await runtime.aclose()


def test_argument_context_exact_coordinates_plain_bounded_copy_and_legacy_none():
    from xdog.agent.extensions.argument_completion import argument_context

    supplied = {'arguments': '开发 enTAIL later', 'cursor': 5, 'start': 3, 'end': 9}
    copied = argument_context(supplied, 'en')
    assert copied == supplied and copied is not supplied
    copied['arguments'] = 'Cannot alter the original input'
    assert supplied['arguments'] == '开发 enTAIL later'
    assert argument_context(None, 'en') is None
    for changed in ({'cursor': True}, {'start': -1}, {'cursor': 4}, {'end': 100}, {'end': 3},
                    {'arguments': 'unsafe\x1b'}, {'arguments': 'x' * 8193}, {'session_id': 'foreign'},
                    {'permission_mode': 'allow-all'}):
        with pytest.raises(ValueError):
            argument_context({**supplied, **changed}, 'en')


async def test_actual_callback_uses_previous_arguments_and_cannot_mutate_supplied_context(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/argcomplete', root / 'argcomplete')
    runtime = ExtensionRuntime(['argcomplete'], root, tmp_path)
    try:
        await runtime.initialize()
        command = 'ext-argcomplete-deploy'
        identity = runtime.command_completions()[command]
        assert (await runtime.complete_command(command, 'en', identity))['items'] == []
        supplied = {'arguments': 'production enTAIL later', 'cursor': 13, 'start': 11, 'end': 17}
        first = await runtime.complete_command(command, 'en', identity, argument_input=supplied)
        assert first['items'][0]['value'] == 'endpoint-production'
        assert supplied['arguments'] == 'production enTAIL later'
        second = await runtime.complete_command(command, 'en', identity,
            argument_input={'arguments': 'staging enTAIL later', 'cursor': 10, 'start': 8, 'end': 14})
        assert second['items'][0]['value'] == 'endpoint-staging'
        assert json.loads(await runtime.command('ext-argcomplete-counts', ''))['executed'] == 0
    finally:
        await runtime.aclose()
