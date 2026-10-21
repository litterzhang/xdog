"""Read-only derived Goal display; task files remain authoritative."""
from __future__ import annotations

import hashlib
from typing import Any

from xdog.agent.extensions.views import answer

from .codec import json_text
from .models import identity
from .service import GoalService


class GoalView:
    def __init__(self, service: GoalService) -> None:
        self.service = service

    async def read(self, query: dict[str, Any]) -> dict[str, Any]:
        self.service._check()
        if type(query) is not dict or set(query) - {'goal_id', 'offset', 'limit'}:
            raise ValueError('Goal view is read-only and owner-bound')
        offset, limit = query.get('offset', 0), query.get('limit', 16)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError('Invalid Goal view page')
        goals = ((await self.service.store.get(identity(query['goal_id'])),) if 'goal_id' in query
                 else await self.service.list())
        rows = []
        for goal in goals[offset:offset + limit]:
            facts = await self.service.summary(goal.id)
            current = facts['current']
            report = facts['latest_report']
            rows.append({'id': goal.id, 'title': current['title'][:160], 'status': current['status'],
                         'version': current['version'], 'wait_for': current['wait_for'],
                         'reason': current['reason'][:160], 'next': {**facts['next'],
                             'reason': facts['next']['reason'][:160],
                             'preview_fields': ['reason'] if len(facts['next']['reason']) > 160 else []},
                         'automation_status': facts.get('automation', {}).get('status'),
                         'delivery_status': facts.get('delivery', {}).get('status'),
                         'execution_state': facts.get('execution', {}).get('state'),
                         'preview_fields': [name for name in ('title', 'reason') if len(current[name]) > 160],
                         'tasks': {'total': facts['task_count'], 'completed': facts['task_counts']['completed'],
                                   'counts': facts['task_counts'], 'revision': facts['tasks_revision']},
                         'report_path': facts['report_path'],
                         'latest_report': None if report is None else {
                             'version': report['version'], 'progress': report['progress'],
                             'summary': report['summary'][:160],
                             'preview_fields': ['summary'] if len(report['summary']) > 160 else []}})
        value = {'format': 'xdog.goal_view.v2', 'items': rows, 'total': len(goals), 'offset': offset,
                 'next_offset': offset + limit if offset + limit < len(goals) else None}
        value['revision'] = hashlib.sha256(json_text(value).encode()).hexdigest()
        self.service._check()
        result = answer(value)
        assert result is not None
        return result
