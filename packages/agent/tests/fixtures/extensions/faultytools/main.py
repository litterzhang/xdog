import asyncio
from pathlib import Path


def setup(api):
    stage = api.settings.get('stage', 'command')

    async def failure(*args):
        marker = api.settings.get('marker')
        if marker:
            Path(marker).write_text('entered')
        if api.settings.get('hold'):
            await asyncio.Event().wait()
        raise ValueError('Intentional offline extension failure')

    async def tool(params, context):
        if stage == 'tool':
            return await failure()
        return 'ORIGINAL-ACTUAL-RESULT'

    api.tool('target', 'Owned failure-projection regression tool', {'type': 'object'}, tool)
    api.command('fail', 'Fail one actual worker callback without replay', failure)
    if stage == 'pre':
        api.before_tool(failure)
    elif stage == 'post':
        api.after_tool(failure)
    elif stage == 'context':
        api.context(failure)
    elif stage == 'input':
        api.on_input(failure)
    elif stage == 'event':
        api.on('turn_start', failure)
    elif stage == 'child-context':
        async def child_context(messages, context):
            if context.get('child_id'):
                return await failure()
            return messages
        api.context(child_context)
