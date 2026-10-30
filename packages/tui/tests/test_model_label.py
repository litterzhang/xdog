"""Model labels are host-supplied identity plus reasoning, not provider discovery."""
import pytest
from xdog.tui.components.status_line import format_model_label


@pytest.mark.parametrize('level', ['off', 'minimal', 'low', 'medium', 'high', 'xhigh'])
@pytest.mark.parametrize('model', ['copilot/gpt-6.1-sol', 'antigravity/vendor/model', 'ext-private/custom'])
def test_model_label_preserves_full_identity_and_level(model, level):
    assert format_model_label(model, level) == f'{model} - {level}'


def test_model_label_does_not_invent_unknown_provider_or_reasoning():
    assert format_model_label('custom-model', 'off') == 'custom-model - off'
    assert format_model_label('', None) == 'unknown'


def test_model_label_sanitizes_terminal_controls_and_stays_on_one_line():
    assert format_model_label('\x1b[31mcopilot/model\n', '\x1b[32mhigh\n') == 'copilot/model - high'


@pytest.mark.parametrize('level', [None, '', 'unknown', ' UNKNOWN ', '\x1b[31munknown\n'])
def test_model_label_hides_unknown_reasoning_without_a_separator(level):
    assert format_model_label('copilot/gpt-6.1-sol', level) == 'copilot/gpt-6.1-sol'
