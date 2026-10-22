"""Immutable output chunks and separate, fenced transport attempts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xdog.agent.leased_sqlite import LeasedSqlite
from xdog.claw.core.inputs.models import InputEnvelope, InputScope, decode_envelope, encode_envelope

_FORMAT = "xdog.claw.outputs.v2"
_STATES = frozenset({"pending", "sending", "delivered", "unknown", "cancelled"})
_SCHEMA = """
CREATE TABLE origins (
    origin_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, workspace TEXT NOT NULL, message_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL, binding TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL);
CREATE TABLE outputs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, output_id TEXT NOT NULL UNIQUE,
    origin_id TEXT NOT NULL REFERENCES origins(origin_id), ordinal INTEGER NOT NULL,
    text TEXT NOT NULL, text_hash TEXT NOT NULL, status TEXT NOT NULL,
    send_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', UNIQUE(origin_id,ordinal));
CREATE TABLE send_attempts (
    send_id TEXT PRIMARY KEY, output_id TEXT NOT NULL REFERENCES outputs(output_id),
    status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
"""
_QUERY = """SELECT o.*, b.group_id, b.workspace, b.message_id, b.attempt_id, b.binding, b.payload, b.payload_hash
FROM outputs o JOIN origins b ON o.origin_id=b.origin_id"""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _key(parts: list[str | int]) -> str:
    return _digest(json.dumps(parts, ensure_ascii=True, separators=(",", ":")))


def _label(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value.encode()) > 256:
        raise ValueError("Invalid output identity or binding")


def _origin_key(envelope: InputEnvelope, attempt_id: str, binding: str) -> str:
    _label(attempt_id)
    _label(binding)
    return _key([envelope.scope.group_id, str(envelope.scope.workspace), envelope.message.id, attempt_id, binding])


def _ordinal(value: int) -> None:
    if type(value) is not int or not 0 <= value < 2**53:
        raise ValueError("Output ordinal must be a nonnegative integer")


def _text(value: str) -> None:
    if not isinstance(value, str) or not value or len(value.encode()) > 64 * 1024:
        raise ValueError("Output chunk must be nonempty bounded text")


@dataclass(frozen=True)
class OutputRecord:
    id: str
    envelope: InputEnvelope
    attempt_id: str
    ordinal: int
    binding: str
    text: str
    status: str = "pending"
    send_id: str = ""
    error: str = ""


def delivery_status(rows: Sequence[OutputRecord]) -> str:
    """Aggregate actual chunk receipts without claiming a partial send succeeded."""
    states = {row.status for row in rows}
    if not states:
        return 'untracked'
    if states == {'delivered'}:
        return 'delivered'
    if 'unknown' in states:
        return 'unknown'
    if states <= {'pending', 'sending'}:
        return 'pending'
    if states == {'cancelled'}:
        return 'cancelled'
    return 'partial'


def _record(row: sqlite3.Row) -> OutputRecord:
    envelope = decode_envelope(row["payload"])
    origin = _origin_key(envelope, row["attempt_id"], row["binding"])
    _ordinal(row["ordinal"])
    _text(row["text"])
    if (
        origin != row["origin_id"]
        or _key([origin, row["ordinal"]]) != row["output_id"]
        or _digest(row["payload"]) != row["payload_hash"]
        or _digest(row["text"]) != row["text_hash"]
        or (envelope.scope.group_id, str(envelope.scope.workspace), envelope.message.id)
        != (row["group_id"], row["workspace"], row["message_id"])
        or row["status"] not in _STATES
        or not isinstance(row["error"], str)
    ):
        raise ValueError("Corrupt output identity, content or state")
    return OutputRecord(
        row["output_id"],
        envelope,
        row["attempt_id"],
        row["ordinal"],
        row["binding"],
        row["text"],
        row["status"],
        row["send_id"],
        row["error"],
    )


def _select(db: sqlite3.Connection, identifier: str) -> OutputRecord:
    row = db.execute(_QUERY + " WHERE o.output_id=?", (identifier,)).fetchone()
    if row is None:
        raise ValueError("Unknown output identity")
    return _record(row)


def _validate(db: sqlite3.Connection) -> None:
    expected = {
        "origins": {
            "origin_id",
            "group_id",
            "workspace",
            "message_id",
            "attempt_id",
            "binding",
            "payload",
            "payload_hash",
        },
        "outputs": {"sequence", "output_id", "origin_id", "ordinal", "text", "text_hash", "status", "send_id", "error"},
        "send_attempts": {"send_id", "output_id", "status", "error"},
    }
    for table, columns in expected.items():
        if {row[1] for row in db.execute(f"PRAGMA table_info({table})")} != columns:
            raise ValueError("Unsupported output journal schema; no upgrade is performed")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Output journal contains a missing original binding")
    for row in db.execute(_QUERY + " ORDER BY o.sequence"):
        record = _record(row)
        attempts = db.execute("SELECT * FROM send_attempts WHERE output_id=? ORDER BY rowid", (record.id,)).fetchall()
        for attempt in attempts:
            uuid.UUID(hex=attempt["send_id"])
            if attempt["status"] not in {"sending", "not_sent", "delivered", "unknown"}:
                raise ValueError("Unknown output send attempt state")
        if record.send_id:
            state = "not_sent" if record.status in {"pending", "cancelled"} else record.status
            if (
                not attempts
                or (attempts[-1]["send_id"], attempts[-1]["status"], attempts[-1]["error"])
                != (record.send_id, state, record.error)
                or any(attempt["status"] != "not_sent" for attempt in attempts[:-1])
            ):
                raise ValueError("Output and send attempt facts disagree")
        elif attempts or record.status not in {"pending", "cancelled"} or record.error:
            raise ValueError("Output delivery state is missing its send attempt")


def _recover(db: sqlite3.Connection) -> None:
    error = "Transport interrupted; output may have been accepted, inspect before retry"
    db.execute("UPDATE send_attempts SET status='unknown',error=? WHERE status='sending'", (error,))
    db.execute("UPDATE outputs SET status='unknown',error=? WHERE status='sending'", (error,))


class OutputStore:
    def __init__(self, database: LeasedSqlite) -> None:
        self._database = database
        self.path = database.path

    @classmethod
    async def open(cls, path: Path) -> OutputStore:
        database = await LeasedSqlite.open(path, format=_FORMAT, schema=_SCHEMA, validate=_validate, recover=_recover)
        return cls(database)

    async def append(
        self, envelope: InputEnvelope, attempt_id: str, ordinal: int, binding: str, text: str
    ) -> OutputRecord:
        _ordinal(ordinal)
        _text(text)
        origin = _origin_key(envelope, attempt_id, binding)
        identifier = _key([origin, ordinal])
        payload = encode_envelope(envelope)

        def write(db: sqlite3.Connection) -> OutputRecord:
            saved = db.execute("SELECT payload FROM origins WHERE origin_id=?", (origin,)).fetchone()
            if saved is not None and saved["payload"] != payload:
                raise ValueError("Output original input conflict")
            db.execute(
                "INSERT OR IGNORE INTO origins VALUES (?,?,?,?,?,?,?,?)",
                (
                    origin,
                    envelope.scope.group_id,
                    str(envelope.scope.workspace),
                    envelope.message.id,
                    attempt_id,
                    binding,
                    payload,
                    _digest(payload),
                ),
            )
            row = db.execute("SELECT text FROM outputs WHERE output_id=?", (identifier,)).fetchone()
            if row is not None and row["text"] != text:
                raise ValueError("Output chunk content conflict")
            db.execute(
                "INSERT OR IGNORE INTO outputs(output_id,origin_id,ordinal,text,text_hash,status) "
                "VALUES (?,?,?,?,?,'pending')",
                (identifier, origin, ordinal, text, _digest(text)),
            )
            return _select(db, identifier)

        return await self._database.disk(write)

    async def records(self, scope: InputScope | None = None, *, message_id: str | None = None) -> list[OutputRecord]:
        def read(db: sqlite3.Connection) -> list[OutputRecord]:
            query, args = _QUERY, () if scope is None else (scope.group_id, str(scope.workspace))
            if scope is not None:
                query += " WHERE b.group_id=? AND b.workspace=?"
            if message_id is not None:
                _label(message_id)
                query += (" AND" if scope is not None else " WHERE") + " b.message_id=?"
                args = (*args, message_id)
            return [_record(row) for row in db.execute(query + " ORDER BY o.sequence", args)]

        return await self._database.disk(read)

    async def delivery_for_reference(self, scope: InputScope, reference: str) -> dict[str, Any]:
        """Bounded public delivery facts, never destinations or message contents."""
        if not isinstance(reference, str) or not reference.strip() or len(reference.encode()) > 256:
            raise ValueError('Invalid output correlation reference')
        def read(db: sqlite3.Connection) -> dict[str, Any]:
            values = db.execute(_QUERY + " WHERE b.group_id=? AND b.workspace=? AND "
                "json_extract(b.payload,'$.context.outbound_tool')='message' AND "
                "json_extract(b.payload,'$.message.provenance.reference')=? ORDER BY o.sequence LIMIT 257",
                (scope.group_id, str(scope.workspace), reference)).fetchall()
            if len(values) > 256:
                return {'status': 'unavailable', 'reason': 'Correlated output facts exceed bounded read capacity'}
            rows = [_record(row) for row in values]
            if not rows:
                return {'status': 'untracked',
                        'reason': 'No correlated output receipt; this is not proof nothing was sent'}
            return {'status': delivery_status(rows), 'outputs': [{'id': row.id, 'status': row.status} for row in rows]}
        return await self._database.disk(read)

    async def cancel_pending(self, scope: InputScope, message_ids: tuple[str, ...]) -> tuple[str, ...]:
        """Cancel only positively unsent outputs, preserving every actual send fact."""
        if not isinstance(scope, InputScope) or type(message_ids) is not tuple:
            raise ValueError("Output cancellation requires a frozen scope/message selection")
        for identifier in message_ids:
            _label(identifier)
        selected = frozenset(message_ids)

        def write(db: sqlite3.Connection) -> tuple[str, ...]:
            rows = db.execute(_QUERY + " WHERE b.group_id=? AND b.workspace=? AND o.status='pending' "
                              "ORDER BY o.sequence", (scope.group_id, str(scope.workspace)))
            identifiers = tuple(row["output_id"] for row in rows if row["message_id"] in selected)
            for identifier in identifiers:
                db.execute("UPDATE outputs SET status='cancelled' WHERE output_id=? AND status='pending'",
                           (identifier,))
            return identifiers

        return await self._database.disk(write)

    async def begin(self, identifier: str) -> OutputRecord:
        def write(db: sqlite3.Connection) -> OutputRecord:
            record = _select(db, identifier)
            if record.status != "pending":
                raise ValueError("Only proven-not-sent output may start a send attempt")
            send_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO send_attempts(send_id,output_id,status) VALUES (?,?,'sending')", (send_id, identifier)
            )
            db.execute(
                "UPDATE outputs SET status='sending',send_id=?,error='' WHERE output_id=?", (send_id, identifier)
            )
            return _select(db, identifier)

        return await self._database.disk(write)

    async def settle(self, identifier: str, send_id: str, status: str, error: str = "") -> OutputRecord:
        if status not in {"pending", "delivered", "unknown"} or not isinstance(error, str):
            raise ValueError("Invalid output send outcome")

        def write(db: sqlite3.Connection) -> OutputRecord:
            record = _select(db, identifier)
            if record.status != "sending" or record.send_id != send_id:
                raise ValueError("Output send attempt changed; no stale settlement")
            attempt_state = "not_sent" if status == "pending" else status
            db.execute("UPDATE send_attempts SET status=?,error=? WHERE send_id=?", (attempt_state, error, send_id))
            db.execute("UPDATE outputs SET status=?,error=? WHERE output_id=?", (status, error, identifier))
            return _select(db, identifier)

        return await self._database.disk(write)

    async def close(self) -> None:
        await self._database.close()
