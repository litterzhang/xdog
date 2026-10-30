"""Real ACP -> main Goal tool -> message tool -> Weixin/output receipt correlation."""
from __future__ import annotations

import asyncio
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from xdog.agent.acp.client import enqueue
from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.acp.server import ClawAcpServer
from xdog.claw.channels.weixin.channel import WeixinChannel
from xdog.claw.channels.weixin.context_tokens import clear_context_tokens, set_context_token
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group
from xdog.skills.goal.host import OperationContext

from packages.claw.tests.test_weixin_application_intake import ChannelApi, sent_parts

QUESTION = {
    'question': 'Which release should I prepare, A or B?',
    'impact': 'The release cannot proceed without this decision.',
    'resume_when': 'The actual user selects a release.',
}
ORDINARY = 'Question recorded; ordinary completion stays on ACP.'
QUESTION_TEXT = '\n'.join(QUESTION.values())


def result_data(message):
    text = ''.join(part.text for part in message.content if isinstance(part, TextContent))
    assert not message.is_error, text
    return json.loads(text)


class QuestionModel:
    """Only the model is scripted; all tool calls go through the real main Agent."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []
        self.results = {}
        self.reference = None

    def __call__(self, model, context, options=None):
        last = context.messages[-1]
        if last.role == 'user':
            call = ToolCall(id='wait-user', name='goal', arguments={'action': 'wait', 'until': 'user', **QUESTION})
        else:
            assert isinstance(last, ToolResultMessage)
            data = self.results[last.tool_call_id] = result_data(last)
            if last.tool_call_id == 'wait-user':
                assert data['status'] == 'waiting' and data['wait_for'] == 'user'
                # wait returns the Goal record; summary exposes the stable
                # question_record ID. Never manufacture a reference from tool-call IDs.
                call = ToolCall(id='before-send', name='goal', arguments={'action': 'summary'})
            elif last.tool_call_id == 'before-send':
                self.reference = data['question']['id']
                assert self.reference == data['question_record']['id']
                assert data['question_record']['delivery_status'] == 'untracked'
                call = ToolCall(id='channels', name='message', arguments={'action': 'channels'})
            elif last.tool_call_id == 'channels':
                channel, = data['channels']
                assert set(channel) == {'id', 'name'} and channel['name'] == 'weixin'
                call = (ToolCall(id='after-send', name='goal', arguments={'action': 'summary'})
                        if self.outcome == 'untracked' else
                        ToolCall(id='send-question', name='message', arguments={
                            'action': 'send', 'channel': channel['id'], 'text': QUESTION_TEXT,
                            'reference': self.reference}))
            elif last.tool_call_id == 'send-question':
                assert data['status'] == self.outcome
                assert data['default_reply_route_changed'] is False
                call = ToolCall(id='after-send', name='goal', arguments={'action': 'summary'})
            else:
                assert last.tool_call_id == 'after-send'
                assert data['question_record']['id'] == self.reference
                assert data['question_record']['delivery_status'] == self.outcome
                self.calls.append('ordinary')
                return EventStream.empty(AssistantMessage(content=(TextContent(text=ORDINARY),)))
        self.calls.append(call.id)
        return EventStream.empty(AssistantMessage(content=(call,), stop_reason='toolUse'))


@asynccontextmanager
async def question_host(tmp_path, outcome):
    model = QuestionModel(outcome)
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=model)
    app.register_group(Group(id='main', workspace=str(tmp_path), enabled_tools=('message', 'goal')))
    account = 'goal-question-delivery'
    clear_context_tokens(account)
    wx = WeixinChannel(tmp_path / 'weixin', account, 'https://example.invalid', '')
    await wx._api_client.close()
    api = ChannelApi()
    if outcome == 'unknown':
        async def ack_lost(request):
            api.sent.put_nowait(request.msg)
            raise ConnectionError('Upstream accepted the question; acknowledgement lost')
        api.send_message = ack_lost
    wx._api_client = api
    wx._proactive_recipient = lambda: 'original-user'
    set_context_token(account, 'original-user', 'original-context')
    server = None
    try:
        # Initialize capabilities before registering Weixin: no monitor/network is
        # started. The live outbound binding still uses the real WeixinChannel.
        await app.start()
        app.add_channel(wx)
        with tempfile.TemporaryDirectory(prefix='goal-q-') as private:
            server = await ClawAcpServer.start(app, Path(private) / 'acp.sock')
            runtime = app._runtimes['main']
            binding = runtime.capabilities.get('goal')
            # Drain actual endpoint/owner readiness jobs before creating a Goal,
            # so a startup reminder cannot steal this test's explicit ACP input.
            await binding.flush()
            assert not model.calls
            target = await runtime._capability_host.acp_target()
            assert target is not None
            goal = await binding.service.create('Release decision', 'Prepare the selected release',
                                                context=OperationContext(True))
            try:
                yield app, runtime, binding, goal, model, api, target
            finally:
                await server.close()
                server = None
    finally:
        if server is not None:
            await server.close()
        await app.stop()
        clear_context_tokens(account)


@pytest.mark.parametrize('outcome', ['delivered', 'unknown', 'untracked'])
async def test_automatic_goal_question_uses_actual_message_output_receipt(tmp_path, outcome):
    async with question_host(tmp_path, outcome) as (app, runtime, binding, goal, model, api, target):
        scope = InputScope('main', tmp_path)
        # These are untrusted labels, not authority or output evidence. In the
        # untracked case they must not turn a saved question into a delivered one.
        forged_reference = f'goal-question:{goal.id}:{goal.version + 1}'
        message = SessionInput('ask-user', 'human', 'message', 'Ask the user about the release.', (), {
            'human_authorized': True, 'user_authorized': True, 'reference': forged_reference,
            'outbound_tool': 'message', 'delivery_status': 'delivered',
        })
        receipt = await enqueue(target, message)
        assert receipt['status'] == 'queued', receipt
        inputs = await app.input_service()
        record = await asyncio.wait_for(inputs.wait(scope, message.id), 10)
        assert record.status == 'succeeded', record.error
        assert record.envelope.human_authorized is False
        assert record.envelope.message.origin == 'acp'
        assert record.envelope.message.source == 'message'
        assert record.envelope.context.get('outbound_tool') is None
        assert record.envelope.message.provenance['reference'] == forged_reference
        assert record.envelope.reply is None and record.response == ORDINARY
        assert len(await inputs.store.records(scope)) == 1
        assert not runtime._active_session._user_operation_authorized
        await binding.flush()

        waiting = await binding.service.current()
        assert waiting.id == goal.id and waiting.status == 'waiting' and waiting.wait_for == 'user'
        assert {key: getattr(waiting, key) for key in QUESTION} == QUESTION
        assert model.reference == forged_reference
        history = await binding.service.store.history(goal.id)
        outputs = await app.output_service()
        rows = await outputs.store.records()
        facts = await runtime._capability_host.message_delivery(model.reference)
        if outcome == 'untracked':
            assert rows == [] and api.sent.empty()
            assert facts['status'] == 'untracked'
            expected_outputs = []
        else:
            row, = rows
            assert row.status == outcome
            assert row.envelope.scope == scope and not row.envelope.human_authorized
            assert row.envelope.context['outbound_tool'] == 'message'
            assert row.envelope.message.provenance['input_id'] == message.id
            assert row.envelope.message.provenance['reference'] == model.reference
            assert row.binding == model.results['channels']['channels'][0]['id']
            sent = api.sent.get_nowait()
            assert sent_parts(sent) == ('original-user', 'original-context', QUESTION_TEXT)
            assert sent.client_id == row.id and api.sent.empty()
            expected_outputs = [{'id': row.id, 'status': outcome}]
            assert facts == {'status': outcome, 'outputs': expected_outputs}
            assert model.results['send-question']['outputs'] == expected_outputs

        summary = await binding.service.summary()
        assert summary['question']['id'] == model.reference
        assert summary['question']['availability'] == 'recorded'
        assert summary['question']['text'] == QUESTION['question']
        assert summary['question']['impact'] == QUESTION['impact']
        assert summary['question']['resume_when'] == QUESTION['resume_when']
        assert summary['question']['delivery']['status'] == outcome
        assert summary['question']['delivery']['outputs'] == expected_outputs
        assert summary['question_record']['delivery_status'] == outcome
        assert summary['next']['owner'] == 'user'
        assert model.results['after-send']['question']['delivery'] == summary['question']['delivery']
        assert model.calls == ['wait-user', 'before-send', 'channels', *(
            [] if outcome == 'untracked' else ['send-question']), 'after-send', 'ordinary']
        # Reads and recovery must neither change the wait nor resend unknown work.
        await outputs.resume()
        await binding.flush()
        assert await binding.service.current() == waiting
        assert await binding.service.store.history(goal.id) == history
        assert await outputs.store.records() == rows and api.sent.empty()
        assert len(await inputs.store.records(scope)) == 1  # wait(user) stops automatic continuation.
        assert runtime._active_session.agent.tool_context['_message_port'] is None
