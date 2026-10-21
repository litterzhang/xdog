"""One leased owner registry: Goal history and reminder outbox, never task state."""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xdog.agent.leased_sqlite import LeasedSqlite

from .codec import decode_goal, encode_goal, json_text, read_json
from .models import Goal, Owner

_FORMAT = 'xdog.goal.registry.v2'
_SIGNAL_SCHEMA = '''CREATE TABLE IF NOT EXISTS wake_signals(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,goal_id TEXT NOT NULL REFERENCES goals(id),
 trigger TEXT NOT NULL,UNIQUE(goal_id,trigger));'''


def remindable(goal: Goal) -> bool:
    return goal.status == 'active' or goal.status == 'waiting' and goal.wait_for == 'tasks'

_SCHEMA = '''
CREATE TABLE owner(key TEXT NOT NULL,workspace TEXT NOT NULL);
CREATE TABLE goals(id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL);
CREATE TABLE revisions(goal_id TEXT NOT NULL REFERENCES goals(id),version INTEGER NOT NULL,
 payload TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(goal_id,version));
CREATE TABLE current(slot INTEGER PRIMARY KEY CHECK(slot=1),goal_id TEXT UNIQUE REFERENCES goals(id));
INSERT INTO current VALUES(1,NULL);
CREATE TABLE reminders(id TEXT PRIMARY KEY,goal_id TEXT NOT NULL REFERENCES goals(id),
 payload TEXT NOT NULL,digest TEXT NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL);
'''


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _goal(row: sqlite3.Row) -> Goal:
    goal = decode_goal(row['payload'])
    if goal.id != row['id'] or goal.version != row['version'] or _digest(row['payload']) != row['digest']:
        raise ValueError('Goal record identity/version/digest conflict')
    return goal


def _validate(db: sqlite3.Connection, owner: Owner) -> None:
    columns = {'owner': {'key', 'workspace'}, 'goals': {'id', 'version', 'payload', 'digest'},
               'revisions': {'goal_id', 'version', 'payload', 'digest'}, 'current': {'slot', 'goal_id'},
               'reminders': {'id', 'goal_id', 'payload', 'digest', 'status', 'error'}}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wake_signals'").fetchone():
        columns['wake_signals'] = {'sequence', 'goal_id', 'trigger'}
    if any({row[1] for row in db.execute(f'PRAGMA table_info({table})')} != expected
           for table, expected in columns.items()):
        raise ValueError('Unsupported Goal registry schema')
    if [tuple(row) for row in db.execute('SELECT * FROM owner')] != [(owner.key, str(owner.workspace))]:
        raise ValueError('Goal registry owner/workspace mismatch')
    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('Orphaned Goal record')
    current = db.execute('SELECT * FROM current').fetchall()
    if len(current) != 1 or current[0]['slot'] != 1:
        raise ValueError('Invalid current Goal binding')
    active = []
    for row in db.execute('SELECT * FROM goals'):
        goal = _goal(row)
        if goal.status not in {'completed', 'cleared'}:
            active.append(goal.id)
        history = db.execute('SELECT * FROM revisions WHERE goal_id=? ORDER BY version', (goal.id,)).fetchall()
        if [item['version'] for item in history] != list(range(1, goal.version + 1)):
            raise ValueError('Goal revision history is incomplete')
        for item in history:
            saved = decode_goal(item['payload'])
            if saved.id != goal.id or saved.version != item['version'] or _digest(item['payload']) != item['digest']:
                raise ValueError('Corrupt Goal history')
        if history[-1]['payload'] != row['payload']:
            raise ValueError('Goal history and current record disagree')
    if active != ([] if current[0]['goal_id'] is None else [current[0]['goal_id']]):
        raise ValueError('An owner may have only one current Goal, including paused/waiting')
    for row in db.execute('SELECT * FROM reminders'):
        value = read_json(row['payload'])
        if (value.get('id') != row['id'] or _digest(row['payload']) != row['digest']
                or row['status'] not in {'pending', 'sending', 'accepted', 'unknown', 'cancelled', 'unavailable'}):
            raise ValueError('Corrupt Goal reminder')


