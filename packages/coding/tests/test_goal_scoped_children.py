"""Real Coding child admission/permissions with the installed, task-scoped Goal provider."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from jsonschema import Draft202012Validator
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.slash_commands import execute_command

from packages.coding.tests.test_child_agents import create
from packages.coding.tests.test_goal_adapter import owned

PRIVATE = 'UNGRANTED-CODING-TASK-MATERIAL'


def config(goal_id):
    return {'tools': ['goal'], 'tool_scopes': {'goal': {'goal_id': goal_id, 'task_ids': ['one']}}}


def text(message):
    return '\n'.join(part.text for part in message.content if isinstance(part, TextContent))


class Model:
    """Only the model transport is replaced; all host/runtime/tool IO is real."""
    def __init__(self, goal_id, attacks=()):
        self.goal_id, self.attacks = goal_id, attacks
        self.parent_contexts, self.child_contexts = [], []

    def __call__(self, model, context, options):
        parent = 'agent' in {tool.name for tool in context.tools}
        calls = self.parent_contexts if parent else self.child_contexts
        calls.append(context)
        step = len(calls) - 1
        if parent:
            call = (ToolCall(id='spawn-scoped', name='agent', arguments={
                'action': 'spawn', 'task': 'Finish the explicitly granted task only', 'access': 'inherit',
                'config': config(self.goal_id)}) if step == 0 else None)
        elif step == 0:
            call = ToolCall(id='read-one', name='goal', arguments={'action': 'tasks', 'task_id': 'one'})
        elif step <= len(self.attacks):
            name, args = self.attacks[step - 1]
            call = ToolCall(id=f'attack-{step}', name=name, arguments=args)
        elif step == len(self.attacks) + 1:
            read = next(message for message in context.messages if message.role == 'toolResult'
                        and message.tool_call_id == 'read-one')
            assert not read.is_error, text(read)
            task, = json.loads(text(read))['tasks']
            call = ToolCall(id='complete-one', name='goal', arguments={'action': 'task_update', 'task': {
                'id': 'one', 'expected_version': task['version'], 'status': 'completed',
                'summary': 'Child inspected its actual scoped task', 'evidence': ['scoped-child-result']}})
        else:
            call = None
        return EventStream.empty(AssistantMessage(content=(call,) if call else (TextContent(text='child done'),),
            stop_reason='toolUse' if call else 'stop'))


@asynccontextmanager
async def host(tmp_path, *, mode='ask', readonly=False, attacks=()):
    session, _ = create(tmp_path, mode)
    try:
        await execute_command('goal', 'create Root-owned scoped child integration', session)
        binding, controller = session.capabilities.get('goal'), session._sdk_controller

        async def seed():
            goal = await binding.service.current()
            for identifier, title in [('one', 'Explicit child task'), ('two', PRIVATE)]:
                await binding.service.task({'id': identifier, 'title': title, 'expected_version': 0})
            return goal

        goal = await owned(controller, seed)
        model = Model(goal.id, attacks)
        session.agent.set_stream_fn(model)
        if readonly:
            await asyncio.to_thread(session.access_control, True)
        requests = []

        def deny_unexpected(identifier, request):
            requests.append(request)
            assert session.children.resolve(identifier, request.id, 'deny')

        session.children.approver = deny_unexpected
        yield session, binding, goal, model, requests
    finally:
        await session.aclose()


async def spawn(session, goal, *, child_config=None):
    async with asyncio.timeout(10):
        identifier = await session.children.runtime.start('Finish only the granted existing task', access='inherit',
            config=config(goal.id) if child_config is None else child_config)
        return await session.children.runtime.control('wait', identifier)


def results(record):
    return {row['tool_call_id']: row for row in record['messages'] if row['role'] == 'toolResult'}


def assert_scoped_inventory(model, goal):
    assert model.child_contexts
    for context in model.child_contexts:
        tool, = context.tools
        assert tool.name == 'goal'  # No bash/filesystem, child spawning or parent-global Goal tool.
        schema = tool.parameters
        assert set(schema['properties']['action']['enum']) == {'tasks', 'task_update'}
        assert schema['properties']['task_id']['enum'] == ['one']
        assert schema['properties']['goal_id']['enum'] == [goal.id]
        validator = Draft202012Validator(schema)
        assert not validator.is_valid({'action': 'tasks'})
        assert not validator.is_valid({'action': 'tasks', 'task_id': 'two'})
        assert not validator.is_valid({'action': 'complete', 'summary': 'child claims Goal done'})
        assert PRIVATE not in str(context.messages)


async def test_parent_agent_spawn_creates_scoped_child_without_goal_worker_or_own_state_approval(tmp_path):
    async with host(tmp_path) as (session, binding, goal, model, child_requests):
        root_requests = []

        def approve_spawn(request):
            root_requests.append(request)
            assert request.tool_name == 'agent' and request.arguments['action'] == 'spawn'
            assert session.permissions.resolve(request.id, 'allow_once')

        session.permissions.set_request_handler(approve_spawn)
        try:
            async with asyncio.timeout(10):
                await session.send_message('Delegate this task with the ordinary agent tool')
                reply, = [message for message in session.messages if message.role == 'toolResult'
                          and message.tool_call_id == 'spawn-scoped']
                assert not reply.is_error, text(reply)
                identifier = text(reply)
                record = await session.children.runtime.control('wait', identifier)
            assert record['status'] == 'completed', record.get('error')
            assert results(record)['read-one']['is_error'] is False
            assert results(record)['complete-one']['is_error'] is False
            assert len(root_requests) == 1 and not child_requests
            assert any('complete' in tool.parameters['properties']['action']['enum']
                       for tool in model.parent_contexts[0].tools if tool.name == 'goal')
            assert_scoped_inventory(model, goal)
            status = await owned(session._sdk_controller, binding.service.status)
            assert status['current']['id'] == goal.id and status['current']['status'] == 'active'
            tasks = {row['id']: row for row in status['tasks']}
            assert tasks['one']['status'] == 'completed' and tasks['one']['version'] == 2
            assert tasks['two']['status'] == 'pending' and tasks['two']['version'] == 1
            assert tasks['one']['worker'] == 'main'  # No Goal-owned assignment/worker protocol.
            saved, = session.children.store.load()
            assert saved['config']['tool_scopes'] == config(goal.id)['tool_scopes']
        finally:
            session.permissions.set_request_handler(None)


@pytest.mark.parametrize(('mode', 'readonly', 'writable'), [
    ('ask', False, True), ('deny', False, False), ('ask', True, False), ('allow-all', True, False),
])
async def test_scoped_goal_own_state_uses_actual_coding_ask_deny_and_readonly_policy(
    tmp_path, mode, readonly, writable,
):
    async with host(tmp_path, mode=mode, readonly=readonly) as (session, binding, goal, model, requests):
        record = await spawn(session, goal)
        assert record['status'] == 'completed', record.get('error')
        messages = results(record)
        assert not messages['read-one']['is_error']
        assert messages['complete-one']['is_error'] is not writable
        assert not requests  # ask own-state is not an extra permission grant; deny never asks.
        assert_scoped_inventory(model, goal)
        snapshot = await owned(session._sdk_controller, binding.service.status)
        one = next(row for row in snapshot['tasks'] if row['id'] == 'one')
        assert one['status'] == ('completed' if writable else 'pending')
        assert one['version'] == (2 if writable else 1)
        assert snapshot['current']['status'] == 'active'


async def test_real_child_cannot_read_other_tasks_report_complete_create_or_invoke_bash(tmp_path):
    attacks = (
        ('goal', {'action': 'tasks', 'task_id': 'two'}),
        ('goal', {'action': 'tasks'}),
        ('goal', {'action': 'tasks', 'task_id': 'one', 'goal_id': 'other-goal'}),
        ('goal', {'action': 'task_update', 'task': {'id': 'two', 'expected_version': 1, 'status': 'completed'}}),
        ('goal', {'action': 'task_update', 'task': {'id': 'new', 'expected_version': 0, 'title': 'child-created'}}),
        ('goal', {'action': 'report', 'report': {}}),
        ('goal', {'action': 'complete', 'summary': 'child must not finish the main Goal'}),
        ('bash', {'command': 'touch coding-child-escape'}),
    )
    async with host(tmp_path, attacks=attacks) as (session, binding, goal, model, requests):
        private = binding.service.files(goal).tasks_dir / 'two.json'
        before = private.read_bytes()
        record = await spawn(session, goal)
        assert record['status'] == 'completed', record.get('error')
        messages = results(record)
        assert all(messages[f'attack-{index}']['is_error'] for index in range(1, len(attacks) + 1))
        assert not messages['complete-one']['is_error'] and not requests
        assert private.read_bytes() == before and not (tmp_path / 'coding-child-escape').exists()
        assert not binding.service.files(goal).progress_path.exists()
        assert (await owned(session._sdk_controller, binding.service.current)).status == 'active'
        assert_scoped_inventory(model, goal)


@pytest.mark.parametrize('scope', [
    {'goal_id': 'other-goal', 'task_ids': ['one']},
    {'goal_id': None, 'task_ids': ['missing']},
    {'goal_id': None, 'task_ids': ['one', 'one']},
    {'goal_id': None, 'task_ids': ['one'], 'owner': 'forged'},
])
async def test_invalid_scope_fails_real_preflight_before_any_child_model_or_task_change(tmp_path, scope):
    async with host(tmp_path) as (session, binding, goal, model, _):
        scope = {**scope, 'goal_id': scope['goal_id'] or goal.id}
        with pytest.raises(ValueError):
            await spawn(session, goal, child_config={'tools': ['goal'], 'tool_scopes': {'goal': scope}})
        assert not model.child_contexts and not session.children.runtime.active
        snapshot = await owned(session._sdk_controller, binding.service.status)
        assert all(row['version'] == 1 for row in snapshot['tasks'])
        assert snapshot['current']['status'] == 'active'


async def test_ungranted_malformed_task_is_not_read_by_real_child_preflight_or_cas_write(tmp_path):
    async with host(tmp_path) as (session, binding, goal, model, requests):
        private = binding.service.files(goal).tasks_dir / 'two.json'
        damaged = b'UNGRANTED MALFORMED TASK: restricted operations must not parse this file\n'
        private.write_bytes(damaged)
        record = await spawn(session, goal)
        assert record['status'] == 'completed', record.get('error')
        assert not results(record)['read-one']['is_error'] and not results(record)['complete-one']['is_error']
        assert private.read_bytes() == damaged and not requests
        selected = await owned(session._sdk_controller, lambda: binding.service.tasks(goal.id, task_id='one'))
        assert selected['tasks'][0]['status'] == 'completed' and selected['tasks'][0]['version'] == 2
        assert (await owned(session._sdk_controller, binding.service.current)).status == 'active'
        assert_scoped_inventory(model, goal)


async def test_selecting_goal_by_name_without_explicit_scope_cannot_inherit_parent_global_tool(tmp_path):
    async with host(tmp_path) as (session, binding, goal, model, _):
        with pytest.raises(ValueError):
            await spawn(session, goal, child_config={'tools': ['goal']})
        assert not model.child_contexts and not session.children.runtime.active
        assert (await owned(session._sdk_controller, binding.service.current)).status == 'active'
