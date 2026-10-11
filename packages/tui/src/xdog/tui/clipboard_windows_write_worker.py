"""Owned copy helper: text arrives on stdin, never in argv, logs or a shell command."""
from __future__ import annotations

import sys

from xdog.tui.clipboard_windows_write import MAX_COPY_BYTES, NativeWriter, unicode_payload


def main() -> int:
    try:
        data = sys.stdin.buffer.read(MAX_COPY_BYTES + 1)
        payload = unicode_payload(data)  # Validate before any native clipboard operations.
        NativeWriter().write(payload)
        return 0
    except Exception:
        return 1  # No text/exception output and no automatic retry/fallback after a write attempt.


if __name__ == '__main__':
    raise SystemExit(main())
