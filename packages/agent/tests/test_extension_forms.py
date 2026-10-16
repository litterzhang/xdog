"""Plugin form codecs/registration cannot be used to allocate native UI."""
from __future__ import annotations

import importlib.util

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import DialogBroker, validate_dialog


def test_removed_sdk_codec_modules_and_form_api():
    assert importlib.util.find_spec('xdog.agent.extensions.forms') is None
    assert importlib.util.find_spec('xdog.agent.extensions.custom') is None
    assert not hasattr(ExtensionAPI({}), 'form')


@pytest.mark.parametrize('kind', ['form', 'custom'])
async def test_removed_dialogs_do_not_allocate_questions_or_publish_anything(kind):
    broker, events = DialogBroker(), []
    broker.publish = events.append
    with pytest.raises(ValueError):
        await broker.ask('forms', {'kind': kind, 'id': 'worker', 'title': 'Removed', 'fields': []})
    assert not events and not broker.has_pending


def test_form_fields_cannot_be_smuggled_into_retained_input_dialog():
    with pytest.raises(ValueError):
        validate_dialog({'kind': 'input', 'id': 'worker', 'title': 'Question', 'fields': []})
