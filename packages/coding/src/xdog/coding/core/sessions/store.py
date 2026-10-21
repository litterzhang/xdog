"""Atomic request sidecar; never append pending work to the model's history."""

from __future__ import annotations

import asyncio
import json
import math
import os
import stat
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xdog.agent.session_input import SessionInput, decode_input, input_data
from xdog.coding.core.sessions.models import STATES, TERMINAL, InputRecord

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


MAX_HISTORY_BYTES = 64 * 1024 * 1024
MAX_ERROR_CHARS = 4096
MAX_BATCH_BYTES = 256


def _diagnostic(value: str) -> str:
    if len(value) <= MAX_ERROR_CHARS:
        return value
    marker = ' [truncated]'
    return value[:MAX_ERROR_CHARS - len(marker)] + marker


def _batch_id(value: str) -> str:
    if not isinstance(value, str) or '\x00' in value or len(value.encode()) > MAX_BATCH_BYTES:
        raise ValueError('Invalid input history batch identity')
    return value


class InputStore:
    def __init__(self, session: AgentSession) -> None:
        self.path = session.session_manager.input_history_path(session.session_id)
        self._lock = asyncio.Lock()
        raw = self.read_records(self.path)
        self.needs_recovery = any(row.status not in TERMINAL for row in raw)
        self._records = tuple(replace(row, status="interrupted") if row.status not in TERMINAL else row for row in raw)

    @staticmethod
    def read_records(path: Path) -> tuple[InputRecord, ...]:
        """Read actual durable facts without interpreting generation or starting an owner."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return ()
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("Input history must be a private regular file")
            if info.st_size > MAX_HISTORY_BYTES:
                raise ValueError("Input history exceeds capacity")
            with os.fdopen(fd, "r", closefd=False) as stream:
                rows = json.load(stream)
        finally:
            os.close(fd)
        if not isinstance(rows, list):
            raise ValueError("Invalid input history")
        result = []
        identifiers: set[str] = set()
        for row in rows:
            if (not isinstance(row, dict) or set(row) not in (
                {"input", "branch_id", "generation", "status", "error"},
                {"input", "branch_id", "generation", "status", "error", "batch_id"},
                {"input", "branch_id", "generation", "status", "error", "batch_id",
                 "start_order", "started_at", "settled_at"}
            )):
                raise ValueError("Invalid input history record")
            if not isinstance(row["status"], str) or row["status"] not in STATES or any(
                not isinstance(row[key], str) for key in ("branch_id", "generation", "error")
            ):
                raise ValueError("Invalid input history state")
            batch_id = row.get('batch_id', '')
            _batch_id(batch_id)
            value = decode_input(row["input"])
            if value.id in identifiers:
                raise ValueError("Duplicate input history identity")
            identifiers.add(value.id)
            order, started, settled = row.get('start_order'), row.get('started_at'), row.get('settled_at')
            if (order is not None and (type(order) is not int or not 0 <= order < 2**53)
                    or any(v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v <= 0)
                           for v in (started, settled))
                    or bool(order) != (started is not None)
                    or settled is not None and (not order or row['status'] not in TERMINAL)
                    or order and (not batch_id or row['status'] in {'queued', 'paused'})
                    or order and row['status'] in TERMINAL and settled is None):
                raise ValueError('Invalid input execution metadata')
            result.append(InputRecord(value, row["branch_id"], row["generation"], row["status"], row["error"],
                                      batch_id, order, started, settled))
        return tuple(result)

    def records(self) -> tuple[InputRecord, ...]:
        return self._records

    @staticmethod
    def _encode(records: tuple[InputRecord, ...], *, reserve: bool = False) -> bytes:
        rows = []
        for row in records:
            pending = reserve and row.status not in TERMINAL
            data: dict[str, Any] = {
                'input': input_data(row.input), 'branch_id': row.branch_id, 'generation': row.generation,
                'status': 'interrupted' if pending else row.status,
                # Non-BMP diagnostics require two JSON escapes per character. Reserve all final metadata
                # for every accepted unfinished input, not just the next batch.
                'error': '\U00010000' * MAX_ERROR_CHARS if pending else row.error,
                'batch_id': '\x01' * MAX_BATCH_BYTES if pending else row.batch_id,
            }
            if row.start_order is not None:
                data.update(start_order=2**53 - 1 if pending else row.start_order,
                            started_at=1.7976931348623157e+308 if pending else row.started_at,
                            settled_at=1.7976931348623157e+308 if pending else row.settled_at)
            rows.append(data)
        return json.dumps(rows, ensure_ascii=True, allow_nan=False).encode('ascii')

    def _commit(self, records: tuple[InputRecord, ...]) -> None:
        payload = self._encode(records)
        if max(len(payload), len(self._encode(records, reserve=True))) > MAX_HISTORY_BYTES:
            raise ValueError('Input history capacity exhausted; start an explicit new session')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError('Input history must not be a symbolic link')
        fd, name = tempfile.mkstemp(prefix=".inputs-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def _save(self, records: tuple[InputRecord, ...]) -> None:
        def commit() -> tuple[InputRecord, ...]:
            # Timestamp the real commit operation, not a queued event or a later
            # reader. Publish only after replace + file/directory fsync succeed.
            now = time.time()
            stamped = tuple(replace(row, started_at=row.started_at if row.started_at is not None else now,
                settled_at=(now if row.status in TERMINAL and row.settled_at is None else row.settled_at))
                if row.start_order else row for row in records)
            self._commit(stamped)
            return stamped
        task = asyncio.create_task(asyncio.to_thread(commit))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        self._records = task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def recover(self) -> None:
        """The leased controller persists interrupted receipts; a query never does."""
        async with self._lock:
            if self.needs_recovery:
                await self._save(self._records)
                self.needs_recovery = False

    @staticmethod
    def latest_execution(path: Path, branch: str) -> dict[str, Any] | None:
        records = InputStore.read_records(path)
        selected = [row for row in records if row.branch_id == branch]
        started = [row for row in selected if row.start_order]
        if not started:
            if any(row.start_order is None for row in selected):
                return {'availability': 'unavailable', 'reason': 'legacy_execution_metadata_missing'}
            return None
        # Validate all recorded batches before selection; corruption is not a
        # legacy/unavailable result. Ordering is never derived from intake order.
        groups: dict[int, list[InputRecord]] = {}
        for row in started:
            assert row.start_order is not None
            groups.setdefault(row.start_order, []).append(row)
        for members in groups.values():
            if len({(r.batch_id, r.generation, r.status, r.started_at, r.settled_at) for r in members}) != 1:
                raise ValueError('Inconsistent input execution batch receipt')
        order = max(groups)
        members = groups[order]
        first = members[0]
        result: dict[str, Any] = {'execution_id': first.batch_id, 'status': first.status,
            'message_ids': [row.input.id for row in members], 'start_order': order, 'started_at': first.started_at}
        if first.settled_at is not None:
            result['settled_at'] = first.settled_at
        return result

    async def accept(self, record: InputRecord) -> None:
        async with self._lock:
            if record.status not in STATES or any(row.input.id == record.input.id for row in self._records):
                raise ValueError("Invalid or duplicate input record")
            checked = replace(record, input=decode_input(input_data(record.input)),
                              error=_diagnostic(record.error), batch_id=_batch_id(record.batch_id))
            await self._save((*self._records, checked))

    async def edit_pending(self, value: SessionInput) -> bool:
        async with self._lock:
            if not any(row.input.id == value.id and row.status in {"queued", "paused"} for row in self._records):
                return False
            checked = decode_input(input_data(value))
            await self._save(tuple(replace(row, input=checked) if row.input.id == value.id else row
                                   for row in self._records))
            return True

    async def transition(self, identifier: str, status: str, error: str = "") -> None:
        async with self._lock:
            if status not in STATES or not any(row.input.id == identifier for row in self._records):
                raise ValueError("Invalid input history transition")
            if any(
                row.input.id == identifier and row.status in TERMINAL and row.status != status for row in self._records
            ):
                raise ValueError("Cannot reactivate a terminal input")
            rows = tuple(
                replace(row, status=status, error=_diagnostic(error)) if row.input.id == identifier else row
                for row in self._records
            )
            await self._save(rows)


    async def transition_batch(self, identifiers: tuple[str, ...], status: str, *, batch_id: str,
                               error: str = '') -> None:
        async with self._lock:
            selected = set(identifiers)
            if (not selected or len(selected) != len(identifiers) or status not in STATES
                    or not selected <= {row.input.id for row in self._records}):
                raise ValueError('Invalid input batch transition')
            if any(row.input.id in selected and row.status in TERMINAL and row.status != status
                   for row in self._records):
                raise ValueError('Cannot reactivate a terminal input')
            batch_id = _batch_id(batch_id)
            order = max((row.start_order or 0 for row in self._records), default=0) + 1
            if status == 'running' and (not batch_id or order >= 2**53):
                raise ValueError('Invalid execution start identity/order')
            rows = tuple(replace(row, status=status, error=_diagnostic(error), batch_id=batch_id,
                                start_order=order if status == 'running' else row.start_order)
                         if row.input.id in selected else row for row in self._records)
            await self._save(rows)
