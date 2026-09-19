"""Tool registry — pure dict. Domains register themselves; registry imports nothing.

Usage::

    from xdog.claw.core.tools.registry import register, create_tools
    register("my_tool", my_factory)     # called by domain __init__.py
    tools = create_tools()              # called by GroupRuntime
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from xdog.agent import AgentTool

#: A factory takes no arguments, or an `initial_cwd` when the tool is stateful
#: about where it works — the bash tool is, because `cd` persists across calls.
ToolFactory = Callable[..., AgentTool]
_registry: dict[str, ToolFactory] = {}


def register(name: str, factory: ToolFactory) -> None:
    """Register a tool factory. Called by domain packages on import."""
    _registry[name] = factory


def create_tools(
    enabled: tuple[str, ...] | None = None, *, workspace_dir: "Path | None" = None, image_model: str = "",
) -> list[AgentTool]:
    """Create the selected tools. None uses defaults; an empty tuple means none.

    *workspace_dir* is where the group's agent works. It reaches a factory only
    if the factory asks for `initial_cwd` — the bash tool does, because it holds
    a cwd that `cd` mutates, so it cannot be told per call through the ctx the
    way the filesystem tool is.

    Without this the bash tool defaulted to `Path.cwd()`, which is wherever the
    operator happened to launch the gateway: the group's workspace existed,
    stayed empty, and the agent's shell wrote its files into the launch
    directory instead.
    """
    import inspect

    selected = set(default_enabled_tools() if enabled is None else enabled)
    unknown = selected - _registry.keys()
    if unknown:
        raise ValueError(f"Unknown tools in enabled_tools: {', '.join(sorted(unknown))}")
    tools: list[AgentTool] = []
    for name, factory in _registry.items():
        if name not in selected:
            continue
        if name == "generate_image":
            tools.append(factory(model=image_model))
        elif workspace_dir is not None and "initial_cwd" in inspect.signature(factory).parameters:
            tools.append(factory(initial_cwd=workspace_dir))
        else:
            tools.append(factory())
    return tools


def default_enabled_tools() -> tuple[str, ...]:
    """Image generation requires explicit opt-in and a configured model."""
    return tuple(sorted(name for name in _registry if name != "generate_image"))


def registered_names() -> frozenset[str]:
    return frozenset(_registry.keys())
