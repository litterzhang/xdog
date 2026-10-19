"""Observer-owned cursor/outbox/report journal; core receipts remain execution authority."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

from xdog.agent.leased_sqlite import LeasedSqlite
from xdog.agent.session_input import SessionInput, decode_input, input_data
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.inputs.store import InputAttempt, InputRecord
from xdog.claw.core.observers.models import ObserverRecord, ObserverStopped, encoded

T = TypeVar('T')
_FORMAT = 'xdog.claw.observers.v1'
_TERMINAL = frozenset({'succeeded', 'failed', 'interrupted', 'cancelled'})
_DELIVERY = frozenset({'pending', 'sending', 'delivered', 'failed', 'unknown', 'not-required'})
_SCHEMA = '''
CREATE TABLE observers(
    observer_id TEXT PRIMARY KEY,group_id TEXT NOT NULL,cwd TEXT NOT NULL,name TEXT NOT NULL,
    definition TEXT NOT NULL,revision INTEGER NOT NULL,enabled INTEGER NOT NULL,recovery TEXT NOT NULL,
    state TEXT NOT NULL,error TEXT NOT NULL,namespace TEXT NOT NULL,owner TEXT NOT NULL,
    notify_only INTEGER NOT NULL,reports_enabled INTEGER NOT NULL DEFAULT 1,created REAL NOT NULL,
    UNIQUE(group_id,cwd,name));
CREATE TABLE outbox(
    group_id TEXT NOT NULL,cwd TEXT NOT NULL,message_id TEXT NOT NULL,observer_id TEXT NOT NULL REFERENCES observers,
    revision INTEGER NOT NULL,payload TEXT NOT NULL,cancelled INTEGER NOT NULL DEFAULT 0,
    receipt_status TEXT NOT NULL DEFAULT 'unaccepted',execution_status TEXT NOT NULL DEFAULT 'pending',
    execution_error TEXT NOT NULL DEFAULT '',session_id TEXT NOT NULL DEFAULT '',
    attempt_id TEXT NOT NULL DEFAULT '',started INTEGER NOT NULL DEFAULT 0,
    delivery_status TEXT NOT NULL DEFAULT 'pending',delivery_error TEXT NOT NULL DEFAULT '',
    transport_status TEXT NOT NULL DEFAULT 'unsent',created REAL NOT NULL,
    PRIMARY KEY(group_id,cwd,message_id));
'''


class ReceiptReader(Protocol):
    async def namespace(self, scope: InputScope) -> str: ...
    async def get(self, scope: InputScope, message_id: str) -> InputRecord | None: ...
    async def attempts(self, scope: InputScope, message_id: str) -> tuple[InputAttempt, ...]: ...


def _row(row: sqlite3.Row) -> ObserverRecord:
    return ObserverRecord(row['observer_id'], row['group_id'], Path(row['cwd']), row['name'],
        json.loads(row['definition']), row['revision'], bool(row['enabled']), row['recovery'],
        json.loads(row['state']), row['error'], row['namespace'], row['owner'], bool(row['notify_only']),
        bool(row['reports_enabled']))


def _validate(db: sqlite3.Connection) -> None:
    columns = {
        'observers': {'observer_id', 'group_id', 'cwd', 'name', 'definition', 'revision', 'enabled', 'recovery',
                      'state', 'error', 'namespace', 'owner', 'notify_only', 'reports_enabled', 'created'},
        'outbox': {'group_id', 'cwd', 'message_id', 'observer_id', 'revision', 'payload', 'cancelled',
                   'receipt_status', 'execution_status', 'execution_error', 'session_id', 'attempt_id', 'started',
                   'delivery_status', 'delivery_error', 'transport_status', 'created'},
    }
    for table, expected in columns.items():
        actual = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
        if actual != expected:
            raise ValueError('Unsupported observer journal schema; no upgrade is performed')
    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('Observer outbox has no registered owner')
    for row in db.execute('SELECT * FROM observers'):
        record = _row(row)
        InputScope(record.group_id, record.cwd)
        uuid.UUID(hex=record.inbox_id)
        if (type(record.revision) is not int or not 1 <= record.revision < 2**53
                or record.recovery not in {'managed', 'ephemeral', 'completed', 'cancelled', 'needs_review'}
                or not isinstance(record.definition, dict) or not isinstance(record.state, dict)
                or any(row[key] not in (0, 1) for key in ('enabled', 'notify_only', 'reports_enabled'))):
            raise ValueError('Invalid saved observer state')
        encoded(record.definition)
        encoded(record.state)
    for row in db.execute('SELECT * FROM outbox'):
        message = decode_input(json.loads(row['payload']), transport_envelope=False)
        parent = db.execute('SELECT * FROM observers WHERE observer_id=?', (row['observer_id'],)).fetchone()
        if (parent is None or (row['group_id'], row['cwd']) != (parent['group_id'], parent['cwd'])
                or type(row['revision']) is not int or not 1 <= row['revision'] <= parent['revision']
                or row['transport_status'] not in {'unsent', 'sending', 'accepted', 'unknown'}):
            raise ValueError('Observer outbox binding is corrupt')
        if (message.id != row['message_id'] or row['delivery_status'] not in _DELIVERY
                or row['execution_status'] not in _TERMINAL | {'pending', 'claimed', 'running'}
                or row['receipt_status'] not in {'unaccepted', 'accepted'}):
            raise ValueError('Invalid saved observer outbox facts')


def _recover(db: sqlite3.Connection) -> None:
    db.execute("UPDATE outbox SET transport_status='unknown' WHERE transport_status='sending'")
    db.execute("UPDATE observers SET enabled=0,recovery='needs_review',error='Unknown channel send; inspect effects' "
               "WHERE observer_id IN (SELECT observer_id FROM outbox WHERE delivery_status='sending')")
    db.execute("UPDATE outbox SET delivery_status='unknown',delivery_error='Channel send interrupted' "
               "WHERE delivery_status='sending'")


class ObserverJournal:
    def __init__(self, database: LeasedSqlite, receipts: ReceiptReader) -> None:
        self._database, self.receipts = database, receipts
        self.path = database.path

    @classmethod
    async def open(cls, path: Path, receipts: ReceiptReader) -> ObserverJournal:
        database = await LeasedSqlite.open(path, format=_FORMAT, schema=_SCHEMA, validate=_validate, recover=_recover)
        return cls(database, receipts)

    async def _disk(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return await self._database.disk(operation)

    async def create(self, group: str, cwd: Path, name: str, definition: dict[str, object], *,
                     identifier: str = '', enabled: bool = True, recovery: str = 'managed',
                     state: dict[str, object] | None = None, error: str = '', owner: str = '',
                     notify_only: bool = False) -> ObserverRecord:
        scope = InputScope(group, cwd)
        if (not name.strip() or len(name.encode()) > 256 or not isinstance(owner, str)
                or owner and not owner.strip() or '\x00' in owner or len(owner.encode()) > 256
                or type(notify_only) is not bool):
            raise ValueError('Expected bounded host owner and notify_only boolean')
        namespace = await self.receipts.namespace(scope)
        identifier = identifier or 'eyes-' + uuid.uuid4().hex
        payload, checkpoint = encoded(definition), encoded(state or {})
        def create(db: sqlite3.Connection) -> ObserverRecord:
            previous = db.execute('SELECT * FROM observers WHERE group_id=? AND cwd=? AND name=?',
                                  (group, str(cwd), name)).fetchone()
            if previous is not None:
                if (previous['definition'] != payload or previous['owner'] != owner
                        or bool(previous['notify_only']) != notify_only):
                    raise ValueError('Observer name already has different content')
                return _row(previous)
            db.execute('INSERT INTO observers(observer_id,group_id,cwd,name,definition,revision,enabled,recovery,'
                'state,error,namespace,owner,notify_only,created) VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?,?)',
                (identifier, group, str(cwd), name, payload, int(enabled), recovery, checkpoint, error,
                 namespace, owner, int(notify_only), time.time()))
            return _row(db.execute('SELECT * FROM observers WHERE observer_id=?', (identifier,)).fetchone())
        return await self._disk(create)

    async def get(self, group: str, cwd: Path, identifier: str) -> ObserverRecord | None:
        def get(db: sqlite3.Connection) -> ObserverRecord | None:
            row = db.execute('SELECT * FROM observers WHERE group_id=? AND cwd=? AND observer_id=?',
                             (group, str(cwd), identifier)).fetchone()
            return _row(row) if row is not None else None
        return await self._disk(get)

    async def list(self, group: str | None = None, cwd: Path | None = None) -> tuple[ObserverRecord, ...]:
        def read(db: sqlite3.Connection) -> tuple[ObserverRecord, ...]:
            if group is not None and cwd is None:
                raise ValueError('Workspace is required with group')
            query, args = ('SELECT * FROM observers', ()) if group is None else (
                'SELECT * FROM observers WHERE group_id=? AND cwd=?', (group, str(cwd)))
            return tuple(_row(row) for row in db.execute(query + ' ORDER BY created', args))
        return await self._disk(read)

    async def save(self, record: ObserverRecord, state: dict[str, object],
                   messages: tuple[SessionInput, ...] = ()) -> None:
        await self.sync(record)
        checkpoint = encoded(state)
        payloads = [(message, encoded(input_data(message))) for message in messages]
        def save(db: sqlite3.Connection) -> None:
            current = db.execute('SELECT * FROM observers WHERE observer_id=?', (record.id,)).fetchone()
            if (current is None or not current['enabled'] or current['revision'] != record.revision
                    or current['owner'] != record.owner or bool(current['notify_only']) != record.notify_only):
                raise ObserverStopped('Observer stopped or changed before trigger commit')
            for message, payload in payloads:
                previous = db.execute('SELECT * FROM outbox WHERE group_id=? AND cwd=? AND message_id=?',
                                      (record.group_id, str(record.cwd), message.id)).fetchone()
                if previous is not None:
                    if (previous['payload'] != payload or previous['observer_id'] != record.id
                            or previous['revision'] != record.revision):
                        raise ValueError('Observer outbox identity/content changed')
                    continue
                unfinished = db.execute("SELECT 1 FROM outbox WHERE observer_id=? AND cancelled=0 AND "
                    "(receipt_status='unaccepted' OR execution_status IN ('pending','claimed','running') "
                    "OR delivery_status IN ('pending','sending')) LIMIT 1", (record.id,)).fetchone()
                if unfinished is not None:
                    raise ValueError('Unfinished overlapping observer outbox')
                db.execute('INSERT INTO outbox(group_id,cwd,message_id,observer_id,revision,payload,created) '
                    'VALUES (?,?,?,?,?,?,?)', (record.group_id, str(record.cwd), message.id, record.id,
                                               record.revision, payload, time.time()))
            if state.get('pending', True) is None and db.execute(
                    "SELECT 1 FROM outbox WHERE observer_id=? AND cancelled=0 AND (receipt_status='unaccepted' "
                    "OR execution_status IN ('pending','claimed','running') "
                    "OR delivery_status IN ('pending','sending'))",
                    (record.id,)).fetchone() is not None:
                raise ValueError('Cannot discard an unfinished source outbox')
            db.execute("UPDATE observers SET state=?,error='' WHERE observer_id=?", (checkpoint, record.id))
        await self._disk(save)

    async def sync(self, record: ObserverRecord) -> None:
        scope = InputScope(record.group_id, record.cwd)
        if await self.receipts.namespace(scope) != record.inbox_id:
            await self.fail(record, 'Input namespace changed; inspect before delivery', review=True)
            return
        pending = await self._disk(lambda db: tuple(dict(row) for row in db.execute(
            "SELECT * FROM outbox WHERE observer_id=? AND cancelled=0 AND (attempt_id='' "
            "OR execution_status IN ('pending','claimed','running'))", (record.id,))))
        for row in pending:
            receipt = await self.receipts.get(scope, row['message_id'])
            if receipt is None:
                continue
            if encoded(input_data(receipt.envelope.message)) != row['payload']:
                await self.fail(record, 'Input content does not match original outbox', review=True)
                raise ValueError('Observer outbox/input identity mismatch')
            attempts = await self.receipts.attempts(scope, row['message_id'])
            attempt = next((item for item in attempts if item.attempt_id == row['attempt_id']), None)
            if attempt is None and not row['attempt_id']:
                attempt = next((item for item in attempts if item.started or item.status == 'failed'), None)
            status = attempt.status if attempt is not None else receipt.status
            error = attempt.error if attempt is not None else receipt.error
            session = attempt.session_id if attempt is not None else receipt.session_id
            identifier = attempt.attempt_id if attempt is not None else ''
            started = attempt is not None and attempt.started
            await self._disk(lambda db: db.execute('UPDATE outbox SET receipt_status=\'accepted\',execution_status=?,'
                'execution_error=?,session_id=?,attempt_id=?,started=? WHERE group_id=? AND cwd=? AND message_id=?',
                (status, error, session, identifier, int(started), record.group_id,
                 str(record.cwd), row['message_id'])))
            if status == 'interrupted':
                await self.fail(record, error or 'Interrupted execution; inspect effects', review=True)

    async def transport_started(self, record: ObserverRecord, message_id: str) -> bool:
        def start(db: sqlite3.Connection) -> bool:
            current = db.execute('SELECT enabled,revision FROM observers WHERE observer_id=?', (record.id,)).fetchone()
            if current is None or not current['enabled'] or current['revision'] != record.revision:
                return False
            changed = db.execute("UPDATE outbox SET transport_status='sending' WHERE observer_id=? AND message_id=? "
                                 'AND revision=? AND cancelled=0', (record.id, message_id, record.revision))
            return changed.rowcount == 1
        return await self._disk(start)

    async def transport_finished(self, record: ObserverRecord, message_id: str, *, status: str) -> None:
        if status not in {'queued', 'unknown', 'not-sent'}:
            raise ValueError('Invalid observer transport outcome')
        outcome = {'queued': 'accepted', 'unknown': 'unknown', 'not-sent': 'unsent'}[status]
        await self._disk(lambda db: db.execute('UPDATE outbox SET transport_status=? '
            'WHERE observer_id=? AND message_id=? AND revision=?', (outcome, record.id, message_id, record.revision)))

    async def cancel(self, group: str, cwd: Path, identifier: str) -> dict[str, int | bool]:
        record = await self.get(group, cwd, identifier)
        if record is None:
            raise ValueError('Unknown observer in this scope')
        await self.sync(record)
        def cancel(db: sqlite3.Connection) -> dict[str, int | bool]:
            db.execute("UPDATE observers SET enabled=0,revision=revision+1,recovery='cancelled' WHERE observer_id=?",
                       (identifier,))
            result = db.execute("UPDATE outbox SET cancelled=1,delivery_status='not-required' WHERE observer_id=? "
                                "AND receipt_status='unaccepted' AND transport_status='unsent'", (identifier,))
            return {'cancelled': True, 'not_started': result.rowcount, 'withdrawn_inputs': 0}
        return await self._disk(cancel)

    async def cancel_notifications(self, group: str, cwd: Path, identifier: str) -> None:
        if await self.get(group, cwd, identifier) is None:
            raise ValueError('Unknown observer in this scope')
        def cancel(db: sqlite3.Connection) -> None:
            db.execute('UPDATE observers SET reports_enabled=0 WHERE observer_id=?', (identifier,))
            db.execute("UPDATE outbox SET delivery_status='not-required' WHERE observer_id=? "
                       "AND delivery_status='pending'",
                       (identifier,))
        await self._disk(cancel)

    async def occurrence(self, receipt: InputRecord) -> dict[str, object] | None:
        def read(db: sqlite3.Connection) -> dict[str, object] | None:
            scope = receipt.envelope.scope
            row = db.execute('SELECT * FROM outbox WHERE group_id=? AND cwd=? AND message_id=?',
                             (scope.group_id, str(scope.workspace), receipt.envelope.message.id)).fetchone()
            return dict(row) if row is not None else None
        return await self._disk(read)

    async def begin_delivery(self, receipt: InputRecord) -> bool:
        row = await self.occurrence(receipt)
        if row is None:
            return False
        scope = receipt.envelope.scope
        identifier = str(row['observer_id'])
        record = await self.get(scope.group_id, scope.workspace, identifier)
        if record is None or await self.receipts.namespace(scope) != record.inbox_id:
            return False
        await self.sync(record)
        actual = await self.occurrence(receipt)
        if (actual is None or actual['attempt_id'] != receipt.attempt_id
                or actual['execution_status'] != receipt.status):
            return False
        def begin(db: sqlite3.Connection) -> bool:
            current = db.execute('SELECT * FROM observers WHERE observer_id=?', (identifier,)).fetchone()
            if current['notify_only'] or not current['reports_enabled']:
                db.execute("UPDATE outbox SET delivery_status='not-required' WHERE group_id=? AND cwd=? "
                           "AND message_id=? AND delivery_status='pending'",
                           (scope.group_id, str(scope.workspace), receipt.envelope.message.id))
                return False
            changed = db.execute("UPDATE outbox SET delivery_status='sending' WHERE group_id=? AND cwd=? "
                "AND message_id=? AND delivery_status='pending' AND cancelled=0 "
                "AND execution_status IN ('succeeded','failed')", (scope.group_id, str(scope.workspace),
                                                                   receipt.envelope.message.id))
            return changed.rowcount == 1
        return await self._disk(begin)

    async def finish_delivery(self, receipt: InputRecord, *, status: str, error: str = '') -> None:
        if status not in {'delivered', 'failed', 'unknown'}:
            raise ValueError('Invalid report delivery status')
        scope = receipt.envelope.scope
        await self._disk(lambda db: db.execute('UPDATE outbox SET delivery_status=?,delivery_error=? '
            "WHERE group_id=? AND cwd=? AND message_id=? AND attempt_id=? AND delivery_status='sending'",
            (status, error[:4096], scope.group_id, str(scope.workspace),
             receipt.envelope.message.id, receipt.attempt_id)))

    async def fail(self, record: ObserverRecord, error: str, *, review: bool = False) -> None:
        await self._disk(lambda db: db.execute('UPDATE observers SET error=?,enabled=?,recovery=? '
            'WHERE observer_id=? AND revision=?', (error[:4096], 0 if review else int(record.enabled),
             'needs_review' if review else record.recovery, record.id, record.revision)))

    async def history(self, group: str, cwd: Path, identifier: str) -> tuple[dict[str, object], ...]:
        record = await self.get(group, cwd, identifier)
        if record is None:
            raise ValueError('Unknown observer in this scope')
        await self.sync(record)
        return await self._disk(lambda db: tuple(dict(row) for row in db.execute(
            'SELECT message_id,revision,started,cancelled,delivery_status,delivery_error,execution_status,'
            'execution_error,session_id,receipt_status,attempt_id,transport_status FROM outbox '
            'WHERE observer_id=? ORDER BY created DESC '
            'LIMIT 64', (identifier,))))

    async def message(self, record: ObserverRecord, identifier: str) -> SessionInput | None:
        def read(db: sqlite3.Connection) -> SessionInput | None:
            row = db.execute('SELECT payload FROM outbox WHERE observer_id=? AND message_id=?',
                             (record.id, identifier)).fetchone()
            return decode_input(json.loads(row['payload']), transport_envelope=False) if row is not None else None
        return await self._disk(read)

    async def complete(self, record: ObserverRecord) -> None:
        await self._disk(lambda db: db.execute("UPDATE observers SET enabled=0,recovery='completed' "
            'WHERE observer_id=? AND revision=?', (record.id, record.revision)))

    async def pending_reports(self) -> tuple[InputRecord, ...]:
        result = []
        candidates = await self._disk(lambda db: tuple(_row(row) for row in db.execute(
            "SELECT DISTINCT o.* FROM observers o JOIN outbox b ON b.observer_id=o.observer_id "
            "WHERE b.cancelled=0 AND b.delivery_status='pending'")))
        for record in candidates:
            await self.sync(record)
            rows = await self._disk(lambda db: tuple(dict(row) for row in db.execute(
                "SELECT * FROM outbox WHERE observer_id=? AND cancelled=0 AND delivery_status='pending' "
                "AND execution_status IN ('succeeded','failed')", (record.id,))))
            for row in rows:
                receipt = await self.receipts.get(InputScope(record.group_id, record.cwd), row['message_id'])
                if receipt is not None:
                    attempts = await self.receipts.attempts(receipt.envelope.scope, row['message_id'])
                    attempt = next((item for item in attempts if item.attempt_id == row['attempt_id']), None)
                    if attempt is not None:
                        result.append(InputRecord(receipt.envelope, attempt.status, attempt.attempt_id,
                                                  attempt.response, attempt.error, attempt.session_id))
        return tuple(result)

    async def close(self) -> None:
        await self._database.close()
