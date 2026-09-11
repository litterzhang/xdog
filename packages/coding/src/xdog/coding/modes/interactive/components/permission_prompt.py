"""Coding policy adapter for the shared permission panel."""
from __future__ import annotations

from collections.abc import Callable

from xdog.coding.core.permissions import PermissionDecision, PermissionRequest
from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.permission_panel import PermissionPanel
from xdog.tui.components.select_list import SelectItem


class PermissionPromptComponent(PermissionPanel[PermissionDecision]):
    def __init__(
        self, request: PermissionRequest, theme: Theme,
        on_decision: Callable[[PermissionDecision], None],
    ) -> None:
        self.request = request
        super().__init__(
            summary=request.summary, tool_name=request.tool_name, theme=theme,
            choices=[
                SelectItem("Allow once", "allow_once", "Run only this call"),
                SelectItem("Allow for this session (same call)", "allow_session", "Same call only"),
                SelectItem("Deny", "deny", "Return a denial to the model"),
            ],
            on_decision=on_decision, on_cancel=lambda: on_decision("deny"), cancel_label="deny",
        )
