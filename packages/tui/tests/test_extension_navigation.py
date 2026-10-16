"""Extension session navigation UI is removed, read-only session queries remain."""
import importlib.util

import pytest
from xdog.tui.components.extension_stack import ExtensionDialogStack


def test_extension_navigation_panel_is_removed():
    assert importlib.util.find_spec('xdog.tui.components.extension_navigation') is None


@pytest.mark.parametrize('operation', ['navigate', 'browse', 'rename', 'compact', 'message', 'select_tools'])
def test_mutation_operators_cannot_select_or_borrow_a_basic_question(operation):
    stack = ExtensionDialogStack()
    current = {'id': 'current', 'kind': 'input'}
    removed = {'id': 'old', 'kind': 'session', 'operation': operation}
    assert not stack.accepts(removed, [current])
    assert stack.choose([removed, current]) is current


@pytest.mark.parametrize('operation', ['snapshot', 'name', 'model', 'usage', 'active_tools', 'commands',
                                      'catalog', 'pending_messages'])
def test_read_only_session_queries_stay_available(operation):
    value = {'id': 'current', 'kind': 'session', 'operation': operation}
    assert ExtensionDialogStack.accepts(value, [])
