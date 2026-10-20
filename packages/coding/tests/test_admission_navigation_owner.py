"""Navigation must settle accepted original-owner disk work before rebinding."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.agent.session_input import SessionInput
from xdog.coding.core.sessions.ownership import SessionLease
from xdog.coding.core.sessions.runtime import SessionRuntime


async def barrier(controller):
    await asyncio.wrap_future(controller._async(lambda: asyncio.sleep(0)))


@pytest.mark.parametrize('action', ['new', 'resume', 'branch'])
async def test_navigation_control_waits_for_admission_before_changing_owner(coding_session, monkeypatch, action):
    from xdog.ai.types import AssistantMessage, UserMessage
    original_messages = [UserMessage(content='original context'), AssistantMessage()]
    coding_session.agent.replace_messages(original_messages)
    coding_session._persist()
    saved_branch = coding_session.create_branch()
    target = coding_session.session_manager.create_session(model='test', working_dir=coding_session.working_dir)
    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start(setup_pending=True)
    await runtime.activate_inputs()
    controller = runtime.controller
    old_id, old_store, lease = coding_session.session_id, controller.store, coding_session._lease
    entered, release = threading.Event(), threading.Event()
    navigation_entered = asyncio.Event()
    commit = old_store._commit

    def blocked(records):
        entered.set()
        assert release.wait(8)
        return commit(records)

    monkeypatch.setattr(old_store, '_commit', blocked)
    monkeypatch.setattr(coding_session, '_resolve_selected_model', lambda _: None)
    ticket = controller.submit(SessionInput('old-admission', 'human', 'test', 'old input', (), {}), peer_id='native')
    navigation = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)

        def change():
            if action == 'new':
                coding_session.new_session()
            elif action == 'resume':
                assert coding_session.switch_session(target.session_id)
            else:
                assert coding_session.restore_branch(saved_branch)
            return coding_session.session_id

        async def navigate():
            navigation_entered.set()
            return await asyncio.to_thread(change)

        navigation = controller.control(navigate, name='new')
        await barrier(controller)
        assert not navigation_entered.is_set(), 'Navigation started before original intake commit settled'
        assert coding_session.session_id == old_id and controller.store is old_store
        with pytest.raises(RuntimeError, match='owned'):
            SessionLease.acquire(lease.directory, old_id)
        release.set()
        new_id = await asyncio.wait_for(asyncio.wrap_future(navigation), 4)
        await asyncio.wrap_future(ticket.accepted)
        result = await asyncio.wrap_future(ticket.result)
        assert result.status == 'discarded'
        assert old_store.records()[0].status == 'discarded'
        assert controller._broken is None and controller.store is not old_store
        if action == 'branch':
            assert new_id == old_id and coding_session.session_data.active_branch
            assert len(coding_session.messages) == 2
            restored = coding_session.messages[0]
            assert isinstance(restored, UserMessage)
            text = restored.content if isinstance(restored.content, str) else restored.content[0].text
            assert text == 'original context' and isinstance(coding_session.messages[1], AssistantMessage)
            assert all(row.status == 'discarded' for row in controller.store.records())
            with pytest.raises(RuntimeError, match='owned'):
                SessionLease.acquire(lease.directory, old_id)
        else:
            assert new_id != old_id and controller.store.records() == () and not coding_session.messages
            replacement = SessionLease.acquire(lease.directory, old_id)
            replacement.close()
    finally:
        release.set()
        if navigation is not None:
            await asyncio.gather(asyncio.wrap_future(navigation), return_exceptions=True)
        await asyncio.gather(asyncio.wrap_future(ticket.accepted), asyncio.wrap_future(ticket.result),
                             return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


async def test_navigation_fences_late_inputs_and_settles_admission_from_active_control(coding_session, monkeypatch):
    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start(setup_pending=True)
    await runtime.activate_inputs()
    controller = runtime.controller
    old_id, old_store, lease = coding_session.session_id, controller.store, coding_session._lease
    control_entered, navigate_now, rotated = asyncio.Event(), asyncio.Event(), asyncio.Event()
    entered, release = threading.Event(), threading.Event()
    commit, rotate = old_store._commit, controller.rotate
    loop = asyncio.get_running_loop()

    def blocked(records):
        entered.set()
        assert release.wait(8)
        return commit(records)

    def mark_rotate(branch):
        receipt = rotate(branch)
        loop.call_soon_threadsafe(rotated.set)
        return receipt

    monkeypatch.setattr(old_store, '_commit', blocked)
    monkeypatch.setattr(controller, 'rotate', mark_rotate)
    monkeypatch.setattr(coding_session, '_resolve_selected_model', lambda _: None)

    async def operation():
        control_entered.set()
        await navigate_now.wait()
        return await asyncio.to_thread(coding_session.new_session)

    navigation = controller.control(operation, name='new-after-other-control-work')
    ticket = late = None
    try:
        await asyncio.wait_for(control_entered.wait(), 3)
        ticket = controller.submit(SessionInput('original', 'human', 'test', 'original input', (), {}),
                                   peer_id='native')
        assert await asyncio.to_thread(entered.wait, 3)
        navigate_now.set()
        await asyncio.wait_for(rotated.wait(), 3)
        late = controller.submit(SessionInput('late', 'human', 'test', 'must not silently retarget', (), {}),
                                 peer_id='native')
        await barrier(controller)
        assert late.accepted.done(), 'Unbound input was admitted/deferred into a changing context'
        with pytest.raises(RuntimeError, match='context|navigation'):
            late.accepted.result()
        assert not navigation.done()
        with pytest.raises(RuntimeError, match='owned'):
            SessionLease.acquire(lease.directory, old_id)
        release.set()
        new_id = await asyncio.wait_for(asyncio.wrap_future(navigation), 4)
        await asyncio.wrap_future(ticket.accepted)
        result = await asyncio.wrap_future(ticket.result)
        assert new_id != old_id and result.status == 'discarded'
        assert old_store.records()[0].status == 'discarded'
        assert controller._broken is None and controller.store.records() == ()
    finally:
        navigate_now.set()
        release.set()
        await asyncio.gather(asyncio.wrap_future(navigation), return_exceptions=True)
        for item in (ticket, late):
            if item is not None:
                await asyncio.gather(asyncio.wrap_future(item.accepted), asyncio.wrap_future(item.result),
                                     return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


async def test_non_navigation_control_keeps_same_context_intake_available(coding_session, monkeypatch):
    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start(setup_pending=True)
    await runtime.activate_inputs()
    controller = runtime.controller
    entered, release = threading.Event(), threading.Event()
    save = coding_session.session_manager.save_session
    writes = 0

    def held_save(data, **kwargs):
        nonlocal writes
        save(data, **kwargs)
        writes += 1
        if writes == 1:
            entered.set()
            assert release.wait(8)

    monkeypatch.setattr(coding_session.session_manager, 'save_session', held_save)

    async def operation():
        await asyncio.to_thread(coding_session.set_thinking_level, None)

    action = controller.control(operation, name='thinking-off')
    ticket = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        ticket = controller.submit(SessionInput('same-context', 'human', 'test', 'queued input', (), {}),
                                   peer_id='native')
        await asyncio.wait_for(asyncio.wrap_future(ticket.accepted), 3)
        assert not ticket.result.done()
        release.set()
        await asyncio.wait_for(asyncio.wrap_future(action), 3)
        result = await asyncio.wait_for(asyncio.wrap_future(ticket.result), 3)
        assert result.status == 'succeeded' and controller._broken is None
    finally:
        release.set()
        await asyncio.gather(asyncio.wrap_future(action), return_exceptions=True)
        if ticket is not None:
            await asyncio.gather(asyncio.wrap_future(ticket.accepted), asyncio.wrap_future(ticket.result),
                                 return_exceptions=True)
        await asyncio.wrap_future(runtime.close())
