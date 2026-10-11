from __future__ import annotations

import base64
import ctypes
import json
import struct
import sys
import threading
from types import SimpleNamespace

import pytest
from xdog.tui import attachments
from xdog.tui.attachments import AttachmentReader
from xdog.tui.bitmap_dib import MAX_DIB_BYTES, dib_image
from xdog.tui.clipboard_windows import MAX_TEXT_BYTES, NativeClipboard, encoded_image, read_windows_clipboard, snapshot

DIB = struct.pack('<IiiHHIIIIII', 40, 1, 1, 1, 24, 0, 4, 0, 0, 0, 0) + b'\0\0\xff\0'
PNG = dib_image(DIB, maximum=10000)
ENCODED = base64.b64encode(PNG).decode()


class Backend:
    def __init__(self, values=None, sequences=(10, 10, 10)):
        self.values = values or {}
        self.sequences = iter(sequences)
        self.calls = []
        self.opened = False

    def open(self):
        self.opened = True
        self.calls.append('open')

    def close(self):
        self.opened = False
        self.calls.append('close')

    def sequence(self):
        return next(self.sequences)

    def format(self, name):
        return name

    def available(self, format):
        return format in self.values

    def read(self, format, maximum):
        self.calls.append(('read', format, maximum))
        value = self.values[format]
        if len(value) > maximum:
            raise ValueError('native length bound')
        return value


def test_snapshot_binary_priority_allocation_padding_and_no_text_fallback():
    backend = Backend({'PNG': PNG + b'UNUSED-PRIVATE-PADDING', 17: DIB, 13: b'private text\0\0'})
    assert snapshot(backend, image_limit=1000) == ('image', PNG)
    assert backend.calls == ['open', ('read', 'PNG', 5096), 'close'] and not backend.opened
    backend = Backend({'PNG': b'PRIVATE-INVALID-PNG', 13: b'private text\0\0'})
    with pytest.raises(ValueError):
        snapshot(backend, image_limit=1000)
    assert not any(isinstance(call, tuple) and call[1] == 13 for call in backend.calls)
    assert not backend.opened


@pytest.mark.parametrize('format', [17, 8])
def test_snapshot_dib_before_text_and_conversion_after_releasing_clipboard(format, monkeypatch):
    import xdog.tui.clipboard_windows as module

    backend = Backend({format: DIB, 13: b'private text\0\0'})
    real = module.dib_image

    def convert(data, *, maximum):
        assert not backend.opened
        return real(data, maximum=maximum)

    monkeypatch.setattr(module, 'dib_image', convert)
    assert snapshot(backend, image_limit=1000) == ('image', PNG)
    assert backend.calls == ['open', ('read', format, MAX_DIB_BYTES), 'close']


@pytest.mark.parametrize('text', ['', '多行\nclipboard 🙂', 'ĀChinese一'])
def test_unicode_text_alignment_empty_and_no_html_or_file_drop_reads(text):
    backend = Backend({13: text.encode('utf-16-le') + b'\0\0' + b'UNUSED-PRIVATE-PADDING', 15: b'file-drop'})
    assert snapshot(backend, image_limit=1000) == ('text', text.encode())
    assert not backend.opened
    assert snapshot(Backend({15: b'file-drop'}), image_limit=1000) == ('text', b'')


