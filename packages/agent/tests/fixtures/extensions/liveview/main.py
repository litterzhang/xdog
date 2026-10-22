import asyncio
import json


def setup(api):
    async def command(args, context):
        state, inputs = 0, 0
        started = asyncio.Event()

        def render(width, height):
            started.set()
            return ['LIVE-STATE=' + str(state) + ';INPUTS=' + str(inputs)]

        def on_input(event):
            nonlocal inputs
            inputs += 1
            if event['kind'] == 'key' and event['key'] == 'enter':
                return {'done': True, 'value': {'state': state, 'inputs': inputs}}

        async def update():
            nonlocal state
            await started.wait()
            for value in (1, 2):
                await asyncio.sleep(0.08)
                state = value
                for _ in range(100):
                    assert api.request_render()

        task = asyncio.create_task(update())
        try:
            value = await api.custom('Live background view', render, on_input, timeout=10)
            return json.dumps({'value': value})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert not api.request_render()  # Closed views cannot repaint another invocation.

    api.command('view', 'Live worker background state', command)

    async def tool(params, context):
        return await command('', context)
