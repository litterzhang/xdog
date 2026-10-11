from __future__ import annotations

import json
from pathlib import Path

import pytest
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.prompt_keymap import PromptKeymap


class Theme:
    accent = bold = dim = border = staticmethod(lambda value: value)


def test_custom_bindings_replace_defaults_and_discover_current_keys() -> None:
    keymap = PromptKeymap.from_overrides({"send": "CTRL+s", "newline": ["alt+n"], "details": []})
    assert keymap.resolve(KeyEvent("s", ctrl=True)) == "send"
    assert keymap.resolve(KeyEvent("enter")) is None
    assert keymap.resolve(KeyEvent("enter", ctrl=True)) is None
    assert keymap.label("details") == "unbound"
    assert "Ctrl+S: send" in keymap.help_text()
    assert "Ctrl+Enter: newline" not in keymap.help_text()
    editor = InputPanel(Theme())
    editor.set_keymap(keymap)
    submitted: list[str] = []
    editor.on_submit = submitted.append
    editor.set_text("draft")
    assert not editor.handle_input(KeyEvent("enter"))
    editor.handle_input(KeyEvent("n", alt=True))
    assert editor.get_text() == "draft\n"
    editor.handle_input(KeyEvent("s", ctrl=True))
    assert submitted == ["draft"]


@pytest.mark.parametrize("overrides", [
    {"unknown": "f2"}, {"details": "ctrl+a"}, {"send": "ctrl+s", "details": "CTRL+S"},
    {"details": ["f2", "F2"]}, {"send": "a"}, {"send": "shift+a"},
    {"details": "ctrl+wat"}, {"details": "ctrl+ctrl+a"}, {"details": 1},
    {"details": [False]}, {"details": "ctrl+alt"}, {"details": "ctrl+"},
    {"cancel": []}, {"redraw": "f2"}, {"external_editor": "escape"}, {"external_editor": "ctrl+l"},
])
def test_invalid_or_conflicting_keymap_is_rejected(overrides) -> None:
    with pytest.raises(ValueError):
        PromptKeymap.from_overrides(overrides)


def test_invalid_file_keeps_all_defaults_with_diagnostic(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    assert PromptKeymap.load(path) == PromptKeymap()
    for data in (
        "[1]", "not json", json.dumps({"details": "ctrl+a"}), "x" * 65_537,
        '{"send":"f2", "send":"f3"}',
    ):
        path.write_text(data)
        loaded = PromptKeymap.load(path)
        assert loaded.bindings == PromptKeymap().bindings
        assert "using defaults" in loaded.error
    path.write_text(json.dumps({"details": "alt+t"}))
    assert PromptKeymap.load(path).label("details") == "Alt+T"


def test_editor_motion_undo_and_completion_use_custom_keys() -> None:
    from xdog.tui.autocomplete import CompletionItem, StaticCompletionProvider

    editor = InputPanel(Theme())
    editor.set_keymap(PromptKeymap.from_overrides({"left": "alt+b", "undo": "ctrl+w", "complete": "f2"}))
    editor.set_text("ab")
    editor.handle_input(KeyEvent("b", alt=True))
    editor.handle_input(KeyEvent("x"))
    assert editor.get_text() == "axb"
    editor.handle_input(KeyEvent("w", ctrl=True))
    assert editor.get_text() == "ab"
    editor.set_autocomplete_provider(StaticCompletionProvider([CompletionItem("@file.py")]))
    editor.set_text("read @fi")
    editor.handle_input(KeyEvent("f2"))
    assert editor.get_text() == "read @file.py"


def test_repeat_and_release_cannot_submit_or_interrupt() -> None:
    editor = InputPanel(Theme())
    submitted: list[str] = []
    interrupted: list[bool] = []
    editor.on_submit = submitted.append
    editor.on_ctrl_c = lambda: interrupted.append(True)
    editor.set_text("draft")
    for event_type in (KeyEventType.REPEAT, KeyEventType.RELEASE):
        editor.handle_input(KeyEvent("enter", event_type=event_type))
        editor.handle_input(KeyEvent("c", ctrl=True, event_type=event_type))
    assert submitted == []
    assert interrupted == []
    assert editor.get_text() == "draft"


def test_parent_save_is_configurable_host_action_not_editor_or_send_event():
    from xdog.tui.prompt_keymap import EDITOR_EVENTS
    default = PromptKeymap()
    assert default.resolve(KeyEvent('s', ctrl=True, alt=True)) == 'save_draft'
    assert 'save_draft' not in EDITOR_EVENTS
    remapped = PromptKeymap.from_overrides({'save_draft': 'f8'})
    assert remapped.resolve(KeyEvent('f8')) == 'save_draft'
    assert remapped.resolve(KeyEvent('s', ctrl=True, alt=True)) is None
    assert 'F8: save_draft' in remapped.help_text()
    disabled = PromptKeymap.from_overrides({'save_draft': []})
    assert disabled.resolve(KeyEvent('s', ctrl=True, alt=True)) is None
    assert disabled.label('save_draft') == 'unbound'
    for overrides in ({'save_draft': 'enter'}, {'save_draft': 'escape'}, {'save_draft': 'ctrl+l'}):
        with pytest.raises(ValueError):
            PromptKeymap.from_overrides(overrides)


def test_remote_parent_save_is_separate_configurable_host_control():
    from xdog.tui.prompt_keymap import EDITOR_EVENTS
    default = PromptKeymap()
    assert default.resolve(KeyEvent('s', ctrl=True, alt=True, shift=True)) == 'remote_save_draft'
    assert 'remote_save_draft' not in EDITOR_EVENTS
    remapped = PromptKeymap.from_overrides({'remote_save_draft': 'f8'})
    assert remapped.resolve(KeyEvent('f8')) == 'remote_save_draft'
    assert remapped.resolve(KeyEvent('s', ctrl=True, alt=True, shift=True)) is None
    disabled = PromptKeymap.from_overrides({'remote_save_draft': []})
    assert disabled.resolve(KeyEvent('s', ctrl=True, alt=True, shift=True)) is None
