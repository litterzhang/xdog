import asyncio

import pytest


@pytest.mark.parametrize('operation', ['compact', 'navigate', 'tools', 'auth', 'terminal', 'child', 'after_run'])
async def test_producer_receipt_prevents_automatic_turn_until_full_settlement(coding_session, operation):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.controller import SessionController
    controller = SessionController(coding_session, bound=True)
    controller.start()
    controller.human_input()
    try:
        controller.hold_producer(operation)
        ticket = controller.submit(SessionInput('r1', 'acp', 'peer', 'Review', (), {}), peer_id='p1')
        await asyncio.wrap_future(ticket.accepted)
        assert not ticket.result.done()
        controller.release_producer(operation)
        assert (await asyncio.wait_for(asyncio.wrap_future(ticket.result), 3)).status == 'succeeded'
    finally:
        await asyncio.wrap_future(controller.close())


async def test_control_reservation_survives_trusted_to_thread_mutation(coding_session):
    from xdog.coding.core.sessions.runtime import SessionRuntime
    runtime = SessionRuntime(coding_session, bound=False)
    runtime.start()
    async def change():
        await asyncio.to_thread(coding_session.set_thinking_level, None)
    try:
        await asyncio.wait_for(asyncio.wrap_future(runtime.controller.control(change, name='thinking')), 2)
        assert coding_session.agent.options.thinking is None
    finally:
        await asyncio.wrap_future(runtime.close())


@pytest.mark.parametrize('state', ['_compacting', '_auth_changing', '_context_changes'])
async def test_session_readiness_fence_prevents_turn_before_context_settles(coding_session, state):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.controller import SessionController
    controller = SessionController(coding_session, bound=False)
    controller.start()
    setattr(coding_session, state, True)
    try:
        ticket = controller.submit(SessionInput('r1', 'acp', 'peer', 'Review', (), {}), peer_id='p1')
        await asyncio.wrap_future(ticket.accepted)
        async def probe():
            return None
        await asyncio.wrap_future(controller.control(probe, name='readiness-probe'))
        assert not ticket.result.done()
        setattr(coding_session, state, False)
        assert (await asyncio.wait_for(asyncio.wrap_future(ticket.result), 3)).status == 'succeeded'
    finally:
        setattr(coding_session, state, False)
        await asyncio.wrap_future(controller.close())


async def test_cancelled_control_caller_retains_receipt_until_cleanup_settles(coding_session):
    from xdog.coding.core.sessions.operations import run_control
    runtime = coding_session.enable_runtime(bound=False)
    runtime.start()
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def operation():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup.set()
            await release.wait()
            return 'cancelled after durable commit'

    task = asyncio.create_task(run_control(coding_session, operation, name='commit'))
    try:
        await entered.wait()
        task.cancel()
        await asyncio.wait_for(cleanup.wait(), 2)
        await asyncio.sleep(0)
        assert not task.done(), 'cancellation is a request, not a producer settlement receipt'
        release.set()
        assert await task == 'cancelled after durable commit'
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.wrap_future(runtime.close())


async def test_shutdown_waits_for_tracked_sidechannel_cleanup(coding_session):
    from xdog.coding.core.sessions.controller import SessionController
    controller = SessionController(coding_session, bound=True)
    controller.start()
    cancelled = asyncio.Event()
    release = controller.reserve_producer('terminal', cancel=cancelled.set)
    closed = controller.close()
    try:
        await asyncio.wait_for(cancelled.wait(), 2)
        assert not closed.done(), 'owner resources must not close before callback cleanup settles'
        release()
        await asyncio.wait_for(asyncio.wrap_future(closed), 2)
        release()  # Late duplicate receipt is harmless even after owner shutdown.
    finally:
        release()
        await asyncio.wrap_future(closed)


async def test_session_control_waits_for_tracked_callback_receipt(coding_session):
    from xdog.coding.core.sessions.controller import SessionController
    controller = SessionController(coding_session, bound=True)
    controller.start()
    release = controller.reserve_producer('terminal')
    ran = []
    async def mutate():
        ran.append('mutation')
    async def probe():
        return None
    try:
        action = controller.control(mutate, name='navigation')
        await asyncio.wrap_future(controller._async(probe))
        assert not ran, 'context mutation must wait for the callback, not only the model loop'
        release()
        await asyncio.wrap_future(action)
        assert ran == ['mutation']
    finally:
        release()
        await asyncio.wrap_future(controller.close())
