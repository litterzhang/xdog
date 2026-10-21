"""Review a captured output artifact and choose its explicit local destination."""
from __future__ import annotations

from collections.abc import Callable

from xdog.tui.components.input_panel import InputPanel
from xdog.tui.components.panel import ThemedPanel
from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.editor_layout import layout_editor
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.transcript_transfer import PreparedTransfer
from xdog.tui.utils import sanitize_terminal_text


class OutputPanel(ThemedPanel):
    requires_visible_confirmation = True

    def __init__(self, prepared: PreparedTransfer, path: str, on_confirm: Callable[[str], None],
                 on_cancel: Callable[[], None]) -> None:
        self.prepared = prepared
        self._focused = False
        self.on_confirm, self.on_cancel = on_confirm, on_cancel
        self._path = InputPanel(self.panel_theme, max_rows=1)
        self._path.set_text(path)
        self._path.on_submit = self._finish_path
        self._path.on_escape = on_cancel
        self._path.on_ctrl_c = on_cancel
        self._path.on_ctrl_d = on_cancel
        self._editing = not bool(prepared.options.path)
        self._budget = 8
        self._visible = False
        self._paged = False
        self._previewing = False
        self._scroll = 0
        self._seen: set[int] = set()
        self._review_layout: tuple[int, int, str, bool] | None = None
        self._preview = TranscriptPanel(
            lambda: (TranscriptRecord("review", "system", "Captured output preview", prepared.text),),
            lambda: None, lambda: None,
        )

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if value != self._focused:
            self._reset_review()
        self._focused = value
        self._path.focused = value and self._editing

    def _reset_review(self) -> None:
        self._visible = False
        self._seen.clear()
        self._review_layout = None
        self._scroll = 0

    def _finish_path(self, value: str) -> None:
        # InputPanel clears its draft before invoking on_submit; retain the
        # submitted value rather than re-reading the now-empty nested editor.
        if value.strip():
            self._path.set_text(value)
            self._editing = False
            self._path.focused = False
            self._previewing = False
            self._reset_review()  # A fresh rendered review is required before Enter can save.

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, min(12, rows))

    def close(self) -> None:
        self._preview.close()

    def poll(self) -> bool:
        return self._preview.poll()

    def handle_paste(self, text: str) -> bool:
        if self._editing:
            self._path.handle_paste(sanitize_terminal_text(text).replace("\n", " "))
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        if event.event_type == KeyEventType.RELEASE:
            return True
        if event.matches("escape") or event.matches("ctrl+c"):
            self.on_cancel()
        elif event.matches("tab"):
            if event.event_type != KeyEventType.PRESS:
                return True
            self._editing = not self._editing
            self._previewing = False
            self._path.focused = self._focused and self._editing
            self._reset_review()
        elif self._editing:
            self._path.handle_input(event)
        elif event.matches("v") and self._paged:
            if event.event_type == KeyEventType.PRESS:
                self._previewing = not self._previewing
                self._visible = False
        elif event.matches("enter"):
            if event.event_type == KeyEventType.PRESS and self._visible and not self._previewing:
                self._visible = False
                self.on_confirm(self._path.get_text().strip())
        elif event.key in {"up", "down", "pageup", "pagedown", "home", "end", "left", "right"}:
            if self._paged and not self._previewing:
                step = max(1, self._budget - 3) if event.key in {"pageup", "pagedown"} else 1
                self._scroll = (0 if event.key == "home" else 1000000 if event.key == "end" else
                                max(0, self._scroll + (-step if event.key in {"up", "pageup"} else step)))
                self._visible = False
            else:
                self._preview.handle_input(event)
        return True

    def render(self, width: int) -> list[str]:
        self._visible = False
        if not self._budget:
            return []
        prepared = self.prepared
        share = prepared.options.action == "share"
        title = "Review local share — no upload" if share else "Review local export"
        warning = ("Review for private data; redaction is heuristic. Tools/reasoning excluded by default."
                   if share else "Private snapshot; may contain sensitive data. Existing files are never replaced.")
        if prepared.options.scope == "artifact":
            warning = ("Lossless binary/metadata; NO redaction. Sensitive original bytes included; "
                       "never overwrite/upload.")
        summary = (f"{title}\n{prepared.options.scope} · {prepared.entries} entries · {len(prepared.data):,} bytes · "
                   f"roles: {', '.join(prepared.roles)}\n{warning}\n"
                   f"SHA-256: {prepared.sha256[:16]}…")
        metadata = [row.text for row in layout_editor(summary, max(1, width)).rows]
        target = [row.text for row in layout_editor("Target: " + sanitize_terminal_text(self._path.get_text()),
                                                    max(1, width)).rows]
        action = "Enter saves local HTML · Tab edits path · Esc cancels" if share else (
            "Enter saves private export · Tab edits path · Esc cancels"
        )
        fits = len(metadata) + len(target) + 3 <= self._budget and width >= 32
        self._paged = not fits
        if fits:
            self._previewing = False  # Both payload and fields are visible again; v need not trap confirmation.
        count = max(0, self._budget - 3)
        content = summary + "\nTarget: " + sanitize_terminal_text(self._path.get_text())
        layout = (width, count, content, self._paged)
        if layout != self._review_layout:
            self._seen.clear()
            self._scroll = 0
            self._review_layout = layout
        if self._editing:
            self._path.set_render_budget(1, show_borders=False)
            rows = metadata[:max(0, self._budget - 2)] + self._render_child(self._path, width)
            rows.append("Enter reviews path (does not save) · Tab preview · Esc cancels")
        elif self._paged and self._previewing:
            self._preview.set_render_budget(max(0, self._budget - 2))
            rows = [title + " · payload preview", *self._render_child(self._preview, width),
                    "v metadata/path · no save in preview · Esc cancels"]
        elif fits:
            room = self._budget - len(metadata) - len(target) - 1
            self._preview.set_render_budget(room)
            rows = metadata + target + self._render_child(self._preview, width) + [action]
            self._visible = bool(self._path.get_text().strip()) and self._preview.settled and self._focused
            if not self._visible:
                rows[-1] = "Loading reviewed snapshot; confirmation disabled · Esc cancels"
        else:
            body = metadata + target
            self._scroll = min(self._scroll, max(0, len(body) - count))
            end = min(len(body), self._scroll + count)
            if width >= 24 and count and self._focused:
                self._seen.update(range(self._scroll, end))
            # Prepare the immutable preview worker; v exposes it separately when
            # small geometry cannot show both review fields and payload at once.
            self._preview.set_render_budget(2)
            self._render_child(self._preview, width)
            self._visible = (len(self._seen) == len(body) and bool(self._path.get_text().strip())
                             and self._preview.settled and self._focused and width >= 24 and self._budget >= 4)
            rows = [f"Review output {self._scroll + 1}-{end}/{len(body)}", *body[self._scroll:end],
                    "PgUp/PgDn all fields · v payload · Tab path",
                    action if self._visible else "Review ALL metadata/path before saving · Esc cancels"]
        return self._styled_rows(rows[:self._budget], width)
