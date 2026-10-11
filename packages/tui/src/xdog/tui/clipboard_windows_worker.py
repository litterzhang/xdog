"""One explicit clipboard read; imported/executed only by the owned paste helper."""
from __future__ import annotations

import base64
import json
import sys

from xdog.tui.clipboard_windows import NativeClipboard, snapshot


def main() -> int:
    try:
        limit = int(sys.argv[1])
        if not 0 < limit <= 10 * 1024 * 1024:
            raise ValueError('Invalid clipboard image limit')
        kind, data = snapshot(NativeClipboard(), image_limit=limit)
        sys.stdout.write(json.dumps({'kind': kind, 'data': base64.b64encode(data).decode('ascii')}))
        return 0
    except Exception:
        return 1  # No clipboard data, private exception or alternate-format fallback on failure.


if __name__ == '__main__':
    raise SystemExit(main())
