"""RPC projects one owned batch and keeps its reader available for tool approval."""
import asyncio
import json
import queue

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream


async def test_rpc_projects_five_independent_inputs_and_one_shared_stream(coding_session):
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.modes.rpc.extensions import RpcExtensions
    from xdog.coding.modes.rpc.navigation import RpcNavigation
    from xdog.coding.modes.rpc.session_events import RpcSessionEvents

    output, calls, scopes = [], [], []
    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start(setup_pending=True)
    extensions = RpcExtensions(coding_session, output.append, navigation=RpcNavigation(coding_session, output.append))
    extensions.negotiate({'extension_dialogs': 1})
    observer = RpcSessionEvents(coding_session, runtime, extensions, output.append)

    def stream(model, context, options):
        calls.append(tuple(context.messages))
        scopes.append(extensions.scope)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='RPC-SHARED-ANSWER'),)))

    coding_session.agent.set_stream_fn(stream)
    output.clear()  # Capability negotiation is separate from runtime readiness.
    await runtime.inbox.snapshot()
    output.insert(0, {'type': 'ready'})
    observer.ready()
    await runtime.activate_inputs()
    entered, release = asyncio.Event(), asyncio.Event()

    async def control():
        entered.set()
        await release.wait()

    barrier = runtime.controller.control(control, name='test-rpc-batch')
    prompts = []
    try:
        await asyncio.wait_for(entered.wait(), 2)
        local = [runtime.controller.submit(SessionInput(f'human-{i}', 'human', 'RPC', f'human-{i}', (), {}),
                                           peer_id='rpc') for i in range(2)]
        for ticket in local:
            await asyncio.wrap_future(ticket.accepted)
        for i in range(3):
            message = InboxMessage(coding_session.session_id, coding_session.working_dir, f'callback-{i}',
                                   f'callback-{i}', ({'path': f'{i}.py'},), f'observer-{i}', {})
            prompts.append(asyncio.create_task(runtime.inbox.submit_prompt(message, peer_id=message.source)))
        async with asyncio.timeout(2):
            while sum(name == 'inbox-prompt-intake' for _, name, _ in runtime.controller._controls) < 3:
                await asyncio.sleep(0.01)
        release.set()
        tickets = local + list(await asyncio.gather(*prompts))
        results = await asyncio.wait_for(asyncio.gather(*(asyncio.wrap_future(t.result) for t in tickets)), 3)
        assert len(calls) == 1 and len(calls[0]) == 5
        batch_id, = {r.batch_id for r in results}
        ends = [e for e in output if e['type'] == 'message_end']
        assert len(ends) == 1, 'RPC must publish one terminal result for the shared batch'
        assert ends[0]['content'] == [{'type': 'text', 'text': 'RPC-SHARED-ANSWER'}]
        assert ends[0]['batch_id'] == batch_id and set(ends[0]['request_ids']) == {t.id for t in tickets}
        assert len([e for e in output if e['type'] == 'message_start']) == 1
        for phase in ('input_queued', 'input_completed'):
            rows = [e for e in output if e['type'] == 'session_event' and e['event'] == phase]
            assert len(rows) == 5 and {e['request_id'] for e in rows} == {t.id for t in tickets}
        assert scopes[0][1] == 'batch-' + batch_id
        assert extensions.scope is None and not observer.scopes
        assert output[0]['type'] == 'ready' and output[1]['type'] == 'inbox_snapshot'
    finally:
        release.set()
        await asyncio.wrap_future(barrier)
        await asyncio.gather(*prompts, return_exceptions=True)
        observer.close()
        await extensions.aclose()
        await asyncio.wrap_future(runtime.close())


