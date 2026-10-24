"""flow.codegen_tools — script-node functions + tool registry for the codegen demo.

These back the ``examples/codegen_builder.json`` workflow, which demonstrates
that ``flow`` can orchestrate a code-generation pipeline:
``design -> implement (writes a file) -> verify (ruff/pytest) -> review (loop)``.

Everything here is flow-only (no agent-package edits).  Script nodes have the
signature ``async def fn(ctx, <input ports by name>) -> str`` — ``ctx`` first,
followed by one keyword parameter per declared input port — and their return
value is stored in the node's output port.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import sys

from xdog.agent import ToolRepo
from xdog.flow.runtime import RuntimeContext
from xdog.flow.tools import default_repo

# Cap each verification subprocess so a runaway check can't hang the workflow.
_CHECK_TIMEOUT_S = 120.0
# Keep only the tail of tool output in the state report (state values are strings).
_REPORT_TAIL = 4000


async def summarize_spec(ctx: RuntimeContext, spec: str) -> str:
    """Entry script node: surface the spec into the pipeline.

    Returns the ``spec`` input port (or empty).  A helper kept for the
    ``run``-ref codegen examples.
    """
    return spec


async def next_task(ctx: RuntimeContext, tasks: str) -> str:
    """Task-queue script node: pop the next pending task from the ``tasks`` port.

    ``tasks`` is a JSON array of task strings (a plain string is treated
    as a one-item list).  Returns a JSON object string::

        {"current": "<task>", "remaining": ["..."], "done": false}

    When the queue is empty it returns ``{"current": "", "remaining": [],
    "done": true}`` so the workflow can branch to completion.  Deterministic;
    no LLM involved.
    """
    raw = tasks or "[]"
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = [raw] if raw else []
    if isinstance(parsed, str):
        parsed = [parsed]
    task_list = [str(t) for t in parsed] if isinstance(parsed, list) else []

    if not task_list:
        return json.dumps({"current": "", "remaining": [], "done": True}, ensure_ascii=False)
    current, *remaining = task_list
    return json.dumps(
        {"current": current, "remaining": remaining, "done": False},
        ensure_ascii=False,
    )


async def _run(cmd: list[str], cwd: str | None) -> tuple[int, str]:
    """Run *cmd* capturing combined output; return (returncode, text)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return 1, f"failed to launch {shlex.join(cmd)}: {exc}"
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_CHECK_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        return 1, f"timeout after {_CHECK_TIMEOUT_S:.0f}s: {shlex.join(cmd)}"
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace")


async def run_checks(ctx: RuntimeContext, target_path: str) -> str:
    """Verify script node: run ruff + pytest against the ``target_path`` port.

    Returns a compact report string beginning with ``PASS`` when both checks
    succeed, or ``FAIL:`` followed by the failing tool's output tail otherwise.
    The review node branches on the ``FAIL`` marker to drive the bounded loop.
    """
    target = target_path
    if not target:
        return "FAIL: no target_path in state"

    ruff_rc, ruff_out = await _run([sys.executable, "-m", "ruff", "check", target], cwd=None)
    if ruff_rc != 0:
        return f"FAIL: ruff\n{ruff_out[-_REPORT_TAIL:]}"

    pytest_rc, pytest_out = await _run([sys.executable, "-m", "pytest", "-q", target], cwd=None)
    if pytest_rc != 0:
        return f"FAIL: pytest\n{pytest_out[-_REPORT_TAIL:]}"

    return "PASS: ruff + pytest clean"


async def verify_generated_module(ctx: RuntimeContext, module_file: str, mypy_target: str, test_file: str) -> str:
    """Production gate for a generated source file.

    Runs, in order, against paths taken from the node's input ports:
      1. ``ruff check`` on ``module_file``
      2. ``mypy --strict`` on ``mypy_target``  (a src dir/file)
      3. ``pytest`` on ``test_file``            (the acceptance contract)

    Returns ``PASS`` only if all three succeed, else ``FAIL: <stage>`` with the
    failing output tail so the review node can drive the regeneration loop.  This
    is the gate that makes flow codegen production-grade: a generated module must
    lint, type-check under --strict, and satisfy its contract test.
    """
    if not module_file or not mypy_target or not test_file:
        return "FAIL: need module_file, mypy_target, and test_file in state"

    ruff_rc, ruff_out = await _run([sys.executable, "-m", "ruff", "check", module_file, test_file], cwd=None)
    if ruff_rc != 0:
        return f"FAIL: ruff\n{ruff_out[-_REPORT_TAIL:]}"

    mypy_rc, mypy_out = await _run([sys.executable, "-m", "mypy", "--strict", mypy_target], cwd=None)
    if mypy_rc != 0:
        return f"FAIL: mypy\n{mypy_out[-_REPORT_TAIL:]}"

    pytest_rc, pytest_out = await _run([sys.executable, "-m", "pytest", "-q", test_file], cwd=None)
    if pytest_rc != 0:
        return f"FAIL: pytest\n{pytest_out[-_REPORT_TAIL:]}"

    return "PASS: ruff + mypy --strict + pytest clean"


async def autofix_module(ctx: RuntimeContext, module_file: str, test_file: str = "") -> str:
    """Apply mechanical, deterministic fixes to generated file(s) before verifying.

    Runs ``ruff check --fix`` then ``ruff format`` on the ``module_file`` port and,
    when present, the ``test_file`` port (the double-blind pipelines generate both).
    These resolve import ordering (I001), unused imports (F401), and formatting —
    fixes a code generator should not have to get right by hand, and which would
    otherwise waste the review→implement loop.  Returns a short report; never
    fails the pipeline (formatting is best-effort).
    """
    targets = [module_file, test_file]
    files = [f for f in targets if f]
    if not files:
        return "autofix skipped: no module_file"
    await _run([sys.executable, "-m", "ruff", "check", "--fix", *files], cwd=None)
    await _run([sys.executable, "-m", "ruff", "format", *files], cwd=None)
    return f"autofixed {', '.join(files)}"


def registry_with_filesystem() -> ToolRepo:
    """Return a registry with the built-in demo tools plus real agent tools.

    Adds the ``filesystem`` tool (so an agent node can write files) and the
    ``bash`` tool (so a setup node can create a git worktree for isolation).
    Pass this to :func:`flow.executor.execute` as ``tool_repo=`` so agent
    nodes declaring ``"tools": ["filesystem"]`` or ``["bash"]`` resolve.
    """
    return default_repo()
