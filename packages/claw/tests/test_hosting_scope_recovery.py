"""Semantic v3 refusal and per-owner report isolation through actual generic ports."""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from xdog.agent.session_input import decode_input
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.outputs.routes import OutputRoute, apply_route
from xdog.claw.core.outputs.store import OutputStore
from xdog.claw.core.outputs.targets import ReplyReference, encode_reply
from xdog.claw.core.runtime.application_ports import ApplicationPorts
from xdog.claw.core.types import UserInput
from xdog.eyes.hosting.journal import ObserverJournal
from xdog.eyes.hosting.manager import ManagedObservers


def message(identifier):
    return {'id': identifier, 'origin': 'acp', 'source': 'Eyes', 'instruction': 'Never run a model',
            'evidence': [], 'provenance': {}}


@asynccontextmanager
async def application(root):
    roots = {'g1': root / 'first', 'g2': root / 'second'}
    inputs = await InputStore.open(root / 'data/inputs/inbox.sqlite3')
    outputs = await OutputStore.open(root / 'data/outputs/journal.sqlite3')
    for owner, cwd in roots.items():
        await inputs.bind_scope(InputScope(owner, cwd))
    subscriptions, routes = set(), {}

    def subscribe(callback):
        subscriptions.add(callback)
        return lambda: subscriptions.discard(callback)

    def register(name, resolve, policy):
        routes[name] = resolve, policy
        return lambda: routes.pop(name)

    app = SimpleNamespace(data_dir=root / 'data', get_group_workspace=roots.get,
                          input_service=AsyncMock(return_value=SimpleNamespace(store=inputs)),
                          output_service=AsyncMock(return_value=SimpleNamespace(store=outputs, subscribe=subscribe)),
                          subscribe_inputs=subscribe, register_output_source=register)
    host = ApplicationPorts(app, root / 'private.sock')
    try:
        yield SimpleNamespace(host=host, app=app, roots=roots, inputs=inputs, outputs=outputs,
                              subscriptions=subscriptions, routes=routes)
    finally:
        await outputs.close()
        await inputs.close()


@pytest.mark.parametrize('field,value', [
    ('group_id', ''), ('group_id', '  '), ('group_id', 'bad\x00owner'), ('group_id', '界' * 86),
    ('cwd', 'relative'), ('cwd', '/tmp/../escape'), ('cwd', '/tmp/\x00bad'), ('cwd', '/' + '界' * 1366),
])
async def test_invalid_replyless_scope_is_refused_before_recovery_byte_preserving(tmp_path, field, value):
    async with application(tmp_path) as app:
        path = app.host.data_dir / 'observers/journal.sqlite3'
        journal = await ObserverJournal.open(path, app.host)
        record = await journal.create('g1', app.roots['g1'], 'original', {}, reply=None)
        await journal.save(record, {'pending': 'original'}, (message('original'),))
        assert await journal.transport_started(record, 'original')
        await journal.close()
        # Preserve parent/outbox binding and SQLite integrity; only semantic
        # scope validation can reject this. There is intentionally no reply.
        with sqlite3.connect(path) as db:
            db.execute(f'UPDATE observers SET {field}=?', (value,))
            db.execute(f'UPDATE outbox SET {field}=?', (value,))
        before = path.read_bytes()
        with pytest.raises(ValueError, match='observer owner/workspace scope'):
            await ObserverJournal.open(path, app.host)
        assert path.read_bytes() == before
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT transport_status FROM outbox').fetchone() == ('sending',)


async def test_readonly_scope_validation_does_not_consult_live_registration(tmp_path, monkeypatch):
    async with application(tmp_path) as app:
        path = app.host.data_dir / 'observers/journal.sqlite3'
        journal = await ObserverJournal.open(path, app.host)
        record = await journal.create('g1', app.roots['g1'], 'old-owner', {}, reply=None)
        await journal.close()
        app.roots.pop('g1')
        live = Mock(side_effect=AssertionError('Readonly v3 validation must not query live registration'))
        monkeypatch.setattr(app.host, 'workspace', live)
        restored = await ObserverJournal.open(path, app.host)
        try:
            assert await restored.get('g1', record.cwd, record.id) == record
            live.assert_not_called()
        finally:
            await restored.close()


async def accepted(manager, app, owner, identifier, record=None):
    cwd = app.roots[owner]
    scope = InputScope(owner, cwd)
    reference = ReplyReference(scope, 'original-' + owner,
                               UserInput(group_id=owner, sender='original-peer-' + owner, channel='tui'))
    if record is None:
        record = await manager.store.create(owner, cwd, identifier, {}, recovery='ephemeral',
                                             reply=encode_reply(reference))
    payload = message(identifier)
    await manager.store.save(record, {'pending': identifier}, (payload,))
    assert await manager.store.transport_started(record, identifier)
    await manager.store.transport_finished(record, identifier, status='queued')
    envelope = apply_route(InputEnvelope(scope, decode_input(payload), context={'kind': 'external'}),
                           OutputRoute('observations', reference))
    await app.inputs.enqueue(envelope)
    running = await app.inputs.start(await app.inputs.claim(scope))
    receipt = await app.inputs.finish(running, 'succeeded')
    output = await app.outputs.append(envelope, receipt.attempt_id, 0, reference.binding, 'original output')
    sending = await app.outputs.begin(output.id)
    await app.outputs.settle(output.id, sending.send_id, 'delivered')
    return await manager.store.get(owner, cwd, record.id), receipt, output


