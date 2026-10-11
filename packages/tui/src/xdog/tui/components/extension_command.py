"""Rich command display artifact; never inserts into or submits the agent draft."""

from __future__ import annotations

import base64
import json
from typing import Any

from xdog.tui.components.image import Image, ImageOptions
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width


class ExtensionCommand(Component):
    transcript_role = "system"

    def __init__(self, command: str, result: dict[str, Any], text: str, *, owner: str = "") -> None:
        self.command, self.owner = command, owner
        self._text = sanitize_terminal_text(text)
        self._expanded = False
        self._images = [
            Image(
                base64.b64decode(part["data"], validate=True),
                alt=part["mimeType"],
                options=ImageOptions(max_height_cells=6),
            )
            for part in result["content"]
            if part["type"] == "image"
        ]
        # Typed metadata is retained independently of presentation and never stringified as a prompt.
        self.result = json.loads(json.dumps(result, ensure_ascii=True))

    @property
    def detail_title(self) -> str:
        return "Extension command /" + self.command + " (read-only artifact)"

    @property
    def detail_body(self) -> str:
        return self._text

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded

    def render(self, width: int) -> list[str]:
        rows = [truncate_to_width(sanitize_terminal_text(self.detail_title), max(0, width), "")]
        if self._expanded:
            from xdog.tui.utils import wrap_text_with_ansi

            rows.extend(wrap_text_with_ansi(self._text, max(1, width)))
        else:
            preview = self._text.splitlines()[0] if self._text else "(image/metadata output)"
            rows.append(truncate_to_width("  " + preview + " · Ctrl+O/transcript for complete data", max(0, width), ""))
        for image in self._images:
            if width < 3:
                rows.append(truncate_to_width("[image]", max(0, width), ""))
            else:
                for row in image.render(width):
                    rows.append(row if "\x1b_G" in row or "\x1b]1337;" in row
                                else truncate_to_width(row, max(0, width), ""))
        return rows
