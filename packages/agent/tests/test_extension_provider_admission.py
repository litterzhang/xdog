from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.providers import ExtensionProviderStream
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.ai.types import Context, ImageContent, StreamOptions, UserMessage


async def owner(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['providermock'], root, tmp_path,
                              settings={'providermock': {'marker': str(tmp_path / 'entered')}})
    await runtime.initialize()
    return runtime


@pytest.mark.parametrize('options,image', [
    (StreamOptions(max_tokens=0), None), (StreamOptions(max_tokens=-1), None),
    (StreamOptions(max_tokens=True), None), (StreamOptions(max_tokens=1.5), None),
    (StreamOptions(), ImageContent(data='aGVsbG8=', mime_type='image/png')),
    (StreamOptions(), ImageContent(data='not base64!', mime_type='image/png')),
    (StreamOptions(), ImageContent(data='iVBORw0KGgo=', mime_type='image/jpeg')),
    (StreamOptions(), ImageContent(data='iVBORw0KGgo=', mime_type='image/gif')),
    (StreamOptions(), ImageContent(data='A' * (14 * 1024 * 1024), mime_type='image/png')),
])
async def test_invalid_binary_or_output_limit_rejected_even_without_declared_vision_limits(tmp_path, options, image):
    runtime = await owner(tmp_path)
    try:
        assert runtime.provider_model('ext-providermock-local/echo').vision_limits is None
        context = Context(messages=(UserMessage(content=(image,)),)) if image else Context()
        stream = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))(
            'ext-providermock-local/echo', context, options)
        assert (await stream.collect())[-1].type == 'error'
        assert await runtime.command('ext-providermock-count', '') == '0'
        assert runtime.provider_models() and not runtime._provider_lock.locked()
    finally:
        await runtime.aclose()


async def test_preset_cancel_does_not_invoke_or_withdraw_worker(tmp_path):
    runtime = await owner(tmp_path)
    cancel = asyncio.Event()
    cancel.set()
    try:
        stream = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))(
            'ext-providermock-local/echo', Context(), StreamOptions(cancel=cancel))
        events = await stream.collect()
        assert events[-1].stop_reason == 'aborted'
        assert (await stream.result()).stop_reason == 'aborted'
        assert await runtime.command('ext-providermock-count', '') == '0'
        assert runtime.provider_models()
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('task_cancel', [True, False])
async def test_pending_read_cancel_retains_partial_resolves_result_and_reaps_owned_worker(tmp_path, task_cancel):
    runtime = await owner(tmp_path)
    process = runtime._manager._clients['providermock'].process
    cancel = asyncio.Event()
    try:
        stream = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))(
            'ext-providermock-local/hold', Context(), StreamOptions(cancel=cancel))
        task = asyncio.create_task(stream.collect())
        async with asyncio.timeout(5):
            while not (tmp_path / 'entered').exists():
                await asyncio.sleep(0.003)
            if task_cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                cancel.set()
                assert (await task)[-1].stop_reason == 'aborted'
            message = await stream.result()
        assert message.stop_reason == 'aborted' and message.content[0].text == 'PROVIDER-HOLD'
        assert process.returncode is not None and not runtime.provider_models()
        assert not runtime._provider_lock.locked()
    finally:
        await runtime.aclose()


async def test_cancel_between_events_closes_healthy_worker_without_another_read(tmp_path):
    runtime = await owner(tmp_path)
    process = runtime._manager._clients['providermock'].process
    cancel = asyncio.Event()
    try:
        stream = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))(
            'ext-providermock-local/hold', Context(), StreamOptions(cancel=cancel))
        events = []
        async for event in stream:
            events.append(event)
            if event.type == 'text_delta':
                cancel.set()
        assert events[-1].stop_reason == 'aborted'
        assert not (tmp_path / 'entered').exists()
        assert process.returncode is None and runtime.provider_models()
        assert await runtime.command('ext-providermock-closed', '') == '1'
        assert not runtime._provider_lock.locked()
    finally:
        await runtime.aclose()


async def test_cancel_waiting_stream_cannot_reap_another_streams_active_worker(tmp_path):
    runtime = await owner(tmp_path)
    process = runtime._manager._clients['providermock'].process
    adapter = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))
    active = adapter('ext-providermock-local/hold', Context(), StreamOptions())
    active_task = asyncio.create_task(active.collect())
    cancel = asyncio.Event()
    queued_task = None
    try:
        async with asyncio.timeout(5):
            while not (tmp_path / 'entered').exists():
                await asyncio.sleep(0.003)
            queued = adapter('ext-providermock-local/echo', Context(), StreamOptions(cancel=cancel))
            queued_task = asyncio.create_task(queued.collect())
            await asyncio.sleep(0.03)
            cancel.set()
            assert (await queued_task)[-1].stop_reason == 'aborted'
            assert not active_task.done() and process.returncode is None and runtime.provider_models()
            assert runtime._provider_lock.locked()
            active_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active_task
        assert not runtime._provider_lock.locked()
    finally:
        for task in (active_task, queued_task):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()
