from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.command_catalog import answer, page, request
from xdog.agent.extensions.runtime import ExtensionRuntime


def test_command_menu_pages_keep_all_rows_revision_query_copy_and_explicit_stale_status():
    commands = {f'item{index:02}': f'Public metadata {index}' for index in range(81)}
    query = {'query': '', 'offset': 0, 'revision': ''}
    first = page(commands, query, session_id='session', owner='command:owner')
    second_query = {**query, 'offset': 32, 'revision': first['revision']}
    second = page(commands, second_query, session_id='session', owner='command:owner')
    third = page(commands, {**second_query, 'offset': 64}, session_id='session', owner='command:owner')
    assert [len(part['commands']) for part in (first, second, third)] == [32, 32, 17]
    assert len({row['name'] for part in (first, second, third) for row in part['commands']}) == 81
    assert third['next_offset'] is None and first['source'] == 'terminal-menu'
    clone = answer(query, first)
    clone['commands'][0]['description'] = 'Modified copy'
    assert first['commands'][0]['description'] != 'Modified copy'
    filtered = page(commands, {'query': 'METADATA 80'}, session_id='session', owner='command:owner')
    assert filtered['total'] == 1 and filtered['commands'][0]['name'] == '/item80'
    commands['item00'] = 'Changed metadata'
    stale = page(commands, second_query, session_id='session', owner='command:owner')
    assert stale['status'] == 'stale' and not stale['commands'] and stale['next_offset'] is None
    foreign = page(commands, second_query, session_id='session', owner='other-command')
    assert foreign['status'] == 'stale'


@pytest.mark.parametrize('invalid', [{'offset': True}, {'offset': -1}, {'offset': 10001}, {'query': '\n'},
    {'query': '\x1bunsafe'}, {'query': 'x' * 257}, {'revision': 'forged'}, {'target': {}}, {'session_id': 'foreign'}])
def test_command_metadata_request_rejects_coercion_oversize_controls_and_host_actions(invalid):
    with pytest.raises(ValueError):
        request(invalid)


@pytest.mark.parametrize('invalid', [{'total': True}, {'source': 'executable'}, {'session_id': False},
    {'commands': []}, {'next_offset': 1}, {'revision': 'foreign'}, {'permissions': 'allow-all'}])
def test_command_metadata_answer_is_not_an_execution_receipt_or_partial_page(invalid):
    value = page({'owned': 'Metadata'}, {}, session_id='session', owner='command')
    with pytest.raises(ValueError):
        answer({}, {**value, **invalid})


async def test_headless_api_returns_unavailable_and_worker_cannot_borrow_menu_from_a_tool(tmp_path):
    api = ExtensionAPI({})
    assert await api.get_command_catalog() is None and not api._dialogs
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/commandreader', root / 'commandreader')
    runtime = ExtensionRuntime(['commandreader'], root, tmp_path, context=lambda: {'session_id': 'session'})
    try:
        assert await runtime.command('ext-commandreader-inspect', '') == 'COMMAND-MENU-UNAVAILABLE'
        requests = []
        async def supplied(extension, query):
            requests.append(query)
            return page({'help': 'Public help', 'ext-commandreader-inspect': 'Read metadata'}, query,
                        session_id='session', owner='command')
        runtime.dialog_handler = supplied
        assert 'COMMAND-MENU ' in await runtime.command('ext-commandreader-inspect', '')
        assert len(requests) == 2 and all(query['operation'] == 'commands' for query in requests)
        requests.clear()
        result = await runtime.tools()[0].execute('tool', {})
        assert result.content[0].text == 'TOOL-MENU-UNAVAILABLE' and not requests
    finally:
        await runtime.aclose()


async def test_api_command_menu_cannot_accept_foreign_session_data_even_with_valid_schema():
    api = ExtensionAPI({})
    api._has_session.set(True)
    api._command_menu_session.set('actual')
    async def forged(*args, **kwargs):
        return page({'safe': 'Metadata'}, {}, session_id='foreign', owner='command')
    api._ask = forged
    with pytest.raises(ValueError, match='another session'):
        await api.get_command_catalog()
