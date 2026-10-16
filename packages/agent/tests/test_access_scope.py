from __future__ import annotations

import importlib.util
from concurrent.futures import ThreadPoolExecutor

import pytest
from xdog.agent.access import AccessScope, AccessState, read_only_call
from xdog.agent.skills import load_packaged_skill


def test_persist_before_publication_failed_write_and_retired_owners():
    seen = []
    owner = AccessScope(AccessState(), lambda state: seen.append((state, owner.state)))
    result = owner.set_read_only(True)
    assert result == AccessState(True, 1)
    assert seen == [(result, AccessState())]
    owner.set_read_only(True)
    assert len(seen) == 1
    owner.close()
    with pytest.raises(RuntimeError, match='closed'):
        owner.set_read_only(False)
    failed = AccessScope(AccessState(True), lambda state: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        failed.set_read_only(False)
    assert failed.state.read_only


def test_parallel_user_restrictions_commit_once():
    saved = []
    owner = AccessScope(AccessState(), saved.append)
    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(owner.set_read_only, [True] * 8))
    assert len(saved) == 1 and all(state == AccessState(True, 1) for state in states)
    assert not hasattr(owner, 'tool')


@pytest.mark.parametrize('bad', [[], False, {}, {'read_only': 1, 'revision': 0},
    {'read_only': False, 'revision': True}, {'read_only': False, 'revision': -1},
    {'read_only': False, 'revision': 1, 'mode': 'off'}])
def test_invalid_new_access_fails_closed(bad):
    with pytest.raises(ValueError):
        AccessState.from_dict(bad)


@pytest.mark.parametrize('bad', [None, [], {'mode': [], 'revision': 1},
    {'mode': 'other', 'revision': 0}, {'mode': 'planning', 'revision': True},
    {'mode': 'planning', 'revision': 1, 'objective': ''},
    {'mode': 'planning', 'revision': 1, 'objective': 'x', 'steps': [{'description': 'x', 'status': []}]}])
def test_malformed_new_or_legacy_never_falls_back_to_writable(bad):
    with pytest.raises(ValueError):
        AccessState.saved({'access': bad, 'plan': {'mode': 'off', 'revision': 0}})
    if bad is not None:
        with pytest.raises(ValueError):
            AccessState.legacy(bad)


def test_legacy_planning_read_only_and_explicit_new_release_wins():
    old = {'mode': 'planning', 'revision': 4, 'objective': 'Investigate', 'steps': []}
    assert AccessState.saved({'plan': old}) == AccessState(True, 4)
    assert AccessState.saved({'plan': old, 'access': {'read_only': False, 'revision': 5}}) == AccessState(False, 5)
    assert AccessState.legacy({'mode': 'executing', 'revision': 2, 'objective': 'Act'}) == AccessState(False, 2)


@pytest.mark.parametrize(('name', 'args', 'safe'), [('current_time', {}, True),
    ('filesystem', {'action': 'read'}, True), ('filesystem', {'action': 'ls'}, True),
    ('filesystem', {'action': 'grep'}, True), ('filesystem', {'action': 'find'}, True),
    ('filesystem', {'action': 'write'}, False), ('filesystem', {}, False), ('bash', {}, False),
    ('mcp_read', {}, False), ('extension_read', {}, False), ('agent_spawn', {}, False),
    ('agent_control', {'action': 'queue'}, False), ('update_plan', {}, False)])
def test_hard_allowlist_never_trusts_tool_names_or_skills(name, args, safe):
    assert read_only_call(name, args) is safe


def test_packaged_planning_skill_not_a_second_tool_or_state_machine():
    skill = load_packaged_skill('xdog-planning')
    assert skill is not None and 'read-only' in skill.content.lower()
    assert importlib.util.find_spec('xdog.agent.planning') is None
