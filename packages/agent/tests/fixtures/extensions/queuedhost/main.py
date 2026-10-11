import json

from xdog.ai.types import ImageContent

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


def setup(api):
    async def request(args, context):
        images = (ImageContent(data=PNG, mime_type='image/png'),) if args.get('image') else ()
        queued = await api.request_queued_input('REVIEWED-QUEUE /quit', delivery=args.get('delivery', 'follow_up'),
                                                images=images, timeout=10)
        if args.get('ending') == 'fail':
            raise ValueError('Failed after accepted queue review')
        if args.get('ending') == 'error':
            return {'content': [{'type': 'text', 'text': 'ERROR AFTER QUEUE REVIEW'}], 'isError': True}
        return 'QUEUED-HOST-' + str(queued)

    async def command(args, context):
        return await request(json.loads(args or '{}'), context)

    async def pending(args, context):
        return 'PENDING-MESSAGES ' + json.dumps(await api.has_pending_messages())

    api.command('request', 'Idle commands cannot borrow active-run queue authority', command)
    api.command('pending', 'Read only whether original owner has queued messages', pending)
    api.tool('request', 'Request reviewed same-run input without borrowing a draft', {'type': 'object'}, request)

    async def provider(model, context, options):
        last = context['messages'][-1]
        value = str(last.get('content', ''))
        if last['role'] == 'user' and 'QUEUE-START' in value:
            tool = next(tool for tool in context['tools'] if tool['name'].startswith('ext_queuedhost_request'))
            yield {'type': 'tool_call', 'id': 'queue-physical', 'name': tool['name'], 'arguments': {
                'delivery': 'steer' if 'STEER' in value else 'follow_up', 'image': 'IMAGE' in value}}
        else:
            yield {'type': 'text', 'text': 'QUEUE-PHYSICAL-DONE'}
        yield {'type': 'done'}

    api.register_provider('offline', [{'id': 'queue', 'context_window': 32000, 'max_tokens': 2048,
                                      'input': ['text', 'image']}], provider)
