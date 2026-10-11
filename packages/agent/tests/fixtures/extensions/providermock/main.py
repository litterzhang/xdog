import asyncio


def setup(api):
    calls = 0
    closed = 0
    async def stream(model, context, options):
        nonlocal calls
        calls += 1
        if await api.input('Provider must not borrow a dialog') is not None:
            raise ValueError('Provider borrowed interactive authority')
        if model['id'] == 'broken':
            yield {'type': 'unknown', 'private': 'must not appear'}
            return
        if model['id'] in ('failure', 'aborted'):
            if api.settings.get('failed_tool'):
                tool = next(item for item in context['tools'] if item['name'] == 'mutate')
                yield {'type': 'tool_call', 'id': 'unexecuted', 'name': tool['name'], 'arguments': {'value': 'change'}}
            yield {'type': 'text', 'text': 'EXT-PROVIDER-PARTIAL'}
            yield {'type': 'usage', 'input': 7, 'output': 3, 'total_tokens': 10}
            diagnostic = 'Provider canceled' if model['id'] == 'aborted' else 'Provider unavailable'
            yield {'type': 'error', 'message': diagnostic,
                   'stop_reason': 'aborted' if model['id'] == 'aborted' else 'error', 'response_id': 'owned-failure'}
            return
        if model['id'] == 'hold':
            yield {'type': 'text', 'text': 'PROVIDER-HOLD'}
            if api.settings.get('marker'):
                from pathlib import Path
                Path(api.settings['marker']).write_text('entered')
            await asyncio.Event().wait()
        if model['id'] == 'tool' and context['messages'][-1]['role'] == 'user':
            tool = next(item for item in context['tools'] if item['name'] == 'mutate')
            yield {'type': 'tool_call', 'id': 'from-provider', 'name': tool['name'], 'arguments': {'value': 'change'},
                   'signature': 'tool-continuity'}
            yield {'type': 'done', 'stop_reason': 'toolUse'}
            return
        yield {'type': 'thinking', 'text': 'bounded reasoning', 'signature': 'thinking-continuity'}
        yield {'type': 'text', 'text': 'EXT-PROVIDER-', 'signature': 'text-one'}
        yield {'type': 'text', 'text': 'ANSWER', 'signature': 'text-two'}
        yield {'type': 'usage', 'input': 7, 'output': 3, 'total_tokens': 10}
        yield {'type': 'done', 'stop_reason': 'stop', 'response_id': 'owned-response'}
    class Iterator:
        def __init__(self, generator):
            self.generator = generator
            self.closed = False
        def __aiter__(self):
            return self
        async def __anext__(self):
            return await self.generator.__anext__()
        async def aclose(self):
            nonlocal closed
            if self.closed:
                raise ValueError('Provider iterator closed twice')
            self.closed = True
            closed += 1
            await self.generator.aclose()
            if api.settings.get('close_fail'):
                raise ValueError('private close failure must not appear')
    async def owned_stream(model, context, options):
        return Iterator(stream(model, context, options))
    api.register_provider('local', [
        {'id': name, 'name': name, 'context_window': 32000, 'max_tokens': 2048,
         'reasoning': True, 'input': ['text', 'image'], 'supports_tool_calls': name != 'plain'}
        for name in ('echo', 'tool', 'plain', 'broken', 'hold', 'failure', 'aborted')
    ], owned_stream)
    async def count(args, context):
        return str(calls)
    api.command('count', 'Inspect provider invocation count', count)
    async def close_count(args, context):
        return str(closed)
    api.command('closed', 'Inspect exact provider iterator cleanup count', close_count)
