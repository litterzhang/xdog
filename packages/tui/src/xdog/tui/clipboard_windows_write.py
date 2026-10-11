"""One explicit Unicode clipboard commit; ownership follows Win32 SetClipboardData.

An invisible message-only window is required: EmptyClipboard after OpenClipboard
with a NULL HWND leaves no owner and prevents subsequent SetClipboardData.
"""
from __future__ import annotations

import ctypes
import sys
from typing import Any

MAX_COPY_BYTES = 32 * 1024 * 1024
PRIVACY_FORMATS = ('ExcludeClipboardContentFromMonitorProcessing',
                   'CanIncludeInClipboardHistory', 'CanUploadToCloudClipboard')


def unicode_payload(data: bytes) -> bytes:
    if len(data) > MAX_COPY_BYTES:
        raise ValueError('Clipboard copy exceeds 32 MiB; export the full snapshot instead')
    text = data.decode('utf-8')
    if '\0' in text:
        raise ValueError('Clipboard text contains NUL; export instead of truncating it')
    return text.encode('utf-16-le') + b'\0\0'


class NativeWriter:
    def __init__(self) -> None:
        loader = getattr(ctypes, 'WinDLL', None)
        if loader is None or sys.platform != 'win32':
            raise ValueError('Native Windows clipboard requires Windows')
        self.user: Any = loader('user32', use_last_error=True)
        self.kernel: Any = loader('kernel32', use_last_error=True)
        from xdog.tui.clipboard_windows import NativeClipboard
        bind = NativeClipboard._bind
        bind(self.user, 'CreateWindowExW', [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
             ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
             ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p)
        bind(self.user, 'DestroyWindow', [ctypes.c_void_p], ctypes.c_int)
        bind(self.user, 'OpenClipboard', [ctypes.c_void_p], ctypes.c_int)
        bind(self.user, 'CloseClipboard', [], ctypes.c_int)
        bind(self.user, 'EmptyClipboard', [], ctypes.c_int)
        bind(self.user, 'RegisterClipboardFormatW', [ctypes.c_wchar_p], ctypes.c_uint)
        bind(self.user, 'SetClipboardData', [ctypes.c_uint, ctypes.c_void_p], ctypes.c_void_p)
        bind(self.kernel, 'GetModuleHandleW', [ctypes.c_wchar_p], ctypes.c_void_p)
        bind(self.kernel, 'GlobalAlloc', [ctypes.c_uint, ctypes.c_size_t], ctypes.c_void_p)
        bind(self.kernel, 'GlobalLock', [ctypes.c_void_p], ctypes.c_void_p)
        bind(self.kernel, 'GlobalUnlock', [ctypes.c_void_p], ctypes.c_int)
        bind(self.kernel, 'GlobalFree', [ctypes.c_void_p], ctypes.c_void_p)

    def _allocate(self, data: bytes, owned: set[int]) -> int:
        handle = self.kernel.GlobalAlloc(0x42, len(data))  # GMEM_MOVEABLE | GMEM_ZEROINIT.
        if not handle:
            raise ValueError('Clipboard memory allocation failed')
        handle = int(handle)
        owned.add(handle)
        pointer = self.kernel.GlobalLock(handle)
        if not pointer:
            raise ValueError('Clipboard memory lock failed')
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            # False with no last error means the last lock was successfully released.
            clear = getattr(ctypes, 'set_last_error', lambda value: None)
            error = getattr(ctypes, 'get_last_error', lambda: 0)
            clear(0)
            if not self.kernel.GlobalUnlock(handle) and error():
                raise ValueError('Clipboard memory unlock failed')
        return handle

    def write(self, payload: bytes) -> None:
        owned: set[int] = set()
        window = None
        opened = False
        try:
            # Prepare ALL bytes/markers before EmptyClipboard. Preparation failure
            # does not erase the user's prior clipboard or expose unprotected text.
            entries = []
            for name in PRIVACY_FORMATS:
                format = self.user.RegisterClipboardFormatW(name)
                if not format:
                    raise ValueError('Clipboard privacy format registration failed')
                entries.append((int(format), self._allocate(b'\0' * 4, owned)))
            entries.append((13, self._allocate(payload, owned)))  # CF_UNICODETEXT.
            module = self.kernel.GetModuleHandleW(None)
            window = self.user.CreateWindowExW(0, 'STATIC', 'XDOG clipboard copy', 0, 0, 0, 0, 0,
                                               ctypes.c_void_p(-3), None, module, None)  # HWND_MESSAGE, not visible.
            if not window:
                raise ValueError('Clipboard owner window creation failed')
            if not self.user.OpenClipboard(window):
                raise ValueError('Clipboard is busy or inaccessible')
            opened = True
            if not self.user.EmptyClipboard():
                raise ValueError('Clipboard empty failed; outcome uncertain')
            for format, handle in entries:
                # Privacy markers precede text. No delayed rendering or listeners.
                if not self.user.SetClipboardData(format, handle):
                    raise ValueError('Clipboard commit failed; outcome uncertain')
                owned.remove(handle)  # The OS owns successfully transferred handles.
        finally:
            try:
                if opened and not self.user.CloseClipboard():
                    raise ValueError('Clipboard close failed; outcome uncertain')
            finally:
                try:
                    for handle in owned:
                        self.kernel.GlobalFree(handle)  # Never free transferred handles.
                finally:
                    if window and not self.user.DestroyWindow(window):
                        raise ValueError('Clipboard owner cleanup failed; outcome uncertain')
