"""Owned offline legacy task peer: requests result only after exact CreateTaskResult."""
from __future__ import annotations

import json
import sys
from pathlib import Path

log, family = Path(sys.argv[1]), sys.argv[2]
method = 'sampling/createMessage' if family == 'sampling' else 'elicitation/create'
params = ({'messages': [{'role': 'user', 'content': {'type': 'text', 'text': 'TASK INPUT'}}], 'maxTokens': 20}
          if family == 'sampling' else {'message': 'Review task form', 'requestedSchema': {
              'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']}})
if family == 'sampling' and len(sys.argv) > 4 and sys.argv[4] == 'required':
    params.update(tools=[{'name': 'approved', 'inputSchema': {'type': 'object'}}],
                  toolChoice={'mode': 'required'})
params['task'] = {'ttl': int(sys.argv[3]) if len(sys.argv) > 3 else 60000, 'pollInterval': 500}


def send(row):
    print(json.dumps(row), flush=True)


for raw in sys.stdin:
    row = json.loads(raw)
    with log.open('a') as stream:
        stream.write(json.dumps(row) + '\n')
    action = row.get('method')
    if action == 'server/discover':
        send({'jsonrpc': '2.0', 'id': row['id'], 'error': {'code': -32601, 'message': 'legacy'}})
    elif action == 'initialize':
        send({'jsonrpc': '2.0', 'id': row['id'], 'result': {
            'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}})
    elif action == 'tools/list':
        send({'jsonrpc': '2.0', 'id': 'create', 'method': method, 'params': params})
        send({'jsonrpc': '2.0', 'id': row['id'], 'result': {'tools': []}})
    elif row.get('id') == 'create' and 'result' in row:
        task = row['result']['task']
        send({'jsonrpc': '2.0', 'id': 'get', 'method': 'tasks/get', 'params': {'taskId': task['taskId']}})
        send({'jsonrpc': '2.0', 'id': 'retrieve', 'method': 'tasks/result', 'params': {'taskId': task['taskId']}})
    elif (action == 'notifications/tasks/status' and row['params']['status'] == 'input_required'
          and len(sys.argv) > 4 and sys.argv[4] == 'cancel'):
        send({'jsonrpc': '2.0', 'id': 'peer-cancel', 'method': 'tasks/cancel',
              'params': {'taskId': row['params']['taskId']}})
    elif action == 'ping':
        send({'jsonrpc': '2.0', 'id': row['id'], 'result': {}})
