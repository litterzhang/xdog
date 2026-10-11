"""Owned dialog selection/cache; hosts still validate runtime and transport leases."""
from __future__ import annotations

from typing import Any

from xdog.tui.tui import Component


class ExtensionDialogStack:
    def __init__(self) -> None:
        self.panels: dict[str, Component] = {}

    @staticmethod
    def accepts(dialog: dict[str, Any], dialogs: list[dict[str, Any]]) -> bool:
        parent = dialog.get('parent_id')
        if parent is None:
            return True
        if not isinstance(parent, str) or not parent or parent == dialog.get('id'):
            return False
        owned = next((item for item in dialogs if item['id'] == parent), None)
        return (owned is not None and owned['kind'] == 'custom'
                and all(owned.get(key) == dialog.get(key) for key in
                        ('extension', 'child_id', 'child_owner', 'session_id')))

    def choose(self, dialogs: list[dict[str, Any]]) -> dict[str, Any] | None:
        live = {item['id'] for item in dialogs}
        self.panels = {key: panel for key, panel in self.panels.items() if key in live}
        roots = [item for item in dialogs if not item.get('parent_id')]
        if not roots:
            return None
        current = roots[0]  # Preserve FIFO among unrelated root questions.
        visited = {current['id']}
        for _ in range(3):
            children = [item for item in dialogs if item.get('parent_id') == current['id']]
            if not children:
                return current
            child = children[0]
            if child['id'] in visited or not self.accepts(child, dialogs):
                return current
            current = child
            visited.add(current['id'])
        return current

    def clear(self) -> None:
        self.panels.clear()
