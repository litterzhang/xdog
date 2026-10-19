"""Generated-image filesystem work remains owned after actual provider completion."""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest
from xdog.claw.core.tools import tool_generate_image as images

from packages.claw.tests.test_generate_image import PNG, generate_images
from packages.claw.tests.test_generate_image import runtime as runtime


async def test_image_path_checks_and_actual_saves_run_off_loop(tmp_path, runtime, monkeypatch):
    owner = threading.get_ident()
    checked = []
    original_stat, original_lstat = os.stat, os.lstat

    def observe(actual, path, *args, **kwargs):
        if isinstance(path, (str, os.PathLike)) and Path(path).is_relative_to(tmp_path):
            checked.append(threading.get_ident())
        return actual(path, *args, **kwargs)

    with monkeypatch.context() as trace:
        trace.setattr(os, 'stat', lambda path, *a, **kw: observe(original_stat, path, *a, **kw))
        trace.setattr(os, 'lstat', lambda path, *a, **kw: observe(original_lstat, path, *a, **kw))
        result = await generate_images('Actual single image operation', workspace=tmp_path)
    assert len(result.paths) == 1 and result.paths[0].read_bytes() == PNG
    runtime.image_generation.assert_awaited_once()
    assert checked and all(thread != owner for thread in checked), 'Image filesystem checks blocked the owner loop'


@pytest.mark.parametrize('stage', ['read_reference', 'save_images'])
async def test_image_cancellation_joins_actual_disk_work_before_return(tmp_path, runtime, monkeypatch, stage):
    (tmp_path / 'reference.png').write_bytes(PNG)
    entered, release, exited = (threading.Event() for _ in range(3))
    original = getattr(images, stage)

    def actual_io(*args, **kwargs):
        value = original(*args, **kwargs)
        entered.set()
        try:
            assert release.wait(5)
            return value
        finally:
            exited.set()

    monkeypatch.setattr(images, stage, actual_io)
    cancelled = asyncio.Event()
    work = asyncio.create_task(generate_images('Actual disk boundary', workspace=tmp_path,
        references=('reference.png',), cancel=cancelled))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        cancelled.set()
        for _ in range(3):
            await asyncio.sleep(0)
            work.cancel()
        await asyncio.sleep(0)
        assert not work.done() and not exited.is_set(), 'Image tool released while real file IO was still running'
        release.set()
        with pytest.raises((asyncio.CancelledError, images.ImageGenerationError)):
            await work
        assert exited.is_set()
        assert runtime.image_generation.await_count == (1 if stage == 'save_images' else 0)
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        await asyncio.to_thread(exited.wait, 3)
