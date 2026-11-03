"""Runtime facts are queried on demand, never baked into the instruction prefix."""
from __future__ import annotations

import asyncio
from pathlib import Path

from xdog.ai.types import AssistantMessage, system_prompt_text
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.config import PlatformInfo
from xdog.coding.core.system_prompt import build_system_prompt


def test_runtime_contract_has_no_mutable_configuration_or_tool_operations():
    blocks = build_system_prompt(Path('/test'), PlatformInfo('Linux', 'test', '3.12', '/home/test', '/bin/bash'))
    text = system_prompt_text(blocks)
    assert '## Runtime' in text
    assert '- Application: xdog-coding' in text and '- Agent runtime: xdog.agent' in text
    assert 'host-reported configuration' in text and 'underlying model identity' in text
    assert 'ext_coding_session' not in text
    assert 'Thinking level:' not in text and 'Permission mode:' not in text and 'Session ID:' not in text


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
    for index, (model, blocks, thinking, info) in enumerate(calls):
        prompt = system_prompt_text(blocks)
        expected = 'copilot/first-model' if index == 0 else 'copilot/second-model'
        assert model == expected
        assert expected not in prompt
        assert 'ext_coding_session' not in prompt
        assert info['model']['id'] == expected
        assert info['model']['thinking'] == (thinking or 'off')
        assert info['permission_mode'] == ('ask' if index == 0 else 'deny')
        assert info['session_id'] == coding_session.session_id
        assert '- Application: xdog-coding' in prompt
        assert '- Agent runtime: xdog.agent' in prompt
    assert 'copilot/first-model' not in system_prompt_text(calls[1][1])
    host = [next(block for block in item[1] if block.text.startswith('# Coding host contract')) for item in calls]
    assert host[0] == host[1] and host[0].cache
    assert calls[1][2] is None
