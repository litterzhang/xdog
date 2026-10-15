"""One execution owner, independent of terminal or transport."""

import asyncio
import threading
from concurrent.futures import Future

import pytest


def value(identifier, origin="acp"):
    from xdog.agent.session_input import SessionInput

    return SessionInput(identifier, origin, "test peer", "Review tests", (), {})


async def result(ticket):
    return await asyncio.wait_for(asyncio.wrap_future(ticket.result), 3)


async def test_control_cleanup_blocks_turn_not_input_admission(coding_session):
    from xdog.coding.core.sessions.controller import SessionController
    from xdog.coding.core.sessions.models import TurnResult

    entered, release, started = threading.Event(), threading.Event(), threading.Event()

    async def execute(item):
        started.set()
        return TurnResult("succeeded", None)

    async def control():
        try:
            return "body finished"
        finally:
            entered.set()
            await asyncio.to_thread(release.wait)

    controller = SessionController(coding_session, bound=True, execute=execute)
    controller.start()
    controller.human_input()
    try:
        action = controller.control(control, name="compact")
        assert await asyncio.to_thread(entered.wait, 2)
        ticket = controller.submit(value("r1"), peer_id="p1")
        assert isinstance(ticket.result, Future)
        await asyncio.wrap_future(ticket.accepted)
        assert not started.is_set()
        assert coding_session.messages == []
        release.set()
        await asyncio.wrap_future(action)
        assert (await result(ticket)).status == "succeeded"
    finally:
        release.set()
        await asyncio.wrap_future(controller.close())


async def test_human_priority_and_one_automatic_wake(coding_session):
    from xdog.coding.core.sessions.controller import SessionController
    from xdog.coding.core.sessions.models import TurnResult

    started = []

    async def execute(item):
        started.append(item.id)
        return TurnResult("succeeded", None)

    controller = SessionController(coding_session, bound=True, execute=execute)
    controller.start()
    try:
        controller.set_human_pending(True)
        first = controller.submit(value("a"), peer_id="p1")
        await asyncio.wrap_future(first.accepted)
        human = controller.submit(value("human", "human"), peer_id="local")
        await asyncio.wrap_future(human.accepted)
        controller.set_human_pending(False)
        assert (await result(human)).status == "succeeded"
        assert (await result(first)).status == "succeeded"
        assert started == ["human", "a"]
        second = controller.submit(value("b"), peer_id="p1")
        await asyncio.wrap_future(second.accepted)
        assert not second.result.done()
        assert controller.store.records()[-1].status == "paused"
        assert not await asyncio.wrap_future(controller.cancel(second.id, peer_id="p2"))
        assert await asyncio.wrap_future(controller.cancel(second.id, peer_id="p1"))
        assert (await result(second)).status == "cancelled"
    finally:
        await asyncio.wrap_future(controller.close())


