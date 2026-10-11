"""Explicit Win32 clipboard snapshots in an owned helper; no shell/HTML/file-drop reads."""
from __future__ import annotations

import base64
import binascii
import ctypes
import json
import struct
import sys
import zlib
from typing import Any, Protocol

from xdog.tui.bitmap_dib import MAX_DIB_BYTES, dib_image
from xdog.tui.clipboard_macos import MAX_TEXT_BYTES, Command


class Backend(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def sequence(self) -> int: ...
    def format(self, name: str) -> int: ...
    def available(self, format: int) -> bool: ...
    def read(self, format: int, maximum: int) -> bytes: ...


class NativeClipboard:
    def __init__(self) -> None:
        loader = getattr(ctypes, 'WinDLL', None)
        if loader is None or sys.platform != 'win32':
            raise ValueError('Native Windows clipboard requires Windows')
        self.user: Any = loader('user32', use_last_error=True)
        self.kernel: Any = loader('kernel32', use_last_error=True)
        self._bind(self.user, 'OpenClipboard', [ctypes.c_void_p], ctypes.c_int)
        self._bind(self.user, 'CloseClipboard', [], ctypes.c_int)
        self._bind(self.user, 'GetClipboardSequenceNumber', [], ctypes.c_uint32)
        self._bind(self.user, 'RegisterClipboardFormatW', [ctypes.c_wchar_p], ctypes.c_uint)
        self._bind(self.user, 'IsClipboardFormatAvailable', [ctypes.c_uint], ctypes.c_int)
        self._bind(self.user, 'GetClipboardData', [ctypes.c_uint], ctypes.c_void_p)
        self._bind(self.kernel, 'GlobalSize', [ctypes.c_void_p], ctypes.c_size_t)
        self._bind(self.kernel, 'GlobalLock', [ctypes.c_void_p], ctypes.c_void_p)
        self._bind(self.kernel, 'GlobalUnlock', [ctypes.c_void_p], ctypes.c_int)

    @staticmethod
    def _bind(library: Any, name: str, args: list[Any], result: Any) -> None:
        function = getattr(library, name)
        function.argtypes, function.restype = args, result

    def open(self) -> None:
        if not self.user.OpenClipboard(None):
            raise ValueError('Clipboard is busy or inaccessible')

    def close(self) -> None:
        if not self.user.CloseClipboard():
            raise ValueError('Clipboard snapshot close failed')

    def sequence(self) -> int:
        return int(self.user.GetClipboardSequenceNumber())

    def format(self, name: str) -> int:
        value = int(self.user.RegisterClipboardFormatW(name))
        if not value:
            raise ValueError('Clipboard format lookup failed')
        return value

    def available(self, format: int) -> bool:
        return bool(self.user.IsClipboardFormatAvailable(format))

    def read(self, format: int, maximum: int) -> bytes:
        handle = self.user.GetClipboardData(format)
        if not handle:
            raise ValueError('Clipboard data unavailable')
        size = int(self.kernel.GlobalSize(handle))
        if not 0 < size <= maximum:
            raise ValueError('Clipboard native data exceeds its size limit')
        pointer = self.kernel.GlobalLock(handle)
        if not pointer:
            raise ValueError('Clipboard data could not be locked')
        try:
            return ctypes.string_at(pointer, size)
        finally:
            self.kernel.GlobalUnlock(handle)  # Clipboard owns the handle; never free it.


def encoded_image(data: bytes, *, maximum: int) -> bytes:
    """Trim native allocation padding using the image's own framing, not arbitrary EOI searches."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        position, first = 8, True
        while position + 12 <= len(data):
            size = struct.unpack_from('>I', data, position)[0]
            end = position + size + 12
            if end > len(data) or end > maximum:
                break
            kind = data[position + 4:position + 8]
            if first and (kind != b'IHDR' or size != 13):
                break
            first = False
            if zlib.crc32(data[position + 4:end - 4]) != struct.unpack_from('>I', data, end - 4)[0]:
                break
            if kind == b'IEND' and size == 0:
                return data[:end]
            position = end
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        end = struct.unpack_from('<I', data, 4)[0] + 8
        if 12 <= end <= min(len(data), maximum):
            return data[:end]
    elif data.startswith(b'\xff\xd8'):
        position, entropy = 2, False
        while position < min(len(data), maximum):
            if entropy:
                position = data.find(b'\xff', position, min(len(data), maximum))
                if position < 0:
                    break
            elif data[position] != 255:
                break
            while position < min(len(data), maximum) and data[position] == 255:
                position += 1
            if position >= min(len(data), maximum):
                break
            marker = data[position]
            position += 1
            if entropy and (marker == 0 or 0xd0 <= marker <= 0xd7):
                continue
            entropy = False
            if marker == 0xd9:
                return data[:position]
            if marker in (0, 0xd8) or position + 2 > min(len(data), maximum):
                break
            size = struct.unpack_from('>H', data, position)[0]
            if size < 2 or position + size > min(len(data), maximum):
                break
            position += size
            entropy = marker == 0xda
    raise ValueError('Invalid or oversized encoded clipboard image')


def snapshot(backend: Backend, *, image_limit: int) -> tuple[str, bytes]:
    backend.open()
    selected: tuple[str, bytes] | None = None
    try:
        sequence = backend.sequence()
        for name in ('PNG', 'image/png', 'image/jpeg', 'image/webp'):
            format = backend.format(name)
            if backend.available(format):
                selected = 'encoded', backend.read(format, image_limit + 4096)
                break
        if selected is None:
            for format in (17, 8):  # CF_DIBV5/CF_DIB, including Windows-synthesized CF_BITMAP data.
                if backend.available(format):
                    selected = 'dib', backend.read(format, MAX_DIB_BYTES)
                    break
        if selected is None:
            text = b''
            if backend.available(13):  # CF_UNICODETEXT: bounded, terminated UTF-16LE, never ANSI inference.
                raw = backend.read(13, 2 * MAX_TEXT_BYTES + 2)
                end = next((index for index in range(0, len(raw) - 1, 2) if raw[index:index + 2] == b'\0\0'), None)
                if end is None:
                    raise ValueError('Clipboard Unicode text is not terminated')
                text = raw[:end].decode('utf-16-le').encode('utf-8')
                if len(text) > MAX_TEXT_BYTES:
                    raise ValueError('Clipboard text exceeds its size limit')
            selected = 'text', text
        if backend.sequence() != sequence:
            raise ValueError('Clipboard changed during its snapshot')
    finally:
        backend.close()  # Release the desktop clipboard BEFORE encoding/compressing raster data.
    kind, data = selected
    if kind == 'dib':
        kind, data = 'image', dib_image(data, maximum=image_limit)
    elif kind == 'encoded':
        kind, data = 'image', encoded_image(data, maximum=image_limit)
    if backend.sequence() != sequence:
        raise ValueError('Clipboard changed during conversion')
    return kind, data


def read_windows_clipboard(command: Command, executable: str, *, image_limit: int) -> tuple[str, bytes]:
    maximum = 4 * ((max(image_limit, MAX_TEXT_BYTES) + 2) // 3) + 128
    try:
        value = json.loads(command([executable, '-m', 'xdog.tui.clipboard_windows_worker', str(image_limit)],
                                   maximum=maximum))
        if (not isinstance(value, dict) or value.keys() != {'kind', 'data'}
                or value['kind'] not in ('image', 'text') or not isinstance(value['data'], str)):
            raise ValueError
        data = base64.b64decode(value['data'], validate=True)
        limit = image_limit if value['kind'] == 'image' else MAX_TEXT_BYTES
        if len(data) > limit or value['kind'] == 'image' and not data:
            raise ValueError
        if value['kind'] == 'text':
            data.decode('utf-8')  # Validate here so malformed helper bytes never enter a UI exception message.
        return value['kind'], data
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        message = 'Windows clipboard snapshot failed or exceeded its limit; try copying again or /attach'
        raise ValueError(message) from None
