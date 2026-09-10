"""Compact main-buffer composition for interactive terminal applications."""

from __future__ import annotations

from xdog.tui.components.text import Text
from xdog.tui.tui import Component
from xdog.tui.utils import truncate_to_width


class CompactText(Text):
    """A short summary whose logical lines never wrap into extra rows."""

    def render(self, width: int) -> list[str]:
        return [truncate_to_width(line, max(0, width), "…") for line in self.text.splitlines()]


class InlineLayout(Component):
    """Compose durable transcript rows with a terminal-height-bounded tail.

    The transient tail contains, in order, an optional work summary, exactly one
    status row, the editor, and at most one auxiliary surface. Auxiliary priority
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
    ) -> None:
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
        children.extend((self.status, self.editor))
        auxiliary = self._auxiliary()
        if auxiliary is not None:
            children.append(auxiliary)
        return children

    def set_height(self, height: int) -> None:
        """Set the total row budget used by the transient tail."""
        self.render_height = max(1, height)

    def render(self, width: int) -> list[str]:
        transcript_lines = self.transcript.render(width)
        self.transient_start = len(transcript_lines)

        auxiliary = self._auxiliary()
        # On a one-row terminal the editor takes priority over metadata.
        show_status = self.render_height > (2 if self.permission is not None else 1)
        status_lines = self.status.render(width)[:1] if show_status else []
        work_lines = self.work.render(width) if self.work is not None else []

        # Status and editor stay visible. The auxiliary surface is bounded first,
        # then any remaining rows are offered to the work summary.
        minimum_editor_rows = 1
        auxiliary_budget = max(
            0,
            self.render_height - len(status_lines) - minimum_editor_rows,
        )
        set_auxiliary_budget = getattr(auxiliary, "set_render_budget", None)
        if callable(set_auxiliary_budget):
            set_auxiliary_budget(auxiliary_budget)
        raw_auxiliary = auxiliary.render(width) if auxiliary is not None else []
        auxiliary_lines = raw_auxiliary[-auxiliary_budget:] if auxiliary_budget else []
        editor_budget = max(
            minimum_editor_rows,
            self.render_height - len(status_lines) - len(auxiliary_lines),
        )
        set_budget = getattr(self.editor, "set_render_budget", None)
        if callable(set_budget):
            set_budget(editor_budget, show_borders=editor_budget >= 3)
        show_autocomplete = getattr(self.editor, "set_autocomplete_visible", None)
        if callable(show_autocomplete):
            show_autocomplete(self.permission is None)
        editor_lines = self.editor.render(width)[-editor_budget:]

        fixed_rows = len(status_lines) + len(editor_lines) + len(auxiliary_lines)
        work_budget = max(0, self.render_height - fixed_rows)
        bounded_work = work_lines[-work_budget:] if work_budget else []
        omitted = len(work_lines) - len(bounded_work)
        if omitted and status_lines:
            status_lines = [truncate_to_width(
                f"{omitted} work summaries hidden | " + status_lines[0], width, "…",
            )]
        return transcript_lines + bounded_work + status_lines + editor_lines + auxiliary_lines

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()

    def _auxiliary(self) -> Component | None:
        if self.permission is not None:
            return self.permission
        if getattr(self.editor, "autocomplete_active", False):
            return None  # Suggestions occupy the editor's auxiliary rows.
        return self.details or self.queue
