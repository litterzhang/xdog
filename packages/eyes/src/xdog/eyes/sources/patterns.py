"""Small relative POSIX glob matcher with segment-aware ** semantics."""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    parts = pattern.split("/")
    pieces = []
    for index, part in enumerate(parts):
        final = index == len(parts) - 1
        if part == "**":
            pieces.append(".*" if final else "(?:[^/]+/)*")
        else:
            pieces.append("".join("[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
                                  for char in part))
            if not final:
                pieces.append("/")
    return re.compile("".join(pieces))


def matches(path: str, pattern: str) -> bool:
    return _compile(pattern).fullmatch(path) is not None
