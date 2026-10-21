"""An actual capability binding may describe its own state, never arbitrary file authority."""
from __future__ import annotations

import asyncio

import pytest
from xdog.coding.core.slash_commands import execute_command

from packages.coding.tests.test_goal_adapter import owned, scripted
from packages.coding.tests.test_goal_lifecycle import dispose, enabled


async def prepared(tmp_path, mode='ask'):
    session, contexts = enabled(tmp_path, mode=mode)
    await execute_command('goal', 'create Goal own-state approval test', session)
    return session, contexts, session.capabilities.get('goal')


async def test_real_goal_plan_report_and_task_update_need_no_ask_approval(tmp_path):
    session, contexts, binding = await prepared(tmp_path)
    try:
        approvals = []
        session.permissions.set_request_handler(approvals.append)
        actions = (
            {'action': 'plan', 'content': 'Read output and check evidence'},
            {'action': 'report', 'report': {'expected_version': 0, 'progress': 'baseline', 'completed': [],
                'evidence': [], 'blockers': [], 'next_steps': ['Check task'], 'summary': 'Initial assessment'}},
            {'action': 'task_update', 'task': {'id': 'one', 'expected_version': 0, 'title': 'Check output'}},
        )
        session.agent.set_stream_fn(scripted(contexts, actions=actions))
        await session.send_message('Maintain this Goal workspace and report progress')
        assert not approvals
        status = await owned(session._sdk_controller, lambda: binding.service.status(detail=True))
        assert status['plan'].startswith('Read output') and status['tasks'][0]['id'] == 'one'
        summary = await owned(session._sdk_controller, binding.service.summary)
        assert summary['latest_report']['summary'] == 'Initial assessment'
        results = [message for message in session.messages if message.role == 'toolResult']
        assert len(results) == 3 and all(not result.is_error for result in results)
    finally:
        session.permissions.set_request_handler(None)
        await dispose(session)


@pytest.mark.parametrize('mode', ['ask', 'deny'])
async def test_readonly_allows_bound_goal_status_but_never_own_state_writes(tmp_path, mode):
    session, contexts, binding = await prepared(tmp_path, mode)
    try:
        await asyncio.to_thread(session.access_control, True)
        session.agent.set_stream_fn(scripted(contexts, actions=(
            {'action': 'status'}, {'action': 'plan', 'content': 'must not write under readonly'},
        )))
        await session.send_message('Inspect current Goal; readonly must remain enforced')
        results = [message for message in session.messages if message.role == 'toolResult']
        assert len(results) == 2 and not results[0].is_error and results[1].is_error
        status = await owned(session._sdk_controller, lambda: binding.service.status(detail=True))
        assert not status['plan']
    finally:
        await dispose(session)


async def test_goal_mutation_cannot_claim_approval_exemption_by_cross_action_fields(tmp_path):
    session, contexts, binding = await prepared(tmp_path)
    try:
        session.agent.set_stream_fn(scripted(contexts, actions=(
            {'action': 'plan', 'content': 'not accepted', 'path': '/tmp/not-goal-data'},
        )))
        await session.send_message('Do not permit arbitrary paths through Goal')
        results = [message for message in session.messages if message.role == 'toolResult']
        assert results and all(result.is_error for result in results)
        assert not (await owned(session._sdk_controller, lambda: binding.service.status(detail=True)))['plan']
    finally:
        await dispose(session)
