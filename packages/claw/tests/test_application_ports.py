"""Generic application capabilities cannot select receipt owners or storage roots."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.outputs.targets import ReplyReference, encode_reply
from xdog.claw.core.runtime.application_ports import ApplicationPorts
from xdog.claw.core.types import UserInput


def host(tmp_path):
    roots = {'first': tmp_path / 'first', 'second': tmp_path / 'second'}
    application = SimpleNamespace(data_dir=tmp_path / 'data', get_group_workspace=roots.get,
                                  input_service=AsyncMock(), output_service=AsyncMock())
    return ApplicationPorts(application, None), application, roots


async def test_receipt_ports_refuse_unknown_owner_wrong_workspace_and_retired_binding_before_io(tmp_path):
    ports, application, roots = host(tmp_path)
    original = roots['first']
    for owner, workspace in [('missing', original), ('first', roots['second']), ('first', original / '..')]:
        for method, arguments in [(ports.input_namespace, ()), (ports.input_get, ('id',)),
                                  (ports.input_attempts, ('id',)), (ports.output_records, ()),
                                  (ports.cancel_outputs, (('id',),))]:
            with pytest.raises(ValueError, match='owner/workspace'):
                await method(owner, workspace, *arguments)
    application.input_service.assert_not_called()
    application.output_service.assert_not_called()

    # An actual service await cannot transfer a previously checked scope.
    async def changed():
        roots['first'] = tmp_path / 'replacement'
        return SimpleNamespace(store=AsyncMock())

    application.input_service.side_effect = changed
    with pytest.raises(ValueError, match='owner/workspace'):
        await ports.input_get('first', original, 'id')
    application.input_service.assert_awaited_once()


def test_capture_reply_requires_actual_host_reference_with_original_registered_scope(tmp_path):
    ports, application, roots = host(tmp_path)
    reference = ReplyReference(InputScope('first', roots['first']), 'original-channel',
                               UserInput(group_id='first', sender='original-peer', channel='tui'))
    encoded = ports.capture_reply({'group_id': 'first', '_reply_reference': reference})
    assert encoded == encode_reply(reference) and json.loads(encoded)['recipient'] == 'original-peer'
    assert ports.capture_reply({'group_id': 'first'}) is None
    for context in [
        {'group_id': 'second', '_reply_reference': reference},
        {'group_id': 'missing', '_reply_reference': reference},
        {'group_id': 'first', '_reply_reference': encoded},
        {'group_id': 'first', '_reply_reference': json.loads(encoded)},
    ]:
        with pytest.raises(ValueError):
            ports.capture_reply(context)
    roots['first'] = tmp_path / 'replacement'
    with pytest.raises(ValueError, match='scope'):
        ports.capture_reply({'group_id': 'first', '_reply_reference': reference})
    application.input_service.assert_not_called()
    application.output_service.assert_not_called()


async def test_injected_database_is_data_root_bounded_and_uses_real_private_lease_no_follow(tmp_path):
    ports, _, _ = host(tmp_path)
    async def open_at(path):
        return await ports.open_database(path, format='generic.test.v1', schema='CREATE TABLE generic(value TEXT);',
                                          validate=lambda db: None, recover=lambda db: None)

    for path in [tmp_path / 'outside' / 'journal.sqlite3', ports.data_dir,
                 ports.data_dir / '..' / 'escaped' / 'journal.sqlite3']:
        with pytest.raises(ValueError, match='data_dir'):
            await open_at(path)
    assert not ports.data_dir.exists() and not (tmp_path / 'outside').exists()
    path = ports.data_dir / 'feature' / 'journal.sqlite3'
    database = await open_at(path)
    try:
        assert database.path == path
        await database.disk(lambda db: db.execute('INSERT INTO generic VALUES (?)', ('original',)))
        with pytest.raises(BlockingIOError):
            await open_at(path)
    finally:
        await database.close()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (ports.data_dir / 'redirect').symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        await open_at(ports.data_dir / 'redirect' / 'journal.sqlite3')
    assert not list(outside.iterdir())
    (ports.data_dir / 'redirect-file.sqlite3').symlink_to(path)
    before = path.read_bytes()
    with pytest.raises((OSError, ValueError)):
        await open_at(ports.data_dir / 'redirect-file.sqlite3')
    assert path.read_bytes() == before
