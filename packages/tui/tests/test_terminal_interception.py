"""UI036: no public interception queue or replay bypass remains."""
import importlib.util

from xdog.tui.tui import TUI


def test_terminal_interception_api_is_removed():
    assert importlib.util.find_spec('xdog.tui.terminal_interception') is None
    assert importlib.util.find_spec('xdog.tui.prompt_terminal') is None
    tui = TUI()
    assert not hasattr(tui, 'set_frame_interceptor')
    assert not hasattr(tui, 'deliver_frame')
    assert not hasattr(tui, '_frame_interceptor')
