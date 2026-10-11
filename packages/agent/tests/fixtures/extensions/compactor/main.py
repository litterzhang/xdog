"""Offline owned compaction hooks, observed only through actual host callbacks."""
import asyncio
import json
from pathlib import Path


def setup(api):
    state = {'before': 0, 'after': 0}
    mode = api.settings.get('mode', 'override')

    async def before(context):
        state['before'] += 1
        preparation = context['compaction']
        state['prepared_count'] = len(preparation['messages'])
        state['automatic'] = preparation['automatic']
        state['focus'] = preparation['instructions']
        state['model'] = context['model']
        state['previous'] = preparation['previous']
        state['image_bytes'] = sum(len(part.get('data', '')) for message in preparation['messages']
                                  for part in message.get('content', []) if part.get('type') == 'image')
        state['has_tool_result'] = any(message['role'] == 'toolResult' for message in preparation['messages'])
        state['no_borrowed_authority'] = (not api.has_ui and await api.input('must not open') is None
            and await api.get_editor() is None and await api.get_session() is None
            and await api.open_sessions() is False)
        if mode == 'hold':
            Path(api.settings['marker']).write_text('owned callback entered')
            await asyncio.Event().wait()
        if mode == 'cancel':
            return {'cancel': True, 'reason': 'VETO-PRESERVE-CONTEXT'}
        if mode == 'invalid':
            return {'summary': ''}
        if mode == 'bad-boundary':
            return {'summary': 'DO-NOT-APPLY', 'first_kept_index': 2}
        if mode == 'continue':
            return None
        # Mutation of the received preparation must never change canonical
        # history or the preparation used by another listener.
        preparation['messages'][0]['content'] = [{'type': 'text', 'text': 'MUTATED-PRIVATE-COPY'}]
        return {'summary': 'CUSTOM-COMPACTION-SUMMARY', 'first_kept_index': 4,
                'details': {'retained': 'whole user/tool turns', 'prepared_count': state['prepared_count']}}

    async def after(context):
        state['after'] += 1
        state['committed'] = context['compaction']
        api.append_entry('note', {'phase': 'committed', 'source': context['compaction']['source']})

    before_off = api.on('session_before_compact', before)
    if mode == 'override':
        def sibling(context):
            state['sibling_saw_original'] = 'MUTATED-PRIVATE-COPY' not in str(context['compaction']['messages'][0])
        api.on('session_before_compact', sibling)
    after_off = api.on('session_compact', after)
    if mode == 'conflict':
        api.on('session_before_compact', lambda context: {'summary': 'SECOND-CONFLICT'})
    api.command('status', 'Read compaction hook observations', lambda args, context: json.dumps(state))
    def dispose(args, context):
        before_off()
        after_off()
        return 'COMPACTION-HOOKS-DISPOSED'
    api.command('dispose', 'Remove this exact compaction registration', dispose)
