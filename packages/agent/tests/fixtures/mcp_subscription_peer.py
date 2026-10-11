import json
import sys
import threading

mode, log = sys.argv[1:]
subscriptions = {}
legacy = set()

def write(value):
    print(json.dumps({'jsonrpc': '2.0', **value}), flush=True)

for line in sys.stdin:
    message = json.loads(line)
    if mode == 'modern-slow-cancel' and message.get('method') == 'notifications/cancelled':
        # A local pipe flush is not proof this peer has processed a one-way notification.
        import time
        time.sleep(0.03)
    with open(log, 'a') as output:
        output.write(json.dumps(message) + '\n')
    identifier, method, params = message.get('id'), message.get('method'), message.get('params', {})
    if identifier is None:
        if method == 'notifications/cancelled':
            old = params['requestId']
            subscriptions.pop(old, None)
            # Deliberately late completion cannot settle/adopt a new watch.
            write({'id': old, 'result': {'resultType': 'complete'}})
        continue
    if method == 'server/discover':
        if mode.startswith('legacy'):
            write({'id': identifier, 'error': {'code': -32601, 'message': 'legacy'}})
        else:
            write({'id': identifier, 'result': {'supportedVersions': ['2026-07-28'],
                  'capabilities': {'resources': {'subscribe': True, 'listChanged': True},
                                   'tools': {'listChanged': True}, 'prompts': {'listChanged': True}}}})
    elif method == 'initialize':
        write({'id': identifier, 'result': {'protocolVersion': '2025-11-25',
              'capabilities': {'resources': {'subscribe': True, 'listChanged': True},
                                   'tools': {'listChanged': True}, 'prompts': {'listChanged': True}}}})
    elif method == 'tools/list':
        write({'id': identifier, 'result': {'tools': []}})
    elif method == 'resources/list':
        write({'id': identifier, 'result': {'resources': [{'uri': 'test://watched', 'name': 'watched'}]}})
    elif method == 'resources/read':
        write({'id': identifier, 'result': {'contents': [{'uri': params['uri'], 'text': 'original'}]}})
    elif method == 'subscriptions/listen':
        filters = params['notifications']
        if ('resourceSubscriptions' not in filters or any(filters.get(kind + 'ListChanged') is True
                for kind in ('tools', 'prompts', 'resources')) or len(filters['resourceSubscriptions']) > 1):
            subscriptions[identifier] = filters
            write({'method': 'notifications/subscriptions/acknowledged', 'params': {
                '_meta': {'io.modelcontextprotocol/subscriptionId': identifier}, 'notifications': filters}})
            if mode == 'catalog-auto':
                def changed(sid=identifier, selected=dict(filters)):
                    for kind in ('tools', 'prompts', 'resources'):
                        if selected.get(kind + 'ListChanged'):
                            write({'method': 'notifications/' + kind + '/list_changed', 'params': {'_meta': {
                                'io.modelcontextprotocol/subscriptionId': sid}}})
                threading.Timer(0.15, changed).start()
            continue
        uris = params['notifications']['resourceSubscriptions']
        if mode == 'preclose' and uris == ['test://refused']:
            write({'id': identifier, 'result': {'_meta': {'io.modelcontextprotocol/subscriptionId': identifier}}})
            continue
        subscriptions[identifier] = uris[0]
        write({'method': 'notifications/subscriptions/acknowledged', 'params': {
            '_meta': {'io.modelcontextprotocol/subscriptionId': identifier},
            'notifications': {'resourceSubscriptions': [] if mode == 'unsupported' else uris}}})
    elif method == 'resources/subscribe':
        legacy.add(params['uri'])
        write({'id': identifier, 'result': {}})
    elif method == 'resources/unsubscribe':
        legacy.discard(params['uri'])
        write({'id': identifier, 'result': {}})
    elif method == 'fixture/status':
        # Ordered request receipt proves all preceding notifications were processed.
        write({'id': identifier, 'result': {'modern': len(subscriptions), 'legacy': len(legacy)}})
    elif method == 'fixture/update':
        for sid, uri in subscriptions.items():
            if isinstance(uri, dict):
                for kind in ('tools', 'prompts', 'resources'):
                    if uri.get(kind + 'ListChanged') is True:
                        write({'method': 'notifications/' + kind + '/list_changed', 'params': {'_meta': {
                            'io.modelcontextprotocol/subscriptionId': sid}}})
                for resource in uri.get('resourceSubscriptions', []):
                    write({'method': 'notifications/resources/updated', 'params': {'uri': resource, '_meta': {
                        'io.modelcontextprotocol/subscriptionId': sid}}})
                continue
            write({'method': 'notifications/resources/updated', 'params': {'uri': uri, '_meta': {
                'io.modelcontextprotocol/subscriptionId': sid}}})
        for kind in ('tools', 'prompts', 'resources'):
            if mode.startswith('legacy'):
                write({'method': 'notifications/' + kind + '/list_changed', 'params': {}})
        for uri in legacy:
            write({'method': 'notifications/resources/updated', 'params': {'uri': uri}})
        write({'id': identifier, 'result': {}})
    elif method in {'fixture/end', 'fixture/error'}:
        for sid in list(subscriptions):
            if method == 'fixture/error':
                write({'id': sid, 'error': {'code': -32603, 'message': 'private peer error'}})
            else:
                write({'id': sid, 'result': {'resultType': 'complete'}})
        subscriptions.clear()
        write({'id': identifier, 'result': {}})
    else:
        write({'id': identifier, 'error': {'code': -32601, 'message': 'unknown'}})
