"""Real layout workers exercise composed frames through actual parent/child RPC owners."""
from __future__ import annotations

from xdog.tui.utils import visible_width


def test_custom_rpc_sanitizer_preserves_grapheme_width_without_terminal_control_authority():
    from xdog.coding.modes.rpc.terminal_data import plain_custom_line
    from xdog.tui.utils import strip_ansi

    raw = '\x1b[36m界👩\u200d💻e\u0301\x1b[0m\x1b]52;c;private\a\x1b[2J\x1b_pi:c\a'
    clean = plain_custom_line(raw)
    assert clean == '界👩\u200d💻e\u0301'
    assert visible_width(clean) == visible_width(strip_ansi('界👩\u200d💻e\u0301')) == 5
    assert '\x1b' not in plain_custom_line('\x1bPprivate\x1b\\safe')
    assert plain_custom_line('a\t界\u202eb') == 'a   界b'
