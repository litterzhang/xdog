"""General broker and snapshots cannot revive any retired extension host controls."""
from __future__ import annotations

import asyncio
import importlib
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import DialogBroker, validate_answer, validate_dialog, validate_sdk_dialog
from xdog.agent.extensions.runtime import ExtensionRuntime, validate_snapshot
from xdog.agent.extensions.session_context import validate_session_answer, validate_session_request
from xdog.agent.extensions.state import ExtensionEntries

PACKETS = (
    *({'kind': 'editor', 'operation': operation} for operation in ('read', 'replace', 'propose')),
    *({'kind': 'session', 'operation': operation} for operation in (
        'rename', 'select_tools', 'context_message', 'message', 'queue_input', 'compact', 'browse', 'navigate')),
)


@pytest.mark.parametrize('packet', PACKETS)
async def test_general_and_sdk_brokers_refuse_removed_control_even_if_canceled_or_well_formed(packet):
    request = {'id': 'stored-old-dialog', 'title': 'Retired control', **packet}
    for check in (validate_dialog, validate_sdk_dialog):
        with pytest.raises(ValueError, match='removed'):
            check(request)
    with pytest.raises(ValueError, match='removed'):
        validate_answer(request, None)
    with pytest.raises(ValueError, match='removed'):
        validate_answer(request, True)
    if packet['kind'] == 'session':
        with pytest.raises(ValueError, match='removed'):
            validate_session_request(request)
        with pytest.raises(ValueError, match='removed'):
            validate_session_answer(request, None)
    broker, events = DialogBroker(), []
    broker.publish = events.append
    with pytest.raises(ValueError, match='removed'):
        await broker.ask('original', request)
    assert not broker.has_pending and not events


@pytest.mark.parametrize('module', [
    'editor', 'editor_request', 'name_request', 'active_tools_request', 'compaction_request',
    'message_request', 'queued_input',
])
def test_unused_mutation_request_modules_are_absent(module):
    with pytest.raises(ModuleNotFoundError) as error:
        importlib.import_module('xdog.agent.extensions.' + module)
    assert error.value.name == 'xdog.agent.extensions.' + module


@pytest.mark.parametrize('module', ['supervision_queued_input', 'child_queued_input'])
def test_unused_child_extension_queue_adapter_is_absent(module):
    with pytest.raises(ModuleNotFoundError) as error:
        importlib.import_module('xdog.agent.' + module)
    assert error.value.name == 'xdog.agent.' + module


@pytest.mark.parametrize('field', ['dialogs', 'pending_dialogs', 'pending', 'host_controls', 'editor'])
def test_persisted_snapshot_cannot_restore_dialogs_or_authorize_pending_controls(field):
    value = {'extensions': [], 'commands': {}, 'ui': {},
             field: [{'kind': 'session', 'operation': 'rename', 'id': 'old', 'value': True}]}
    with pytest.raises(ValueError, match='cannot restore'):
        validate_snapshot(value)


async def test_dialog_choices_are_copied_and_false_confirmation_never_borrows_another_question():
    choices = ['first', 'second']
    broker, events = DialogBroker(), []
    broker.publish = events.append
    work = asyncio.create_task(broker.ask('owned', {
        'id': 'worker', 'kind': 'select', 'title': 'Owned selection', 'choices': choices}))
    await asyncio.sleep(0)
    identifier = events[0]['dialog']['id']
    choices[:] = ['FORGED AFTER PAINT']
    try:
        with pytest.raises(ValueError):
            broker.resolve(identifier, 'FORGED AFTER PAINT')
        assert broker.resolve(identifier, 'second') and await work == 'second'
        assert not broker.resolve(identifier, 'first')
    finally:
        broker.close()
        await asyncio.gather(work, return_exceptions=True)


