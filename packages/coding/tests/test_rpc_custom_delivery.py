"""Frame queue accounting and owned overflow cancellation, not permission changes."""
from __future__ import annotations

import asyncio

import pytest
from xdog.coding.modes.rpc.custom_delivery import CustomDelivery
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.coding.tests.test_rpc_extensions import close, session


async def test_cross_thread_delivery_count_bytes_refusal_and_success_release(monkeypatch):
    delivery = CustomDelivery(asyncio.get_running_loop())
    queued = []
    monkeypatch.setattr(delivery.loop, 'call_soon_threadsafe', lambda *args: queued.append(args))
    seen = []
    for index in range(16):
        assert delivery.schedule(seen.append, (index,), 128 * 1024)
    assert delivery.pending == 16 and delivery.bytes == 2 * 1024 * 1024
    assert not delivery.schedule(seen.append, (17,), 1)
    for callback, *args in queued:
        callback(*args)
    assert seen == list(range(16)) and delivery.pending == delivery.bytes == 0
    assert not delivery.schedule(seen.append, ('too-large',), 2 * 1024 * 1024 + 1)


async def test_failure_and_closed_loop_release_reservations(monkeypatch):
    delivery = CustomDelivery(asyncio.get_running_loop())
    def fails(*args):
        raise RuntimeError('loop closed')
    monkeypatch.setattr(delivery.loop, 'call_soon_threadsafe', fails)
    assert not delivery.schedule(lambda: None, (), 100)
    assert delivery.pending == delivery.bytes == 0
    def bad_callback():
        raise ValueError('callback failed')
    delivery.pending, delivery.bytes = 1, 100
    with pytest.raises(ValueError):
        delivery._deliver(bad_callback, (), 100)
    assert delivery.pending == delivery.bytes == 0


async def test_failure_reports_are_deduplicated_bounded_and_released(monkeypatch):
    delivery = CustomDelivery(asyncio.get_running_loop())
    queued, seen = [], []
    monkeypatch.setattr(delivery.loop, 'call_soon_threadsafe', lambda callback: queued.append(callback))
    for index in range(8):
        key = (str(index),)
        assert delivery.report_failure(key, seen.append, (index,))
        assert not delivery.report_failure(key, seen.append, (index,))
    assert not delivery.report_failure(('overflow',), seen.append, (9,))
    assert len(queued) == len(delivery.failures) == 8
    for callback in queued:
        callback()
    assert seen == list(range(8)) and not delivery.failures


async def test_stale_parent_overflow_report_does_not_touch_replacement_view(tmp_path):
    owner, _ = session(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_custom_views': 1})
        async with controller.bind('old'):
            old_scope = controller.scope
            old_generation = controller.custom_generation
        async with controller.bind('replacement'):
            frame = {'revision': 2}
            controller.custom_frames['same-id'] = frame
            controller._frame_overflow(old_scope, old_generation, 'same-id')
            assert controller.custom_frames['same-id'] is frame
            assert not any(event['type'] == 'extension_custom_error' for event in events)
            current_scope = controller.scope
            controller.negotiate({'extension_custom_views': 0})
            controller.negotiate({'extension_custom_views': 1})
            controller.custom_frames['same-id'] = frame
            controller._frame_overflow(current_scope, old_generation, 'same-id')
            assert controller.custom_frames['same-id'] is frame
            assert not any(event['type'] == 'extension_custom_error' for event in events)
    finally:
        await controller.aclose()
        await close(owner)


async def test_stale_child_overflow_report_cannot_clear_frame_after_capability_reenable(tmp_path):
    from packages.coding.tests.test_rpc_children import close as close_child
    from packages.coding.tests.test_rpc_children import setup
    owner, _, nav, sources, controller, events = setup(tmp_path)
    try:
        controller.negotiate({'child_custom_views': 1})
        generation, bound, binding = controller.custom_generation, controller.bound, controller.binding
        controller.negotiate({'child_custom_views': 0})
        controller.negotiate({'child_custom_views': 1})
        frame = {'revision': 2}
        controller.custom_frames['same-id'] = frame
        controller._frame_overflow(bound, binding, generation, 'a' * 12, 'same-id')
        assert controller.custom_frames['same-id'] is frame
        assert not any(event['type'] == 'child_extension_custom_error' for event in events)
    finally:
        await close_child(owner, nav, sources, controller)
