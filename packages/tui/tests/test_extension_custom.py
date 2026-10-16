"""No custom extension component; native focus and overlay ownership remain local."""
from __future__ import annotations

import importlib.util

import pytest
from xdog.tui.components.extension_dialog import ExtensionDialog
from xdog.tui.components.text import Text
from xdog.tui.keys import KeyEventType
from xdog.tui.stdin_buffer import KeyBytes
from xdog.tui.tui import TUI, Component, OverlayOptions


def test_extension_custom_component_module_is_removed():
    assert importlib.util.find_spec('xdog.tui.components.extension_custom') is None


@pytest.mark.parametrize('kind', ['custom', 'form'])
def test_basic_component_refuses_removed_kinds(kind):
    with pytest.raises(ValueError):
        ExtensionDialog('test', {'kind': kind, 'title': 'Removed'}, lambda value: None)


def test_native_focus_is_idempotent_and_resize_fences_confirmation(monkeypatch):
    import xdog.tui.tui as module
    answers = []
    panel = ExtensionDialog('test', {'kind': 'confirm', 'title': 'Question'}, answers.append)
    tui = TUI()
    tui.set_focus(panel)
    panel.render(80)
    lease = tui.input_lease
    tui.set_focus(panel)
    assert tui.input_lease == lease and panel._visible
    tui._previous_width, tui._previous_height = 80, 24
    monkeypatch.setattr(module, '_terminal_width', lambda: 79)
    monkeypatch.setattr(module, '_terminal_height', lambda: 24)
    tui._handle_frame(KeyBytes(b'\r'))
    assert not answers and not panel._visible
    tui._handle_frame(KeyBytes(b'\x1b'))
    assert answers == [None]


def test_native_components_may_opt_into_release_without_a_plugin_observer(monkeypatch):
    class Native(Component):
        wants_key_release = True
        def render(self, width):
            return ['native']
        def handle_input(self, event):
            seen.append(event.event_type)
            return True
    seen, tui = [], TUI()
    tui.set_focus(Native())
    assert tui._handle_frame(KeyBytes(b'\x1b[113;1:2u\x1b[113;1:3u'))
    assert seen == [KeyEventType.REPEAT, KeyEventType.RELEASE]


def test_general_native_overlay_handles_keep_independent_focus_and_geometry():
    tui = TUI()
    editor, first, permission = Text('parent draft'), Text('native view'), Text('permission')
    tui.set_focus(editor)
    handle = tui.show_overlay(first, OverlayOptions(width='75%', max_height=12, anchor='top-right'))
    assert handle.is_focused() and tui.has_overlay() and not tui.fullscreen
    tui._composite_overlays(['parent draft'], 80, 24)
    protected = tui.show_overlay(permission)
    assert protected.is_focused() and not handle.is_focused()
    handle.hide()
    assert protected.is_focused() and tui.has_overlay() and len(tui._overlay_stack) == 1
    protected.hide()
    assert not tui.has_overlay() and tui._focused is editor
