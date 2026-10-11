"""Complete command intent must not be replaced by a neighboring autocomplete action."""
from dataclasses import dataclass

import pytest
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent


@dataclass(frozen=True)
class Theme:
    accent = staticmethod(lambda value: value)
    bold = staticmethod(lambda value: value)
    dim = staticmethod(lambda value: value)
    border = staticmethod(lambda value: value)


def editor(commands):
    panel = InputPanel(Theme(), command_provider=lambda: commands)
    panel.focused = True
    submitted = []
    panel.on_submit = submitted.append
    return panel, submitted


@pytest.mark.parametrize('text', ['/session', '/SESSION', '/Session'])
@pytest.mark.parametrize('rows', [1, 3, 8])
def test_exact_enter_uses_typed_command_even_with_longer_visible_or_hidden_suggestion(text, rows):
    panel, submitted = editor({'session': 'Session info', 'sessions': 'Activate browser'})
    panel.set_render_budget(rows)
    panel.set_text(text)
    assert panel._select_list.selected_value == '/sessions'
    panel.render(40)
    panel.handle_input(KeyEvent('enter'))
    assert submitted == [text] and not panel.get_text()


def test_explicit_arrow_or_tab_can_choose_neighbor_without_breaking_exact_submit():
    panel, submitted = editor({'session': 'Session info', 'sessions': 'Browse'})
    panel.set_text('/session')
    panel.handle_input(KeyEvent('down'))
    panel.handle_input(KeyEvent('enter'))
    assert submitted == ['/sessions']
    panel.set_text('/session')
    panel.handle_input(KeyEvent('tab'))
    assert panel.get_text() == '/sessions' and submitted == ['/sessions']
    panel.handle_input(KeyEvent('enter'))
    assert submitted == ['/sessions', '/sessions']


def test_edit_history_undo_and_new_catalog_clear_neighbor_selection_intent():
    commands = {'session': 'Session info', 'sessions': 'Browse'}
    panel, submitted = editor(commands)
    panel.set_text('/session')
    panel.handle_input(KeyEvent('down'))
    panel.handle_input(KeyEvent('backspace'))
    panel.handle_input(KeyEvent('n'))
    panel.handle_input(KeyEvent('enter'))
    assert submitted == ['/session']
    panel.add_to_history('/session')
    panel.handle_input(KeyEvent('up'))
    panel.handle_input(KeyEvent('enter'))
    assert submitted == ['/session', '/session']
    panel.set_text('/session')
    panel.handle_input(KeyEvent('down'))
    commands['session-new'] = 'Changed catalog'
    panel.poll_autocomplete()
    panel.handle_input(KeyEvent('enter'))
    assert submitted[-1] == '/session'


def test_withdrawn_exact_command_never_turns_into_neighbor_or_stale_selection():
    commands = {'ext-demo-show': 'Show', 'ext-demo-shows': 'Other action'}
    panel, submitted = editor(commands)
    panel.set_text('/ext-demo-show')
    del commands['ext-demo-show']
    panel.poll_autocomplete()
    panel.handle_input(KeyEvent('enter'))
    assert submitted == ['/ext-demo-show']  # Dispatcher reports unavailable; no reinterpretation.
    panel.set_text('/ext-demo-sh')
    assert panel._select_list.selected_value == '/ext-demo-shows'
    del commands['ext-demo-shows']
    panel.handle_input(KeyEvent('enter'))
    assert submitted == ['/ext-demo-show'] and panel.get_text() == '/ext-demo-sh'


def test_prefix_and_argument_submit_contracts_retained():
    panel, submitted = editor({'session': 'Info', '/sessions': 'Browse'})
    panel.set_text('/sess')
    panel.handle_input(KeyEvent('enter'))
    assert submitted == ['/session']
    panel.set_text('/session explicit args')
    panel.handle_input(KeyEvent('enter'))
    assert submitted[-1] == '/session explicit args'
