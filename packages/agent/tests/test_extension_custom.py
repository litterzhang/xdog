"""Custom UI SDK/protocol entrypoints are gone; basic worker dialogs remain."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import DialogBroker
from xdog.agent.extensions.runtime import ExtensionRuntime


@pytest.mark.parametrize('name', ['form', 'custom', 'request_render', 'request_overlay', 'widget', 'clear_widget'])
def test_removed_public_custom_ui_registration_and_control_apis(name):
    assert not hasattr(ExtensionAPI({}), name)


@pytest.mark.parametrize('kind', ['custom', 'form'])
async def test_removed_dialog_kinds_never_publish_or_allocate_input_ownership(kind):
    broker = DialogBroker()
    events = []
    broker.publish = events.append
    with pytest.raises(ValueError):
        await broker.ask('fixture', {'kind': kind, 'id': 'worker', 'title': 'Removed UI'})
    assert not broker.has_pending and not events


@pytest.mark.parametrize('method', ['xdog/custom_frame', 'xdog/custom_refresh', 'xdog/custom_control'])
async def test_removed_worker_protocol_never_calls_retained_input_or_tools(method):
    from xdog.agent.extensions.worker import dispatch
    api = ExtensionAPI({})
    calls = []
    api.on_input(lambda *args: calls.append(args))
    with pytest.raises(ValueError, match='Unknown extension method'):
        await dispatch(api, method, {'event': {'kind': 'key', 'key': 'enter'}})
    assert not calls


async def test_real_worker_basic_dialog_sequence_remains_owned_without_render_capabilities(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/dialogue', root / 'dialogue')
    runtime = ExtensionRuntime(['dialogue'], root, tmp_path)
    questions = []
    async def answer_dialog(extension, value):
        assert extension == 'dialogue' and value['kind'] in {'select', 'input', 'confirm'}
        questions.append(value['kind'])
        return {'select': 'Red', 'input': 'answer', 'confirm': True}[value['kind']]
    runtime.dialog_handler = answer_dialog
    try:
        result = await runtime.command('ext-dialogue-ask', '')
        assert questions == ['select', 'input', 'confirm'] and 'answer' in str(result)
        for name in ('custom_handler', 'custom_refresh_handler', 'custom_control_handler'):
            assert not hasattr(runtime, name)
    finally:
        await runtime.aclose()
