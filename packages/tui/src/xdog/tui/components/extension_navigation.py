"""Visible whole-target navigation consent; no direct session/runtime mutation."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from xdog.tui.components.extension_form import ExtensionForm
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import Component


class ExtensionNavigation(Component):
    requires_visible_confirmation = True

    def __init__(self, extension: str, dialog: dict[str, Any], answer: Callable[[Any], None], current: str) -> None:
        naming = dialog.get('operation') == 'rename'
        selecting_tools = dialog.get('operation') == 'select_tools'
        compacting = dialog.get('operation') == 'compact'
        messaging = dialog.get('operation') == 'message'
        target = ({'action': 'rename', 'name': dialog['name'], 'expected_name': dialog['expected_name']}
                  if naming else {'action': 'select_tools', 'names': dialog['names'],
                      'revision': dialog['revision'], 'current_tools': dialog['current_tools']}
                  if selecting_tools else {'action': 'compact', 'instructions': dialog['instructions']}
                  if compacting else {'action': 'message', 'message': dialog['host_message'],
                                     'images': dialog.get('image_summaries', [])}
                  if messaging else dict(dialog['target']))
        self.dialog = dialog
        self.form = ExtensionForm(
            extension,
            {
                "title": dialog["title"],
                "message": "Current owner: " + current + "\n" + dialog.get("message", "") +
                    ("\nWait for the original producer to finish successfully before name CAS persistence."
                     if dialog.get("name_after_run") is True else ""),
                "fields": [
                    {
                        "name": "approve",
                        "kind": "boolean",
                        "label": ("Send this extension message" if messaging else
                                  "Queue this exact compaction" if compacting else
                                  "Queue this active-tool selection" if selecting_tools else
                                  "Queue this exact name change" if naming else "Queue this exact navigation"),
                        "required": True,
                        "default": False,
                        "description": json.dumps(target, ensure_ascii=False, indent=2) + "\n" + (
                            "After callback success, start a new model turn with this exact labeled text "
                            "and listed images. "
                            "No composer text, uploads or unlisted attachments are sent. "
                            "Tool permission policy remains unchanged. "
                            "Queued is not consumed or completed." if messaging else
                            "After callback success, host compaction may call the selected model, "
                            "replace older history, "
                            "and run permitted memory-flush tools. Existing permissions remain; "
                            "queued is not completed."
                            if compacting else
                            "Tool inventory/revision are revalidated after callback success; queued is not applied. "
                            "Selection changes no tool permissions and executes nothing." if selecting_tools else
                            "Name/owner are revalidated after callback success. Queued is not saved. "
                            "No history/tools/models/permission changes are approved." if naming else
                            "Target is revalidated by the existing runtime after callback success. "
                            "Queued is not activated. No tools/models/permission changes are approved."),
                    }
                ],
            },
            lambda value: answer(bool(value and value.get("approve") is True)),
        )

    @property
    def focused(self) -> bool:
        return self.form.focused

    @focused.setter
    def focused(self, value: bool) -> None:
        self.form.focused = value

    def invalidate(self) -> None:
        self.form.invalidate()

    def set_render_budget(self, rows: int) -> None:
        self.form.set_render_budget(rows)

    def handle_paste(self, text: str) -> bool:
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        return self.form.handle_input(event)

    def render(self, width: int) -> list[str]:
        return self.form.render(width)