async def test_navigation_discards_old_queued_generation(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    controller = SessionController(coding_session, bound=True)
    controller.start()
    try:
        controller.set_human_pending(True)
        ticket = controller.submit(value("r1"), peer_id="p1")
        await asyncio.wrap_future(ticket.accepted)
        controller.rotate("another-branch")
        assert (await result(ticket)).status == "discarded"
        assert coding_session.messages == []
    finally:
        await asyncio.wrap_future(controller.close())


async def test_persistence_failure_is_not_announced_as_admitted(coding_session, monkeypatch):
    from xdog.coding.core.sessions.controller import SessionController

    controller = SessionController(coding_session, bound=True)
    controller.start()
    events = []
    controller.subscribe(events.append)

    def fail(records):
        raise OSError("disk full")

    monkeypatch.setattr(controller.store, "_commit", fail)
    try:
        ticket = controller.submit(value("r1"), peer_id="p1")
        with pytest.raises(OSError, match="disk full"):
            await asyncio.wrap_future(ticket.accepted)
        with pytest.raises(OSError, match="disk full"):
            await result(ticket)
        assert not events
        assert controller.store.records() == ()
    finally:
        await asyncio.wrap_future(controller.close())


async def test_synchronous_host_owns_loop_thread_and_closes_it(coding_session):
    from xdog.coding.core.sessions.controller import SessionController

    controller = SessionController(coding_session, bound=False)
    await asyncio.to_thread(controller.start)
    try:
        ticket = controller.submit(value("threaded"), peer_id="p1")
        assert (await result(ticket)).status == "succeeded"
    finally:
        await asyncio.wrap_future(controller.close())
    await asyncio.to_thread(controller._thread.join, 2)
    assert not controller._thread.is_alive()


async def test_storage_failure_after_execution_stops_following_model_work(coding_session, monkeypatch):
    from xdog.coding.core.sessions.controller import SessionController
    from xdog.coding.core.sessions.models import TurnResult

    started = []

    async def execute(item):
        started.append(item.id)
        return TurnResult("succeeded", None)

    controller = SessionController(coding_session, bound=False, execute=execute)
    controller.start()
    commit = controller.store._commit

    def fail_terminal(records):
        if any(row.status == "succeeded" for row in records):
            raise OSError("final write failed")
        commit(records)

    monkeypatch.setattr(controller.store, "_commit", fail_terminal)
    try:
        controller.set_human_pending(True)
        a = controller.submit(value("a"), peer_id="p1")
        b = controller.submit(value("b"), peer_id="p1")
        await asyncio.wrap_future(a.accepted)
        await asyncio.wrap_future(b.accepted)
        controller.set_human_pending(False)
        assert (await result(a)).status == "failed"
        assert (await result(b)).status == "failed"
        assert started == ["a"]
    finally:
        await asyncio.wrap_future(controller.close())


async def test_pending_human_edits_moves_and_removals_change_owner_queue(coding_session):
    """Editing the display alone must not leave the original model submission runnable."""
    from xdog.coding.core.sessions.controller import SessionController
    from xdog.coding.core.sessions.models import TurnResult

    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def execute(item):
        seen.append(item.instruction)
        if item.id == 'first':
            entered.set()
            await release.wait()
        return TurnResult('succeeded', None)

    controller = SessionController(coding_session, bound=True, execute=execute)
    controller.start()
    tickets = []
    try:
        first = controller.submit(value('first', 'human'), peer_id='native')
        await entered.wait()
        for identifier in ('second', 'third', 'removed'):
            ticket = controller.submit(value(identifier, 'human'), peer_id='native')
            await asyncio.wrap_future(ticket.accepted)
            tickets.append(ticket)
        assert not await asyncio.wrap_future(controller.change_pending('second', peer_id='other', action='edit',
                                                                        content='forged'))
        assert await asyncio.wrap_future(controller.change_pending('second', peer_id='native', action='edit',
                                                                    content='edited second'))
        assert await asyncio.wrap_future(
            controller.change_pending('third', peer_id='native', action='move', direction=-1))
        assert await asyncio.wrap_future(controller.change_pending('removed', peer_id='native', action='remove'))
        release.set()
        assert (await result(first)).status == 'succeeded'
        assert [ (await result(ticket)).status for ticket in tickets] == ['succeeded', 'succeeded', 'cancelled']
        assert seen == ['Review tests', 'Review tests', 'edited second']
        edited = next(row for row in controller.store.records() if row.input.id == 'second')
        assert edited.input.instruction == 'edited second'
    finally:
        release.set()
        await asyncio.wrap_future(controller.close())


async def test_cancellation_during_admission_cannot_start_model_after_disk_commit(coding_session, monkeypatch):
    from xdog.coding.core.sessions.controller import SessionController
    from xdog.coding.core.sessions.models import TurnResult
    entered, release = threading.Event(), threading.Event()
    started = []
    async def execute(item):
        started.append(item.id)
        return TurnResult('succeeded', None)
    controller = SessionController(coding_session, bound=False, execute=execute)
    controller.start()
    commit = controller.store._commit
    def blocked(records):
        if not entered.is_set():
            entered.set()
            assert release.wait(3)
        commit(records)
    monkeypatch.setattr(controller.store, '_commit', blocked)
    try:
        ticket = controller.submit(value('cancel-before-admission', 'human'), peer_id='native')
        assert await asyncio.to_thread(entered.wait, 2)
        assert await asyncio.wrap_future(controller.cancel(ticket.id, peer_id='native'))
        release.set()
        assert (await result(ticket)).status == 'cancelled'
        assert started == []
    finally:
        release.set()
        await asyncio.wrap_future(controller.close())
