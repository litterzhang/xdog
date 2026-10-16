"""Removed editor packets are rejected; generic invocation settlement still stays once-only."""
from __future__ import annotations

import pytest
from xdog.agent.extensions.dialogs import validate_dialog
from xdog.agent.extensions.invocation import InvocationLease


def test_proposal_codec_refuses_controls_and_oversize() -> None:

    def _check_case(bad):
        with pytest.raises(ValueError):
            validate_dialog({"kind": "editor", "id": "owned", "title": "Write", "operation": "propose", "text": bad})

    for bad in ["\x1b[2J", "\x00", "\r", "\ud800", "a" * 65537]:
        _check_case(bad)


async def test_once_only_generic_lease_discard_nested_success_and_no_retry_of_failed_commit():
    events = []
    async def commit():
        events.append('commit')
    def discard():
        events.append('discard')
    key = object()
    root = InvocationLease('extension', 'session', '', 'tools/call', lambda: True, key)
    nested = InvocationLease('extension', 'session', '', 'xdog/event', lambda: True, key, root)
    assert nested.defer(commit, discard)
    await nested.complete(True)
    assert not events
    await root.complete(False)
    await root.complete(True)
    assert events == ['discard'] and not root.defer(commit)
    fresh = InvocationLease('extension', 'session', '', 'tools/call', lambda: True, key)
    async def broken():
        events.append('uncertain')
        raise OSError('write outcome unknown')
    assert fresh.defer(broken, discard) and fresh.defer(commit, discard)
    with pytest.raises(OSError):
        await fresh.complete(True)
    await fresh.complete(True)
    assert events == ['discard', 'uncertain', 'discard']
