"""Reuse the existing undo journal for a set of staged context files."""
from __future__ import annotations

from asyncio import CancelledError
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from xdog.claw.core.persistence import navigation_journal
from xdog.claw.core.persistence.navigation_journal import (
    JOURNAL,
    NavigationJournal,
    NavigationRecoveryError,
    load_manifest,
    recover_locked,
)

if TYPE_CHECKING:
    from xdog.claw.core.persistence.transcript_store import TranscriptStore


@contextmanager
def transaction(store: TranscriptStore, paths: set[Path], *,
                alive: Callable[[], bool] = lambda: True) -> Iterator[None]:
    """Caller stages cache changes and publishes only after successful exit.

    A failed rollback fences this store until reopen; it is never permission to
    append, reset, fall back to another model or replay uncertain tool effects.
    """
    with store._lock:
        store._require_navigation_ready()
        with navigation_journal.ownership(store._sessions_dir):
            if not alive():
                raise CancelledError
            if store._load_index() != store._index:
                raise ValueError('Context file transaction owner changed outside its writer')
            pending = load_manifest(store._sessions_dir)
            if pending is not None:
                if pending['phase'] != 'committed':
                    store._navigation_recovery_required = True
                    raise NavigationRecoveryError('Uncertain context transaction; reopen original store')
                recover_locked(store._sessions_dir)  # Committed housekeeping only.
            try:
                journal = NavigationJournal(store._sessions_dir, paths)
            except BaseException:
                store._navigation_recovery_required = (store._sessions_dir / JOURNAL).exists()
                raise
            try:
                yield
                journal.commit()  # Sync replacement names BEFORE the durable commit marker.
            except BaseException:
                try:
                    journal.rollback()
                except BaseException:
                    store._navigation_recovery_required = True
                    raise
                raise
