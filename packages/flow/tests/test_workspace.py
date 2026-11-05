"""Workspace confinement, end to end through the executor.

The unit tests in `xdog-agent` cover the allowlist itself. These cover the thing
that is easy to get wrong and impossible to notice: a run that computes a
workspace perfectly and then never passes it to the tool would satisfy every
"it did not crash" test. So each of these asserts on what the tool was actually
handed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from xdog.agent import ToolRepo
from xdog.agent.tools.tool_filesystem import CONFINE_CTX_KEY
from xdog.flow.models import NodeDef
from xdog.flow.runners import SdkRunner


class _CapturingAgent:
    """Stands in for `xdog.agent.Agent`, remembering the ctx it was built with."""

    seen: dict[str, Any] = {}

    prompt_seen: str = ""

    def __init__(self, stream_fn: Any, **kwargs: Any) -> None:
        _CapturingAgent.seen = dict(kwargs.get("tool_ctx") or {})
        config = kwargs.get("config")
        _CapturingAgent.prompt_seen = str(getattr(config, "system_prompt", "") or "")
        self._kwargs = kwargs
        self.closed = False

    @property
    def state(self) -> Any:
        raise AssertionError("not reached")

    def dump(self) -> dict[str, Any]:
        assert not self.closed, "runner must dump before closing its Agent"
        return {}

    async def wait_for_idle(self) -> None:
        pass

    async def aclose(self) -> None:
        self.closed = True

    async def prompt(self, text: str) -> Any:
        class _Empty:
            def __aiter__(self) -> Any:
                return self

            async def __anext__(self) -> Any:
                raise StopAsyncIteration

        return _Empty()


async def _tool_ctx_for(
    monkeypatch: Any, *, node_tools: tuple[str, ...] = (), **runner_kwargs: Any
) -> dict[str, Any]:
    """Run one agent turn and return the tool_ctx the Agent was constructed with."""
    import xdog.agent.agent as agent_module
    from xdog.flow.tools import default_repo

    monkeypatch.setattr(agent_module, "Agent", _CapturingAgent)
    _CapturingAgent.seen = {}
    _CapturingAgent.prompt_seen = ""

    runner = SdkRunner(
        stream_fn_factory=lambda _m: object(),  # type: ignore[arg-type,return-value]
        tool_repo=default_repo() if node_tools else ToolRepo(builtin=False),
        web_search_fn_factory=None,
        **runner_kwargs,
    )
    await runner.run(
        NodeDef(id="n", tools=node_tools),
        system_prompt="",
        user_prompt="go",
        model="m",
        timeout=5.0,
        inputs={},
        step=0,
        fan_index=None,
    )
    return _CapturingAgent.seen


async def test_a_workspace_reaches_the_tool(monkeypatch: Any, tmp_path: Path) -> None:
    """The wiring that the whole feature rests on."""
    workspace = tmp_path / "runtime"

    ctx = await _tool_ctx_for(monkeypatch, confine_to=[workspace])

    assert ctx[CONFINE_CTX_KEY] == [str(workspace)]


async def test_confinement_survives_a_structured_node(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """A structured node builds its own `tool_ctx` for `submit_result`. Before
    this was merged rather than assigned, that overwrote the confinement — the
    bound would silently vanish for exactly the nodes doing the most work."""
    from xdog.flow.models import Port

    workspace = tmp_path / "runtime"
    import xdog.agent.agent as agent_module

    monkeypatch.setattr(agent_module, "Agent", _CapturingAgent)
    _CapturingAgent.seen = {}

    runner = SdkRunner(
        stream_fn_factory=lambda _m: object(),  # type: ignore[arg-type,return-value]
        tool_repo=ToolRepo(builtin=False),
        web_search_fn_factory=None,
        confine_to=[workspace],
    )
    structured = NodeDef(
        id="n",
        output_ports=(
            Port("a", schema={"type": "string"}),
            Port("b", schema={"type": "string"}),
        ),
    )
    try:
        await runner.run(
            structured,
            system_prompt="",
            user_prompt="go",
            model="m",
            timeout=5.0,
            inputs={},
            step=0,
            fan_index=None,
        )
    except Exception:
        pass  # the stub agent submits nothing; we only care about the ctx

    assert _CapturingAgent.seen[CONFINE_CTX_KEY] == [str(workspace)]
    assert "result_sink" in _CapturingAgent.seen, "and the sink is still there"


# -- What --confined refuses, and why it must -------------------------------


def _wf(nodes: list[dict], edges: list[dict]) -> Any:
    from xdog.flow.loader import parse_workflow

    return parse_workflow({
        "name": "c", "provider": "p", "defaults": {"model": "m"},
        "entry": nodes[0]["id"], "nodes": nodes, "edges": edges,
    })


def test_bash_and_cli_backends_cannot_be_confined() -> None:
    from xdog.flow.loader import unconfinable_reasons

    with_bash = _wf(
        [{"id": "a", "type": "agent", "prompt": "go", "tools": ["bash"], "outputs": ["out"]}],
        [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    )
    assert any("general-purpose escape" in r for r in unconfinable_reasons(with_bash))

    with_cli = _wf(
        [{"id": "a", "type": "agent", "prompt": "go", "backend": "claude-cli",
          "outputs": ["out"]}],
        [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    )
    assert any("owns its own session" in r for r in unconfinable_reasons(with_cli))


# -- interpret == compile ----------------------------------------------------


def test_the_generated_module_confines_the_same_way(tmp_path: Path) -> None:
    """The invariant, and the one place it matters most.

    Confinement shipped in the interpreter first and codegen knew nothing about
    it, so the same write that `--confined` refused succeeded when the workflow
    was compiled. Nothing failed; the bound simply was not there. A divergence
    in the *permissive* direction is the worst kind, because the run that skips
    the check is the one that looks like it worked.
    """
    import os
    import re

    from xdog.flow.codegen import generate
    from xdog.flow.loader import parse_workflow

    wf = parse_workflow({
        "name": "c", "provider": "p", "defaults": {"model": "m"}, "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "go", "tools": ["filesystem"],
                   "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    })
    source = generate(wf)

    # The generated module reads its bound from the environment, never from its
    # own source — a module told by its own text what it may touch is not bound.
    assert "FLOW_CONFINED" in source
    assert "fs_confine_to" in source
    assert "fs_workspace" in source
    assert not re.search(r'fs_confine_to["\']\s*:\s*\[["\']/', source), (
        "a hard-coded root would mean the workflow granted its own access"
    )

    namespace: dict[str, Any] = {}
    exec(compile(source, "<gen>", "exec"), namespace)  # noqa: S102 - our own output

    roots = namespace["_confinement_roots"]
    workspace_dir = namespace["_workspace_dir"]
    _granted = namespace["_granted_paths"]
    workspace = tmp_path / "runtime"

    old_ws = os.environ.pop("FLOW_WORKSPACE", None)
    old_conf = os.environ.pop("FLOW_CONFINED", None)
    try:
        # No file behind this module means no workspace. It used to fall back to
        # `Path.cwd()/runtime` and create it, which left a directory in whatever
        # directory the caller was standing in — the same mistake as a tool
        # defaulting its cwd to the launch directory.
        assert workspace_dir() is None
        assert roots() is None, "but nothing is enforced until asked, as in the interpreter"

        os.environ["FLOW_WORKSPACE"] = str(workspace)
        assert workspace_dir() == workspace
        assert roots() is None, "naming a workspace still does not confine to it"

        os.environ["FLOW_CONFINED"] = "1"
        assert roots() == [str(workspace.resolve())]

        os.environ["FLOW_ALLOW_PATHS"] = str(tmp_path / "data")
        assert roots() == [str(workspace.resolve()), str((tmp_path / "data").resolve())]
        # And the briefing: both engines must tell the model the same thing.
        # A generated module that confines correctly but describes the bound
        # differently produces a *different run*, since the prompt is an input.
        # The equality is near-tautological once codegen calls the shared
        # function — which is the point. The assertion that carries weight is
        # that it calls it at all, and with this run's workspace and roots
        # rather than a string of its own.
        from xdog.agent.workspace import workspace_briefing

        assert "workspace_briefing(" in source, "the module briefs rather than staying silent"
        assert "_workspace_dir()" in source and "_roots" in source, (
            "and briefs from the live values, not from anything baked into its text"
        )
        assert workspace_briefing(workspace_dir(), _granted(), confined=True) == (
            workspace_briefing(workspace, [tmp_path / "data"], confined=True)
        )
    finally:
        os.environ.pop("FLOW_ALLOW_PATHS", None)
        for key, old in (("FLOW_WORKSPACE", old_ws), ("FLOW_CONFINED", old_conf)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


# -- the grant in the scheduling unit ----------------------------------------


def _scheduled(node: dict) -> Any:
    from xdog.flow.loader import parse_workflow

    return parse_workflow({
        "name": "sched", "provider": "p", "defaults": {"model": "m"}, "entry": node["id"],
        "schedule": {"mode": "timer", "every": "1h"},
        "nodes": [node],
        "edges": [{"from": node["id"], "to": "$output", "map": {"out": "r"}}],
    })


# -- the workspace is on by default; confinement is not ----------------------


async def _prompt_for(monkeypatch: Any, **runner_kwargs: Any) -> str:
    await _tool_ctx_for(monkeypatch, **runner_kwargs)
    return _CapturingAgent.prompt_seen


def test_a_scripts_top_level_is_bounded_too(tmp_path: Path) -> None:
    """A script node's `code` is not only its function.

    Any other top-level statement runs as well, and it ran *unbounded* until this
    was tested -- so a workflow could put its filesystem access at module level
    and step around the audit hook entirely, `--confined` or not. Both engines
    had it, which is why it was easy to miss: nothing looked inconsistent.
    """
    import asyncio

    from xdog.flow.executor import execute
    from xdog.flow.loader import parse_workflow

    escape = tmp_path / "ESCAPED_AT_TOP_LEVEL.txt"
    wf = parse_workflow({
        "name": "t", "provider": "p", "defaults": {"model": "m"}, "entry": "s",
        "nodes": [{"id": "s", "type": "script", "outputs": ["r"], "code":
                   "from pathlib import Path\n"
                   f"Path({str(escape)!r}).write_text('escaped')\n"
                   "def s(ctx):\n    return 'ran'"}],
        "edges": [{"from": "s", "to": "$output", "map": {"r": "result"}}],
    })

    with pytest.raises(PermissionError):
        asyncio.run(execute(wf, workspace=tmp_path / "runtime", timeout=10))

    assert not escape.exists()


def test_a_run_ref_modules_import_is_deliberately_not_bounded(tmp_path: Path) -> None:
    """The asymmetry, stated so it is a decision rather than a discovery.

    `run: module:callable` loads a file from disk. Its import-time code is no
    more bounded than any other dependency's, because that is what importing is;
    the bound applies to the *call*. Inline `code` is different: it is text
    carried inside the workflow, which is the thing that may have been generated.
    """
    import asyncio
    import sys

    from xdog.flow.executor import execute
    from xdog.flow.loader import parse_workflow

    at_import = tmp_path / "at_import.txt"
    (tmp_path / "modref.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(at_import)!r}).write_text('import time')\n"
        "def go(ctx):\n"
        "    return str(ctx.workspace)\n",
        encoding="utf-8",
    )
    sys.modules.pop("modref", None)
    wf = parse_workflow({
        "name": "t", "provider": "p", "defaults": {"model": "m"}, "entry": "s",
        "nodes": [{"id": "s", "type": "script", "run": "modref:go", "outputs": ["r"]}],
        "edges": [{"from": "s", "to": "$output", "map": {"r": "result"}}],
    })

    asyncio.run(execute(wf, base_dir=tmp_path, workspace=tmp_path / "runtime", timeout=10))

    assert at_import.exists(), "import-time code runs unbounded, like any dependency"


# -- skills on agent nodes ---------------------------------------------------
