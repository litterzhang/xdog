"""Busy native intake is durable; controls belong to its actual admitted attempt."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.claw.tests.eyes_helpers import ScriptedModel
from packages.claw.tests.test_gateway_inputs import receive, send


@asynccontextmanager
async def application(tmp_path):
    model = ScriptedModel()
    for instruction in ("FIRST", "SECOND"):
        model.gates[instruction] = asyncio.Event(), asyncio.Event()
    config = ClawConfig(
        model="test/dummy",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "native.sock"),
        pid_file=str(tmp_path / "native.pid"),
        groups=(GroupDef(id="main", workspace=str(tmp_path / "work")),),
    )
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/dummy", model)):
        await server.start()
    try:
        yield server, model, config.socket_path
    finally:
        for entered, release in model.gates.values():
            release.set()
        await asyncio.wait_for(server.stop(), 6)


def message(run, content):
    return {"type": "message", "run_id": run, "group_id": "main", "content": content}


def steer(run):
    return {
        "type": "input_control",
        "run_id": run,
        "group_id": "main",
        "request_id": "steer",
        "input_id": "input-one",
        "action": "add",
        "content": "must not leak",
        "delivery": "steer",
        "direction": 0,
    }


async def begin(reader, writer, model):
    await send(writer, message("first", "FIRST"))
    await receive(reader, "run_ack")
    await asyncio.wait_for(model.gates["FIRST"][0].wait(), 3)


async def test_busy_native_ack_is_a_real_pending_input_not_steering_or_a_second_agent(tmp_path):
    async with application(tmp_path) as (server, model, path):
        r1, w1 = await asyncio.open_unix_connection(path)
        r2, w2 = await asyncio.open_unix_connection(path)
        try:
            await begin(r1, w1, model)
            await send(w2, message("second", "SECOND"))
            ack = await receive(r2, "run_ack")
            assert ack["message_id"] == "native:second"
            app = server._orchestrator
            inputs = await app.input_service()
            scope = InputScope("main", app.get_group_workspace("main"))
            assert (await inputs.store.get(scope, "native:second")).status == "pending"
            assert model.calls == ["FIRST"] and not app.pending_inputs("main", "")
            model.gates["FIRST"][1].set()
            await asyncio.wait_for(model.gates["SECOND"][0].wait(), 3)
            model.gates["SECOND"][1].set()
            await receive(r1, "response")
            await receive(r2, "response")
            assert model.calls == ["FIRST", "SECOND"] and model.peak == 1
            assert (await inputs.wait(scope, "native:second")).status == "succeeded"
        finally:
            w1.close()
            w2.close()
            await w1.wait_closed()
            await w2.wait_closed()


async def test_queued_peer_cannot_steer_first_and_abort_withdraws_only_its_queued_input(tmp_path):
    async with application(tmp_path) as (server, model, path):
        r1, w1 = await asyncio.open_unix_connection(path)
        r2, w2 = await asyncio.open_unix_connection(path)
        try:
            await begin(r1, w1, model)
            await send(w2, message("second", "SECOND"))
            await receive(r2, "run_ack")
            await send(w2, steer("second"))
            assert not (await receive(r2, "input_ack"))["success"]
            app = server._orchestrator
            assert not app.pending_inputs("main", "")
            await send(w2, {"type": "abort", "run_id": "second", "group_id": "main"})
            assert (await receive(r2, "abort_ack"))["status"] == "requested"
            await receive(r2, "aborted")
            inputs = await app.input_service()
            scope = InputScope("main", app.get_group_workspace("main"))
            assert (await inputs.store.get(scope, "native:second")).status == "cancelled"
            assert (await inputs.store.get(scope, "native:first")).status == "running"
            assert not app._runtimes["main"]._active_session.agent.cancellation_requested
            model.gates["FIRST"][1].set()
            await receive(r1, "response")
            assert model.calls == ["FIRST"]
        finally:
            w1.close()
            w2.close()
            await w1.wait_closed()
            await w2.wait_closed()


async def test_running_abort_joins_original_attempt_before_the_next_pending_input_runs(tmp_path):
    async with application(tmp_path) as (server, model, path):
        r1, w1 = await asyncio.open_unix_connection(path)
        r2, w2 = await asyncio.open_unix_connection(path)
        try:
            await begin(r1, w1, model)
            await send(w2, message("second", "SECOND"))
            await receive(r2, "run_ack")
            await send(w1, {"type": "abort", "run_id": "first", "group_id": "main"})
            assert (await receive(r1, "abort_ack"))["status"] == "requested"
            await receive(r1, "aborted")
            await asyncio.wait_for(model.gates["SECOND"][0].wait(), 3)
            model.gates["SECOND"][1].set()
            await receive(r2, "response")
            app = server._orchestrator
            inputs = await app.input_service()
            scope = InputScope("main", app.get_group_workspace("main"))
            assert (await inputs.store.get(scope, "native:first")).status == "interrupted"
            assert (await inputs.store.get(scope, "native:second")).status == "succeeded"
            assert model.calls == ["FIRST", "SECOND"] and model.peak == 1
        finally:
            w1.close()
            w2.close()
            await w1.wait_closed()
            await w2.wait_closed()


async def test_abort_before_commit_stops_future_submission_without_waiting_for_the_model(tmp_path, monkeypatch):
    async with application(tmp_path) as (server, model, path):
        app = server._orchestrator
        inputs = await app.input_service()
        entered, release = asyncio.Event(), asyncio.Event()
        enqueue = inputs.store.enqueue

        async def delayed_enqueue(*args, **kwargs):
            entered.set()
            await release.wait()
            return await enqueue(*args, **kwargs)

        monkeypatch.setattr(inputs.store, "enqueue", delayed_enqueue)
        reader, writer = await asyncio.open_unix_connection(path)
        try:
            await send(writer, message("never-accepted", "FIRST"))
            await asyncio.wait_for(entered.wait(), 3)
            await send(writer, {"type": "abort", "run_id": "never-accepted", "group_id": "main"})
            await receive(reader, "abort_ack")
            release.set()
            await send(writer, {"type": "ping"})
            await receive(reader, "pong")
            scope = InputScope("main", app.get_group_workspace("main"))
            row = await inputs.store.get(scope, "native:never-accepted")
            assert row is None or row.status == "cancelled"
            assert model.calls == []
        finally:
            release.set()
            writer.close()
            await writer.wait_closed()


@pytest.mark.parametrize("control", ["input", "abort"])
@pytest.mark.parametrize("next_attempt", ["successor", "retry"])
async def test_delayed_old_view_cannot_control_a_successor_or_retry_attempt(
    tmp_path, monkeypatch, control, next_attempt
):
    async with application(tmp_path) as (server, model, path):
        from xdog.ai.types import AssistantMessage

        if next_attempt == "retry":
            model.replies["FIRST"] = AssistantMessage(
                content=(), stop_reason="error", error_message="explicit retry fixture"
            )
        app = server._orchestrator
        blocked, view_release, applied = asyncio.Event(), asyncio.Event(), asyncio.Event()
        write = server._write_response

        async def hold_original_view(writer, value):
            if value.get("type") in {"response", "error"} and value.get("run_id") == "first":
                blocked.set()
                await view_release.wait()
            await write(writer, value)

        monkeypatch.setattr(server, "_write_response", hold_original_view)
        results = []
        if control == "input":
            original_control = app.control_input

            def observed(*args, **kwargs):
                result = original_control(*args, **kwargs)
                results.append(result)
                applied.set()
                return result

            monkeypatch.setattr(app, "control_input", observed)
        else:
            original_abort = app.abort_native_input

            async def observed_abort(*args, **kwargs):
                result = await original_abort(*args, **kwargs)
                results.append(result)
                applied.set()
                return result

            monkeypatch.setattr(app, "abort_native_input", observed_abort)
        r1, w1 = await asyncio.open_unix_connection(path)
        r2, w2 = await asyncio.open_unix_connection(path)
        try:
            await begin(r1, w1, model)
            if next_attempt == "successor":
                await send(w2, message("second", "SECOND"))
                await receive(r2, "run_ack")
            model.gates["FIRST"][1].set()
            await asyncio.wait_for(blocked.wait(), 3)
            if next_attempt == "retry":
                inputs = await app.input_service()
                scope = InputScope("main", app.get_group_workspace("main"))
                model.gates["FIRST"] = model.gates["SECOND"]
                model.replies.pop("FIRST", None)
                await inputs.store.retry(scope, "native:first")  # Explicit retry, not crash replay.
                inputs.wake(scope)
            await asyncio.wait_for(model.gates["SECOND"][0].wait(), 3)
            await send(
                w1, steer("first") if control == "input" else {"type": "abort", "run_id": "first", "group_id": "main"}
            )
            await asyncio.wait_for(applied.wait(), 3)
            assert results == [False]
            assert not app.pending_inputs("main", "")
            assert not app._runtimes["main"]._active_session.agent.cancellation_requested
            view_release.set()
            response = await receive(r1, "input_ack" if control == "input" else "abort_ack")
            assert not response.get("success", False) if control == "input" else response["status"] == "not_active"
            model.gates["SECOND"][1].set()
            if next_attempt == "successor":
                await receive(r2, "response")
            else:
                assert (await asyncio.wait_for(inputs.wait(scope, "native:first"), 3)).status == "succeeded"
            assert model.calls == ["FIRST", "SECOND" if next_attempt == "successor" else "FIRST"]
        finally:
            view_release.set()
            w1.close()
            w2.close()
            await w1.wait_closed()
            await w2.wait_closed()
