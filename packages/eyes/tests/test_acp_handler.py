"""Eyes must interoperate with a real third-party SDK process, not a coding host."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from xdog.eyes.executor import Delivery
from xdog.eyes.models import Event, FileEvidence, FileState, HandlerDef


def definition(record, *, mode="success", xdog=False, gate=None):
    argv = [
        sys.executable,
        str(Path(__file__).with_name("fixtures") / "acp_agent.py"),
        "--record",
        str(record),
        "--mode",
        mode,
    ]
    if xdog:
        argv.append("--xdog")
    if gate is not None:
        argv.extend(["--gate", str(gate)])
    return HandlerDef("handler", "acp", tuple(argv), "target", "Review tests")


def delivery(identifier="d1"):
    evidence = FileEvidence("src/a.py; $(touch should-not-exist)", None, FileState("hash", 3), 1.0, "/project")
    return Delivery(
        identifier,
        "registration",
        "route",
        "Review tests",
        (Event("event", "files", "changes", "file.created", 1.0, (evidence,)),),
    )


def rows(record):
    return [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_generic_sdk_agent_gets_explicit_target_and_untrusted_evidence(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path / "record;touch should-not-exist"
    handler = AcpHandler(definition(record), cwd=tmp_path)
    try:
        await handler.connect()
        assert handler.owner_generation is None
        result = await handler.deliver(delivery())
        assert result.status == "succeeded"
        data = rows(record)
        loaded = next(row for row in data if row["kind"] == "load")
        assert loaded["session_id"] == "target" and loaded["cwd"] == str(tmp_path)
        assert loaded["mcp_servers"] == []
        prompt = next(row for row in data if row["kind"] == "prompt")
        assert "Review tests" in prompt["prompt"][0]["text"]
        assert "Untrusted evidence" in prompt["prompt"][1]["text"]
        assert "src/a.py" in prompt["prompt"][1]["text"]
        xdog = prompt["kwargs"]["xdog"]
        assert set(xdog) == {"source", "evidence", "provenance"}
        assert xdog["provenance"]["delivery_id"] == "d1"
        assert xdog["evidence"][0]["source_id"] == "files"
        assert not [row for row in data if row["kind"] == "extension"]
        assert not (tmp_path / "should-not-exist").exists()
    finally:
        await handler.close()
    assert handler.process.returncode is not None


async def test_update_is_not_completion_and_shared_handler_serializes_prompts(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record, gate = tmp_path / "record", tmp_path / "gate"
    handler = AcpHandler(definition(record, mode="wait", gate=gate), cwd=tmp_path)
    first = second = None
    try:
        await handler.connect()
        first = asyncio.create_task(handler.deliver(delivery()))
        await wait_for(lambda: any(row["kind"] == "update" for row in rows(record)))
        second = asyncio.create_task(handler.deliver(delivery("d2")))
        await asyncio.sleep(0.05)
        assert not first.done() and not second.done()
        assert len([row for row in rows(record) if row["kind"] == "prompt"]) == 1
        gate.touch()
        assert (await first).status == "succeeded"
        assert (await second).status == "paused"
        assert not await handler.resume()  # Generic ACP has no authorized resume extension.
        assert [row["concurrent"] for row in rows(record) if row["kind"] == "prompt"] == [1]
    finally:
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)
        await handler.close()


@pytest.mark.parametrize(
    "mode,status",
    [
        ("error", "failed"),
        ("refusal", "failed"),
        ("permission", "cancelled"),
        ("disconnect", "outcome-unknown"),
        ("stderr", "succeeded"),
    ],
)
async def test_explicit_result_and_disconnect_have_distinct_semantics(tmp_path, mode, status):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path / "record"
    handler = AcpHandler(definition(record, mode=mode), cwd=tmp_path)
    try:
        async with asyncio.timeout(8):
            await handler.connect()
            result = await handler.deliver(delivery())
        assert result.status == status
        if mode == "permission":
            assert next(row for row in rows(record) if row["kind"] == "permission")["outcome"] == {
                "outcome": "cancelled"
            }
        if mode == "disconnect":
            assert (await handler.deliver(delivery())).status == "outcome-unknown"
            assert not await handler.resume()
            assert len([row for row in rows(record) if row["kind"] == "prompt"]) == 1
    finally:
        await handler.close()


async def test_extensions_are_used_only_when_negotiated_and_do_not_follow_a_new_owner(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path / "record"
    handler = AcpHandler(definition(record, mode="changed-owner", xdog=True), cwd=tmp_path)
    try:
        await handler.connect()
        assert handler.owner_generation == "g1"
        assert (await handler.deliver(delivery())).status == "cancelled"
        assert not [row for row in rows(record) if row["kind"] == "prompt"]
    finally:
        await handler.close()


async def test_resume_is_an_explicit_negotiated_permission_request(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path / "record"
    handler = AcpHandler(definition(record, mode="deny-resume", xdog=True), cwd=tmp_path)
    try:
        await handler.connect()
        assert not await handler.resume()
        assert len([row for row in rows(record) if row["kind"] == "extension" and row["method"] == "xdog/resume"]) == 1
        assert not [row for row in rows(record) if row["kind"] == "prompt"]
    finally:
        await handler.close()


async def test_load_support_and_exact_session_are_required_before_any_prompt(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path / "record"
    handler = AcpHandler(definition(record, mode="no-load"), cwd=tmp_path)
    try:
        with pytest.raises(ValueError, match="load"):
            await handler.connect()
        assert not [row for row in rows(record) if row["kind"] in {"load", "prompt"}]
    finally:
        await handler.close()
    assert handler.process.returncode is not None


async def test_target_managed_pending_prompt_resumes_without_reissuing_it(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path / "record"
    handler = AcpHandler(definition(record, xdog=True), cwd=tmp_path)
    pending = None
    try:
        await handler.connect()
        assert (await handler.deliver(delivery())).status == "succeeded"
        pending = asyncio.create_task(handler.deliver(delivery("d2")))
        await asyncio.sleep(0.05)
        assert not pending.done()
        assert len([row for row in rows(record) if row["kind"] == "prompt"]) == 1
        assert await handler.resume()
        assert (await pending).status == "succeeded"
        assert len([row for row in rows(record) if row["kind"] == "received"]) == 2
        assert [row["concurrent"] for row in rows(record) if row["kind"] == "prompt"] == [1, 1]
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await handler.close()


async def test_human_credit_at_target_does_not_need_a_second_eyes_budget(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record, gate = tmp_path / "record", tmp_path / "human-credit"
    handler = AcpHandler(definition(record, mode="human-credit", xdog=True, gate=gate), cwd=tmp_path)
    try:
        await handler.connect()
        assert (await handler.deliver(delivery())).status == "succeeded"
        gate.touch()
        assert (await handler.deliver(delivery("d2"))).status == "succeeded"
        assert not [row for row in rows(record) if row["kind"] == "extension" and row["method"] == "xdog/resume"]
    finally:
        await handler.close()


async def test_v2_does_not_fall_back_to_the_retired_event_host_during_runtime_migration(tmp_path, observer_data):
    from xdog.eyes.runtime import ObserverRuntime
    from xdog.eyes.transport.protocol import HostContext, Owner

    observer_data["sources"][0]["root"] = str(tmp_path)
    context = HostContext(
        tmp_path / "unused.sock",
        Owner("old", "session", "branch", "generation"),
        "unused-test-token",
        tmp_path / "unused.json",
    )
    runtime = ObserverRuntime(context)
    try:
        with pytest.raises(RuntimeError, match="ACP"):
            await runtime.start(observer_data)
        assert not runtime.entries
    finally:
        await runtime.client.aclose()
