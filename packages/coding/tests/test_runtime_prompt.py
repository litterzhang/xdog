"""Runtime facts must reach the model, not just the native UI or saved settings."""
from __future__ import annotations

from dataclasses import replace

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.config import PlatformInfo, RuntimeConfig
from xdog.coding.core.system_prompt import build_system_prompt


def config():
    return RuntimeConfig(model='copilot/configured-model', thinking_level='medium', permission_mode='ask',
        allowed_tools=(), custom_instructions='', extensions=(), working_dir='/test',
        platform_info=PlatformInfo('Linux', 'test', '3.12', '/home/test', '/bin/bash'))


@pytest.mark.parametrize('native_tool_calls', [False, True])
def test_runtime_identity_is_present_with_either_tool_description_mode(native_tool_calls):
    prompt = build_system_prompt(config(), [], native_tool_calls=native_tool_calls)
    assert '## Runtime' in prompt
    assert '- Application: xdog-coding' in prompt
    assert '- Agent runtime: xdog.agent' in prompt
    assert '- Selected provider/model: copilot/configured-model' in prompt
    assert '- Thinking level: medium' in prompt
    assert '- Permission mode: ask' in prompt
    assert 'host-reported configuration' in prompt
    assert 'underlying model identity' in prompt
    assert 'Session ID:' not in prompt  # Standalone builders do not invent a session.


def test_session_id_is_explicit_optional_host_metadata():
    prompt = build_system_prompt(config(), [], session_id='host-session')
    assert '- Session ID: host-session' in prompt
    assert 'host-session' not in build_system_prompt(config(), [])


@pytest.mark.parametrize('thinking', ['off', 'high'])
def test_runtime_section_uses_current_config_without_fallback(thinking):
    prompt = build_system_prompt(replace(config(), model='opaque/model', thinking_level=thinking,
                                        permission_mode='deny'), [])
    assert '- Selected provider/model: opaque/model' in prompt
    assert f'- Thinking level: {thinking}' in prompt
    assert '- Permission mode: deny' in prompt
    assert 'copilot/configured-model' not in prompt


async def test_real_turn_context_reflects_model_thinking_permission_changes(coding_session):
    calls = []

    def capture(model, context, options):
        calls.append((model, context.system_prompt, options.thinking))
        return EventStream.empty(AssistantMessage(content=()))

    coding_session.agent.set_stream_fn(capture)
    coding_session.set_model('copilot/first-model')
    coding_session.set_thinking_level('medium')
    coding_session.permissions.set_mode('ask')
    await coding_session.send_message('First turn')

    coding_session.set_model('copilot/second-model')
    coding_session.set_thinking_level(None)
    coding_session.permissions.set_mode('deny')
    await coding_session.send_message('After switching')

    assert len(calls) == 2
    for index, (model, prompt, thinking) in enumerate(calls):
        expected = 'copilot/first-model' if index == 0 else 'copilot/second-model'
        assert model == expected
        assert f'- Selected provider/model: {expected}' in prompt
        assert f'- Thinking level: {thinking or "off"}' in prompt
        assert f'- Permission mode: {"ask" if index == 0 else "deny"}' in prompt
        assert f'- Session ID: {coding_session.session_id}' in prompt
        assert '- Application: xdog-coding' in prompt
        assert '- Agent runtime: xdog.agent' in prompt
    assert 'copilot/first-model' not in calls[1][1]
    assert calls[1][2] is None
