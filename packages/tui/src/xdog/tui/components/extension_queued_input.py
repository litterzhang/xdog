"""Whole-proposal, false-default review of extension input to an existing active run."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from xdog.tui.components.extension_form import ExtensionForm
from xdog.tui.components.extension_navigation import ExtensionNavigation

_FIELDS = {'id', 'kind', 'operation', 'title', 'message', 'timeout', 'extension', 'parent_id',
           'text', 'delivery', 'images', 'host_message', 'image_summaries', 'child_id', 'queued_input_generation'}
_MEDIA = frozenset({'image/png', 'image/jpeg', 'image/webp'})


def _projection(extension: str, dialog: dict[str, Any]) -> dict[str, Any]:
    """Validate and copy only the bounded binary-free review; no agent dependency."""
    if (not isinstance(extension, str) or re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,31}', extension) is None
            or not isinstance(dialog, dict) or dialog.keys() - _FIELDS
            or dialog.get('kind') != 'session' or dialog.get('operation') != 'queue_input'
            or not isinstance(dialog.get('images'), list) or dialog['images']
            or dialog.get('delivery') not in ('steer', 'follow_up')):
        raise ValueError('Queued input requires a binary-free host review projection')
    if ('child_id' in dialog and (not isinstance(dialog['child_id'], str)
            or re.fullmatch('[a-f0-9]{12}', dialog['child_id']) is None)
            or 'queued_input_generation' in dialog and (type(dialog['queued_input_generation']) is not int
                or not 0 <= dialog['queued_input_generation'] <= 2**53)):
        raise ValueError('Invalid host child queued-input review identity')
    for key, limit in (('title', 1000), ('message', 8000), ('text', 16384), ('host_message', 17000)):
        value = dialog.get(key, '' if key == 'message' else None)
        if not isinstance(value, str) or len(value.encode('utf-8')) > limit:
            raise ValueError('Invalid queued input review text')
    if not dialog['title'].strip() or not dialog['text'].strip() or '\x00' in dialog['text']:
        raise ValueError('Invalid queued input review text')
    expected = f'[User-approved message requested by extension {extension}]\n\n' + dialog['text']
    if dialog['host_message'] != expected:
        raise ValueError('Queued input review must identify the exact extension and proposed text')
    rows = dialog.get('image_summaries')
    if not isinstance(rows, list) or len(rows) > 4:
        raise ValueError('Invalid queued image review summaries')
    summaries = []
    encoded_bytes = 0
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'mime_type', 'bytes', 'sha256'}
                or not isinstance(row['mime_type'], str) or row['mime_type'] not in _MEDIA
                or type(row['bytes']) is not int or not 1 <= row['bytes'] <= 4 * 1024 * 1024
                or not isinstance(row['sha256'], str) or re.fullmatch('[a-f0-9]{64}', row['sha256']) is None):
            raise ValueError('Invalid queued image review summary')
        encoded_bytes += ((row['bytes'] + 2) // 3) * 4
        summaries.append(dict(row))
    if encoded_bytes > 6 * 1024 * 1024:
        raise ValueError('Queued image summaries exceed the proposal budget')
    return {**dialog, 'images': [], 'image_summaries': summaries}


class ExtensionQueuedInput(ExtensionNavigation):
    def __init__(self, extension: str, dialog: dict[str, Any], answer: Callable[[Any], None], current: str) -> None:
        dialog = _projection(extension, dialog)
        self.dialog = dialog
        self.form = ExtensionForm(extension, {
            'title': dialog['title'],
            'message': 'Original active owner: ' + current + '\n' + dialog.get('message', ''),
            'fields': [{'name': 'approve', 'kind': 'boolean', 'label': 'Queue this exact extension input',
                'required': True, 'default': False,
                'description': json.dumps({'delivery': dialog['delivery'], 'message': dialog['host_message'],
                    'images': dialog['image_summaries']}, ensure_ascii=False, indent=2) + '\n'
                    'After callback success, queue only to this same active run if it is still available. '
                    'Image descriptors identify the exact reviewed inline bytes; no image is opened by this form. '
                    'No new turn, borrowed draft, attachment, source change or tool permission is approved. '
                    'Staged is not admitted, consumed, or completed.'}],
        }, lambda value: answer(bool(value and value.get('approve') is True)))
