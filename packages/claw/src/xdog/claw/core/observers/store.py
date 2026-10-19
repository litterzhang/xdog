"""Managed observer records borrowing the already leased group inbox owner.

No tool creates a second store, and a source cursor never commits separately
from its immutable outbox. Provenance labels are not authorization: only a
matching host-created outbox record can opt an ACP input into managed behavior.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xdog.agent.session_input import SessionInput, decode_input, input_data
from xdog.claw.core.observers.models import ObserverRecord, ObserverStopped, encoded

if TYPE_CHECKING:
    from xdog.claw.acp.inbox import GroupInbox, InboxRecord



def observer_row(row: sqlite3.Row) -> ObserverRecord:
    return ObserverRecord(row['observer_id'], row['group_id'], Path(row['cwd']), row['name'],
                          json.loads(row['definition']), row['revision'], bool(row['enabled']),
                          row['recovery'], json.loads(row['state']), row['error'],
                          row['namespace'], row['owner'], bool(row['notify_only']))


def initialize(db: sqlite3.Connection) -> None:
    """Initialize/recover inside the inbox's existing single-owner transaction."""
    db.execute('CREATE TABLE IF NOT EXISTS managed_observers ('
               'observer_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, cwd TEXT NOT NULL, '
               'name TEXT NOT NULL, definition TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, '
               'enabled INTEGER NOT NULL, recovery TEXT NOT NULL, state TEXT NOT NULL DEFAULT \'{}\', '
               'error TEXT NOT NULL DEFAULT \'\', created REAL NOT NULL)')
    if 'namespace' not in {row[1] for row in db.execute('PRAGMA table_info(managed_observers)')}:
        db.execute("ALTER TABLE managed_observers ADD COLUMN namespace TEXT NOT NULL DEFAULT ''")
    columns = {row[1] for row in db.execute('PRAGMA table_info(managed_observers)')}
    if 'owner' not in columns:
        db.execute("ALTER TABLE managed_observers ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
    if 'notify_only' not in columns:
        db.execute("ALTER TABLE managed_observers ADD COLUMN notify_only INTEGER NOT NULL DEFAULT 0")
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS managed_names ON managed_observers(group_id,cwd,name)')
    db.execute('CREATE TABLE IF NOT EXISTS managed_occurrences ('
               'group_id TEXT NOT NULL,cwd TEXT NOT NULL,message_id TEXT NOT NULL,observer_id TEXT NOT NULL,'
               'revision INTEGER NOT NULL,payload TEXT NOT NULL,started INTEGER NOT NULL DEFAULT 0,'
               'cancelled INTEGER NOT NULL DEFAULT 0,delivery_status TEXT NOT NULL DEFAULT \'pending\','
               'delivery_error TEXT NOT NULL DEFAULT \'\', created REAL NOT NULL,'
               'PRIMARY KEY(group_id,cwd,message_id))')
    db.execute("UPDATE managed_observers SET enabled=0,recovery='needs_review',"
               "error='Saved inbox namespace changed; inspect before new work' WHERE NOT EXISTS ("
               "SELECT 1 FROM scopes x WHERE x.group_id=managed_observers.group_id "
               "AND x.cwd=managed_observers.cwd AND x.namespace=managed_observers.namespace)")
    db.execute("UPDATE managed_occurrences SET cancelled=1,delivery_status='not-required' "
               "WHERE started=0 AND observer_id IN (SELECT s.observer_id FROM managed_observers s "
               "WHERE NOT EXISTS (SELECT 1 FROM scopes x WHERE x.group_id=s.group_id AND x.cwd=s.cwd "
               "AND x.namespace=s.namespace))")
    # Claimed is not started: only this host-owned admission fence can distinguish
    # a safe queued recovery from an interrupted model/tool execution.
    db.execute("UPDATE messages SET status='pending',attempt_id='' WHERE status='running' AND EXISTS ("
               "SELECT 1 FROM managed_occurrences o JOIN managed_observers s ON s.observer_id=o.observer_id "
               "WHERE o.group_id=messages.group_id AND o.cwd=messages.cwd AND o.message_id=messages.message_id "
               "AND o.started=0 AND o.cancelled=0 AND s.enabled=1 AND s.revision=o.revision)")
    db.execute("UPDATE messages SET status='cancelled',error='Observer cancelled before execution' "
               "WHERE status IN ('pending','running') AND EXISTS (SELECT 1 FROM managed_occurrences o "
               "WHERE o.group_id=messages.group_id AND o.cwd=messages.cwd AND o.message_id=messages.message_id "
               "AND o.cancelled=1 AND o.started=0)")
    db.execute("UPDATE managed_occurrences SET delivery_status='unknown',"
               "delivery_error='Receiver stopped during channel send; inspect before resending' "
               "WHERE delivery_status='sending'")


def recover_started(db: sqlite3.Connection) -> None:
    """Called after the inbox changes remaining running messages to interrupted."""
    db.execute("UPDATE managed_observers SET enabled=0,recovery='needs_review',"
               "error='Interrupted execution or unknown delivery; inspect effects before new work' "
               "WHERE observer_id IN (SELECT o.observer_id FROM managed_occurrences o LEFT JOIN messages m "
               "ON m.group_id=o.group_id AND m.cwd=o.cwd AND m.message_id=o.message_id "
               "WHERE m.status='interrupted' OR o.delivery_status='unknown')")


class ObserverStore:
    def __init__(self, inbox: GroupInbox) -> None:
        self.inbox = inbox

    async def create(self, group: str, cwd: Path, name: str, definition: dict[str, Any], *,
                     identifier: str = '', enabled: bool = True, recovery: str = 'managed',
                     state: dict[str, Any] | None = None, error: str = '',
                     owner: str = '', notify_only: bool = False) -> ObserverRecord:
        if not group.strip() or not cwd.is_absolute() or not name.strip() or len(name.encode()) > 256:
            raise ValueError('Expected current group, absolute workspace and bounded observer name')
        if (not isinstance(owner, str) or len(owner.encode()) > 256
                or owner and not owner.strip() or type(notify_only) is not bool):
            raise ValueError('Expected bounded host owner and notify_only boolean')
        identifier = identifier or 'eyes-' + uuid.uuid4().hex
        payload, checkpoint = encoded(definition), encoded(state or {})

        def create(db: sqlite3.Connection) -> ObserverRecord:
            previous = db.execute('SELECT * FROM managed_observers WHERE group_id=? AND cwd=? AND name=?',
                                  (group, str(cwd), name)).fetchone()
            if previous is not None:
                if (previous['definition'] != payload or previous['owner'] != owner
                        or bool(previous['notify_only']) != notify_only):
                    raise ValueError('Observer name already has different content; use a new name')
                return observer_row(previous)  # Never reactivate a cancelled/uncertain observer implicitly.
            count = db.execute('SELECT count(*) FROM managed_observers WHERE group_id=? AND cwd=?',
                               (group, str(cwd))).fetchone()[0]
            if count >= 128:
                raise ValueError('Managed observer capacity exceeded; no record was dropped')
            db.execute('INSERT OR IGNORE INTO scopes VALUES (?,?,?)', (group, str(cwd), uuid.uuid4().hex))
            namespace = db.execute('SELECT namespace FROM scopes WHERE group_id=? AND cwd=?',
                                   (group, str(cwd))).fetchone()[0]
            db.execute('INSERT INTO managed_observers '
                       '(observer_id,group_id,cwd,name,definition,enabled,recovery,state,error,'
                       'created,namespace,owner,notify_only) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (identifier, group, str(cwd), name, payload, int(enabled), recovery, checkpoint, error,
                        time.time(), namespace, owner, int(notify_only)))
            return observer_row(db.execute('SELECT * FROM managed_observers WHERE observer_id=?',
                                           (identifier,)).fetchone())
        return await self.inbox._disk(create)

    async def get(self, group: str, cwd: Path, identifier: str) -> ObserverRecord | None:
        def read(db: sqlite3.Connection) -> ObserverRecord | None:
            row = db.execute('SELECT * FROM managed_observers WHERE observer_id=? AND group_id=? AND cwd=?',
                             (identifier, group, str(cwd))).fetchone()
            return observer_row(row) if row is not None else None
        return await self.inbox._disk(read)

    async def list(self, group: str | None = None, cwd: Path | None = None) -> tuple[ObserverRecord, ...]:
        def read(db: sqlite3.Connection) -> tuple[ObserverRecord, ...]:
            query = 'SELECT * FROM managed_observers'
            args: tuple[str, ...] = ()
            if group is not None:
                if cwd is None:
                    raise ValueError('Workspace is required with a group filter')
                query, args = query + ' WHERE group_id=? AND cwd=?', (group, str(cwd))
            return tuple(observer_row(row) for row in db.execute(query + ' ORDER BY created', args))
        return await self.inbox._disk(read)

    async def save(self, record: ObserverRecord, state: dict[str, Any],
                   messages: tuple[SessionInput, ...] = ()) -> None:
        checkpoint = encoded(state)
        payloads = [(message, encoded(input_data(message))) for message in messages]

        def save(db: sqlite3.Connection) -> None:
            current = db.execute('SELECT * FROM managed_observers WHERE observer_id=?', (record.id,)).fetchone()
            if (current is None or not current['enabled'] or current['revision'] != record.revision
                    or current['owner'] != record.owner or bool(current['notify_only']) != record.notify_only):
                raise ObserverStopped('Observer cancelled or changed before trigger persistence')
            for message, payload in payloads:
                previous = db.execute('SELECT * FROM managed_occurrences '
                                      'WHERE group_id=? AND cwd=? AND message_id=?',
                                      (record.group_id, str(record.cwd), message.id)).fetchone()
                if previous is not None:
                    if (previous['payload'] != payload or previous['observer_id'] != record.id
                            or previous['revision'] != record.revision):
                        raise ValueError('Managed occurrence identity/content changed')
                    continue
                count = db.execute('SELECT count(*) FROM managed_occurrences WHERE observer_id=?',
                                   (record.id,)).fetchone()[0]
                if count >= 4096:
                    raise ValueError('Occurrence history capacity exceeded; inspect/archive before continuing')
                unfinished = db.execute("SELECT 1 FROM managed_occurrences o LEFT JOIN messages m ON "
                    "m.group_id=o.group_id AND m.cwd=o.cwd AND m.message_id=o.message_id "
                    "WHERE o.observer_id=? AND o.cancelled=0 AND (m.status IS NULL OR "
                    "m.status IN ('pending','running') OR o.delivery_status IN ('pending','sending')) LIMIT 1",
                    (record.id,)).fetchone()
                if unfinished is not None:
                    raise ValueError('An occurrence still owns this observer; overlapping work is not admitted')
                db.execute('INSERT INTO managed_occurrences '
                           '(group_id,cwd,message_id,observer_id,revision,payload,created) VALUES (?,?,?,?,?,?,?)',
                           (record.group_id, str(record.cwd), message.id, record.id, record.revision, payload,
                            time.time()))
            if 'pending' in state and state['pending'] is None:
                unfinished = db.execute("SELECT 1 FROM managed_occurrences o LEFT JOIN messages m ON "
                    "m.group_id=o.group_id AND m.cwd=o.cwd AND m.message_id=o.message_id "
                    "WHERE o.observer_id=? AND o.cancelled=0 AND (m.status IS NULL OR "
                    "m.status IN ('pending','running') OR o.delivery_status IN ('pending','sending')) LIMIT 1",
                    (record.id,)).fetchone()
                if unfinished is not None:
                    raise ValueError('Cannot discard an unfinished managed outbox from its source cursor')
            db.execute('UPDATE managed_observers SET state=?,error=\'\' WHERE observer_id=?',
                       (checkpoint, record.id))
        await self.inbox._disk(save)

    async def cancel(self, group: str, cwd: Path, identifier: str) -> dict[str, int | bool]:
        def cancel(db: sqlite3.Connection) -> dict[str, int | bool]:
            current = db.execute('SELECT * FROM managed_observers WHERE observer_id=? AND group_id=? AND cwd=?',
                                 (identifier, group, str(cwd))).fetchone()
            if current is None:
                raise ValueError('Unknown observer in the current group/workspace')
            db.execute("UPDATE managed_observers SET enabled=0,revision=revision+1,recovery='cancelled' "
                       "WHERE observer_id=?",
                       (identifier,))
            rows = db.execute(
                'SELECT o.* FROM managed_occurrences o LEFT JOIN messages m ON '
                'm.group_id=o.group_id AND m.cwd=o.cwd AND m.message_id=o.message_id '
                "WHERE o.observer_id=? AND o.started=0 AND o.cancelled=0 AND "
                "(m.status IS NULL OR m.status IN ('pending','running'))", (identifier,)).fetchall()
            for row in rows:
                db.execute('UPDATE managed_occurrences SET cancelled=1,delivery_status=\'not-required\' '
                           'WHERE group_id=? AND cwd=? AND message_id=?',
                           (group, str(cwd), row['message_id']))
                db.execute("UPDATE messages SET status='cancelled',error='Observer cancelled before execution' "
                           "WHERE group_id=? AND cwd=? AND message_id=? AND status IN ('pending','running')",
                           (group, str(cwd), row['message_id']))
            running = db.execute('SELECT count(*) FROM managed_occurrences o JOIN messages m ON '
                "m.group_id=o.group_id AND m.cwd=o.cwd AND m.message_id=o.message_id WHERE o.observer_id=? "
                "AND o.started=1 AND m.status='running'", (identifier,)).fetchone()[0]
            return {'cancelled': True, 'not_started': len(rows), 'already_started': int(running)}
        return await self.inbox._disk(cancel)

    async def occurrence(self, record: InboxRecord) -> dict[str, Any] | None:
        def read(db: sqlite3.Connection) -> dict[str, Any] | None:
            row = db.execute('SELECT * FROM managed_occurrences WHERE group_id=? AND cwd=? AND message_id=?',
                             (record.group_id, str(record.cwd), record.message.id)).fetchone()
            return dict(row) if row is not None else None
        return await self.inbox._disk(read)

    async def admit(self, record: InboxRecord) -> bool:
        def admit(db: sqlite3.Connection) -> bool:
            row = db.execute('SELECT o.*,s.enabled,s.revision AS current_revision,s.namespace, '
                             'x.namespace AS actual_namespace FROM managed_occurrences o '
                             'JOIN managed_observers s ON s.observer_id=o.observer_id '
                             'LEFT JOIN scopes x ON x.group_id=o.group_id AND x.cwd=o.cwd '
                             'WHERE o.group_id=? AND o.cwd=? AND o.message_id=?',
                             (record.group_id, str(record.cwd), record.message.id)).fetchone()
            if row is None:
                return True
            if row['started']:
                return False
            if not row['namespace'] or row['namespace'] != row['actual_namespace']:
                db.execute("UPDATE managed_observers SET enabled=0,recovery='needs_review',"
                           "error='Saved inbox namespace changed before admission' WHERE observer_id=?",
                           (row['observer_id'],))
                db.execute("UPDATE managed_occurrences SET cancelled=1,delivery_status='not-required' "
                           'WHERE group_id=? AND cwd=? AND message_id=? AND started=0',
                           (record.group_id, str(record.cwd), record.message.id))
                db.execute("UPDATE messages SET status='cancelled',error='Inbox namespace changed before admission' "
                           "WHERE group_id=? AND cwd=? AND message_id=? AND attempt_id=? AND status='running'",
                           (record.group_id, str(record.cwd), record.message.id, record.attempt_id))
                return False
            if row['started'] or row['cancelled'] or not row['enabled'] or row['revision'] != row['current_revision']:
                return False
            updated = db.execute("SELECT status,attempt_id FROM messages WHERE group_id=? AND cwd=? AND message_id=?",
                                 (record.group_id, str(record.cwd), record.message.id)).fetchone()
            if updated is None or updated['status'] != 'running' or updated['attempt_id'] != record.attempt_id:
                return False
            db.execute('UPDATE managed_occurrences SET started=1 WHERE group_id=? AND cwd=? AND message_id=?',
                       (record.group_id, str(record.cwd), record.message.id))
            return True
        return await self.inbox._disk(admit)

    async def begin_delivery(self, record: InboxRecord) -> bool:
        def begin(db: sqlite3.Connection) -> bool:
            binding = db.execute('SELECT o.observer_id,s.namespace,s.notify_only,x.namespace AS actual_namespace '
                'FROM managed_occurrences o JOIN managed_observers s ON s.observer_id=o.observer_id '
                'LEFT JOIN scopes x ON x.group_id=o.group_id AND x.cwd=o.cwd '
                'WHERE o.group_id=? AND o.cwd=? AND o.message_id=?',
                (record.group_id, str(record.cwd), record.message.id)).fetchone()
            if binding is not None and binding['notify_only']:
                # Only trusted host policy suppresses sending; event provenance
                # and source evidence cannot opt into or out of notifications.
                db.execute("UPDATE managed_occurrences SET delivery_status='not-required' "
                           "WHERE group_id=? AND cwd=? AND message_id=? AND delivery_status='pending'",
                           (record.group_id, str(record.cwd), record.message.id))
                return False
            if binding is not None and (not binding['namespace']
                                         or binding['namespace'] != binding['actual_namespace']):
                db.execute("UPDATE managed_observers SET enabled=0,recovery='needs_review',"
                           "error='Saved inbox namespace changed before report delivery' WHERE observer_id=?",
                           (binding['observer_id'],))
                db.execute("UPDATE managed_occurrences SET delivery_status='failed',"
                           "delivery_error='Namespace changed before channel send; report was not sent' "
                           "WHERE group_id=? AND cwd=? AND message_id=? AND delivery_status='pending'",
                           (record.group_id, str(record.cwd), record.message.id))
                return False
            result = db.execute("UPDATE managed_occurrences SET delivery_status='sending' "
                                "WHERE group_id=? AND cwd=? AND message_id=? AND delivery_status='pending' "
                                "AND cancelled=0 AND started=1",
                                (record.group_id, str(record.cwd), record.message.id))
            return result.rowcount == 1
        return await self.inbox._disk(begin)

    async def finish_delivery(self, record: InboxRecord, *, status: str, error: str = '') -> None:
        if status not in {'delivered', 'failed', 'unknown'}:
            raise ValueError('Invalid channel delivery status')
        await self.inbox._disk(lambda db: db.execute(
            'UPDATE managed_occurrences SET delivery_status=?,delivery_error=? '
            "WHERE group_id=? AND cwd=? AND message_id=? AND delivery_status='sending'",
            (status, error[:4096], record.group_id, str(record.cwd), record.message.id)))

    async def fail(self, record: ObserverRecord, error: str, *, review: bool = False) -> None:
        def fail(db: sqlite3.Connection) -> None:
            db.execute('UPDATE managed_observers SET error=?,enabled=?,recovery=? '
                       'WHERE observer_id=? AND revision=?',
                       (error[:4096], 0 if review else int(record.enabled),
                        'needs_review' if review else record.recovery, record.id, record.revision))
        await self.inbox._disk(fail)

    async def history(self, group: str, cwd: Path, identifier: str) -> tuple[dict[str, Any], ...]:
        if await self.get(group, cwd, identifier) is None:
            raise ValueError('Unknown observer in the current group/workspace')
        return await self.inbox._disk(lambda db: tuple(dict(row) for row in db.execute(
            "SELECT o.message_id,o.revision,o.started,o.cancelled,o.delivery_status,o.delivery_error,"
            "coalesce(m.status,'pending') AS execution_status,substr(m.error,1,4096) AS execution_error,"
            "m.session_id,CASE WHEN m.message_id IS NULL THEN 'pending' ELSE 'accepted' END AS receipt_status "
            'FROM managed_occurrences o LEFT JOIN messages m ON m.group_id=o.group_id AND m.cwd=o.cwd '
            'AND m.message_id=o.message_id WHERE o.observer_id=? ORDER BY o.created DESC LIMIT 64', (identifier,))))

    async def message(self, record: ObserverRecord, identifier: str) -> SessionInput | None:
        def read(db: sqlite3.Connection) -> SessionInput | None:
            row = db.execute('SELECT payload FROM managed_occurrences WHERE group_id=? AND cwd=? AND message_id=? '
                             'AND observer_id=?', (record.group_id, str(record.cwd), identifier, record.id)).fetchone()
            return decode_input(json.loads(row['payload'])) if row is not None else None
        return await self.inbox._disk(read)

    async def release_unstarted(self, record: InboxRecord) -> bool:
        """Orderly shutdown may return only a never-admitted managed claim to pending."""
        def release(db: sqlite3.Connection) -> bool:
            row = db.execute('SELECT started,cancelled FROM managed_occurrences '
                             'WHERE group_id=? AND cwd=? AND message_id=?',
                             (record.group_id, str(record.cwd), record.message.id)).fetchone()
            if row is None or row['started']:
                return False
            db.execute("UPDATE messages SET status=?,attempt_id='' WHERE group_id=? AND cwd=? AND message_id=? "
                       "AND attempt_id=? AND status='running'",
                       ('cancelled' if row['cancelled'] else 'pending', record.group_id, str(record.cwd),
                        record.message.id, record.attempt_id))
            return True
        return await self.inbox._disk(release)

    async def complete(self, record: ObserverRecord) -> None:
        await self.inbox._disk(lambda db: db.execute(
            "UPDATE managed_observers SET enabled=0,recovery='completed' WHERE observer_id=? AND revision=?",
            (record.id, record.revision)))

    async def pending_reports(self) -> tuple[InboxRecord, ...]:
        """Only known-not-sent terminal reports recover, never model execution."""
        from xdog.claw.acp.inbox import _row
        return await self.inbox._disk(lambda db: tuple(_row(row) for row in db.execute(
            'SELECT m.* FROM messages m JOIN managed_occurrences o ON '
            'm.group_id=o.group_id AND m.cwd=o.cwd AND m.message_id=o.message_id '
            "WHERE m.status IN ('succeeded','failed') AND o.started=1 AND o.cancelled=0 "
            "AND o.delivery_status='pending' ORDER BY m.sequence")))
