"""Planning mode is not a native user control; access scope is separate."""
import importlib

import pytest


def test_planning_panel_module_is_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module('xdog.tui.components.plan_panel')
