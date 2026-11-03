"""Interactive acknowledgement is cancellable IO, never a polling/model worker."""
from __future__ import annotations

import asyncio
import io
import os

import pytest
from xdog.claw.cli import xhs_login


def stdin(monkeypatch, stream):
    original = xhs_login.click.get_text_stream
    monkeypatch.setattr(xhs_login.click, 'get_text_stream',
                        lambda name: stream if name == 'stdin' else original(name))


async def test_partial_input_does_not_block_loop_and_enter_confirms(monkeypatch):
    reader, writer = os.pipe()
    with os.fdopen(reader) as stream:
        stdin(monkeypatch, stream)
        task = asyncio.create_task(xhs_login.confirm_scan())
        try:
            await asyncio.sleep(.01)
            os.write(writer, b'confirmed')
            await asyncio.sleep(.01)
            assert not task.done()
            os.write(writer, b'\n')
            await asyncio.wait_for(task, 2)
            assert not asyncio.get_running_loop().remove_reader(reader)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            os.close(writer)


async def test_cancel_removes_reader_without_a_background_input_thread(monkeypatch):
    reader, writer = os.pipe()
    with os.fdopen(reader) as stream:
        stdin(monkeypatch, stream)
        task = asyncio.create_task(xhs_login.confirm_scan())
        try:
            await asyncio.sleep(.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not asyncio.get_running_loop().remove_reader(reader)
        finally:
            os.close(writer)


async def test_eof_does_not_count_as_user_confirmation(monkeypatch):
    reader, writer = os.pipe()
    with os.fdopen(reader) as stream:
        stdin(monkeypatch, stream)
        os.close(writer)
        with pytest.raises(ValueError, match='input closed'):
            await xhs_login.confirm_scan()
        assert not asyncio.get_running_loop().remove_reader(reader)


async def test_unsupported_stdin_fails_without_starting_an_input_thread(monkeypatch):
    stdin(monkeypatch, io.StringIO('\n'))
    with pytest.raises(ValueError, match='interactive terminal'):
        await xhs_login.confirm_scan()
