"""Thin tool and CLI share a real daemon; JSON alone selects destinations."""
from __future__ import annotations

import json


async def invoke(binding, action, *, owner='caller-selected', **fields):
    result = json.loads(await binding.tools[0].execute('call',
        {'action': action, 'owner': owner, **fields}))
    assert result['exit_code'] == 0, result['stderr']
    return json.loads(result['stdout'])


def raw_definition(root):
    return {'version': 2, 'name': 'raw-events',
            'sources': [{'id': 'files', 'type': 'file', 'root': str(root), 'include': ['*.txt'],
                         'poll_interval_ms': 50}],
            'detectors': [{'id': 'change', 'type': 'file.change', 'source': 'files',
                           'debounce_ms': 50, 'max_batch_delay_ms': 100}],
            'handlers': [{'id': 'sink', 'type': 'events'}],
            'routes': [{'id': 'route', 'detector': 'change', 'handler': 'sink', 'event_types': ['file.created']}]}
