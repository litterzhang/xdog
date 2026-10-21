"""Native multiline command output keeps the same gutter on every wrapped line."""
import pytest
from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.utils import strip_ansi, visible_width


@pytest.mark.parametrize('width', [12, 24, 80, 160])
def test_system_command_lines_and_wrapped_continuations_share_body_gutter(width):
    log = ChatLog(create_default_theme())
    log.add_system('Goal: 无人值守测试\nStatus: waiting | wait_for: user\n'
                   'Reason: ' + '等待用户确认委派配置 ' * 12 + '\nNext: waiting for user input.')
    lines = [strip_ansi(line).rstrip() for line in log.render(width)]
    nonempty = [line for line in lines if line.strip()]
    assert len(nonempty) >= 4
    assert all(line.startswith('   ') and not line.startswith('    ') for line in nonempty)
    assert all(visible_width(line) <= width for line in lines)
    full = ''.join(line.strip() for line in nonempty)
    assert 'Goal:' in full and 'Status:' in full and 'Reason:' in full and 'Next:' in full


def test_system_command_padding_preserves_intentional_nested_indentation_and_single_line():
    log = ChatLog(create_default_theme())
    log.add_system('Goal: test\n  [blocked] task\n\nNext: wait')
    lines = [strip_ansi(line).rstrip() for line in log.render(80)]
    assert lines == ['', '   Goal: test', '     [blocked] task', '', '   Next: wait', '']
    log = ChatLog(create_default_theme())
    log.add_system('Ready')
    assert [strip_ansi(line).rstrip() for line in log.render(80)] == ['', '   Ready', '']


def test_system_command_still_sanitizes_terminal_sequences():
    log = ChatLog(create_default_theme())
    log.add_system('Goal: test\nStatus: waiting\x1b[2J\x1b]52;c;unsafe\x07')
    text = '\n'.join(strip_ansi(line) for line in log.render(80))
    assert 'Goal: test' in text and 'Status: waiting' in text
    assert '\x1b' not in text
