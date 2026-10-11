from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.terminal_input import decision, metadata, text


def test_terminal_decisions_consume_transform_empty_and_false_are_distinct():
    assert decision(None).to_dict() == {'consume': False}
    assert decision({}).to_dict() == {'consume': False}
    assert decision({'consume': False}).to_dict() == {'consume': False}
    assert decision({'data': ''}).to_dict() == {'consume': False, 'data': ''}
    assert decision({'data': '\x1b[A', 'consume': True}).to_dict() == {'consume': True, 'data': '\x1b[A'}
    assert metadata({'kind': 'paste', 'surface': 'editor'}) == {'kind': 'paste', 'surface': 'editor'}
    assert text('界' * 5333) == '界' * 5333


@pytest.mark.parametrize('value', [True, 'x', [], {'consume': 1}, {'consume': None}, {'data': None},
                                  {'data': 'x' * 16001}, {'data': '\ud800'}, {'unknown': 'field'}])
def test_invalid_terminal_decisions_cannot_be_coerced_or_forwarded(value):
    with pytest.raises(ValueError):
        decision(value)


@pytest.mark.parametrize('value', [None, {}, {'kind': 'key'}, {'kind': 'other', 'surface': 'editor'},
                                  {'kind': 'key', 'surface': ''}, {'kind': 'key', 'surface': 'x' * 129},
                                  {'kind': 'key', 'surface': 'editor', 'unknown': 'field'}])
def test_terminal_metadata_bounds(value):
    with pytest.raises(ValueError):
        metadata(value)


def test_registration_unsubscribe_is_idempotent_bounded_and_requires_distinct_callables():
    api = ExtensionAPI({})
    def callback(*args):
        return None
    unsubscribe = api.on_terminal_input(callback)
    with pytest.raises(ValueError):
        api.on_terminal_input(callback)
    unsubscribe()
    unsubscribe()
    assert not api.terminal_hooks
    for _ in range(32):
        api.on_terminal_input(lambda *args: None)
    with pytest.raises(ValueError):
        api.on_terminal_input(lambda *args: None)
    with pytest.raises(ValueError):
        ExtensionAPI({}).on_terminal_input(None)


async def test_real_worker_transform_consume_order_unsubscribe_and_no_dialog_authority(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['terminal'], root, tmp_path)
    questions = []
    async def ui(name, prompt):
        questions.append(prompt)
        return 'must not be called'
    runtime.dialog_handler = ui
    try:
        await runtime.initialize()
        assert runtime.terminal_input_available
        assert await runtime.terminal_input('x', kind='key', surface='editor') == {'consume': False, 'data': '[x]'}
        assert await runtime.terminal_input('paste\ntext', kind='paste', surface='editor') == {
            'consume': False, 'data': '[paste\ntext]'}
        assert await runtime.terminal_input('drop', kind='key', surface='transcript') == {'consume': True}
        assert await runtime.terminal_input('quiet', kind='key', surface='editor') == {'consume': True}
        assert await runtime.terminal_input('after', kind='key', surface='editor') == {
            'consume': False, 'data': 'after'}
        assert await runtime.command('ext-terminal-count', '') == '4' and not questions
        assert runtime.snapshot()['extensions'][0]['state'] == 'active'
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('data', ['bad', 'hang'])
async def test_failed_or_timed_out_terminal_hooks_never_replay_or_leave_owned_workers(tmp_path, data):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['terminal'], root, tmp_path, timeout=1)
    try:
        await runtime.initialize()
        process = runtime._manager._clients['terminal'].process
        with pytest.raises(ValueError, match='not replayed'):
            await runtime.terminal_input(data, kind='key', surface='editor')
        assert process.returncode is not None and not runtime.terminal_input_available
        assert runtime.snapshot()['extensions'][0]['state'] == 'failed'
        with pytest.raises(ValueError):
            await runtime.command('ext-terminal-count', '')
    finally:
        await runtime.aclose()


async def test_canceling_owned_terminal_callback_reaps_process_without_replay(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['terminal'], root, tmp_path)
    try:
        await runtime.initialize()
        process = runtime._manager._clients['terminal'].process
        task = asyncio.create_task(runtime.terminal_input('hang', kind='key', surface='editor'))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None and not runtime.terminal_input_available
    finally:
        await runtime.aclose()


