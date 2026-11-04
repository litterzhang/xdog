from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

from xdog.agent.mcp.context import McpBlock, McpDocument
from xdog.claw.channels.tui.mcp_data import McpDataReceiver, spool_mcp_data
from xdog.claw.channels.tui.mcp_protocol import McpSender


def request(identifier="one"):
    return {"request_id": identifier, "group_id": "main", "session_id": "session", "action": "read"}


def document():
    return McpDocument("fixture", "resource", "fixture://text", (
        McpBlock("user", text="x" * 200000, content_type="text"),))


async def wait(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.002)


async def test_sender_coalesces_and_cancels_remote_work_without_blocking_controls():
    entered, canceled = asyncio.Event(), asyncio.Event()
    frames = []
    async def fetch(value):
        if value["request_id"] == "one":
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()
        return document()
    async def write(frame):
        frames.append(frame)
    sender = McpSender(fetch, lambda group: ("session", 1), write)
    try:
        sender.request(request())
        await entered.wait()
        sender.request(request("two"))
        sender.request(request("three"))
        await wait(lambda: frames and frames[-1]["type"] == "mcp_data_end")
        assert canceled.is_set()
        assert {frame["request_id"] for frame in frames} == {"three"}
        sender.cancel("one")  # A stale cancellation must not affect the successor.
        events = []
        receiver = McpDataReceiver(events.append)
        receiver.expect("three", "main", "session")
        for frame in frames:
            receiver.feed(frame)
        assert events[-1]["type"] == "mcp_data_ready"
        assert events[-1]["records"] == document()
        receiver.close()
    finally:
        await sender.close()


async def test_cancel_during_spooling_settles_one_file_owner_before_successor():
    entered, release = threading.Event(), threading.Event()
    files, calls, frames = [], [], []
    def prepare(value):
        file, size, checksum = spool_mcp_data(value)
        files.append(file)
        if len(files) == 1:
            entered.set()
            assert release.wait(3)
        return file, size, checksum
    async def fetch(value):
        calls.append(value["request_id"])
        return document()
    async def write(frame):
        frames.append(frame)
    sender = McpSender(fetch, lambda group: ("session", 1), write)
    with patch("xdog.claw.channels.tui.mcp_protocol.spool_mcp_data", prepare):
        try:
            sender.request(request())
            await wait(entered.is_set)
            sender.request(request("two"))
            sender.cancel("one")
            sender.cancel("one")
            await asyncio.sleep(0.02)
            assert calls == ["one"] and not frames and not files[0].closed
            release.set()
            await wait(lambda: frames and frames[-1]["type"] == "mcp_data_end")
            assert files[0].closed
            assert {frame["request_id"] for frame in frames} == {"two"}
        finally:
            release.set()
            await sender.close()
    assert all(file.closed for file in files)


async def test_context_change_during_remote_read_rejects_preview():
    entered, release = asyncio.Event(), asyncio.Event()
    lease, frames = ["session", 1], []
    async def fetch(value):
        entered.set()
        await release.wait()
        return document()
    async def write(frame):
        frames.append(frame)
    sender = McpSender(fetch, lambda group: tuple(lease), write)
    try:
        sender.request(request())
        await entered.wait()
        lease[1] = 2
        release.set()
        await wait(lambda: frames)
        assert len(frames) == 1 and frames[0]["type"] == "mcp_data_error"
        assert "changed" in frames[0]["error"]
    finally:
        await sender.close()


async def test_completion_payload_uses_same_bounded_checksum_session_fenced_transfer():
    from xdog.agent.mcp.completion import McpCompletion
    value = McpCompletion('peer', 'prompt', 'review', 'topic', 'pre', (('language', 'python'),),
                          ('first', '第二项'), 5, True)
    frames, events = [], []
    async def fetch(request):
        return value
    async def write(frame):
        frames.append(frame)
    sender = McpSender(fetch, lambda group: ('session', 3), write)
    receiver = McpDataReceiver(events.append)
    try:
        sender.request({**request('completion'), 'action': 'complete_prompt'})
        await wait(lambda: frames and frames[-1]['type'] == 'mcp_data_end')
        receiver.expect('completion', 'main', 'session')
        for frame in frames:
            receiver.feed(frame)
        assert events[-1]['type'] == 'mcp_data_ready' and events[-1]['records'] == value
        assert all(frame['revision'] == 3 for frame in frames)
    finally:
        receiver.close()
        await sender.close()


async def test_opaque_completion_transport_keeps_reference_value_and_context_as_data():
    from xdog.agent.mcp.completion import McpCompletion
    value = McpCompletion('peer', 'resource', 'file:///not-followed', 'opaque-field', 'pre',
                          (('language', '中文'),), ('not-a-uri', '../../private'), 2, False)
    frames, events = [], []
    async def fetch(request):
        return value
    async def write(frame):
        frames.append(frame)
    sender = McpSender(fetch, lambda group: ('session', 4), write)
    receiver = McpDataReceiver(events.append)
    try:
        sender.request({**request('opaque'), 'action': 'complete_resource'})
        await wait(lambda: frames and frames[-1]['type'] == 'mcp_data_end')
        receiver.expect('opaque', 'main', 'session')
        for frame in frames:
            receiver.feed(frame)
        assert events[-1]['records'] == value
        assert all(frame['revision'] == 4 for frame in frames)
    finally:
        receiver.close()
        await sender.close()


async def test_typed_empty_binary_roundtrip_cannot_downgrade_to_text():
    import pytest
    from xdog.agent.mcp.stdio import McpError
    from xdog.claw.channels.tui.mcp_data import _decode

    empty = McpDocument('peer', 'resource', 'test://empty', (
        McpBlock('resource', data='', mime_type='audio/wav', content_type='audio'),))
    file, _size, _checksum = spool_mcp_data(empty)
    try:
        decoded = _decode(file)
        assert decoded == empty and '0 bytes' in decoded.text
        with pytest.raises(McpError, match='Binary'):
            decoded.draft_text()
    finally:
        file.close()
    calls, frames = [], []
    async def fetch(value):
        calls.append(value)
        return empty
    async def write(value):
        frames.append(value)
    sender = McpSender(fetch, lambda _: ('session', 1), write)
    events = []
    receiver = McpDataReceiver(events.append)
    receiver.expect('one', 'main', 'session')
    try:
        sender.request(request())
        await wait(lambda: frames and frames[-1]['type'] == 'mcp_data_end')
        for frame in frames:
            receiver.feed(frame)
        assert len(calls) == 1 and events[-1]['records'] == empty
    finally:
        receiver.close()
        await sender.close()


def test_untyped_native_mcp_documents_are_rejected():
    import io
    import json

    import pytest
    from xdog.claw.channels.tui.mcp_data import _decode

    document = {'server': 'peer', 'kind': 'resource', 'name': 'old', 'blocks': [
        {'role': 'resource', 'text': 'metadata', 'mime_type': '', 'data': '', 'uri': 'test://old'}]}
    with pytest.raises(TypeError):
        _decode(io.StringIO(json.dumps({'kind': 'document', 'document': document})))
