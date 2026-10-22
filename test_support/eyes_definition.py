"""Explicit Eyes JSON fixtures, not a production routing adapter."""
from __future__ import annotations


def definition(endpoint, session, name, source, instruction):
    source = {'id': 'source', **source}
    scheduled = source.get('type') == 'scheduler'
    return {'version': 2, 'name': name, 'sources': [source], 'detectors': [
        {'id': 'detector', 'type': 'scheduler.due' if scheduled else 'file.change', 'source': source['id']}],
        'handlers': [{'id': 'handler', 'type': 'acp',
                      'connection': {'transport': 'unix', 'path': str(endpoint)},
                      'session': session, 'delivery': 'enqueue', 'instruction': instruction}],
        'routes': [{'id': 'route', 'detector': 'detector', 'handler': 'handler',
                    'event_types': ['scheduler.due'] if scheduled else
                                   ['file.created', 'file.modified', 'file.deleted']}]}


def save(workspace, endpoint, session, name, source, instruction):
    import json
    path = workspace / (name + '.eyes.json')
    path.write_text(json.dumps(definition(endpoint, session, name, source, instruction)))
    return str(path)