@asynccontextmanager
async def reporting(app):
    manager = ManagedObservers(app.host)
    release = asyncio.Event()
    reports = manager._reports

    async def held_reports():
        await release.wait()
        await reports()

    manager._reports = held_reports
    await manager.start()
    try:
        yield manager, release
    finally:
        await manager.aclose()


async def settled(manager, old, valid, identifier):
    async with asyncio.timeout(3):
        while True:
            assert not manager._report_task.done(), 'One stale scope terminated the shared report worker'
            retired = await manager.store.get(old.group_id, old.cwd, old.id)
            rows = await manager.store.history(valid.group_id, valid.cwd, valid.id)
            if retired.recovery == 'needs_review' and any(
                row['message_id'] == identifier and row['delivery_status'] == 'delivered' for row in rows
            ):
                return retired
            await asyncio.sleep(.001)


@pytest.mark.parametrize('drift', ['removed', 'replaced'])
async def test_retired_owner_does_not_stop_other_group_reports_or_change_original_work(tmp_path, drift):
    async with application(tmp_path) as app, reporting(app) as (manager, release):
        old, receipt, output = await accepted(manager, app, 'g1', 'old')
        await manager.store.sync(old)
        valid, _, _ = await accepted(manager, app, 'g2', 'valid')
        before = await manager.store.occurrence({'envelope': {
            'group_id': old.group_id, 'workspace': str(old.cwd), 'message': message('old')}})
        if drift == 'removed':
            app.roots.pop('g1')
        else:
            app.roots['g1'] = tmp_path / 'replacement'
        release.set()
        retired = await settled(manager, old, valid, 'valid')
        assert not retired.enabled
        for field in ('group_id', 'cwd', 'definition', 'state', 'revision', 'inbox_id', 'reply'):
            assert getattr(retired, field) == getattr(old, field)
        assert await manager.store.occurrence({'envelope': {
            'group_id': old.group_id, 'workspace': str(old.cwd), 'message': message('old')}}) == before
        assert await app.inputs.get(receipt.envelope.scope, 'old') == receipt
        assert (await app.outputs.records(receipt.envelope.scope))[0].id == output.id
        assert (await app.outputs.records(receipt.envelope.scope))[0].status == 'delivered'
        # Another real Core occurrence proves the worker still consumes later hints.
        await accepted(manager, app, 'g2', 'next', valid)
        manager._report_signal.set()
        await settled(manager, old, valid, 'next')
        assert not manager._tasks and not manager._files  # No source/model replay.


@pytest.mark.parametrize('method,nth', [
    ('input_namespace', 1), ('input_get', 1), ('input_attempts', 1),
    ('input_get', 2), ('input_attempts', 2), ('input_namespace', 2), ('output_records', 1),
])
async def test_live_scope_loss_during_receipt_and_output_awaits_is_per_record(tmp_path, monkeypatch, method, nth):
    async with application(tmp_path) as app, reporting(app) as (manager, release):
        old, _, _ = await accepted(manager, app, 'g1', 'old')
        valid, _, _ = await accepted(manager, app, 'g2', 'valid')
        original = getattr(app.host, method)
        service_name = 'output_service' if method == 'output_records' else 'input_service'
        service = getattr(app.app, service_name)
        armed, calls, fired = False, 0, False

        async def awaited_service():
            nonlocal armed, fired
            await asyncio.sleep(0)
            if armed:
                armed, fired = False, True
                app.roots['g1'] = tmp_path / 'replaced-during-await'
            return await service()

        async def raced(owner, cwd, *args):
            nonlocal armed, calls
            if owner == 'g1':
                calls += 1
                if calls == nth:
                    armed = True
            return await original(owner, cwd, *args)

        monkeypatch.setattr(app.app, service_name, awaited_service)
        monkeypatch.setattr(app.host, method, raced)
        release.set()
        await settled(manager, old, valid, 'valid')
        assert fired
        assert not manager._tasks and not manager._files


@pytest.mark.parametrize('method', ['input_namespace', 'input_get', 'input_attempts', 'output_records'])
async def test_real_protocol_errors_are_not_misclassified_as_retired_owners(tmp_path, monkeypatch, method):
    async with application(tmp_path) as app, reporting(app) as (manager, _):
        old, _, _ = await accepted(manager, app, 'g1', 'old')
        original = getattr(app.host, method)
        error = ValueError('genuine protocol failure')

        async def invalid(owner, cwd, *args):
            if owner == 'g1':
                raise error
            return await original(owner, cwd, *args)

        monkeypatch.setattr(app.host, method, invalid)
        with pytest.raises(ValueError, match='genuine protocol failure') as failure:
            await manager.flush()
        assert failure.value is error
        current = await manager.store.get(old.group_id, old.cwd, old.id)
        assert current.enabled and current.recovery == 'ephemeral'


async def test_real_output_storage_error_propagates_without_quarantine(tmp_path, monkeypatch):
    async with application(tmp_path) as app, reporting(app) as (manager, _):
        old, _, _ = await accepted(manager, app, 'g1', 'old')
        error = sqlite3.OperationalError('actual output storage error')

        async def unavailable(scope):
            raise error

        monkeypatch.setattr(app.outputs, 'records', unavailable)
        with pytest.raises(sqlite3.OperationalError, match='actual output storage error') as failure:
            await manager.flush()
        assert failure.value is error
        current = await manager.store.get(old.group_id, old.cwd, old.id)
        assert current.enabled and current.recovery == 'ephemeral'
