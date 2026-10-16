"""Linux-only clipboard selection; removed desktop helpers cannot be launched."""
from __future__ import annotations

import base64
import importlib.util
import sys
import threading

import pytest
from xdog.tui.attachments import AttachmentReader
from xdog.tui.transcript_transfer import clipboard_command, copy_snapshot

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')


def test_removed_adapter_modules_are_not_shipped():
    for module in ('clipboard_macos', 'clipboard_windows', 'clipboard_windows_worker',
                   'clipboard_windows_write', 'clipboard_windows_write_worker', 'bitmap_dib', 'dib_color_profile'):
        assert importlib.util.find_spec('xdog.tui.' + module) is None


@pytest.mark.parametrize('platform', ['darwin', 'win32', 'freebsd14'])
def test_other_platforms_refuse_before_clipboard_or_file_io(monkeypatch, platform):
    import xdog.tui.attachments as attachments
    import xdog.tui.transcript_transfer as transfer

    def unexpected(*args, **kwargs):
        pytest.fail('unsupported platform attempted clipboard or fallback I/O')

    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(attachments.shutil, 'which', unexpected)
    monkeypatch.setattr(transfer.tempfile, 'mkdtemp', unexpected)
    reader = AttachmentReader(unexpected)
    monkeypatch.setattr(reader, '_command', unexpected)
    with pytest.raises(ValueError, match='requires Linux'):
        reader.clipboard()
    with pytest.raises(ValueError, match='requires Linux'):
        copy_snapshot('private text', threading.Event())
    reader.close()


def test_linux_never_discovers_apple_helpers(tmp_path, monkeypatch):
    import xdog.tui.attachments as attachments

    looked_up = []
    def which(name):
        looked_up.append(name)
        return '/must-not-run/' + name if name in {'pbpaste', 'pbcopy', 'osascript'} else None

    monkeypatch.setenv('WAYLAND_DISPLAY', 'fixture')
    monkeypatch.setenv('DISPLAY', 'fixture')
    monkeypatch.setattr(attachments.shutil, 'which', which)
    reader = AttachmentReader(lambda event: pytest.fail('not a background read'))
    with pytest.raises(ValueError, match='wl-paste, xclip'):
        reader.clipboard()
    assert clipboard_command() is None
    assert set(looked_up) == {'wl-paste', 'xclip', 'wl-copy'}
    reader.close()


def test_linux_x11_real_helper_image_text_and_exact_copy(tmp_path, monkeypatch):
    binary = tmp_path / 'xclip'
    target = tmp_path / 'written'
    binary.write_text(f'#!{sys.executable}\nimport sys,base64\n'
                      "if '-in' in sys.argv:\n"
                      f"    open({str(target)!r}, 'wb').write(sys.stdin.buffer.read())\n"
                      "elif 'TARGETS' in sys.argv: print('image/png')\n"
                      f"else: sys.stdout.buffer.write(base64.b64decode({base64.b64encode(PNG).decode()!r}))\n")
    binary.chmod(0o700)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    monkeypatch.setenv('DISPLAY', 'controlled')
    monkeypatch.setenv('PATH', str(tmp_path))
    reader = AttachmentReader(lambda event: None)
    result = reader.clipboard()
    assert result.image is not None and result.image.data == PNG
    assert clipboard_command() == ['xclip', '-selection', 'clipboard', '-in']
    text = '多行\nexact e\u0301'
    assert 'copied' in copy_snapshot(text, threading.Event())
    assert target.read_bytes() == text.encode()
    binary.write_text(f'#!{sys.executable}\nimport sys\n'
                      "print('UTF8_STRING' if 'TARGETS' in sys.argv else 'exact text')\n")
    assert reader.clipboard().text == 'exact text\n'
    reader.close()
