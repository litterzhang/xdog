"""Bounded, terminal-safe text views of recorded Goal facts (no execution policy)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

__all__ = ['format_status', 'format_history', 'format_change']

# Consume terminal strings as well as CSI sequences, including their C1 forms.
_TERMINAL = re.compile(
    r'(?:\x1b\]|\x9d)[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c|$)'
    r'|(?:\x1b[P^_X]|[\x90\x98\x9e\x9f])[^\x1b\x9c]*(?:\x1b\\|\x9c|$)'
    r'|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]'
    r'|\x1b[ -/]*[@-~]'
)
_DETAILS = 'Full data: /goal status --json (… = omitted/truncated)'


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 180) -> str:
    """Keep Unicode text, never decode literal escapes or stringify containers."""
    if not isinstance(value, (str, int, float, bool)):
        return '—'
    raw = _TERMINAL.sub('', str(value))
    safe = ''.join(
        ' ' if ch.isspace() else ch
        for ch in raw
        if ch.isspace() or unicodedata.category(ch) not in {'Cc', 'Cs', 'Cf'} or ch in '\u200c\u200d'
    )
    safe = ' '.join(safe.split())
    return (safe[:limit - 1] + '…') if len(safe) > limit else safe or '—'


def _count(value: Any) -> str:
    return str(value) if type(value) is int and 0 <= value < 10**12 else '?'


def _heading(goal: dict[str, Any]) -> str:
    return f"Goal: {_text(goal.get('title'), 140)} [{_text(goal.get('id'), 72)}]"


def _state(goal: dict[str, Any]) -> list[str]:
    lines = [f"Status: {_text(goal.get('status'), 40)} | wait_for: {_text(goal.get('wait_for') or 'none', 40)}"]
    if goal.get('status') == 'active':
        lines.append('Responsibility: main_agent — next useful work; active does not mean running.')
    elif goal.get('status') == 'waiting':
        owner = {'user': 'user', 'host': 'host', 'tasks': 'task_workers; main_agent checks evidence'}.get(
            goal.get('wait_for') or '', 'not recorded')
        lines.append(f'Responsibility: {owner}')
    elif goal.get('status') == 'paused':
        lines.append('Responsibility: user — explicit resume; automatic continuation stopped.')
    elif goal.get('status') in {'completed', 'cleared'}:
        lines.append('Responsibility: none for this Goal; user may choose a new Goal.')
    if goal.get('status') == 'waiting' and goal.get('wait_for') == 'user':
        for name, label in [('question', 'Question'), ('impact', 'Impact'), ('resume_when', 'Resume when')]:
            lines.append(f"{label}: {_text(goal[name]) if goal.get(name) else 'not recorded'}")

    if goal.get('reason'):
        lines.append(f"Reason: {_text(goal['reason'])}")
    return lines


def _question_delivery(facts: dict[str, Any]) -> list[str]:
    question = _mapping(facts.get('question'))
    if not question:
        return []
    delivery = _mapping(question.get('delivery'))
    return [f"Question reference: {_text(question.get('id') or 'not recorded', 180)}",
            f"Question delivery: {_text(delivery.get('status') or 'unavailable', 32)}; "
            'saving is not proof of delivery to the user.']


def _transition(facts: dict[str, Any], goal: dict[str, Any]) -> list[str]:
    """Show recorded provenance only; never infer it from status or the command route."""
    data = _mapping(facts.get('last_transition'))
    if not goal and not data:
        return []
    version = data.get('version')
    current_version = goal.get('version')
    version_matches = (type(version) is int and type(current_version) is int
                       and 0 < version <= current_version)
    if (data.get('availability') != 'available' or data.get('format') != 'xdog.goal_transition.v1'
            or (goal and (data.get('goal_id') != goal.get('id') or not version_matches))):
        return ['Last transition: unavailable — Lifecycle change origin not recorded']

    def state(value: Any) -> str:
        if value is None:
            return 'none'
        item = _mapping(value)
        return f"{_text(item.get('status'), 16)}/{_text(item.get('wait_for') or 'none', 8)}"

    authorized = data.get('user_authorized')
    authorization = 'true' if authorized is True else 'false' if authorized is False else 'unknown'
    line = (f"Last transition: {_text(data.get('action'), 12)}"
            f" | {_text(data.get('origin'), 16)}/{_text(data.get('actor'), 7)}"
            f" | user_authorized={authorization}")
    if data.get('command'):
        line += f" | {_text(data['command'], 24)}"
    line += f" | {state(data.get('from'))} → {state(data.get('to'))}"
    if (goal and data.get('goal_id') == goal.get('id')
            and version_matches
            and _mapping(data.get('to')) == {'status': goal.get('status'), 'wait_for': goal.get('wait_for')}
            and data.get('action') == 'resume' and data.get('origin') == 'native_command'
            and data.get('actor') == 'user' and authorized is True and data.get('command') == '/goal resume'):
        line += ' | verified native user resume'
    line += ' (not tool authorization or repair)'
    return [_text(line, 290)]


def _next(value: Any, goal: dict[str, Any]) -> list[str]:
    # An inconsistent/stale next hint must not obscure a recorded human wait.
    if goal.get('status') == 'waiting' and goal.get('wait_for') == 'user':
        return ['Next: waiting for user input; no automatic retry.']
    if goal.get('status') == 'waiting' and goal.get('wait_for') == 'host':
        return ['Next: waiting for actual host recovery; no automatic retry.']
    if not isinstance(value, dict):
        return [f'Next: {_text(value)}'] if value else []
    if goal.get('status') == 'completed' and value.get('action') == 'create':
        # A create hint means no executable Goal, not an empty current binding.
        value = {**value, 'reason': 'Current Goal is completed; create replaces it, /goal clear releases it'}
    line = f"Next: {_text(value.get('action'), 60)}"
    if value.get('owner'):
        line += f" | owner: {_text(value['owner'], 32)}"
    if value.get('reason'):
        line += f" — {_text(value['reason'], 140)}"
    if value.get('requires_user') is True:
        line += ' (requires user)'
    return [line]


def _metadata(facts: dict[str, Any]) -> list[str]:
    lines = []
    for key, field in [('automation', 'status'), ('delivery', 'status'), ('execution', 'state')]:
        data = _mapping(facts.get(key))
        if not data:
            continue
        line = f'{key.capitalize()}: {_text(data.get(field), 40)}'
        # Do not expand receipts, evidence, or arbitrary nested host metadata.
        for detail in ('reason', 'error', 'notice'):
            if data.get(detail):
                line += f" | {_text(data[detail], 120)}"
                break
        lines.append(line)
    faults = facts.get('runtime_faults')
    if isinstance(faults, list) and faults:
        fault = _mapping(faults[-1])
        lines.append(f"Runtime fault: {_text(fault.get('source'), 24)}/{_text(fault.get('status'), 32)}"
                     f" — {_text(fault.get('error'), 140)}; Goal business state unchanged.")
    if _mapping(facts.get('automation')) or _mapping(facts.get('delivery')):
        lines.append('Automation/delivery are recorded metadata, not proof of Agent execution or Goal completion.')
    return lines


def _legacy(facts: dict[str, Any]) -> list[str]:
    legacy = facts.get('legacy_storage')
    if isinstance(legacy, (list, tuple)) and legacy:
        return [f'Legacy storage: {len(legacy)} retained unchanged (paths omitted).']
    return []


def format_status(facts: dict[str, Any]) -> str:
    """Render GoalService.summary facts without deriving progress or completion."""
    goal = _mapping(facts.get('current'))
    if not goal:
        return '\n'.join(['No current Goal. Create with /goal create <outcome> or view /goal history.',
                          *_transition(facts, goal), *_legacy(facts), _DETAILS])
    lines = [_heading(goal), *_state(goal), *_question_delivery(facts), *_transition(facts, goal)]
    counts = _mapping(facts.get('task_counts'))
    lines.append(f"Tasks: {_count(counts.get('completed'))}/{_count(facts.get('task_count'))} completed"
                 f" | blocked: {_count(counts.get('blocked'))}"
                 f" | in_progress: {_count(counts.get('in_progress'))}"
                 f" | pending: {_count(counts.get('pending'))}")
    report = _mapping(facts.get('latest_report'))
    if report:
        lines.append(f"Main Agent assessment (not a script verdict): {_text(report.get('progress'), 32)}")
        lines.append(f"Progress: {_text(report.get('summary'))}")
    else:
        lines.append('Main Agent assessment: no recorded progress report.')
    preview = facts.get('tasks_preview')
    tasks = preview if isinstance(preview, list) else []
    for item in tasks[:6]:
        task = _mapping(item)
        title = _text(task.get('title'), 140)
        if 'title' in (task.get('preview_fields') or []) and not title.endswith('…'):
            title += '…'
        lines.append(f"  [{_text(task.get('status'), 32)}] {title}")
    total = facts.get('task_count')
    if len(tasks) > 6 or facts.get('tasks_preview_only') or (type(total) is int and total > len(tasks[:6])):
        lines.append('  … more tasks omitted; see /goal status --json')
    lines.extend(_next(facts.get('next'), goal))
    lines.extend(_metadata(facts))
    if facts.get('workspace'):
        lines.append(f"Workspace: {_text(facts['workspace'])}")
    return '\n'.join([*lines, *_legacy(facts), _DETAILS])


def format_history(facts: dict[str, Any]) -> str:
    """Render at most one 16-record history page, with the supplied next offset."""
    value = facts.get('goals')
    goals = value if isinstance(value, list) else []
    lines = [f"Goal history: {_count(facts.get('total'))} total"]
    if not goals:
        lines.append('No Goals on this page. Create with /goal create <outcome> or view /goal history.')
    for item in goals[:16]:
        goal = _mapping(item)
        lines.append(f"  [{_text(goal.get('status'), 32)}] {_text(goal.get('title'), 110)}"
                     f" [{_text(goal.get('id'), 64)}] v{_count(goal.get('version'))}")
    if len(goals) > 16:
        lines.append('… additional records omitted (16 per page).')
    offset = facts.get('next_offset')
    if type(offset) is int and offset >= 0:
        lines.append(f'Next page (16 per page): /goal history {offset}')
    return '\n'.join([*lines, *_legacy(facts), 'Full history data: /goal history --json (… = omitted/truncated)'])


def format_change(action: str, facts: dict[str, Any]) -> str:
    """Describe an actual change response, not an assumed action outcome."""
    goal = _mapping(facts.get('goal'))
    lines = [f"Goal change: {_text(action, 40)}", _heading(goal), *_state(goal),
             *_question_delivery(facts), *_transition(facts, goal)]
    if facts.get('notice'):
        lines.append(f"Notice: {_text(facts['notice'], 240)}")
    lines.extend(_next(facts.get('next'), goal))
    lines.extend(_metadata(facts))
    lines.append('No worker start or execution success is implied by this response.')
    return '\n'.join([*lines, _DETAILS])
