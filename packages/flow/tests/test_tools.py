"""Tests for flow.tools — ToolRepo and built-in demo tools."""

from __future__ import annotations

from pathlib import Path

import pytest
from xdog.agent import ToolRepo
from xdog.agent.core import AgentTool
from xdog.flow.errors import WorkflowValidationError
from xdog.flow.models import WorkflowDef
from xdog.flow.tools import (
    coerce_tool,
    default_repo,
    load_tool_ref,
    register_workflow_tools,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def test_default_repo_has_echo() -> None:
    reg = default_repo()
    tool = reg.get("echo")
    assert tool.name == "echo"


def test_get_unknown_raises() -> None:
    reg = default_repo()
    with pytest.raises(ValueError, match="nope"):
        reg.get("nope")


def test_select_preserves_actual_tool_identity() -> None:
    reg = default_repo()
    result = reg.select(["echo"]).tools
    assert len(result) == 1
    assert result[0] is reg.get("echo")


async def test_echo_execute() -> None:
    reg = default_repo()
    tool = reg.get("echo")
    assert tool.execute is not None
    result = await tool.execute("id1", {"text": "hello"})
    assert result.content[0].text == "hello"  # type: ignore[union-attr]


# --- custom tool manifest loading --------------------------------------------


def test_names_includes_builtins() -> None:
    names = default_repo().names()
    assert {"echo", "bash", "filesystem"} <= names


def test_coerce_tool_accepts_instance() -> None:
    tool = AgentTool(name="x", description="d")
    assert coerce_tool(tool) is tool


def test_coerce_tool_calls_factory() -> None:
    tool = AgentTool(name="x", description="d")
    assert coerce_tool(lambda: tool) is tool


def test_coerce_tool_rejects_non_tool() -> None:
    with pytest.raises(WorkflowValidationError, match="AgentTool"):
        coerce_tool(42)


def test_coerce_tool_rejects_factory_returning_non_tool() -> None:
    with pytest.raises(WorkflowValidationError, match="must return an AgentTool"):
        coerce_tool(lambda: 42)


def test_load_tool_ref_factory() -> None:
    tool = load_tool_ref("mytools:make_reverse", _FIXTURES)
    assert tool.name == "reverse_internal"


def test_load_tool_ref_constant() -> None:
    tool = load_tool_ref("mytools:REVERSE_TOOL", _FIXTURES)
    assert tool.name == "reverse_const_internal"


def test_load_tool_ref_non_tool_raises() -> None:
    with pytest.raises(WorkflowValidationError):
        load_tool_ref("mytools:NOT_A_TOOL", _FIXTURES)


def test_register_workflow_tools_uses_manifest_name() -> None:
    wf = WorkflowDef(
        name="w",
        provider="fake",
        entry="n",
        nodes=(),
        edges=(),
        tool_refs=(("reverse", "mytools:make_reverse"), ("rev2", "mytools:REVERSE_TOOL")),
    )
    reg = ToolRepo(builtin=False)
    register_workflow_tools(wf, reg, _FIXTURES)
    # Manifest key is authoritative, overriding each tool's internal name.
    assert {"reverse", "rev2"} <= reg.names()
    assert reg.get("reverse").name == "reverse"
    assert reg.get("rev2").name == "rev2"


# --- describe_tools (builder Tools page) --------------------------------------


def test_describe_tools_includes_builtins() -> None:
    from xdog.flow.tools import describe_tools

    wf = WorkflowDef(name="w", provider="fake", entry="n", nodes=(), edges=())
    infos = describe_tools(wf)
    names = {i.name for i in infos}
    assert {"echo", "bash", "filesystem"} <= names
    assert all(i.origin == "builtin" for i in infos)
    echo = next(i for i in infos if i.name == "echo")
    assert echo.description == "Echo the given text."
    assert echo.source is not None  # inspect.getsource on the execute fn


def test_describe_tools_includes_custom_from_manifest() -> None:
    from xdog.flow.tools import describe_tools

    wf = WorkflowDef(
        name="w",
        provider="fake",
        entry="n",
        nodes=(),
        edges=(),
        tool_refs=(("reverse", "mytools:make_reverse"),),
    )
    infos = describe_tools(wf, _FIXTURES)
    rev = next(i for i in infos if i.name == "reverse")
    assert rev.origin == "custom"
    assert rev.description == "Reverse the given text."
    assert rev.source is not None
    assert "reverse" in rev.params.get("properties", {}) or "text" in rev.params.get("properties", {})


def test_describe_tools_broken_ref_is_skipped_gracefully() -> None:
    from xdog.flow.tools import describe_tools

    wf = WorkflowDef(
        name="w",
        provider="fake",
        entry="n",
        nodes=(),
        edges=(),
        tool_refs=(("broken", "mytools:NOT_A_TOOL"),),
    )
    infos = describe_tools(wf, _FIXTURES)
    broken = next(i for i in infos if i.name == "broken")
    assert broken.origin == "custom"
    assert broken.source is None


# --- read_run_source (builder Functions page, static / no-import) -------------


def test_read_run_source_in_tree_module() -> None:
    """An importable module resolves via inspect (e.g. flow.codegen_tools)."""
    from xdog.flow.tools import read_run_source

    src = read_run_source("xdog.flow.codegen_tools:next_task", None)
    assert src is not None
    assert "def next_task" in src


def test_read_run_source_from_subdir_without_import(tmp_path: Path) -> None:
    """A run: module in a subdir (scripts/) is read statically — no import runs."""
    from xdog.flow.tools import read_run_source

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    # Top-level import of a missing module: a naive import-based reader would fail;
    # the static AST reader must still return the function body.
    (scripts / "nodes.py").write_text(
        "import a_module_that_does_not_exist\n\n\ndef scope(ctx, repo):\n    return '{}'\n",
        encoding="utf-8",
    )
    src = read_run_source("nodes:scope", tmp_path)
    assert src is not None
    assert src.startswith("def scope(ctx, repo):")
    assert "return '{}'" in src


def test_read_run_source_missing_returns_none(tmp_path: Path) -> None:
    from xdog.flow.tools import read_run_source

    assert read_run_source("nonexistent:fn", tmp_path) is None


def test_default_repo_is_sdk_repo_with_actual_fresh_builtins() -> None:
    first, second = default_repo(), default_repo()
    assert type(first) is ToolRepo
    assert {"echo", "bash", "filesystem", "current_time", "submit_result"} == first.names()
    for name in ("bash", "filesystem", "current_time", "submit_result"):
        assert first.get(name) is not second.get(name)


def test_manifest_explicit_replacement_fences_original_receipt() -> None:
    old = AgentTool(name="echo", description="original")
    repo = ToolRepo(builtin=False)
    receipt = repo.register(old)
    wf = WorkflowDef(
        name="replacement", provider="offline", entry="n", nodes=(), edges=(),
        tool_refs=(("echo", "mytools:make_reverse"),),
    )
    register_workflow_tools(wf, repo, _FIXTURES)
    replacement = repo.get("echo")
    assert replacement is not old
    assert replacement.description == "Reverse the given text."
    receipt.close()
    assert repo.get("echo") is replacement
    with pytest.raises(ValueError):
        repo.register(old)
    assert repo.get("echo") is replacement


def test_invalid_manifest_ref_does_not_unregister_existing_tool() -> None:
    repo = default_repo()
    original = repo.get("echo")
    wf = WorkflowDef(
        name="invalid", provider="offline", entry="n", nodes=(), edges=(),
        tool_refs=(("echo", "mytools:NOT_A_TOOL"),),
    )
    with pytest.raises(WorkflowValidationError):
        register_workflow_tools(wf, repo, _FIXTURES)
    assert repo.get("echo") is original
