from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.providers import ExtensionProviderStream
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.ai.types import AssistantMessage, Context, StreamOptions
from xdog.ai.utils.event_stream import EventStream


async def owner(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['providerdynamic'], root, tmp_path)
    await runtime.initialize()
    return runtime


def test_api_explicit_replace_and_old_disposer_cannot_remove_a_successor():
    api = ExtensionAPI({})
    models = [{'id': 'one', 'context_window': 10000, 'max_tokens': 1000}]
    def callback(*args):
        return None
    old = api.register_provider('local', models, callback)
    initial = api.providers['local'][0]['registration_id']
    with pytest.raises(ValueError):
        api.register_provider('LOCAL', models, callback)
    with pytest.raises(ValueError):
        api.register_provider('local', models, callback, replace=1)
    new = api.register_provider('LOCAL', models, callback, replace=True)
    assert api.providers['local'][0]['registration_id'] != initial
    revision = api._provider_revision
    old()
    assert api._provider_revision == revision and 'local' in api.providers
    new()
    assert api._provider_revision == revision + 1 and not api.providers
    new()
    assert api._provider_revision == revision + 1
    assert not api.unregister_provider('local')


async def test_real_worker_dynamic_catalog_metadata_and_explicit_removal_no_fallback(tmp_path):
    runtime = await owner(tmp_path)
    base_calls = []

    def base(*args):
        base_calls.append(args)
        return EventStream.empty(AssistantMessage())

    adapter = ExtensionProviderStream(runtime, base)
    try:
        process = runtime._manager._clients['providerdynamic'].process
        assert len(runtime.provider_models()) == 1
        assert await runtime.command('ext-providerdynamic-enable', '') == 'DYNAMIC-ENABLED'
        alias = 'ext-providerdynamic-live/echo'
        assert runtime.provider_model(alias).context_window == 48000
        stream = adapter(alias, Context(), StreamOptions())
        await stream.collect()
        assert (await stream.result()).stop_reason == 'stop'
        assert await runtime.command('ext-providerdynamic-count', '') == 'CALLS=1;CLOSED=1'
        await runtime.command('ext-providerdynamic-replace', '')
        assert runtime.provider_model(alias).context_window == 16000
        assert runtime.provider_model(alias).supports_tool_calls is False
        await runtime.command('ext-providerdynamic-disable', '')
        assert runtime.provider_model(alias) is None and len(runtime.provider_models()) == 1
        missing = adapter(alias, Context(), StreamOptions())
        assert (await missing.collect())[-1].type == 'error'
        assert not base_calls and process.returncode is None
        assert await runtime.command('ext-providerdynamic-count', '') == 'CALLS=1;CLOSED=1'
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('model_id', ['selfswap', 'side'])
async def test_route_identity_pins_only_its_registration_and_closes_changed_stream_once(tmp_path, model_id):
    runtime = await owner(tmp_path)
    try:
        await runtime.command('ext-providerdynamic-enable', '')
        generation = runtime.generation
        process = runtime._manager._clients['providerdynamic'].process
        stream = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))(
            'ext-providerdynamic-live/' + model_id, Context(), StreamOptions())
        events = await stream.collect()
        result = await stream.result()
        assert events[-1].type == ('error' if model_id == 'selfswap' else 'done')
        text = ''.join(getattr(part, 'text', '') for part in result.content)
        assert 'FORBIDDEN-REPLACEMENT-CONTENT' not in text
        assert ('BEFORE-REPLACEMENT' if model_id == 'selfswap' else 'DYNAMIC-PROVIDER-ANSWER') in text
        assert runtime.generation == generation and not runtime._provider_lock.locked()
        assert process.returncode is None
        assert await runtime.command('ext-providerdynamic-count', '') == 'CALLS=1;CLOSED=1'
    finally:
        await runtime.aclose()


