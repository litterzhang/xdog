"""Whole-proposal review; the form itself cannot mutate any editor."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xdog.tui.components.extension_form import ExtensionForm
from xdog.tui.components.extension_navigation import ExtensionNavigation


class ExtensionEditorWrite(ExtensionNavigation):
    def __init__(self, extension: str, dialog: dict[str, Any], answer: Callable[[Any], None]) -> None:
        self.dialog = dialog
        identity = dialog['editor_write']
        self.form = ExtensionForm(extension, {
            'title': dialog['title'],
            'message': 'Original owner: ' + identity['session_id'] +
                (' / child ' + identity['child_id'] if identity['child_id'] else '') +
                '\nCallback: ' + identity['method'],
            'fields': [{'name': 'approve', 'kind': 'boolean', 'label': 'Replace this owner’s unsent text',
                'required': True, 'default': False,
                'description': 'EXACT PROPOSED TEXT:\n' + dialog['text'] +
                    '\nEND PROPOSED TEXT\nAfter callback success and unchanged original draft/owner only. '
                    'Attachments remain; nothing is submitted and no tool permission changes.'}],
        }, lambda value: answer(bool(value and value.get('approve') is True)))
