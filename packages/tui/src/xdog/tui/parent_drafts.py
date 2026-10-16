"""Opt-in parent composer persistence and reviewed recovery, with host-owned admission."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from xdog.tui.child_draft_autosave import ChildDraftAutosave
from xdog.tui.child_draft_io import ChildDraftIO
from xdog.tui.child_draft_store import ChildDraftStore, SavedChildDraft
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.child_draft_panel import DraftEntry


class ParentDrafts:
    def __init__(self, root: Callable[[], Path], snapshot: Callable[[], ChildDraft], context: Callable[[], str],
                 notice: Callable[[str], None], remaining_bytes: Callable[[], int] = lambda: 40 * 1024 * 1024,
                 *, create_parent: bool = False) -> None:
        self.root, self.snapshot, self.context, self.notice = root, snapshot, context, notice
        self.remaining_bytes = remaining_bytes
        self.create_parent = create_parent
        self.autosave = ChildDraftAutosave()
        self.io = ChildDraftIO()
        self.saved: dict[str, SavedChildDraft] = {}
        self._read_context = ''
        self._read_snapshot: ChildDraft | None = None
        self._read_alive: Callable[[], bool] = lambda: False
        self._save_entry: DraftEntry | None = None

    @property
    def busy(self) -> bool:
        return self.io.busy or self.autosave.busy

    def invalidate_read(self) -> None:
        """Revoke view delivery and release transient references, not admitted disk operations."""
        self._read_alive = lambda: False
        self._read_snapshot = None
        self.saved.clear()
        self._save_entry = None

    def bind_read_view(self, alive: Callable[[], bool]) -> None:
        """Bind an already validated read to its exact recovery view owner.

        Hosts call this only while presenting a successful read. Captured
        composer/context checks remain in force; this is not fresh read consent.
        """
        self._read_alive = alive

    def command(self, args: str, *, alive: Callable[[], bool] = lambda: True) -> None:
        command, _, value = args.strip().partition(' ')
        if command == 'autosave' and value in {'on', 'off', ''}:
            if value:
                self.autosave.set_enabled(value == 'on')
            self.notice('Parent draft autosave ' + ('ON' if self.autosave.enabled else 'OFF')
                        + ' · PRIVATE PLAINTEXT · pending/writing edits are not acknowledged durable; '
                        'committed is a polled disk receipt, not per-key saving · /draft restore reviews recovery')
        elif command in {'restore', 'saved'}:
            if self.io.busy:
                raise ValueError('Parent draft disk work still settling')
            # Legacy "saved list" is the same bounded explicit selector, not a directory catalog.
            value = '' if value == 'list' else value
            if value and (len(value) != 32 or any(c not in '0123456789abcdef' for c in value)):
                raise ValueError('Usage: /draft restore [SNAPSHOT_ID]')
            self.invalidate_read()
            self._read_alive = alive
            self._read_context = self.context()
            self._read_snapshot = self.snapshot()
            self.io.start('load' if value else 'list', ChildDraftStore(self.root(), create_parent=self.create_parent),
                          owner=self._read_context, key=value)
        else:
            raise ValueError('Usage: /draft save | restore [SNAPSHOT_ID] | autosave [on|off]')

    def prepare_save(self, *, alive: Callable[[], bool]) -> None:
        if self.io.busy:
            raise ValueError('Parent draft disk work still settling')
        current = self.snapshot()
        if not current.text and not current.images:
            raise ValueError('Parent composer is empty; no snapshot to save')
        self.invalidate_read()
        self._read_context = self.context()
        self._read_snapshot = current
        self._read_alive = alive
        self._save_entry = DraftEntry(self._read_context, ('parent', ''), current, current,
                                      self._read_context, True)

    def save_entry(self) -> DraftEntry:
        if self._save_entry is None:
            raise ValueError('No prepared parent save')
        return self._save_entry

    def confirm_save(self, entry: DraftEntry) -> None:
        if (self.io.busy or self._save_entry is not entry or not self._read_alive()
                or self.context() != self._read_context or self.snapshot() != self._read_snapshot
                or entry.value != self.snapshot()):
            raise ValueError('Parent save draft/view changed; review current composer, no replay')
        self.io.start('save', ChildDraftStore(self.root(), create_parent=self.create_parent),
                      owner=self._read_context, entry=entry)
        self._save_entry = None  # Single-use admission; preserve composer and settle actual writer.

    def entries(self) -> tuple[DraftEntry, ...]:
        # Scopes are stored provenance, not a guessed workspace/current-slot/latest identity.
        return tuple(DraftEntry('saved-draft:parent/' + row.id, (row.source_action, row.id), row.draft, row.draft,
            f'{row.id} · {row.source_action}/{row.source_slot!r} · source {row.source_scope} · {row.label}', False)
            for row in self.saved.values())

    def action(self, action: str, entry: DraftEntry, restore: Callable[[ChildDraft], None]) -> None:
        if action != 'copy':
            raise ValueError('Primary recovery only supports explicit restore; snapshots are not deleted')
        row = self.saved.get(entry.key[1])
        if (self.io.busy or row is None or row.draft is not entry.original
                or row.draft != entry.value or self.context() != self._read_context
                or not self._read_alive() or self.snapshot() != self._read_snapshot):
            raise ValueError('Parent draft/recovery owner changed; reopen and review, no replay')
        current = self.snapshot()
        if current.text or current.images:
            raise ValueError('Parent composer must be empty; existing draft preserved')
        restore(row.draft)  # Validated conservative text/images only; never policy or cursor adoption.
        self.invalidate_read()

    def poll(self, present: Callable[[], None], *, flush: bool = False) -> bool:
        def deliver(result: dict[str, Any]) -> None:
            if result.get('error'):
                self.notice(str(result['error']))
                self._read_snapshot = None
                return
            if result['action'] == 'save':
                self.notice('Parent draft saved privately: ' + result['saved'].id
                            + ' · /draft restore reviews recovery')
                self._read_snapshot = None
            else:
                if (not self._read_alive() or self.context() != self._read_context
                        or self.snapshot() != self._read_snapshot):
                    self.notice('Parent draft/view changed during read; saved snapshots not adopted')
                    self._read_snapshot = None
                    return
                records = result['saved']
                if sum(len(image.data) for row in records for image in row.draft.images) > self.remaining_bytes():
                    self.notice('Primary snapshots exceed local retained image capacity; no partial recovery')
                    self._read_snapshot = None
                    return
                self.saved = {row.id: row for row in records}
                present()
        changed = self.io.poll(deliver)
        current = self.snapshot()
        # The shared codec permits 64 KiB for parent inputs; former execution
        # settings are not persisted or restored as policy.
        row = DraftEntry(self.context(), ('parent', ''), current, current, self.context(), True)
        return self.autosave.poll(ChildDraftStore(self.root(), create_parent=self.create_parent), (row,),
                                  lambda text: self.notice(text.replace('child autosave', 'parent autosave')
                                                          .replace('/agents saved', '/draft saved')),
                                  flush=flush) or changed
