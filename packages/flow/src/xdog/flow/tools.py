"""flow.tools — tool registry and built-in demo tools for workflow nodes."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib
import inspect
import sys
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from xdog.agent.core import AgentTool, AgentToolResult
from xdog.ai.types import TextContent
from xdog.flow.errors import WorkflowValidationError

if TYPE_CHECKING:
    from xdog.flow.models import WorkflowDef


class ToolRegistry:
    """Registry mapping tool names to :class:`~agent.core.AgentTool` objects."""

    def __init__(self) -> None:
        from xdog.agent.tools import ToolRegistry as AgentToolRegistry

        self._registry = AgentToolRegistry(builtins=False)

    def register(self, tool: AgentTool) -> None:
        """Register *tool* by its name (replaces any existing entry)."""
        self._registry.replace_source(tool.name, (tool,))

    def get(self, name: str) -> AgentTool:
        """Return the tool registered under *name*.

        Raises :class:`~flow.errors.WorkflowValidationError` if unknown.
        """
        try:
            return next(tool for tool in self._registry.tools if tool.name == name)
        except StopIteration:
            known = ", ".join(sorted(tool.name for tool in self._registry.tools)) or "<none>"
            raise WorkflowValidationError(f"Unknown tool {name!r}. Known tools: {known}") from None

    def names(self) -> frozenset[str]:
        """Return the set of registered tool names."""
        return frozenset(tool.name for tool in self._registry.tools)

    def resolve(self, names: Iterable[str]) -> tuple[AgentTool, ...]:
        """Resolve an iterable of tool names to a tuple of tools."""
        return tuple(self.get(n) for n in names)


# ---------------------------------------------------------------------------
# Built-in demo tool
# ---------------------------------------------------------------------------


async def _echo_execute(
    tool_call_id: str,
    params: dict[str, Any],
    cancel: asyncio.Event | None = None,
    on_update: Any | None = None,
    ctx: dict[str, Any] | None = None,
) -> AgentToolResult:
    text: str = params.get("text", "")
    return AgentToolResult(content=(TextContent(text=text),))


_ECHO_TOOL = AgentTool(
    name="echo",
    description="Echo the given text.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string"},
        },
        "required": ["text"],
    },
    label="Echo",
    execute=_echo_execute,
)


def default_registry() -> ToolRegistry:
    """Return a fresh :class:`ToolRegistry` pre-loaded with built-in tools.

    Includes the demo ``echo`` tool plus the SDK default tools and the
    explicitly selected ``submit_result`` factory.  This means a workflow node can declare
    ``"tools": ["filesystem"]`` and have it resolve out of the box — including
    from the ``xdog-flow run`` CLI, which uses this registry by default.
    """
    registry = ToolRegistry()
    registry.register(_ECHO_TOOL)
    for tool in _agent_builtin_tools():
        registry.register(tool)
    return registry


def _agent_builtin_tools() -> tuple[AgentTool, ...]:
    """Create independent SDK default tools plus Flow's structured result tool.

    Imported lazily; the execution owner creates its own SDK registry. No
    import-order registration or process-global runtime is consulted.
    """
    try:
        from xdog.agent.tools import ToolRegistry as AgentToolRegistry
    except ImportError:  # pragma: no cover - agent always present in the workspace
        return ()
    registry = AgentToolRegistry()
    return tuple(registry.create_tools(names=(*registry.default_names(), "submit_result")))


# ---------------------------------------------------------------------------
# Custom tool manifest loading (workflow-level "tools" refs)
# ---------------------------------------------------------------------------


def coerce_tool(obj: object) -> AgentTool:
    """Coerce a loaded manifest reference into an :class:`AgentTool`.

    Accepts an :class:`AgentTool` instance as-is, or a zero-argument callable
    (a factory) whose return value is an :class:`AgentTool`.
    """
    if isinstance(obj, AgentTool):
        return obj
    if callable(obj):
        built = obj()
        if not isinstance(built, AgentTool):
            raise WorkflowValidationError(
                f"Tool factory {getattr(obj, '__name__', obj)!r} must return an AgentTool, got {type(built).__name__}"
            )
        return built
    raise WorkflowValidationError(
        f"Tool ref must resolve to an AgentTool or a factory returning one, got {type(obj).__name__}"
    )


def load_tool_ref(ref: str, base_dir: Path | None = None) -> AgentTool:
    """Load an ``AgentTool`` from a ``'module:attr'`` reference.

    Imports *module* with *base_dir* (the workflow's own directory) temporarily
    on ``sys.path`` — the same idiom script nodes use for ``run`` — so a workflow
    can bundle its tool ``.py`` files alongside the JSON.  The resolved attribute
    is coerced via :func:`coerce_tool`.
    """
    module_name, _, attr = ref.partition(":")
    if base_dir is not None:
        sys.path.insert(0, str(base_dir))
        importlib.invalidate_caches()
        try:
            module = importlib.import_module(module_name)
        finally:
            with contextlib.suppress(ValueError):
                sys.path.remove(str(base_dir))
    else:
        module = importlib.import_module(module_name)
    return coerce_tool(getattr(module, attr))


def register_workflow_tools(wf: WorkflowDef, registry: ToolRegistry, base_dir: Path | None = None) -> None:
    """Load every tool in *wf*'s manifest and register it into *registry*.

    Each ``(name, ref)`` pair is loaded via :func:`load_tool_ref` and registered
    under the manifest *name* (authoritative, so the LLM-facing ``tool.name``
    matches what nodes reference), overriding the tool's own name if different.
    """
    for name, ref in wf.tool_refs:
        tool = load_tool_ref(ref, base_dir)
        registry.register(replace(tool, name=name))


def bind_tool(obj: object, name: str) -> AgentTool:
    """Coerce *obj* to an :class:`AgentTool` and rename it to *name*.

    The single helper generated workflow modules call to register a manifest
    tool under its authoritative name (keeps their import list to one
    ``flow.tools`` line — no ``dataclasses`` import needed).
    """
    return replace(coerce_tool(obj), name=name)


# ---------------------------------------------------------------------------
# Static source reading (for the builder's Functions page)
# ---------------------------------------------------------------------------


def _extract_function_source(module_source: str, func_name: str) -> str | None:
    """Return the source of top-level ``func_name`` in *module_source*, or None."""
    try:
        tree = ast.parse(module_source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == func_name:
            segment = ast.get_source_segment(module_source, node)
            if segment is not None:
                return segment
    return None


def read_run_source(run: str, base_dir: Path | None = None) -> str | None:
    """Return the source of a script node's ``run: "module:func"`` reference.

    Read-only and side-effect-free: it never imports the workflow's modules
    (which may pull in heavy or missing dependencies).  Resolution order:

    1. If *module* is already importable (e.g. an in-tree ``flow.codegen_tools``),
       import it and use :func:`inspect.getsource` on the function.
    2. Otherwise search *base_dir* and its immediate subdirectories for
       ``<last_module_part>.py`` and statically extract the function via AST.

    Returns ``None`` when the source can't be located.
    """
    module_name, _, func_name = run.partition(":")
    if not func_name:
        return None

    # 1) Already-importable module — safe to introspect its live object.
    with contextlib.suppress(Exception):
        module = importlib.import_module(module_name)
        fn = getattr(module, func_name, None)
        if fn is not None:
            return inspect.getsource(fn)

    # 2) Static file search — no import, no execution.
    if base_dir is None:
        return None
    leaf = module_name.split(".")[-1]
    candidates = [base_dir / f"{leaf}.py", *(sub / f"{leaf}.py" for sub in _child_dirs(base_dir))]
    for path in candidates:
        if path.is_file():
            with contextlib.suppress(OSError):
                extracted = _extract_function_source(path.read_text(encoding="utf-8"), func_name)
                if extracted is not None:
                    return extracted
    return None


def _child_dirs(base_dir: Path) -> list[Path]:
    """Immediate subdirectories of *base_dir* (sorted, non-hidden)."""
    try:
        return sorted(p for p in base_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


# ---------------------------------------------------------------------------
# Tool introspection (for the builder's Tools page)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolInfo:
    """A descriptive snapshot of a tool for read-only display.

    ``origin`` is ``"builtin"`` for tools from :func:`default_registry` or
    ``"custom"`` for tools declared in a workflow's ``tool_refs`` manifest.
    ``source`` is the ``execute`` callable's source when retrievable, else
    ``None``.
    """

    name: str
    origin: Literal["builtin", "custom"]
    description: str
    params: dict[str, Any]
    source: str | None


def _tool_source(tool: AgentTool) -> str | None:
    """Best-effort source of a tool's ``execute`` callable, or ``None``."""
    fn = tool.execute
    if fn is None:
        return None
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        return None


def describe_tools(wf: WorkflowDef, base_dir: Path | None = None) -> tuple[ToolInfo, ...]:
    """Return :class:`ToolInfo` for every built-in and manifest tool of *wf*.

    Built-ins come from :func:`default_registry`; custom tools are loaded from
    the workflow's ``tool_refs`` (via :func:`load_tool_ref`, *base_dir*-aware).
    A custom tool that fails to load is skipped rather than raising, so the
    builder's read-only view degrades gracefully.
    """
    infos: list[ToolInfo] = []
    for name in sorted(default_registry().names()):
        tool = default_registry().get(name)
        infos.append(
            ToolInfo(
                name=name,
                origin="builtin",
                description=tool.description,
                params=dict(tool.parameters),
                source=_tool_source(tool),
            )
        )
    for name, ref in wf.tool_refs:
        try:
            tool = replace(load_tool_ref(ref, base_dir), name=name)
        except Exception:  # noqa: BLE001 - a broken ref shouldn't crash the viewer
            infos.append(
                ToolInfo(name=name, origin="custom", description=f"(failed to load {ref})", params={}, source=None)
            )
            continue
        infos.append(
            ToolInfo(
                name=name,
                origin="custom",
                description=tool.description,
                params=dict(tool.parameters),
                source=_tool_source(tool),
            )
        )
    return tuple(infos)
