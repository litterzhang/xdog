"""One leased owner registry: Goal history and reminder outbox, never task state."""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from xdog.agent.leased_sqlite import LeasedSqlite

from .codec import decode_goal, encode_goal, json_text, read_json
from .host import OperationContext
from .models import Goal, Owner, number, text
from .transitions import transition_data

_FORMAT = 'xdog.goal.registry.v2'
_SIGNAL_SCHEMA = '''CREATE TABLE IF NOT EXISTS wake_signals(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,goal_id TEXT NOT NULL REFERENCES goals(id),
 trigger TEXT NOT NULL,UNIQUE(goal_id,trigger));'''


_FAULT_SCHEMA = '''CREATE TABLE IF NOT EXISTS runtime_faults(
 goal_id TEXT NOT NULL REFERENCES goals(id),version INTEGER NOT NULL,
 source TEXT NOT NULL,identity TEXT NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL,
 PRIMARY KEY(goal_id,source,identity),
 FOREIGN KEY(goal_id,version) REFERENCES revisions(goal_id,version));'''


_FAULT_STATE_SCHEMA = '''CREATE TABLE IF NOT EXISTS runtime_fault_state(
 goal_id TEXT NOT NULL,source TEXT NOT NULL,identity TEXT NOT NULL,
 through INTEGER NOT NULL,recovery_id TEXT NOT NULL,PRIMARY KEY(goal_id,source,identity),
 FOREIGN KEY(goal_id,source,identity) REFERENCES runtime_faults(goal_id,source,identity));'''


_TRANSITION_SCHEMA = '''CREATE TABLE IF NOT EXISTS transitions(
 goal_id TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,
 PRIMARY KEY(goal_id,version),FOREIGN KEY(goal_id,version) REFERENCES revisions(goal_id,version));'''


