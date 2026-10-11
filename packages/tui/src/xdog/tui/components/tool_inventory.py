"""Read-only configured tool definitions on the existing full-source browser."""
from __future__ import annotations

import json
from typing import Any

from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.transcript_browser import TranscriptRecord


def inventory_records(snapshot: dict[str, Any]) -> tuple[TranscriptRecord, ...]:
    # Application hosts independently validate the full owner-bound envelope.
    model, enabled, tools = snapshot['model'], snapshot['api_enabled'], snapshot['tools']
    active = set(snapshot['active_tools']) if 'active_tools' in snapshot else None
    selection = ('Selection: unknown (legacy snapshot)' if active is None else
                 f'Selection: {snapshot["selection_mode"]} · selected {len(active)} / {len(tools)} configured'
                 f' · revision {snapshot["selection_revision"]}')
    summary = (f'Model: {model}\nConfigured definitions: {len(tools)}\n'
               f'{selection}\n'
               f'Model API gate: {"enabled/unknown support" if enabled else "disabled"}\n'
               'Read-only snapshot; listing is NOT execution approval.\n'
               'Refresh explicitly; no tool execution, registration or model input.')
    records = [TranscriptRecord('tools:summary', 'system', 'Configured and active tool inventory', summary)]
    for tool in tools:
        schema = json.dumps(tool['parameters'], ensure_ascii=False, allow_nan=False, sort_keys=True,
                            separators=(',', ':'))
        status = ('unknown (legacy snapshot)' if active is None else
                  'selected' if tool['name'] in active else 'excluded')
        records.append(TranscriptRecord('tools:' + tool['name'], 'tool', tool['name'],
            'Name: ' + tool['name'] + '\nSelection: ' + status + '\n' + tool['description']
            + '\nSchema (exact JSON):\n' + schema))
    return tuple(records)


class ToolInventoryPanel(TranscriptPanel):
    """Shares transcript search/selection/scroll/fullscreen, never conversation fallback."""
    caption = "Tools"
    content_label = "tool definitions"
    source_label = "configured tool inventory"

    inventory_ready = False

    @property
    def settled(self) -> bool:
        return self.inventory_ready and super().settled

    def begin_inventory_read(self) -> None:
        self.inventory_ready = False
        self.set_source_status('Reading configured definitions', busy=True)

    def publish_inventory(self, records: tuple[TranscriptRecord, ...]) -> None:
        self.replace_records(records)
        self.inventory_ready = True
        self.set_source_status('Configured snapshot · selection metadata · r refresh · not approval')

    def invalidate_inventory(self, reason: str) -> None:
        self.inventory_ready = False
        self.replace_records(())
        self.set_source_status(reason)
