"""Explicitly enabled, debounced child draft persistence; no automatic recovery or execution."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from xdog.tui.child_draft_io import ChildDraftIO
from xdog.tui.child_draft_store import ChildDraftStore, SavedChildDraft
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.child_draft_panel import DraftEntry

DEBOUNCE = 0.5
MAX_LOCAL_DRAFTS = 32


def _fingerprint(entry: DraftEntry) -> tuple[Any, ...]:
    draft = entry.value
    return (draft.text, draft.access, json.dumps(draft.config, sort_keys=True), draft.uncertain, draft.cursor,
            tuple((image.id, id(image.data), len(image.data), image.mime_type) for image in draft.images), entry.label)


class ChildDraftAutosave:
    def __init__(self) -> None:
        self.enabled = False
        self.io = ChildDraftIO()
        self._observed: dict[tuple[str, str, str], tuple[tuple[Any, ...], float]] = {}
        self._written: dict[tuple[str, str, str], tuple[tuple[Any, ...], SavedChildDraft]] = {}
        self._active: tuple[DraftEntry, tuple[Any, ...]] | None = None
        self._failed = False

    @property
    def busy(self) -> bool:
        return self.io.busy

    @property
    def durability(self) -> str:
        """Current observed state, not a per-key or physical-power-loss guarantee.

        A queued worker receipt does not commit UI acknowledgement until polled.
        Edits made during a write stay pending after that older write succeeds.
        """
        if self._failed:
            return 'uncertain'
        if self.busy:
            return 'writing'
        if not self.enabled:
            return 'off'
        if any(self._written.get(key, (None, None))[0] != value[0]
               for key, value in self._observed.items()):
            return 'pending'
        return 'committed' if self._written else 'empty'

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            self._failed = False
        if not enabled:
            self._observed.clear()

    def poll(self, store: ChildDraftStore, entries: tuple[DraftEntry, ...], notice: Callable[[str], None],
             *, flush: bool = False) -> bool:
        def deliver(result: dict[str, Any]) -> None:
            active, self._active = self._active, None
            if result.get('error'):
                self._failed = True
                self.set_enabled(False)  # One failure stops repeated uncertain writes until explicit re-enable.
                notice(str(result['error']) + ' · child autosave disabled; inspect /agents saved list')
            elif active is not None:
                entry, fingerprint = active
                # Future updates use only ID/digest; do not retain a second decoded
                # image/text copy outside the application's retention accounting.
                receipt = replace(result['saved'], draft=ChildDraft())
                self._written[entry.id] = fingerprint, receipt
        changed = self.io.poll(deliver)
        if not self.enabled:
            return changed
        candidates = tuple(entry for entry in entries if not entry.scope.startswith('saved-draft:')
                           and (entry.value.text or entry.value.images))
        if len(candidates) > MAX_LOCAL_DRAFTS:
            self._failed = True
            self.set_enabled(False)
            notice('Child autosave disabled: too many local draft slots; no drafts discarded')
            return True
        current_ids = {entry.id for entry in candidates}
        self._observed = {key: value for key, value in self._observed.items() if key in current_ids}
        self._written = {key: value for key, value in self._written.items() if key in current_ids}
        # Pruning tracking never deletes a saved recovery snapshot. If a removed
        # slot reappears later, it receives a new record rather than overwriting
        # the prior disk recovery copy.
        now = time.monotonic()
        for entry in candidates:
            fingerprint = _fingerprint(entry)
            previous = self._observed.get(entry.id)
            if previous is None or previous[0] != fingerprint:
                self._observed[entry.id] = fingerprint, now
        if self.busy:
            return changed
        for entry in candidates:
            fingerprint, observed_at = self._observed[entry.id]
            written = self._written.get(entry.id)
            if written is not None and written[0] == fingerprint:
                continue
            if not flush and now - observed_at < DEBOUNCE:
                continue
            self._active = entry, fingerprint
            try:
                self.io.start('autosave', store, owner='explicit-child-autosave', entry=entry,
                              saved=written[1] if written is not None else None)
            except ValueError as exc:
                self._active = None
                self._failed = True
                self.set_enabled(False)
                notice(str(exc) + ' · child autosave disabled')
            return True
        return changed