def _recover(db: sqlite3.Connection) -> None:
    # A send may already have been accepted; never replay with a new message ID.
    db.execute("UPDATE reminders SET status='unknown',error='Interrupted delivery; inspect receipt' "
               "WHERE status='sending'")


class GoalStore:
    def __init__(self, database: LeasedSqlite, owner: Owner) -> None:
        self.database, self.owner, self.path = database, owner, database.path

    @classmethod
    async def open(cls, path: Path, owner: Owner) -> GoalStore:
        def quote(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"
        schema = _SCHEMA + f'INSERT INTO owner VALUES({quote(owner.key)},{quote(str(owner.workspace))});'
        db = await LeasedSqlite.open(path, format=_FORMAT, schema=schema,
                                    validate=lambda db: _validate(db, owner), recover=_recover)
        # Additive index of reminder causes; v2 records and immutable outbox stay unchanged.
        try:
            def ensure(connection: sqlite3.Connection) -> None:
                connection.execute(_SIGNAL_SCHEMA)
            await db.disk(ensure)
        except BaseException:
            await db.close()
            raise
        return cls(db, owner)

    async def current(self) -> Goal | None:
        def read(db: sqlite3.Connection) -> Goal | None:
            row = db.execute('SELECT g.* FROM goals g JOIN current c ON c.goal_id=g.id').fetchone()
            return _goal(row) if row is not None else None
        return await self.database.disk(read)

    async def get(self, identifier: str) -> Goal:
        def read(db: sqlite3.Connection) -> Goal:
            row = db.execute('SELECT * FROM goals WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise ValueError('Unknown Goal for this owner')
            return _goal(row)
        return await self.database.disk(read)

    async def list(self) -> tuple[Goal, ...]:
        return await self.database.disk(lambda db: tuple(_goal(row) for row in db.execute(
            'SELECT * FROM goals ORDER BY rowid DESC')))

    async def history(self, identifier: str) -> tuple[Goal, ...]:
        return await self.database.disk(lambda db: tuple(decode_goal(row['payload']) for row in db.execute(
            'SELECT payload FROM revisions WHERE goal_id=? ORDER BY version', (identifier,))))

    async def put(self, goal: Goal, *, expected_version: int, wake: bool = False,
                  check: Callable[[], None] = lambda: None) -> Goal:
        if type(expected_version) is not int or expected_version < 0 or goal.version != expected_version + 1:
            raise ValueError('Goal version conflict')
        if type(wake) is not bool or wake and goal.status != 'active':
            raise ValueError('Explicit wake requires an active Goal')
        payload = encode_goal(goal)
        def write(db: sqlite3.Connection) -> Goal:
            check()  # Actual invocation fence after asynchronous transaction admission.
            previous = db.execute('SELECT * FROM goals WHERE id=?', (goal.id,)).fetchone()
            if (previous is None and expected_version != 0
                    or previous is not None and previous['version'] != expected_version):
                raise ValueError('Goal changed; re-read before updating')
            bound = db.execute('SELECT goal_id FROM current').fetchone()[0]
            if previous is None:
                if bound is not None:
                    raise ValueError('This agent already has a current Goal; complete or clear it first')
                if goal.status != 'active':
                    raise ValueError('A new Goal must start active')
            elif bound != goal.id:
                raise ValueError('Historical Goal is read-only; create a new Goal instead')
            db.execute('INSERT INTO goals VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                       'version=excluded.version,payload=excluded.payload,digest=excluded.digest',
                       (goal.id, goal.version, payload, _digest(payload)))
            db.execute('INSERT INTO revisions VALUES(?,?,?,?)', (goal.id, goal.version, payload, _digest(payload)))
            if goal.status == 'active' and (wake or previous is None or _goal(previous).status != 'active'):
                db.execute('INSERT OR IGNORE INTO wake_signals(goal_id,trigger) VALUES(?,?)',
                           (goal.id, 'goal-state:' + str(goal.version)))
            terminal = goal.status in {'completed', 'cleared'}
            db.execute('UPDATE current SET goal_id=?', (None if terminal else goal.id,))
            if not remindable(goal):
                db.execute("UPDATE reminders SET status='cancelled' WHERE goal_id=? "
                           "AND status IN ('pending','unavailable')", (goal.id,))
            return goal
        return await self.database.disk(write)

    async def reminder(self, goal: Goal, message: dict[str, Any]) -> dict[str, Any]:
        payload = json_text(message)
        def write(db: sqlite3.Connection) -> dict[str, Any]:
            row = db.execute('SELECT * FROM goals WHERE id=?', (goal.id,)).fetchone()
            if row is None or _goal(row) != goal or not remindable(goal):
                raise ValueError('Goal changed before reminder commit')
            previous = db.execute('SELECT * FROM reminders WHERE id=?', (message['id'],)).fetchone()
            if previous is not None and previous['payload'] != payload:
                raise ValueError('Goal reminder ID/content conflict')
            db.execute("INSERT OR IGNORE INTO reminders VALUES(?,?,?,?,'pending','')",
                       (message['id'], goal.id, payload, _digest(payload)))
            return dict(db.execute('SELECT * FROM reminders WHERE id=?', (message['id'],)).fetchone())
        return await self.database.disk(write)

    async def claim_reminder(self, identifier: str, goal: Goal) -> bool:
        """Atomically admit only this original pending send for the live revision.

        Pause/clear/wait/version changes cannot resurrect a cancelled or stale
        send. A successful claim is intake admission, never proof of acceptance;
        accepted/unknown work must subsequently be settled without withdrawal.
        """
        def claim(db: sqlite3.Connection) -> bool:
            row = db.execute('SELECT g.* FROM goals g JOIN current c ON c.goal_id=g.id '
                             'WHERE g.id=?', (goal.id,)).fetchone()
            if row is None or not remindable(goal) or _goal(row) != goal:
                return False
            reminder = db.execute('SELECT * FROM reminders WHERE id=?', (identifier,)).fetchone()
            if (reminder is None or reminder['goal_id'] != goal.id
                    or reminder['status'] not in {'pending', 'unavailable'}):
                return False
            payload = read_json(reminder['payload'])
            if payload.get('id') != identifier or _digest(reminder['payload']) != reminder['digest']:
                raise ValueError('Corrupt Goal reminder')
            provenance = payload.get('provenance')
            if isinstance(provenance, dict) and ('goal_id' in provenance or 'goal_version' in provenance):
                if (provenance.get('goal_id') != goal.id or type(provenance.get('goal_version')) is not int
                        or provenance['goal_version'] != goal.version):
                    return False  # A current Goal cannot relabel an older canonical reminder.
            changed = db.execute("UPDATE reminders SET status='sending',error='' "
                                 "WHERE id=? AND goal_id=? AND status IN ('pending','unavailable')",
                                 (identifier, goal.id))
            return changed.rowcount == 1
        return await self.database.disk(claim)

    async def settle_reminder(self, identifier: str, status: str, error: str = '') -> None:
        """Settle only an actually claimed send; never admit or revive one.

        An already claimed send may settle after Goal pause/clear. Pending
        cancellation belongs to the Goal transition; sending admission belongs
        exclusively to claim_reminder. Terminal/unknown outcomes are immutable.
        """
        if status not in {'accepted', 'unknown', 'unavailable'}:
            raise ValueError('Invalid reminder outcome; sending requires claim_reminder')
        def settle(db: sqlite3.Connection) -> None:
            changed = db.execute("UPDATE reminders SET status=?,error=? WHERE id=? AND status='sending'",
                                 (status, error[:4096], identifier))
            if changed.rowcount != 1:
                raise ValueError('Only an original sending reminder can be settled')
        await self.database.disk(settle)

    async def reminders(self) -> tuple[dict[str, Any], ...]:
        return await self.database.disk(lambda db: tuple(dict(row) for row in db.execute('SELECT * FROM reminders')))

    async def signal(self, goal: Goal, trigger: str) -> bool:
        """Persist an idempotent cause, not a second copy of task/execution state."""
        if not isinstance(trigger, str) or not trigger or len(trigger.encode()) > 1024:
            raise ValueError('Invalid Goal reminder trigger')
        def write(db: sqlite3.Connection) -> bool:
            row = db.execute('SELECT g.* FROM goals g JOIN current c ON c.goal_id=g.id').fetchone()
            if row is None or _goal(row).id != goal.id or not remindable(_goal(row)):
                return False
            db.execute('INSERT OR IGNORE INTO wake_signals(goal_id,trigger) VALUES(?,?)', (goal.id, trigger))
            return True
        return await self.database.disk(write)

    async def wake_state(self, goal_id: str) -> dict[str, Any]:
        def read(db: sqlite3.Connection) -> dict[str, Any]:
            sequence = db.execute('SELECT COALESCE(MAX(sequence),0) FROM wake_signals WHERE goal_id=?',
                                  (goal_id,)).fetchone()[0]
            causes = [row[0] for row in db.execute('SELECT trigger FROM wake_signals WHERE goal_id=? '
                       'ORDER BY sequence DESC LIMIT 16', (goal_id,))]
            reminders = [dict(row) for row in db.execute('SELECT * FROM reminders WHERE goal_id=? ORDER BY rowid',
                                                         (goal_id,))]
            history = [decode_goal(row[0]) for row in db.execute(
                'SELECT payload FROM revisions WHERE goal_id=? ORDER BY version', (goal_id,))]
            starts = {row[0] for row in db.execute("SELECT trigger FROM wake_signals WHERE goal_id=? "
                       "AND trigger LIKE 'goal-state:%'", (goal_id,))}
            epoch = 1
            epoch_time = history[0].created_at if history else 0
            for before, after in zip(history, history[1:]):
                if after.status == 'active' and ('goal-state:' + str(after.version) in starts
                        or before.status == 'paused' or (before.status == 'waiting'
                        and before.wait_for in {'user', 'host'})):
                    epoch_time = after.updated_at
                    epoch = after.version  # Explicitly authorized new continuation, not an old send replay.
            accepted = [row for row in reminders if row['status'] == 'accepted']
            reminders = [row for row in reminders if read_json(row['payload']).get(
                'provenance', {}).get('goal_version', 1) >= epoch]
            task_sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM wake_signals WHERE goal_id=? "
                "AND (trigger LIKE 'tasks:%' OR trigger LIKE 'task-snapshot:%')", (goal_id,)).fetchone()[0]
            manifest = db.execute("SELECT trigger FROM wake_signals WHERE goal_id=? AND trigger LIKE "
                                  "'task-snapshot:%' ORDER BY sequence DESC LIMIT 1", (goal_id,)).fetchone()
            return {'sequence': sequence, 'causes': causes, 'reminders': reminders, 'epoch': epoch,
                    'manifest': manifest[0] if manifest is not None else '', 'epoch_time': epoch_time,
                    'task_sequence': task_sequence, 'accepted': accepted}
        return await self.database.disk(read)

    async def cancel_pending(self, identifier: str) -> None:
        # Only unclaimed local work. Never withdraw host-accepted input.
        def cancel(db: sqlite3.Connection) -> None:
            db.execute("UPDATE reminders SET status='cancelled' WHERE id=? AND status IN ('pending','unavailable')",
                       (identifier,))
        await self.database.disk(cancel)

    async def close(self) -> None:
        await self.database.close()
