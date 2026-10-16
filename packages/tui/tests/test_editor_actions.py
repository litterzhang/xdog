"""Plugin editor read/write receipts are gone; the native editor remains user-owned."""
import importlib.util

from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent


class Theme:
    accent = bold = dim = border = staticmethod(lambda value: value)


def test_removed_plugin_editor_actions_module():
    assert importlib.util.find_spec('xdog.tui.editor_actions') is None


def test_native_editor_can_edit_undo_and_paste_without_implicit_submission():
    submitted = []
    editor = InputPanel(Theme())
    editor.on_submit = submitted.append
    editor.set_text('original')
    editor.handle_input(KeyEvent('x'))
    assert editor.get_text() == 'originalx'
    editor.handle_input(KeyEvent('z', alt=True))
    assert editor.get_text() == 'original'
    editor.handle_paste('\n/exit\ryes')
    assert '/exit' in editor.get_text() and not submitted
