"""Unit tests for the codegen_tools script helpers (no LLM, no network).

Covers:
- the ``run_checks`` script node reports PASS/FAIL against a real temp dir;
- ``next_task`` pops the task queue deterministically;
- ``verify_generated_module`` / ``autofix_module`` behave on real temp files;
- ``registry_with_filesystem`` resolves the filesystem + bash tools.
"""

from __future__ import annotations

import json
import pathlib

from xdog.flow.codegen_tools import (
    autofix_module,
    next_task,
    registry_with_filesystem,
    run_checks,
    verify_generated_module,
)
from xdog.flow.runtime import RuntimeContext


def _ctx() -> RuntimeContext:
    """A minimal RuntimeContext for calling script functions directly."""
    return RuntimeContext(step=0, node_id="n", workflow_name="test")


# ---------------------------------------------------------------------------
# next_task (task queue script node)
# ---------------------------------------------------------------------------


async def test_next_task_pops_head() -> None:
    out = json.loads(await next_task(_ctx(), tasks=json.dumps(["a", "b", "c"])))
    assert out == {"current": "a", "remaining": ["b", "c"], "done": False}


async def test_next_task_empty_is_done() -> None:
    out = json.loads(await next_task(_ctx(), tasks="[]"))
    assert out == {"current": "", "remaining": [], "done": True}


async def test_next_task_plain_string() -> None:
    out = json.loads(await next_task(_ctx(), tasks="single task"))
    assert out["current"] == "single task"
    assert out["done"] is False


# ---------------------------------------------------------------------------
# run_checks (verify script node) against real temp files
# ---------------------------------------------------------------------------


async def test_run_checks_pass(tmp_path: pathlib.Path) -> None:
    (tmp_path / "mod.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    (tmp_path / "test_mod.py").write_text(
        "from mod import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    report = await run_checks(_ctx(), target_path=str(tmp_path))
    assert report.startswith("PASS"), report


async def test_run_checks_fail_ruff(tmp_path: pathlib.Path) -> None:
    # Unused import -> ruff F401 failure.
    (tmp_path / "bad.py").write_text("import os\n", encoding="utf-8")
    report = await run_checks(_ctx(), target_path=str(tmp_path))
    assert report.startswith("FAIL"), report


async def test_run_checks_no_target() -> None:
    report = await run_checks(_ctx(), target_path="")
    assert report.startswith("FAIL")


# ---------------------------------------------------------------------------
# verify_generated_module + autofix_module (production codegen gate)
# ---------------------------------------------------------------------------


async def test_verify_generated_module_needs_all_paths() -> None:
    report = await verify_generated_module(_ctx(), module_file="x", mypy_target="", test_file="")
    assert report.startswith("FAIL")


async def test_autofix_module_cleans_imports(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "mod.py"
    # unused import + unsorted -> ruff --fix removes/reorders it
    f.write_text("import sys\nimport os\n\n\ndef g() -> int:\n    return 1\n", encoding="utf-8")
    report = await autofix_module(_ctx(), module_file=str(f))
    assert "autofixed" in report
    cleaned = f.read_text(encoding="utf-8")
    assert "import os" not in cleaned
    assert "import sys" not in cleaned


async def test_autofix_module_no_target_is_safe() -> None:
    report = await autofix_module(_ctx(), module_file="")
    assert "skipped" in report


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_registry_resolves_filesystem_and_bash() -> None:
    reg = registry_with_filesystem()
    tools = reg.select(["filesystem", "bash"]).tools
    names = {t.name for t in tools}
    assert "filesystem" in names
    assert "bash" in names
