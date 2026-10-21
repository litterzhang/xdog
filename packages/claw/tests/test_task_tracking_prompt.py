"""The generic Claw prompt must not introduce a second task authority."""
from types import SimpleNamespace

import pytest
from xdog.claw.core.prompt import build_system_prompt
from xdog.claw.core.prompt.templates import TOOL_USAGE_RULES


@pytest.mark.parametrize("with_todo", [False, True])
def test_built_prompt_reuses_authoritative_tasks_and_makes_todo_optional(tmp_path, with_todo):
    tools = [SimpleNamespace(name="todo", description="Track local tasks")] if with_todo else []
    blocks = build_system_prompt(tmp_path, tools=tools)
    static = " ".join(blocks[0].text.split())

    assert blocks[0].cache
    assert "existing authoritative task list" in static
    assert "update it rather than copying it into a parallel checklist" in static
    assert "use `todo` only when it helps track progress, not as a requirement for every task" in static
    assert "Use `todo` to show progress on multi-step tasks" not in static
    assert "Mark each task as completed as you finish it" not in static
    assert "goal" not in static.lower()
    assert ("`todo` — Track local tasks" in static) is with_todo


def test_task_tracking_change_preserves_other_tool_guidance():
    assert "`filesystem` (action: read)" in TOOL_USAGE_RULES
    assert "`filesystem` (action: edit)" in TOOL_USAGE_RULES
    assert "If there are no dependencies between calls, make them in parallel" in TOOL_USAGE_RULES
