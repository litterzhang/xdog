"""Trusted native callbacks share command routing, not worker/tool authority."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.command_catalog import page
from xdog.agent.extensions.runtime import ExtensionRuntime, validate_snapshot

FIXTURE = Path(__file__).parent / 'fixtures/extensions/publiccommands'


def runtime_at(tmp_path, enabled=()):
    return ExtensionRuntime(enabled, tmp_path / 'extensions', tmp_path)


async def test_native_query_dispatch_snapshot_help_and_dispose_never_start_workers(tmp_path):
    runtime = runtime_at(tmp_path, ('display',))
    actual, unselected = [], []

    async def callback(args):
        actual.append(args)
        return 'actual:' + args

    async def foreign(args):
        unselected.append(args)
        return 'wrong'

    declarations = {'inspect-state': ('Inspect pinned state', callback)}
    dispose = runtime.bind_native_commands('records', declarations, valid=lambda: True)
    # Caller mutation and JSON-looking arguments cannot select another object.
    declarations['inspect-state'] = ('Replacement', foreign)
    try:
        assert runtime.commands() == {'inspect-state': 'Inspect pinned state'}
        assert runtime.command_owner('inspect-state') == 'records'
        assert runtime.command_owner('ext-records-inspect-state') == 'records'
        assert runtime.command_conflicts() == {}
        args = '{"service":"foreign","owner":"other","authorized":true}'
        assert await runtime.command('inspect-state', args) == 'actual:' + args
        assert actual == [args] and unselected == []
        snapshot = validate_snapshot(runtime.snapshot())
        assert snapshot['command_owners'] == {'inspect-state': 'records'}
        assert snapshot['extensions'][-1] == {'name': 'records', 'state': 'active'}
        help_page = page(runtime.commands(), {}, session_id='session', owner='host')
        assert help_page['commands'] == [{'name': '/inspect-state', 'description': 'Inspect pinned state'}]
        assert not hasattr(runtime, "tools") and not runtime._manager._clients and runtime._manager._thread is None
        dispose()
        dispose()
        assert not runtime.commands() and runtime.command_owner('inspect-state') is None
        with pytest.raises(ValueError, match='stale'):
            await runtime.command('inspect-state', 'retired')
        assert runtime._manager._thread is None
        assert validate_snapshot(runtime.snapshot())
    finally:
        await runtime.aclose()
    assert runtime._manager._thread is None


async def test_explicit_skill_shadow_never_overrides_host_or_extension_and_keeps_skill_object(tmp_path):
    runtime = runtime_at(tmp_path)

    async def callback(args):
        return args

    skill = object()  # The existing skill/tool loader is independent of this catalog.
    installed_skills = {'records': skill}
    runtime.reserve_commands({'quit'}, skill_commands={'records'})
    try:
        with pytest.raises(ValueError, match='reserved'):
            runtime.bind_native_commands('first', {'records': ('Inspect', callback)}, valid=lambda: True)
        dispose = runtime.bind_native_commands('first', {'records': ('Inspect', callback)},
                                               valid=lambda: True, shadow_skills={'records'})
        assert await runtime.command('records', 'text') == 'text'
        assert installed_skills['records'] is skill and not runtime.command_conflicts()
        runtime.reserve_commands({'quit', 'ext-first-records'}, skill_commands={'records'})
        assert runtime.command_owner('ext-first-records') is None
        with pytest.raises(ValueError, match='conflict'):
            await runtime.command('ext-first-records', '')
        assert await runtime.command('records', 'still current') == 'still current'
        with pytest.raises(ValueError, match='registered'):
            runtime.bind_native_commands('second', {'records': ('Other', callback)},
                                         valid=lambda: True, shadow_skills={'records'})
        with pytest.raises(ValueError, match='reserved'):
            runtime.bind_native_commands('second', {'quit': ('Exit', callback)},
                                         valid=lambda: True, shadow_skills={'quit'})
        # A builtin remains reserved even if the same string is also a skill.
        runtime.reserve_commands({'quit', 'records'}, skill_commands={'records'})
        assert runtime.command_conflicts() == {'records': 'first'}
        assert not runtime.commands()
        with pytest.raises(ValueError, match='conflict'):
            await runtime.command('records', '')
        assert validate_snapshot(runtime.snapshot())
        dispose()
        runtime.reserve_commands({'quit'}, skill_commands={'records'})
        assert not runtime.command_conflicts() and installed_skills['records'] is skill
    finally:
        await runtime.aclose()


async def test_legacy_reservations_and_late_skill_collisions_stay_fail_closed(tmp_path):
    runtime = runtime_at(tmp_path)

    async def callback(args):
        return args

    dispose = runtime.bind_native_commands('first', {'inspect': ('Inspect', callback)}, valid=lambda: True)
    try:
        runtime.reserve_commands(set(), skill_commands={'inspect'})
        assert runtime.command_conflicts() == {'inspect': 'first'}
        assert not runtime.commands()
        with pytest.raises(ValueError, match='conflict'):
            await runtime.command('inspect', '')
        dispose()
        runtime.reserve_commands({'inspect'})
        with pytest.raises(ValueError, match='reserved'):
            runtime.bind_native_commands('first', {'inspect': ('Inspect', callback)},
                                         valid=lambda: True, shadow_skills={'inspect'})
    finally:
        await runtime.aclose()


async def test_stale_owner_cannot_invoke_or_publish_and_old_disposer_cannot_remove_successor(tmp_path):
    runtime = runtime_at(tmp_path, ('display',))
    valid = True
    entered, release = asyncio.Event(), asyncio.Event()

    async def callback(args):
        entered.set()
        await release.wait()
        return 'old output'

    dispose = runtime.bind_native_commands('records', {'inspect': ('Inspect', callback)}, valid=lambda: valid)
    call = asyncio.create_task(runtime.command('inspect', ''))
    await entered.wait()
    valid = False
    assert not runtime.commands() and runtime.command_owner('inspect') is None
    with pytest.raises(ValueError, match='stale'):
        await runtime.command('inspect', '')
    assert runtime._manager._thread is None

    async def current(args):
        return 'new output'

    current_dispose = runtime.bind_native_commands('records', {'inspect': ('Current', current)}, valid=lambda: True)
    dispose()
    release.set()
    try:
        with pytest.raises(ValueError, match='stale'):
            await call
        assert await runtime.command('inspect', '') == 'new output'
        assert validate_snapshot(runtime.snapshot())['command_owners'] == {'inspect': 'records'}
        current_dispose()
        assert not runtime.commands()
    finally:
        await runtime.aclose()


async def test_dispose_during_call_retires_exact_callback_before_late_output(tmp_path):
    runtime = runtime_at(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def callback(args):
        entered.set()
        await release.wait()
        return args

    dispose = runtime.bind_native_commands('records', {'inspect': ('Inspect', callback)}, valid=lambda: True)
    call = asyncio.create_task(runtime.command('inspect', 'late'))
    await entered.wait()
    dispose()
    release.set()
    try:
        with pytest.raises(ValueError, match='stale'):
            await call
        assert not runtime.commands()
    finally:
        await runtime.aclose()


async def test_close_and_repeated_waiter_cancellation_join_actual_native_cleanup(tmp_path):
    runtime = runtime_at(tmp_path)
    entered, stopping, release, stopped = (asyncio.Event() for _ in range(4))

    async def callback(args):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopping.set()
            await release.wait()
            stopped.set()

    runtime.bind_native_commands('records', {'inspect': ('Inspect', callback)}, valid=lambda: True)
    call = asyncio.create_task(runtime.command('inspect', ''))
    await entered.wait()
    runtime.close()  # Synchronous retirement; asynchronous closure is the real join.
    assert not runtime.commands() and not runtime.snapshot().get('command_owners')
    close = asyncio.create_task(runtime.aclose())
    await stopping.wait()
    for _ in range(3):
        close.cancel()
        await asyncio.sleep(0)
        assert not close.done() and not call.done() and not stopped.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await close
    with pytest.raises(asyncio.CancelledError):
        await call
    await runtime.aclose()
    assert stopped.is_set() and not runtime._native_commands._tasks
    assert runtime._manager._thread is None
    with pytest.raises(ValueError, match='closed'):
        runtime.bind_native_commands('new', {'inspect': ('New', callback)}, valid=lambda: True)


async def test_command_caller_cancellation_also_joins_cleanup(tmp_path):
    runtime = runtime_at(tmp_path)
    entered, stopping, release, stopped = (asyncio.Event() for _ in range(4))

    async def callback(args):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopping.set()
            await release.wait()
            stopped.set()

    runtime.bind_native_commands('records', {'inspect': ('Inspect', callback)}, valid=lambda: True)
    call = asyncio.create_task(runtime.command('inspect', ''))
    await entered.wait()
    call.cancel()
    await stopping.wait()
    for _ in range(3):
        call.cancel()
        await asyncio.sleep(0)
        assert not call.done()
    release.set()
    try:
        with pytest.raises(asyncio.CancelledError):
            await call
        assert stopped.is_set()
    finally:
        await runtime.aclose()


async def test_actual_callback_errors_and_bad_result_types_are_not_hidden_as_success(tmp_path):
    runtime = runtime_at(tmp_path)
    error = OSError('actual command failure')

    async def failing(args):
        raise error

    async def invalid(args):
        return {'authorized': True}

    runtime.bind_native_commands('records', {'inspect': ('Inspect', failing), 'invalid': ('Invalid', invalid)},
                                 valid=lambda: True)
    try:
        with pytest.raises(OSError) as failure:
            await runtime.command('inspect', '')
        assert failure.value is error
        with pytest.raises(ValueError, match='text'):
            await runtime.command('invalid', '')
    finally:
        await runtime.aclose()


async def test_selected_worker_identity_including_display_profile_cannot_be_elevated(tmp_path):
    runtime = runtime_at(tmp_path, ('display',))
    runtime._display_only.add('display')

    async def callback(args):
        return args

    try:
        for extension in ('display', 'Display'):
            with pytest.raises(ValueError, match='identity.*worker'):
                runtime.bind_native_commands(extension, {'inspect': ('Inspect', callback)}, valid=lambda: True)
        assert not runtime.commands() and not hasattr(runtime, "tools")
        assert not runtime._manager._clients and runtime._manager._thread is None
    finally:
        await runtime.aclose()


async def test_native_and_real_worker_contributions_use_one_conflict_catalog(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root / 'publiccommands')
    runtime = ExtensionRuntime(('publiccommands',), root, tmp_path)

    async def callback(args):
        return args

    try:
        await runtime.initialize()
        with pytest.raises(ValueError, match='registered'):
            runtime.bind_native_commands('records', {'inspect-state': ('Inspect', callback)}, valid=lambda: True)
        dispose = runtime.bind_native_commands('records', {'native-state': ('Native', callback)}, valid=lambda: True)
        assert await runtime.command('native-state', 'native') == 'native'
        assert await runtime.command('inspect-state', 'worker') == 'INSPECT:worker'
        assert validate_snapshot(runtime.snapshot())['command_owners']['native-state'] == 'records'
        dispose()
        # Failed native admission never steals the established worker route.
        assert runtime.command_owner('inspect-state') == 'publiccommands'
    finally:
        await runtime.aclose()


async def test_real_worker_cannot_take_over_an_existing_native_command(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root / 'publiccommands')
    runtime = ExtensionRuntime(('publiccommands',), root, tmp_path)

    async def callback(args):
        return 'native:' + args

    runtime.bind_native_commands('records', {'inspect-state': ('Native', callback)}, valid=lambda: True)
    try:
        await runtime.initialize()
        assert 'publiccommands' in runtime._failed
        assert runtime.command_owner('inspect-state') == 'records'
        assert await runtime.command('inspect-state', 'actual') == 'native:actual'
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('extension,commands,shadow', [
    ('invalid-id', {'inspect': ('Inspect', lambda args: args)}, set()),
    ('records', {'ext-records-inspect': ('Inspect', lambda args: args)}, set()),
    ('records', {'inspect': ['Not actual tuple', 'not callable']}, set()),
    ('records', {'inspect': ('Inspect', lambda args: args)}, {'other'}),
])
async def test_native_binding_requires_actual_bounded_objects_not_data_grants(tmp_path, extension, commands, shadow):
    runtime = runtime_at(tmp_path)
    try:
        with pytest.raises(ValueError):
            runtime.bind_native_commands(extension, commands, valid=lambda: True, shadow_skills=shadow)
        assert not runtime.commands() and runtime._manager._thread is None
    finally:
        await runtime.aclose()


async def test_declared_native_overlap_keeps_actual_same_name_skill_loading_independent(tmp_path):
    from xdog.agent.skills import SkillManager

    directory = tmp_path / 'packaged' / 'records'
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text('---\nname: records\ndescription: Record guidance\n---\nActual skill body.')
    skills = SkillManager(tmp_path / 'shared', packaged={'records': directory})
    runtime = runtime_at(tmp_path)

    async def callback(args):
        return 'native command'

    try:
        runtime.reserve_commands({'quit'}, skill_commands={skill.slug for skill in skills.list_skills()})
        runtime.bind_native_commands('native', {'records': ('Records command', callback)},
                                     valid=lambda: True, shadow_skills={'records'})
        assert await runtime.command('records', '') == 'native command'
        assert skills.load_skill('records').content == 'Actual skill body.'
        assert not hasattr(runtime, "tools") and runtime._manager._thread is None
    finally:
        await runtime.aclose()


async def test_close_joins_all_native_callbacks_and_manager_even_after_cleanup_error(tmp_path, monkeypatch):
    runtime = runtime_at(tmp_path)
    entered = [asyncio.Event(), asyncio.Event()]
    stopping = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    error = OSError('actual callback cleanup failed')
    manager_closed = []
    original_close = runtime._manager.aclose

    async def manager_close():
        manager_closed.append(True)
        await original_close()

    async def callback(args):
        index = int(args)
        entered[index].set()
        try:
            await asyncio.Future()
        finally:
            stopping[index].set()
            if index:
                await release.wait()
            else:
                raise error

    monkeypatch.setattr(runtime._manager, 'aclose', manager_close)
    runtime.bind_native_commands('native', {'inspect': ('Inspect', callback)}, valid=lambda: True)
    calls = [asyncio.create_task(runtime.command('inspect', str(index))) for index in range(2)]
    await asyncio.gather(*(event.wait() for event in entered))
    runtime.close()
    await asyncio.gather(*(event.wait() for event in stopping))
    # A callback may already have failed before the asynchronous join begins.
    await asyncio.sleep(0)
    closing = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    assert not closing.done() and not manager_closed
    release.set()
    with pytest.raises(OSError) as failure:
        await closing
    assert failure.value is error and manager_closed == [True]
    await asyncio.gather(*calls, return_exceptions=True)
    assert not runtime._native_commands._tasks
    assert runtime._manager._thread is None


async def test_native_registration_during_initialization_never_becomes_a_worker_rpc(tmp_path, monkeypatch):
    runtime = runtime_at(tmp_path)
    called = []

    async def callback(args):
        called.append(args)
        return 'late bound'

    async def initialize():
        runtime.bind_native_commands('native', {'inspect': ('Inspect', callback)}, valid=lambda: True)

    monkeypatch.setattr(runtime, 'initialize', initialize)
    try:
        assert await runtime.command('inspect', 'actual') == 'late bound'
        assert called == ['actual'] and runtime._manager._thread is None
    finally:
        await runtime.aclose()
