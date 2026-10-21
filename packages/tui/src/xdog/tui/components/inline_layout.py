"""Compact main-buffer composition for interactive terminal applications."""

from __future__ import annotations

from collections.abc import Callable

from xdog.tui.components.panel import PanelTheme
from xdog.tui.components.text import Text
from xdog.tui.tui import Component
from xdog.tui.utils import strip_ansi, truncate_to_width


class CompactText(Text):
    """A short summary whose logical lines never wrap into extra rows."""

    def __init__(
        self, text: str = "", padding_x: int = 1, padding_y: int = 1,
        bg_fn: Callable[[str], str] | None = None, *, style: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__(text, padding_x, padding_y, bg_fn)
        self._style = style or (lambda value: value)

    def render(self, width: int) -> list[str]:
        return [self._style(truncate_to_width(line, max(0, width), "…")) for line in self.text.splitlines()]


class InlineLayout(Component):
    """Compose durable transcript rows with a terminal-height-bounded tail.

    The transient tail contains, in order, an optional work summary, at most one
    auxiliary surface, one status row, the editor and optional hints. Details and permission panels appear
    after the editor. Auxiliary priority
    is permission, details/autocomplete, then queue.
    """

    def __init__(
        self,
        transcript: Component,
        status: Component,
        editor: Component,
        *,
        work: Component | None = None,
        permission: Component | None = None,
        details: Component | None = None,
        queue: Component | None = None,
        render_height: int = 24,
        hints: Component | None = None,
        panel_theme: PanelTheme | None = None,
    ) -> None:
        self.panel_theme = panel_theme or PanelTheme()
        self.fullscreen_view: Component | None = None
        self.hints = hints
        self.transcript = transcript
        self.work = work
        self.status = status
        self.editor = editor
        self.permission = permission
        self.details = details
        self.queue = queue
        self.render_height = max(1, render_height)
        self.transient_start = 0

    @property
    def children(self) -> list[Component]:
        """Return current components in render order for compatibility."""
        children = [self.transcript]
        if self.work is not None:
            children.append(self.work)
        auxiliary = self._auxiliary()
        if auxiliary is not None and auxiliary is not self.details and auxiliary is not self.permission:
            children.append(auxiliary)
        children.extend((self.status, self.editor))
        if auxiliary is not None and (auxiliary is self.details or auxiliary is self.permission):
            children.append(auxiliary)
        if self.hints is not None:
            children.append(self.hints)
        return children

    def set_height(self, height: int) -> None:
        """Set the total row budget used by the transient tail."""
        self.render_height = max(1, height)

    def render(self, width: int) -> list[str]:
        if self.fullscreen_view is not None:
            self.transient_start = 0
            self._style_panel(self.fullscreen_view)
            self.fullscreen_view.set_height(self.render_height)
            return self.fullscreen_view.render(width)[:self.render_height]
        transcript_lines = self.transcript.render(width)
        self.transient_start = len(transcript_lines)

        inset = min(max(0, getattr(self.editor, "content_column", 0)), max(0, width - 1))
        content_width = max(0, width - inset)
        auxiliary = self._auxiliary()
        # On a one-row terminal the editor takes priority over metadata.
        show_status = self.render_height > (2 if self.permission is not None else 1)
        status_lines = self.status.render(content_width)[:1] if show_status else []
        work_lines = self.work.render(width) if self.work is not None else []

        # Status and editor stay visible. The auxiliary surface is bounded first,
        # then any remaining rows are offered to the work summary.
        hint_lines = self.hints.render(content_width)[:1] if self.hints is not None and self.render_height >= 10 else []
        minimum_editor_rows = 1
        auxiliary_budget = max(
            0,
            min(12, self.render_height - len(status_lines) - len(hint_lines) - minimum_editor_rows),
        )
        set_auxiliary_budget = getattr(auxiliary, "set_render_budget", None)
        if callable(set_auxiliary_budget):
            set_auxiliary_budget(auxiliary_budget)
        self._style_panel(auxiliary)
        raw_auxiliary = auxiliary.render(width) if auxiliary is not None else []
        auxiliary_lines = raw_auxiliary[-auxiliary_budget:] if auxiliary_budget else []
        editor_budget = max(
            minimum_editor_rows,
            self.render_height - len(status_lines) - len(auxiliary_lines) - len(hint_lines),
        )
        set_budget = getattr(self.editor, "set_render_budget", None)
        if callable(set_budget):
            set_budget(editor_budget, show_borders=editor_budget >= 3)
        show_autocomplete = getattr(self.editor, "set_autocomplete_visible", None)
        if callable(show_autocomplete):
            show_autocomplete(
                self.permission is None and not (self.details is not None and auxiliary is self.details)
            )
        editor_lines = self.editor.render(width)[-editor_budget:]

        fixed_rows = len(status_lines) + len(editor_lines) + len(auxiliary_lines) + len(hint_lines)
        work_budget = max(0, self.render_height - fixed_rows)
        bounded_work = work_lines[-work_budget:] if work_budget else []
        omitted = len(work_lines) - len(bounded_work)
        if omitted and status_lines:
            status_lines = [truncate_to_width(
                f"{omitted} work summaries hidden | " + status_lines[0], content_width, "…",
            )]
        # The editor supplies geometry; the layout knows no host/prompt policy.
        status_lines = [" " * inset + line for line in status_lines]
        hint_lines = [" " * inset + line for line in hint_lines]
        # Decorative spacing uses spare rows only; never displace input or panels.
        before_status = bounded_work or transcript_lines
        if auxiliary is not self.details and auxiliary is not self.permission:
            before_status = auxiliary_lines or before_status
        status_gap = [""] if (
            before_status and strip_ansi(before_status[-1]).strip() and status_lines
            and fixed_rows + len(bounded_work) < self.render_height
        ) else []
        if auxiliary is self.details or auxiliary is self.permission:
            return (
                transcript_lines + bounded_work + status_gap + status_lines
                + editor_lines + auxiliary_lines + hint_lines
            )
        return transcript_lines + bounded_work + auxiliary_lines + status_gap + status_lines + editor_lines + hint_lines

    def _style_panel(self, component: Component | None) -> None:
        setter = getattr(component, 'set_panel_theme', None)
        if callable(setter):
            setter(self.panel_theme)

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()

    def _auxiliary(self) -> Component | None:
        if self.permission is not None:
            return self.permission
        if self.details is not None and not getattr(self.editor, "focused", True):
            return self.details  # Background completions cannot hide the focused dialog.
        if getattr(self.editor, "autocomplete_active", False):
            return None  # Suggestions occupy the editor's auxiliary rows.
        return self.details or self.queue
