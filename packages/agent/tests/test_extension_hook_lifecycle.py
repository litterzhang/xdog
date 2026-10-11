from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.hooks import declaration
from xdog.agent.extensions.input import InputRejected
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.worker import dispatch
from xdog.ai.types import UserMessage


async def test_snapshot_dispatch_self_disposal_new_listener_next_round_and_old_disposer_identity():
    api, calls = ExtensionAPI({}), []
    async def first(context):
        calls.append('first')
        off()
        api.on('turn_start', third)
    async def second(context):
        calls.append('second')
    async def third(context):
        calls.append('third')
    off = api.on('turn_start', first)
    api.on('turn_start', second)
    await dispatch(api, 'xdog/event', {'event': 'turn_start'})
    assert calls == ['first', 'second']  # Removing first cannot skip the already admitted second.
    off()
    await dispatch(api, 'xdog/event', {'event': 'turn_start'})
    assert calls == ['first', 'second', 'second', 'third']
    revision = api._hook_revision
    off()
    assert api._hook_revision == revision
    replacement = api.on('turn_start', first)
    off()
    assert len(api.events['turn_start']) == 3
    replacement()


@pytest.mark.parametrize('kind', ['pre', 'post', 'context', 'input', 'event'])
def test_each_hook_family_has_bounded_callable_registration_and_idempotent_disposal(kind):
    api = ExtensionAPI({})
    register = {'pre': api.before_tool, 'post': api.after_tool, 'context': api.context,
                'input': api.on_input, 'event': lambda callback: api.on('turn_start', callback)}[kind]
    with pytest.raises(ValueError):
        register('not callable')
    disposers = [register(lambda *args: None) for _ in range(32)]
    with pytest.raises(ValueError):
        register(lambda *args: None)
    for dispose in disposers:
        dispose()
        dispose()
    current = api.hook_snapshot()
    assert not any(current[key] for key in ('pre', 'post', 'context', 'input')) and not current['events']


@pytest.mark.parametrize('changes', [{'revision': True}, {'events': ['unknown']}, {'events': ['turn_start'] * 2},
    {'pre': 1}, {'callback': 'private'}, {'input': 'true'}])
def test_live_hook_metadata_does_not_coerce_or_accept_callback_objects(changes):
    with pytest.raises(ValueError):
        declaration({**ExtensionAPI({}).hook_snapshot(), **changes})


async def test_real_worker_dynamic_hooks_disposal_reenable_stale_metadata_and_fail_closed_validation(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['hookmock'], root, tmp_path)
    try:
        await runtime.initialize()
        client = runtime._manager._clients['hookmock']
        process = client.process
        assert await runtime.command('ext-hookmock-enable', '') == 'HOOKS-ENABLED'
        assert runtime._infos['hookmock']['events'] == ['turn_start']
        assert 'hookmock' in runtime._input_required
        old = dict(client._hook_snapshot)
        await runtime.event('turn_start')
        assert await runtime.pre('safe', {}) == {}
        assert await runtime.post('safe', {}, 'text') == 'text'
        assert await runtime.transform([]) == []
        assert (await runtime.input(UserMessage(content='draft'))).content[0].text == 'LISTENER-draft'
        assert json.loads(await runtime.command('ext-hookmock-status', '')) == dict.fromkeys(
            ('event', 'pre', 'post', 'context', 'input'), 1)
        await runtime.command('ext-hookmock-enable', '')
        await runtime.command('ext-hookmock-old', '')
        await runtime.event('turn_start')
        assert json.loads(await runtime.command('ext-hookmock-status', ''))['event'] == 2
        await runtime.command('ext-hookmock-disable', '')
        assert not runtime._infos['hookmock']['events'] and 'hookmock' not in runtime._input_required
        runtime.accept_hook_registration('hookmock', client, old)
        assert not runtime._infos['hookmock']['events']  # An old metadata receipt cannot re-arm listeners.
        assert (await runtime.input(UserMessage(content='draft'))).content == 'draft'
        await runtime.event('turn_start')
        assert json.loads(await runtime.command('ext-hookmock-status', ''))['event'] == 2
        assert runtime._manager._clients['hookmock'].process is process and process.returncode is None
        await runtime.command('ext-hookmock-enable', '')
        runtime.fail('hookmock')
        runtime.accept_hook_registration('hookmock', client, {**old, 'revision': 999, 'input': False})
        with pytest.raises(InputRejected, match='unavailable'):
            await runtime.input(UserMessage(content='must not bypass failed validation'))
    finally:
        await runtime.aclose()


async def test_conflicting_hook_revision_and_retired_worker_metadata_never_change_current_registration(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['hookmock'], root, tmp_path)
    try:
        await runtime.command('ext-hookmock-enable', '')
        client = runtime._manager._clients['hookmock']
        current = dict(client._hook_snapshot)
        with pytest.raises(ValueError, match='Conflicting'):
            runtime.accept_hook_registration('hookmock', client, {**current, 'input': False})
        assert runtime._infos['hookmock']['input'] and 'hookmock' in runtime._input_required
        await runtime.manage('reload', 'hookmock')
        replacement = runtime._manager._clients['hookmock']
        assert replacement is not client
        assert not runtime._infos['hookmock']['input']
        runtime.accept_hook_registration('hookmock', client, {**current, 'revision': 999})
        assert not runtime._infos['hookmock']['input'] and replacement._hook_snapshot['revision'] == 0
    finally:
        await runtime.aclose()
