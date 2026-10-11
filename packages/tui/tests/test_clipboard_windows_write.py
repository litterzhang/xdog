from __future__ import annotations

import ctypes
import io
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from xdog.tui.clipboard_windows_write import MAX_COPY_BYTES, PRIVACY_FORMATS, NativeWriter, unicode_payload
from xdog.tui.transcript_transfer import clipboard_command, copy_snapshot


class Function:
    def __init__(self, fn):
        self.fn = fn
    def __call__(self, *args):
        return self.fn(*args)


class NativePeer:
    def __init__(self, failure=''):
        self.failure, self.calls, self.buffers, self.freed, self.transferred = failure, [], {}, [], []
        self.last_error = 0
        self.window = 2**40 + 99
        self.user = SimpleNamespace(
            CreateWindowExW=Function(self.window_create), DestroyWindow=Function(self.window_destroy),
            OpenClipboard=Function(self.open), CloseClipboard=Function(self.close), EmptyClipboard=Function(self.empty),
            RegisterClipboardFormatW=Function(self.format), SetClipboardData=Function(self.set))
        self.kernel = SimpleNamespace(GetModuleHandleW=Function(lambda name: 2**40 + 77),
            GlobalAlloc=Function(self.alloc), GlobalLock=Function(self.lock),
            GlobalUnlock=Function(self.unlock), GlobalFree=Function(self.free))

    def window_create(self, *args):
        self.calls.append(('window', args))
        assert args[1] == 'STATIC' and args[3] == 0 and args[8].value == ctypes.c_void_p(-3).value
        return 0 if self.failure == 'window' else self.window

    def window_destroy(self, window):
        self.calls.append(('destroy', window))
        return self.failure != 'destroy'

    def open(self, window):
        assert window == self.window  # NULL HWND would fail after EmptyClipboard on real Windows.
        self.calls.append(('open', window))
        return self.failure != 'open'

    def close(self):
        self.calls.append(('close',))
        return self.failure != 'close'

    def empty(self):
        self.calls.append(('empty',))
        assert len(self.buffers) == 4  # All allocations/privacy marker lookup precede destruction.
        return self.failure != 'empty'

    def format(self, name):
        self.calls.append(('format', name))
        return 0 if self.failure == 'format' else 0xc000 + PRIVACY_FORMATS.index(name)

    def alloc(self, flags, size):
        assert flags == 0x42
        if self.failure == 'alloc':
            return 0
        handle = 2**40 + len(self.buffers) + 1
        self.buffers[handle] = ctypes.create_string_buffer(size)
        self.calls.append(('alloc', handle))
        return handle

    def lock(self, handle):
        self.calls.append(('lock', handle))
        return 0 if self.failure == 'lock' else ctypes.addressof(self.buffers[handle])

    def unlock(self, handle):
        self.calls.append(('unlock', handle))
        self.last_error = 5 if self.failure == 'unlock' else 0
        return 0  # The documented successful last-unlock result is false with last_error=0.

    def free(self, handle):
        assert handle not in self.transferred
        self.calls.append(('free', handle))
        self.freed.append(handle)
        return 0

    def set(self, format, handle):
        self.calls.append(('set', format, handle))
        if self.failure == 'set' and format == 13:
            return 0
        self.transferred.append(handle)
        return handle


def install(monkeypatch, failure=''):
    import xdog.tui.clipboard_windows_write as module

    peer = NativePeer(failure)
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **kwargs: peer.user if name == 'user32' else peer.kernel,
                        raising=False)
    monkeypatch.setattr(ctypes, 'set_last_error', lambda value: setattr(peer, 'last_error', value), raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: peer.last_error, raising=False)
    return peer, NativeWriter()


def test_native_unicode_privacy_markers_owner_window_and_transferred_handles(monkeypatch):
    peer, writer = install(monkeypatch)
    text = 'exact e\u0301界\n🙂'
    writer.write(unicode_payload(text.encode()))
    sets = [call for call in peer.calls if call[0] == 'set']
    assert [call[1] for call in sets] == [0xc000, 0xc001, 0xc002, 13]
    assert all(peer.buffers[call[2]].raw == b'\0' * 4 for call in sets[:3])
    assert peer.buffers[sets[-1][2]].raw == text.encode('utf-16-le') + b'\0\0'
    assert not peer.freed and len(peer.transferred) == 4
    assert peer.calls[-2:] == [('close',), ('destroy', peer.window)]
    assert peer.user.SetClipboardData.restype is ctypes.c_void_p
    assert peer.kernel.GlobalAlloc.restype is ctypes.c_void_p
    assert peer.user.CreateWindowExW.restype is ctypes.c_void_p


@pytest.mark.parametrize('failure', ['format', 'alloc', 'lock', 'unlock', 'window', 'open', 'empty', 'set',
                                    'close', 'destroy'])
