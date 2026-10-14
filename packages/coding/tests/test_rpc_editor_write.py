"""Actual RPC worker/client review; only a scoped apply request, never a claimed remote mutation."""
from __future__ import annotations

import asyncio

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.coding.tests.test_rpc_extensions import close, session


async def wait(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(.003)


async def test_independent_proposal_negotiation_preserves_editor_receipts_and_invalid_batches_are_atomic(tmp_path):
    owner, _ = session(tmp_path)
    rpc = RpcExtensions(owner, lambda event: None)
    try:
        rpc.negotiate({'extension_editor': 1, 'extension_dialogs': 1})
        rpc.editor_revisions['original'] = 'a' * 64
        rpc.negotiate({'extension_editor_write': 1})
        assert rpc.editor_enabled and rpc.dialogs_enabled and rpc.editor_revisions == {'original': 'a' * 64}
        generation = rpc.editor_writes.generation
        with pytest.raises(ValueError):
            rpc.negotiate({'extension_editor_write': 0, 'extension_editor': True})
        assert rpc.editor_writes.enabled and rpc.editor_writes.generation == generation
        rpc.negotiate({'extension_editor_write': 0})
        assert rpc.editor_enabled and rpc.dialogs_enabled and rpc.editor_revisions == {'original': 'a' * 64}
    finally:
        await rpc.aclose()
        await close(owner)
