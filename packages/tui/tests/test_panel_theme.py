"""Panel color is host-provided presentation, never navigation or review authority."""
from __future__ import annotations

import time

import pytest
from xdog.tui.components.details_panel import DetailRecord, DetailsPanel
from xdog.tui.components.inline_layout import InlineLayout
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.components.navigation_panel import NavigationPanel
from xdog.tui.components.panel import PanelTheme
from xdog.tui.components.text import Text
from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.navigation import NavigationEntry, NavigationFeed, NavigationSnapshot
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.tui import CURSOR_MARKER
from xdog.tui.utils import strip_ansi, visible_width


def color(code):
    return lambda text: f'\x1b[{code}m{text}\x1b[0m'


def theme():
    return PanelTheme(fg=color(37), header=color(33), accent=color(36), dim=color(90), border=color(90),
                      error=color(31), success=color(32), bold=color(1), inverse=color(7))


def navigation():
    feed = NavigationFeed()
    panel = NavigationPanel(feed, lambda entry: None, lambda: None)
    feed.publish(feed.snapshot().serial, NavigationSnapshot(total=2, entries=(
        NavigationEntry('one', 'first 宽 🐕', active=True), NavigationEntry('two', 'second'),
    )))
    panel.poll()
    return panel


@pytest.mark.parametrize('width', [0, 1, 8, 40, 80])
def test_navigation_styles_preserve_cells_rows_and_plain_mode(width):
    panel = navigation()
    plain = panel.render(width)
    panel.set_panel_theme(theme())
    colored = panel.render(width)
    assert [strip_ansi(row) for row in colored] == plain
    assert len(colored) == len(plain) and all(visible_width(row) <= width for row in colored)
    if plain[0] and plain[1]:
        assert '\x1b[33m' in colored[0] and '\x1b[36m' in colored[1]
    panel.set_panel_theme(PanelTheme())
    assert panel.render(width) == plain


def test_layout_applies_the_same_theme_to_late_inline_and_fullscreen_panels():
    style = theme()
    editor = InputPanel(PanelTheme())
    layout = InlineLayout(Text('history'), Text('status'), editor, panel_theme=style)
    panel = navigation()
    layout.details = panel
    editor.focused = False
    rows = layout.render(80)
    assert panel.panel_theme is style and any('\x1b[33m' in row for row in rows)
    fullscreen = DetailsPanel(lambda: [DetailRecord('tool details', 'body')])
    layout.fullscreen_view = fullscreen
    rows = layout.render(80)
    assert fullscreen.panel_theme is style and '\x1b[33m' in rows[0]


def test_nested_mcp_form_uses_host_theme_without_changing_input_or_visibility_gate():
    panel = McpPanel(lambda *args: None, lambda: None, lambda: None, lambda: None)
    panel._mode = 'arguments'
    panel._arguments.set_text('literal 宽 🐕')
    panel.focused = True
    panel.set_panel_theme(theme())
    rows = panel.render(80)
    assert '\x1b[33m' in rows[0] and '\x1b[36m' in ''.join(rows)
    assert panel._arguments.get_text() == 'literal 宽 🐕'
    assert CURSOR_MARKER in ''.join(rows)
    panel.set_panel_theme(PanelTheme())
    assert '\x1b[' not in ''.join(panel.render(80))


def test_transcript_theme_does_not_leak_into_source_and_survives_inline_fullscreen():
    record = TranscriptRecord('one', 'assistant', 'Response', 'first 宽\nsecond')
    panel = TranscriptPanel(lambda: (record,), lambda: None, lambda: None)
    try:
        for fullscreen in (False, True):
            panel.fullscreen = fullscreen
            panel.set_render_budget(6)
            panel.set_panel_theme(theme())
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                panel.render(40)
                panel.poll()
                if panel.settled:
                    break
                time.sleep(0.002)
            rows = panel.render(40)
            assert '\x1b[33m' in rows[0] and '\x1b[90m' in rows[-1]
            assert any('\x1b[36m' in row for row in rows[1:-1])
            assert all(visible_width(row) <= 40 for row in rows)
            assert panel.records == (record,)
            panel.set_panel_theme(PanelTheme())
            assert '\x1b[' not in ''.join(panel.render(40))
    finally:
        panel.close()


def test_semantic_statuses_use_host_colors_not_a_new_palette():
    style = theme()
    assert '\x1b[32m' in style.status('completed', 'completed')
    assert '\x1b[31m' in style.status('failed', 'failed')
    assert '\x1b[36m' in style.status('running', 'running')
    assert '\x1b[90m' in style.status('unknown', 'unknown')


def test_mcp_untrusted_escapes_are_removed_before_host_colors_are_applied():
    panel = McpPanel(lambda *args: None, lambda: None, lambda: None, lambda: None)
    panel._rows = ({'server': '\x1b[2Jremote\nserver', 'state': 'connected\x1b[3J'},)
    panel._message = '\x1b[2Jmessage\twith-tab\nnext-line'
    panel.set_panel_theme(theme())
    rendered = ''.join(panel.render(80))
    assert '\x1b[2J' not in rendered and '\x1b[3J' not in rendered
    assert '\n' not in rendered and '\t' not in rendered
    assert '\x1b[33m' in rendered and 'remote server' in strip_ansi(rendered)


@pytest.mark.parametrize('width', [1, 4, 9])
def test_truncating_nested_colors_closes_style_on_native_foreground(width):
    style = PanelTheme(success=color(32))  # Native foreground itself emits no reset.
    rows = style.rows([style.success('connected') + ' metadata'], width,
                      header=False, footer=False, selection=False)
    assert visible_width(rows[0]) <= width
    assert rows[0].endswith('\x1b[0m'), 'Cropping must not leave an inner style open'
