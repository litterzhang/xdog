"""Real Claw group/child execution with installed, explicitly task-scoped Goal authority.

Claw has configured tool policy and AccessScope, not Coding's ask/deny manager.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from jsonschema import Draft202012Validator
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput
from xdog.skills.goal.host import OperationContext

PRIVATE = 'UNGRANTED-CLAW-TASK-MATERIAL'


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
            read = next(message for message in context.messages if getattr(message, 'role', None) == 'toolResult'
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


@asynccontextmanager
async def host(tmp_path, *, readonly=False, attacks=()):
    model = Model('', attacks)
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/parent', stream_fn=model)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace')))
    try:
        await app.start()
        runtime = app._runtimes['main']
        session = await runtime.prepare_session(allow_reset=False)
        binding = runtime.capabilities.get('goal')
        goal = await binding.service.create('Root-owned scoped child integration', 'Inspect the child result',
                                            context=OperationContext(True))
        for identifier, title in [('one', 'Explicit child task'), ('two', PRIVATE)]:
            await binding.service.task({'id': identifier, 'title': title, 'expected_version': 0})
        model.goal_id = goal.id
        if readonly:
            session.access.set_read_only(True)
        yield app, runtime, session, binding, goal, model
    finally:
        await app.stop()


async def spawn(session, goal, *, child_config=None):
    async with asyncio.timeout(10):
        identifier = await session.children.runtime.start('Finish only the granted existing task', access='inherit',
            config=config(goal.id) if child_config is None else child_config)
        return await session.children.runtime.control('wait', identifier)


async def test_parent_agent_spawn_uses_real_group_child_route_not_a_goal_worker(tmp_path):
    async with host(tmp_path) as (app, _, session, binding, goal, model):
        async with asyncio.timeout(10):
            response = await app.route_message(UserInput(group_id='main',
                content='Delegate this task with the ordinary agent tool', channel='tui'))
            assert response is not None and not response.error
            reply, = [message for message in session.agent.messages if getattr(message, 'role', None) == 'toolResult'
                      and message.tool_call_id == 'spawn-scoped']
            assert not reply.is_error, text(reply)
            record = await session.children.runtime.control('wait', text(reply))
        assert record['status'] == 'completed', record.get('error')
        assert not results(record)['read-one']['is_error'] and not results(record)['complete-one']['is_error']
        assert_scoped_inventory(model, goal)
        assert any('complete' in tool.parameters['properties']['action']['enum']
                   for tool in model.parent_contexts[0].tools if tool.name == 'goal')
        status = await binding.service.status()
        assert status['current']['id'] == goal.id and status['current']['status'] == 'active'
        tasks = {row['id']: row for row in status['tasks']}
        assert tasks['one']['status'] == 'completed' and tasks['one']['version'] == 2
        assert tasks['two']['status'] == 'pending' and tasks['two']['version'] == 1
        assert tasks['one']['worker'] == 'main'
        saved, = session.children.store.load()
        assert saved['config']['tool_scopes'] == config(goal.id)['tool_scopes']


@pytest.mark.parametrize('readonly', [False, True])
async def test_scoped_goal_read_and_write_obey_real_claw_parent_readonly_policy(tmp_path, readonly):
    async with host(tmp_path, readonly=readonly) as (_, _, session, binding, goal, model):
        record = await spawn(session, goal)
        assert record['status'] == 'completed', record.get('error')
        messages = results(record)
        assert not messages['read-one']['is_error']
        assert messages['complete-one']['is_error'] is readonly
        assert_scoped_inventory(model, goal)
        snapshot = await binding.service.status()
        one = next(row for row in snapshot['tasks'] if row['id'] == 'one')
        assert one['status'] == ('pending' if readonly else 'completed')
        assert one['version'] == (1 if readonly else 2)
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
        ('bash', {'command': 'touch claw-child-escape'}),
    )
    async with host(tmp_path, attacks=attacks) as (_, runtime, session, binding, goal, model):
        private = binding.service.files(goal).tasks_dir / 'two.json'
        before = private.read_bytes()
        record = await spawn(session, goal)
        assert record['status'] == 'completed', record.get('error')
        messages = results(record)
        assert all(messages[f'attack-{index}']['is_error'] for index in range(1, len(attacks) + 1))
        assert not messages['complete-one']['is_error']
        assert private.read_bytes() == before and not (runtime.workspace_dir / 'claw-child-escape').exists()
        assert not binding.service.files(goal).progress_path.exists()
        assert (await binding.service.current()).status == 'active'
        assert_scoped_inventory(model, goal)


@pytest.mark.parametrize('scope', [
    {'goal_id': 'other-goal', 'task_ids': ['one']},
    {'goal_id': None, 'task_ids': ['missing']},
    {'goal_id': None, 'task_ids': ['one', 'one']},
    {'goal_id': None, 'task_ids': ['one'], 'owner': 'forged'},
])
async def test_invalid_scope_fails_real_preflight_before_any_child_model_or_task_change(tmp_path, scope):
    async with host(tmp_path) as (_, _, session, binding, goal, model):
        scope = {**scope, 'goal_id': scope['goal_id'] or goal.id}
        with pytest.raises(ValueError):
            await spawn(session, goal, child_config={'tools': ['goal'], 'tool_scopes': {'goal': scope}})
        assert not model.child_contexts and not session.children.runtime.active
        snapshot = await binding.service.status()
        assert all(row['version'] == 1 for row in snapshot['tasks'])
        assert snapshot['current']['status'] == 'active'


async def test_ungranted_malformed_task_is_not_read_by_real_child_preflight_or_cas_write(tmp_path):
    async with host(tmp_path) as (_, _, session, binding, goal, model):
        private = binding.service.files(goal).tasks_dir / 'two.json'
        damaged = b'UNGRANTED MALFORMED TASK: restricted operations must not parse this file\n'
        private.write_bytes(damaged)
        record = await spawn(session, goal)
        assert record['status'] == 'completed', record.get('error')
        assert not results(record)['read-one']['is_error'] and not results(record)['complete-one']['is_error']
        assert private.read_bytes() == damaged
        selected = await binding.service.tasks(goal.id, task_id='one')
        assert selected['tasks'][0]['status'] == 'completed' and selected['tasks'][0]['version'] == 2
        assert (await binding.service.current()).status == 'active'
        assert_scoped_inventory(model, goal)


async def test_selecting_goal_by_name_without_explicit_scope_cannot_inherit_parent_global_tool(tmp_path):
    async with host(tmp_path) as (_, _, session, binding, goal, model):
        with pytest.raises(ValueError):
            await spawn(session, goal, child_config={'tools': ['goal']})
        assert not model.child_contexts and not session.children.runtime.active
        assert (await binding.service.current()).status == 'active'
