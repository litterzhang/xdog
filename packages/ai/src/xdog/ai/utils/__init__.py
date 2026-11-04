"""Utility sub-package for ai.

Re-exports commonly used helpers so callers can do::

    from xdog.ai.utils import EventStream, parse_partial_json
"""

from xdog.ai.utils.event_stream import EventStream
from xdog.ai.utils.hash import sha256_hex, stable_hash
from xdog.ai.utils.json_parse import parse_partial_json
from xdog.ai.utils.overflow import estimate_token_count, is_context_overflow
from xdog.ai.utils.sanitize_unicode import sanitize_unicode
from xdog.ai.utils.validation import validate_tool_arguments, validate_tool_schema

__all__ = [
    "EventStream",
    "estimate_token_count",
    "is_context_overflow",
    "parse_partial_json",
    "sanitize_unicode",
    "sha256_hex",
    "stable_hash",
    "validate_tool_arguments",
    "validate_tool_schema",
]
