"""The actual application consumer retains the one group owner through receipts."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.consumer import InputOutcome, InputService
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.runtime.group_controller import GroupController
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput

from packages.claw.tests.eyes_helpers import ScriptedModel


async def test_original_consumer_scope_sees_committed_success_before_releasing_owner(tmp_path):
    store = await InputStore.open(tmp_path / "private" / "inputs.sqlite3")
    controller = GroupController("main", asyncio.Semaphore(1))
    observed = []

    @asynccontextmanager
    async def scope(record):
        async with controller.acquire(is_user=True, input_key=(record.envelope.message.id, record.attempt_id)):
            try:
                yield
            finally:
                saved = await store.get(record.envelope.scope, record.envelope.message.id)
                observed.append((saved.status, controller.busy))

    def admitted(record, session):
        assert record.status == "running"
        controller.confirm_input(record.envelope.message.id, record.attempt_id, session)

    async def execute(record, admit):
        assert await admit("actual-session")
        assert controller.owns_input("one", record.attempt_id)
        return InputOutcome(response="once")

    service = InputService(store, execute, execution_scope=scope, on_admitted=admitted)
    original = freeze_input(UserInput(group_id="main", content="one"), tmp_path, message_id="one")
    try:
        await service.submit(original)
        assert (await asyncio.wait_for(service.wait(original.scope, "one"), 3)).status == "succeeded"
        async with controller.acquire(is_user=True):
            assert observed == [("succeeded", True)]
    finally:
        await service.close()


async def test_cancellation_settles_interrupted_inside_original_scope_before_unlock(tmp_path):
    store = await InputStore.open(tmp_path / "private" / "inputs.sqlite3")
    controller = GroupController("main", asyncio.Semaphore(1))
    entered, committing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    seen = []
    actual_finish = store.finish

    async def gated_finish(record, status, **kwargs):
        if status == "interrupted":
            committing.set()
            await release.wait()
        return await actual_finish(record, status, **kwargs)

    store.finish = gated_finish

    @asynccontextmanager
    async def scope(record):
        async with controller.acquire(is_user=True, input_key=(record.envelope.message.id, record.attempt_id)):
            try:
                yield
            finally:
                seen.append((await store.get(record.envelope.scope, record.envelope.message.id)).status)

    async def execute(record, admit):
        assert await admit("session")
        entered.set()
        await asyncio.Event().wait()

    service = InputService(store, execute, execution_scope=scope)
    original = freeze_input(UserInput(group_id="main", content="one"), tmp_path, message_id="one")
    closing = None
    try:
        await service.submit(original)
        await asyncio.wait_for(entered.wait(), 3)
        closing = asyncio.create_task(service.close())
        await asyncio.wait_for(committing.wait(), 3)
        assert controller.busy and seen == []
        release.set()
        await asyncio.wait_for(closing, 3)
        assert seen == ["interrupted"] and not controller.busy
    finally:
        release.set()
        await service.close()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)


async def test_reserved_operation_fences_real_input_admission_while_acceptance_remains_lightweight(tmp_path):
    model = ScriptedModel()
    orch = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    orch.register_group(Group(id="main", workspace=str(tmp_path), enabled_tools=()))
    lease = orch.try_reserve_group("main")
    entered, release = asyncio.Event(), asyncio.Event()

    async def operation():
        async with orch.group_controller("main").acquire(is_user=True):
            entered.set()
            await release.wait()

    job = None
    try:
        job = orch.start_group_operation(lease, operation())
        await asyncio.wait_for(entered.wait(), 3)
        await orch.route_message(
            UserInput(group_id="main", content="original"), message_id="one", wait_for_result=False
        )
        inputs = await orch.input_service()
        record = await inputs.store.get(InputScope("main", tmp_path), "one")
        assert record.status in {"pending", "claimed"} and model.calls == []
        release.set()
        await asyncio.wait_for(job, 3)
        result = await asyncio.wait_for(inputs.wait(record.envelope.scope, "one"), 3)
        assert result.status == "succeeded" and model.calls == ["original"]
    finally:
        release.set()
        if job is not None:
            await asyncio.gather(job, return_exceptions=True)
        orch.release_group_reservation(lease)
        await orch.stop()


async def test_real_application_group_stays_owned_until_terminal_disk_commit(tmp_path, monkeypatch):
    model = ScriptedModel()
    orch = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    orch.register_group(Group(id="main", workspace=str(tmp_path), enabled_tools=()))
    inputs = await orch.input_service()
    entered, release, waiting, following = (asyncio.Event() for _ in range(4))
    actual_finish = inputs.store.finish

    async def finish(record, status, **kwargs):
        entered.set()
        await release.wait()
        return await actual_finish(record, status, **kwargs)

    monkeypatch.setattr(inputs.store, "finish", finish)
    task = asyncio.create_task(orch.route_message(UserInput(group_id="main", content="original"), message_id="one"))
    successor = None
    observed = []

    async def next_operation():
        waiting.set()
        async with orch.group_controller("main").acquire(is_user=True):
            following.set()
            observed.append((await inputs.store.get(InputScope("main", tmp_path), "one")).status)

    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert orch.group_controller("main").busy
        successor = asyncio.create_task(next_operation())
        await asyncio.wait_for(waiting.wait(), 3)
        assert not following.is_set()
        release.set()
        await asyncio.wait_for(task, 3)
        await asyncio.wait_for(successor, 3)
        assert observed == ["succeeded"] and model.calls == ["original"]
    finally:
        release.set()
        await asyncio.gather(*(item for item in (task, successor) if item is not None), return_exceptions=True)
        await orch.stop()
