"""Child-owned prompt/skill drafts and full review, with no context inheritance UI."""
from __future__ import annotations

import json

from xdog.tui.child_browser import child_entries
from xdog.tui.components.child_panel import ChildPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi


def make():
    sent, started = [], []
    def configure(value):
        if not isinstance(value, dict):
            raise ValueError('Expected object')
        return {**value, 'inherit_context': False}
    panel = ChildPanel(lambda *args: sent.append(args), lambda child: None, lambda: None,
                       lambda *args: None, lambda: None, configure=configure,
                       configured_start=lambda *args: started.append(args))
    panel.focused = True
    panel.publish(child_entries({'children': [dict(id='0123456789ab', task='work', status='completed',
        access='read-only', calls=1, tokens=1, revision=1)]}))
    return panel, sent, started


def painted(panel):
    return '\n'.join(strip_ansi(row) for row in panel.render(100))


def confirm_all(panel):
    for _ in range(80):
        panel.render(100)
        panel.handle_input(KeyEvent('pagedown'))
    panel.render(100)
    panel.handle_input(KeyEvent('enter'))


def test_independent_system_prompt_and_empty_skill_selection_keep_task_and_access():
    panel, sent, started = make()
    try:
        panel._form('start')
        task = 'TASK knowledge is not SYSTEM\nexact continuation'
        panel._input.set_text(task)
        panel.handle_input(KeyEvent('tab'))
        panel.handle_input(KeyEvent('y', ctrl=True))
        assert panel._mode == 'system-prompt'
        assert 'task stays first USER message' in painted(panel)
        panel._input.set_text('CHILD system prompt\nrole only')
        panel.handle_input(KeyEvent('enter'))
        assert panel._input.get_text() == task
        assert panel._config['system_prompt'] == 'CHILD system prompt\nrole only'
        assert 'inherit_context' not in panel._config and panel._access == 'inherit'
        panel.handle_input(KeyEvent('k', ctrl=True))
        assert panel._mode == 'skills' and sent == [('skills', '', '', 'read-only')]
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'skills'  # Loading cannot apply a fabricated empty catalogue.
        panel.complete('skills', data=[])
        assert 'No available skills' in painted(panel)
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'form' and panel._config['skills'] == []
        assert panel._input.get_text() == task
        painted(panel)
        panel.handle_input(KeyEvent('enter'))
        assert not started
        confirm_all(panel)
        assert started == [(task, 'inherit', {'system_prompt': 'CHILD system prompt\nrole only', 'skills': []})]
    finally:
        panel.close()


def test_skill_picker_toggles_draft_only_and_escape_discards_selection():
    panel, sent, started = make()
    try:
        panel._form('start')
        panel._input.set_text('unchanged task')
        panel._config = {'skills': ['existing']}
        panel.open_skills()
        panel.complete('skills', data=[{'slug': 'first', 'description': 'First skill'},
                                       {'slug': 'second', 'description': 'Second skill'}])
        painted(panel)
        panel.handle_input(KeyEvent('space'))
        panel.handle_input(KeyEvent('down'))
        painted(panel)
        panel.handle_input(KeyEvent('space'))
        painted(panel)
        panel.handle_input(KeyEvent('enter'))
        assert panel._config['skills'] == ['existing', 'first', 'second']
        assert panel._input.get_text() == 'unchanged task' and not started
        panel.open_skills()
        panel.complete('skills', data=[{'slug': 'first', 'description': 'First skill'}])
        painted(panel)
        panel.handle_input(KeyEvent('space'))
        panel.handle_input(KeyEvent('escape'))
        assert panel._config['skills'] == ['existing', 'first', 'second']
    finally:
        panel.close()


def test_settings_json_accepts_tool_action_arrays_but_does_not_expose_context_inheritance():
    panel, sent, started = make()
    try:
        panel._form('start')
        panel._input.set_text('task knowledge')
        panel.handle_input(KeyEvent('g', ctrl=True))
        painted(panel)
        config = {'system_prompt': 'Specialist', 'tools': ['filesystem'],
                  'tool_actions': {'filesystem': ['read', 'list']}, 'skills': []}
        panel._input.set_text(json.dumps(config))
        panel.handle_input(KeyEvent('enter'))
        assert panel._config == config and panel._input.get_text() == 'task knowledge'
        panel.handle_input(KeyEvent('g', ctrl=True))
        assert 'inherit_context' not in panel._input.get_text()
        assert 'tool_actions' in painted(panel)
        panel._input.set_text('[]')
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'config' and 'object' in panel._error
        panel.handle_input(KeyEvent('escape'))
        assert panel._config == config
    finally:
        panel.close()


def test_current_child_prompt_edit_requires_actual_read_and_full_painted_review():
    panel, sent, started = make()
    try:
        panel.edit_system_prompt()
        assert sent == [('prompt', '0123456789ab', '', 'read-only')]
        assert panel._mode == 'prompt-loading'
        panel.complete('prompt', data={'config': {'system_prompt': 'Actual existing prompt'}})
        assert panel._input.get_text() == 'Actual existing prompt'
        panel._input.set_text('replacement\n' * 30 + 'LAST LINE')
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'form'  # Not painted; no admission/review.
        painted(panel)
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'confirm'
        painted(panel)
        panel.handle_input(KeyEvent('enter'))
        assert len(sent) == 1
        confirm_all(panel)
        assert sent[-1] == ('set_system_prompt', '0123456789ab', 'replacement\n' * 30 + 'LAST LINE', 'read-only')
        assert not started
    finally:
        panel.close()


def test_empty_prompt_is_explicit_reviewed_clear_and_late_read_cannot_replace_task():
    panel, sent, started = make()
    try:
        panel.edit_system_prompt()
        panel.handle_input(KeyEvent('escape'))
        panel._form('start')
        panel._input.set_text('new task')
        panel.complete('prompt', data={'config': {'system_prompt': 'stale prompt'}})
        assert panel._input.get_text() == 'new task'
        panel.back()
        panel.edit_system_prompt()
        panel.complete('prompt', data={'config': {'system_prompt': 'clear me'}})
        panel._input.set_text('')
        painted(panel)
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'confirm'
        confirm_all(panel)
        assert sent[-1] == ('set_system_prompt', '0123456789ab', '', 'read-only')
        assert not started
    finally:
        panel.close()
