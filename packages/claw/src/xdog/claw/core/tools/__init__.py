"""Claw's explicit tool metadata and owner-local actual tool composition.

The metadata is for configuration UIs, not a runtime registry. Construct only the
selected Claw tools; optional tools must satisfy their original configuration.
"""
from collections.abc import Iterable
from pathlib import Path

from xdog.agent.tools import ToolRepo
from xdog.agent.tools.tool_submit_result import create_submit_result_tool
from xdog.claw.core.tools.tool_generate_image import create_generate_image_tool
from xdog.claw.core.tools.tool_memory import create_memory_tool
from xdog.claw.core.tools.tool_message import create_message_tool
from xdog.claw.core.tools.tool_skill import create_skill_tool
from xdog.claw.core.tools.tool_todo import create_todo_tool

DEFAULT_TOOL_NAMES = ('bash', 'filesystem', 'current_time', 'memory', 'message', 'skill', 'todo')
AVAILABLE_TOOL_NAMES = frozenset((*DEFAULT_TOOL_NAMES, 'submit_result', 'generate_image'))


def new_tool_repo(names: Iterable[str] | None = None, *, cwd: Path | None = None,
                  image_model: str = '') -> ToolRepo:
    """Create actual selected tools, without opening capabilities or MCP.

    None keeps Claw's existing default set. Empty selects no tools. Image
    generation is opt-in and still requires an explicitly configured model.
    """
    if isinstance(names, str):
        raise TypeError('Tool names must be an iterable of names, not a string')
    selected = tuple(DEFAULT_TOOL_NAMES if names is None else names)
    if any(not isinstance(name, str) or not name or name.strip() != name for name in selected):
        raise ValueError('Tool names must be nonempty text without surrounding whitespace')
    if len(set(selected)) != len(selected):
        raise ValueError('Duplicate selected tool name')
    unknown = set(selected) - AVAILABLE_TOOL_NAMES
    if unknown:
        raise ValueError('Unknown tools: ' + ', '.join(sorted(unknown)))
    if 'generate_image' in selected and not image_model:
        raise ValueError('generate_image requires image_model configuration. Run `xdog-claw onboard` first.')

    builtin_names = {'bash', 'filesystem', 'current_time'}
    repo = ToolRepo(cwd=cwd) if builtin_names.intersection(selected) else ToolRepo(builtin=False)
    tools = []
    for name in selected:
        if name in builtin_names:
            tools.append(repo.get(name))
        elif name == 'memory':
            tools.append(create_memory_tool())
        elif name == 'message':
            tools.append(create_message_tool())
        elif name == 'skill':
            tools.append(create_skill_tool())
        elif name == 'todo':
            tools.append(create_todo_tool())
        elif name == 'submit_result':
            tools.append(create_submit_result_tool())
        elif name == 'generate_image':
            tools.append(create_generate_image_tool(model=image_model))
    return ToolRepo(*tools, builtin=False)


__all__ = ['AVAILABLE_TOOL_NAMES', 'DEFAULT_TOOL_NAMES', 'new_tool_repo']
