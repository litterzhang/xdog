"""Real pipe backpressure, ordered framing and owned asynchronous output teardown."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from xdog.coding.modes.rpc.output import RpcOutput

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Native POSIX fd ownership')


def pipe(monkeypatch):
    reader, writer = os.pipe()
    os.set_blocking(reader, False)
    class Output:
        def fileno(self):
            return writer
    monkeypatch.setattr(sys, 'stdout', Output())
    output = RpcOutput()
    output.open()
    assert output.fd == writer and not os.get_blocking(writer)
    return output, reader, writer


async def lines(reader, count):
    data = bytearray()
    async with asyncio.timeout(3):
        while data.count(b'\n') < count:
            try:
                block = os.read(reader, 65536)
            except BlockingIOError:
                await asyncio.sleep(0.001)
                continue
            assert block
            data.extend(block)
    return [json.loads(line) for line in data.splitlines()]


async def test_stalled_peer_does_not_block_loop_and_canceled_producer_cannot_split_a_json_line(monkeypatch):
    output, reader, writer = pipe(monkeypatch)
    sending = asyncio.create_task(output.send({'type': 'large', 'text': '🙂' * 100000}))
    try:
        await asyncio.sleep(0.02)
        assert not sending.done() and output.bytes > 0
        sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)
        output.emit({'type': 'status', 'responsive': True})
        received = await lines(reader, 2)
        assert received == [{'type': 'large', 'text': '🙂' * 100000}, {'type': 'status', 'responsive': True}]
        await output.aclose()
        assert os.get_blocking(writer) and not output.queue and output.bytes == 0
    finally:
        await output.aclose()
        os.close(reader)
        os.close(writer)


async def test_bounded_backlog_refuses_whole_frame_then_keeps_ordered_reader_recovery(monkeypatch):
    output, reader, writer = pipe(monkeypatch)
    monkeypatch.setattr('xdog.coding.modes.rpc.output.MAX_OUTPUT_BYTES', 100)
    try:
        with pytest.raises(OSError, match='backlog'):
            output.emit({'type': 'large', 'text': 'x' * 200})
        assert output.bytes == 0 and not output.queue
        output.emit({'type': 'status', 'ok': True})
        assert (await lines(reader, 1))[0]['ok']
    finally:
        await output.aclose()
        os.close(reader)
        os.close(writer)


async def test_closed_output_peer_signals_owner_while_input_can_remain_open(monkeypatch):
    output, reader, writer = pipe(monkeypatch)
    os.close(reader)
    try:
        output.emit({'type': 'status'})
        async with asyncio.timeout(2):
            await output.failure
        assert isinstance(output.error, OSError)
        with pytest.raises(OSError):
            output.emit({'type': 'next'})
        await output.aclose()
        assert os.get_blocking(writer)
    finally:
        await output.aclose()
        os.close(writer)


async def test_shutdown_with_unread_source_settles_waiter_and_restores_fd_flags(monkeypatch):
    output, reader, writer = pipe(monkeypatch)
    sending = asyncio.create_task(output.send({'type': 'large', 'text': 'x' * 200000}))
    try:
        await asyncio.sleep(0.02)
        async with asyncio.timeout(2):
            await output.aclose()
        with pytest.raises(OSError, match='closed'):
            await sending
        assert os.get_blocking(writer) and output.task.done() and output.bytes == 0
    finally:
        await output.aclose()
        os.close(reader)
        os.close(writer)


@pytest.mark.parametrize('kind', ['transient_event', 'child_transient_event',
                                  'extension_contribution', 'child_contribution'])
async def test_transient_flood_reserves_control_memory_and_reports_loss_on_next_same_kind(monkeypatch, kind):
    output, reader, writer = pipe(monkeypatch)
    monkeypatch.setattr('xdog.coding.modes.rpc.output.TRANSIENT_BACKLOG_BYTES', 4096)
    try:
        output.emit({'type': kind, 'data': 'x' * 2000, 'sequence': 1})
        for index in range(2, 102):
            output.emit({'type': kind, 'data': 'x' * 3000, 'sequence': index})
        assert output.bytes < 4096 and len(output.queue) == 1
        assert output.transient_dropped[kind] == 100
        output.emit({'type': 'permission_request', 'id': 'must-remain-responsive'})
        records = await lines(reader, 2)
        assert records[0]['sequence'] == 1 and records[1]['type'] == 'permission_request'
        output.emit({'type': kind, 'sequence': 102, 'data': 'resumed'})
        recovered = (await lines(reader, 1))[0]
        assert recovered['limited'] is True and recovered['transport_dropped'] == 100
        assert recovered['sequence'] == 102 and not output.transient_dropped
    finally:
        await output.aclose()
        os.close(reader)
        os.close(writer)


async def test_dropped_transient_counts_are_not_attributed_to_another_event_family(monkeypatch):
    output, reader, writer = pipe(monkeypatch)
    monkeypatch.setattr('xdog.coding.modes.rpc.output.TRANSIENT_BACKLOG_BYTES', 100)
    try:
        output.emit({'type': 'child_transient_event', 'data': 'x' * 200})
        output.emit({'type': 'transient_event', 'sequence': 1})
        assert 'transport_dropped' not in (await lines(reader, 1))[0]
        assert output.transient_dropped == {'child_transient_event': 1}
    finally:
        await output.aclose()
        os.close(reader)
        os.close(writer)
