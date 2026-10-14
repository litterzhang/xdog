from __future__ import annotations

import pytest
from xdog.tui.command_help import command_help


def test_help_shows_complete_normalized_sorted_menu_and_filters_names_or_descriptions():
    commands = {'zebra': 'Last command', '/help': 'Search terminal commands', 'inspect': 'Readonly record lookup'}
    text = command_help(commands)
    assert '3/3 matches' in text
    assert text.index('/help') < text.index('/inspect') < text.index('/zebra')
    assert command_help(commands, 'LOOKUP').endswith('/inspect — Readonly record lookup')
    assert '0/3 matches' in command_help(commands, 'missing')
    assert 'never runs commands or grants approval' in text
    assert commands['inspect'] == 'Readonly record lookup'


def test_help_does_not_execute_metadata_sanitize_control_bearing_descriptions_or_truncate_long_rows():
    description = 'Plain\x1b[2J\x1b]52;c;unsafe\a\n/quit ' + 'tail-' * 600
    text = command_help({'owned': description})
    assert '\x1b' not in text and text.endswith('tail-' * 600)
    assert 'Plain /quit' in text  # Inert flattened description, never a submitted line.


def test_invalid_help_data_is_unavailable_not_partial_or_silently_reinterpreted() -> None:

    def _check_case(commands, query):
        with pytest.raises(ValueError):
            command_help(commands, query)

    for commands, query in [
        ({"bad name": "bad"}, ""),
        ({"bad\u202ename": "bad"}, ""),
        ({"bad\x1b": "bad"}, ""),
        ({"": "bad"}, ""),
        ({"name": False}, ""),
        ({"x" * 257: "name"}, ""),
        ({"name": "x" * 8193}, ""),
        ({"name": "normal"}, "x" * 257),
        ({"name": "normal"}, "\x1bunsafe"),
        ({"name": "normal"}, "\u2066query"),
        ({"name": "normal"}, "multiline\nquery"),
        ({"same": "one", "/same": "two"}, ""),
    ]:
        _check_case(commands, query)


def test_help_retains_every_match_and_refuses_excessive_metadata_without_partial_catalog():
    commands = {f'item{index:04}': f'Description {index}' for index in range(300)}
    text = command_help(commands)
    assert '300/300 matches' in text and '/item0299' in text
    with pytest.raises(ValueError, match='capacity'):
        command_help({str(index): 'description' for index in range(10001)})
    with pytest.raises(ValueError, match='byte capacity'):
        command_help({str(index): 'x' * 8192 for index in range(129)})
