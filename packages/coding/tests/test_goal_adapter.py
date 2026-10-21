"""The installed Goal records the main Agent's decisions; no role executor or verdict layer."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.skills.goal.host import OperationContext

from packages.coding.tests.test_child_agents import create


def scripted(contexts, *, try_write=False, actions=()):
    def stream(model, context, options):
        contexts.append(context)
        start = max((i for i, m in enumerate(context.messages) if m.role == 'user'), default=-1)
        turn = sum(m.role == 'assistant' for m in context.messages[start + 1:])
        calls = ([('filesystem', {'action': 'write', 'path': 'forbidden.txt', 'content': 'not approved'})]
                 if try_write else []) + [('goal', action) for action in actions]
        parts = ((ToolCall(id='call-' + str(turn), name=calls[turn][0], arguments=calls[turn][1]),)
                 if turn < len(calls) else (TextContent(text='main Agent finished'),))
        message = AssistantMessage(content=parts, stop_reason='toolUse' if isinstance(parts[0], ToolCall) else 'stop')
        async def events():
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)
    return stream


async def owned(controller, operation):
    return await asyncio.wrap_future(controller.control(operation, name='goal-host-test'))


async def setup(tmp_path, *, mode='ask', try_write=False):
    session, _ = create(tmp_path, mode)
    tmp_path.joinpath('result.txt').write_text('real output')
    contexts = []
    session.agent.set_stream_fn(scripted(contexts, try_write=try_write))
    await session.send_message('Ready')
    return session, session.capabilities.get('goal'), contexts


async def close(session, host=None):
    await session.aclose()


async def test_main_agent_updates_authoritative_plan_tasks_and_completes_without_children(tmp_path):
    session, host, contexts = await setup(tmp_path, mode='allow-all')
    try:
        goal = await owned(session._sdk_controller, lambda: host.service.create(
            'Output', 'Inspect result', ('Result exists',), context=OperationContext(True)))
        actions = ({'action': 'plan', 'content': 'Read result.txt and record evidence'},
                   {'action': 'task_update', 'task': {'id': 'inspect', 'title': 'Inspect result',
                    'expected_version': 0}},
                   {'action': 'task_update', 'task': {'id': 'inspect', 'expected_version': 1,
                    'status': 'completed', 'summary': 'Read actual result', 'evidence': ['result.txt']}},
                   {'action': 'complete', 'summary': 'Main Agent verified the evidence'})
        session.agent.set_stream_fn(scripted(contexts, actions=actions))
        await session.send_message('Perform this main Agent plan')
        status = await owned(session._sdk_controller, lambda: host.service.status(goal.id))
        assert status['current']['status'] == 'completed' and status['tasks'][0]['status'] == 'completed'
        assert status['tasks'][0]['worker'] == 'main' and status['plan'].startswith('Read result.txt')
        task = json.loads((Path(status['task_dir']) / 'inspect.json').read_text())
        assert task['format'] == 'xdog.goal_task.v2' and task['version'] == 2
        assert await owned(session._sdk_controller, host.service.current) is None
        assert not session.children.supervisor.active and not session.children.supervisor._children
        assert all(not {'task_report', 'goal_context'}.intersection(t.name for t in c.tools) for c in contexts)
        assert not hasattr(host.service, 'assign') and not hasattr(host.service, 'request_complete')
    finally:
        await close(session)


@pytest.mark.parametrize('mode', ['ask', 'deny', 'allow-all'])
async def test_model_goal_tool_and_filesystem_keep_actual_permission_policy(tmp_path, mode):
    session, host, contexts = await setup(tmp_path, mode=mode, try_write=True)
    try:
        goal = await owned(session._sdk_controller, lambda: host.service.create(
            'Output', 'Inspect', context=OperationContext(True)))
        session.agent.set_stream_fn(scripted(contexts, actions=({'action': 'task_update',
            'task': {'id': 'work', 'title': 'Main work', 'expected_version': 0}},)))
        await session.send_message('Record the main task')
        status = await owned(session._sdk_controller, lambda: host.service.status(goal.id))
        assert bool(status['tasks']) == (mode == 'allow-all')
        assert (tmp_path / 'forbidden.txt').exists() == (mode == 'allow-all')
        assert not session.children.supervisor.active
    finally:
        await close(session)


async def test_ordinary_child_forged_goal_label_has_no_parent_tool_or_role_exemption(tmp_path):
    session, host, contexts = await setup(tmp_path, mode='allow-all')
    try:
        session.agent.set_stream_fn(scripted(contexts, try_write=True))
        async def start():
            return await session.children.supervisor.start('ordinary', config={'label': 'goal-worker'})
        child = await owned(session._sdk_controller, start)
        row = await session.children.supervisor.control('wait', child)
        assert row['status'] == 'completed' and not (tmp_path / 'forbidden.txt').exists()
        child_calls = contexts[1:]
        assert child_calls and all('goal' not in {t.name for t in c.tools} for c in child_calls)
        assert await owned(session._sdk_controller, host.service.current) is None
    finally:
        await close(session)


async def test_current_goal_occupies_paused_slot_and_user_clear_releases_it(tmp_path):
    session, host, _ = await setup(tmp_path)
    try:
        async def changes():
            goal = await host.service.create('One', 'One target', context=OperationContext(True))
            await host.service.change('pause', context=OperationContext(True), reason='User pause')
            with pytest.raises(ValueError, match='current Goal'):
                await host.service.create('Two', 'Not delegated', context=OperationContext(True))
            with pytest.raises(ValueError, match='user'):
                await host.service.change('resume')
            await host.service.change('clear', context=OperationContext(True), reason='User archived it')
            assert await host.service.current() is None
            assert (await host.service.status(goal.id))['current']['status'] == 'cleared'
            return await host.service.create('Two', 'New explicit target', context=OperationContext(True))
        successor = await owned(session._sdk_controller, changes)
        assert successor.title == 'Two' and not session.children.supervisor.active
    finally:
        await close(session)


async def test_parent_tool_cannot_fabricate_user_authority_or_use_old_role_actions(tmp_path):
    session, host, _ = await setup(tmp_path)
    try:
        tool = host.tools[0]
        for fields in ({}, {'user_authorized': True}):
            result = await tool.execute('forged', {'action': 'create', 'title': 'Forged', **fields})
            assert result.is_error
        assert (await tool.execute('old', {'action': 'assign'})).is_error
        assert await owned(session._sdk_controller, host.service.current) is None
        assert any(tool is value for value in session.capabilities.tools)
        assert not any(replace(tool) is value for value in session.capabilities.tools)
    finally:
        await close(session)


async def test_another_exact_session_cannot_read_original_registry_or_bind_its_parent(tmp_path):
    session, host, _ = await setup(tmp_path)
    other = None
    try:
        goal = await owned(session._sdk_controller, lambda: host.service.create(
            'Private', 'Exact owner', context=OperationContext(True)))
        other, other_host, _ = await setup(tmp_path)
        assert other_host.owner != host.owner
        assert await owned(other._sdk_controller, other_host.service.current) is None
        with pytest.raises(ValueError, match='Unknown Goal'):
            await owned(other._sdk_controller, lambda: other_host.service.status(goal.id))
        with pytest.raises(ValueError, match='owner'):
            host.attach(await other._capability_host.prepare_parent())
    finally:
        if other is not None:
            await close(other)
        await close(session)