@pytest.mark.parametrize('decision', ['allow_once', 'deny'])
async def test_actual_rpc_reader_approves_a_tool_for_a_mixed_batch(coding_session, tmp_path, monkeypatch, decision):
    from xdog.agent import AgentTool, AgentToolResult
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.modes.rpc import rpc_mode

    inputs, output = queue.Queue(), []
    ready, entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    target = tmp_path / 'tool-effect'

    async def execute(*args, **kwargs):
        target.write_text('approved')
        return AgentToolResult(content=(TextContent(text='written'),))

    coding_session.agent.set_tools([AgentTool(name='batch_write', description='write', parameters={}, execute=execute)])

    def stream(model, context, options):
        calls.append(tuple(context.messages))
        content = ((ToolCall(id='write', name='batch_write', arguments={}),) if len(calls) == 1
                   else (TextContent(text='MIXED-RESULT'),))
        return EventStream.empty(AssistantMessage(content=content))

    coding_session.agent.set_stream_fn(stream)

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
                if event['type'] == 'ready':
                    ready.set()
                elif event['type'] == 'permission_request':
                    assert not target.exists()
                    inputs.put({'type': 'permission_response', 'id': event['id'], 'decision': decision})
                elif event['type'] == 'message_end':
                    inputs.put({'type': 'quit'})
            return len(value)

        def flush(self):
            pass

    monkeypatch.setattr(rpc_mode.sys, 'stdin', Input())
    monkeypatch.setattr(rpc_mode.sys, 'stdout', Output())
    running = asyncio.create_task(rpc_mode.run_rpc_mode(coding_session))
    barrier, prompts = None, []

    async def control():
        entered.set()
        await release.wait()

    try:
        await asyncio.wait_for(ready.wait(), 2)
        runtime = coding_session.runtime
        async with asyncio.timeout(2):
            while not runtime.inbox._enabled:
                await asyncio.sleep(0.01)
        submit = runtime.controller.submit

        def paused_submit(value, *, peer_id, **kwargs):
            nonlocal barrier
            if peer_id == 'rpc' and barrier is None:
                barrier = runtime.controller.control(control, name='test-reader-mixed')
            return submit(value, peer_id=peer_id, **kwargs)

        monkeypatch.setattr(runtime.controller, 'submit', paused_submit)
        inputs.put({'type': 'prompt', 'message': 'human request'})
        await asyncio.wait_for(entered.wait(), 2)
        async with asyncio.timeout(2):
            while not runtime.controller.store.records():
                await asyncio.sleep(0.01)
        for i in range(2):
            message = InboxMessage(coding_session.session_id, tmp_path, f'm{i}', f'callback-{i}', (), 'Eyes', {})
            prompts.append(asyncio.create_task(runtime.inbox.submit_prompt(message, peer_id=f'peer-{i}')))
        async with asyncio.timeout(2):
            while sum(name == 'inbox-prompt-intake' for _, name, _ in runtime.controller._controls) < 2:
                await asyncio.sleep(0.01)
        release.set()
        assert await asyncio.wait_for(asyncio.shield(running), 5) == 0
        assert target.exists() is (decision == 'allow_once')
        assert len(calls) == 2 and len(calls[0]) == 3
        tool, = [e for e in output if e['type'] == 'tool_result']
        assert tool['is_error'] is (decision == 'deny')
        ends = [e for e in output if e['type'] == 'message_end']
        assert len(ends) == 1 and ends[0]['content'] == [{'type': 'text', 'text': 'MIXED-RESULT'}]
        assert len(ends[0]['request_ids']) == 3
        approval, = [e for e in output if e['type'] == 'permission_request']
        assert approval['batch_id'] == ends[0]['batch_id']
        assert approval['request_ids'] == ends[0]['request_ids']
        assert not any(e['type'] == 'error' for e in output), output
    finally:
        release.set()
        coding_session.permissions.deny_all()
        inputs.put({'type': 'quit'})
        if barrier is not None:
            await asyncio.wrap_future(barrier)
        if not running.done():
            running.cancel()
        await asyncio.gather(running, *prompts, return_exceptions=True)
