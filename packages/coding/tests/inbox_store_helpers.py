"""Durable intake is separate from Agent construction and model history."""


import pytest


@pytest.fixture
def box(tmp_path):
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore

    manager = SessionManager(tmp_path / 'sessions')
    data = manager.create_session(working_dir=tmp_path)
    message = InboxMessage(data.session_id, tmp_path, 'm1', 'Review', ({'path': 'a.py'},), 'Eyes', {'route': 'r'})
    return InboxStore(manager._dir, tmp_path / 'inbox'), message, manager, data
