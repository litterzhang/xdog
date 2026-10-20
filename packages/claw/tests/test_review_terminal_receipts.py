"""Wholebranch review: valid completed output cannot poison intake or leak withdrawn callbacks."""

from __future__ import annotations

import asyncio
import gc
import weakref

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


@pytest.mark.parametrize("text", ["x" * (1024 * 1024 + 1), "原始完整输出" * 70000], ids=["ascii", "utf8"])
async def test_large_successful_aggregate_has_truthful_receipt_complete_history_and_successor(tmp_path, text):
    calls = []

    def model(*args):
        calls.append(args)
        return EventStream.empty(
            AssistantMessage(content=(TextContent(text=text if len(calls) == 1 else "successor"),))
        )

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/large", stream_fn=model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
    try:
        result = await app.route_message(UserInput(group_id="main", content="First input"), message_id="large")
        assert result.error is None
        service = await app.input_service()
        scope = InputScope("main", app.get_group_workspace("main"))
        receipt = await service.store.get(scope, "large")
        assert receipt.status == "succeeded" and len(receipt.response.encode()) <= 1024 * 1024
        assert "receipt projection" in receipt.response and receipt.session_id in receipt.response
        rows = await asyncio.to_thread(app._runtimes["main"].transcript_store.load_transcript, receipt.session_id)
        assert rows[-1]["content"][0]["text"] == text
        assert (
            await app.route_message(UserInput(group_id="main", content="Next input"), message_id="next")
        ).error is None
        assert (await service.store.get(scope, "next")).status == "succeeded" and len(calls) == 2
    finally:
        await app.stop()


async def test_detached_queued_withdrawal_releases_actual_callback_graph_without_touching_running_owner(tmp_path):
    from packages.claw.tests.test_acp_backend import ScriptedModel

    model = ScriptedModel()
    model.release.clear()
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/withdraw", stream_fn=model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
    first = asyncio.create_task(
        app.route_message(UserInput(group_id="main", content="Hold original"), message_id="first")
    )
    queued = None

    class Callback:
        def __init__(self):
            self.attachment_graph = bytearray(256 * 1024)

        def __call__(self, event):
            pass

    try:
        await asyncio.wait_for(model.entered.wait(), 3)
        service = await app.input_service()
        scope = InputScope("main", app.get_group_workspace("main"))
        callback = Callback()
        reference = weakref.ref(callback)
        queued = asyncio.create_task(
            app.route_message(
                UserInput(group_id="main", content="Withdraw only this"),
                message_id="withdraw",
                reserved=True,
                on_display_event=callback,
            )
        )
        async with asyncio.timeout(3):
            while await service.store.get(scope, "withdraw") is None:
                await asyncio.sleep(0.002)
        call_ref = weakref.ref(app._input_calls[scope, "withdraw"])
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert app.is_group_running("main")
        await service.withdraw(scope, "withdraw")
        assert (scope, "withdraw") not in app._input_calls, (
            "Authoritative queued withdrawal retained original live call"
        )
        del callback
        gc.collect()
        assert reference() is None and call_ref() is None
        assert len(model.inputs) == 1 and app.is_group_running("main")
        model.release.set()
        await first
        await app.route_message(UserInput(group_id="main", content="Unaffected successor"), message_id="successor")
        assert len(model.inputs) == 2
    finally:
        model.release.set()
        await asyncio.gather(*(task for task in (first, queued) if task is not None), return_exceptions=True)
        await app.stop()


async def test_repeated_native_queued_abort_drops_only_cancelled_callback_records(tmp_path):
    from packages.claw.tests.test_gateway_inputs import receive, send
    from packages.claw.tests.test_native_busy_intake import application, begin, message

    async with application(tmp_path) as (server, model, endpoint):
        r1, w1 = await asyncio.open_unix_connection(endpoint)
        r2, w2 = await asyncio.open_unix_connection(endpoint)
        references = []
        try:
            await begin(r1, w1, model)
            app = server._orchestrator
            scope = InputScope("main", app.get_group_workspace("main"))
            for index in range(4):
                run = f"withdraw-{index}"
                await send(w2, message(run, "SECOND"))
                await receive(r2, "run_ack")
                references.append(weakref.ref(app._input_calls[scope, "native:" + run]))
                await send(w2, {"type": "abort", "run_id": run, "group_id": "main"})
                await receive(r2, "abort_ack")
                await receive(r2, "aborted")
                assert (scope, "native:" + run) not in app._input_calls
                assert (scope, "native:first") in app._input_calls and model.calls == ["FIRST"]
            w2.close()
            await w2.wait_closed()
            gc.collect()
            assert all(reference() is None for reference in references)
            model.gates["FIRST"][1].set()
            await receive(r1, "response")
            await send(w1, message("successor", "SECOND"))
            await receive(r1, "run_ack")
            await asyncio.wait_for(model.gates["SECOND"][0].wait(), 3)
            model.gates["SECOND"][1].set()
            await receive(r1, "response")
            assert model.calls == ["FIRST", "SECOND"]
        finally:
            w1.close()
            w2.close()
            await w1.wait_closed()
            await w2.wait_closed()


@pytest.mark.parametrize(
    "response,error",
    [("", "错误" * 400000), ("完整输出" * 140000, "完整错误" * 140000)],
    ids=["error-only", "combined"],
)
def test_combined_receipt_projection_preserves_error_and_utf8_budget(response, error):
    from xdog.claw.core.inputs.receipt_projection import project

    shown, problem = project(response, error, "actual-session")
    assert len(shown.encode()) + len(problem.encode()) <= 1024 * 1024
    assert problem and "receipt projection" in shown + problem
    assert "actual-session" in shown + problem
