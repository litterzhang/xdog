"""Queued proposal completion/transport boundaries remove only original extension IDs."""
from __future__ import annotations

import asyncio

import pytest

from packages.claw.tests.test_extension_message_gateway import peer
from packages.claw.tests.test_extension_message_ui import native_ui
from packages.claw.tests.test_extension_queued_input_ui import queued_server, review


@pytest.mark.parametrize('ending', ['receipt-fail', 'withdraw-after-add', 'cancel'])
async def test_queue_result_transport_settles_before_consumption_and_never_cancels_other_inputs(
        tmp_path, monkeypatch, ending):
    async with queued_server(tmp_path, ending='receipt-fail') as (
            server, _runtime, owner, config, calls, marker, release):
        async with native_ui(config) as (ui, wait):
            ui._editor.set_text('PRIVATE QUEUE TRANSPORT DRAFT')
            ui._start_message('original user input', ())
            await wait(lambda: ui._extension_dialog_panel is not None)
            review(ui._extension_dialog_panel)
            await wait(marker.exists)
            held, settle = asyncio.Event(), asyncio.Event()
            writers = set()
            real_write, real_drain = asyncio.StreamWriter.write, asyncio.StreamWriter.drain
            def write(writer, data):
                if b'"extension_input_queued"' in data and b'"success": true' in data:
                    writers.add(writer)
                return real_write(writer, data)
            async def drain(writer):
                if writer in writers:
                    writers.discard(writer)
                    held.set()
                    await settle.wait()
                    if ending == 'receipt-fail':
                        raise ConnectionResetError('Queue receipt drain failed')
                await real_drain(writer)
            monkeypatch.setattr(asyncio.StreamWriter, 'write', write)
            monkeypatch.setattr(asyncio.StreamWriter, 'drain', drain)
            try:
                release.write_text('complete original callback')
                await wait(held.is_set)
                pending = owner.agent.pending_inputs()
                assert len(pending) == 1 and 'REVIEWED-QUEUE' in pending[0].content
                assert len(calls) == 1  # The original callback still owns the producer checkpoint.
                if ending == 'withdraw-after-add':
                    prefix = pending[0].id.split('extq-', 1)[0]
                    assert server._orchestrator.control_input('main', 'add', prefix + 'manual',
                        'MANUAL UNRELATED QUEUE', 'follow_up', images_validated=True)
                    ui._queue_request({'type': 'ping', 'extension_queued_inputs': 0})
                    await wait(lambda: all('extq-' not in row.id for row in owner.agent.pending_inputs()))
                    assert any(row.content == 'MANUAL UNRELATED QUEUE' for row in owner.agent.pending_inputs())
                elif ending == 'cancel':
                    ui._handle_escape()
                    await wait(lambda: not owner.agent.pending_inputs())
                settle.set()
                await wait(lambda: ui._active_run_id is None)
                assert 'REVIEWED-QUEUE' not in str(owner.agent.messages)
                assert 'REVIEWED-QUEUE' not in ui._editor.get_text()
                if ending == 'withdraw-after-add':
                    assert 'MANUAL UNRELATED QUEUE' in str(owner.agent.messages)
                assert not owner.agent.pending_inputs() and not ui._extension_queued_inputs
            finally:
                settle.set()
                release.write_text('cleanup')


async def test_foreign_socket_cannot_answer_original_queued_input_review(tmp_path):
    async with queued_server(tmp_path) as (_, _, owner, config, calls, _marker, _release):
        async with native_ui(config) as (ui, wait):
            ui._start_message('original turn', ())
            await wait(lambda: ui._extension_dialog_panel is not None)
            panel = ui._extension_dialog_panel
            async with peer(config) as (send, receive, _writer):
                await send({'type': 'extension_dialog_answer', 'id': panel.dialog['id'], 'value': True})
                assert (await receive('extension_dialog_ack'))['accepted'] is False
            assert len(calls) == 1 and not owner.agent.pending_inputs()
            review(panel, False)
            await wait(lambda: ui._active_run_id is None)
            assert 'REVIEWED-QUEUE' not in str(owner.agent.messages)
