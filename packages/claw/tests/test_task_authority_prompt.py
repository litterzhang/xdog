"""The host contract is not a coding persona, tool catalog or mandatory task tracker."""
from __future__ import annotations

import importlib.util

import pytest
from xdog.claw.core.prompt import build_system_prompt, init_workspace, templates
from xdog.claw.core.tools import new_tool_repo


def test_retired_prompt_sections_have_no_aliases_or_default_injection(tmp_path):
    assert importlib.util.find_spec('xdog.claw.core.prompt.sections') is None
    for name in ('TASK_EXECUTION', 'OUTPUT', 'TONE', 'ACTION_SAFETY', 'IDENTITY',
                 'TOOL_SECTION_HEADER', 'TOOL_ENTRY', 'TOOL_USAGE_RULES'):
        assert not hasattr(templates, name)
    blocks = build_system_prompt(tmp_path)
    static = blocks[0].text
    assert blocks[0].cache and not blocks[1].cache
    assert '# Host contract' in static
    for retired in ('# Doing tasks', '# Communicating with the user', '# Tone and style',
                    'Use Github-flavored markdown', 'Only use emojis', 'Before your first tool call',
                    'Three similar lines of code', 'filesystem', 'bash', 'todo', 'memory(action=', 'skill tool'):
        assert retired not in static
    assert 'do not claim completion or delivery without checking' in static
    assert 'Do not\nask again for an already authorized action' in static
    assert 'do not bypass a\npermission denial' in static


def test_working_rules_and_persona_are_group_editable_not_fixed_defaults(tmp_path):
    init_workspace(tmp_path)
    before = build_system_prompt(tmp_path)
    rules = '# Instructions\nRead relevant code before modifying it.\nUse emoji freely.'
    (tmp_path / 'AGENTS.md').write_text(rules)
    after = build_system_prompt(tmp_path)
    assert before[0] == after[0]
    assert rules in after[1].text and rules not in after[0].text
    assert 'Read relevant code before modifying it' not in before[1].text
    assert 'IDENTITY.md' in after[0].text and 'SOUL.md' in after[0].text


@pytest.mark.parametrize('name', ['todo', 'memory', 'skill', 'message'])
def test_tool_procedures_live_with_actual_schemas_not_system_prompt(tmp_path, name):
    tool = new_tool_repo((name,)).get(name)
    assert tool.description and tool.parameters['properties']
    assert tool.description not in '\n'.join(block.text for block in build_system_prompt(tmp_path))
    if name == 'todo':
        assert 'authoritative task files' in tool.description and 'Not required' in tool.description
    elif name == 'message':
        assert 'unknown delivery' in tool.description and 'send.reference' in tool.description
        assert 'same person' in tool.description
    elif name == 'skill':
        assert 'not after every successful task' in tool.description
