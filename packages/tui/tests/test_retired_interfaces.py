"""Old component aliases do not survive in the current terminal SDK."""
import importlib


def test_removed_tui_aliases_do_not_remove_the_editor_protocol():
    components = importlib.import_module('xdog.tui.components')
    for name in (
        "Box",
        "Editor",
        "Image",
        "Input",
        "Loader",
        "Markdown",
        "SelectList",
        "Spacer",
        "Text",
        "TruncatedText",
    ):
        assert hasattr(components, name)
        assert not hasattr(components, name + 'Component')
    assert importlib.import_module('xdog.tui.editor_component').EditorComponent is not None


def test_retired_panel_modules_and_typescript_accessors_are_absent():
    import importlib.util

    from xdog.tui import utils
    from xdog.tui.components.input import Input
    from xdog.tui.stdin_buffer import StdinBuffer

    for name in ('bounded_details', 'prompt_editor'):
        assert importlib.util.find_spec('xdog.tui.components.' + name) is None
    assert not hasattr(Input, 'getValue')
    assert not hasattr(utils, 'visible_width')
    for name in ('read_buffered', 'read_complete', 'has_data', 'on_data'):
        assert not hasattr(StdinBuffer(), name)