def test_failures_free_only_untransferred_memory_and_close_owned_resources(monkeypatch, failure):
    peer, writer = install(monkeypatch, failure)
    with pytest.raises(ValueError):
        writer.write(unicode_payload(b'PRIVATE-SNAPSHOT'))
    assert set(peer.freed) == set(peer.buffers) - set(peer.transferred)
    if failure in {'format', 'alloc', 'lock', 'unlock', 'window', 'open'}:
        assert not any(call[0] == 'empty' for call in peer.calls)
    if failure in {'empty', 'set', 'close', 'destroy'}:
        assert any(call[0] == 'close' for call in peer.calls)
    if failure not in {'format', 'alloc', 'lock', 'unlock', 'window'}:
        assert ('destroy', peer.window) in peer.calls
    assert sum(call[0] == 'empty' for call in peer.calls) <= 1  # No uncertain commit replay.


def test_unicode_validation_before_native_work_and_actual_worker_keeps_stdin_private(monkeypatch, capsys):
    import xdog.tui.clipboard_windows_write_worker as worker

    captured = []
    class Writer:
        def write(self, payload):
            captured.append(payload)
    monkeypatch.setattr(worker, 'NativeWriter', Writer)
    for data, code in [(b'exact\ntext', 0), (b'PRIVATE\0SNAPSHOT', 1), (b'\xffPRIVATE', 1)]:
        monkeypatch.setattr(worker, 'sys', SimpleNamespace(stdin=SimpleNamespace(buffer=io.BytesIO(data))))
        assert worker.main() == code
        assert not capsys.readouterr().out
    assert captured == [b'e\0x\0a\0c\0t\0\n\0t\0e\0x\0t\0\0\0']
    with pytest.raises(ValueError, match='32 MiB'):
        unicode_payload(b'x' * (MAX_COPY_BYTES + 1))


def helper(tmp_path, monkeypatch, body):
    import xdog.tui.transcript_transfer as module

    binary = tmp_path / 'owned-win-copy'
    binary.write_text(f'#!{sys.executable}\nimport sys\n'
                     'assert sys.argv[1:] == ["-m", "xdog.tui.clipboard_windows_write_worker"]\n' + body + '\n')
    binary.chmod(0o700)
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32', executable=str(binary)))
    monkeypatch.setenv('WAYLAND_DISPLAY', 'must-not-query-another-clipboard')
    monkeypatch.setenv('DISPLAY', 'must-not-query-another-clipboard')
    return binary


def test_actual_owned_copy_helper_exact_utf8_stdin_and_no_sensitive_argv(tmp_path, monkeypatch):
    destination = tmp_path / 'copied'
    binary = helper(tmp_path, monkeypatch, f'open({str(destination)!r}, "wb").write(sys.stdin.buffer.read())')
    text = '多行 e\u0301\n🙂 ' + 'x' * 100000
    assert clipboard_command() == [str(binary), '-m', 'xdog.tui.clipboard_windows_write_worker']
    assert 'copied' in copy_snapshot(text, threading.Event())
    assert destination.read_bytes() == text.encode()


def test_canceled_or_invalid_copy_does_not_launch_helper_or_touch_old_clipboard(tmp_path, monkeypatch):
    destination = tmp_path / 'must-not-exist'
    helper(tmp_path, monkeypatch, f'open({str(destination)!r}, "wb").write(b"changed")')
    canceled = threading.Event()
    canceled.set()
    with pytest.raises(ValueError, match='before clipboard'):
        copy_snapshot('snapshot', canceled)
    with pytest.raises(ValueError, match='NUL'):
        copy_snapshot('private\0snapshot', threading.Event())
    assert not destination.exists()


@pytest.mark.parametrize('mode', ['failed', 'cancel'])
def test_uncertain_native_helper_write_is_not_replayed_or_replaced_with_file_fallback(tmp_path, monkeypatch, mode):
    import xdog.tui.transcript_transfer as module

    helper(tmp_path, monkeypatch, 'raise SystemExit(1)' if mode == 'failed' else 'import time\ntime.sleep(30)')
    created, errors = [], []
    original = module.subprocess.Popen
    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        created.append(process)
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', popen)
    monkeypatch.setattr(module.tempfile, 'mkdtemp', lambda **kwargs: pytest.fail('No fallback after write attempt'))
    canceled = threading.Event()
    def copy():
        try:
            copy_snapshot('PRIVATE-SNAPSHOT', canceled)
        except ValueError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=copy)
    thread.start()
    deadline = time.monotonic() + 3
    while not created and time.monotonic() < deadline:
        time.sleep(0.002)
    if mode == 'cancel':
        canceled.set()
    thread.join(4)
    assert not thread.is_alive() and len(created) == 1 and created[0].poll() is not None
    assert 'uncertain' in errors[0] and 'PRIVATE-SNAPSHOT' not in errors[0]
