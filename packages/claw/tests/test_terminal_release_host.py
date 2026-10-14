"""Independent native Claw release negotiation selects only the original input owner."""
from __future__ import annotations

from types import SimpleNamespace

from xdog.claw.channels.tui.tui_app import ChatApp


def test_child_release_selection_never_borrows_parent_registration():
    ui = ChatApp('/unused')
    ui._terminal_supported = ui._terminal_release_supported = ui._terminal_child_supported = True
    ui._extension_snapshot = {'terminal_input': True, 'terminal_events': ['release']}
    assert ui._terminal_release_available()
    child = 'a' * 12
    ui._child_panel = SimpleNamespace(terminal_child=child)
    ui._terminal_children = {child: {'active': True, 'generation': 1, 'events': ['key', 'paste']}}
    assert ui._terminal_available() and not ui._terminal_release_available()
    ui._accept_child_terminal_metadata({'child_terminal_owner': 'b' * 32,
        'terminal_children': {child: {'active': True, 'generation': 1, 'events': ['release']}}})
    assert ui._terminal_release_available()
    ui._terminal_release_supported = False
    assert not ui._terminal_release_available()
    ui._terminal_release_supported = True
    ui._accept_child_terminal_metadata({'child_terminal_owner': 'b' * 32,
        'terminal_children': {child: {'active': True, 'generation': 1}}})
    assert not ui._terminal_release_available()  # Legacy child registry never opts into release.
