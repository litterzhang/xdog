"""A closing RPC peer must not own a new canceled-command delivery receipt."""
from __future__ import annotations

import asyncio

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('cancel_before_close', [False, True])
async def test_closing_controller_reaps_callback_without_waiting_for_reply_sink(
        tmp_path, monkeypatch, cancel_before_close):
    owner, _ = session(tmp_path)
    entered, stopped, release_output = asyncio.Event(), asyncio.Event(), asyncio.Event()
    delivered, applied = [], []
    sending, send_stopped = asyncio.Event(), asyncio.Event()

    async def command(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def send(event):
        delivered.append(event)
        sending.set()
        try:
            await release_output.wait()  # Models an unread preceding partial JSON frame.
        finally:
            send_stopped.set()

    async def apply(*args):
        applied.append(args)

    monkeypatch.setattr(owner, 'extension_command', command)
    controller = RpcExtensions(owner, lambda event: None, send=send)
    original = list(owner.messages)
    try:
        controller.start({'request_id': 'owned', 'session_id': owner.session_id,
                          'action': 'command', 'name': 'ext-dialogue-ask'}, turn_running=False)
        await asyncio.wait_for(entered.wait(), 5)
        controller.context_messages._pending.append(apply)
        controller.editor_writes._pending.append(({'unused': 'review'}, apply))
        assert controller.after_run_compaction.producer is None
        assert not controller.after_run_compaction.jobs
        if cancel_before_close:
            controller.cancel({'request_id': 'owned', 'session_id': owner.session_id})
            await asyncio.wait_for(sending.wait(), 1)
            assert controller.task.cancelling() > 0 and stopped.is_set()
        async with asyncio.timeout(1):
            await controller.aclose()
        assert stopped.is_set() and controller.task.done() and controller.scope is None
        assert len(delivered) == int(cancel_before_close) and not applied
        if cancel_before_close:
            assert send_stopped.is_set()
        assert not controller.context_messages._pending and not controller.editor_writes._pending
        assert owner.messages == original and owner.permissions.mode == 'deny'
    finally:
        release_output.set()
        await controller.aclose()
        await close(owner)


async def test_explicit_command_cancel_still_delivers_canceled_result_to_open_peer(tmp_path, monkeypatch):
    owner, _ = session(tmp_path)
    entered, stopped = asyncio.Event(), asyncio.Event()
    delivered = []

    async def command(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def send(event):
        delivered.append(event)

    monkeypatch.setattr(owner, 'extension_command', command)
    controller = RpcExtensions(owner, lambda event: None, send=send)
    try:
        controller.start({'request_id': 'owned', 'session_id': owner.session_id,
                          'action': 'command', 'name': 'ext-dialogue-ask'}, turn_running=False)
        await asyncio.wait_for(entered.wait(), 5)
        controller.cancel({'request_id': 'owned', 'session_id': owner.session_id})
        async with asyncio.timeout(1):
            await controller.task
        assert stopped.is_set() and not controller.closed
        assert len(delivered) == 1 and delivered[0]['type'] == 'extension_result'
        assert delivered[0]['success'] is False and delivered[0]['canceled'] is True
    finally:
        await controller.aclose()
        await close(owner)