def _transition(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    data = read_json(row['payload'])
    after_row = db.execute('SELECT payload FROM revisions WHERE goal_id=? AND version=?',
                           (row['goal_id'], row['version'])).fetchone()
    before_row = db.execute('SELECT payload FROM revisions WHERE goal_id=? AND version=?',
                            (row['goal_id'], row['version'] - 1)).fetchone()
    if after_row is None or _digest(row['payload']) != row['digest']:
        raise ValueError('Corrupt Goal transition')
    try:
        expected = transition_data(data['action'], OperationContext(data['user_authorized'], data['origin']),
                                   decode_goal(before_row[0]) if before_row else None, decode_goal(after_row[0]))
    except (KeyError, TypeError) as exc:
        raise ValueError('Corrupt Goal transition provenance') from exc
    if json_text(data) != json_text(expected):
        raise ValueError('Goal transition/revision provenance conflict')
    return data


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
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='transitions'").fetchone():
        columns['transitions'] = {'goal_id', 'version', 'payload', 'digest'}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_faults'").fetchone():
        columns['runtime_faults'] = {'goal_id', 'version', 'source', 'identity', 'status', 'error'}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_fault_state'").fetchone():
        columns['runtime_fault_state'] = {'goal_id', 'source', 'identity', 'through', 'recovery_id'}
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
    bound = current[0]['goal_id']
    if active:
        valid_binding = active == [bound]
    else:
        row = db.execute('SELECT * FROM goals WHERE id=?', (bound,)).fetchone() if bound else None
        valid_binding = bound is None or row is not None and _goal(row).status == 'completed'
    if not valid_binding:
        raise ValueError('An owner may have only one current Goal, including paused/waiting/completed')
    if 'transitions' in columns:
        for row in db.execute('SELECT * FROM transitions'):
            _transition(db, row)
    if 'runtime_faults' in columns:
        for row in db.execute('SELECT * FROM runtime_faults'):
            _validate_fault(row['source'], row['identity'], row['status'], row['error'])
            number(row['version'])
    if 'runtime_fault_state' in columns:
        for row in db.execute('SELECT * FROM runtime_fault_state'):
            if type(row['through']) is not int or row['through'] < 0:
                raise ValueError('Invalid Goal fault watermark')
            text(row['recovery_id'], empty=True, maximum=1024)
            if row['recovery_id']:
                fault = db.execute('SELECT status FROM runtime_faults WHERE goal_id=? AND source=? AND identity=?',
                                   (row['goal_id'], row['source'], row['identity'])).fetchone()
                if row['source'] != 'execution' or fault['status'] not in {'failed', 'cancelled', 'canceled'}:
                    raise ValueError('Uncertain Goal effects cannot be released by later execution')
    for row in db.execute('SELECT * FROM reminders'):
        value = read_json(row['payload'])
        if (value.get('id') != row['id'] or _digest(row['payload']) != row['digest']
                or row['status'] not in {'pending', 'sending', 'accepted', 'unknown', 'cancelled', 'unavailable'}):
            raise ValueError('Corrupt Goal reminder')


def _validate_fault(source: str, identifier: str, status: str, error: str) -> None:
    if (source not in {'execution', 'delivery'} or status not in {
            'failed', 'interrupted', 'cancelled', 'canceled', 'preparation_failed', 'unknown'}
            or source == 'delivery' and status != 'unknown'):
        raise ValueError('Invalid Goal runtime fault')
    text(identifier, maximum=1024)
    text(error, empty=True)


def _epoch(db: sqlite3.Connection, goal_id: str) -> tuple[int, float]:
    history = [decode_goal(row[0]) for row in db.execute(
        'SELECT payload FROM revisions WHERE goal_id=? ORDER BY version', (goal_id,))]
    starts = {row[0] for row in db.execute("SELECT trigger FROM wake_signals WHERE goal_id=? "
               "AND trigger LIKE 'goal-state:%'", (goal_id,))}
    epoch, epoch_time = 1, history[0].created_at if history else 0
    for before, after in zip(history, history[1:]):
        if after.status == 'active' and ('goal-state:' + str(after.version) in starts
                or before.status == 'paused' or before.status == 'waiting' and before.wait_for in {'user', 'host'}):
            epoch, epoch_time = after.version, after.updated_at
    return epoch, epoch_time


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
                connection.execute(_TRANSITION_SCHEMA)
                connection.execute(_FAULT_SCHEMA)
                connection.execute(_FAULT_STATE_SCHEMA)
                # Earlier fault records have no cause boundary. Preserve them and
                # conservatively cover existing causes; only fresh work may wake.
                connection.execute(
                    "INSERT OR IGNORE INTO runtime_fault_state "
                    "SELECT f.goal_id,f.source,f.identity,COALESCE(MAX(w.sequence),0),'' "
                    "FROM runtime_faults f LEFT JOIN wake_signals w ON w.goal_id=f.goal_id "
                    "GROUP BY f.goal_id,f.source,f.identity")
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
                  check: Callable[[], None] = lambda: None, action: str = '',
                  context: OperationContext = OperationContext()) -> Goal:
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
            if goal.wait_for == 'host' and (previous is None or _goal(previous).wait_for != 'host'):
                raise ValueError('wait(host) is historical read-only data; new host waits are not supported')
            bound = db.execute('SELECT goal_id FROM current').fetchone()[0]
            if previous is None:
                if bound is not None:
                    original = db.execute('SELECT * FROM goals WHERE id=?', (bound,)).fetchone()
                    if original is None or _goal(original).status != 'completed':
                        raise ValueError('This agent already has a current Goal; complete or clear it first')
                if goal.status != 'active':
                    raise ValueError('A new Goal must start active')
            elif bound != goal.id:
                raise ValueError('Historical Goal is read-only; create a new Goal instead')
            elif _goal(previous).status == 'completed' and (goal.status != 'cleared' or action != 'clear'):
                raise ValueError('Completed Goal is read-only; clear it or create a new Goal')
            db.execute('INSERT INTO goals VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                       'version=excluded.version,payload=excluded.payload,digest=excluded.digest',
                       (goal.id, goal.version, payload, _digest(payload)))
            db.execute('INSERT INTO revisions VALUES(?,?,?,?)', (goal.id, goal.version, payload, _digest(payload)))
            if action:
                provenance = transition_data(action, context, _goal(previous) if previous else None, goal)
                encoded = json_text(provenance)
                db.execute('INSERT INTO transitions VALUES(?,?,?,?)',
                           (goal.id, goal.version, encoded, _digest(encoded)))
            prior = _goal(previous) if previous is not None else None
            # activate describes work already admitted to this main Agent; it is
            # not a second wake request. Explicit user/host recovery still starts
            # its authority epoch from the recorded waiting -> active revisions.
            if goal.status == 'active' and action != 'activate' and (
                    wake or prior is None or prior.status != 'active'):
                db.execute('INSERT OR IGNORE INTO wake_signals(goal_id,trigger) VALUES(?,?)',
                           (goal.id, 'goal-state:' + str(goal.version)))
            db.execute('UPDATE current SET goal_id=?', (None if goal.status == 'cleared' else goal.id,))
            if not remindable(goal):
                db.execute("UPDATE reminders SET status='cancelled' WHERE goal_id=? "
                           "AND status IN ('pending','unavailable')", (goal.id,))
            return goal
        return await self.database.disk(write)

    async def last_transition(self, goal: Goal) -> dict[str, Any]:
        """Read provenance at this exact snapshot, never infer actors for old revisions."""
        def read(db: sqlite3.Connection) -> dict[str, Any]:
            row = db.execute('SELECT * FROM transitions WHERE goal_id=? AND version<=? '
                             'ORDER BY version DESC LIMIT 1', (goal.id, goal.version)).fetchone()
            unavailable = {'availability': 'unavailable', 'goal_id': goal.id, 'version': goal.version,
                           'reason': 'Lifecycle change origin not recorded'}
            if row is None:
                return unavailable
            data = _transition(db, row)
            state = data['to']
            # Content/report versions do not erase the last lifecycle operation.
            # A later unrecorded lifecycle change, however, must not inherit its actor.
            for newer in db.execute('SELECT payload FROM revisions WHERE goal_id=? AND version>? AND version<=? '
                                    'ORDER BY version', (goal.id, row['version'], goal.version)):
                saved = decode_goal(newer[0])
                if {'status': saved.status, 'wait_for': saved.wait_for} != state:
                    return unavailable
            return {'availability': 'available', **data}
        return await self.database.disk(read)

    async def question_reference(self, goal: Goal) -> str | None:
        """Reference the actual recorded user-wait revision, not a later plan version.

        Old records without a question or proven wait origin get no invented ID.
        Reading immutable revisions never upgrades their payloads or digests.
        """
        if goal.status != 'waiting' or goal.wait_for != 'user' or not goal.question:
            return None
        transition = await self.last_transition(goal)
        if transition.get('availability') != 'available' or transition.get('action') != 'wait':
            return None
        def read(db: sqlite3.Connection) -> str | None:
            row = db.execute('SELECT goal_id AS id,version,payload,digest FROM revisions '
                             'WHERE goal_id=? AND version=?', (goal.id, transition['version'])).fetchone()
            if row is None:
                raise ValueError('Missing question origin revision')
            saved = _goal(row)
            if (saved.status != 'waiting' or saved.wait_for != 'user'
                    or any(getattr(saved, key) != getattr(goal, key)
                           for key in ('question', 'impact', 'resume_when'))):
                return None
            return f"goal-question:{goal.id}:{saved.version}"
        return await self.database.disk(read)

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
            reminders = [dict(row) for row in db.execute('SELECT * FROM reminders WHERE goal_id=? ORDER BY rowid',
                                                         (goal_id,))]
            epoch, epoch_time = _epoch(db, goal_id)
            accepted = [row for row in reminders if row['status'] == 'accepted']
            reminders = [row for row in reminders if row['status'] == 'unknown' or read_json(row['payload']).get(
                'provenance', {}).get('goal_version', 1) >= epoch]
            fault_history = [dict(row) for row in db.execute(
                'SELECT f.*,s.through,s.recovery_id FROM runtime_faults f JOIN runtime_fault_state s '
                'USING(goal_id,source,identity) WHERE f.goal_id=? ORDER BY f.rowid', (goal_id,))]
            faults = [row for row in fault_history if (
                row['source'] == 'delivery' or row['status'] in {'interrupted', 'unknown'}
                or row['status'] != 'preparation_failed' and row['version'] >= epoch and not row['recovery_id'])]
            failure_through = max((row['through'] for row in fault_history
                                   if row['source'] == 'execution'), default=0)
            recovered = [row['identity'] for row in fault_history if row['recovery_id']]
            # Recovery scheduling keeps its original epoch-local coverage. Body
            # deltas use all accepted history so a new epoch cannot repeat old causes.
            # Both describe Inbox acceptance, NOT handled work; runtime still checks
            # the original execution receipt before admitting another reminder.
            through = max((read_json(row['payload']).get('provenance', {}).get('wake_through', 0)
                           for row in reminders if row['status'] == 'accepted'), default=0)
            cause_through = max((read_json(row['payload']).get('provenance', {}).get('wake_through', 0)
                                 for row in accepted), default=0)
            causes = [row[0] for row in db.execute(
                'SELECT trigger FROM wake_signals WHERE goal_id=? AND sequence>? AND sequence<=? '
                'ORDER BY sequence DESC LIMIT 16', (goal_id, cause_through, sequence))]
            cause_count = db.execute('SELECT COUNT(*) FROM wake_signals WHERE goal_id=? '
                                     'AND sequence>? AND sequence<=?', (goal_id, cause_through, sequence)).fetchone()[0]
            task_sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM wake_signals WHERE goal_id=? "
                "AND (trigger LIKE 'tasks:%' OR trigger LIKE 'task-snapshot:%')", (goal_id,)).fetchone()[0]
            manifest = db.execute("SELECT trigger FROM wake_signals WHERE goal_id=? AND trigger LIKE "
                                  "'task-snapshot:%' ORDER BY sequence DESC LIMIT 1", (goal_id,)).fetchone()
            return {'sequence': sequence, 'causes': causes, 'cause_count': cause_count, 'through': through,
                    'cause_through': cause_through, 'reminders': reminders, 'epoch': epoch,
                    'manifest': manifest[0] if manifest is not None else '', 'epoch_time': epoch_time,
                    'task_sequence': task_sequence, 'accepted': accepted, 'faults': faults,
                    'fault_history': fault_history, 'failure_through': failure_through, 'recovered': recovered}
        return await self.database.disk(read)

    async def record_fault(self, goal: Goal, source: str, identifier: str, status: str, error: str) -> bool:
        """Persist original execution/delivery facts without a lifecycle revision or wake.

        A stale asynchronous fact cannot fence a newer continuation. Content-only
        edits are harmless; explicit recovery epochs, not generic version bumps,
        fence stale admission; later verified host success may supersede known failures.
        """
        _validate_fault(source, identifier, status, error)
        def write(db: sqlite3.Connection) -> bool:
            row = db.execute('SELECT g.* FROM goals g JOIN current c ON c.goal_id=g.id').fetchone()
            if row is None or _goal(row).id != goal.id or not remindable(_goal(row)):
                return False
            saved = db.execute('SELECT payload FROM revisions WHERE goal_id=? AND version=?',
                               (goal.id, goal.version)).fetchone()
            if saved is None or decode_goal(saved[0]) != goal or _epoch(db, goal.id)[0] > goal.version:
                return False  # Content revisions do not discard a fault; explicit recovery epochs do.
            db.execute('INSERT OR IGNORE INTO runtime_faults VALUES(?,?,?,?,?,?)',
                       (goal.id, goal.version, source, identifier, status, error[:4096]))
            db.execute("INSERT OR IGNORE INTO runtime_fault_state "
                       "SELECT ?,?,?,COALESCE(MAX(sequence),0),'' FROM wake_signals WHERE goal_id=?",
                       (goal.id, source, identifier, goal.id))
            return True
        return await self.database.disk(write)

    async def recover_execution(self, goal: Goal, identifier: str, faults: Sequence[dict[str, Any]]) -> bool:
        """Record actual later success for the exact previously observed failures.

        Caller verifies the host's latest durable execution AFTER taking this
        snapshot. Late/new faults cannot be cleared by an earlier read. Original
        fault/outbox rows and Goal lifecycle remain unchanged; uncertain effects
        are deliberately ineligible, even across user pause/resume.
        """
        text(identifier, maximum=1024)
        eligible = [row for row in faults if row['source'] == 'execution'
                    and row['status'] in {'failed', 'cancelled', 'canceled'} and row['identity'] != identifier]
        def write(db: sqlite3.Connection) -> bool:
            row = db.execute('SELECT g.* FROM goals g JOIN current c ON c.goal_id=g.id').fetchone()
            if row is None or _goal(row).id != goal.id or not remindable(_goal(row)):
                return False
            saved = db.execute('SELECT payload FROM revisions WHERE goal_id=? AND version=?',
                               (goal.id, goal.version)).fetchone()
            if saved is None or decode_goal(saved[0]) != goal or _epoch(db, goal.id)[0] > goal.version:
                return False
            for fault in eligible:
                if fault['goal_id'] != goal.id:
                    raise ValueError('Recovery cannot borrow another Goal fault')
                db.execute('UPDATE runtime_fault_state SET recovery_id=? WHERE goal_id=? AND source=? AND identity=? '
                           "AND recovery_id='' AND EXISTS(SELECT 1 FROM runtime_faults f WHERE "
                           'f.goal_id=runtime_fault_state.goal_id AND f.source=runtime_fault_state.source '
                           'AND f.identity=runtime_fault_state.identity AND f.version=? AND f.status=?)',
                           (identifier, goal.id, fault['source'], fault['identity'], fault['version'], fault['status']))
            return True
        return await self.database.disk(write)

    async def cancel_pending(self, identifier: str) -> None:
        # Only unclaimed local work. Never withdraw host-accepted input.
        def cancel(db: sqlite3.Connection) -> None:
            db.execute("UPDATE reminders SET status='cancelled' WHERE id=? AND status IN ('pending','unavailable')",
                       (identifier,))
        await self.database.disk(cancel)

    async def close(self) -> None:
        await self.database.close()
