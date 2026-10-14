from __future__ import annotations

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import validate_answer, validate_dialog
from xdog.agent.session_name import name_snapshot


def test_name_snapshot_refuses_foreign_unbounded_or_noncanonical_metadata() -> None:

    def _check_case(changes):
        value = {"session_id": "session", "owner_id": "owner", "name": "Saved name"}
        with pytest.raises(ValueError):
            name_snapshot({**value, **changes}, session_id="session", owner_id="owner")

    for changes in [
        {"owner_id": "foreign"},
        {"session_id": "foreign"},
        {"name": None},
        {"name": "\x1bunsafe"},
        {"name": " noncanonical "},
        {"history": []},
    ]:
        _check_case(changes)


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
