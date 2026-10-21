"""Native Goal text is a bounded view, never a policy or execution verdict."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from xdog.skills.goal.presentation import format_change, format_history, format_status


def facts(status: str = 'active', wait_for: str = '') -> dict[str, Any]:
    return {
        'current': {'id': 'goal-one', 'title': '完成中文任务 🐶', 'status': status, 'wait_for': wait_for,
                    'reason': '', 'outcome': 'HIDDEN_OUTCOME', 'criteria': ['HIDDEN_CRITERIA'] * 100},
        'task_count': 4,
        'task_counts': {'completed': 1, 'blocked': 1, 'in_progress': 1, 'pending': 1},
        'tasks_preview': [{'title': '检查状态 👩‍💻', 'status': 'in_progress'}],
        'latest_report': {'progress': 'advanced', 'summary': '主 Agent 已核对，仍需用户确认',
                          'completed': ['HIDDEN_COMPLETED'], 'evidence': ['HIDDEN_EVIDENCE'] * 100},
        'next': {'action': 'main_agent_work', 'reason': 'Goal is active', 'requires_user': False},
        'automation': {'status': 'watching'},
        'delivery': {'status': 'accepted', 'receipt': {'large': ['HIDDEN_RECEIPT'] * 100}},
        'execution': {'state': 'idle'},
        'workspace': '/项目/.xdog/goals/goal-one',
        'legacy_storage': ['/private/journal.sqlite3', '/private/goals.json'],
    }


def test_empty_goal_offers_only_explicit_creation_or_history():
    text = format_status({'current': None, 'legacy_storage': ['/private/goals.json']})
    assert 'No current Goal' in text
    assert '/goal create <outcome>' in text and '/goal history' in text
    assert 'Legacy storage: 1 retained unchanged' in text
    assert '/private' not in text
    assert 'started' not in text and 'Tasks:' not in text


@pytest.mark.parametrize('status,wait_for', [('active', ''), ('waiting', 'user'), ('waiting', 'tasks'),
                                           ('paused', ''), ('completed', '')])
def test_recorded_state_and_main_agent_report_are_not_task_count_verdicts(status, wait_for):
    data = facts(status, wait_for)
    text = format_status(data)
    assert f'Status: {status} | wait_for: {wait_for or "none"}' in text
    assert 'Tasks: 1/4 completed | blocked: 1 | in_progress: 1 | pending: 1' in text
    assert 'Main Agent assessment (not a script verdict): advanced' in text
    assert data['latest_report']['summary'] in text
    assert '完成中文任务 🐶 [goal-one]' in text
    assert 'Workspace: /项目/.xdog/goals/goal-one' in text
    assert text.count('Workspace:') == 1
    assert 'HIDDEN_' not in text
    assert '/private' not in text
    assert 'Automation: watching' in text and 'Delivery: accepted' in text and 'Execution: idle' in text
    assert 'not proof of Agent execution or Goal completion' in text


def test_waiting_user_overrides_misleading_next_hint_and_shows_block_reason():
    data = facts('waiting', 'user')
    data['current']['reason'] = '阻塞：需要确认实际账号'
    text = format_status(data)
    assert 'Reason: 阻塞：需要确认实际账号' in text
    assert 'Next: waiting for user input; no automatic retry.' in text
    assert 'main_agent_work' not in text


def test_blocked_task_is_not_inferred_goal_status():
    data = facts()
    data['tasks_preview'] = [{'status': 'blocked', 'title': '等待凭证'}]
    data['current']['reason'] = '暂缺凭证'
    text = format_status(data)
    assert 'Status: active' in text
    assert '[blocked] 等待凭证' in text and 'Reason: 暂缺凭证' in text
    data['task_counts'] = {'completed': 4, 'blocked': 0, 'pending': 0, 'in_progress': 0}
    text = format_status(data)
    assert 'Status: active' in text and 'Tasks: 4/4 completed' in text
    assert 'Status: completed' not in text


def test_missing_counts_and_report_are_not_fabricated():
    text = format_status({'current': {'title': '无任务', 'id': 'g', 'status': 'active'}})
    assert 'Tasks: ?/? completed' in text
    assert 'no recorded progress report' in text
    assert 'Status: active' in text


def test_long_values_are_single_line_bounded_with_six_task_preview_and_json_hint():
    data = facts()
    long = '内容\n' * 10000
    for key in ['title', 'id', 'reason', 'wait_for']:
        data['current'][key] = long
    data['latest_report']['summary'] = long
    data['tasks_preview'] = [{'title': long, 'status': long} for _ in range(16)]
    data['task_count'] = 100
    data['workspace'] = long
    data['next'] = {'action': long, 'reason': long, 'requires_user': True}
    data['automation'] = {'status': long, 'reason': long}
    text = format_status(data)
    assert len(text) < 4500
    assert max(map(len, text.splitlines())) < 300
    assert sum(line.startswith('  [') for line in text.splitlines()) == 6
    assert '… more tasks omitted' in text
    assert '/goal status --json' in text and 'omitted/truncated' in text


def test_summary_already_truncated_title_is_marked():
    data = facts()
    data['tasks_preview'] = [{'title': '字段预览', 'status': 'pending', 'preview_fields': ['title']}]
    assert '[pending] 字段预览…' in format_status(data)


@pytest.mark.parametrize('render', [format_status, format_history, lambda value: format_change('create', value)])
def test_chinese_emoji_and_literal_escapes_are_preserved_without_mutation(render):
    data = facts()
    title = r'中文 👩‍💻 🐶 字面量 \u4f60 \n'
    data['current']['title'] = title
    data['goal'] = deepcopy(data['current'])
    data['goals'] = [deepcopy(data['current'])]
    before = deepcopy(data)
    text = render(data)
    assert title in text
    assert '字面量 你' not in text
    assert data == before


@pytest.mark.parametrize('payload', [
    '\x1b[31m红色\x1b[0m', '\x9b31m红色\x9b0m',
    '\x1b]8;;https://unsafe.test\x07链接\x1b]8;;\x07',
    '\x9d52;c;HIDDEN_CLIPBOARD\x9c内容',
    '\x1bP HIDDEN_TERMINAL\x1b\\内容', '\x1b]unterminated',
    '标题\r\n伪造行\t\b\x00\x7f\u202e尾部\ud800',
])
def test_terminal_controls_cannot_escape_into_any_view(payload):
    import unicodedata

    data = facts()
    data['current']['title'] = payload
    data['goal'] = deepcopy(data['current'])
    data['goals'] = [deepcopy(data['current'])]
    data['notice'] = payload
    data['next'] = payload
    for text in [format_status(data), format_history(data), format_change(payload, data)]:
        assert '\x1b' not in text
        assert 'HIDDEN_' not in text
        assert all(c in '\n\u200c\u200d' or unicodedata.category(c) not in {'Cc', 'Cs', 'Cf'} for c in text)
        text.encode('utf-8')


def test_history_paginates_sixteen_records_and_omits_legacy_paths():
    data = {'goals': [{'id': f'g{i}', 'title': f'历史目标 {i}', 'status': 'paused', 'version': 3}
                      for i in range(16)], 'total': 35, 'next_offset': 32,
            'legacy_storage': ['/secret/legacy.json']}
    text = format_history(data)
    assert 'Goal history: 35 total' in text
    assert text.count('  [paused]') == 16
    assert '历史目标 15 [g15] v3' in text
    assert '/goal history 32' in text and '16 per page' in text
    assert '/secret' not in text
    data['next_offset'] = None
    assert 'Next page' not in format_history(data)
    data['goals'] = []
    assert 'No Goals on this page' in format_history(data)


def test_history_defensively_bounds_overfull_and_long_page():
    data = {'goals': [{'id': 'g' * 10000, 'title': '中' * 10000, 'status': 's' * 10000,
                       'version': 1} for _ in range(100)], 'total': 100, 'next_offset': 16}
    text = format_history(data)
    assert text.count('  [') == 16 and len(text) < 4500
    assert '… additional records omitted' in text
    assert max(map(len, text.splitlines())) < 250


@pytest.mark.parametrize('action,status', [('create', 'active'), ('pause', 'paused'),
                                          ('resume', 'waiting'), ('clear', 'cleared')])
def test_change_displays_actual_status_and_notice_not_action_based_success(action, status):
    data = facts(status, 'user' if status == 'waiting' else '')
    data['goal'] = data['current']
    data['notice'] = '自动提醒已停；已接受的输入、工具和子任务未取消'
    data['next'] = 'The current main Agent follows the goal skill; no worker was started here'
    text = format_change(action, data)
    assert f'Goal change: {action}' in text
    assert f'Status: {status}' in text
    assert data['notice'] in text
    assert 'No worker start or execution success is implied' in text
    assert 'HIDDEN_' not in text
    if action == 'resume':
        assert 'waiting for user input; no automatic retry' in text
        assert 'Status: active' not in text


def test_change_long_notice_and_next_are_bounded():
    data = {'goal': {'title': '长' * 5000, 'id': 'g', 'status': 'paused'},
            'notice': '提示\n' * 5000, 'next': '下一步\n' * 5000}
    text = format_change('pause', data)
    assert len(text) < 1200 and max(map(len, text.splitlines())) < 300
    assert '…' in text and '/goal status --json' in text


@pytest.mark.parametrize('progress', ['baseline', 'advanced', 'unchanged', 'regressed'])
def test_main_agent_progress_is_reported_verbatim_without_a_service_verdict(progress):
    data = facts()
    data['latest_report']['progress'] = progress
    data['latest_report']['summary'] = '主 Agent 判断：尚未完成'
    text = format_status(data)
    assert f'Main Agent assessment (not a script verdict): {progress}' in text
    assert 'Progress: 主 Agent 判断：尚未完成' in text
    assert 'Status: active' in text


def test_unknown_delivery_and_unavailable_execution_do_not_imply_success_or_replay():
    data = facts()
    data['delivery'] = {'status': 'unknown', 'notice': '发送结果不确定，请核对原回执'}
    data['execution'] = {'state': 'unavailable'}
    data['next'] = {'action': 'inspect_delivery', 'reason': 'Do not replay blindly', 'requires_user': True}
    text = format_status(data)
    assert 'Delivery: unknown | 发送结果不确定，请核对原回执' in text
    assert 'Execution: unavailable' in text
    assert 'Next: inspect_delivery — Do not replay blindly (requires user)' in text
    assert 'not proof of Agent execution or Goal completion' in text


@pytest.mark.parametrize('view', ['status', 'change'])
@pytest.mark.parametrize('reason', ['No current Goal', 'No current executable Goal'])
def test_completed_current_with_create_hint_keeps_binding_and_does_not_claim_absence(view, reason):
    data = facts('completed')
    data['goal'] = data['current']
    data['next'] = {'action': 'create', 'reason': reason, 'requires_user': True}
    before = deepcopy(data)
    text = format_status(data) if view == 'status' else format_change('complete', data)
    assert 'Goal: 完成中文任务 🐶 [goal-one]' in text
    assert 'Status: completed | wait_for: none' in text
    assert ('Next: create — Current Goal is completed; create replaces it, '
            '/goal clear releases it (requires user)') in text
    assert 'No current' not in text
    assert 'Status: cleared' not in text
    assert '/goal status --json' in text
    assert data == before


def test_completed_current_without_next_hint_is_not_an_empty_current_binding():
    data = facts('completed')
    del data['next']
    text = format_status(data)
    assert 'Goal: 完成中文任务 🐶 [goal-one]' in text
    assert 'Status: completed' in text
    assert 'No current Goal' not in text
    assert 'Next:' not in text  # Do not invent a lifecycle operation.


@pytest.mark.parametrize('view', ['status', 'change'])
def test_waiting_host_overrides_stale_work_hint_without_claiming_recovery(view):
    data = facts('waiting', 'host')
    data['goal'] = data['current']
    data['current']['reason'] = '等待核对原执行结果'
    before = deepcopy(data)
    text = format_status(data) if view == 'status' else format_change('activate', data)
    assert 'Status: waiting | wait_for: host' in text
    assert 'Reason: 等待核对原执行结果' in text
    assert 'Next: waiting for actual host recovery; no automatic retry.' in text
    assert 'main_agent_work' not in text
    assert 'Status: active' not in text
    assert data == before
