"""Real Eyes -> coalescer -> Unix ACP -> original Agent, without user/UI polling."""
import asyncio
import json
import threading
from pathlib import Path

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.skills.goal.host import OperationContext

from packages.coding.tests.test_child_agents import create

_WAIT_TIMEOUT = 10


async def until(predicate):
    async with asyncio.timeout(_WAIT_TIMEOUT):
        while not await predicate():
            await asyncio.sleep(.01)


@pytest.mark.parametrize('blocker', ['child', 'draft', 'producer'])
async def test_completed_child_rechecks_deferred_real_eyes_cause_and_wakes_original_agent(tmp_path, blocker):
    session, _ = create(tmp_path, 'allow-all')
    runtime = session.enable_runtime(bound=True)
    runtime.start(own_thread=True)
    controller = runtime.controller
    start_write, finish_child = threading.Event(), threading.Event()
    entered = threading.Event()
    main_calls, child_calls = [], []

    def goal_result(context, identifier):
        result = next(message for message in context.messages if message.role == 'toolResult'
                      and message.tool_call_id == identifier)
        assert result.tool_name == 'goal' and not result.is_error
        return json.loads(''.join(part.text for part in result.content if part.type == 'text'))

    def model(_model, context, _options):
        tool = next(tool for tool in context.tools if tool.name == 'goal')
        actions = set(tool.parameters['properties']['action']['enum'])
        child = actions == {'tasks', 'task_update'}
        if context.messages[-1].role == 'toolResult':
            assert not context.messages[-1].is_error
        gate = None
        if child:
            child_calls.append(context)
            assert len(context.tools) == 1  # No bash, filesystem or parent-global Goal tool.
            assert tool.parameters['properties']['goal_id']['enum'] == [goal.id]
            assert tool.parameters['properties']['task_id']['enum'] == ['delayed']
            assert tool.parameters['properties']['task']['properties']['id']['enum'] == ['delayed']
            index = len(child_calls)
            if index == 1:
                action = {'action': 'tasks', 'goal_id': goal.id, 'task_id': 'delayed'}
                call_id = 'task-read'
            elif index == 2:
                observed = goal_result(context, 'task-read')
                assert observed['goal_id'] == goal.id
                task, = observed['tasks']
                assert task['id'] == 'delayed' and task['status'] == 'pending' and task['version'] == 1
                action = {'action': 'task_update', 'goal_id': goal.id, 'task': {
                    'id': task['id'], 'expected_version': task['version'], 'status': 'completed',
                    'summary': 'Real scoped Goal CAS write', 'evidence': ['isolated-eyes-child']}}
                call_id, gate = 'task-cas', start_write
            else:
                assert index == 3
                updated = goal_result(context, 'task-cas')
                assert updated['id'] == 'delayed' and updated['status'] == 'completed' and updated['version'] == 2
                action, call_id, gate = None, '', finish_child
            message = AssistantMessage(content=(ToolCall(id=call_id, name='goal', arguments=action),)
                if action else (TextContent(text='child done'),), stop_reason='toolUse' if action else 'stop')
        else:
            assert {'report', 'activate', 'complete'} <= actions
            main_calls.append(context)
            index = len(main_calls)
            if index > 1:
                assert not session._user_operation_authorized  # Original automatic ACP input, not a user turn.
            if index == 2:
                action = {'action': 'status', 'goal_id': goal.id, 'detail': True}
            elif index == 3:
                # The original main Agent reads the authoritative task/result
                # and persists its assessment BEFORE releasing this tasks wait.
                status = goal_result(context, 'parent-2')
                current = status['current']
                assert current['id'] == goal.id and current['status'] == 'waiting'
                assert current['wait_for'] == 'tasks'
                assert status['tasks'][0]['id'] == 'delayed'
                assert status['tasks'][0]['status'] == 'completed' and status['tasks'][0]['version'] == 2
                assert status['tasks'][0]['evidence'] == ['isolated-eyes-child']
                action = {'action': 'report', 'goal_id': goal.id, 'report': {
                    'expected_version': 0, 'progress': 'baseline',
                    'summary': 'Actual automatic task-change input received', 'completed': ['Read task'],
                    'evidence': ['CAS task version 2'], 'blockers': [], 'next_steps': ['Activate and complete test']}}
            elif index == 4:
                report = goal_result(context, 'parent-3')
                assert report['version'] == 1 and report['progress'] == 'baseline'
                # Report CAS is separate from Goal revision and cannot release
                # a wait; activate still needs the observed Goal version/reason.
                current = goal_result(context, 'parent-2')['current']
                action = {'action': 'activate', 'goal_id': current['id'], 'version': current['version'],
                          'reason': 'Read assigned task delayed: completed with CAS version 2 and evidence'}
            elif index == 5:
                active = goal_result(context, 'parent-4')
                assert active['id'] == goal.id and active['status'] == 'active' and not active['wait_for']
                action = {'action': 'complete', 'goal_id': goal.id, 'summary': 'Verified automatic continuation'}
            else:
                action = None
            message = AssistantMessage(content=(ToolCall(id=f'parent-{index}', name='goal', arguments=action),)
                if action else (TextContent(text='main done'),), stop_reason='toolUse' if action else 'stop')

        async def events():
            if child and gate is not None:
                if gate is start_write:
                    entered.set()  # Child has read the version and is about to perform its scoped CAS.
                while not gate.is_set():
                    await asyncio.sleep(.002)
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)

    async def read(operation):
        # No controller control/owner.available signals: this only observes test
        # facts on the original loop, so cannot accidentally repair a missing hint.
        return await asyncio.wrap_future(controller._async(operation))

    try:
        session.agent.set_stream_fn(model)
        await session.send_message('Prepare original owner')
        goal_runtime = session.capabilities.get('goal')
        controller.set_human_pending(True)  # Keep initial setup causes deferred.
        async def prepare():
            goal = await goal_runtime.service.create('Eyes actual child test', 'Wake the original main Agent',
                                                     context=OperationContext(True))
            await goal_runtime.service.task({'id': 'delayed', 'title': 'Only child writes', 'expected_version': 0})
            await goal_runtime.service.change('wait', until='tasks', reason='Wait for real task change')
            await goal_runtime.flush()
            return goal
        goal = await asyncio.wrap_future(controller.control(prepare, name='test-setup'))
        assert goal_runtime.automation['status'] == 'watching'
        child_config = {'tools': ['goal'], 'skills': [],
                        'tool_scopes': {'goal': {'goal_id': goal.id, 'task_ids': ['delayed']}}}
        child_id = await session.children.runtime.start('Update assigned task', access='inherit', config=child_config)
        # Readiness includes real scoped-tool IO and thread scheduling, not a
        # three-second performance contract. Keep the same bound as later waits.
        assert await asyncio.to_thread(entered.wait, _WAIT_TIMEOUT), session.children.runtime.capture_source(child_id)
        if blocker == 'producer':
            controller.hold_producer('unattended-test-operation')
        if blocker != 'draft':
            controller.set_human_pending(False)
        before = await read(lambda: goal_runtime.service.store.wake_state(goal.id))
        start_write.set()
        async def changed():
            now = await goal_runtime.service.store.wake_state(goal.id)
            return now['task_sequence'] > before['task_sequence']
        await until(lambda: read(changed))
        assert session.children.runtime.active
        assert len(main_calls) == 1
        assert await read(goal_runtime.service.store.reminders) == ()
        finish_child.set()  # No wait/control call, new user input or main prompt.
        if blocker != 'child':
            async def child_settled():
                return not session.children.runtime.active
            await until(lambda: read(child_settled))
            assert len(main_calls) == 1
            assert await read(goal_runtime.service.store.reminders) == ()
            if blocker == 'draft':
                controller.set_human_pending(False)
            else:
                controller.release_producer('unattended-test-operation')
        async def complete():
            current = await goal_runtime.service.current()
            return (current is not None and current.id == goal.id and current.status == 'completed'
                    and controller.execution_snapshot()['state'] == 'idle')
        await until(lambda: read(complete))
        assert session.children.runtime.capture_source(child_id).status == 'completed'
        saved = await read(lambda: goal_runtime.service.summary())
        assert saved['current']['id'] == goal.id and saved['current']['status'] == 'completed'
        assert saved['automation']['status'] == 'idle' and goal_runtime._observer is None
        assert saved['latest_report']['version'] == 1 and saved['latest_report']['progress'] == 'baseline'
        assert saved['latest_report']['evidence'] == ['CAS task version 2']
        history = await read(goal_runtime.service.list)
        assert history[0].status == 'completed'
        reminders = await read(goal_runtime.service.store.reminders)
        assert len(reminders) == 1 and reminders[0]['status'] == 'accepted'
        payload = json.loads(reminders[0]['payload'])
        assert payload['provenance']['goal_id'] == goal.id
        assert any(cause.startswith('tasks:') for cause in payload['provenance']['triggers'])
        records = await read(lambda: records_async(controller))
        assert len(records) == 2 and records[0].input.origin == 'human'
        assert records[1].input.source == 'goal' and records[1].input.origin != 'human'
        assert records[1].status == 'succeeded'
        # ACP message identity and execution request/attempt identities are
        # deliberately distinct; pin the actual original Inbox mapping, not a
        # fabricated equality or a second execution/receiver.
        receipts = await read(lambda: receipts_async(controller))
        receipt, = receipts
        assert receipt.status == 'succeeded' and len(receipt.attempts) == 1
        assert receipt.message.message_id == reminders[0]['id'] == payload['id']
        assert receipt.message.session_id == session.session_id and receipt.message.cwd == tmp_path.resolve()
        assert receipt.request_id == records[1].input.id
        assert receipt.attempt_id == records[1].input.provenance['inbox']['attempt_id']
        assert receipt.message.message_id == records[1].input.provenance['inbox']['message_id']
        assert receipt.branch_id == records[1].input.provenance['inbox']['branch_id'] == controller.branch
        assert receipt.owner_generation == controller.generation and receipt.batch_id == records[1].batch_id
        assert receipt.message.provenance == records[1].input.provenance['submitted'] == payload['provenance']
        child_record, = session.children.store.load()
        assert child_record['config']['tools'] == ['goal']
        assert child_record['config']['tool_scopes'] == child_config['tool_scopes']
        assert len(child_calls) == 3
        child_results = [message for message in child_calls[-1].messages if message.role == 'toolResult']
        assert [(result.tool_call_id, result.tool_name, result.is_error) for result in child_results] == [
            ('task-read', 'goal', False), ('task-cas', 'goal', False)]
        task = json.loads((Path(tmp_path) / '.xdog/goals' / goal.id / 'tasks/delayed.json').read_text())
        assert task['status'] == 'completed' and task['version'] == 2
        assert len(main_calls) == 6  # prepare, status, report, activate, complete, final text
        # Passively observe the real Eyes/ACP/settlement chain: no new user turn,
        # manual wake, child wait/control or owner-available signal repairs it.
        await asyncio.sleep(.6)  # Also span the configured 500ms task detector debounce.
        assert len(main_calls) == 6
        assert await read(goal_runtime.service.store.reminders) == reminders
        assert await read(lambda: records_async(controller)) == records
        assert await read(lambda: receipts_async(controller)) == receipts
        assert await read(complete)
    finally:
        start_write.set()
        finish_child.set()
        await session.aclose()


async def records_async(controller):
    return controller.store.records()


async def receipts_async(controller):
    # Read durable receipt facts off-loop; do not publish an Inbox snapshot or
    # inject an owner-available hint that could conceal a missing automatic wake.
    inbox = controller.inbox
    return await asyncio.to_thread(inbox.store.records, inbox.session_id, inbox.cwd)
