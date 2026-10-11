from __future__ import annotations

from collections.abc import Callable

from xdog.tui.attachments import Attachment
from xdog.tui.components.image import Image, ImageOptions
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width


class AttachmentPanel(Component):
    focused = False

    def __init__(self, item: Attachment, on_close: Callable[[], None]) -> None:
        self.item = item
        self.on_close = on_close
        self._budget = 8
        self._image = Image(item.data, alt="attachment", options=ImageOptions(
            filename=item.name, max_width_cells=60, max_height_cells=6,
        ))

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, min(8, rows))

    def handle_input(self, event: KeyEvent) -> bool:
        if event.matches("escape") or event.matches("ctrl+c"):
            self.on_close()
        return True

    def handle_paste(self, text: str) -> bool:
        return True

    def render(self, width: int) -> list[str]:
        if not self._budget:
            return []
        rows = [truncate_to_width("Attachment · " + sanitize_terminal_text(self.item.summary), width)]
        if self._budget >= 8:
            rows.extend(self._image.render(max(3, width)))
        else:
            rows.append(truncate_to_width("Enlarge terminal for image preview · Esc close", width))
        return rows[:self._budget]
