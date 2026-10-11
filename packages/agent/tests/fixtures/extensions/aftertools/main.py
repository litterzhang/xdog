import asyncio
import json
from pathlib import Path


def setup(api):
    async def propose(scope, args=None):
        args = args or {}
        current = await api.get_active_tools()
        revision = current['revision'] if current else api.settings.get('revision', 0)
        accepted = await api.set_active_tools(args.get('names', ['safe']), revision, timeout=10)
        if api.settings.get('marker'):
            Path(api.settings['marker']).write_text(str(accepted))
        if api.settings.get('release'):
            async with asyncio.timeout(10):
                while not Path(api.settings['release']).exists():
                    await asyncio.sleep(.003)
        if args.get('error'):
            return {'content': [{'type': 'text', 'text': 'ERROR AFTER TOOL SELECTION REVIEW'}], 'isError': True}
        if args.get('fail'):
            raise RuntimeError('Failed after tool selection review')
        return 'TOOL-SELECTION-' + str(accepted)

    async def tool(args, context):
        return await propose('tool', args)

    async def event(context):
        if api.settings.get('scope') == 'event':
            await propose('event')

    async def pre(name, args, context):
        if api.settings.get('scope') == 'pre':
            await propose('pre')
        return args

    async def post(name, args, result, context):
        if api.settings.get('scope') == 'post':
            await propose('post')
        return result

    async def input_hook(text, metadata, context):
        if api.settings.get('scope') == 'input':
            await propose('input')
        return {'action': 'continue'}

    async def transform(messages, context):
        if api.settings.get('scope') == 'context':
            await propose('context')
        return messages

    async def command(args, context):
        return await propose('command', json.loads(args or '{}'))

    async def terminal(data, metadata, context):
        assert not await api.set_active_tools([], 0)
        return {'consume': False}

    api.tool('select', 'Request reviewed selection after the whole original parent run', {'type': 'object'}, tool)
    api.command('select', 'Existing idle command selection path', command)
    api.on('turn_start', event)
    api.before_tool(pre)
    api.after_tool(post)
    api.on_input(input_hook)
    api.context(transform)
    api.on_terminal_input(terminal)