@pytest.mark.parametrize('values,sequences', [
    ({13: b'x\0'}, (10, 10, 10)), ({13: b'\0\xd8\0\0'}, (10, 10, 10)),
    ({13: ('一' * (MAX_TEXT_BYTES // 2)).encode('utf-16-le') + b'\0\0'}, (10, 10, 10)),
    ({'PNG': PNG}, (10, 11)), ({17: DIB}, (10, 10, 11)),
])
def test_snapshot_changes_invalid_text_and_bounds_never_leave_clipboard_open(values, sequences):
    backend = Backend(values, sequences)
    with pytest.raises(ValueError):
        snapshot(backend, image_limit=1000)
    assert not backend.opened


def test_encoded_png_jpeg_and_webp_frames_exclude_native_allocation_padding():
    assert encoded_image(PNG + b'PRIVATE', maximum=1000) == PNG
    jpeg = b'\xff\xd8\xff\xe1\0\x08ab\xff\xd9cd\xff\xda\0\x02xy\xff\0z\xff\xd0q\xff\xd9'
    assert encoded_image(jpeg + b'PRIVATE', maximum=1000) == jpeg  # Thumbnail EOI is inside a sized segment.
    webp = b'RIFF' + struct.pack('<I', 4) + b'WEBP'
    assert encoded_image(webp + b'PRIVATE', maximum=1000) == webp
    for invalid in (PNG[:-1], PNG[:24] + b'bad CRC' + PNG[31:], jpeg[:-1], b'RIFF\0\0\0\0WEBP'):
        with pytest.raises(ValueError):
            encoded_image(invalid, maximum=1000)


def test_native_abi_pointer_sized_handles_lock_copy_unlock_and_clipboard_owned_memory(monkeypatch):
    import xdog.tui.clipboard_windows as module

    buffer = ctypes.create_string_buffer(PNG)
    calls = []

    class Function:
        def __init__(self, fn):
            self.fn = fn
        def __call__(self, *args):
            return self.fn(*args)

    handle = 2**40  # Must not be truncated to a 32-bit int by a missing ctypes restype.
    user = SimpleNamespace(OpenClipboard=Function(lambda hwnd: calls.append(('open', hwnd)) or 1),
        CloseClipboard=Function(lambda: calls.append(('close',)) or 1),
        GetClipboardSequenceNumber=Function(lambda: 10), RegisterClipboardFormatW=Function(lambda name: 0xc000),
        IsClipboardFormatAvailable=Function(lambda format: 1), GetClipboardData=Function(lambda format: handle))
    kernel = SimpleNamespace(GlobalSize=Function(lambda value: calls.append(('size', value)) or len(PNG)),
        GlobalLock=Function(lambda value: calls.append(('lock', value)) or ctypes.addressof(buffer)),
        GlobalUnlock=Function(lambda value: calls.append(('unlock', value)) or 0))
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **kwargs: user if name == 'user32' else kernel, raising=False)
    backend = NativeClipboard()
    assert user.GetClipboardData.restype is ctypes.c_void_p
    assert kernel.GlobalSize.restype is ctypes.c_size_t and kernel.GlobalLock.restype is ctypes.c_void_p
    assert snapshot(backend, image_limit=1000) == ('image', PNG)
    assert ('lock', handle) in calls and ('unlock', handle) in calls and calls[-1] == ('close',)
    assert not hasattr(kernel, 'GlobalFree')


def test_owned_helper_codec_uses_current_interpreter_without_shell_and_masks_malformed_outputs():
    calls = []

    def command(args, *, maximum):
        calls.append((args, maximum))
        return json.dumps({'kind': 'image', 'data': ENCODED}).encode()

    assert read_windows_clipboard(command, '/trusted/python', image_limit=1000) == ('image', PNG)
    assert calls[0][0] == ['/trusted/python', '-m', 'xdog.tui.clipboard_windows_worker', '1000']
    for reply in (b'PRIVATE', b'[]', b'{"kind":"file","data":""}', b'{"kind":"image","data":""}',
                  json.dumps({'kind': 'text', 'data': base64.b64encode(b'\xffPRIVATE').decode()}).encode()):
        with pytest.raises(ValueError) as caught:
            read_windows_clipboard(lambda args, maximum: reply, '/trusted/python', image_limit=1000)
        assert 'PRIVATE' not in str(caught.value)


def test_reader_owns_actual_helper_off_terminal_thread_without_querying_linux_clipboards(tmp_path, monkeypatch):
    helper = tmp_path / 'owned-python'
    helper.write_text(f'#!{sys.executable}\nimport sys\n'
        'assert sys.argv[1:] == ["-m", "xdog.tui.clipboard_windows_worker", "10485760"]\n'
        f'print({json.dumps({"kind": "image", "data": ENCODED})!r})\n')
    helper.chmod(0o700)
    monkeypatch.setattr(attachments, 'sys', SimpleNamespace(platform='win32', executable=str(helper)))
    monkeypatch.setenv('WAYLAND_DISPLAY', 'must-not-read')
    events, ready = [], threading.Event()

    def publish(event):
        assert threading.current_thread() is not threading.main_thread()
        events.append(event)
        ready.set()

    reader = AttachmentReader(publish)
    try:
        assert reader.read(None, 3) and ready.wait(3)
        assert events[0]['image'].data == PNG and events[0]['epoch'] == 3
    finally:
        reader.close()
        reader._thread.join(3)
        assert not reader._thread.is_alive() and reader._process is None


@pytest.mark.parametrize('mode', ['cancel', 'oversized', 'invalid'])
def test_owned_windows_helper_is_reaped_without_stale_image_or_private_output(tmp_path, monkeypatch, mode):
    import time

    helper = tmp_path / 'owned-python'
    program = ('import time\ntime.sleep(30)' if mode == 'cancel' else
               'import sys\nsys.stdout.write("x" * (15 * 1024 * 1024))' if mode == 'oversized' else
               'print("PRIVATE-WINDOWS-CLIPBOARD")')
    helper.write_text(f'#!{sys.executable}\n{program}\n')
    helper.chmod(0o700)
    monkeypatch.setattr(attachments, 'sys', SimpleNamespace(platform='win32', executable=str(helper)))
    events = []
    reader = AttachmentReader(events.append)
    try:
        assert reader.read(None, 99)
        deadline = time.monotonic() + 3
        while reader._process is None and reader._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.002)
        process = reader._process
        if mode == 'cancel':
            assert process is not None
            reader.close()
        reader._thread.join(4)
        assert not reader._thread.is_alive() and reader._process is None
        if process is not None:
            assert process.poll() is not None
        if mode == 'cancel':
            assert not events
        else:
            assert events[0]['epoch'] == 99 and 'error' in events[0]
            assert 'PRIVATE-WINDOWS-CLIPBOARD' not in str(events)
    finally:
        reader.close()


def test_real_worker_main_outputs_only_the_bounded_snapshot_and_no_exception_details(monkeypatch, capsys):
    import xdog.tui.clipboard_windows_worker as worker

    monkeypatch.setattr(worker, 'sys', SimpleNamespace(argv=['worker', '1000'], stdout=sys.stdout))
    monkeypatch.setattr(worker, 'NativeClipboard', lambda: Backend({'PNG': PNG}))
    assert worker.main() == 0
    assert json.loads(capsys.readouterr().out) == {'kind': 'image', 'data': ENCODED}
    monkeypatch.setattr(worker, 'NativeClipboard', lambda: Backend({'PNG': b'PRIVATE-WINDOWS-CLIPBOARD'}))
    assert worker.main() == 1 and capsys.readouterr().out == ''
    monkeypatch.setattr(worker, 'sys', SimpleNamespace(argv=['worker', '-1'], stdout=sys.stdout))
    assert worker.main() == 1 and capsys.readouterr().out == ''
