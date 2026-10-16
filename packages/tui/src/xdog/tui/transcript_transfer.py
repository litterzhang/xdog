"""Explicit snapshot copy/export/share. No upload, provider or application policy."""
from __future__ import annotations

import hashlib
import html
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from xdog.tui.transcript_browser import TranscriptBrowser, TranscriptRecord
from xdog.tui.utils import redact_sensitive_text, sanitize_terminal_text

SCOPES = frozenset({"response", "selection", "entry", "conversation", "all", "artifact"})
FORMATS = frozenset({"text", "md", "json"})


@dataclass(frozen=True, slots=True)
class TransferOptions:
    action: str
    scope: str
    format: str
    path: str = ""


def parse_transfer(action: str, args: str) -> TransferOptions:
    parts = shlex.split(args)
    if action == "copy":
        if len(parts) > 1 or parts and parts[0] not in SCOPES:
            raise ValueError("Usage: /copy [response|selection|entry|conversation|all]")
        if parts and parts[0] == "artifact":
            raise ValueError("Binary artifacts require /export PATH artifact; they are not copied/shared implicitly")
        return TransferOptions(action, parts[0] if parts else "response", "text")
    if action not in {"export", "share"}:
        raise ValueError("Unknown output action")
    path = parts.pop(0) if parts else ""
    scope = "conversation" if action == "share" else "all"
    format = "html" if action == "share" else "md"
    explicit_format = False
    for part in parts:
        if part in SCOPES:
            scope = part
        elif action == "export" and part in FORMATS:
            format = part
            explicit_format = True
        else:
            raise ValueError("Usage: /export PATH [text|md|json] [scope] or /share PATH [scope]")
    if scope == "artifact":
        if action != "export" or explicit_format and format != "json":
            raise ValueError("Binary artifact export is private JSON only; use /export PATH artifact")
        format = "json"
    return TransferOptions(action, scope, format, path)


@dataclass(frozen=True, slots=True)
class PreparedTransfer:
    id: str
    context: str
    options: TransferOptions
    text: str
    data: bytes
    sha256: str
    entries: int
    roles: tuple[str, ...]
    redactions: bool


