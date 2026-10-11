from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import validate_answer, validate_dialog
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.session_name import name_snapshot


@pytest.mark.parametrize('changes', [{'owner_id': 'foreign'}, {'session_id': 'foreign'}, {'name': None},
    {'name': '\x1bunsafe'}, {'name': ' noncanonical '}, {'history': []}])
def test_name_snapshot_refuses_foreign_unbounded_or_noncanonical_metadata(changes):
    value = {'session_id': 'session', 'owner_id': 'owner', 'name': 'Saved name'}
    with pytest.raises(ValueError):
        name_snapshot({**value, **changes}, session_id='session', owner_id='owner')


def test_name_dialog_read_and_review_have_exact_plaintext_types_and_no_foreign_target():
    request = validate_dialog({'kind': 'session', 'id': 'read', 'title': 'Name', 'operation': 'name'})
    assert validate_answer(request, {'session_id': 'current', 'name': ''}) == {'session_id': 'current', 'name': ''}
    for value in ({'session_id': 'current', 'name': None}, {'session_id': True, 'name': 'Saved'},
                  {'session_id': 'current', 'name': 'name', 'history': []}):
        with pytest.raises(ValueError):
            validate_answer(request, value)
    rename = {'kind': 'session', 'id': 'rename', 'title': 'Name', 'operation': 'rename',
              'name': 'New name', 'expected_name': ''}
    validated = validate_dialog(rename)
    assert validate_answer(validated, True) is True and validate_answer(validated, None) is None
    for extra in ({'name': '\x1bunsafe'}, {'expected_name': None}, {'target': {}}, {'session_id': 'foreign'},
                  {'name': ' untrimmed '}, {'kind': 'confirm'}):
        with pytest.raises(ValueError):
            validate_dialog({**rename, **extra})
    with pytest.raises(ValueError):
        validate_answer(validated, 'true')


async def test_api_missing_or_headless_name_never_creates_mutation_dialogs():
    api = ExtensionAPI({})
    assert await api.get_session_name() is None and not await api.set_session_name('Unavailable')
    api._session_name.set({'session_id': 'parent', 'owner_id': 'parent', 'name': 'Captured'})
    assert await api.get_session_name() == 'Captured'
    assert not await api.set_session_name('Cannot silently mutate') and not api._dialogs


async def test_actual_worker_owned_name_projection_live_command_read_and_headless_mutation_refusal(
    tmp_path, monkeypatch,
):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/namer', root / 'namer')
    owner = {'session_id': 'session', 'owner_id': 'session', 'name': 'Original'}
    runtime = ExtensionRuntime(['namer'], root, tmp_path, context=lambda: {'session_id': 'session'})
    runtime.session_name = lambda: dict(owner)
    requests = []
    async def answer(extension, request):
        requests.append(request)
        return {'session_id': 'session', 'name': owner['name']} if request['operation'] == 'name' else True
    try:
        await runtime.initialize()
        assert runtime.entries.for_extension('namer')[0]['data']['name'] == 'Original'
        assert await runtime.pre('native', {'value': 1}) == {'value': 1}
        assert await runtime.post('native', {'value': 1}, 'unchanged') == 'unchanged'
        assert await runtime.transform([]) == []
        rows = [row['data'] for row in runtime.entries.for_extension('namer')]
        assert {row['scope'] for row in rows} == {'event', 'pre', 'post', 'context'}
        assert all(row['name'] == 'Original' for row in rows)
        result = await runtime.tools()[0].execute('read', {})
        assert 'Original' in result.content[0].text and not requests
        assert 'Original' in await runtime.command('ext-namer-inspect', '')
        assert not requests  # Headless command snapshots remain read-only.
        assert '"accepted": false' in await runtime.command('ext-namer-rename', '{"name":"No mutation"}')
        runtime.dialog_handler = answer
        output = await runtime.command('ext-namer-rename', '{"name":"New"}')
        assert '"accepted": true' in output and owner['name'] == 'Original'
        assert [request['operation'] for request in requests] == ['name', 'name', 'rename', 'name']
        assert requests[2]['expected_name'] == 'Original' and requests[2]['name'] == 'New'
        owner['name'] = 'Later'
        runtime.dialog_handler = None
        client = runtime._manager._clients['namer']
        forged = {'session_id': 'session', 'owner_id': 'session', 'name': 'FORGED-CALLER-NAME'}
        result = await runtime._manager._bridge(client.request('xdog/command', {'name': 'inspect', 'args': '',
            'context': {'host_session_name': forged}}))
        assert json.loads(result['output'].removeprefix('NAME-INSPECT '))['name'] == 'Later'
        from xdog.agent.extensions import runtime as runtime_module
        limit = runtime_module.MAX_FRAME
        monkeypatch.setattr(runtime_module, 'MAX_FRAME', 8192)
        result = await runtime._manager._bridge(client.request('xdog/command', {'name': 'inspect', 'args': '',
            'context': {'payload': 'x' * 5000}}))
        assert json.loads(result['output'].removeprefix('NAME-INSPECT '))['name'] is None
        monkeypatch.setattr(runtime_module, 'MAX_FRAME', limit)
        assert 'Later' in await runtime.command('ext-namer-inspect', '')
        runtime.context = lambda: {'session_id': 'session', 'child_id': 'independent-child'}
        assert 'null' in await runtime.command('ext-namer-inspect', '')  # Never borrow the parent's name.
        runtime.context = lambda: {'session_id': 'session'}
        runtime.session_name = lambda: {'session_id': 'foreign', 'owner_id': 'foreign', 'name': 'Foreign'}
        assert 'null' in await runtime.command('ext-namer-inspect', '')
        assert runtime.snapshot()['extensions'][0]['state'] == 'active'
    finally:
        await runtime.aclose()
