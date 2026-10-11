import asyncio
import json


def setup(api):
    queue = None

    async def command(args, context):
        nonlocal queue
        queue = asyncio.Queue(maxsize=32)
        started = asyncio.Event()
        phase, inputs = 'initial', []

        def render(width, height):
            started.set()
            return {'lines': ['LIVE-OVERLAY ' + phase, 'INPUTS=' + str(len(inputs))],
                    'cursor': [1, 0] if height >= 2 else None}

        async def on_input(event):
            inputs.append(event.get('text', event.get('key')))
            if event['kind'] == 'key' and event['key'] == 'f2':
                await api.input('Overlay nested question', default='nested draft')
            if event['kind'] == 'key' and event['key'] == 'enter':
                return {'done': True, 'value': {'inputs': inputs}}

        async def controls():
            nonlocal phase
            await started.wait()
            if args == 'auto':
                for action, delay in [('hide', 0.6), ('show', 0.6), ('focus', 2)]:
                    await asyncio.sleep(delay)
                    phase = action
                    assert api.request_overlay(action)
                    assert api.request_render()
            while True:
                phase = await queue.get()
                assert api.request_overlay(phase)
                assert api.request_render()

        task = asyncio.create_task(controls())
        try:
            result = await api.custom('Live overlay fixture', render, on_input,
                overlay={'width': 44, 'max_height': 8, 'anchor': 'top-right', 'non_capturing': True}, timeout=300)
            return json.dumps({'value': result})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            queue = None
            assert not api.request_overlay('focus')

    async def control(args, context):
        if queue is None or args not in ('hide', 'show', 'focus', 'unfocus'):
            return 'REFUSED'
        queue.put_nowait(args)
        return 'QUEUED'

    api.command('view', 'Owned live non-capturing overlay', command)
    api.command('control', 'Fixture-only owned background controller', control)
    async def tool(params, context):
        return await command(params.get('mode', ''), context)
    api.tool('view', 'Owned live overlay, not tool approval', {'type': 'object'}, tool)
