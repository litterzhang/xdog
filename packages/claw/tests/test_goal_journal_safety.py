"""Malformed/unknown Goal data disables only Goal work; never silently overwrites user state."""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from xdog.claw.core.planning.goal_tracker import GoalTracker
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group, UserInput

from packages.claw.tests.test_group_goal_gate import Model


@pytest.mark.parametrize('data', [
    b'{broken', b'{"version":3,"goals":{},"notifications":[]}',
    b'{"goals":{},"goals":{},"notifications":[]}',
    b'{"goals":{"unsafe":{"id":"different","group_id":"main"}},"notifications":[]}',
    b'{"goals":{},"notifications":[{"unknown":"field"}]}',
    b'{"goals":{},"notifications":[],"future_capabilities":true}',
])
def test_invalid_journal_is_not_partially_restored_or_overwritten(tmp_path, data):
    path = tmp_path / 'goals.json'
    path.write_bytes(data)
    tracker = GoalTracker(path)
    assert tracker.diagnostic and tracker.list_goals() == []
    with pytest.raises(ValueError, match='repair and reload'):
        tracker.create_goal('main', 'new', 'must not overwrite damaged old state', [])
    assert path.read_bytes() == data


def test_future_or_invalid_record_prevents_silent_partial_drop(tmp_path):
    path = tmp_path / 'goals.json'
    initial = GoalTracker(path)
    goal = initial.create_goal('main', 'keep', 'existing requirement', ['task'])
    raw = json.loads(path.read_text())
    assert raw['version'] == 2
    raw['goals'][goal.id]['phase'] = 'future-unknown-work'
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    recovered = GoalTracker(path)
    assert recovered.diagnostic
    with pytest.raises(ValueError):
        recovered.create_goal('main', 'other', 'no data loss', [])
    assert path.read_bytes() == before


def test_legacy_versionless_goal_journal_remains_data_only_and_readable(tmp_path):
    path = tmp_path / 'goals.json'
    tracker = GoalTracker(path)
    goal = tracker.create_goal('main', 'old', 'legacy criteria remain', ['task'])
    raw = json.loads(path.read_text())
    raw.pop('version')
    path.write_text(json.dumps(raw))
    restored = GoalTracker(path)
    assert not restored.diagnostic and restored.get_goal(goal.id).description == 'legacy criteria remain'


def test_symlink_journal_does_not_read_or_overwrite_target(tmp_path):
    outside = tmp_path / 'outside.json'
    data = b'{"goals":{},"notifications":[]}'
    outside.write_bytes(data)
    path = tmp_path / 'goals.json'
    path.symlink_to(outside)
    tracker = GoalTracker(path)
    assert tracker.diagnostic
    with pytest.raises(ValueError):
        tracker.create_goal('main', 'new', 'no external write', [])
    assert outside.read_bytes() == data and path.is_symlink()


async def test_corrupt_goal_store_keeps_chat_available_but_pins_context_and_stops_goal_work(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    path = data / 'groups' / 'main' / 'goals.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{corrupt existing goals')
    model = Model()
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=('goal','filesystem')),
        data_dir=data, workspace_dir=tmp_path / 'workspace', model='test/model', stream_fn=model)
    session = runtime.get_or_create_session(allow_reset=False)
    try:
        manager = runtime.goal_manager
        assert manager.tracker.diagnostic and manager.has_unfinished_goals()
        assert 'explicit repair' in manager.goals_summary()
        monkeypatch.setattr(runtime.transcript_store, 'needs_daily_reset', Mock(return_value=True))
        assert runtime.get_or_create_session() is session
        await manager.tick()
        assert not model.contexts
        result = await session.run_turn(UserInput(group_id='main', content='ordinary chat remains available'))
        assert not result.error and len(model.contexts) == 1
        assert path.read_bytes() == b'{corrupt existing goals'
    finally:
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        await runtime.mcp.aclose()


@pytest.mark.parametrize('kind', ['directory', 'fifo'])
def test_invalid_journal_rejections_do_not_leak_owned_file_descriptors(tmp_path, kind):
    import os
    path = tmp_path / 'goals.json'
    if kind == 'directory':
        path.mkdir()
    else:
        os.mkfifo(path)
    before = len(os.listdir('/proc/self/fd'))
    for _ in range(8):
        tracker = GoalTracker(path)
        assert tracker.diagnostic
    assert len(os.listdir('/proc/self/fd')) == before


def test_invalid_goal_document_rejection_closes_target_and_parent_descriptors(tmp_path):
    import os

    from xdog.claw.core.planning.goal_workspace import GoalWorkspace
    workspace = GoalWorkspace(tmp_path / 'goals')
    path = workspace.goal_dir('g-test')
    path.mkdir(parents=True)
    (path / 'plan.md').mkdir()
    before = len(os.listdir('/proc/self/fd'))
    for _ in range(8):
        with pytest.raises(ValueError, match='regular files'):
            workspace.read_plan('g-test')
    assert len(os.listdir('/proc/self/fd')) == before