def prepare_transfer(options: TransferOptions, records: tuple[TranscriptRecord, ...], context: str,
                     *, selection: tuple[int, int] | None = None, entry_id: str = "",
                     job_id: str = "") -> PreparedTransfer:
    if options.scope == "artifact":
        raise ValueError("Artifact export requires an owned binary snapshot, not textual transcript records")
    chosen = records
    selected_roles: set[str] | None = None
    if options.scope == "selection":
        if selection is None:
            raise ValueError("No settled transcript selection; mark lines with v/a first")
        text = TranscriptBrowser.selected_text(records, *selection)
        selected_roles = set()
        line = 0
        for record in records:
            count = len(sanitize_terminal_text(record.body).split("\n"))
            if line <= selection[1] and line + count - 1 >= selection[0]:
                selected_roles.add(record.role)
            line += count
        chosen = (TranscriptRecord("selection", "selection", "Selected source lines", text),)
    elif options.scope == "entry":
        chosen = tuple(record for record in records if record.id == entry_id)
    elif options.scope == "response":
        chosen = tuple(record for record in records if record.role == "assistant")[-1:]
    elif options.scope == "conversation":
        chosen = tuple(record for record in records if record.role in {"user", "assistant"})
    if not chosen or not any(record.body for record in chosen):
        raise ValueError("No source content available for that output scope")
    safe = tuple(TranscriptRecord(record.id, record.role, sanitize_terminal_text(record.title),
                                  sanitize_terminal_text(record.body)) for record in chosen)
    redacted = False
    if options.action == "share":
        replaced = tuple(TranscriptRecord(record.id, record.role, redact_sensitive_text(record.title),
                                          redact_sensitive_text(record.body)) for record in safe)
        redacted = replaced != safe
        safe = replaced
    if options.scope in {"response", "entry", "selection"}:
        text = safe[0].body
    else:
        text = "\n\n".join(f"[{record.role}] {record.title}\n{record.body}" for record in safe)
    if options.format == "json":
        data = (json.dumps({"schema": 1, "scope": options.scope, "records": [asdict(record) for record in safe]},
                           ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    elif options.format == "html":
        blocks = "\n".join(f"<section><h2>{html.escape(record.role)} — {html.escape(record.title)}</h2>"
                           f"<pre>{html.escape(record.body)}</pre></section>" for record in safe)
        document = ("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
                    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                    "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
                    "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
                    "<title>XDOG conversation snapshot</title><style>"
                    "body{font:16px system-ui;max-width:80ch;margin:2rem auto;padding:0 1rem}"
                    "pre{white-space:pre-wrap;overflow-wrap:anywhere;unicode-bidi:plaintext}"
                    "section{border-top:1px solid #888;padding:1rem 0}"
                    "</style><h1>Conversation snapshot</h1>"
                    "<p>Local export; no scripts, remote resources or upload. Review before distributing. "
                    "Credential redaction is heuristic, not a confidentiality guarantee.</p>" + blocks + "</html>\n")
        data = document.encode("utf-8")
    elif options.format == "md" and options.scope not in {"response", "entry", "selection"}:
        data = ("# XDOG transcript snapshot\n\n" + "\n\n".join(
            f"## {record.role}: {record.title.replace(chr(10), ' ')}\n\n{record.body}" for record in safe
        ) + "\n").encode("utf-8")
    else:
        data = text.encode("utf-8")
    return PreparedTransfer(job_id or uuid.uuid4().hex, context, options, text, data,
                            hashlib.sha256(data).hexdigest(), len(safe),
                            tuple(sorted(selected_roles if selected_roles is not None else {r.role for r in safe})),
                            redacted)


def write_snapshot(data: bytes, target: Path, canceled: threading.Event) -> None:
    """Private, exclusive atomic creation. Existing files/symlinks are never replaced."""
    target = target.expanduser()
    fd, temporary = tempfile.mkstemp(prefix=".xdog-output-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            for offset in range(0, len(data), 64 * 1024):
                if canceled.is_set():
                    raise ValueError("Output canceled before commit")
                stream.write(data[offset:offset + 64 * 1024])
            stream.flush()
            os.fsync(stream.fileno())
        if canceled.is_set():
            raise ValueError("Output canceled before commit")
        os.link(temporary, target, follow_symlinks=False)
    finally:
        os.unlink(temporary)


def clipboard_command() -> list[str] | None:
    if sys.platform != "linux":
        raise ValueError("Clipboard integration requires Linux; use local export")
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return ["wl-copy", "--type", "text/plain;charset=utf-8"]
    if os.environ.get("DISPLAY") and shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard", "-in"]
    return None


def _stop_clipboard(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=0.5)
    # A failed helper can have forked even after its parent was reaped.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def copy_snapshot(text: str, canceled: threading.Event) -> str:
    if canceled.is_set():
        raise ValueError("Copy canceled before clipboard commit")
    data = text.encode("utf-8")
    command = clipboard_command()
    if command is None:
        if canceled.is_set():
            raise ValueError("Copy canceled")
        directory = Path(tempfile.mkdtemp(prefix="xdog-copy-"))
        target = directory / "snapshot.txt"
        try:
            write_snapshot(data, target, canceled)
        except BaseException:
            directory.rmdir()
            raise
        return f"Clipboard unavailable; snapshot saved privately to {target} (delete when no longer needed)"
    with tempfile.TemporaryFile() as source:
        source.write(data)
        source.seek(0)
        if canceled.is_set():
            raise ValueError("Copy canceled before clipboard commit")
        process = subprocess.Popen(command, stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        deadline = time.monotonic() + 3
        success = False
        try:
            while process.poll() is None:
                if canceled.wait(0.01):
                    raise ValueError("Copy canceled; clipboard outcome may be uncertain")
                if time.monotonic() >= deadline:
                    raise ValueError("Clipboard write timed out; outcome uncertain, not retried")
            if process.returncode:
                raise ValueError("Clipboard helper failed; outcome uncertain, not retried")
            if canceled.is_set():
                raise ValueError("Copy canceled; clipboard outcome may be uncertain")
            success = True
        finally:
            if not success:
                _stop_clipboard(process)
    return "Snapshot copied; the OS clipboard may expose sensitive text to other applications"


class TransferWorker:
    """One explicit output operation at a time; no replay/coalescing of writes."""
    def __init__(self, publish: Callable[[dict[str, Any]], None]) -> None:
        self.publish = publish
        self._cancel = threading.Event()
        self._closed = False
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, options: TransferOptions, records: tuple[TranscriptRecord, ...], context: str,
              selection: tuple[int, int] | None, entry_id: str, job_id: str) -> bool:
        return self._start(lambda: prepare_transfer(options, records, context, selection=selection,
                                                    entry_id=entry_id, job_id=job_id), job_id)

    def start_snapshot(self, prepare: Callable[[], PreparedTransfer], job_id: str) -> bool:
        return self._start(prepare, job_id, review=True)

    def commit(self, prepared: PreparedTransfer, path: str) -> bool:
        return self._start(lambda: prepared, prepared.id, path=path)

    def _start(self, prepare: Callable[[], PreparedTransfer], job_id: str, *, path: str | None = None,
               review: bool = False) -> bool:
        if self.busy or self._closed:
            return False
        self._cancel = threading.Event()
        def run() -> None:
            try:
                prepared = prepare()
                if self._cancel.is_set():
                    raise ValueError("Output canceled")
                target = path if path is not None else prepared.options.path
                if path is None and (
                    review or prepared.options.action == "share" or not target and prepared.options.action != "copy"
                ):
                    event = {"type": "output_prepared", "id": job_id, "prepared": prepared}
                else:
                    if prepared.options.action == "copy":
                        message = copy_snapshot(prepared.text, self._cancel)
                    else:
                        write_snapshot(prepared.data, Path(target), self._cancel)
                        message = f"Saved {len(prepared.data):,} bytes to {target}; captured snapshot, no upload"
                    event = {"type": "output_done", "id": job_id, "message": message}
            except Exception as exc:
                event = {"type": "output_failed", "id": job_id, "error": str(exc)}
            if not self._closed:
                self.publish(event)
        self._thread = threading.Thread(target=run, daemon=True, name="xdog-transcript-output")
        self._thread.start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def close(self) -> None:
        self._closed = True
        self.cancel()
