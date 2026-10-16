"""FIFO basic dialog cache; host leases still own admission and answers."""
from __future__ import annotations

from typing import Any

from xdog.tui.tui import Component

_READ_ONLY_OPERATIONS = frozenset({
    'snapshot', 'name', 'model', 'usage', 'active_tools', 'commands', 'catalog', 'pending_messages',
})


class ExtensionDialogStack:
    def __init__(self) -> None:
        self.panels: dict[str, Component] = {}

    @staticmethod
    def accepts(dialog: dict[str, Any], dialogs: list[dict[str, Any]]) -> bool:
        kind, operation = dialog.get('kind'), dialog.get('operation')
        return (isinstance(kind, str)
                and not {'fields', 'parent_id', 'view_id', 'fullscreen', 'key_events', 'overlay', 'live_overlay'}
                & dialog.keys()
                and (kind in {'input', 'select', 'confirm'} and 'operation' not in dialog
                     or kind == 'session' and isinstance(operation, str) and operation in _READ_ONLY_OPERATIONS))


    def choose(self, dialogs: list[dict[str, Any]]) -> dict[str, Any] | None:
        live = [item for item in dialogs if self.accepts(item, dialogs)]
        identifiers = {item['id'] for item in live}
        self.panels = {key: panel for key, panel in self.panels.items() if key in identifiers}
        return live[0] if live else None

    def clear(self) -> None:
        self.panels.clear()
