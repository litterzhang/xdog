from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time

import pytest
from xdog.tui.components.output_panel import OutputPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.transcript_actions import TranscriptActions
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.transcript_transfer import (
    copy_snapshot,
    parse_transfer,
    prepare_transfer,
    write_snapshot,
)
from xdog.tui.utils import visible_width

RECORDS = (TranscriptRecord("u", "user", "input", "password=secret-value <script>bad()</script>"),
           TranscriptRecord("a", "assistant", "answer", "exact response\n" + "x" * 30000 + "TAIL"),
           TranscriptRecord("t", "tool", "bash", "private tool path"),
           TranscriptRecord("r", "reasoning", "thinking", "private thought"))


def test_prepare_exact_response_selection_and_json_have_no_preview_caps():
    prepared = prepare_transfer(parse_transfer("copy", ""), RECORDS, "ctx")
    assert prepared.text == RECORDS[1].body
    assert prepared.text.endswith("TAIL") and len(prepared.data) > 30000
    selection = prepare_transfer(parse_transfer("copy", "selection"), RECORDS, "ctx", selection=(1, 2))
    assert selection.text == RECORDS[1].body
    assert selection.roles == ("assistant",)
    saved = prepare_transfer(parse_transfer("export", "snapshot.json json all"), RECORDS, "ctx")
    assert json.loads(saved.data)["records"][1]["body"] == RECORDS[1].body
    with pytest.raises(ValueError, match="selection"):
        prepare_transfer(parse_transfer("copy", "selection"), RECORDS, "ctx")


def test_share_default_omits_private_roles_redacts_escapes_and_has_no_upload():
    share = prepare_transfer(parse_transfer("share", "snapshot.html"), RECORDS, "ctx")
    assert share.roles == ("assistant", "user") and share.redactions
    assert all(value not in share.data for value in (b"secret-value", b"private thought", b"private tool"))
    assert b"&lt;script&gt;" in share.data and b"<script>bad" not in share.data
    assert b"default-src 'none'" in share.data and b"no scripts, remote resources or upload" in share.data
    full = prepare_transfer(parse_transfer("share", "snapshot.html all"), RECORDS, "ctx")
    assert "tool" in full.roles and "reasoning" in full.roles


def test_atomic_private_export_refuses_overwrite_symlinks_and_cancel(tmp_path):
    target = tmp_path / "snapshot.txt"
    write_snapshot(b"all data", target, threading.Event())
    assert target.read_bytes() == b"all data"
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_snapshot(b"replace", target, threading.Event())
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_snapshot(b"replace", link, threading.Event())
    assert target.read_bytes() == b"all data"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ValueError, match="canceled"):
        write_snapshot(b"not written", tmp_path / "canceled", cancel)
    assert not (tmp_path / "canceled").exists()
    assert not list(tmp_path.glob(".xdog-output-*"))


def test_real_controlled_clipboard_helper_writes_exact_utf8_off_thread(tmp_path, monkeypatch):
    helper = tmp_path / "wl-copy"
    destination = tmp_path / "clipboard"
    helper.write_text(f"#!{sys.executable}\nimport sys\n"
                      f"open({str(destination)!r},'wb').write(sys.stdin.buffer.read())\n")
    helper.chmod(0o700)
    monkeypatch.setenv("WAYLAND_DISPLAY", "fixture")
    monkeypatch.setenv("PATH", str(tmp_path))
    text = "exact e\u0301界\n" + "x" * 100000
    assert "copied" in copy_snapshot(text, threading.Event())
    assert destination.read_bytes() == text.encode()


def test_missing_clipboard_helper_uses_visible_private_file_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    import xdog.tui.transcript_transfer as module
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "private"))
    (tmp_path / "private").mkdir(mode=0o700)
    message = copy_snapshot("fallback text", threading.Event())
    assert "unavailable" in message and "snapshot.txt" in message
    assert (tmp_path / "private" / "snapshot.txt").read_text() == "fallback text"


def test_review_requires_visible_content_and_path_no_coalesced_paste_save(tmp_path):
    prepared = prepare_transfer(parse_transfer("share", str(tmp_path / "share.html")), RECORDS, "ctx")
    saved, canceled = [], []
    panel = OutputPanel(prepared, prepared.options.path, saved.append, lambda: canceled.append(True))
    panel.focused = True
    try:
        panel.set_render_budget(1)
        panel.render(16)
        panel.handle_input(KeyEvent("enter"))
        panel.handle_paste("\nenter\n")
        assert not saved
        panel.set_render_budget(12)
        deadline = time.monotonic() + 3
        while not panel._visible and time.monotonic() < deadline:
            panel.render(120)
            panel.poll()
            time.sleep(0.002)
        assert panel._visible
        panel.handle_input(KeyEvent("enter", alt=True))
        assert not saved
        panel.handle_input(KeyEvent("enter"))
        assert saved == [prepared.options.path]
        assert all(visible_width(row) <= 120 for row in panel.render(120))
    finally:
        panel.close()