async def test_global_catalog_capacity_failure_reaps_worker_without_publishing_half_a_catalog(tmp_path):
    runtime = await owner(tmp_path)
    try:
        process = runtime._manager._clients['providerdynamic'].process
        with pytest.raises(ValueError, match='capacity'):
            await runtime.command('ext-providerdynamic-overflow', '')
        assert process.returncode is not None and not runtime.provider_models()
    finally:
        await runtime.aclose()


async def test_retired_or_stale_catalog_receipts_cannot_rewrite_live_owner(tmp_path):
    runtime = await owner(tmp_path)
    try:
        client = runtime._manager._clients['providerdynamic']
        old_revision, old_catalog = client._provider_snapshot
        await runtime.command('ext-providerdynamic-enable', '')
        before = runtime.provider_models()
        runtime.accept_provider_registration('providerdynamic', client,
                                             {'revision': old_revision, 'providers': old_catalog})
        assert runtime.provider_models() == before
        revision, catalog = client._provider_snapshot
        with pytest.raises(ValueError, match='Conflicting'):
            runtime.accept_provider_registration('providerdynamic', client, {'revision': revision, 'providers': []})
        assert runtime.provider_models() == before
        await runtime.manage('reload', 'providerdynamic')
        runtime.accept_provider_registration('providerdynamic', client,
                                             {'revision': revision + 100, 'providers': catalog})
        assert len(runtime.provider_models()) == 1
    finally:
        await runtime.aclose()


async def test_actual_route_uses_live_caps_not_a_stale_host_tool_reasoning_or_image_snapshot(tmp_path):
    from xdog.ai.types import ImageContent, Tool, UserMessage

    runtime = await owner(tmp_path)
    try:
        await runtime.command('ext-providerdynamic-enable', '')
        await runtime.command('ext-providerdynamic-replace', '')
        adapter = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))
        alias = 'ext-providerdynamic-live/echo'
        stale = Context(tools=(Tool(name='mutate', description='mutation', parameters={'type': 'object'}),))
        stream = adapter(alias, stale, StreamOptions(thinking='low'))
        assert (await stream.collect())[-1].type == 'done'
        image_context = Context(messages=(UserMessage(content=(
            ImageContent(data='aGVsbG8=', mime_type='image/png'),)),))
        rejected = adapter(alias, image_context, StreamOptions())
        assert (await rejected.collect())[-1].type == 'error'
        assert await runtime.command('ext-providerdynamic-count', '') == 'CALLS=1;CLOSED=1'
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('snapshot', [
    {'revision': True, 'providers': []}, {'revision': -1, 'providers': []},
    {'revision': 2**54, 'providers': []}, {'revision': 99, 'providers': [], 'approval': 'allow-all'},
    {'revision': 99, 'providers': [{'name': 'bad', 'models': []}]},
    {'revision': 99, 'providers': [{'name': 'bad', 'models': [
        {'id': 'echo', 'context_window': 1000, 'max_tokens': 100}], 'registration_id': 'foreign'}]},
])
async def test_invalid_live_metadata_is_not_applied(snapshot, tmp_path):
    runtime = await owner(tmp_path)
    try:
        client = runtime._manager._clients['providerdynamic']
        before = runtime.provider_models()
        with pytest.raises(ValueError):
            runtime.accept_provider_registration('providerdynamic', client, snapshot)
        assert runtime.provider_models() == before
    finally:
        await runtime.aclose()


async def test_invalid_provider_snapshot_on_custom_frame_reaps_its_owner_not_just_the_view(tmp_path, monkeypatch):
    from xdog.agent.mcp.stdio import StdioClient

    runtime = await owner(tmp_path)
    try:
        client = runtime._manager._clients['providerdynamic']
        process = client.process

        async def malformed(self, method, params, **kwargs):
            return {'xdog_provider_snapshot': {'revision': True, 'providers': []}}

        monkeypatch.setattr(StdioClient, 'request', malformed)
        with pytest.raises(ValueError, match='revision'):
            await runtime._manager._bridge(client.request('xdog/custom_frame', {}))
        assert process.returncode is not None and not runtime.provider_models()
    finally:
        await runtime.aclose()
