
def setup(api):
    async def request():
        queued = await api.set_session_name('AFTER-RUN-NAME')
        if api.settings.get('fail'):
            raise ValueError('Callback failed after naming review')
        return queued

    async def event(context):
        if api.settings.get('method', 'event') == 'event':
            await request()

    async def tool(params, context):
        queued = await request()
        return {'content': [{'type': 'text', 'text': 'AFTER-RUN-QUEUED=' + str(queued)}],
                'isError': bool(params.get('error'))}

    async def pre(name, args, context):
        if api.settings.get('method') == 'pre':
            await request()
        return args

    async def post(name, args, result, context):
        if api.settings.get('method') == 'post':
            await request()
        return result

    async def input_hook(text, metadata, context):
        if api.settings.get('method') == 'input':
            await request()
        return {'action': 'continue'}

    async def context_hook(messages, context):
        if api.settings.get('method') == 'context':
            await request()
        return messages

    api.before_tool(pre)
    api.after_tool(post)
    api.on_input(input_hook)
    api.context(context_hook)
    api.on('turn_end', event)
