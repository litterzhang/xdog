"""Physical source spool reads and cleanup cannot outlive their sender owner."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.runtime.source_protocol import SourceSender
from xdog.claw.core.runtime.transcript_source import SourceCapture, SourceSpool, prepare_source
from xdog.claw.core.types import Group, UserInput


@pytest.mark.parametrize('stage', ['preparation', 'read', 'close'])
async def test_repeated_source_close_cancel_settles_real_spool_io(tmp_path, stage):
    owner = threading.get_ident()
    store = TranscriptStore(tmp_path / 'sessions', owner_group='main')
    meta = store.create_session('main')
    body = 'Actual original source evidence\n' * 10_000
    store.replace_transcript(meta.session_id, [{'role': 'user', 'content': body}])
    capture = SourceCapture(meta.session_id, lambda: store.load_transcript(meta.session_id))
    entered, release, exited = (threading.Event() for _ in range(3))
    files, closed = [], []
    payloads = []

    def boundary():
        entered.set()
        if threading.get_ident() != owner:
            assert release.wait(5)
        exited.set()

    class ActualFile:
        def __init__(self, file):
            self.file = file

        def read(self, size=-1):
            value = self.file.read(size)
            if stage == 'read' and not entered.is_set():
                boundary()
            return value

        def close(self):
            self.file.close()
            closed.append(threading.get_ident())
            if stage == 'close':
                boundary()

    def prepare(snapshot, cancel):
        actual = prepare_source(snapshot, cancel)
        file = ActualFile(actual.file)
        files.append(file)
        if stage == 'preparation':
            boundary()
        return SourceSpool(file, actual.size, actual.sha256)

    async def write(payload):
        payloads.append(payload)

    sender = SourceSender(lambda _group, _session: capture,
                          lambda _group: (meta.session_id, 0), write, prepare)
    closing = None
    try:
        sender.request('original', 'main', meta.session_id)
        assert await asyncio.to_thread(entered.wait, 3)
        closing = asyncio.create_task(sender.close())
        await asyncio.sleep(0)
        for _ in range(3):
            closing.cancel()
            await asyncio.sleep(0)
        assert not closing.done() and not exited.is_set(), 'Source cleanup returned before actual spool IO settled'
        assert sender._active == 'original', 'Source owner was cleared while its actual disk work was running'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert files[0].file.closed and sender._active == ''
        assert len(closed) == 1 and closed[0] != owner, 'Spool close ran on the protocol loop or replayed'
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await sender.close()
        # Settle and close the real test-owned file even on the broken RED path.
        await asyncio.to_thread(exited.wait, 3)
        if files and not files[0].file.closed:
            await asyncio.to_thread(files[0].file.close)


@pytest.mark.parametrize('text,channel', [
    ('[Goal: A literal ordinary user input]', ''),
    ('Goal completed: quoted ordinary text', ''),
    ('Continue working on your active goals: quoted text', ''),
    ('Ordinary message with a source label', 'goal_runner'),
    ('Ordinary message with a source label', 'scheduler'),
])
async def test_generic_source_preserves_actual_ordinary_input_without_goal_policy(tmp_path, text, channel):
    calls = []

    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Actual ordinary response'),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/source', stream_fn=model)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=()))
    spool = None
    try:
        await app.start()
        result = await app.route_message(UserInput(group_id='main', content=text, channel=channel))
        assert result.error is None and len(calls) == 1
        runtime = app._runtimes['main']
        identifier = runtime.get_new_session_id()
        saved = await asyncio.to_thread(runtime.transcript_store.load_transcript, identifier)
        assert saved[0]['type'] == 'xdog.claw.actor.v1'
        assert saved[1]['content'][0]['text'] == text
        spool = await asyncio.to_thread(prepare_source, runtime.capture_source(identifier), threading.Event())
        projected = [json.loads(line) for line in (await asyncio.to_thread(spool.file.read)).splitlines()]
        assert any(row['role'] == 'user' and row['body'] == text for row in projected), (
            'Ordinary canonical input was hidden by Goal-specific source filtering', projected)
        assert any(row['role'] == 'assistant' and row['body'] == 'Actual ordinary response' for row in projected)
    finally:
        if spool is not None:
            await asyncio.to_thread(spool.file.close)
        await app.stop()


async def test_source_late_close_failure_is_reported_without_replaying_cleanup(tmp_path, caplog):
    capture = SourceCapture('actual-source', lambda: [{'role': 'user', 'content': 'Actual readonly source'}])
    files, closes = [], []

    class ActualFile:
        def __init__(self, file):
            self.file = file

        def read(self, size=-1):
            return self.file.read(size)

        def close(self):
            self.file.close()
            closes.append(True)
            raise OSError('Actual spool close failed after releasing descriptor')

    def prepare(snapshot, cancel):
        actual = prepare_source(snapshot, cancel)
        file = ActualFile(actual.file)
        files.append(file)
        return SourceSpool(file, actual.size, actual.sha256)

    async def write(payload):
        pass

    sender = SourceSender(lambda _group, _session: capture, lambda _group: ('actual-source', 0), write, prepare)
    try:
        sender.request('original', 'main', 'actual-source')
        with pytest.raises(OSError, match='Actual spool close'):
            await sender._task
        assert files[0].file.closed and closes == [True]
        assert 'Actual spool close failed' in caplog.text, 'A connected peer lost its actual late cleanup error'
        await sender.close()
        await sender.close()
        assert closes == [True]
    finally:
        await sender.close()


async def test_tui_missing_endpoint_preflight_is_owned_readonly_io(tmp_path, monkeypatch):
    from pathlib import Path

    from xdog.claw.channels.tui.tui_client import GatewayConnectionError, connect_tui

    endpoint = tmp_path / 'absent.sock'
    original = Path.exists
    owner, calls = threading.get_ident(), []

    def exists(path):
        if path == endpoint:
            calls.append(threading.get_ident())
        return original(path)

    monkeypatch.setattr(Path, 'exists', exists)
    with pytest.raises(GatewayConnectionError, match='not found'):
        await connect_tui(str(endpoint))
    assert calls and all(thread != owner for thread in calls), 'TUI connection preflight blocked the shared event loop'
