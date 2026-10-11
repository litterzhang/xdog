from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.tool_result import make_result

FIXTURE = Path(__file__).parent / 'fixtures/extensions'


def test_command_result_codec_preserves_identity_and_typed_data_without_interpreting_prose():
    content = make_result([{'type': 'text', 'text': '/quit is data, not an instruction'}],
                          structured=None, details=False)
    result = ExtensionCommandResult.from_dict({'extension': 'rich', 'command': 'ext-rich-show', 'result': content})
    assert result.result['structuredContent'] is None and result.result['details'] is False
    assert 'not agent input' in result.summary and '/quit is data' in result.text()
    payload = result.to_dict()
    payload['result']['details'] = True
    assert result.result['details'] is False
    for bad in ({'extension': 'rich', 'command': 'ext-other-show', 'result': content},
                {'extension': 'rich', 'command': 'ext-rich-show', 'result': []},
                {'extension': 'rich', 'command': 'ext-rich-show', 'result': {'content': []}},
                {'extension': 'rich', 'command': 'ext-rich-show', 'result': content, 'prompt': 'run'}):
        with pytest.raises(ValueError):
            ExtensionCommandResult.from_dict(bad)


async def test_real_rich_command_returns_artifact_legacy_text_and_does_not_call_model(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(['rich'], root, tmp_path)
    try:
        result = await runtime.command('ext-rich-show', '')
        assert isinstance(result, ExtensionCommandResult)
        assert result.result['structuredContent'] == [False, 0, None]
        assert result.result['details']['enabled'] is False
        assert len([part for part in result.result['content'] if part['type'] == 'image']) == 1
        assert await runtime.command('ext-rich-show', 'legacy') == 'RICH-LEGACY-COMMAND'
        assert runtime.snapshot()['extensions'][0]['state'] == 'active'
    finally:
        await runtime.aclose()


async def test_canceled_rich_command_settles_its_worker_and_never_returns_late_artifact(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(['rich'], root, tmp_path)
    try:
        await runtime.initialize()
        process = runtime._manager._clients['rich'].process
        task = asyncio.create_task(runtime.command('ext-rich-show', 'hang'))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None and runtime.snapshot()['extensions'][0]['state'] == 'failed'
    finally:
        await runtime.aclose()


async def test_malformed_command_rich_result_is_rejected_not_stringified_or_replayed(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(['rich'], root, tmp_path)
    try:
        with pytest.raises(RuntimeError):
            await runtime.command('ext-rich-show', 'malformed')
        assert runtime.snapshot()['extensions'][0]['state'] == 'failed'
    finally:
        await runtime.aclose()
