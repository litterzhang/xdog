import asyncio
import json
from pathlib import Path


def setup(api):
    executed = 0
    completed = 0
    disposers = []
    async def command(args, context):
        nonlocal executed
        executed += 1
        return 'COMMAND-EXECUTED-' + args

    async def complete(prefix, context):
        nonlocal completed
        completed += 1
        assert not api.has_ui
        assert await api.get_editor() is None
        assert not await api.navigate_session('new')
        assert not await api.set_session_name('COMPLETION-CANNOT-RENAME')
        if prefix == 'hold':
            Path(api.settings['marker']).write_text('entered')
            await asyncio.Event().wait()
        if prefix == 'en':
            metadata = context.get('argument_context')
            if metadata is None:
                return []  # Explicit prefix-only caller cannot impersonate a full argument draft.
            assert metadata['arguments'][metadata['start']:metadata['cursor']] == prefix
            previous = metadata['arguments'][:metadata['start']].strip()
            if previous not in ('production', 'staging'):
                return []
            result = [{'value': 'endpoint-' + previous, 'label': 'Endpoint for ' + previous,
                       'description': 'Depends on preceding argument'}]
            metadata['arguments'] = 'LOCAL-CALLBACK-MUTATION'
            return result
        metadata = context.get('argument_context')
        if metadata is not None and metadata['arguments'].startswith('literal '):
            token = metadata['arguments'][metadata['start']:metadata['end']]
            assert token.startswith(prefix)
            replacements = {
                'release\\ canTAIL': 'release\\ candidate',
                '"release canTAIL"': '"release candidate"',
                "'release\\ canTAIL'": "'release\\ candidate'",
            }
            if token in replacements:
                assert metadata['arguments'][metadata['end']:] == ' later'
                return [{'value': replacements[token], 'label': 'Literal complete token',
                         'description': 'Replace suffix and retain explicit quoting'}]
        if prefix == 'label':
            return [{'value': value, 'label': '🚀 发布候选', 'description': 'Preview only; inserts ' + value}
                    for value in ('release-1', 'release-2')]
        if prefix == 'bad':
            return [{'value': 'unsafe\x1b[2J'}]
        if prefix == 'change':
            api.command('deploy', 'Replacement during completion', command,
                        replace=True, complete=lambda *args: [{'value': 'replacement'}])
        return [{'value': value, 'label': value.upper(), 'description': 'Completion data only'}
                for value in ('alpha', 'beta', 'release candidate') if value.startswith(prefix)]

    disposers.append(api.command('deploy', 'Registered argument completion', command, complete=complete))
    async def replace(args, context):
        disposers.append(api.command('deploy', 'Replaced arguments', command, replace=True,
                                    complete=lambda *args: [{'value': 'new value'}]))
        return 'REPLACED'
    async def old(args, context):
        disposers[0]()
        return 'OLD-DISPOSER'
    async def disable(args, context):
        disposers[-1]()
        return 'DISABLED'
    async def counts(args, context):
        return json.dumps({'executed': executed, 'completed': completed})
    api.command('replace', 'Replace current completion registration explicitly', replace)
    api.command('old', 'Old disposer cannot remove replacement', old)
    api.command('disable', 'Withdraw current command and completer', disable)
    api.command('counts', 'Read isolated callback counters', counts)