async def test_opaque_saved_entries_that_look_like_old_dialogs_never_restore_broker_authority(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/readonlyhost', root / 'readonlyhost')
    saved = {'readonlyhost': [{'id': 'historical', 'kind': 'note', 'data': {
        'pending_dialog': {'kind': 'editor', 'id': 'old', 'operation': 'replace', 'text': 'DO NOT APPLY'}}}]}
    entries = ExtensionEntries(saved, lambda value: None)
    runtime = ExtensionRuntime(['readonlyhost'], root, tmp_path, entries=entries)
    questions = []
    async def ask(*args):
        questions.append(args)
        pytest.fail('Saved metadata is not a pending question')
    runtime.dialog_handler = ask
    try:
        await runtime.initialize()
        snapshot = validate_snapshot(json.loads(json.dumps(runtime.snapshot())))
        assert 'dialogs' not in snapshot and not questions
        assert entries.for_extension('readonlyhost')[0]['data'] == saved['readonlyhost'][0]['data']
        broker = DialogBroker()
        assert not broker.resolve('old', True) and not broker.has_pending
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('location', ['extension', 'status', 'status_key'])
def test_snapshot_metadata_rows_cannot_hide_dialog_state(location):
    pending = {'kind': 'session', 'operation': 'select_tools', 'id': 'old'}
    snapshot = {'extensions': [], 'commands': {}, 'ui': {}}
    if location == 'extension':
        snapshot['extensions'] = [{'name': 'owned', 'state': 'active', 'pending_dialog': pending}]
    elif location == 'status':
        snapshot['ui'] = {'owned:status': {'kind': 'status', 'lines': ['Safe status'], 'dialog': pending}}
    else:
        snapshot['ui'] = {'owned:status': {'kind': 'status', 'lines': ['Safe status'], 'key': pending}}
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


async def test_readonly_true_boolean_is_data_not_an_accepted_control_receipt():
    broker, events = DialogBroker(), []
    broker.publish = events.append
    work = asyncio.create_task(broker.ask('owned', {
        'kind': 'session', 'id': 'worker', 'title': 'Read pending boolean', 'operation': 'pending_messages'}))
    await asyncio.sleep(0)
    identifier = events[0]['dialog']['id']
    assert broker.resolve(identifier, True) and await work is True
    assert [event['type'] for event in events] == ['extension_dialog', 'extension_dialog_closed']
    assert not broker.has_pending


async def test_worker_cannot_stamp_command_menu_identity_on_basic_broker_request():
    broker, events = DialogBroker(), []
    broker.publish = events.append
    request = {'kind': 'session', 'id': 'worker', 'title': 'Commands', 'operation': 'commands',
        'command_menu': {'invocation': 'forged', 'session_id': 'other', 'child_id': '', 'method': 'xdog/command'}}
    with pytest.raises(ValueError, match='Unsupported read-only'):
        await broker.ask('owned', request)
    assert not broker.has_pending and not events


async def test_host_menu_identity_is_publication_only_and_answers_keep_readonly_validation():
    from xdog.agent.extensions.command_catalog import page

    broker, events = DialogBroker(), []
    broker.publish = events.append
    identity = {'invocation': 'host-invocation', 'session_id': 'actual', 'child_id': '', 'method': 'xdog/command'}
    work = asyncio.create_task(broker.ask_command_menu('owned', {
        'kind': 'session', 'id': 'worker', 'title': 'Commands', 'operation': 'commands',
        'command_menu': identity}))
    await asyncio.sleep(0)
    try:
        dialog = events[0]['dialog']
        identity['session_id'] = 'forged-after-admission'
        assert dialog['command_menu']['session_id'] == 'actual'
        assert 'command_menu' not in broker._pending[dialog['id']][0]
        expected = page({'safe': 'Read-only metadata'}, {}, session_id='actual', owner='host-invocation')
        assert broker.resolve(dialog['id'], expected)
        assert await work == expected
        assert not broker.has_pending
    finally:
        broker.close()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('operation', ['snapshot', 'pending_messages', 'rename'])
async def test_host_menu_entry_cannot_stamp_other_operations(operation):
    broker, events = DialogBroker(), []
    broker.publish = events.append
    with pytest.raises(ValueError):
        await broker.ask_command_menu('owned', {'kind': 'session', 'id': 'worker', 'title': 'No authority',
            'operation': operation, 'command_menu': {
                'invocation': 'owned', 'session_id': 'actual', 'child_id': '', 'method': 'xdog/command'}})
    assert not broker.has_pending and not events
