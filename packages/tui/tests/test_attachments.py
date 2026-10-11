from __future__ import annotations

import base64
import os
import sys
import time

import pytest
from xdog.tui.attachments import AttachmentComposer, AttachmentReader, attachment, read_attachment
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import visible_width

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=")


class Theme:
    accent = bold = dim = border = staticmethod(lambda value: value)


def wait(reader, events):
    deadline = time.monotonic() + 3
    while not events and time.monotonic() < deadline:
        time.sleep(0.005)
    assert events
    reader.close()
    reader._thread.join(3)
    assert not reader._thread.is_alive()


def test_file_reads_actual_png_and_rejects_wrong_type_nonregular_and_limits(tmp_path):
    path = tmp_path / "file.bin"
    path.write_bytes(PNG)
    image = read_attachment(path)
    assert image.data == PNG and image.mime_type == "image/png" and image.width == image.height == 1
    for data in (b"text", b"\x89PNG\r\n\x1a\n", b"x" * (10 * 1024 * 1024 + 1)):
        with pytest.raises(ValueError):
            attachment(data, "bad")
    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / "fifo")
        with pytest.raises(ValueError, match="regular"):
            read_attachment(tmp_path / "fifo")


def test_composer_recovery_preview_remove_and_late_read_never_submit(tmp_path):
    events, notices, changes, pasted, previews = [], [], [], [], []
    composer = AttachmentComposer(
        events.append, lambda: changes.append(True), notices.append, pasted.append, previews.append,
    )
    image = attachment(PNG, "image")
    composer.handle({"epoch": 0, "image": image, "text": ""})
    assert composer.take() == (image,)
    composer.restore((image,))
    composer.command("attachments", f"preview {image.id}", tmp_path)
    assert previews == [image]
    composer.command("attachments", f"remove {image.id}", tmp_path)
    assert not composer.items
    composer.handle({"epoch": 0, "image": image, "text": "clipboard text"})
    assert image.id in composer.items and pasted == ["clipboard text"]
    assert any("Late" in message for message in notices)
    composer.close()


def test_explicit_wayland_clipboard_image_and_text_are_read_off_thread(tmp_path, monkeypatch):
    binary = tmp_path / "wl-paste"
    binary.write_text(f"#!{sys.executable}\nimport sys,base64\n"
                      "if '--list-types' in sys.argv: print('image/png')\n"
                      f"else: sys.stdout.buffer.write(base64.b64decode({base64.b64encode(PNG).decode()!r}))\n")
    binary.chmod(0o700)
    monkeypatch.setenv("WAYLAND_DISPLAY", "controlled")
    monkeypatch.setenv("PATH", str(tmp_path))
    events = []
    reader = AttachmentReader(events.append)
    assert reader.read(None, 3)
    wait(reader, events)
    assert events[0]["epoch"] == 3 and events[0]["image"].data == PNG
    binary.write_text(f"#!{sys.executable}\nimport sys\n"
                      "print('text/plain' if '--list-types' in sys.argv else 'atomic text')\n")
    events = []
    reader = AttachmentReader(events.append)
    assert reader.read(None, 4)
    wait(reader, events)
    assert events[0]["text"] == "atomic text\n"


def test_clipboard_child_is_reaped_on_close_and_late_results_are_not_published(tmp_path, monkeypatch):
    binary = tmp_path / "wl-paste"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    binary.chmod(0o700)
    monkeypatch.setenv("WAYLAND_DISPLAY", "controlled")
    monkeypatch.setenv("PATH", str(tmp_path))
    events = []
    reader = AttachmentReader(events.append)
    reader.read(None, 0)
    deadline = time.monotonic() + 3
    while reader._process is None and time.monotonic() < deadline:
        time.sleep(0.005)
    process = reader._process
    assert process is not None
    reader.close()
    reader._thread.join(3)
    assert process.poll() is not None and not events


def test_editor_image_only_submit_and_summary_stays_within_tiny_budget():
    editor = InputPanel(Theme())
    sent = []
    editor.on_submit = sent.append
    editor.set_attachment_summary("Images: 2 · inspect /attachments")
    for height in (1, 2, 4, 8):
        editor.set_render_budget(height)
        rows = editor.render(16)
        assert len(rows) <= height
        assert all(visible_width(row) <= 16 for row in rows)
    editor.handle_input(KeyEvent("enter"))
    assert sent == [""]
    editor.set_attachment_summary("")
    editor.handle_input(KeyEvent("enter"))
    assert sent == [""]



def test_explicit_clear_fences_pending_reads_but_send_keeps_late_result_editable(tmp_path):
    notices, pasted = [], []
    composer = AttachmentComposer(lambda event: None, lambda: None, notices.append, pasted.append, lambda item: None)
    image = attachment(PNG, "pending.png")
    composer.command("attachments", "clear", tmp_path)
    composer.handle({"epoch": 0, "image": image, "text": "pending clipboard"})
    assert not composer.items and not pasted
    assert "discarded" in notices[-1]
    epoch = composer.epoch
    composer.take()
    composer.handle({"epoch": epoch, "image": image, "text": "late after send"})
    assert tuple(composer.items.values()) == (image,)
    assert pasted == ["late after send"]
    composer.close()
