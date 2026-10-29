"""Bounded durable pushed-input ledger, using the daemon's existing transaction owner.

An accepted ID is never evicted/reused. Admission ACK is not detector processing
or downstream delivery. Payloads leave the queue only with the executor checkpoint.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from xdog.eyes.daemon_store import DaemonStore
from xdog.eyes.local_control import finite
from xdog.eyes.models import EventSourceDef
from xdog.eyes.sources.event import EventInput
from xdog.eyes.sources.records import canonical, pointer, record


class EventIntake:
    def __init__(self, store: DaemonStore) -> None:
        self.store = store

    async def publish(
        self,
        registration: dict[str, Any],
        source: EventSourceDef,
        *,
        event_id: str,
        payload: object,
        received_at: float,
    ) -> tuple[str, dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ValueError("Event payload must be a finite JSON object")
        finite(payload)
        encoded = canonical(payload)
        size = len(encoded.encode("utf-8"))
        if size > source.max_bytes:
            raise ValueError("Event payload exceeds source max_bytes")
        try:
            selected = event_id if source.key_pointer is None else pointer(payload, source.key_pointer)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise ValueError("Event key_pointer does not select a value") from exc
        if type(selected) not in {str, int}:
            raise ValueError("Event key_pointer must select a nonempty string or integer")
        key = record(str(selected), payload, "").key
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        identifier = registration["id"]

        def commit(db: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
            previous = db.execute(
                "SELECT sequence, digest, processed FROM source_events "
                "WHERE registration_id=? AND source_id=? AND event_id=?",
                (identifier, source.id, event_id),
            ).fetchone()
            if previous is not None:
                if previous["digest"] != digest:
                    raise ValueError("Event ID already accepted with different content")
                return "duplicate", {
                    "registration_id": identifier,
                    "source_id": source.id,
                    "event_id": event_id,
                    "sequence": previous["sequence"],
                    "state": "processed" if previous["processed"] else "accepted",
                }
            state = db.execute("SELECT state FROM registrations WHERE id=?", (identifier,)).fetchone()
            if registration["state"] != "running" or state is None or state[0] != "running":
                raise ValueError("Event source is not running; paused/cancelled sources reject new inputs")
            count, pending, used = db.execute(
                "SELECT COUNT(*), "
                "COALESCE(SUM(CASE WHEN processed=0 THEN 1 ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN processed=0 THEN length(CAST(payload AS BLOB)) ELSE 0 END),0) "
                "FROM source_events WHERE registration_id=? AND source_id=?",
                (identifier, source.id),
            ).fetchone()
            if count >= source.max_events:
                raise ValueError("Event idempotency ledger capacity reached; create a new registration explicitly")
            if pending >= source.max_pending_events or used + size > source.max_pending_bytes:
                raise ValueError("Event queue capacity reached; input not accepted, retry the same ID later")
            cursor = db.execute(
                "INSERT INTO source_events "
                "(registration_id,source_id,event_id,record_key,digest,payload,received_at) VALUES (?,?,?,?,?,?,?)",
                (identifier, source.id, event_id, key, digest, encoded, received_at),
            )
            return "accepted", {
                "registration_id": identifier,
                "source_id": source.id,
                "event_id": event_id,
                "sequence": cursor.lastrowid,
                "state": "accepted",
            }

        return await self.store.run(commit)

    async def next(self, identifier: str, source: EventSourceDef, after: int) -> EventInput | None:
        def read(db: sqlite3.Connection) -> EventInput | None:
            row = db.execute(
                "SELECT * FROM source_events WHERE registration_id=? AND source_id=? "
                "AND sequence>? AND processed=0 ORDER BY sequence LIMIT 1",
                (identifier, source.id, after),
            ).fetchone()
            if row is None:
                return None
            return EventInput(row["sequence"], row["event_id"], row["record_key"], row["payload"], row["received_at"])

        return await self.store.run(read)

    async def status(self, identifier: str) -> dict[str, Any]:
        def read(db: sqlite3.Connection) -> dict[str, Any]:
            return {
                row["source_id"]: {
                    "accepted": row["accepted"],
                    "pending": row["pending"],
                    "processed": row["accepted"] - row["pending"],
                }
                for row in db.execute(
                    "SELECT source_id, COUNT(*) AS accepted, SUM(CASE WHEN processed=0 THEN 1 ELSE 0 END) AS pending "
                    "FROM source_events WHERE registration_id=? GROUP BY source_id",
                    (identifier,),
                )
            }

        return await self.store.run(read)
