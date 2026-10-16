"""Owned passive-notice peer; it has no subscription registry or background work."""
from __future__ import annotations

import json
import sys

mode, log = sys.argv[1:]


def write(value):
    print(json.dumps({'jsonrpc': '2.0', **value}), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    with open(log, 'a') as output:
        output.write(json.dumps(message) + '\n')
    identifier, method, params = message.get('id'), message.get('method'), message.get('params', {})
    if identifier is None:
        continue
    if method == 'server/discover':
        if mode.startswith('legacy'):
            write({'id': identifier, 'error': {'code': -32601, 'message': 'legacy'}})
        else:
            write({'id': identifier, 'result': {'supportedVersions': ['2026-07-28'], 'capabilities': {
                'resources': {'subscribe': True, 'listChanged': True},
                'tools': {'listChanged': True}, 'prompts': {'listChanged': True}}}})
    elif method == 'initialize':
        write({'id': identifier, 'result': {'protocolVersion': '2025-11-25', 'capabilities': {
            'resources': {'subscribe': True, 'listChanged': True},
            'tools': {'listChanged': True}, 'prompts': {'listChanged': True}}}})
    elif method == 'tools/list':
        write({'id': identifier, 'result': {'tools': [{'name': 'echo', 'inputSchema': {
            'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}}]}})
    elif method == 'tools/call':
        write({'id': identifier, 'result': {'content': [{'type': 'text', 'text': params['arguments']['value']}]}})
    elif method == 'resources/list':
        write({'id': identifier, 'result': {'resources': [{'uri': 'test://watched', 'name': 'watched'}]}})
    elif method == 'resources/read':
        write({'id': identifier, 'result': {'contents': [{'uri': params['uri'], 'text': 'ordinary resource'}]}})
    elif method == 'prompts/list':
        write({'id': identifier, 'result': {'prompts': [{'name': 'review'}]}})
    elif method == 'prompts/get':
        write({'id': identifier, 'result': {'messages': [
            {'role': 'user', 'content': {'type': 'text', 'text': 'ordinary prompt'}}]}})
    elif method == 'fixture/update':
        kind = params.get('kind', 'tools')
        write({'method': 'notifications/' + kind + '/list_changed', 'params': params.get('notice', {})})
        write({'id': identifier, 'result': {}})
    elif method == 'fixture/resource-notice':
        write({'method': 'notifications/resources/updated', 'params': {'uri': 'test://not-fetched'}})
        write({'id': identifier, 'result': {}})
    elif method == 'fixture/old-receipt':
        write({'id': -1, 'result': {'_meta': {'io.modelcontextprotocol/subscriptionId': -1}}})
        write({'id': identifier, 'result': {'value': 'current result'}})
    elif method == 'fixture/old-ack':
        write({'method': 'notifications/subscriptions/acknowledged', 'params': {
            '_meta': {'io.modelcontextprotocol/subscriptionId': -1},
            'notifications': {'resourceSubscriptions': ['test://not-fetched']}}})
        write({'id': identifier, 'result': {}})
    elif method == 'ping':
        write({'id': identifier, 'result': {}})
    else:
        write({'id': identifier, 'error': {'code': -32601, 'message': 'unsupported'}})
