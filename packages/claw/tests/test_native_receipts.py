"""Native ACK-loss recovery reads authoritative receipts, never replays turns."""

from __future__ import annotations

import asyncio

import pytest
from xdog.claw.core.inputs.models import InputScope

from packages.claw.tests.test_gateway_inputs import gateway as gateway
from packages.claw.tests.test_gateway_inputs import receive, send


def request(owner_namespace, **changes):
    return {
        "type": "input_status",
        "request_id": "receipt-1",
        "group_id": "main",
        "namespace": owner_namespace,
        "message_id": "native:original",
        "wait": False,
        **changes,
    }


async def hello(reader, writer):
    await send(writer, {"type": "ping", "group_id": "main"})
    pong = await receive(reader, "pong")
    assert pong["input_receipts"] == 1
    assert len(pong["inbox_namespace"]) == 32
    return pong["inbox_namespace"]


async def test_disconnect_after_admission_without_reading_ack_reads_same_running_and_final_receipt(gateway):
    server, entered, release, contexts, socket_path = gateway
    reader, writer = await asyncio.open_unix_connection(socket_path)
    namespace = await hello(reader, writer)
    await send(writer, {"type": "message", "run_id": "original", "group_id": "main", "content": "HOLD"})
    await asyncio.wait_for(entered.wait(), 3)
    writer.close()  # No run_ack read: client cannot assume acceptance from its transport.
    await writer.wait_closed()
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        assert await hello(reader, writer) == namespace
        await send(writer, request(namespace))
        current = await receive(reader, "input_status_result")
        assert current["success"] and current["message"]["status"] == "running"
        attempt = current["message"]["attempt_id"]
        await send(writer, request(namespace, request_id="wait", wait=True))
        # An online wait is a detached read, never a blocking socket dispatch.
        await send(writer, {"type": "ping"})
        assert (await receive(reader, "pong"))["input_receipts"] == 1
        assert contexts == [["HOLD"]]
        release.set()
        final = await receive(reader, "input_status_result", request_id="wait")
        assert final["success"] and final["message"]["status"] == "succeeded"
        assert final["message"]["attempt_id"] == attempt
        assert final["message"]["response"] == "answer"
        assert "images" not in final["message"] and "instruction" not in final["message"]
        assert contexts == [["HOLD"]]
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()


async def test_disconnect_of_a_receipt_waiter_does_not_cancel_the_original_agent(gateway):
    server, entered, release, contexts, socket_path = gateway
    first_reader, first_writer = await asyncio.open_unix_connection(socket_path)
    namespace = await hello(first_reader, first_writer)
    await send(first_writer, {"type": "message", "run_id": "original", "group_id": "main", "content": "HOLD"})
    ack = await receive(first_reader, "run_ack")
    assert ack["inbox_namespace"] == namespace
    await asyncio.wait_for(entered.wait(), 3)
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        await send(writer, request(namespace, wait=True))
        await send(writer, {"type": "ping"})
        await receive(reader, "pong")
        writer.close()
        await writer.wait_closed()
        release.set()
        await receive(first_reader, "response")
        inputs = await server._orchestrator.input_service()
        scope = InputScope("main", server._orchestrator.get_group_workspace("main"))
        assert (await inputs.wait(scope, "native:original")).status == "succeeded"
        assert contexts == [["HOLD"]]
    finally:
        release.set()
        first_writer.close()
        writer.close()
        await first_writer.wait_closed()
        await writer.wait_closed()


@pytest.mark.parametrize(
    "change",
    [
        {"namespace": "0" * 32},
        {"namespace": []},
        {"group_id": "missing"},
        {"wait": 1},
        {"message_id": "acp-one"},
        {"message_id": "native:"},
        {"message_id": "\ud800"},
        {"request_id": ""},
    ],
)
async def test_malformed_or_wrong_scope_receipt_read_cannot_submit_or_change_input(gateway, change):
    server, entered, release, contexts, socket_path = gateway
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        namespace = await hello(reader, writer)
        await send(writer, request(namespace, **change))
        result = await receive(reader, "input_status_result")
        assert not result["success"] and "message" not in result
        inputs = await server._orchestrator.input_service()
        scope = InputScope("main", server._orchestrator.get_group_workspace("main"))
        assert not await inputs.store.records(scope) and contexts == []
    finally:
        writer.close()
        await writer.wait_closed()


