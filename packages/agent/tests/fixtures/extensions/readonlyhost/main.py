"""Retained host queries and business registrations never grant host mutation APIs."""
import json

REMOVED = (
    'request_editor_text', 'get_editor', 'set_editor', 'set_session_name', 'set_active_tools',
    'request_context_message', 'request_message', 'request_queued_input', 'request_compaction',
    'open_sessions', 'navigate_session',
)
CAPABILITIES = (
    'host_editor', 'host_editor_write', 'host_navigation', 'host_name_request',
    'host_active_tools_request', 'host_context_message', 'host_queued_input', 'host_compaction_request',
)


def setup(api):
    async def probe(scope, context):
        assert all(not hasattr(api, name) for name in REMOVED)
        assert not (set(CAPABILITIES) & context.keys())
        value = {
            'scope': scope, 'has_ui': api.has_ui, 'session': await api.get_session(),
            'name': await api.get_session_name(), 'model': await api.get_model(),
            'usage': await api.get_context_usage(), 'tools': await api.get_tool_inventory(),
            'active': await api.get_active_tools(), 'commands': await api.get_command_catalog(),
            'sessions': await api.list_sessions(), 'tree': await api.get_session_tree(),
            'pending': await api.has_pending_messages(),
        }
        api.append_entry('readonly_probe', value)
        api.notify('Read-only host queries settled')
        api.status('read', 'Metadata only; no execution authority')
        return json.dumps(value, sort_keys=True)

    api.command('probe', 'Read original owner metadata only', lambda args, ctx: probe('command', ctx))
    api.tool('probe', 'Read original invocation metadata; normal tool policy still applies',
             {'type': 'object'}, lambda args, ctx: probe('tool', ctx))

    async def event(context):
        if api.settings.get('scope') == 'event':
            await probe('event', context)
    api.on('turn_start', event)

    async def completion(prefix, context):
        assert not api.has_ui and all(not hasattr(api, name) for name in REMOVED)
        return [{'value': 'release candidate', 'label': 'Completion data, never control'}]
    api.command('complete', 'Retained business command with argument completion',
                lambda args, ctx: args, complete=completion)

    async def question(args, context):
        return str(await api.input('Retained question', timeout=10))
    api.command('ask', 'Basic input remains separately owned', question)
