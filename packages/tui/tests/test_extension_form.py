"""Neither public plugin forms nor retired extension mutation field reviews remain."""
import importlib.util

import pytest
from xdog.tui.components.extension_dialog import ExtensionDialog


def test_plugin_form_and_retired_private_mutation_review_modules_are_absent():
    assert importlib.util.find_spec('xdog.tui.components.extension_form') is None
    assert importlib.util.find_spec('xdog.tui.components.review_fields') is None


def test_basic_dialog_cannot_be_used_as_a_multifield_extension_form():
    with pytest.raises(ValueError):
        ExtensionDialog('legacy', {'kind': 'form', 'title': 'Removed', 'fields': []}, lambda value: None)
