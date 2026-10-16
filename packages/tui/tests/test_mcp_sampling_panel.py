"""Sampling component/registration is removed; retained MCP review capabilities are independent."""
import importlib.util

from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.keys import KeyEvent


def test_sampling_panel_and_browser_registration_apis_are_absent():
    assert importlib.util.find_spec('xdog.tui.components.mcp_sampling_panel') is None
    requests = []
    panel = McpPanel(lambda *args: requests.append(args), lambda: None, lambda: None, lambda: None)
    panel.publish('list', rows=({'server': 'owned', 'state': 'disconnected'},))
    for name in ('sampling_review', 'publish_sampling', 'sampling_closed', '_answer_sampling', '_cancel_sampling'):
        assert not hasattr(panel, name)
    before = list(requests)
    for key in (KeyEvent('b'), KeyEvent('j'), KeyEvent('b', alt=True)):
        panel.handle_input(key)
    assert requests == before
    for key in ('f', 'k', 'o'):
        panel.handle_input(KeyEvent(key))
    assert requests == before
    assert not hasattr(panel, 'roots_review') and not hasattr(panel, 'elicitation_review')
    panel.close()
