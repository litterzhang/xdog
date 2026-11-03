"""Exercise actual request schemas, prompt cache blocks and failed skill selection."""
from __future__ import annotations

import pytest
from xdog.agent import AgentTool
from xdog.ai.types import AssistantMessage, TextContent, system_prompt_text
from xdog.ai.utils.event_stream import EventStream


@pytest.mark.parametrize('capability', [None, False, True])
async def test_actual_requests_advertise_tools_once_and_inspection_is_not_execution(coding_session, capability):
    session, contexts = coding_session, []
    marker = AgentTool('marker-tool', description='UNIQUE-TOOL-DESCRIPTION')
    disabled = AgentTool('disabled-tool', description='DISABLED-TOOL-SECRET')
    session._publish_tools([*session.agent.local_tools, marker, disabled])
    session.agent.set_active_tools(['marker-tool'], expected_revision=session.agent.tool_selection_revision)
    session.agent.set_model_capabilities(context_window=session.agent.context_window,
        max_prompt_tokens=session.agent.max_prompt_tokens, supports_tool_calls=capability)
    def stream(_model, context, _options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='answer'),)))
    session.agent.set_stream_fn(stream)
    await session.send_message('Inspect available tools')
    assert len(contexts) == 1
    context = contexts[0]
    text = system_prompt_text(context.system_prompt)
    assert 'DISABLED-TOOL-SECRET' not in text
    assert ('UNIQUE-TOOL-DESCRIPTION' in text) is (capability is False)
    assert bool(context.tools) is (capability is not False)
    if context.tools:
        assert {tool.name for tool in context.tools} == {'marker-tool'}
        assert context.tools[0].description == marker.description
    else:
        assert 'inspection only' in text and 'cannot call native tools' in text
    fixed = next(block for block in context.system_prompt if block.text.startswith('# Coding host contract'))
    assert fixed.cache and 'UNIQUE-TOOL-DESCRIPTION' not in fixed.text


@pytest.mark.parametrize('operation', ['activate', 'deactivate'])
@pytest.mark.parametrize('failure', ['load', 'apply', 'prompt'])
def test_failed_skill_selection_keeps_host_and_sdk_state(coding_session, monkeypatch, operation, failure):
    session = coding_session
    repo = session.agent._skills
    repo.save_skill('guard', 'GUARD-BODY')
    repo.save_skill('second', 'SECOND-BODY')
    session.activate_skill('guard')
    if operation == 'deactivate':
        session.activate_skill('second')
    original_host = session.active_skills
    original_sdk = session.agent.active_skills
    original_prompt = session.agent.state.system_prompt
    def fail(*_args, **_kwargs):
        raise OSError('selection unavailable')
    if failure == 'load':
        monkeypatch.setattr(repo, 'load_skill', fail)
    elif failure == 'apply':
        monkeypatch.setattr(session.agent, 'set_active_skills', fail)
    else:
        monkeypatch.setattr(session, '_prompt_blocks', fail)
    with pytest.raises(OSError, match='selection unavailable'):
        if operation == 'activate':
            session.activate_skill('second')
        else:
            session.deactivate_skill('second')
    assert session.active_skills == original_host
    assert session.agent.active_skills == original_sdk
    assert session.agent.state.system_prompt == original_prompt


def test_successful_skill_selection_uses_one_sdk_body_without_ui_instructions(coding_session):
    session = coding_session
    session.agent._skills.save_skill('guard', 'GUARD-ONCE', description='A reusable constraint')
    session.agent.set_active_skills(())
    session._rebuild_system_prompt()
    initial = session.agent.state.system_prompt
    session.activate_skill('guard')
    text = system_prompt_text(session.agent.state.system_prompt)
    assert text.count('GUARD-ONCE') == 1
    assert 'The user activated these' not in text and '/unload' not in text
    assert session.active_skills == frozenset({'guard'}) and session.agent.active_skills == ('guard',)
    assert session.deactivate_skill('guard')
    assert session.agent.state.system_prompt == initial


async def test_failed_activation_cannot_report_success_through_slash_command(coding_session, monkeypatch):
    from xdog.coding.core import slash_commands

    session = coding_session
    session.agent._skills.save_skill('guard', 'GUARD', description='A constraint')
    monkeypatch.setattr(slash_commands, 'skill_repo', lambda: session.agent._skills)
    def fail(*_args, **_kwargs):
        raise OSError('selection unavailable')
    monkeypatch.setattr(session.agent, 'set_active_skills', fail)
    with pytest.raises(OSError, match='selection unavailable'):
        await slash_commands.execute_command('guard', 'do not start this work', session)
    assert session.active_skills == frozenset() and session.agent.active_skills == ()
