"""Single owned off-UI-thread durable draft operation, with explicit receipt delivery."""
from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from xdog.tui.child_draft_store import ChildDraftStore, DraftStoreFullError, SavedChildDraft, encode_draft
from xdog.tui.components.child_draft_panel import DraftEntry


class ChildDraftIO:
    def __init__(self) -> None:
        self.failures = 0
        self._thread: threading.Thread | None = None
        self._results: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()

    @property
    def busy(self) -> bool:
        return self._thread is not None  # Until receipt is collected, not merely until the worker exits.

    def start(self, action: str, store: ChildDraftStore, *, owner: str, entry: DraftEntry | None = None,
              saved: SavedChildDraft | None = None, key: str = '') -> None:
        if self.busy:
            raise ValueError('A durable child draft operation still owns work; wait for its receipt')
        # Isolate mutable metadata before crossing threads. Byte attachments are immutable.
        if entry is not None:
            entry = replace(entry, value=replace(entry.value, config=json.loads(json.dumps(entry.value.config))))
        def work() -> None:
            result: dict[str, Any] = {'action': action, 'owner': owner}
            try:
                if action in {'save', 'autosave'} and entry is not None:
                    data = encode_draft(entry.value, label=entry.label, scope=entry.scope,
                                        action=entry.key[0], slot=entry.key[1])
                    result['saved'] = (store.update(saved.id, saved.sha256, data)
                                       if action == 'autosave' and saved is not None else store.save(data))
                elif action == 'list':
                    result['saved'] = store.list()
                elif action == 'catalog':
                    result['catalog'] = store.catalog()
                elif action == 'load':
                    result['saved'] = (store.load(key),)
                elif action == 'discard' and saved is not None:
                    store.discard(saved.id, saved.sha256)
                    result['discarded'] = saved.id
                else:
                    raise ValueError('Invalid durable child draft operation')
            except DraftStoreFullError:
                result['error'] = ('Snapshot store full (32 snapshots); no snapshot saved, overwritten or removed; '
                                   'unsaved source remains editable')
            except Exception as exc:
                # Do not disclose task/config/image data through exception diagnostics.
                result['error'] = ('Durable child draft operation failed (' + type(exc).__name__
                                   + '); effects may be uncertain, inspect saved snapshots before retrying')
            self._results.put(result)
        thread = threading.Thread(target=work, daemon=True, name='xdog-durable-child-draft')
        self._thread = thread
        try:
            thread.start()
        except RuntimeError:
            # No worker owns a write or receipt if native thread admission failed.
            self._thread = None
            raise ValueError('Durable child draft worker unavailable; no operation admitted') from None

    def poll(self, deliver: Callable[[dict[str, Any]], None]) -> bool:
        thread = self._thread
        # A receipt may already be queued while the worker is still finishing.
        # Keep its capacity lease, but never join a live thread on the UI loop.
        if thread is None or thread.is_alive():
            return False
        try:
            result = self._results.get_nowait()
        except queue.Empty:
            return False
        self._thread = None
        if result.get('error'):
            self.failures += 1
        deliver(result)
        return True
