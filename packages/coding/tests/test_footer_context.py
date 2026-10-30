"""Tests for the interactive-mode footer context accounting."""

from xdog.ai.types import AssistantMessage, Usage, UserMessage
from xdog.coding.modes.interactive.interactive_mode import _context_tokens_from_messages


def test_context_tokens_uses_latest_assistant_usage():
    messages = [
        AssistantMessage(usage=Usage(input=10, output=2, cache_read=5, cache_write=1)),
        UserMessage(content="next"),
        AssistantMessage(usage=Usage(input=100, output=20, cache_read=900, cache_write=50)),
    ]

    assert _context_tokens_from_messages(messages) == 1050


def test_context_tokens_zero_without_assistant_usage():
    assert _context_tokens_from_messages([UserMessage(content="hi")]) == 0


def test_interactive_footer_uses_usable_prompt_limit():
    from unittest.mock import MagicMock

    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    mode = object.__new__(InteractiveMode)
    mode._session = MagicMock()
    mode._session.model = "gpt-5.6-sol"
    mode._session.agent.options.thinking = "high"
    mode._session.permissions.mode = "ask"
    mode._session.session_id = "session-id"
    mode._session.messages = []
    mode._session.working_dir = "/workspace/project"
    mode._session.context_window = 1_050_000
    mode._session.context_limit = 922_000
    mode._footer = MagicMock()

    mode._update_footer()

    assert mode._footer.update.call_args.kwargs["max_context"] == 922_000


def test_footer_displays_provider_model_and_thinking_without_changing_context():
    from xdog.coding.modes.interactive.components.footer import FooterComponent
    from xdog.coding.modes.interactive.theme import create_default_theme
    from xdog.tui.utils import strip_ansi, visible_width

    footer = FooterComponent(create_default_theme())
    footer.update(model='copilot/gpt-6.1-sol', thinking='medium', context_tokens=250, max_context=1000)
    label = 'copilot/gpt-6.1-sol - medium'
    assert label in strip_ansi(footer.render(100)[0])
    assert label in footer.describe()
    assert 'thinking:' not in footer.describe()
    footer.update(model='antigravity/vendor/model', thinking='off', context_tokens=250, max_context=1000)
    assert 'antigravity/vendor/model - off' in strip_ansi(footer.render(100)[0])
    footer.update(model='antigravity/vendor/model', thinking='unknown', context_tokens=250, max_context=1000)
    assert footer._model == 'antigravity/vendor/model'
    assert ' - unknown' not in footer.describe()
    footer.set_queue_count(2)
    narrow = strip_ansi(footer.render(42)[0])
    assert 'ctx:250/1.0k' in narrow and 'queued 2' in narrow
    assert visible_width(footer.render(42)[0]) <= 42


def test_coding_startup_header_uses_full_model_and_actual_thinking():
    from unittest.mock import MagicMock

    from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

    mode = object.__new__(InteractiveMode)
    mode._session = MagicMock()
    mode._session.model = 'copilot/gpt-6.1-sol'
    mode._session.agent.options.thinking = 'high'
    mode._theme = MagicMock()
    mode._theme.header.side_effect = lambda value: value
    mode._header = MagicMock()
    mode._chat_log = MagicMock()
    mode._banner_added = False
    mode._update_header()
    assert mode._header.set_text.call_args.args[0] == '  coding | copilot/gpt-6.1-sol - high'
    mode._update_header()
    assert mode._chat_log.add_child.call_count == 1
