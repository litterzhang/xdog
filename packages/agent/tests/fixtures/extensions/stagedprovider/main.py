import asyncio
import json
from pathlib import Path


def setup(api):
    state = {'starts': 0}
    async def start(context):
        state['starts'] += 1
        state['session_id'] = context['session_id']
        flags = [row['data'] for row in context['entries'] if isinstance(row.get('data'), dict)]
        api.append_entry('note', {'prepared': context['session_id']})
        if any(row.get('hold') for row in flags):
            Path(api.settings['marker']).write_text('staged callback entered')
            await asyncio.Event().wait()
        if any(row.get('fail') for row in flags):
            raise ValueError('private setup failure')
        if any(row.get('allow') for row in flags):
            async def stream(model, context, options):
                yield {'type': 'text', 'text': 'STAGED-OWNED-ANSWER'}
                yield {'type': 'done', 'stop_reason': 'stop'}
            api.register_provider('local', [{'id': 'choice', 'name': 'choice', 'context_window': 32000,
                'max_tokens': 2048, 'reasoning': True, 'supported_efforts': ['low']}], stream)
    api.on('session_start', start)
    api.command('status', 'Inspect prepared session owner/start count', lambda args, context: json.dumps(state))
