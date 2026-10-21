"""Lifecycle provenance is a compact diagnostic fact, never execution authority."""

from copy import deepcopy
from typing import Any

import pytest
from xdog.skills.goal.presentation import format_change, format_history, format_status


def transition_facts() -> dict[str, Any]:
    goal = {'id': 'goal-one', 'title': '恢复任务 🐶', 'version': 3, 'status': 'active', 'wait_for': ''}
    return {
        'current': goal,
        'goal': goal,
        'last_transition': {
            'availability': 'available', 'format': 'xdog.goal_transition.v1',
            'goal_id': 'goal-one', 'version': 3, 'action': 'resume',
            'origin': 'native_command', 'actor': 'user', 'user_authorized': True,
            'command': '/goal resume',
            'from': {'status': 'paused', 'wait_for': ''},
            'to': {'status': 'active', 'wait_for': ''},
        },
    }


def transition_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.startswith('Last transition:')]
    assert len(lines) == 1
    return lines[0]


@pytest.mark.parametrize('view', ['status', 'change'])
def test_native_user_resume_is_verified_once_without_changing_existing_status(view):
    data = transition_facts()
    before = deepcopy(data)
    text = format_status(data) if view == 'status' else format_change('resume', data)
    line = transition_line(text)
    assert 'resume | native_command/user | user_authorized=true | /goal resume' in line
    assert 'paused/none → active/none' in line
    assert 'verified native user resume' in line
    assert '(not tool authorization or repair)' in line
    assert len(line) < 300
    assert text.count('Status: active | wait_for: none') == 1
    assert 'No worker start or execution success is implied' in text or view == 'status'
    without = deepcopy(data)
    del without['last_transition']
    baseline = format_status(without) if view == 'status' else format_change('resume', without)
    assert [x for x in text.splitlines() if not x.startswith('Last transition:')] == [
        x for x in baseline.splitlines() if not x.startswith('Last transition:')
    ]
    assert data == before


@pytest.mark.parametrize('action', ['create', 'pause', 'resume', 'activate', 'wait', 'complete', 'clear'])
@pytest.mark.parametrize('origin,actor', [('native_command', 'user'), ('model_tool', 'agent'),
                                          ('runtime', 'host'), ('service', 'unknown')])
def test_actions_and_origins_are_recorded_not_inferred_from_display_route(action, origin, actor):
    data = transition_facts()
    change = data['last_transition']
    change.update(action=action, origin=origin, actor=actor, user_authorized=False,
                  command=f'/goal {action}' if origin == 'native_command' else '')
    if action == 'create':
        change['from'] = None
    text = format_change('resume', data)
    line = transition_line(text)
    assert f'Last transition: {action} | {origin}/{actor} | user_authorized=false' in line
    assert 'verified native user resume' not in text
    assert ('/goal ' + action in line) == (origin == 'native_command')
    if action == 'create':
        assert 'none → active/none' in line


@pytest.mark.parametrize('patch', [
    {'origin': 'model_tool'}, {'actor': 'agent'}, {'user_authorized': False},
    {'user_authorized': 'true'}, {'user_authorized': 1}, {'action': 'wait'},
    {'command': ''}, {'command': '/goal resume extra'}, {'command': '/goal\nresume'},
    {'format': 'unknown'}, {'availability': 'unavailable'}, {'goal_id': 'other'},
    {'version': 4}, {'version': '3'}, {'version': 0},
])
def test_only_exact_owner_bound_native_resume_facts_can_be_verified(patch):
    data = transition_facts()
    data['last_transition'].update(patch)
    text = format_status(data)
    assert 'verified native user resume' not in text
    assert 'Status: active' in text


@pytest.mark.parametrize('value', [None, {}, [], {
    'availability': 'unavailable', 'goal_id': 'goal-one', 'version': 3,
    'reason': 'Lifecycle change origin not recorded',
}])
def test_unavailable_and_missing_history_are_not_inferred_as_user_resume(value):
    data = transition_facts()
    data['last_transition'] = value
    for text in [format_status(data), format_change('resume', data)]:
        assert transition_line(text) == 'Last transition: unavailable — Lifecycle change origin not recorded'
        assert 'verified native user resume' not in text
        assert 'user_authorized=true' not in text


