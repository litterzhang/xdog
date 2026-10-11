def setup(api):
    calls = closed = 0
    dispose = None

    def models(*, replacement=False):
        return [{'id': name, 'context_window': 16000 if replacement else 48000,
                 'max_tokens': 512 if replacement else 2048,
                 'input': ['text'] if replacement else ['text', 'image'],
                 'supports_tool_calls': not replacement}
                for name in (('echo',) if replacement else ('echo', 'selfswap', 'side'))]

    async def stream(model, context, options):
        nonlocal calls, closed
        calls += 1
        try:
            if model.get('supports_tool_calls') is False and context['tools'] is not None:
                raise ValueError('Host offered tools to a text-only provider')
            if model.get('supports_tool_calls') is False:
                yield {'type': 'text', 'text': 'TEXT-ONLY-NO-API-TOOLS\n'}
            if not model.get('reasoning') and options['thinking'] is not None:
                raise ValueError('Host offered unsupported reasoning')
            if model['id'] == 'selfswap':
                yield {'type': 'text', 'text': 'BEFORE-REPLACEMENT'}
                api.register_provider('live', models(replacement=True), stream, replace=True)
                yield {'type': 'text', 'text': 'FORBIDDEN-REPLACEMENT-CONTENT'}
            elif model['id'] == 'side':
                api.register_provider('other', models(replacement=True), stream)
            yield {'type': 'text', 'text': 'DYNAMIC-PROVIDER-ANSWER'}
            yield {'type': 'done', 'response_id': 'dynamic-response'}
        finally:
            closed += 1

    api.register_provider('anchor', [{'id': 'stable', 'context_window': 48000, 'max_tokens': 2048}], stream)

    async def enable(args, context):
        nonlocal dispose
        dispose = api.register_provider('live', models(), stream)
        return 'DYNAMIC-ENABLED'

    async def replace(args, context):
        api.register_provider('live', models(replacement=True), stream, replace=True)
        if dispose is not None:
            dispose()  # An old registration receipt must not remove the new callback.
        return 'DYNAMIC-REPLACED'

    async def disable(args, context):
        api.unregister_provider('live')
        return 'DYNAMIC-DISABLED'

    async def count(args, context):
        return f'CALLS={calls};CLOSED={closed}'

    async def overflow(args, context):
        for index in range(3):
            api.register_provider('overflow' + str(index), [
                {'id': 'model' + str(number), 'context_window': 48000, 'max_tokens': 2048}
                for number in range(32)], stream)
        return 'invalid catalog must not be admitted'

    for name, callback in [('enable', enable), ('replace', replace), ('disable', disable),
                           ('count', count), ('overflow', overflow)]:
        api.command(name, 'Inspect/change the owned dynamic provider catalog', callback)

    async def on_turn(context):
        if api.settings.get('turn_replace') and 'live' in api.providers:
            api.register_provider('live', models(replacement=True), stream, replace=True)
    api.on('turn_start', on_turn)
