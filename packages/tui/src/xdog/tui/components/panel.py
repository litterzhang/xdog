"""Host-supplied semantic panel styles; no palette or application policy."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from xdog.tui.tui import Component
from xdog.tui.utils import strip_ansi, truncate_to_width

Style = Callable[[str], str]


def _plain(text: str) -> str:
    return text


@dataclass(frozen=True)
class PanelTheme:
    """The same callable colors used by the host's main UI; plain by default."""

    fg: Style = _plain
    header: Style = _plain
    accent: Style = _plain
    dim: Style = _plain
    border: Style = _plain
    error: Style = _plain
    success: Style = _plain
    bold: Style = _plain
    inverse: Style = _plain

    def selected(self, text: str) -> str:
        return self.bold(self.accent(text))

    def status(self, text: str, state: str) -> str:
        if state in {'completed', 'succeeded', 'success', 'delivered', 'connected'}:
            return self.success(text)
        if state in {'failed', 'error', 'interrupted', 'unknown-send'}:
            return self.error(text)
        if state in {'running', 'working', 'pending', 'queued', 'connecting', 'waiting', 'submitted'}:
            return self.accent(text)
        return self.dim(text)

    def rows(self, rows: Sequence[str], width: int, *, header: bool = True,
             footer: bool = True, selection: bool = True, ellipsis: str = '') -> list[str]:
        result: list[str] = []
        for index, row in enumerate(rows):
            # Truncate before styling so a narrow terminal cannot lose a reset.
            text = truncate_to_width(row, max(0, width), ellipsis)
            if not text:
                result.append(text)
                continue
            if header and index == 0:
                style = self.header
            elif footer and index == len(rows) - 1:
                style = self.dim
            elif selection and strip_ansi(text).lstrip().startswith(('▶ ', '→ ')):
                style = self.selected
            else:
                style = self.fg
            painted = style(text)
            if '\x1b[' in painted and not painted.endswith('\x1b[0m'):
                painted += '\x1b[0m'  # Cropping may have removed a nested style's reset.
            result.append(painted)
        return result


class ThemedPanel(Component):
    """Shared rendering helpers; colors never alter row budgets or input gates."""

    panel_theme = PanelTheme()

    def set_panel_theme(self, theme: PanelTheme) -> None:
        self.panel_theme = theme

    def _render_child(self, component: Component, width: int) -> list[str]:
        setter = getattr(component, 'set_panel_theme', None)
        if callable(setter):
            setter(self.panel_theme)
        return component.render(width)

    def _styled_rows(self, rows: Sequence[str], width: int, *, header: bool = True,
                     footer: bool = True, selection: bool = True) -> list[str]:
        return self.panel_theme.rows(rows, width, header=header, footer=footer, selection=selection)
