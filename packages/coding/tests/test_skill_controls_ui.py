from __future__ import annotations

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp


def write_skills(directory):
    for slug, scope, marker in [('guard', 'session', 'SESSION-GUARD-BODY'), ('once', 'turn', 'TURN-ONLY-BODY')]:
        skill = directory / slug
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text(f'---\nname: {slug}\ndescription: {slug} description\n'
                                      f'metadata:\n  scope: {scope}\n---\n{marker}\n')


@pytest.mark.parametrize('scenario', ['stale', 'malformed', 'withdrawn', 'disconnected', 'unsolicited', 'timeout'])
def test_skill_receipts_cannot_change_foreign_or_unavailable_owner(scenario, monkeypatch):
    ui = ChatApp('/tmp/not-connected-skill-test.sock')
    ui._connection_ready = scenario != 'disconnected'
    ui._skills_supported = scenario != 'withdrawn'
    ui._gateway_session_id = 'current'
    ui._skills_session = 'current'
    ui._skills_request = '' if scenario == 'unsolicited' else 'request'
    ui._skills_deadline = 0
    if scenario == 'timeout':
        ui._poll()
        assert not ui._skills_request
    ui._handle_response({'type': 'skills_result', 'group_id': ui.group_id,
        'request_id': 'request', 'session_id': 'old' if scenario == 'stale' else 'current',
        'success': True, 'active': [None] if scenario == 'malformed' else ['guard'],
        'skills': [{'slug': 'guard', 'scope': 'session', 'active': True, 'description': 'RECEIPT-MARKER'}]})
    assert ui._gateway_session_id == 'current'
    assert 'RECEIPT-MARKER' not in str(ui._chat_log.render(120))
    ui._request_exit()
