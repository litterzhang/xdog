"""UI032 SDK removal: no mutation grants, with real owned query/registration callbacks."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool
from xdog.agent.extensions.active_tools import snapshot as active_snapshot
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.catalog import page as catalog_page
from xdog.agent.extensions.command_catalog import page as menu_page
from xdog.agent.extensions.dialogs import (
    READ_ONLY_SESSION_OPERATIONS,
    REMOVED_HOST_CAPABILITIES,
    validate_sdk_dialog,
)
from xdog.agent.extensions.inspection import capture, snapshot
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.tool_inventory import capture as capture_tools
from xdog.ai.types import AssistantMessage, Model, TextContent, Usage

REMOVED = (
    'request_editor_text', 'get_editor', 'set_editor', 'set_session_name', 'set_active_tools',
    'request_context_message', 'request_message', 'request_queued_input', 'request_compaction',
    'open_sessions', 'navigate_session',
)
PACKETS = (
    *({'kind': 'editor', 'operation': operation} for operation in ('read', 'replace', 'propose')),
    *({'kind': 'session', 'operation': operation} for operation in (
        'rename', 'select_tools', 'context_message', 'message', 'queue_input', 'compact', 'browse', 'navigate')),
)


def make(tmp_path, **settings):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/readonlyhost', root / 'readonlyhost')
    def forbidden(*args):
        pytest.fail('Host metadata reads must not call a model or execute tools')
    agent = Agent(forbidden, config=AgentConfig(model='private/model'),
                  tools=[AgentTool(name='safe', description='Public metadata', parameters={'type': 'object'})])
    agent.append_message(AssistantMessage(model='private/model', content=(TextContent(text='retained'),),
                                         usage=Usage(input=12, output=3)))
    context = {'session_id': 'original', **{key: True for key in REMOVED_HOST_CAPABILITIES}}
    runtime = ExtensionRuntime(['readonlyhost'], root, tmp_path,
        context=lambda: context, settings={'readonlyhost': settings},
        inspection=lambda: capture(agent, context['session_id'], context.get('child_id') or context['session_id']))
    runtime.tool_inventory = lambda: capture_tools(agent, context['session_id'],
                                                   context.get('child_id') or context['session_id'])
    runtime.session_name = lambda: {'session_id': context['session_id'],
                                    'owner_id': context.get('child_id') or context['session_id'], 'name': 'Captured'}
    return runtime, agent, context


@pytest.mark.parametrize('name', REMOVED)
def test_removed_api_names_are_absent_not_silent_unavailable_success(name):
    assert not hasattr(ExtensionAPI({}), name)


@pytest.mark.parametrize('packet', PACKETS)
async def test_removed_control_packets_never_allocate_sdk_questions(packet):
    api = ExtensionAPI({})
    api._has_ui.set(True)
    api._call_id.set('owned')
    api._dialog_events['owned'] = asyncio.Event()
    with pytest.raises(ValueError, match='removed'):
        await api._ask(packet['kind'], 'Removed request', '', operation=packet['operation'])
    assert not api._dialogs and not api._dialog_events['owned'].is_set()


@pytest.mark.parametrize('field,value', [('target', {}), ('permission', 'allow-all'), ('after_run', True),
                                        ('text', '/quit'), ('images', []), ('expected_name', 'before')])
def test_readonly_queries_cannot_smuggle_control_fields(field, value):
    with pytest.raises(ValueError, match='no host control grant'):
        validate_sdk_dialog({'kind': 'session', 'id': 'owned', 'title': 'Read', 'operation': 'snapshot', field: value})


@pytest.mark.parametrize('packet', PACKETS)
async def test_legacy_worker_prompts_cannot_reach_bound_host_handlers(tmp_path, monkeypatch, packet):
    from xdog.agent.mcp.stdio import StdioClient

    runtime, _, _ = make(tmp_path)
    handlers = []
    async def forbidden(*args):
        handlers.append(args)
        pytest.fail('Removed worker controls must not reach a host binding')
    for attr in ('dialog_handler', 'editor_write_handler', 'name_request_handler', 'queued_input_handler',
                 'active_tools_request_handler', 'compaction_request_handler', 'context_message_handler'):
        setattr(runtime, attr, forbidden)
    try:
        await runtime.initialize()
        original = StdioClient.request
        async def legacy(self, method, params, **kwargs):
            if method == 'xdog/command':
                assert not (REMOVED_HOST_CAPABILITIES & params['context'].keys())
                return {'xdog_pending': 'legacy', 'xdog_prompt': {'id': 'old', 'title': 'Old control', **packet}}
            return await original(self, method, params, **kwargs)
        monkeypatch.setattr(StdioClient, 'request', legacy)
        with pytest.raises(ValueError, match='removed'):
            await runtime.command('ext-readonlyhost-probe', '')
        assert not handlers
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('scope', ['command', 'tool', 'event', 'child-tool'])
async def test_actual_worker_readonly_queries_keep_original_owner_and_no_control_grant(tmp_path, scope):
    runtime, agent, context = make(tmp_path, scope='event' if scope == 'event' else '')
    before = tuple(agent.messages)
    if scope == 'child-tool':
        context['child_id'] = 'owned-child'
    requests = []
    async def read(extension, prompt):
        assert extension == 'readonlyhost' and prompt['operation'] in READ_ONLY_SESSION_OPERATIONS
        operation = prompt['operation']
        requests.append(operation)
        if operation == 'snapshot':
            return {'session_id': 'original', 'workspace': str(tmp_path), 'group_id': ''}
        if operation == 'name':
            return {'session_id': 'original', 'name': 'Captured'}
        if operation == 'active_tools':
            return active_snapshot(agent, 'original')
        return snapshot(agent, 'original', operation, Model(id='private/model'))
    runtime.dialog_handler = read
    async def menu(extension, prompt):
        return menu_page({'business': 'Metadata, not execution permission'}, prompt,
                         session_id='original', owner='owned')
    runtime.command_menu_handler = menu
    async def catalog(extension, prompt):
        return catalog_page([], prompt, 'original')
    runtime.catalog_handler = catalog
    runtime.pending_messages = lambda: False
    async def forbidden(*args):
        pytest.fail('A read-only invocation cannot request host mutation')
    for attr in ('editor_write_handler', 'name_request_handler', 'queued_input_handler',
                 'active_tools_request_handler', 'compaction_request_handler', 'context_message_handler'):
        setattr(runtime, attr, forbidden)
    try:
        await runtime.initialize()
        if scope == 'command':
            value = json.loads(await runtime.command('ext-readonlyhost-probe', ''))
        elif scope == 'event':
            await runtime.event('turn_start', {})
            value = runtime.entries.for_extension('readonlyhost')[-1]['data']
        else:
            result = await runtime.tools()[0].execute('owned', {})
            value = json.loads(result.content[0].text)
        assert value['model']['id'] == 'private/model' and value['usage']['tokens'] == 15
        assert value['tools']['owner_id'] == ('owned-child' if scope == 'child-tool' else 'original')
        assert value['active']['active'] == ['safe'] and value['name'] == 'Captured'
        assert value['commands']['commands'][0]['name'] == '/business' and value['pending'] is False
        assert (value['session'] is not None) is (scope == 'command')
        assert (value['sessions'] is not None) is (scope == 'command')
        assert (value['tree'] is not None) is (scope == 'command')
        assert tuple(agent.messages) == before and tuple(tool.name for tool in agent.active_tools) == ('safe',)
        assert set(requests) <= READ_ONLY_SESSION_OPERATIONS
    finally:
        await runtime.aclose()


async def test_legacy_mutation_bindings_alone_do_not_grant_basic_ui_or_host_controls(tmp_path):
    runtime, _, _ = make(tmp_path)
    calls = []
    async def forbidden(*args):
        calls.append(args)
    for attr in ('editor_write_handler', 'name_request_handler', 'queued_input_handler',
                 'active_tools_request_handler', 'compaction_request_handler', 'context_message_handler'):
        setattr(runtime, attr, forbidden)
    try:
        value = json.loads(await runtime.command('ext-readonlyhost-probe', ''))
        assert not value['has_ui'] and value['session'] is None and not calls
        assert await runtime.command('ext-readonlyhost-ask', '') == 'None'
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('attribute', [
    'editor_write_handler', 'name_request_handler', 'queued_input_handler',
    'active_tools_request_handler', 'compaction_request_handler', 'context_message_handler',
])
def test_retired_runtime_handler_attributes_are_not_declared(tmp_path, attribute):
    runtime = ExtensionRuntime([], tmp_path, tmp_path)
    assert not hasattr(runtime, attribute)
