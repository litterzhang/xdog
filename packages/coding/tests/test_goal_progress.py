"""The actual Coding main Agent reports before work; original permissions still gate writes."""
from pathlib import Path

import pytest
from xdog.skills.goal.host import OperationContext

from packages.coding.tests.test_goal_adapter import close, owned, scripted, setup


def report(version, progress, summary):
    return {'action': 'report', 'report': {'expected_version': version, 'progress': progress,
        'summary': summary, 'completed': [], 'evidence': ['result.txt'],
        'blockers': ['No new viable approach'] if progress == 'unchanged' else [],
        'next_steps': ['Pause and ask for a new input'] if progress == 'unchanged' else ['Inspect result']}}


async def test_main_reports_baseline_then_no_progress_then_pauses_without_completion(tmp_path):
    session, host, contexts = await setup(tmp_path, mode='allow-all')
    try:
        await owned(session._sdk_controller, lambda: host.service.create(
            'Output', 'Inspect actual result', context=OperationContext(True)))
        session.agent.set_stream_fn(scripted(contexts, actions=(report(0, 'baseline', 'Initial actual state'),)))
        await session.send_message('Establish the initial assessment before work')
        session.agent.set_stream_fn(scripted(contexts, actions=(
            report(1, 'unchanged', 'Repeated approaches failed; no feasible new strategy'),
            {'action': 'pause', 'reason': 'Need different input after repeated unsuccessful approaches'})))
        await session.send_message('Assess the previous round and stop repeating unproductive work')
        summary = await owned(session._sdk_controller, host.service.summary)
        assert summary['current']['status'] == 'paused'
        assert summary['latest_report']['version'] == 2
        assert summary['latest_report']['progress'] == 'unchanged'
        assert Path(summary['report_path']).read_text().count('```json goal-report-v1') == 2
        assert host._observer is None
        assert summary['automation']['status'] == 'paused'
        assert not session.children.supervisor.active
    finally:
        await close(session)


@pytest.mark.parametrize('mode', ['ask', 'deny'])
async def test_goal_report_does_not_bypass_actual_coding_permissions(tmp_path, mode):
    session, host, contexts = await setup(tmp_path, mode=mode)
    try:
        goal = await owned(session._sdk_controller, lambda: host.service.create(
            'Output', 'No implicit write permissions', context=OperationContext(True)))
        session.agent.set_stream_fn(scripted(contexts, actions=(
            report(0, 'baseline', 'Must require write permission'),)))
        await session.send_message('Inspect only; no write approval')
        summary = await owned(session._sdk_controller, host.service.summary)
        assert summary['latest_report'] is None
        assert not host.service.files(goal).progress_path.exists()
    finally:
        await close(session)
