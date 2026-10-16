"""Mode adapters retain real outcomes and never invent an answer for handled input."""
import asyncio
import json
import queue

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream


async def drive_rpc(session, monkeypatch):
    from xdog.coding.modes.rpc import rpc_mode

    inputs, output = queue.Queue(), []
    inputs.put({'type': 'prompt', 'message': 'one input'})

    class Input:
        def readline(self, size=-1):
            return json.dumps(inputs.get(timeout=5)) + '\n'

    class Output:
        def write(self, value):
            for line in value.splitlines():
                if not line:
                    continue
                event = json.loads(line)
                output.append(event)
                if event['type'] in {'message_end', 'error'}:
                    inputs.put({'type': 'quit'})
            return len(value)
        def flush(self):
            pass

    monkeypatch.setattr(rpc_mode.sys, 'stdin', Input())
    monkeypatch.setattr(rpc_mode.sys, 'stdout', Output())
    async with asyncio.timeout(5):
        assert await rpc_mode.run_rpc_mode(session) == 0
    return output


@pytest.mark.parametrize('mode', ['sdk', 'print', 'rpc'])
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
    else:
        output = await drive_rpc(coding_session, monkeypatch)
        end, = [e for e in output if e['type'] == 'message_end']
        assert end['is_error'] and end['status'] == 'failed'
        assert end['content'] == [{'type': 'text', 'text': 'REAL-PARTIAL-ANSWER'}]
        errors = [e for e in output if e['type'] == 'error']
        assert len(errors) == 1 and 'post-model finalization failed' in errors[0]['message']


@pytest.mark.parametrize('mode', ['sdk', 'print', 'rpc'])
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
    else:
        output = await drive_rpc(coding_session, monkeypatch)
        end, = [e for e in output if e['type'] == 'message_end']
        assert end['input_handled'] and end['content'] == [] and not end.get('is_error')
        feedback, = [e for e in output if e['type'] == 'input_handled']
        assert feedback['feedback'] == 'SYSTEM-HANDLED-FEEDBACK'
    assert calls == [] and coding_session.messages == []
