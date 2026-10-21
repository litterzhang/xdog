"""Compact tool presentation without changing execution or retained output."""

import pytest
from xdog.tui.components.tool_message import ToolMessage
from xdog.tui.utils import strip_ansi, visible_width


@pytest.mark.parametrize(('name', 'args', 'expected'), [
    ('bash', {'command': 'uv run pytest -q'}, 'uv run pytest -q'),
    ('bash', {'command': "python - <<'PY'\nprint('hi')\nPY"}, 'Python script'),
    ('filesystem', {'action': 'read', 'path': 'config.py', 'content': 'not a summary'}, 'read config.py'),
    ('goal', {'action': 'report', 'content': 'not a summary'}, 'report'),
    ('unknown', {'content': 'not a summary'}, ''),
])
def test_shared_action_target_summary(name, args, expected):
    assert ToolMessage.summarize_args(args, name) == expected


def test_headers_are_concise_sanitized_and_keep_non_success_states(monkeypatch):
    monkeypatch.delenv('XDOG_TUI_ASCII', raising=False)
    monkeypatch.setenv('TERM', 'xterm-256color')
    for state, verb in [('running', 'Running'), ('success', 'Ran'), ('error', 'Failed'), ('canceled', 'Cancelled')]:
        header = ToolMessage.make_header('bash', 'pytest\n-q\x1b[2J', state)
        assert header == f'• {verb} pytest -q'
    assert ToolMessage.make_header('filesystem', 'read config.py', 'success') == '• Ran filesystem read config.py'
    assert 'password=hidden' not in ToolMessage.make_header('bash', 'echo password=hidden', 'running')
    monkeypatch.setenv('TERM', 'dumb')
    assert ToolMessage.make_header('bash', 'pwd', 'success') == '* Ran pwd'


def test_previews_are_bounded_full_results_retained_and_single_line_details_discoverable():
    tool = ToolMessage()
    output = 'first\x1b[2J\x1b]52;c;payload\x07\n' + '界' * 500 + '\nlatest'
    safe = 'first\n' + '界' * 500 + '\nlatest'
    for running, expected in [(True, 'latest'), (False, 'first')]:
        tool.update(header='• Running test', summary='', output=output, running=running, style=lambda text: text)
        assert expected in strip_ansi(tool.render(80)[1])
        assert '2 hidden lines' in strip_ansi(tool.render(80)[1])
        assert tool.detail_body == safe
        for width in range(81):
            rows = tool.render(width)
            assert len(rows) == 3
            assert all(visible_width(row) <= width for row in rows)
            assert all('\n' not in row and '\r' not in row for row in rows)
            assert all('\x1b' not in row for row in rows)
    tool.update(header='• Ran test', summary='', output='x' * 1000, running=False, style=lambda text: text)
    assert 'Ctrl+O' in tool.render(80)[1]
    assert tool.detail_body == 'x' * 1000


def test_waiting_and_empty_success_are_not_fabricated_results():
    tool = ToolMessage()
    for running, expected in [(True, '(waiting for output)'), (False, '(no output)')]:
        tool.update(header='test', summary='args', output='', running=running, style=lambda text: text)
        assert expected in tool.render(80)[1]
        assert tool.detail_body == ''


def test_preview_truncation_keeps_combining_graphemes_and_cell_bounds():
    tool = ToolMessage()
    output = '界' * 30 + 'e\u0301' * 100
    for running in (True, False):
        tool.update(header='tool', summary='', output=output, running=running, style=lambda text: text)
        for width in range(90):
            rows = tool.render(width)
            assert all(visible_width(row) <= width for row in rows)
            assert rows[1].count('e') == rows[1].count('\u0301')
        assert tool.detail_body == output


def test_unchanged_update_and_render_skip_sanitize_layout_and_graphemes(monkeypatch):
    import xdog.tui.components.tool_message as module

    counts = dict.fromkeys(('sanitize_terminal_text', 'string_width', 'truncate_to_width', 'grapheme_clusters'), 0)
    for name in counts:
        original = getattr(module, name)

        def counted(*args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, counted)

    tool = ToolMessage()
    def style(text):
        return text

    args = dict(header='tool', summary='args\x1b[2J', output='old\n' + '界e\u0301' * 100,
                running=True, style=style)
    tool.update(**args)
    first = tool.render(80)
    baseline = counts.copy()
    assert baseline['sanitize_terminal_text'] == 2
    assert all(baseline.values())
    for _ in range(10):
        tool.update(**args)
        assert tool.render(80) == first
    assert counts == baseline
    # A caller cannot corrupt the retained rows by editing a returned list.
    first.clear()
    assert len(tool.render(80)) == 3
    tool.render(40)
    assert counts['grapheme_clusters'] == baseline['grapheme_clusters'] + 1
    assert counts['sanitize_terminal_text'] == baseline['sanitize_terminal_text']
    # Only one layout is retained: returning to an old width does real work.
    tool.render(80)
    assert counts['grapheme_clusters'] == baseline['grapheme_clusters'] + 2
    tool.update(**(args | {'header': 'changed', 'running': False}))
    assert 'changed' in tool.render(80)[0]
    assert 'old' in tool.render(80)[1]
    assert counts['sanitize_terminal_text'] == baseline['sanitize_terminal_text']
    tool.update(**(args | {'output': 'new\x1b[2J'}))
    assert tool.detail_body == 'new'
    assert 'new' in tool.render(80)[1]
    assert counts['sanitize_terminal_text'] == baseline['sanitize_terminal_text'] + 1


def test_render_cache_tracks_mutable_fields_style_invalidate_and_ascii(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm-256color')
    monkeypatch.delenv('XDOG_TUI_ASCII', raising=False)
    tool = ToolMessage()
    calls = []
    palette = ['A']

    def style(text):
        calls.append(text)
        return palette[0] + text

    tool.update(header='header', summary='', output='first\nlast', running=True, style=style)
    original = tool.render(80)
    assert tool.render(80) == original
    assert len(calls) == 1
    tool.header = 'direct header'
    assert 'direct header' in tool.render(80)[0]
    tool.output = 'direct first\ndirect last'
    assert 'direct last' in tool.render(80)[1]
    tool.running = False
    assert 'direct first' in tool.render(80)[1]
    tool.style = lambda text: 'B' + text
    assert tool.render(80)[1].startswith('B')
    tool.style = style
    tool.render(80)
    palette[0] = 'C'
    before = len(calls)
    tool.invalidate()
    assert tool.render(80)[1].startswith('C')
    assert len(calls) == before + 1
    monkeypatch.setenv('XDOG_TUI_ASCII', '1')
    assert '->' in tool.render(80)[1]
    monkeypatch.delenv('XDOG_TUI_ASCII')
    monkeypatch.setenv('TERM', 'dumb')
    assert '->' in tool.render(80)[1]
    monkeypatch.setenv('TERM', 'xterm-256color')
    assert '└' in tool.render(80)[1]


def test_output_line_summary_is_retained_across_widths_but_not_updates():
    calls = []

    class CountedOutput(str):
        def splitlines(self, *args, **kwargs):
            calls.append(self)
            return super().splitlines(*args, **kwargs)

    tool = ToolMessage()
    tool.output = CountedOutput('first\nlast')
    tool.render(80)
    tool.render(40)
    tool.render(80)
    assert len(calls) == 1
    tool.running = False
    assert 'first' in tool.render(80)[1]
    assert len(calls) == 2
    tool.output = CountedOutput('updated\ntail')
    assert 'updated' in tool.render(80)[1]
    assert len(calls) == 3
    tool.invalidate()
    tool.render(80)
    assert len(calls) == 4
