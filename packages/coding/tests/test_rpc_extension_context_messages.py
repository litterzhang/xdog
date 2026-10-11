"""Next-turn custom context requires successful command delivery and a later user prompt."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.rpc_mode import _handle_prompt

from packages.coding.tests.test_rpc_compaction import wait
from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'withdraw', 'old-client', 'delivery-fail'])
async def test_context_persistence_waits_complete_command_result_then_next_explicit_user_prompt(tmp_path, ending):
    owner, _ = session(tmp_path, extra=('contextrequest',))
    events, calls = [], []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='NEXT USER ANSWER'),)))
    owner.agent.set_stream_fn(stream)
    entered, release = asyncio.Event(), asyncio.Event()
    async def send(event):
        entered.set()
        await release.wait()
        if ending == 'delivery-fail':
            raise OSError('Injected incomplete command-result delivery')
        events.append(event)
    nav = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=nav, send=send)
    controller.negotiate({'extension_context_messages': int(ending != 'old-client')})
    try:
        controller.start({'request_id': 'owned', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-contextrequest-request', 'args': json.dumps({'fail': ending == 'fail'})}, turn_running=False)
        if ending != 'old-client':
            await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
            event = next(row for row in events if row['type'] == 'extension_dialog')
            assert event['dialog']['operation'] == 'context_message'
            response = {'id': event['dialog']['id'], 'request_id': event['request_id'],
                        'session_id': event['session_id'], 'scope': event['scope'], 'value': ending != 'deny'}
            with pytest.raises(ValueError):
                controller.answer({**response, 'scope': 'foreign'})
            controller.answer(response)
        await wait(entered.is_set)
        assert not (await owner.context_messages.capture())[1] and not owner.messages and not calls
        if ending == 'withdraw':
            controller.negotiate({'extension_context_messages': 0})
            controller.negotiate({'extension_context_messages': 1})
        release.set()
        await asyncio.gather(controller.task, return_exceptions=True)
        assert not owner.messages and not calls
        pending = (await owner.context_messages.capture())[1]
        assert bool(pending) is (ending == 'apply')
        if ending == 'apply':
            async with controller.bind('extension-prompt'):
                await _handle_prompt(owner, 'Not a manually submitted prompt', events.append, input_origin='extension')
            assert len(calls) == 1 and 'RETAINED-CUSTOM-CONTENT' not in str(calls[0].messages)
            assert (await owner.context_messages.capture())[1] == pending
            async with controller.bind('user-prompt'):
                await _handle_prompt(owner, 'Actual next user prompt', events.append)
            assert not (await owner.context_messages.capture())[1]
            assert len([row for row in owner.messages if isinstance(row, CustomAgentMessage)]) == 1
            assert 'RETAINED-CUSTOM-CONTENT' in str(calls[-1].messages)
            assert 'PRIVATE-NOT-MODEL' not in str(calls[-1].messages)
        assert owner.permissions.mode == 'deny'
    finally:
        release.set()
        await controller.aclose()
        await nav.aclose()
        await close(owner)
