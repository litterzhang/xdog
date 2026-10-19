"""Retired historical navigation no longer has a target-Agent staging runtime."""
from __future__ import annotations

import importlib.util
from unittest.mock import Mock

import pytest
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group


def test_historical_agent_session_staging_module_is_removed():
    assert importlib.util.find_spec('xdog.claw.core.runtime.session_staging') is None
    assert not hasattr(GroupRuntime, '_navigate_prepared')


@pytest.mark.parametrize('action', ['resume', 'fork', 'branch', 'goto'])
def test_retired_navigation_rejects_before_history_or_cached_agent_side_effects(tmp_path, action):
    # Explicit injected Goal owner isolates this boundary from Goal implementation.
    manager = Mock()
    runtime = GroupRuntime(group=Group(id='main'), data_dir=tmp_path, goal_manager=manager)
    try:
        runtime.transcript_store.load_transcript = Mock(side_effect=AssertionError('Must not read target'))
        runtime._active_session = Mock()
        with pytest.raises(ValueError, match='read-only'):
            runtime.navigate_session(action, 'untrusted-target', 'legacy-branch', 0, '')
        runtime._active_session.assert_not_called()
        runtime._active_session._children_idle.assert_not_called()
        runtime._active_session._persist.assert_not_called()
        runtime._active_session.dispose.assert_not_called()
        assert not hasattr(runtime.transcript_store, 'commit_navigation')
        manager.attach.assert_called_once_with(runtime)
    finally:
        runtime._active_session = None
        runtime.mcp.close()
