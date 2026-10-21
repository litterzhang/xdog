"""Cancellation settles actual image threads before releasing producer ownership."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.agent.child_transfer import settled_image_work


@pytest.mark.parametrize('fails', [False, True])
async def test_repeated_cancellation_waits_for_real_thread_and_consumes_failure(fails):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    def work():
        entered.set()
        try:
            assert release.wait(5)
            if fails:
                raise ValueError('Owned late worker failure')
            return 'NEVER DELIVER AFTER CANCEL'
        finally:
            exited.set()
    task = asyncio.create_task(settled_image_work(work))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and not exited.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_worker_result_and_uncancelled_error_are_preserved():
    assert await settled_image_work(lambda: {'actual': True}) == {'actual': True}
    def fail():
        raise ValueError('actual failure')
    with pytest.raises(ValueError, match='actual failure'):
        await settled_image_work(fail)


async def test_cancel_during_actual_sender_decode_emits_no_frames(monkeypatch):
    import xdog.agent.child_transfer as module
    from xdog.ai.utils.image_files import decode_image
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    def held(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(5)
            return decode_image(*args, **kwargs)
        finally:
            exited.set()
    monkeypatch.setattr(module, 'decode_image', held)
    frames = []
    async def send(frame):
        frames.append(frame)
    payload = {'images': [{'data': 'iVBORw0KGgo=', 'mime_type': 'image/png'}],
               'receipt': {'content': 'copy', 'image_sha256': ['a' * 64], 'image_mime_types': ['image/png']}}
    task = asyncio.create_task(module.send_recovery(payload, {}, send, lambda: True))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and not frames
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set() and not frames
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
