"""Eyes must interoperate with a real third-party SDK process, not a coding host."""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from xdog.eyes.deliveries import Delivery
from xdog.eyes.models import AcpHandlerDef, Event, FileEvidence, FileState


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
    from xdog.eyes.connections import StdioConnection

    return AcpHandlerDef("handler", "acp", StdioConnection(tuple(argv)), "target", "Review tests")


def delivery(identifier="d1"):
    evidence = FileEvidence("src/a.py; $(touch should-not-exist)", None, FileState("hash", 3), 1.0, "/project")
    return Delivery(
        identifier,
        "registration",
        "route",
        (Event("event", "files", "changes", "file.created", 1.0, (evidence,)),),
    )


def rows(record):
    return [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_generic_sdk_agent_gets_explicit_target_and_untrusted_evidence(tmp_path, monkeypatch):
    from xdog.eyes.handlers.acp import AcpHandler

    caller = tmp_path / "stdio-caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    record = tmp_path / "record;touch should-not-exist"
    handler = AcpHandler(replace(definition(record), cwd=tmp_path))
    try:
        await handler.start()
        assert handler.owner_generation is None
        result = await handler.deliver(delivery())
        assert result.status == "succeeded"
        data = rows(record)
        initialized = next(row for row in data if row["kind"] == "initialize")
        assert initialized["process_cwd"] == str(caller)
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
    handler = AcpHandler(replace(definition(record, mode="wait", gate=gate), cwd=tmp_path))
    first = second = None
    try:
        await handler.start()
        first = asyncio.create_task(handler.deliver(delivery()))
        await wait_for(lambda: any(row["kind"] == "update" for row in rows(record)))
        second = asyncio.create_task(handler.deliver(delivery("d2")))
        await asyncio.sleep(0.05)
        assert not first.done() and not second.done()
        assert len([row for row in rows(record) if row["kind"] == "prompt"]) == 1
        gate.touch()
        assert (await first).status == "succeeded"
        assert (await second).status == "succeeded"
        assert not await handler.resume()  # Generic ACP has no authorized resume extension.
        assert [row["concurrent"] for row in rows(record) if row["kind"] == "prompt"] == [1, 1]
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
    handler = AcpHandler(replace(definition(record, mode=mode), cwd=tmp_path))
    try:
        async with asyncio.timeout(8):
            await handler.start()
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
    handler = AcpHandler(replace(definition(record, mode="changed-owner", xdog=True), cwd=tmp_path))
    try:
        await handler.start()
        assert handler.owner_generation == "g1"
        assert (await handler.deliver(delivery())).status == "cancelled"
        assert not [row for row in rows(record) if row["kind"] == "prompt"]
    finally:
        await handler.close()


async def test_resume_does_not_request_message_execution_permission(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path / "record"
    handler = AcpHandler(replace(definition(record, mode="deny-resume", xdog=True), cwd=tmp_path))
    try:
        await handler.start()
        assert not await handler.resume()
        assert len([row for row in rows(record) if row["kind"] == "extension" and row["method"] == "xdog/resume"]) == 0
        assert not [row for row in rows(record) if row["kind"] == "prompt"]
    finally:
        await handler.close()


async def test_load_support_and_exact_session_are_required_before_any_prompt(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path / "record"
    handler = AcpHandler(replace(definition(record, mode="no-load"), cwd=tmp_path))
    try:
        with pytest.raises(ValueError, match="load"):
            await handler.start()
        assert not [row for row in rows(record) if row["kind"] in {"load", "prompt"}]
    finally:
        await handler.close()
    assert handler.process.returncode is not None


async def test_negotiated_target_extensions_do_not_require_a_resume_request(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path / 'record'
    handler = AcpHandler(replace(definition(record, xdog=True), cwd=tmp_path))
    try:
        await handler.start()
        for identifier in ('first', 'second'):
            assert (await handler.deliver(delivery(identifier))).status == 'succeeded'
        assert len([row for row in rows(record) if row['kind'] == 'received']) == 2
        assert [row['concurrent'] for row in rows(record) if row['kind'] == 'prompt'] == [1, 1]
        assert not [r for r in rows(record) if r['kind'] == 'extension' and r['method'] == 'xdog/resume']
    finally:
        await handler.close()


async def test_target_metadata_does_not_grant_message_execution_credit(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record, gate = tmp_path / "record", tmp_path / "human-credit"
    handler = AcpHandler(replace(definition(record, mode="human-credit", xdog=True, gate=gate), cwd=tmp_path))
    try:
        await handler.start()
        assert (await handler.deliver(delivery())).status == "succeeded"
        assert not gate.exists()
        assert (await handler.deliver(delivery("d2"))).status == "succeeded"
        assert not [row for row in rows(record) if row["kind"] == "extension" and row["method"] == "xdog/resume"]
    finally:
        await handler.close()




@pytest.mark.parametrize('mode',['headless','no-budget-hint'])
async def test_missing_or_false_budget_metadata_does_not_block_second_message(tmp_path,mode):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path/'peer.jsonl'
    handler = AcpHandler(replace(definition(record,mode=mode,xdog=True), cwd=tmp_path))
    try:
        await handler.start()
        assert (await handler.deliver(delivery())).status=='succeeded'
        assert (await handler.deliver(delivery('second'))).status=='succeeded'
        assert len([r for r in rows(record) if r['kind']=='prompt'])==2
    finally:
        await handler.close()


async def test_two_prompt_deliveries_need_no_human_credit(tmp_path):
    from xdog.eyes.handlers.acp import AcpHandler

    record = tmp_path / 'auto-delivery.jsonl'
    handler = AcpHandler(replace(definition(record), cwd=tmp_path))
    try:
        await handler.start()
        assert (await handler.deliver(delivery('first'))).status == 'succeeded'
        assert (await handler.deliver(delivery('second'))).status == 'succeeded'
        assert len([row for row in rows(record) if row['kind'] == 'prompt']) == 2
        assert not [row for row in rows(record) if row['kind'] == 'extension']
    finally:
        await handler.close()
