"""Native child queue proposals keep their original factory Agent, transport, model and draft owner."""
from __future__ import annotations

import pytest
from xdog.tui.attachments import attachment

from packages.claw.tests.test_extension_message_ui import PNG, native_ui
from packages.claw.tests.test_extension_queued_input_ui import queued_server, review


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('ending', ['apply', 'deny', 'error', 'image', 'image-denied', 'withdraw', 'old-client'])
async def test_child_original_queue_callback_has_independent_capability_and_no_parent_borrow(
        tmp_path, delivery, ending):
    async with queued_server(tmp_path, delivery, ending) as (_, _, owner, config, calls, marker, release):
        async with native_ui(config) as (ui, wait):
            assert ui._child_queued_inputs_supported
            # Child authority is not inherited from the parent active-run capability.
            ui._queue_request({'type': 'ping', 'extension_queued_inputs': 0})
            await wait(lambda: not ui._extension_queued_inputs_supported)
            assert ui._child_queued_inputs_supported
            if ending == 'old-client':
                ui._queue_request({'type': 'ping', 'child_extension_queued_inputs': 0})
                await wait(lambda: not ui._child_queued_inputs_supported)
            ui._editor.set_text('PRIVATE PARENT CHILD-QUEUE DRAFT')
            ui._editor._cursor = 4
            image = attachment(PNG, 'private-parent.png')
            ui._composer.items[image.id] = image
            ui._agents_command('start --inherit --model offline/model owned child queue')
            await wait(lambda: bool(owner.children.extensions.owners))
            child = next(iter(owner.children.extensions.owners))
            if ending != 'old-client':
                await wait(lambda: ui._extension_dialog_panel is not None)
                panel = ui._extension_dialog_panel
                source = next(item for item in ui._extension_dialog_queue if item['id'] == panel.dialog['id'])
                assert source['child_id'] == child and source['child_owner'] == ui._child_queued_inputs_owner
                assert source['_child_queue_generation'] == ui._child_queued_inputs_generation
                assert 'PRIVATE' not in str(panel.dialog) and panel.dialog['images'] == []
                assert len(calls) == 1 and not owner.agent.pending_inputs()
                review(panel, ending != 'deny')
            if ending == 'withdraw':
                await wait(marker.exists)
                assert marker.read_text() == 'True'
                old = ui._child_queued_inputs_generation
                ui._queue_request({'type': 'ping', 'child_extension_queued_inputs': 0})
                await wait(lambda: not ui._child_queued_inputs_supported)
                ui._queue_request({'type': 'ping', 'child_extension_queued_inputs': 1})
                await wait(lambda: ui._child_queued_inputs_supported and ui._child_queued_inputs_generation != old)
                release.write_text('callback finally settled')
            await wait(lambda: not owner.children.supervisor.active and not ui._children_pending)
            assert not owner.agent.messages and not owner.agent.pending_inputs()
            actual = [msg for context in calls[1:] for msg in context.messages
                      if msg.role == 'user' and 'REVIEWED-QUEUE' in str(msg.content)]
            assert bool(actual) is (ending in {'apply', 'image'})
            if actual:
                assert 'requested by extension queuedhost' in str(actual[-1].content)
                assert ('mime_type=' in str(actual[-1].content)) is (ending == 'image')
            assert ui._editor.get_text() == 'PRIVATE PARENT CHILD-QUEUE DRAFT' and ui._editor._cursor == 4
            assert ui._composer.items == {image.id: image}
            assert not ui._routed_inputs and not ui._extension_queued_inputs and not ui._child_extension_inputs


@pytest.mark.parametrize('ending', ['transport', 'withdraw'])
async def test_child_queue_complete_receipt_failure_removes_only_unconsumed_extension_input(
        tmp_path, monkeypatch, ending):
    import asyncio
    async with queued_server(tmp_path, ending='receipt-fail') as (_, _, owner, config, calls, marker, release):
        async with native_ui(config) as (ui, wait):
            ui._editor.set_text('PARENT PRIVATE NOT A CHILD QUEUE')
            ui._agents_command('start --inherit --model offline/model child queue transport')
            await wait(lambda: ui._extension_dialog_panel is not None)
            child = next(iter(owner.children.extensions.owners))
            agent = owner.children.supervisor._children[child].agent
            review(ui._extension_dialog_panel)
            await wait(marker.exists)
            held, settle = asyncio.Event(), asyncio.Event()
            writers = set()
            real_write, real_drain = asyncio.StreamWriter.write, asyncio.StreamWriter.drain
            def write(writer, data):
                if b'"type": "child_extension_input_queued"' in data:
                    writers.add(writer)
                return real_write(writer, data)
            async def drain(writer):
                if writer in writers:
                    writers.discard(writer)
                    held.set()
                    await settle.wait()
                    if ending == 'transport':
                        raise BrokenPipeError('Child queued input receipt failed after full bytes')
                await real_drain(writer)
            monkeypatch.setattr(asyncio.StreamWriter, 'write', write)
            monkeypatch.setattr(asyncio.StreamWriter, 'drain', drain)
            try:
                release.write_text('callback success')
                await wait(held.is_set)
                assert len(calls) == 1 and len(agent.pending_inputs()) == 1
                assert not owner.agent.pending_inputs()
                if ending == 'withdraw':
                    ui._queue_request({'type': 'ping', 'child_extension_queued_inputs': 0})
                    await wait(lambda: not owner.children.extensions._queued_inputs.tokens)
                settle.set()
                await wait(lambda: not owner.children.supervisor.active)
                await wait(lambda: not ui._child_extension_inputs)
                assert 'REVIEWED-QUEUE' not in str(agent.messages) and not agent.pending_inputs()
                assert ui._editor.get_text() == 'PARENT PRIVATE NOT A CHILD QUEUE'
                assert not owner.agent.messages
            finally:
                settle.set()
                release.write_text('cleanup')