async def test_known_absent_receipt_is_a_read_only_fact_in_the_same_namespace(gateway):
    server, entered, release, contexts, socket_path = gateway
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        namespace = await hello(reader, writer)
        await send(writer, request(namespace, wait=True))
        result = await receive(reader, "input_status_result")
        assert result["success"] and result["namespace"] == namespace and result["message"] is None
        assert contexts == []
    finally:
        writer.close()
        await writer.wait_closed()


async def test_receipt_wait_rechecks_original_workspace_after_actual_completion(gateway):
    from xdog.claw.core.runtime.input_queries import NativeInputReceipts

    server, entered, release, contexts, socket_path = gateway
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        namespace = await hello(reader, writer)
        await send(writer, {"type": "message", "run_id": "original", "group_id": "main", "content": "HOLD"})
        await receive(reader, "run_ack")
        await asyncio.wait_for(entered.wait(), 3)
        inputs = await server._orchestrator.input_service()
        scope = InputScope("main", server._orchestrator.get_group_workspace("main"))
        binding = [scope]
        queries = NativeInputReceipts(inputs, lambda group: binding[0])
        waiting = asyncio.Event()
        actual_wait = inputs.wait

        async def marked_wait(*args):
            waiting.set()
            return await actual_wait(*args)

        inputs.wait = marked_wait
        reading = asyncio.create_task(queries.read("main", namespace, "native:original", wait=True))
        await asyncio.wait_for(waiting.wait(), 3)
        binding[0] = InputScope("main", scope.workspace / "another-workspace")
        release.set()
        with pytest.raises(ValueError, match="workspace or inbox changed"):
            await asyncio.wait_for(reading, 3)
        await receive(reader, "response")
        assert (await inputs.store.get(scope, "native:original")).status == "succeeded"
        assert contexts == [["HOLD"]]
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()


async def test_native_id_cannot_borrow_another_transport_binding(gateway):
    from xdog.agent.session_input import SessionInput
    from xdog.claw.core.inputs.models import InputEnvelope, ReplyTarget

    server, entered, release, contexts, socket_path = gateway
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        namespace = await hello(reader, writer)
        inputs = await server._orchestrator.input_service()
        scope = InputScope("main", server._orchestrator.get_group_workspace("main"))
        await inputs.store.enqueue(
            InputEnvelope(
                scope,
                SessionInput("native:original", "automatic", "source", "foreign input", (), {}),
                reply=ReplyTarget("weixin", "not-a-native-run"),
            )
        )
        await send(writer, request(namespace, wait=True))
        response = await receive(reader, "input_status_result")
        assert not response["success"] and "another host binding" in response["error"]
        assert (await inputs.store.get(scope, "native:original")).status == "pending"
        assert contexts == []
    finally:
        writer.close()
        await writer.wait_closed()


async def test_outstanding_receipt_waits_are_bounded_without_blocking_controls(gateway):
    server, entered, release, contexts, socket_path = gateway
    source_reader, source_writer = await asyncio.open_unix_connection(socket_path)
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        namespace = await hello(source_reader, source_writer)
        await send(source_writer, {"type": "message", "run_id": "original", "group_id": "main", "content": "HOLD"})
        await receive(source_reader, "run_ack")
        await asyncio.wait_for(entered.wait(), 3)
        for number in range(16):
            await send(writer, request(namespace, request_id=f"wait-{number}", wait=True))
        await send(writer, request(namespace, request_id="overflow", wait=True))
        response = await receive(reader, "input_status_result", request_id="overflow")
        assert not response["success"] and "Too many" in response["error"]
        await send(writer, {"type": "ping"})
        assert (await receive(reader, "pong"))["input_receipts"] == 1
        writer.close()
        await writer.wait_closed()
        release.set()
        await receive(source_reader, "response")
        assert contexts == [["HOLD"]]
    finally:
        release.set()
        writer.close()
        source_writer.close()
        await writer.wait_closed()
        await source_writer.wait_closed()