def test_share_worker_has_no_write_until_confirmation_and_context_change_cancels(tmp_path):
    events, notices = [], []
    context = ["one"]
    actions = TranscriptActions(events.append, notices.append, lambda: context[0], lambda: None)
    target = tmp_path / "share.html"
    try:
        actions.start(parse_transfer("share", str(target)), RECORDS, tmp_path)
        deadline = time.monotonic() + 3
        while actions.worker.busy and time.monotonic() < deadline:
            time.sleep(0.002)
        assert not target.exists()
        actions.handle(events.pop())
        actions.poll()
        assert actions.panel is not None
        context[0] = "new"
        actions.poll()
        assert actions.panel is None and not target.exists()
        assert any("context changed" in notice for notice in notices)
    finally:
        actions.close()


def test_clipboard_owned_child_cancel_is_reaped_without_fallback_retry(tmp_path, monkeypatch):
    import xdog.tui.transcript_transfer as module

    helper = tmp_path / "wl-copy"
    helper.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    helper.chmod(0o700)
    monkeypatch.setenv("WAYLAND_DISPLAY", "fixture")
    monkeypatch.setenv("PATH", str(tmp_path))
    created = []
    original = module.subprocess.Popen
    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        created.append(process)
        return process
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    canceled = threading.Event()
    errors = []
    def copy():
        try:
            copy_snapshot("snapshot", canceled)
        except ValueError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=copy)
    worker.start()
    deadline = time.monotonic() + 3
    while not created and time.monotonic() < deadline:
        time.sleep(0.002)
    canceled.set()
    worker.join(3)
    assert not worker.is_alive() and len(created) == 1
    assert created[0].poll() is not None and "uncertain" in errors[0]


def test_export_io_error_leaves_no_partial_target(tmp_path, monkeypatch):
    import xdog.tui.transcript_transfer as module

    def fail(fd):
        raise OSError("injected fsync failure")
    monkeypatch.setattr(module.os, "fsync", fail)
    target = tmp_path / "not-created"
    with pytest.raises(OSError, match="fsync"):
        write_snapshot(b"complete snapshot", target, threading.Event())
    assert not target.exists() and not list(tmp_path.glob(".xdog-output-*"))


def test_output_path_editor_focus_cursor_blur_and_review_phase_are_owned(tmp_path):
    from xdog.tui.tui import CURSOR_MARKER
    prepared = prepare_transfer(parse_transfer('export', ''), RECORDS, 'ctx')
    panel = OutputPanel(prepared, str(tmp_path / 'export.json'), lambda _: None, lambda: None)
    try:
        panel.focused = True
        panel.set_render_budget(12)
        assert panel._path.focused and CURSOR_MARKER in '\n'.join(panel.render(120))
        panel.focused = False
        assert not panel._path.focused and CURSOR_MARKER not in '\n'.join(panel.render(120))
        panel.focused = True
        panel.handle_input(KeyEvent('enter'))
        assert not panel._editing and not panel._path.focused
        assert panel._path.get_text() == str(tmp_path / "export.json")
        assert CURSOR_MARKER not in '\n'.join(panel.render(120))
        panel.handle_input(KeyEvent('tab'))
        assert panel._editing and panel._path.focused
        assert CURSOR_MARKER in '\n'.join(panel.render(120))
    finally:
        panel.close()


def test_small_export_review_pages_every_path_field_and_invalidates_proof_on_resize_edit_or_blur(tmp_path):
    path = str(tmp_path / ('界e\u0301-' * 18 + 'last.json'))
    prepared = prepare_transfer(parse_transfer('export', path), RECORDS, 'ctx')
    saved = []
    panel = OutputPanel(prepared, path, saved.append, lambda: None)
    panel.focused = True
    panel.set_render_budget(6)
    try:
        painted = []
        deadline = time.monotonic() + 3
        while not panel._visible and time.monotonic() < deadline:
            rows = panel.render(36)
            painted.extend(rows)
            panel.poll()
            if panel._visible:
                break
            panel.handle_input(KeyEvent('enter'))
            assert not saved
            panel.handle_input(KeyEvent('pagedown'))
            time.sleep(0.003)
        assert panel._visible
        assert 'last.json' in ''.join(painted) and 'Review output' in painted[0]
        assert all(visible_width(row) <= 36 for row in painted)
        panel.handle_input(KeyEvent('v'))
        assert panel._previewing
        panel.render(36)
        panel.handle_input(KeyEvent('enter'))
        assert not saved
        panel.handle_input(KeyEvent('v'))
        deadline = time.monotonic() + 3
        while not panel._visible and time.monotonic() < deadline:
            panel.render(36)
            panel.poll()
            time.sleep(0.003)
        assert panel._visible
        panel.render(32)
        assert not panel._visible
        panel.handle_input(KeyEvent('end'))
        panel.render(32)
        assert not panel._visible  # Last page alone is not proof of the first fields.
        panel.focused = False
        panel.render(32)
        assert not panel._seen and not panel._visible
        panel.focused = True
        panel.handle_input(KeyEvent('home'))
        for _ in range(100):
            panel.render(32)
            panel.poll()
            if panel._visible:
                break
            panel.handle_input(KeyEvent('pagedown'))
        assert panel._visible
        from xdog.tui.keys import KeyEventType
        panel.handle_input(KeyEvent('tab', event_type=KeyEventType.REPEAT))
        assert not panel._editing
        panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
        assert not saved
        panel.handle_input(KeyEvent('enter'))
        assert saved == [path]
    finally:
        panel.close()
