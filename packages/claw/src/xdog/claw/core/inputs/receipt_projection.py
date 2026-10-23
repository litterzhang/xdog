"""Bound receipt display, not execution/history or group lifetime."""
from __future__ import annotations

MAX_RECEIPT_BYTES = 1024 * 1024

MAX_EXCEPTION_SUMMARY_BYTES = 8192
MAX_EXCEPTION_SUMMARY_DEPTH = 8
MAX_EXCEPTION_SUMMARY_NODES = 32
_MAX_EXCEPTION_DETAIL_BYTES = 1024
_EXCEPTION_SUMMARY_MARKER = '\n[exception summary truncated]'


def summarize_error(error: BaseException) -> str:
    """Keep ordinary errors unchanged; project group diagnostics without tracebacks.

    Preorder preserves the primary error before cleanup leaves. Bound each detail
    independently so a large primary message leaves room for cleanup diagnostics.
    Traverse only bounded depth/count; never expand causes, contexts or notes.
    Actual execution cancellation is handled by the consumer before this boundary.
    """
    if not isinstance(error, BaseExceptionGroup):
        return str(error) or type(error).__name__

    lines: list[str] = []
    remaining = MAX_EXCEPTION_SUMMARY_BYTES - len(_EXCEPTION_SUMMARY_MARKER.encode())
    visited = 0
    truncated = False

    def detail(value: str, maximum: int) -> str:
        nonlocal truncated
        # Slice before encoding: do not allocate an unbounded UTF-8 copy just
        # to discover that the message exceeds the receipt's small budget.
        data = value[:maximum].encode('utf-8', errors='replace')
        if len(value) > maximum or len(data) > maximum:
            truncated = True
        return data[:maximum].decode('utf-8', errors='ignore')

    def visit(current: BaseException, depth: int) -> bool:
        nonlocal visited, remaining, truncated
        if visited >= MAX_EXCEPTION_SUMMARY_NODES or remaining <= 1:
            truncated = True
            return False
        visited += 1
        name = detail(type(current).__name__, 128)
        # Do not invoke a group's potentially recursive/custom __str__.
        text = current.message if isinstance(current, BaseExceptionGroup) else str(current)
        line = '\n' + '  ' * depth + name + ': ' + detail(text, _MAX_EXCEPTION_DETAIL_BYTES)
        line = detail(line, remaining)
        lines.append(line)
        remaining -= len(line.encode())
        if isinstance(current, BaseExceptionGroup):
            if depth >= MAX_EXCEPTION_SUMMARY_DEPTH:
                truncated = True
            else:
                for child in current.exceptions:
                    if not visit(child, depth + 1):
                        return False
        return True

    visit(error, 0)
    return ''.join(lines).lstrip('\n') + (_EXCEPTION_SUMMARY_MARKER if truncated else '')


def _project(value: str, maximum: int, kind: str, session_id: str) -> str:
    data = value.encode('utf-8')
    if len(data) <= maximum:
        return value
    # This is explicitly a preview/reference. Full conversation history is
    # durable separately; an error preview does not claim every exception byte
    # was recorded there or that earlier effects did not occur.
    marker = (f"\n[xdog receipt projection: {kind}; original_utf8_bytes={len(data)}; "
              f"session_id={session_id or 'unavailable'}; inspect durable conversation history/effects; "
              "this receipt preview is not the full output]")
    budget = maximum - len(marker.encode())
    return data[:max(0, budget)].decode('utf-8', errors='ignore') + marker


def project(response: str, error: str, session_id: str) -> tuple[str, str]:
    if len(response.encode()) + len(error.encode()) <= MAX_RECEIPT_BYTES:
        return response, error
    error_budget = min(len(error.encode()), MAX_RECEIPT_BYTES // 2)
    response = _project(response, MAX_RECEIPT_BYTES - error_budget, 'response', session_id)
    error = _project(error, MAX_RECEIPT_BYTES - len(response.encode()), 'error', session_id)
    return response, error