async def test_real_hook_reload_changes_generation_without_replaying_or_quarantining_new_worker(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    source = root / 'terminal/main.py'
    source.write_text(source.read_text().replace("        if data == 'bad':",
        "        if data == 'delay':\n            await asyncio.sleep(0.2)\n        if data == 'bad':"))
    runtime = ExtensionRuntime(['terminal'], root, tmp_path)
    try:
        await runtime.initialize()
        generation = runtime.generation
        first = asyncio.create_task(runtime.terminal_input('delay', kind='key', surface='prompt'))
        await asyncio.sleep(0.05)
        reload = asyncio.create_task(runtime.manage('reload', 'terminal'))
        with pytest.raises(ValueError, match='selection changed|not replayed'):
            await first
        await reload
        assert runtime.generation > generation and runtime.terminal_input_available
        assert await runtime.terminal_input('fresh', kind='paste', surface='prompt') == {
            'consume': False, 'data': '[fresh]'}
        assert await runtime.command('ext-terminal-count', '') == '1'
        assert runtime.snapshot()['terminal_generation'] == runtime.generation
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('value', [{'terminal_input': 1}, {'terminal_generation': True},
                                  {'terminal_generation': -1}, {'terminal_generation': 2**54}])
def test_terminal_snapshot_types_cannot_be_coerced(value):
    from xdog.agent.extensions.runtime import validate_snapshot
    with pytest.raises(ValueError):
        validate_snapshot({'extensions': [], 'commands': {}, 'ui': {}, **value})


async def test_terminal_registry_unsubscribe_and_command_reregistration_are_live_without_owner_replacement(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['terminal'], root, tmp_path)
    try:
        await runtime.initialize()
        generation = runtime.generation
        original = runtime._manager._clients['terminal']
        assert await runtime.terminal_input('quiet', kind='key', surface='prompt') == {'consume': True}
        assert not runtime.terminal_input_available and not runtime.snapshot()['terminal_input']
        assert await runtime.terminal_input('after', kind='key', surface='prompt') == {
            'consume': False, 'data': 'after'}
        assert await runtime.command('ext-terminal-count', '') == '1'
        assert await runtime.command('ext-terminal-enable', '') == 'Terminal hook enabled'
        assert runtime.terminal_input_available and runtime.generation == generation
        assert runtime._manager._clients['terminal'] is original
        assert await runtime.terminal_input('x', kind='key', surface='prompt') == {
            'consume': False, 'data': '[x]'}
        assert await runtime.command('ext-terminal-count', '') == '2'
        runtime.accept_terminal_registration('terminal', object(), False)
        assert runtime.terminal_input_available  # Metadata from a foreign/retired client is ignored.
        with pytest.raises(ValueError):
            runtime.accept_terminal_registration('terminal', original, 'true')
    finally:
        await runtime.aclose()


async def test_synchronous_owner_close_is_not_a_benign_unsubscribe_generation(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['terminal'], root, tmp_path)
    try:
        await runtime.initialize()
        generation = runtime.generation
        runtime.close()
        assert runtime.generation > generation and not runtime.terminal_input_available
        closed_generation = runtime.generation
        runtime.close()
        assert runtime.generation == closed_generation
        with pytest.raises(ValueError, match='closed'):
            await runtime.terminal_input('never replay', kind='key', surface='prompt')
    finally:
        await runtime.aclose()


def add_release_observer(source, log, *, behavior='continue'):
    """Install a real opt-in observer next to the fixture's unchanged legacy hook."""
    source.write_text(source.read_text() + f'''
    async def observe_release(data, metadata, context):
        import json
        from pathlib import Path
        with Path({str(log)!r}).open('a') as output:
            output.write(json.dumps({{'data': data, **metadata}}) + '\\n')
        if {behavior!r} == 'hang':
            await asyncio.Event().wait()
        if {behavior!r} == 'transform':
            return {{'data': 'forged press', 'consume': True}}
        return {{'consume': False}}
    api.on_terminal_input(observe_release, events=('release',))
''')


@pytest.mark.parametrize('events', [(), ('release', 'release'), ('resize',), ('key', True), 'release', None])
def test_terminal_event_registration_is_explicit_strict_and_nonempty(events):
    api = ExtensionAPI({})
    with pytest.raises(ValueError):
        api.on_terminal_input(lambda *args: None, events=events)
    assert not api.terminal_hooks and api.terminal_events() == []


async def test_real_worker_release_is_opt_in_observation_not_edit_or_legacy_hook(tmp_path):
    import json
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    log = tmp_path / 'releases.jsonl'
    add_release_observer(root / 'terminal/main.py', log)
    runtime = ExtensionRuntime(['terminal'], root, tmp_path)
    try:
        await runtime.initialize()
        assert runtime.terminal_events == ('key', 'paste', 'release')
        assert runtime.snapshot()['terminal_events'] == ['key', 'paste', 'release']
        release = '\x1b[97;1:3u'
        assert await runtime.terminal_input(release, kind='release', surface='prompt') == {'consume': True}
        assert json.loads(log.read_text()) == {'data': release, 'kind': 'release', 'surface': 'prompt'}
        assert await runtime.command('ext-terminal-count', '') == '0'
        assert await runtime.terminal_input('a', kind='key', surface='prompt') == {'consume': False, 'data': '[a]'}
        assert len(log.read_text().splitlines()) == 1
        assert await runtime.command('ext-terminal-count', '') == '1'
    finally:
        await runtime.aclose()


async def test_release_cannot_transform_even_with_consume_true_and_bad_worker_is_reaped(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    add_release_observer(root / 'terminal/main.py', tmp_path / 'releases', behavior='transform')
    runtime = ExtensionRuntime(['terminal'], root, tmp_path)
    try:
        await runtime.initialize()
        process = runtime._manager._clients['terminal'].process
        with pytest.raises(ValueError, match='not replayed'):
            await runtime.terminal_input('\x1b[97;1:3u', kind='release', surface='prompt')
        assert process.returncode is not None
        assert runtime.terminal_events == ()
    finally:
        await runtime.aclose()


async def test_release_continue_chain_keeps_all_opted_in_extensions_and_disposal_live(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    shutil.copytree(root / 'terminal', root / 'observer')
    (root / 'observer/extension.yaml').write_text('name: observer\nentry_point: main\n')
    for name in ('terminal', 'observer'):
        add_release_observer(root / name / 'main.py', tmp_path / name)
    runtime = ExtensionRuntime(['terminal', 'observer'], root, tmp_path)
    try:
        await runtime.initialize()
        assert await runtime.terminal_input('\x1b[97;1:3u', kind='release', surface='child-prompt') == {'consume': True}
        assert (tmp_path / 'terminal').is_file() and (tmp_path / 'observer').is_file()
        await runtime.manage('unload', 'observer')
        await runtime.manage('unload', 'terminal')
        assert runtime.terminal_events == () and not runtime.terminal_input_available
    finally:
        await runtime.aclose()


def test_stale_terminal_disposer_cannot_remove_reregistered_release_observer():
    api = ExtensionAPI({})
    def callback(*args):
        return None
    old = api.on_terminal_input(callback)
    old()
    current = api.on_terminal_input(callback, events=('release',))
    old()
    assert api.terminal_hooks == [callback] and api.terminal_events() == ['release']
    current()
    assert not api.terminal_hooks and not api.terminal_events()


async def test_actual_release_registration_changes_are_live_without_replacing_legacy_hooks(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    source, log = root / 'terminal/main.py', tmp_path / 'release.jsonl'
    add_release_observer(source, log)
    source.write_text(source.read_text().replace(
        "    api.on_terminal_input(observe_release, events=('release',))",
        "    release_disposer = api.on_terminal_input(observe_release, events=('release',))") + '''
    async def observe_off(args, context):
        release_disposer()
        return 'off'
    async def observe_on(args, context):
        nonlocal release_disposer
        release_disposer = api.on_terminal_input(observe_release, events=('release',))
        return 'on'
    api.command('observe_off', 'Disable release observer only', observe_off)
    api.command('observe_on', 'Enable release observer only', observe_on)
''')
    runtime = ExtensionRuntime(['terminal'], root, tmp_path)
    try:
        await runtime.initialize()
        worker, generation = runtime._manager._clients['terminal'], runtime.generation
        assert await runtime.command('ext-terminal-observe_off', '') == 'off'
        assert runtime.terminal_events == ('key', 'paste') and runtime.terminal_input_available
        assert 'terminal_events' not in runtime.snapshot()  # Legacy snapshot shape is unchanged.
        assert await runtime.terminal_input('a', kind='key', surface='prompt') == {'consume': False, 'data': '[a]'}
        assert not log.exists()
        assert await runtime.command('ext-terminal-observe_on', '') == 'on'
        assert runtime.terminal_events == ('key', 'paste', 'release')
        assert await runtime.terminal_input('\x1b[97;1:3u', kind='release', surface='prompt') == {'consume': True}
        assert log.exists() and runtime._manager._clients['terminal'] is worker and runtime.generation == generation
    finally:
        await runtime.aclose()
