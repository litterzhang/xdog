"""Goal-owned leased journal, history, artifacts and fenced notification outbox."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from xdog.agent.leased_sqlite import LeasedSqlite
from xdog.goal.codec import decode_goal, decode_notice, encode_goal, encode_notice, json_text
from xdog.goal.models import Goal, Notice, Owner, text

_FORMAT = "xdog.goal.journal.v1"
_SCHEMA = """
CREATE TABLE owner(key TEXT NOT NULL,workspace TEXT NOT NULL);
CREATE TABLE goals(id TEXT PRIMARY KEY,version INTEGER NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL);
CREATE TABLE revisions(goal_id TEXT NOT NULL REFERENCES goals(id),version INTEGER NOT NULL,
    payload TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(goal_id,version));
CREATE TABLE artifacts(goal_id TEXT NOT NULL REFERENCES goals(id),key TEXT NOT NULL,
    payload TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(goal_id,key));
CREATE TABLE notices(id TEXT PRIMARY KEY,goal_id TEXT NOT NULL REFERENCES goals(id),payload TEXT NOT NULL,
    digest TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',send_id TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '');
CREATE TABLE notice_attempts(send_id TEXT PRIMARY KEY,notice_id TEXT NOT NULL REFERENCES notices(id),
    status TEXT NOT NULL,error TEXT NOT NULL DEFAULT '');
"""


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def _goal(row: sqlite3.Row) -> Goal:
    goal = decode_goal(row["payload"])
    if goal.id != row["id"] or goal.version != row["version"] or _digest(row["payload"]) != row["digest"]:
        raise ValueError("Goal snapshot identity/version/digest disagree")
    return goal


@dataclass(frozen=True)
class NoticeReceipt:
    notice: Notice
    status: str
    send_id: str = ""
    error: str = ""


def _notice(row: sqlite3.Row) -> NoticeReceipt:
    notice = decode_notice(row["payload"])
    if (
        notice.id != row["id"]
        or notice.goal_id != row["goal_id"]
        or _digest(row["payload"]) != row["digest"]
        or row["status"] not in {"pending", "sending", "accepted", "unknown", "cancelled"}
    ):
        raise ValueError("Invalid Goal notification facts")
    return NoticeReceipt(notice, row["status"], row["send_id"], row["error"])


def _write(db: sqlite3.Connection, goal: Goal, expected: int) -> None:
    payload = encode_goal(goal)
    current = db.execute("SELECT version FROM goals WHERE id=?", (goal.id,)).fetchone()
    if (
        type(expected) is not int
        or expected < 0
        or goal.version != expected + 1
        or (current is None and expected != 0)
        or (current is not None and current["version"] != expected)
    ):
        raise ValueError("Goal version changed; re-read before applying this operation")
    db.execute(
        "INSERT INTO goals VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "version=excluded.version,payload=excluded.payload,digest=excluded.digest",
        (goal.id, goal.version, payload, _digest(payload)),
    )
    db.execute("INSERT INTO revisions VALUES (?,?,?,?)", (goal.id, goal.version, payload, _digest(payload)))


def _validate(db: sqlite3.Connection, owner: Owner) -> None:
    expected = {
        "owner": {"key", "workspace"},
        "goals": {"id", "version", "payload", "digest"},
        "revisions": {"goal_id", "version", "payload", "digest"},
        "artifacts": {"goal_id", "key", "payload", "digest"},
        "notices": {"id", "goal_id", "payload", "digest", "status", "send_id", "error"},
        "notice_attempts": {"send_id", "notice_id", "status", "error"},
    }
    for table, columns in expected.items():
        if {row[1] for row in db.execute(f"PRAGMA table_info({table})")} != columns:
            raise ValueError("Unsupported Goal journal schema; no upgrade is performed")
    rows = db.execute("SELECT key,workspace FROM owner").fetchall()
    if [tuple(row) for row in rows] != [(owner.key, str(owner.workspace))]:
        raise ValueError("Goal storage owner/workspace changed; no transfer or upgrade")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Goal journal contains orphaned domain facts")
    for row in db.execute("SELECT * FROM goals"):
        goal = _goal(row)
        history = db.execute("SELECT * FROM revisions WHERE goal_id=? ORDER BY version", (goal.id,)).fetchall()
        if [item["version"] for item in history] != list(range(1, goal.version + 1)):
            raise ValueError("Goal historical versions are missing")
        for item in history:
            saved = decode_goal(item["payload"])
            if saved.id != goal.id or saved.version != item["version"] or _digest(item["payload"]) != item["digest"]:
                raise ValueError("Goal history is corrupt")
        if history[-1]["payload"] != row["payload"]:
            raise ValueError("Current Goal does not match its authoritative history")
    for row in db.execute("SELECT * FROM artifacts"):
        json_text(json.loads(row["payload"]), maximum=64 * 1024)
        if _digest(row["payload"]) != row["digest"]:
            raise ValueError("Goal evidence artifact digest is corrupt")
    for row in db.execute("SELECT * FROM notices"):
        receipt = _notice(row)
        attempts = db.execute(
            "SELECT * FROM notice_attempts WHERE notice_id=? ORDER BY rowid", (receipt.notice.id,)
        ).fetchall()
        for attempt in attempts:
            uuid.UUID(hex=attempt["send_id"])
            if attempt["status"] not in {"pending", "sending", "accepted", "unknown"}:
                raise ValueError("Unknown Goal notice attempt state")
        if any(attempt["status"] not in {"pending", "unknown"} for attempt in attempts[:-1]):
            raise ValueError("A historical Goal notice attempt remains active or accepted")
        if receipt.send_id:
            if not attempts or (attempts[-1]["send_id"], attempts[-1]["status"], attempts[-1]["error"]) != (
                receipt.send_id,
                "pending" if receipt.status == "cancelled" else receipt.status,
                receipt.error,
            ):
                raise ValueError("Goal notification and attempt facts disagree")
        elif attempts or receipt.status not in {"pending", "cancelled"} or receipt.error:
            raise ValueError("Goal notification outcome has no send attempt")


def _recover(db: sqlite3.Connection) -> None:
    for row in db.execute("SELECT * FROM goals").fetchall():
        goal = _goal(row)
        tasks = tuple(
            replace(
                task,
                state="interrupted",
                attempts=(*task.attempts[:-1], replace(task.attempts[-1], state="interrupted")),
            )
            if task.attempts and task.attempts[-1].state in {"starting", "running"}
            else task
            for task in goal.tasks
        )
        review = goal.review
        if review is not None and review.state in {"starting", "running"}:
            review = replace(review, state="interrupted")
        if tasks != goal.tasks or review != goal.review:
            recovered = replace(
                goal,
                version=goal.version + 1,
                tasks=tasks,
                review=review,
                status="waiting" if goal.status == "reviewing" else goal.status,
                wait_reason="Host execution interrupted; inspect effects before an explicit retry",
            )
            _write(db, recovered, goal.version)
    error = "Notification transport interrupted; inspect original enqueue receipt"
    db.execute("UPDATE notices SET status='unknown',error=? WHERE status='sending'", (error,))
    db.execute("UPDATE notice_attempts SET status='unknown',error=? WHERE status='sending'", (error,))


class GoalStore:
    """One owner owns Goal facts. It never imports or stores a host runtime."""

    def __init__(self, database: LeasedSqlite, owner: Owner) -> None:
        self._database, self.owner = database, owner
        self.path = database.path

    @classmethod
    async def open(cls, path: Path, owner: Owner) -> GoalStore:
        if not isinstance(owner, Owner):
            raise ValueError("Goal storage requires an explicit host owner")
        # Install owner only during exclusive creation, not as an existing-file
        # repair. The literal is parameter-quoted, not a model-selected schema.
        quoted_key = "'" + owner.key.replace("'", "''") + "'"
        quoted_workspace = "'" + str(owner.workspace).replace("'", "''") + "'"
        schema = _SCHEMA + f"INSERT INTO owner VALUES ({quoted_key},{quoted_workspace});"
        database = await LeasedSqlite.open(
            path, format=_FORMAT, schema=schema, validate=lambda db: _validate(db, owner), recover=_recover
        )
        return cls(database, owner)

    async def get(self, identifier: str) -> Goal | None:
        def read(db: sqlite3.Connection) -> Goal | None:
            row = db.execute("SELECT * FROM goals WHERE id=?", (identifier,)).fetchone()
            return _goal(row) if row is not None else None

        return await self._database.disk(read)

    async def list(self) -> tuple[Goal, ...]:
        return await self._database.disk(
            lambda db: tuple(_goal(row) for row in db.execute("SELECT * FROM goals ORDER BY rowid"))
        )

    async def history(self, identifier: str) -> tuple[Goal, ...]:
        return await self._database.disk(
            lambda db: tuple(
                decode_goal(row[0])
                for row in db.execute("SELECT payload FROM revisions WHERE goal_id=? ORDER BY version", (identifier,))
            )
        )

    async def at_version(self, identifier: str, version: int) -> Goal:
        def read(db: sqlite3.Connection) -> Goal:
            row = db.execute(
                "SELECT payload FROM revisions WHERE goal_id=? AND version=?", (identifier, version)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown Goal contract version")
            return decode_goal(row[0])

        return await self._database.disk(read)

    async def put(
        self,
        goal: Goal,
        *,
        expected_version: int,
        notices: tuple[Notice, ...] = (),
        artifacts: tuple[tuple[str, object], ...] = (),
        cancel_pending_notices: bool = False,
    ) -> Goal:
        # Sever bounded mutable artifact/notice input before yielding. The Goal
        # itself is immutable typed data; aggregate encoding/validation is owned
        # worker work, not an event-loop or business lifetime byte budget.
        notice_data = [(notice, encode_notice(notice)) for notice in notices]
        artifact_data = [(text(key, maximum=256), json_text(data, maximum=64 * 1024)) for key, data in artifacts]
        saved = await self._database.run(lambda: decode_goal(encode_goal(goal)))

        def write(db: sqlite3.Connection) -> Goal:
            _write(db, saved, expected_version)
            for notice, payload in notice_data:
                if notice.goal_id != saved.id:
                    raise ValueError("Notification belongs to another Goal")
                old = db.execute("SELECT payload FROM notices WHERE id=?", (notice.id,)).fetchone()
                if old is not None and old["payload"] != payload:
                    raise ValueError("Goal notice identity/content conflict")
                db.execute(
                    "INSERT OR IGNORE INTO notices(id,goal_id,payload,digest) VALUES (?,?,?,?)",
                    (notice.id, saved.id, payload, _digest(payload)),
                )
            for key, payload in artifact_data:
                old = db.execute("SELECT payload FROM artifacts WHERE goal_id=? AND key=?", (saved.id, key)).fetchone()
                if old is not None and old["payload"] != payload:
                    raise ValueError("Goal evidence artifact content conflict")
                db.execute(
                    "INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?)", (saved.id, key, payload, _digest(payload))
                )
            if cancel_pending_notices:
                db.execute("UPDATE notices SET status='cancelled' WHERE goal_id=? AND status='pending'", (saved.id,))
            return saved

        return await self._database.disk(write)

    async def artifact(self, identifier: str, key: str) -> object:
        def read(db: sqlite3.Connection) -> object:
            row = db.execute("SELECT payload FROM artifacts WHERE goal_id=? AND key=?", (identifier, key)).fetchone()
            return json.loads(row[0]) if row is not None else None

        return await self._database.disk(read)

    async def notices(self) -> tuple[NoticeReceipt, ...]:
        return await self._database.disk(
            lambda db: tuple(_notice(row) for row in db.execute("SELECT * FROM notices ORDER BY rowid"))
        )

    async def notice_started(self, identifier: str) -> NoticeReceipt:
        def write(db: sqlite3.Connection) -> NoticeReceipt:
            row = db.execute("SELECT * FROM notices WHERE id=?", (identifier,)).fetchone()
            if row is None or row["status"] not in {"pending", "unknown"}:
                raise ValueError("Goal notification is not available for original-ID enqueue")
            send_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO notice_attempts(send_id,notice_id,status) VALUES (?,?,'sending')", (send_id, identifier)
            )
            db.execute("UPDATE notices SET status='sending',send_id=?,error='' WHERE id=?", (send_id, identifier))
            return _notice(db.execute("SELECT * FROM notices WHERE id=?", (identifier,)).fetchone())

        return await self._database.disk(write)

    async def notice_settled(self, identifier: str, status: str, error: str = "", *, send_id: str) -> None:
        if status not in {"pending", "accepted", "unknown"}:
            raise ValueError("Invalid Goal notification delivery outcome")

        def write(db: sqlite3.Connection) -> None:
            row = db.execute("SELECT * FROM notices WHERE id=?", (identifier,)).fetchone()
            if (
                row is None
                or row["send_id"] != send_id
                or row["status"] != "sending"
                and not (row["status"] == "unknown" and status == "accepted")
            ):
                raise ValueError("Goal notification send attempt changed")
            db.execute("UPDATE notices SET status=?,error=? WHERE id=?", (status, error[:4096], identifier))
            db.execute("UPDATE notice_attempts SET status=?,error=? WHERE send_id=?", (status, error[:4096], send_id))

        await self._database.disk(write)

    async def close(self) -> None:
        await self._database.close()
