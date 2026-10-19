"""Context rotation retains group-owned terminal Gate receipts before disposal."""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from xdog.claw.core.types import GoalStatus

from packages.claw.tests.test_group_context_controls import owner as owner
from packages.claw.tests.test_group_goal_gate import create, settled
from packages.claw.tests.test_group_goal_gate import runtime as runtime


@pytest.mark.parametrize('operation', ['new', 'reset'])
async def test_context_rotation_records_child_snapshot_before_old_context_disposal(owner, monkeypatch, operation):
    runtime, manager = owner
    session = runtime.get_or_create_session(allow_reset=False)
    terminal = [{'id': 'review-terminal', 'status': 'completed', 'settling': False}]
    order = []
    monkeypatch.setattr(session.children.supervisor, 'snapshot', lambda: {'children': terminal})
    manager.record_children_status.side_effect = lambda rows: order.append(('record', rows))
    original = session.dispose
    def dispose():
        assert order == [('record', terminal)]
        order.append(('dispose', None))
        original()
    monkeypatch.setattr(session, 'dispose', dispose)
    if operation == 'new':
        runtime.navigate_session('new', '', '', -1, session.meta.session_id)
    else:
        runtime.reset_session()
    manager.record_children_status.assert_called_once_with(terminal)
    assert order == [('record', terminal), ('dispose', None)]
    assert runtime._active_session is None and runtime.goal_manager is manager


async def test_receipt_persistence_failure_never_disposes_context_or_changes_active_pointer(owner, monkeypatch):
    runtime, manager = owner
    session = runtime.get_or_create_session(allow_reset=False)
    identifier = session.meta.session_id
    manager.record_children_status.side_effect = OSError('Goal receipt storage unavailable')
    dispose = Mock(side_effect=AssertionError('Do not dispose before durable receipt'))
    with monkeypatch.context() as patch:
        patch.setattr(session, 'dispose', dispose)
        with pytest.raises(OSError, match='receipt storage'):
            runtime.reset_session()
        dispose.assert_not_called()
    assert runtime._active_session is session and runtime.get_new_session_id() == identifier


async def test_active_children_reject_rotation_before_receipt_capture(owner, monkeypatch):
    runtime, manager = owner
    session = runtime.get_or_create_session(allow_reset=False)
    identifier = session.meta.session_id
    monkeypatch.setattr(session, '_children_idle', Mock(side_effect=RuntimeError('Child work is active')))
    with pytest.raises(RuntimeError, match='Child work'):
        runtime.reset_session()
    manager.record_children_status.assert_not_called()
    assert runtime._active_session is session and runtime.get_new_session_id() == identifier


@pytest.mark.parametrize('operation', ['new', 'reset'])
async def test_finished_independent_gate_receipt_survives_rotation_without_reviewer_reexecution(runtime, operation):
    runtime, old, model = runtime
    manager = runtime.goal_manager
    goal = create(runtime, tasks=())
    manager.request_review('main', goal.id, evidence_paths=('artifact.txt',))
    await manager.tick()
    reviewing = manager.owned('main', goal.id)
    await settled(old, reviewing.review_child_id)
    assert manager.owned('main', goal.id).review_output
    calls = len(model.contexts)
    if operation == 'new':
        runtime.navigate_session('new', '', '', -1, old.meta.session_id)
    else:
        runtime.reset_session()
    fresh = runtime.get_or_create_session(allow_reset=False)
    try:
        assert fresh.meta.session_id != old.meta.session_id
        assert not fresh.children.supervisor.snapshot()['children']
        await manager.tick()
        final = manager.owned('main', goal.id)
        assert final.status == GoalStatus.COMPLETED
        assert final.review_child_id == reviewing.review_child_id
        assert len(model.contexts) == calls  # No replacement reviewer or model replay.
        assert runtime.goal_manager is manager and manager.runtime is runtime
    finally:
        await fresh.children.supervisor.aclose()
        await fresh.extensions.aclose()
        fresh.dispose()
