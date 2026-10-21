"""Runtime facts are queried on demand, never baked into the instruction prefix."""
from __future__ import annotations

import asyncio
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
def test_runtime_query_instructions_work_with_either_tool_description_mode(native_tool_calls):
    prompt = build_system_prompt(config(), [{'name': 'ext_coding_session'}], native_tool_calls=native_tool_calls)
    assert '## Runtime' in prompt
    assert '- Application: xdog-coding' in prompt
    assert '- Agent runtime: xdog.agent' in prompt
    assert 'copilot/configured-model' not in prompt
    assert 'Thinking level:' not in prompt and 'Permission mode:' not in prompt
    assert 'Call `ext_coding_session`' in prompt
    assert 'host-reported configuration' in prompt
    assert 'underlying model identity' in prompt
    assert 'Session ID:' not in prompt  # Standalone builders do not invent a session.


def test_session_id_is_not_baked_into_the_instruction_prefix():
    prompt = build_system_prompt(config(), [], session_id='host-session')
    assert 'host-session' not in prompt
    assert 'host-session' not in build_system_prompt(config(), [])


@pytest.mark.parametrize('thinking', ['off', 'high'])
def test_runtime_section_does_not_embed_mutable_config(thinking):
    prompt = build_system_prompt(replace(config(), model='opaque/model', thinking_level=thinking,
                                        permission_mode='deny'), [])
    assert 'opaque/model' not in prompt
    assert 'Thinking level:' not in prompt and 'Permission mode:' not in prompt
    assert 'copilot/configured-model' not in prompt


async def test_real_turn_queries_reflect_changes_without_embedding_them_in_instructions(coding_session):
    calls = []

    def capture(model, context, options):
        assert any(tool.name == 'ext_coding_session' for tool in coding_session.agent.active_tools)
        calls.append((model, context.system_prompt, options.thinking, coding_session.session_info()))
        return EventStream.empty(AssistantMessage(content=()))

    coding_session.agent.set_stream_fn(capture)
    coding_session.set_model('copilot/first-model')
    coding_session.set_thinking_level('medium')
    coding_session.permissions.set_mode('ask')
    await coding_session.send_message('First turn')

    await asyncio.to_thread(coding_session.set_model, 'copilot/second-model')
    await asyncio.to_thread(coding_session.set_thinking_level, None)
    coding_session.permissions.set_mode('deny')
    await coding_session.send_message('After switching')

    assert len(calls) == 2
    for index, (model, prompt, thinking, info) in enumerate(calls):
        expected = 'copilot/first-model' if index == 0 else 'copilot/second-model'
        assert model == expected
        assert expected not in prompt
        assert 'Call `ext_coding_session`' in prompt
        assert info['model']['id'] == expected
        assert info['model']['thinking'] == (thinking or 'off')
        assert info['permission_mode'] == ('ask' if index == 0 else 'deny')
        assert info['session_id'] == coding_session.session_id
        assert '- Application: xdog-coding' in prompt
        assert '- Agent runtime: xdog.agent' in prompt
    assert 'copilot/first-model' not in calls[1][1]
    assert calls[1][2] is None
