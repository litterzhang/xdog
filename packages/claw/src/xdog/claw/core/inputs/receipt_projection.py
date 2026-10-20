"""Bound receipt display, not execution/history or group lifetime."""
from __future__ import annotations

MAX_RECEIPT_BYTES = 1024 * 1024


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
