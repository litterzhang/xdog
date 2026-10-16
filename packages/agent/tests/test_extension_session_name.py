from __future__ import annotations

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import validate_answer, validate_sdk_dialog
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


def test_name_dialog_read_is_typed_and_rename_has_no_sdk_route():
    request = validate_sdk_dialog({'kind': 'session', 'id': 'read', 'title': 'Name', 'operation': 'name'})
    assert validate_answer(request, {'session_id': 'current', 'name': ''}) == {'session_id': 'current', 'name': ''}
    for value in ({'session_id': 'current', 'name': None}, {'session_id': True, 'name': 'Saved'},
                  {'session_id': 'current', 'name': 'name', 'history': []}):
        with pytest.raises(ValueError):
            validate_answer(request, value)
    rename = {'kind': 'session', 'id': 'rename', 'title': 'Name', 'operation': 'rename',
              'name': 'New name', 'expected_name': ''}
    with pytest.raises(ValueError, match='removed'):
        validate_sdk_dialog(rename)


async def test_api_missing_or_headless_name_never_creates_mutation_dialogs():
    api = ExtensionAPI({})
    assert await api.get_session_name() is None and not hasattr(api, 'set_session_name')
    api._session_name.set({'session_id': 'parent', 'owner_id': 'parent', 'name': 'Captured'})
    assert await api.get_session_name() == 'Captured'
    assert not hasattr(api, 'set_session_name') and not api._dialogs
