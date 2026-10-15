"""Optional ACP capability detection without importing the SDK on ordinary paths."""

from __future__ import annotations

import importlib.util
import sys


def acp_available() -> bool:
    if sys.version_info >= (3, 15):
        return False
    try:
        return importlib.util.find_spec('acp') is not None
    except (ImportError, ValueError):
        return False
