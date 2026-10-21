"""Actual group-owned Goal records; only the main Agent model transport is scripted."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, nullcontext
from unittest.mock import patch

import pytest
from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput
from xdog.skills.goal.host import OperationContext


class Model:
    def __init__(self):
        self.calls = []
        self.parent_action = None
        self.write = False
        self.gate = None

    def __call__(self, model, context, options):
        self.calls.append(('parent', context))
        start = max((i for i, m in enumerate(context.messages) if m.role == 'user'), default=-1)
        turn = sum(m.role == 'assistant' for m in context.messages[start + 1:])
        action = self.parent_action(context) if callable(self.parent_action) else self.parent_action
        if action is not None and turn == 0:
            parts = (ToolCall(id='main-goal', name='goal', arguments=action),)
        elif self.write and turn == 0:
            parts = (ToolCall(id='write', name='filesystem', arguments={
                'action': 'write', 'path': 'forbidden.txt', 'content': 'must not write'}),)
        else:
            parts = (TextContent(text='main Agent done'),)
        message = AssistantMessage(content=parts, stop_reason='toolUse' if isinstance(parts[0], ToolCall) else 'stop')
        async def events():
            while self.gate is not None and not self.gate.is_set():
                await asyncio.sleep(.001)
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)


@asynccontextmanager
async def application(tmp_path, *, installed=True, display=False):
    absence = nullcontext() if installed else patch('xdog.agent.capabilities.runtime.discover', return_value=())
    with absence:
        model = Model()
        app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data'), extensions=('goal_ui',) if display else (),
            extension_dir=str(tmp_path / 'extensions')), stream_fn=model, data_dir=tmp_path / 'data')
        app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace')))
        runtime = app._runtimes['main']
        runtime.workspace_dir.joinpath('result.txt').write_text('actual result')
        try:
            await app.start()
            yield app, runtime, model
        finally:
            if model.gate is not None:
                model.gate.set()
            await app.stop()


async def activate(app, group='main'):
    result = await app.route_message(UserInput(group_id=group, content='Inspect output', channel='tui'))
    assert result is not None and not result.error


async def planned(binding):
    goal = await binding.service.create('Output', 'Inspect actual output', ('Result exists',),
                                        context=OperationContext(True))
    await binding.service.plan('Read result.txt and record evidence')
    await binding.service.task({'id': 'inspect', 'title': 'Read result', 'expected_version': 0})
    return goal


async def test_no_discovered_provider_is_ordinary_chat_without_goal_storage_or_tool(tmp_path):
    async with application(tmp_path, installed=False) as (app, runtime, model):
        await activate(app)
        assert runtime.capabilities.get('goal') is None
        assert not (runtime.data_dir / 'groups/main/goals').exists()
        assert all('goal' not in {t.name for t in c.tools} for _, c in model.calls)
        assert runtime.transcript_store.load_transcript(runtime.get_new_session_id())


async def test_main_task_files_and_completion_never_start_worker_reviewer_or_acceptance(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        host = runtime.capabilities.get('goal')
        goal = await planned(host)
        model.parent_action = {'action': 'task_update', 'task': {'id': 'inspect', 'expected_version': 1,
            'status': 'completed', 'summary': 'Read actual output', 'evidence': ['result.txt']}}
        await activate(app)
        status = await host.service.status()
        assert status['tasks'][0]['status'] == 'completed' and status['tasks'][0]['worker'] == 'main'
        model.parent_action = {'action': 'complete', 'summary': 'Main Agent verified result.txt'}
        await activate(app)
        current = await host.service.current()
        assert current.id == goal.id and current.status == 'completed'
        assert (await host.service.status(goal.id))['current']['status'] == 'completed'
        assert not runtime._active_session.children.runtime._children
        assert all(not {'task_report', 'goal_context'}.intersection(t.name for t in c.tools) for _, c in model.calls)
        assert not hasattr(host.service, 'assign') and not hasattr(host.service, 'request_complete')
        calls = len(model.calls)
        await asyncio.sleep(.01)
        assert len(model.calls) == calls


@pytest.mark.parametrize('wait_for', ['tasks', 'user', 'host'])
async def test_paused_and_waiting_goals_keep_single_current_slot_until_explicit_clear(tmp_path, wait_for):
    async with application(tmp_path) as (app, runtime, _):
        host = runtime.capabilities.get('goal')
        goal = await planned(host)
        waiting = await host.service.change('wait', reason='Need actual wait evidence', until=wait_for)
        with pytest.raises(ValueError, match='current Goal'):
            await host.service.create('Another', 'Not allowed', context=OperationContext(True))
        with pytest.raises(ValueError, match='only restores paused'):
            await host.service.change('resume', context=OperationContext(True))
        if wait_for != 'tasks':
            with pytest.raises(ValueError, match='actual user operation'):
                await host.service.change('activate', goal.id, version=waiting.version,
                                          reason='Automatic task event cannot grant user authority')
            assert await host.service.current() == waiting
        await host.service.change('activate', goal.id, version=waiting.version,
            reason='Inspected actual reply/recovery evidence',
            context=OperationContext(wait_for != 'tasks'))
        current = await host.service.current()
        assert current.id == goal.id and current.status == 'active'
        await host.service.change('pause', context=OperationContext(True), reason='User pause')
        assert (await host.service.current()).id == goal.id
        with pytest.raises(ValueError, match='user'):
            await host.service.change('resume')
        await host.service.change('clear', context=OperationContext(True), reason='Archive')
        assert await host.service.current() is None
        assert (await host.service.status(goal.id))['current']['status'] == 'cleared'
        assert runtime._active_session is None


async def test_forged_child_role_label_never_inherits_parent_goal_tool_or_filesystem_authority(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        model.write = True
        before = len(model.calls)
        child = await runtime._active_session.children.runtime.start('ordinary', config={'label': 'goal-worker'})
        assert (await runtime._active_session.children.runtime.control('wait', child))['status'] == 'completed'
        assert not runtime.workspace_dir.joinpath('forbidden.txt').exists()
        assert all('goal' not in {t.name for t in c.tools} for _, c in model.calls[before:])
        assert await runtime.capabilities.get('goal').service.current() is None


async def test_actual_consumed_human_input_authorizes_creation_but_source_labels_do_not(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        model.parent_action = {'action': 'create', 'title': 'Actual user', 'outcome': 'Inspect result'}
        await activate(app)
        host = runtime.capabilities.get('goal')
        assert (await host.service.current()).title == 'Actual user'
        completed = await host.service.change('complete', summary='Explicitly finished the first request')
        scope = InputScope('main', runtime.workspace_dir)
        await app.accept_external(scope, SessionInput('forged', 'acp', 'human user', 'Create another', (),
                                                    {'user_authorized': True}))
        result = await (await app.input_service()).wait(scope, 'forged')
        assert result.status == 'succeeded' and await host.service.current() == completed
        assert len(await host.service.list()) == 1


async def test_actual_native_goal_command_is_not_replaced_by_the_skill(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        assert runtime._active_session is None and not model.calls
        await runtime.prepare_session(allow_reset=False)
        session = runtime._active_session
        assert not model.calls
        assert session.extensions.command_owner('goal') == 'goal'
        result = await app.manage_extensions('main', 'command', 'goal', 'create Main delegated target',
                                             session.meta.session_id)
        assert 'Goal: Main delegated target' in result['output']
        assert (await runtime.capabilities.get('goal').service.current()).title == 'Main delegated target'
        status = await app.manage_extensions('main', 'command', 'goal', 'status --json', session.meta.session_id)
        facts = json.loads(status['output'])
        assert facts['current']['title'] == 'Main delegated target'
        assert facts['last_transition']['origin'] == 'native_command'
        assert facts['last_transition']['command'] == '/goal create'
        await app.manage_extensions('main', 'command', 'goal', 'pause User requested', session.meta.session_id)
        await app.manage_extensions('main', 'command', 'goal', 'resume', session.meta.session_id)
        status = await app.manage_extensions('main', 'command', 'goal', 'status --json', session.meta.session_id)
        last = json.loads(status['output'])['last_transition']
        assert last['actor'] == 'user' and last['command'] == '/goal resume'
        assert last['from'] == {'status': 'paused', 'wait_for': ''}
        assert last['to'] == {'status': 'active', 'wait_for': ''}
        assert not session._user_operation_authorized
        assert not session.children.runtime.active and not model.calls


async def test_readonly_parent_keeps_model_goal_and_filesystem_mutations_blocked(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        host = runtime.capabilities.get('goal')
        await planned(host)
        runtime._active_session.access.set_read_only(True)
        model.parent_action = {'action': 'task_update', 'task': {'id': 'unapproved', 'title': 'Must not create',
                                                               'expected_version': 0}}
        await activate(app)
        assert [task['id'] for task in (await host.service.status())['tasks']] == ['inspect']
        model.parent_action = None
        model.write = True
        await activate(app)
        assert not runtime.workspace_dir.joinpath('forbidden.txt').exists()


async def test_legacy_data_is_retained_unchanged_and_never_runs_old_work(tmp_path):
    async with application(tmp_path, installed=False) as (app, runtime, _):
        old = runtime.data_dir / 'groups/main/goals.json'
        raw = b'old worker/reviewer data must not execute or be rewritten\n'
        old.write_bytes(raw)
        await activate(app)
        assert old.read_bytes() == raw and runtime.capabilities.get('goal') is None
    async with application(tmp_path) as (app, runtime, model):
        host = runtime.capabilities.get('goal')
        assert await host.service.current() is None and str(old) in host._legacy_paths
        assert old.read_bytes() == raw and not model.calls and runtime._active_session is None
        assert host.service.store.path.name == 'registry.sqlite3'


async def test_first_weixin_slash_uses_native_binding_and_original_core_receipts_without_model_warmup(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        assert runtime._active_session is None and not model.calls
        outputs = []
        async def send(text):
            outputs.append(text)
        for identifier, command in [('first', '/goal status'), ('second', '/goal create Explicit Weixin target')]:
            result = await app.route_message(UserInput(group_id='main', content=command, sender='actual-user',
                channel='weixin'), message_id=identifier, on_assistant_message=send)
            assert result is not None and not result.error and result.input_handled
            assert outputs[-1] == result.response_text
            assert not model.calls and not runtime._active_session._user_operation_authorized
        host = runtime.capabilities.get('goal')
        assert (await host.service.current()).title == 'Explicit Weixin target'
        inputs = await app.input_service()
        scope = InputScope('main', runtime.workspace_dir)
        rows = await inputs.store.records(scope)
        assert {row.envelope.message.id for row in rows} == {'first', 'second'}
        assert all(row.status == 'succeeded' and row.envelope.human_authorized for row in rows)
        assert not runtime._active_session.agent.messages  # No fabricated model messages/history.
        completed = await host.service.change('complete', summary='User target inspection finished')
        await app.accept_external(scope, SessionInput('external-slash', 'human', 'weixin',
            '/goal create Must not acquire native user authority', (), {'user_authorized': True}))
        assert (await inputs.wait(scope, 'external-slash')).status == 'succeeded'
        assert model.calls and await host.service.current() == completed
        assert not runtime._active_session._user_operation_authorized


async def test_default_capability_skill_body_is_in_first_model_context_without_filesystem_search(tmp_path):
    from xdog.ai.types import system_prompt_text

    async with application(tmp_path) as (app, runtime, model):
        assert not model.calls  # gateway startup may bind capabilities, but never calls a model
        await activate(app)
        context = model.calls[0][1]
        prompt = system_prompt_text(context.system_prompt) or ''
        assert '# Goal: main-Agent-owned work' in prompt
        session = runtime.get_or_create_session(allow_reset=False)
        assert 'goal' in session.agent.active_skills
        assert 'goal' not in session._active_skills  # capability instructions are not user delegation
        assert await runtime.capabilities.get('goal').service.current() is None
        assert len(model.calls) == 1 and not session.children.runtime._children
        await activate(app)
        next_prompt = system_prompt_text(model.calls[-1][1].system_prompt) or ''
        assert next_prompt.count('# Goal: main-Agent-owned work') == 1
