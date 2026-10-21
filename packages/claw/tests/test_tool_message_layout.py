"""Claw integration of shared inline Markdown and compact tool cards."""

from xdog.ai.types import ImageContent
from xdog.claw.channels.tui.tui_app import AssistantMessage, ToolMessage
from xdog.tui.components.image import Image
from xdog.tui.utils import strip_ansi, visible_width


def rows(component, width=100):
    return [strip_ansi(row).rstrip() for row in component.render(width)]


def test_claw_inline_list_flows_in_the_shared_markdown():
    message = AssistantMessage('- before `code` **after**')
    assert [row for row in rows(message) if row.strip()] == [' • - before code after']


def test_claw_compact_success_and_full_arguments_result_expansion():
    tool = ToolMessage('filesystem', {'action': 'read', 'path': 'config.py', 'extra': 'FULL-ARG'})
    output = 'first\n' + 'x' * 600 + '\nFULL-TAIL'
    tool.set_result(output)
    assert rows(tool)[:2] == [' • Ran filesystem read config.py', '   └ first · 2 hidden lines; Ctrl+O']
    assert tool.detail_body == output
    assert 'FULL-ARG' in tool.transcript_body
    tool.set_expanded(True)
    expanded = '\n'.join(rows(tool))
    assert 'FULL-ARG' in expanded and 'FULL-TAIL' in expanded


def test_claw_running_cancel_and_error_keep_real_state_and_stream_tail():
    for terminal in ('cancel', 'error'):
        tool = ToolMessage('bash', {'command': 'pytest -q'})
        tool.set_streaming('old\nLATEST')
        assert rows(tool)[:2] == [' • Running pytest -q', '   └ LATEST · 1 hidden lines; Ctrl+O']
        if terminal == 'cancel':
            tool.set_canceled()
            tool.set_result('late success')
            assert rows(tool)[0] == ' • Cancelled pytest -q'
            assert tool.detail_body == 'old\nLATEST'
        else:
            tool.set_result('ERROR\nfull detail', is_error=True)
            assert rows(tool)[0] == ' • Failed pytest -q'
            assert 'ERROR' in rows(tool)[1]
        before = tool.detail_body
        tool.set_streaming('late delta')
        assert tool.detail_body == before
        for width in range(31):
            assert all(visible_width(row) <= width for row in tool.render(width))


def test_claw_tool_keeps_image_rendering_separate_from_text(monkeypatch):
    tool = ToolMessage('image', {})
    tool.set_image(ImageContent(data='aW1hZ2U=', mime_type='image/png'))
    tool.set_image(ImageContent(data='not base64!', mime_type='image/png'))
    assert len([child for child in tool.children if isinstance(child, Image)]) == 1
    sentinel = ['', '', '\x1b_Ga=T;payload\x1b\\']
    image_calls = []

    def render_image(self, width):
        image_calls.append(width)
        return sentinel

    monkeypatch.setattr(Image, 'render', render_image)
    for _ in range(3):
        assert tool.render(80)[2:-1] == sentinel
    tool.invalidate()
    assert tool.render(80)[2:-1] == sentinel
    tool.set_expanded(True)
    for _ in range(3):
        assert sentinel[-1] in tool.render(80)
    assert image_calls == [80] * 7


def test_claw_tool_marker_alignment_matches_prose_when_expanded_or_compact():
    for expanded in (False, True):
        tool = ToolMessage('bash', {'command': 'pwd'})
        tool.set_result('/workspace')
        tool.set_expanded(expanded)
        assert next(row for row in rows(tool) if row.strip()).startswith(' • Ran pwd')



def test_claw_unchanged_tool_frames_skip_header_summary_and_text_layout(monkeypatch):
    import xdog.tui.components.tool_message as shared

    counts = dict.fromkeys(('make_header', 'summarize_args', 'sanitize_terminal_text',
                            'truncate_to_width', 'grapheme_clusters'), 0)
    for name in counts:
        owner = shared.ToolMessage if name in ('make_header', 'summarize_args') else shared
        original = getattr(owner, name)

        def counted(*args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(owner, name, staticmethod(counted) if owner is shared.ToolMessage else counted)

    tool = ToolMessage('bash', {'command': 'pwd'})
    tool.set_streaming('first\n' + '界e\u0301' * 100)
    expected = rows(tool, 80)
    baseline = counts.copy()
    for _ in range(10):
        assert rows(tool, 80) == expected
    assert counts == baseline
    rows(tool, 40)
    assert counts['truncate_to_width'] > baseline['truncate_to_width']
    assert counts['sanitize_terminal_text'] == baseline['sanitize_terminal_text']
    assert counts['make_header'] == baseline['make_header']
    assert counts['summarize_args'] == baseline['summarize_args']
    tool.set_streaming('first\nCHANGED')
    assert 'CHANGED' in rows(tool, 80)[1]
    tool.set_result('FAILED\nfull detail', is_error=True)
    assert rows(tool, 80)[0] == ' • Failed pwd'
    assert 'FAILED' in rows(tool, 80)[1]
    tool.set_streaming('late delta')
    assert tool.detail_body == 'FAILED\nfull detail'
    tool.set_expanded(True)
    assert 'full detail' in '\n'.join(rows(tool, 80))
    tool.set_expanded(False)
    before = counts.copy()
    tool.invalidate()
    rows(tool, 80)
    assert counts['make_header'] > before['make_header']
    assert counts['truncate_to_width'] > before['truncate_to_width']
    # invalidate refreshes the sanitized header, not the retained full output.
    assert counts['sanitize_terminal_text'] == before['sanitize_terminal_text'] + 1


def test_claw_cached_tool_header_refreshes_for_ascii_and_theme(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm-256color')
    monkeypatch.delenv('XDOG_TUI_ASCII', raising=False)
    tool = ToolMessage('bash', {'command': 'pwd'})
    assert rows(tool)[0] == ' • Running pwd'
    monkeypatch.setenv('XDOG_TUI_ASCII', '1')
    assert rows(tool)[0] == ' * Running pwd'
    assert '->' in rows(tool)[1]
    tool.set_expanded(True)
    monkeypatch.delenv('XDOG_TUI_ASCII')
    assert next(row for row in rows(tool) if row.strip()) == ' • Running pwd'
    tool.set_expanded(False)
    tool.set_result('done')
    import xdog.claw.channels.tui.tui_app as host

    monkeypatch.setattr(host, 'theme_accent', lambda text: 'THEME ' + text)
    monkeypatch.setattr(host, 'theme_dim', lambda text: 'DIM ' + text)
    tool.invalidate()
    assert 'THEME' in rows(tool)[0]
    assert 'DIM' in rows(tool)[1]
