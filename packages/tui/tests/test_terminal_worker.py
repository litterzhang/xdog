"""UI036: no plugin terminal worker remains to intercept local input."""
import importlib.util


def test_terminal_worker_module_is_removed():
    assert importlib.util.find_spec('xdog.tui.terminal_worker') is None
