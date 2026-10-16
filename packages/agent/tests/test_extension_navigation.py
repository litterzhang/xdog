"""Legacy host navigation packets never acquire SDK mutation authority."""
import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import validate_sdk_dialog


@pytest.mark.parametrize('action', ['new', 'resume', 'branch', 'goto', 'fork'])
def test_navigation_requests_are_removed_even_with_well_formed_targets(action):
    target = {'action': action, 'session_id': 'saved' if action == 'resume' else '',
              'branch_id': 'branch' if action == 'branch' else '', 'index': 0 if action == 'goto' else -1}
    with pytest.raises(ValueError, match='removed'):
        validate_sdk_dialog({'kind': 'session', 'id': 'nav', 'title': 'Navigate',
                             'operation': 'navigate', 'target': target})
    assert not hasattr(ExtensionAPI({}), 'navigate_session')
    assert not hasattr(ExtensionAPI({}), 'open_sessions')
