"""Automatic Claw input can report and self-pause, never release a user pause."""
from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.inputs.models import InputScope
from xdog.skills.goal.host import OperationContext

from packages.claw.tests.test_goal_host import activate, application


async def test_automatic_main_reports_then_self_pauses_and_stale_reminder_cannot_resume(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        binding = runtime.capabilities.get('goal')
        goal = await binding.service.create('Output', 'Inspect result', context=OperationContext(True))
        report = {'expected_version': 0, 'progress': 'baseline', 'summary': 'Initial actual state',
                  'completed': [], 'evidence': ['result.txt'], 'blockers': [], 'next_steps': ['Inspect result']}
        actions = [{'action': 'report', 'report': report}]

        def stream(selected, context, options):
            start = max((i for i, message in enumerate(context.messages) if message.role == 'user'), default=-1)
            turn = sum(message.role == 'assistant' for message in context.messages[start + 1:])
            content = ((ToolCall(id=f'report-{turn}', name='goal', arguments=actions[turn]),)
                       if turn < len(actions) else (TextContent(text='Main Agent paused; needs a new input'),))
            return EventStream.empty(AssistantMessage(content=content,
                stop_reason='toolUse' if isinstance(content[0], ToolCall) else 'stop'))

        runtime._active_session.agent.set_stream_fn(stream)
        scope = InputScope('main', runtime.workspace_dir)

        async def automatic(identifier):
            await app.accept_external(scope, SessionInput(identifier, 'automatic', 'goal',
                'Read current status and assess progress before work; this is not user authority', (), {}))
            result = await (await app.input_service()).wait(scope, identifier)
            assert result.status == 'succeeded'
            assert not runtime._active_session._user_operation_authorized

        await automatic('baseline')
        actions[:] = [{'action': 'report', 'report': {**report, 'expected_version': 1, 'progress': 'unchanged',
            'summary': 'Repeated attempts had no progress and no new viable approach',
            'blockers': ['Need a new approach'], 'next_steps': ['Pause; ask user for missing input']}},
            {'action': 'pause', 'reason': 'No viable approach; need a new user input'}]
        await automatic('assess-and-pause')
        await binding.flush()
        summary = await binding.service.summary()
        assert summary['current']['status'] == 'paused'
        assert summary['latest_report']['version'] == 2
        assert summary['automation']['status'] == 'paused' and binding._observer is None
        assert binding.service.files(goal).progress_path.exists()
        actions[:] = [{'action': 'resume'}]
        await automatic('stale-queued-reminder')
        assert (await binding.service.current()).status == 'paused'
        assert binding.service.files(goal).latest_report()['version'] == 2
        assert not runtime._active_session.children.supervisor.active
