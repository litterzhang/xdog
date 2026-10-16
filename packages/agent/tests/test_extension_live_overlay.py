"""Removed custom overlays cannot claim ownership through a basic dialog."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import DialogBroker, validate_dialog


@pytest.mark.parametrize('field,value', [('overlay', {}), ('live_overlay', True), ('fullscreen', True),
    ('parent_id', 'foreign'), ('view_id', 'foreign'), ('key_events', ['release']),
    ('overlay_state', {'capturing': True})])
def test_custom_presentation_cannot_be_smuggled_into_basic_dialogs(field, value):
    with pytest.raises(ValueError):
        validate_dialog({'id': 'worker', 'kind': 'confirm', 'title': 'Question', field: value})


def test_overlay_control_and_capture_apis_are_absent():
    assert not hasattr(ExtensionAPI({}), 'request_overlay')
    broker = DialogBroker()
    for name in ('custom_input_owner', 'ask_custom', 'request_custom', 'control_custom'):
        assert not hasattr(broker, name)


async def test_newer_basic_question_owns_input_and_cancel_does_not_answer_older_question():
    broker, events = DialogBroker(), []
    broker.publish = events.append
    first = asyncio.create_task(broker.ask('one', {'id': 'a', 'kind': 'input', 'title': 'First'}))
    second = asyncio.create_task(broker.ask('two', {'id': 'b', 'kind': 'confirm', 'title': 'Second'}))
    await asyncio.sleep(0)
    original, latest = [event['dialog']['id'] for event in events]
    assert broker.input_owner(latest) and not broker.input_owner(original)
    broker.cancel_extension('two')
    assert await second is False and not first.done()
    assert broker.input_owner(original)
    assert not broker.resolve(latest, True)
    broker.close()
    assert await first is None and not broker.has_pending
