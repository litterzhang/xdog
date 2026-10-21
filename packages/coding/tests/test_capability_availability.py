"""Availability hints come from original execution owners, never a UI polling loop."""
import asyncio
import threading

from xdog.ai.utils.event_stream import EventStream

from packages.coding.tests.test_child_agents import create


async def barrier(controller):
    async def ready():
        await asyncio.sleep(0)
    await asyncio.wrap_future(controller._async(ready))


async def test_child_settlement_notifies_capabilities_on_original_parent_loop(tmp_path):
    session, _ = create(tmp_path, 'allow-all')
    release, entered = threading.Event(), threading.Event()
    records = []
    try:
        await session.send_message('Prepare original parent')
        controller, host = session._sdk_controller, session._capability_host
        original = session.agent._stream_fn
        def blocked(*args):
            source = original(*args)
            async def events():
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(.002)
                async for event in source:
                    yield event
            return EventStream.from_async_generator(events(), result_future=source._result_future)
        session.agent.set_stream_fn(blocked)
        def disconnected(_event):
            raise RuntimeError('TUI disconnected')
        session.children.runtime.publish = disconnected
        async def subscribe():
            return host.subscribe_events(lambda event: records.append((event, asyncio.get_running_loop())))
        remove = await asyncio.wrap_future(controller._async(subscribe))
        child = await session.children.runtime.start('ordinary child')
        assert await asyncio.to_thread(entered.wait, 3)
        await barrier(controller)
        records.clear()  # No main turn, control call or UI action occurs after this point.
        release.set()
        async with asyncio.timeout(3):
            while not any(event['type'] == 'owner.available'
                          and not event['snapshot']['children_active'] for event, _ in records):
                await asyncio.sleep(.005)
        assert all(loop is controller._loop for _, loop in records)
        assert all(event['generation'] == controller.generation for event, _ in records)
        assert all(event['type'] != 'execution.settled' for event, _ in records)
        assert session.children.runtime.capture_source(child).status == 'completed'
        await asyncio.wrap_future(controller._async(lambda: unsubscribe(remove)))
        records.clear()
        # A late actual lifecycle hint after detachment cannot notify old listeners.
        session.children.runtime._notify(session.children.runtime._children[child])
        await barrier(controller)
        assert not records
    finally:
        release.set()
        await session.aclose()


async def unsubscribe(remove):
    remove()


async def test_draft_clearing_notifies_once_without_synthesizing_a_completed_execution(tmp_path):
    session, _ = create(tmp_path)
    records = []
    try:
        await session.send_message('Prepare')
        controller = session._sdk_controller
        remove = controller.subscribe_execution(records.append)
        controller.set_human_pending(True)
        await barrier(controller)
        records.clear()
        controller.set_human_pending(False)
        await barrier(controller)
        assert len(records) == 1 and records[0]['type'] == 'owner.available'
        assert not records[0]['snapshot']['user_pending']
        records.clear()
        controller.set_human_pending(False)
        await barrier(controller)
        assert not records  # Rendering/polling the same blank draft is not a new signal.
        remove()
    finally:
        await session.aclose()


async def test_final_producer_release_notifies_capabilities_without_a_model_turn(tmp_path):
    session, _ = create(tmp_path)
    records = []
    try:
        await session.send_message('Prepare')
        controller = session._sdk_controller
        remove = controller.subscribe_execution(records.append)
        release = controller.reserve_producer('pending-host-operation')
        await barrier(controller)
        records.clear()
        release()
        await barrier(controller)
        assert len(records) == 1 and records[0]['type'] == 'owner.available'
        assert records[0]['snapshot']['state'] == 'idle'
        records.clear()
        release()
        await barrier(controller)
        assert not records
        controller.hold_producer('legacy-host-operation')
        await barrier(controller)
        controller.release_producer('legacy-host-operation')
        await barrier(controller)
        assert len(records) == 1 and records[0]['type'] == 'owner.available'
        remove()
    finally:
        await session.aclose()


async def test_retired_parent_does_not_receive_late_child_availability(tmp_path):
    session, _ = create(tmp_path, 'allow-all')
    release, entered = threading.Event(), threading.Event()
    records = []
    try:
        await session.send_message('Prepare original parent')
        controller, host = session._sdk_controller, session._capability_host
        original = session.agent._stream_fn
        def blocked(*args):
            source = original(*args)
            async def events():
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(.002)
                async for event in source:
                    yield event
            return EventStream.from_async_generator(events(), result_future=source._result_future)
        session.agent.set_stream_fn(blocked)
        async def subscribe():
            return host.subscribe_events(records.append)
        remove = await asyncio.wrap_future(controller._async(subscribe))
        child = await session.children.runtime.start('ordinary child')
        assert await asyncio.to_thread(entered.wait, 3)
        host.retire()
        await barrier(controller)
        records.clear()
        release.set()
        async with asyncio.timeout(3):
            while session.children.runtime.active:
                await asyncio.sleep(.005)
        await barrier(controller)
        assert not records
        assert session.children.runtime.capture_source(child).status == 'completed'
        await asyncio.wrap_future(controller._async(lambda: unsubscribe(remove)))
    finally:
        release.set()
        await session.aclose()
