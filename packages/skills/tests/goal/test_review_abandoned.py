"""An actual late independent reviewer cannot resurrect an abandoned contract."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.agent.supervision import settle_on
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.models import Owner
from xdog.skills.goal.service import GoalService
from xdog.skills.goal.store import GoalStore

from packages.skills.tests.goal.agent_host import AgentHost


@pytest.mark.parametrize("verdict", ["passed", "continue", "blocked", "failed"])
async def test_abandon_wins_actual_late_reviewer_outcome_without_implicit_cancel(tmp_path, verdict):
    entered, release = threading.Event(), threading.Event()

    class ActualHost(AgentHost):
        async def factory(self, spec, publish):
            agent = await super().factory(spec, publish)
            role, data = self.roles[spec.label]
            assert role == "reviewer"
            original = await settle_on(self.loop, self.service.review_context(data.goal_id, data.review_id))
            turn = 0

            def model(*args):
                nonlocal turn
                step = turn
                turn += 1
                self.model_calls.append((role, step))
                body = {
                    "verdict": verdict,
                    "summary": "Actual independent result",
                    "conditions": [
                        {
                            "condition": criterion,
                            "status": "passed" if verdict == "passed" else "blocked",
                            "evidence": ["result.txt"],
                            "reason": "Read actual allowed evidence",
                        }
                        for criterion in original.criteria
                    ],
                }
                message = (
                    AssistantMessage(
                        content=(
                            ToolCall(id="read", name="filesystem", arguments={"action": "read", "path": "result.txt"}),
                        )
                    )
                    if step == 0
                    else AssistantMessage(content=(TextContent(text=json.dumps(body)),))
                )

                async def generate():
                    if step:
                        entered.set()
                        while not release.is_set():
                            await asyncio.sleep(0.002)
                        if verdict == "failed":
                            raise RuntimeError("Actual reviewer model boundary failed")
                    yield DoneEvent(message=message)

                future = asyncio.get_running_loop().create_future()
                future.set_result(message)
                return EventStream.from_async_generator(generate(), result_future=future)

            agent.set_stream_fn(model)
            return agent

    (tmp_path / "result.txt").write_text("Actual reviewer evidence")
    owner = Owner("actual-abandon-review-owner", tmp_path)
    host = ActualHost(owner)
    service = GoalService(await GoalStore.open(tmp_path / "private" / "goals.sqlite3", owner), host)
    host.service = service
    try:
        goal = await service.create("Outcome", "Inspect file", ("Original criterion",), context=OperationContext(True))
        reviewing = await service.request_complete(goal.id, paths=("result.txt",))
        reference = reviewing.review.worker
        assert await asyncio.to_thread(entered.wait, 3)
        abandoned = await service.abandon(goal.id, "User no longer needs this work")
        assert abandoned.status == "abandoned" and abandoned.review.worker == reference
        assert host.supervisor.capture_source(reference.id).status == "running"
        release.set()
        await host.wait(reference)
        final = await service.reconcile(goal.id)
        assert final.status == "abandoned", "Late reviewer replaced authoritative terminal Goal state"
        assert final.review.worker == reference
        assert final.wait_reason == "User no longer needs this work"
        assert final.review.state == ("invalid" if verdict == "failed" else verdict)
        assert (await service.reconcile(goal.id)).status == "abandoned"
        assert host.supervisor.capture_source(reference.id).status != "cancelled"
    finally:
        release.set()
        await host.close()
        await service.close()


async def test_abandon_during_actual_reconcile_fingerprint_cannot_cross_final_commit_fence(tmp_path, monkeypatch):
    from xdog.skills.goal import review as module

    from packages.skills.tests.goal.test_data_pages import actual_service

    async with actual_service(tmp_path) as (service, host, human):
        (tmp_path / "result.txt").write_text("Actual independent evidence")
        goal = await service.create("Race", "Inspect file", ("Original criterion",), context=human)
        reviewing = await service.request_complete(goal.id, paths=("result.txt",))
        reference = reviewing.review.worker
        await host.wait(reference)
        entered, release = threading.Event(), threading.Event()
        original = module.fingerprint

        async def actual_fingerprint(*args, **kwargs):
            value = await original(*args, **kwargs)
            entered.set()
            assert await asyncio.to_thread(release.wait, 5)
            return value

        monkeypatch.setattr(module, "fingerprint", actual_fingerprint)
        work = asyncio.create_task(service.reconcile(goal.id))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            await service.abandon(goal.id, "Actual later user abandonment")
            release.set()
            with pytest.raises(ValueError, match="changed"):
                await work
            assert (await service.status(goal.id)).status == "abandoned"
            monkeypatch.setattr(module, "fingerprint", original)
            final = await service.reconcile(goal.id)
            assert final.status == "abandoned" and final.review.state == "passed"
            assert final.wait_reason == "Actual later user abandonment" and final.review.worker == reference
        finally:
            release.set()
            await asyncio.gather(work, return_exceptions=True)
