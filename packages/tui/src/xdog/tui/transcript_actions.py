"""UI-owned explicit output lifecycle, independent of agent/permission policy."""
from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from xdog.tui.components.output_panel import OutputPanel
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.transcript_transfer import PreparedTransfer, TransferOptions, TransferWorker


class TranscriptActions:
    def __init__(self, publish: Callable[[dict[str, Any]], None], notice: Callable[[str], None],
                 context: Callable[[], str], changed: Callable[[], None]) -> None:
        self.worker = TransferWorker(publish)
        self.notice, self.context, self.changed = notice, context, changed
        self.panel: OutputPanel | None = None
        self._prepared: PreparedTransfer | None = None
        self._job: str | None = None
        self._root = Path.cwd()

    @property
    def pending(self) -> bool:
        return self._job is not None or self.panel is not None

    def start(self, options: TransferOptions, records: tuple[TranscriptRecord, ...], root: Path,
              selection: tuple[int, int] | None = None, entry_id: str = "") -> None:
        if self.pending or self.worker.busy:
            self.notice("An output operation is pending; finish/cancel it before starting another")
            return
        self._root = root
        if options.path:
            target = Path(options.path).expanduser()
            options = replace(options, path=str(target if target.is_absolute() else root / target))
        self._job = uuid.uuid4().hex
        if not self.worker.start(options, records, self.context(), selection, entry_id, self._job):
            self._job = None
            self.notice("Output worker is closing or busy")
        else:
            self.notice(f"Preparing {options.action} from a captured {options.scope} snapshot")
        self.changed()

    def start_snapshot(self, options: TransferOptions, root: Path,
                       prepare: Callable[[TransferOptions, str, str], PreparedTransfer]) -> None:
        """Explicit typed export always reviews, including when the path is already supplied."""
        if self.pending or self.worker.busy:
            self.notice("An output operation is pending; finish/cancel it first")
            return
        self._root = root
        if options.path:
            target = Path(options.path).expanduser()
            options = replace(options, path=str(target if target.is_absolute() else root / target))
        self._job = uuid.uuid4().hex
        key, context = self._job, self.context()
        if not self.worker.start_snapshot(lambda: prepare(options, context, key), key):
            self._job = None
            self.notice("Output worker is closing or busy")
        else:
            self.notice("Preparing a lossless binary artifact for explicit private review; no upload")
        self.changed()

    def handle(self, event: Mapping[str, Any]) -> None:
        if event.get("id") != self._job:
            return
        if event["type"] == "output_prepared":
            prepared = event.get("prepared")
            if isinstance(prepared, PreparedTransfer) and prepared.context == self.context():
                self._prepared = prepared
            else:
                self.cancel("Output context changed before review; nothing saved")
        else:
            self.notice(str(event.get("message", event.get("error", "Output failed"))))
            self._job = None
            self._prepared = None
        self.changed()

    def poll(self) -> bool:
        changed = self.panel.poll() if self.panel is not None else False
        if self._prepared is not None and self._prepared.context != self.context():
            self.cancel("Output context changed; review canceled without saving")
            return True
        if self._prepared is not None and self.panel is None and not self.worker.busy:
            prepared = self._prepared
            path = prepared.options.path or str(self._root / f"transcript-{prepared.id[:8]}.{prepared.options.format}")
            self.panel = OutputPanel(prepared, path, self._confirm,
                                     lambda: self.cancel("Output canceled; nothing saved"))
            self.changed()
            return True
        return changed

    def _confirm(self, path: str) -> None:
        prepared = self._prepared
        if prepared is None or prepared.context != self.context():
            self.cancel("Output context changed; nothing saved")
            return
        target = Path(path).expanduser()
        target = target if target.is_absolute() else self._root / target
        if not self.worker.commit(prepared, str(target)):
            self.notice("Output worker still settling; confirm again after it finishes")
            self.changed()
            return
        self.panel.close() if self.panel is not None else None
        self.panel = None
        self._prepared = None
        self.notice("Saving the reviewed immutable snapshot; no upload")
        self.changed()

    def cancel(self, message: str = "Output canceled; an already committed change cannot be undone") -> None:
        self.worker.cancel()
        if self.panel is not None:
            self.panel.close()
        self.panel = None
        self._prepared = None
        self._job = None
        self.notice(message)
        self.changed()

    def close(self) -> None:
        self.worker.close()
        if self.panel is not None:
            self.panel.close()
        self.panel = None
        self._prepared = None
        self._job = None
