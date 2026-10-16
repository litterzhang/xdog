"""Mode adapters retain real outcomes and never invent an answer for handled input."""
import json

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream


@pytest.mark.parametrize('mode', ['sdk', 'print'])
async def test_post_model_finalization_error_is_not_success(coding_session, monkeypatch, capsys, mode):
    coding_session.agent.set_stream_fn(lambda *_: EventStream.empty(
        AssistantMessage(content=(TextContent(text='REAL-PARTIAL-ANSWER'),))))

    def fail():
        raise OSError('post-model finalization failed')

    monkeypatch.setattr(coding_session, '_persist', fail)
    if mode == 'sdk':
        with pytest.raises(OSError, match='post-model finalization failed'):
            await coding_session.send_message('one input')
    elif mode == 'print':
        from xdog.coding.modes.print_mode import run_print_mode
        assert await run_print_mode(coding_session, 'one input') == 1
        assert 'post-model finalization failed' in capsys.readouterr().err


@pytest.mark.parametrize('mode', ['sdk', 'print'])
async def test_handled_input_is_system_feedback_without_model_or_assistant(coding_session, monkeypatch, capsys, mode):
    from xdog.agent import HandledInput

    calls = []
    coding_session.agent.set_stream_fn(lambda *_: calls.append(True) or EventStream.empty(AssistantMessage()))

    async def handled(message, source, cancel=None):
        return HandledInput(feedback='SYSTEM-HANDLED-FEEDBACK')
    coding_session.agent.set_input_transform(handled)
    if mode == 'sdk':
        assert await coding_session.send_message('one input') is None
        assert coding_session.handled_feedback == ['SYSTEM-HANDLED-FEEDBACK']
    elif mode == 'print':
        from xdog.coding.modes.print_mode import run_print_mode
        assert await run_print_mode(coding_session, 'one input', output_format='json') == 0
        result = json.loads(capsys.readouterr().out)
        assert result['role'] == 'system' and result['assistant'] is None
        assert result['feedback'] == ['SYSTEM-HANDLED-FEEDBACK']
    assert calls == [] and coding_session.messages == []