@pytest.mark.parametrize('status,wait_for', [('paused', ''), ('waiting', 'user'), ('waiting', 'host')])
def test_transition_never_overwrites_current_pause_or_wait(status, wait_for):
    data = transition_facts()
    data['current'].update(status=status, wait_for=wait_for)
    data['next'] = {'action': 'main_agent_work'}
    for text in [format_status(data), format_change('resume', data)]:
        assert f'Status: {status} | wait_for: {wait_for or "none"}' in text
        assert 'Status: active' not in text
        assert 'verified native user resume' not in text
        if wait_for == 'user':
            assert 'waiting for user input; no automatic retry' in text
            assert 'main_agent_work' not in text


def test_clear_transition_can_be_displayed_without_a_current_binding():
    data = transition_facts()
    data['current'] = None
    data['last_transition'].update(action='clear', command='/goal clear',
                                   to={'status': 'cleared', 'wait_for': ''})
    text = format_status(data)
    assert 'No current Goal' in text
    assert 'Last transition: clear | native_command/user' in text
    assert '→ cleared/none' in text
    assert 'verified native user resume' not in text


def test_history_remains_compact_without_reconstructing_transition_facts():
    data = transition_facts()
    data.update(goals=[data['goal']], total=1)
    text = format_history(data)
    assert '[active] 恢复任务 🐶 [goal-one] v3' in text
    assert 'Last transition:' not in text
    assert '/goal resume' not in text


def test_transition_values_are_terminal_safe_bounded_and_never_dump_nested_data():
    data = transition_facts()
    change = data['last_transition']
    payload = '\x1b]52;c;HIDDEN_CLIPBOARD\x07中文 🐶\n' * 5000
    change.update(action=payload, origin=payload, actor=payload, command=payload,
                  user_authorized={'private': 'HIDDEN_AUTH'},
                  evidence=['HIDDEN_EVIDENCE'] * 100)
    change['from'] = {'status': payload, 'wait_for': payload}
    change['to'] = {'status': {'private': 'HIDDEN_STATE'}, 'wait_for': payload}
    before = deepcopy(data)
    for text in [format_status(data), format_change('resume', data)]:
        line = transition_line(text)
        assert len(line) <= 290
        assert 'HIDDEN_' not in text and '\x1b' not in text and '\x07' not in text
        assert 'user_authorized=unknown' in line
        assert 'verified native user resume' not in text
        text.encode('utf-8')
    assert data == before


def test_content_revisions_do_not_erase_last_verified_lifecycle_transition():
    data = transition_facts()
    data['current']['version'] = 5
    for text in [format_status(data), format_change('resume', data)]:
        assert 'verified native user resume' in transition_line(text)
        assert 'unavailable' not in text


async def test_actual_native_resume_change_and_readonly_status_show_same_provenance(tmp_path, monkeypatch):
    import json

    from packages.skills.tests.goal.test_runtime import close, setup

    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    try:
        state['state'] = 'running'  # Keep automatic reminder delivery deferred during display checks.
        await parent.extensions.command('goal', 'create 验证用户恢复来源')
        await parent.extensions.command('goal', 'pause 等待明确恢复')
        change = await parent.extensions.command('goal', 'resume')
        assert 'verified native user resume' in transition_line(change)
        assert 'paused/none → active/none' in change
        await runtime.service.plan('内容修改不会擦除用户恢复来源')
        authorized[0] = False  # Read-only status does not need a new user authorization.
        text = await parent.extensions.command('goal', 'status')
        raw = json.loads(await parent.extensions.command('goal', 'status --json'))
        assert 'verified native user resume' in transition_line(text)
        assert raw['last_transition']['origin'] == 'native_command'
        assert raw['last_transition']['command'] == '/goal resume'
        assert raw['last_transition']['version'] < raw['current']['version']
        assert raw['last_transition']['user_authorized'] is True
        assert not sent
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('view', ['status', 'change'])
@pytest.mark.parametrize('wait_for', ['tasks', 'user', 'host'])
@pytest.mark.parametrize('user_authorized', [False, True])
def test_model_activation_is_not_presented_as_native_user_resume(view, wait_for, user_authorized):
    data = transition_facts()
    data['last_transition'].update(
        action='activate', origin='model_tool', actor='agent',
        user_authorized=user_authorized, command='',
        **{'from': {'status': 'waiting', 'wait_for': wait_for}},
    )
    before = deepcopy(data)
    text = format_status(data) if view == 'status' else format_change('activate', data)
    line = transition_line(text)
    assert f'activate | model_tool/agent | user_authorized={str(user_authorized).lower()}' in line
    assert f'waiting/{wait_for} → active/none' in line
    assert '(not tool authorization or repair)' in line
    assert 'verified native user resume' not in text
    assert '/goal resume' not in text
    assert 'Status: active | wait_for: none' in text
    assert len(line) < 300
    assert data == before
