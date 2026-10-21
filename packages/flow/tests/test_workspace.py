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
from xdog.agent.tools.tool_filesystem import CONFINE_CTX_KEY, WORKSPACE_CTX_KEY
from xdog.flow.models import NodeDef
from xdog.flow.runners import SdkRunner
from xdog.flow.tools import ToolRegistry


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
    from xdog.flow.tools import default_registry

    monkeypatch.setattr(agent_module, "Agent", _CapturingAgent)
    _CapturingAgent.seen = {}
    _CapturingAgent.prompt_seen = ""

    runner = SdkRunner(
        stream_fn_factory=lambda _m: object(),  # type: ignore[arg-type,return-value]
        tool_registry=default_registry() if node_tools else ToolRegistry(),
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


async def test_an_unconfined_run_passes_no_bound(monkeypatch: Any) -> None:
    """The default, and what every caller before this got. `xdog-coding` and
    `xdog-claw` build their own agents and must be unaffected."""
    ctx = await _tool_ctx_for(monkeypatch)

    assert CONFINE_CTX_KEY not in ctx


async def test_a_workspace_reaches_the_tool(monkeypatch: Any, tmp_path: Path) -> None:
    """The wiring that the whole feature rests on."""
    workspace = tmp_path / "runtime"

    ctx = await _tool_ctx_for(monkeypatch, confine_to=[workspace])

    assert ctx[CONFINE_CTX_KEY] == [str(workspace)]


async def test_granted_trees_are_added_to_the_workspace(
    monkeypatch: Any, tmp_path: Path
) -> None:
    workspace = tmp_path / "runtime"
    granted = tmp_path / "data"

    ctx = await _tool_ctx_for(monkeypatch, confine_to=[workspace, granted])

    assert ctx[CONFINE_CTX_KEY] == [str(workspace), str(granted)]


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
        tool_registry=ToolRegistry(),
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
    assert "flow_result_sink" in _CapturingAgent.seen, "and the sink is still there"


# -- What --confined refuses, and why it must -------------------------------


def _wf(nodes: list[dict], edges: list[dict]) -> Any:
    from xdog.flow.loader import parse_workflow

    return parse_workflow({
        "name": "c", "provider": "p", "defaults": {"model": "m"},
        "entry": nodes[0]["id"], "nodes": nodes, "edges": edges,
    })


def test_a_confinable_workflow_has_no_reasons() -> None:
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "a", "type": "agent", "prompt": "go", "tools": ["filesystem"],
          "outputs": ["out"]}],
        [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    )
    assert unconfinable_reasons(wf) == []


def test_an_inline_script_is_no_longer_a_reason_to_refuse() -> None:
    """This used to refuse, and the reasoning was right at the time: `exec` in
    the executor's own process met no check. The audit hook is that check. A
    script node now runs and gets stopped when it reaches for something, which
    is a better answer than refusing the workflow that contains it."""
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "s", "type": "script", "code": "def s(ctx):\n    return 1", "outputs": ["out"]}],
        [{"from": "s", "to": "$output", "map": {"out": "r"}}],
    )

    assert unconfinable_reasons(wf) == []


def test_what_still_refuses_is_what_leaves_this_process() -> None:
    """The remaining rules are not a list of bad things; they are the boundary of
    what an in-process hook can see."""
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "a", "type": "agent", "prompt": "go", "tools": ["bash"], "outputs": ["out"]}],
        [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    )
    assert unconfinable_reasons(wf), "a shell is a child process; the hook cannot follow it"


def test_a_run_ref_script_can_be_confined() -> None:
    """`run: module:callable` imports reviewed code from disk rather than
    executing text carried inside the workflow — the same trust as any other
    dependency, so it is not a reason to refuse."""
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "s", "type": "script", "run": "mymod:fn", "outputs": ["out"]}],
        [{"from": "s", "to": "$output", "map": {"out": "r"}}],
    )
    assert unconfinable_reasons(wf) == []


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


def test_a_subflow_hides_nothing() -> None:
    """A child runs in the parent's process, so a `bash` tool buried in one is
    exactly as unconfinable as at the top level — and much easier to miss."""
    from xdog.flow.loader import unconfinable_reasons

    wf = _wf(
        [{"id": "outer", "type": "subflow", "subflow": {
            "name": "child", "provider": "p", "defaults": {"model": "m"},
            "entry": "inner",
            "nodes": [{"id": "inner", "type": "agent", "prompt": "go",
                       "tools": ["bash"], "outputs": ["out"]}],
            "edges": [{"from": "inner", "to": "$output", "map": {"out": "out"}}],
        }}],
        [{"from": "outer", "to": "$output", "map": {"out": "r"}}],
    )
    reasons = unconfinable_reasons(wf)

    assert reasons and "subflow 'outer'" in reasons[0]


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


def test_the_timer_unit_carries_the_grant(tmp_path: Path) -> None:
    """A scheduled run is the case confinement was written for: nobody is
    watching. The unit is where the grant belongs, because the person who wrote
    the unit is the one with authority to give it."""
    from xdog.flow.scheduler.systemd import ConfineGrant, render_timer_units

    wf = _scheduled({"id": "a", "type": "agent", "prompt": "go", "tools": ["filesystem"],
                     "outputs": ["out"]})
    grant = ConfineGrant(workspace=tmp_path / "runtime", allow_paths=(tmp_path / "data",))

    units = render_timer_units("sched", tmp_path / "bundle", wf.schedule, confine=grant)  # type: ignore[arg-type]
    service = units.files["sched.service"]

    assert f"Environment=FLOW_WORKSPACE={tmp_path / 'runtime'}" in service
    assert f"Environment=FLOW_ALLOW_PATHS={tmp_path / 'data'}" in service


def test_an_unconfined_install_renders_exactly_what_it_did_before(tmp_path: Path) -> None:
    """The default path must not change: every existing install is unconfined."""
    from xdog.flow.scheduler.systemd import render_timer_units

    wf = _scheduled({"id": "a", "type": "agent", "prompt": "go", "outputs": ["out"]})

    service = render_timer_units("sched", tmp_path / "b", wf.schedule).files["sched.service"]  # type: ignore[arg-type]

    assert "FLOW_WORKSPACE" not in service


def test_installing_an_unconfinable_workflow_confined_is_refused(tmp_path: Path) -> None:
    """At install time, when someone is present to read why — rather than at 3am
    with only a systemd status to explain it."""
    import pytest
    from xdog.flow.scheduler.install import Installer
    from xdog.flow.scheduler.systemd import ConfineGrant

    wf = _scheduled({"id": "s", "type": "agent", "prompt": "go", "tools": ["bash"],
                     "outputs": ["out"]})
    installer = Installer(unit_dir=tmp_path / "units", data_dir=tmp_path / "data")

    with pytest.raises(ValueError, match="cannot be confined"):
        installer.install(wf, dry_run=True, confine=ConfineGrant(workspace=tmp_path / "ws"))


def test_a_hook_workflow_gets_its_grant_through_the_registry(tmp_path: Path) -> None:
    """A hook workflow has no unit of its own — the shared listener spawns it —
    so the registry is the only place the grant can live. If the listener did not
    apply it, `--confined` would be silently inert for exactly the mode that runs
    on someone else's schedule."""
    from xdog.flow.scheduler.listener import HookRoute, Router

    fired: list[dict[str, str]] = []
    route = HookRoute(
        name="h", bundle=tmp_path / "b", signal="go",
        listen={"type": "http", "path": "/h"},
        confine={"FLOW_WORKSPACE": str(tmp_path / "ws")},
    )
    router = Router(routes=[route], spawn=lambda _b, env: fired.append(env))

    router.deliver_http("/h", b'{"x": 1}')

    assert fired[0]["FLOW_WORKSPACE"] == str(tmp_path / "ws")


# -- the workspace is on by default; confinement is not ----------------------


async def _prompt_for(monkeypatch: Any, **runner_kwargs: Any) -> str:
    await _tool_ctx_for(monkeypatch, **runner_kwargs)
    return _CapturingAgent.prompt_seen


async def test_a_default_run_gets_a_workspace_but_no_bound(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """The two halves, and why they are separate. Every run has a workspace so
    relative paths land somewhere predictable; only `--confined` makes leaving it
    an error. Collapsing these — as the first implementation did — means an
    ordinary workflow has no workspace at all until someone asks to be confined."""
    workspace = tmp_path / "runtime"

    ctx = await _tool_ctx_for(monkeypatch, workspace=workspace)

    assert ctx[WORKSPACE_CTX_KEY] == str(workspace)
    assert CONFINE_CTX_KEY not in ctx, "a workspace is a convention until confined"


async def test_the_agent_is_told_where_its_workspace_is(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """A bound the model cannot see is one it can only find by tripping over it,
    which costs a turn and looks to the model like a malfunction."""
    workspace = tmp_path / "runtime"

    prompt = await _prompt_for(
        monkeypatch, node_tools=("filesystem",), workspace=workspace
    )

    assert str(workspace) in prompt
    assert "Do not read or write anything outside" in prompt, (
        "the instruction is the point: unconfined, this is all we have"
    )
    assert "This run is confined" not in prompt, "nothing is actually refused yet"


async def test_a_confined_agent_is_told_the_walls_and_the_grants(
    monkeypatch: Any, tmp_path: Path
) -> None:
    workspace = tmp_path / "runtime"
    granted = tmp_path / "data"

    prompt = await _prompt_for(
        monkeypatch, node_tools=("filesystem",), workspace=workspace,
        allow_paths=[granted], confine_to=[workspace, granted],
    )

    assert str(workspace) in prompt
    assert str(granted) in prompt, "a grant it is not told about is a grant it cannot use"
    assert "This run is confined" in prompt


async def test_even_a_node_with_no_declared_tools_is_briefed(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """This started out keyed off the tool list and that was a guess.

    A node's declared tools do not bound what it can touch: a custom tool can
    write files, so can an MCP server, and a model can name a path for a
    downstream node to use. The workspace is where this run's files belong,
    which is true of a node whether or not we can see how it writes them.
    """
    prompt = await _prompt_for(monkeypatch, workspace=tmp_path / "runtime")

    assert str(tmp_path / "runtime") in prompt


def test_the_two_engines_default_their_workspace_to_different_places(tmp_path: Path) -> None:
    """The one place `interpret == compile` deliberately does not mean "the same
    path", and it is worth pinning so it stays deliberate.

    Both engines apply the same rule — `runtime/` beside the artifact — but the
    artifact differs: the interpreter's is the workflow file, the compiled one's
    is the module. Making the module use the workflow file's directory would be
    worse than the difference, since a bundle routinely runs on a machine where
    that file does not exist. Anyone who needs them to agree names the workspace
    explicitly, which is what `--workspace` and `FLOW_WORKSPACE` are for.
    """
    import os

    from xdog.flow.codegen import generate
    from xdog.flow.loader import parse_workflow

    wf = parse_workflow({
        "name": "c", "provider": "p", "defaults": {"model": "m"}, "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "go", "tools": ["filesystem"],
                   "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    })
    namespace: dict[str, Any] = {"__file__": str(tmp_path / "gen.py")}
    old = os.environ.pop("FLOW_WORKSPACE", None)
    try:
        exec(compile(generate(wf), "<gen>", "exec"), namespace)  # noqa: S102 - our own output

        # Same rule, different anchor: `runtime/` beside the module, not beside
        # the workflow file.
        assert namespace["_workspace_dir"]() == tmp_path / "runtime"

        # And naming it explicitly makes them agree, which is the escape hatch.
        os.environ["FLOW_WORKSPACE"] = str(tmp_path / "runtime")
        assert namespace["_workspace_dir"]() == tmp_path / "runtime"
    finally:
        if old is None:
            os.environ.pop("FLOW_WORKSPACE", None)
        else:
            os.environ["FLOW_WORKSPACE"] = old


def test_the_generated_guard_enforces_the_same_events() -> None:
    """The generated module carries its own copy of the audit guard, because a
    script-only bundle installs no xdog-agent. Two copies of a security-relevant
    table is exactly the drift that let codegen ship unconfined once already, so
    the tables are compared rather than trusted.
    """
    from xdog.agent import workspace as lib
    from xdog.flow.codegen import generate
    from xdog.flow.loader import parse_workflow

    wf = parse_workflow({
        "name": "s", "provider": "p", "defaults": {"model": "m"}, "entry": "s",
        "nodes": [{"id": "s", "type": "script", "outputs": ["out"],
                   "code": "def s(ctx):\n    return 1"}],
        "edges": [{"from": "s", "to": "$output", "map": {"out": "r"}}],
    })
    namespace: dict[str, Any] = {}
    exec(compile(generate(wf), "<gen>", "exec"), namespace)  # noqa: S102 - our own output

    assert namespace["_WRITE_EVENTS"] == lib._WRITE_EVENTS, (
        "an event enforced in one engine and not the other is a bound that "
        "depends on how the workflow was run"
    )
    assert namespace["_UNAUDITABLE"] == lib._UNAUDITABLE


def test_a_script_node_is_told_where_its_workspace_is(tmp_path: Path) -> None:
    """Being refused for writing outside a directory you were never told about
    is a trap, not a rule — and relative paths are no help, since nodes run
    concurrently and the executor cannot chdir on a script's behalf."""
    import asyncio

    from xdog.flow.executor import execute
    from xdog.flow.loader import parse_workflow

    wf = parse_workflow({
        "name": "s", "provider": "p", "defaults": {"model": "m"}, "entry": "s",
        "nodes": [{"id": "s", "type": "script", "outputs": ["ws_path"], "code":
                   "def s(ctx):\n"
                   "    (ctx.workspace / 'out.txt').write_text('written')\n"
                   "    return str(ctx.workspace)"}],
        "edges": [{"from": "s", "to": "$output", "map": {"ws_path": "r"}}],
    })
    ws = tmp_path / "runtime"

    result = asyncio.run(execute(wf, workspace=ws, timeout=10))

    assert result.runtime["out"]["r"] == str(ws)
    assert (ws / "out.txt").read_text() == "written", "and writing there is allowed"


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


def test_the_generated_module_bounds_its_top_level_at_import(tmp_path: Path) -> None:
    """And the compiled half, where it is worse: the inlined top level runs when
    the module is imported, before `main()` is called at all."""
    import os

    from xdog.flow.codegen import generate
    from xdog.flow.loader import parse_workflow

    escape = tmp_path / "ESCAPED_AT_IMPORT.txt"
    wf = parse_workflow({
        "name": "t", "provider": "p", "defaults": {"model": "m"}, "entry": "s",
        "nodes": [{"id": "s", "type": "script", "outputs": ["r"], "code":
                   "from pathlib import Path\n"
                   f"Path({str(escape)!r}).write_text('escaped')\n"
                   "def s(ctx):\n    return 'ran'"}],
        "edges": [{"from": "s", "to": "$output", "map": {"r": "result"}}],
    })
    source = generate(wf)
    assert "with script_bound(" in source

    old = os.environ.get("FLOW_WORKSPACE")
    os.environ["FLOW_WORKSPACE"] = str(tmp_path / "runtime")
    try:
        with pytest.raises(PermissionError):
            exec(compile(source, "<gen>", "exec"), {})  # noqa: S102 - our own output
    finally:
        if old is None:
            os.environ.pop("FLOW_WORKSPACE", None)
        else:
            os.environ["FLOW_WORKSPACE"] = old

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


def test_a_module_may_import_its_siblings_but_not_read_their_data(tmp_path: Path) -> None:
    """A `run:` module lives beside the workflow and may import what sits with it.

    That is not a new grant: importing from there is exactly how the node itself
    was resolved. Granting the whole directory would be different — `base_dir` is
    wherever the .json happens to live, so a workflow kept in a home directory
    would make ~/.ssh readable. Hence code roots, restricted to importable files.
    """
    import asyncio
    import sys

    from xdog.flow.executor import execute
    from xdog.flow.loader import parse_workflow

    (tmp_path / "helper.py").write_text("VALUE = 'sibling'\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not code\n", encoding="utf-8")
    (tmp_path / "lazymod.py").write_text(
        "def go(ctx):\n"
        "    import helper                      # lazy, inside the body\n"
        "    from pathlib import Path\n"
        "    try:\n"
        "        Path(__file__).parent.joinpath('secret.txt').read_text()\n"
        "        return 'READ DATA TOO'\n"
        "    except PermissionError:\n"
        "        return helper.VALUE\n",
        encoding="utf-8",
    )
    for name in ("helper", "lazymod"):
        sys.modules.pop(name, None)
    wf = parse_workflow({
        "name": "t", "provider": "p", "defaults": {"model": "m"}, "entry": "s",
        "nodes": [{"id": "s", "type": "script", "run": "lazymod:go", "outputs": ["r"]}],
        "edges": [{"from": "s", "to": "$output", "map": {"r": "result"}}],
    })

    result = asyncio.run(execute(wf, base_dir=tmp_path, workspace=tmp_path / "runtime", timeout=10))

    assert result.runtime["out"]["result"] == "sibling"


def test_import_roots_do_not_depend_on_sys_path(monkeypatch: Any, tmp_path: Path) -> None:
    """The divergence this fixed, at its source.

    Read roots were taken from all of `sys.path`, which is environment-dependent:
    a bundle run as `python <dir>` has its own directory at sys.path[0], so the
    compiled engine granted itself a root the interpreter never had and the same
    workflow behaved differently depending on how it was run.
    """
    import sys

    from xdog.agent.workspace import _import_roots

    monkeypatch.syspath_prepend(str(tmp_path))
    assert str(tmp_path) in sys.path

    assert not any(str(r) == str(tmp_path) for r in _import_roots()), (
        "an entry on sys.path must not become a readable root by itself"
    )


async def test_a_structured_node_keeps_its_briefing(monkeypatch: Any, tmp_path: Path) -> None:
    """The submit_result instruction used to be appended to the *raw* prompt,
    which silently dropped the workspace briefing for every structured node —
    and only in the interpreter, since codegen appends. The nodes doing the most
    work were the ones told nothing."""
    from xdog.flow.models import Port

    workspace = tmp_path / "runtime"
    import xdog.agent.agent as agent_module

    monkeypatch.setattr(agent_module, "Agent", _CapturingAgent)
    _CapturingAgent.prompt_seen = ""
    runner = SdkRunner(
        stream_fn_factory=lambda _m: object(),  # type: ignore[arg-type,return-value]
        tool_registry=ToolRegistry(),
        web_search_fn_factory=None,
        workspace=workspace,
    )
    structured = NodeDef(
        id="n",
        output_ports=(Port("a", schema={"type": "string"}), Port("b", schema={"type": "string"})),
    )
    try:
        await runner.run(
            structured, system_prompt="", user_prompt="go", model="m",
            timeout=5.0, inputs={}, step=0, fan_index=None,
        )
    except Exception:
        pass

    assert str(workspace) in _CapturingAgent.prompt_seen, "briefing survived"
    assert "submit_result" in _CapturingAgent.prompt_seen, "and so did the instruction"


def test_a_confined_install_actually_turns_confinement_on(tmp_path: Path) -> None:
    """The bundle gates on FLOW_CONFINED. Recording only FLOW_WORKSPACE set a
    directory the bundle would have defaulted to anyway, so `install --confined`
    refused unconfinable workflows and then ran the rest unconfined — a flag that
    reads as a guarantee and is inert is worse than no flag."""
    from xdog.flow.scheduler.systemd import ConfineGrant, render_timer_units

    wf = _scheduled({"id": "a", "type": "agent", "prompt": "go", "tools": ["filesystem"],
                     "outputs": ["out"]})
    grant = ConfineGrant(workspace=tmp_path / "runtime")

    service = render_timer_units("s", tmp_path / "b", wf.schedule, confine=grant).files["s.service"]  # type: ignore[arg-type]

    assert "Environment=FLOW_CONFINED=1" in service


def test_an_optional_loop_carried_port_does_not_break_the_compiled_engine() -> None:
    """`interpret == compile` for a port that is absent by design.

    The interpreter passes every declared port, defaulting an unfed one to "".
    The generated module builds `ins` from the edges that actually fired, so a
    non-required loop-carried port is simply missing on the first pass — and the
    node function required it positionally. The workflow ran fine interpreted and
    raised TypeError compiled, which surfaced inside a systemd timer: the
    environment least able to explain itself.
    """
    import asyncio

    from xdog.flow.codegen import generate
    from xdog.flow.executor import execute
    from xdog.flow.loader import parse_workflow

    wf = parse_workflow({
        "name": "opt", "provider": "p", "defaults": {"model": "m"}, "entry": "a",
        "nodes": [
            {"id": "a", "type": "script", "outputs": ["seed"],
             "code": "def a(ctx):\n    return 'go'"},
            {"id": "b", "type": "script",
             "inputs": [
                 {"name": "seed", "schema": {"type": "string"}},
                 {"name": "carried", "schema": {"type": "string"}, "required": False},
             ],
             "outputs": ["out"],
             "code": "def b(ctx, seed, carried):\n    return f'{seed}/{carried!r}'"},
        ],
        "edges": [
            {"from": "a", "to": "b", "map": {"seed": "seed"}},
            {"from": "b", "to": "$output", "map": {"out": "r"}},
        ],
    })

    interpreted = asyncio.run(execute(wf, timeout=10)).runtime["out"]["r"]

    namespace: dict[str, Any] = {}
    exec(compile(generate(wf), "<gen>", "exec"), namespace)  # noqa: S102 - our own output
    asyncio.run(namespace["main"]())

    assert namespace["_OUTPUT"]["r"] == interpreted


# -- skills on agent nodes ---------------------------------------------------


def test_a_node_can_name_a_skill_and_both_engines_render_it(monkeypatch: Any, tmp_path: Path) -> None:
    """An agent asked to produce a format it was never shown does not fail — it
    produces something plausible and wrong. That is not hypothetical: a
    scheduling workflow was written with an invented node type by an agent with
    no access to the skill describing the format, and every downstream check
    passed."""
    from xdog.agent.skills import packaged_skills
    from xdog.flow.codegen import generate
    from xdog.flow.loader import parse_workflow

    assert "flow" in packaged_skills(), "the skill must resolve from the package"

    wf = parse_workflow({
        "name": "s", "provider": "p", "defaults": {"model": "m"}, "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "go",
                   "skills": ["flow"], "outputs": ["o"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"o": "r"}}],
    })
    assert wf.nodes[0].skills == ("flow",)

    source = generate(wf)
    assert "skills=_skill_manager()" in source and "active_skills=skills" in source, (
        "the compiled engine hands a manager and slugs to the Agent, and places nothing"
    )
    assert "skills=('flow',)" in source, "with this node's skills"


def test_a_generated_module_does_not_import_flow_for_skills() -> None:
    """`skills_preamble` lives in xdog-agent for this reason: a bundle depends on
    the agent package when it has SDK nodes, and never on flow."""
    from xdog.flow.codegen import generate
    from xdog.flow.loader import parse_workflow

    wf = parse_workflow({
        "name": "s", "provider": "p", "defaults": {"model": "m"}, "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "go",
                   "skills": ["flow"], "outputs": ["o"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"o": "r"}}],
    })
    imports = [ln for ln in generate(wf).splitlines() if ln.startswith(("import ", "from "))]

    assert not any("xdog.flow" in ln for ln in imports), imports


def test_an_unknown_skill_is_refused_at_load() -> None:
    """Failing at load, not at run: the run would succeed and be wrong."""
    from xdog.flow.loader import parse_workflow, validation_errors

    wf = parse_workflow({
        "name": "s", "provider": "p", "defaults": {"model": "m"}, "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "go",
                   "skills": ["no-such-skill"], "outputs": ["o"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"o": "r"}}],
    })
    errors = validation_errors(wf)

    assert [e.code for e in errors] == ["unknown-reference"]
    assert "no-such-skill" in errors[0].args[0]


def test_skills_survive_a_round_trip() -> None:
    """`inherit` was dropped by the serializer once and only the example corpus
    caught it; a field that does not round-trip is a field that vanishes when a
    workflow is edited by the builder."""
    from xdog.flow.builder.serialize import workflow_to_dict
    from xdog.flow.loader import parse_workflow

    raw = {
        "name": "s", "provider": "p", "defaults": {"model": "m"}, "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "go",
                   "skills": ["flow"], "outputs": ["o"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"o": "r"}}],
    }
    again = parse_workflow(workflow_to_dict(parse_workflow(raw)))

    assert again.nodes[0].skills == ("flow",)


def test_a_skill_beside_the_workflow_resolves_and_travels(tmp_path: Path) -> None:
    """A `skills/` directory next to the workflow file is part of the artifact,
    like the sibling modules a `run:` node imports — so it does not reintroduce
    the problem that reading a machine's own skill directory would, where the
    same workflow behaves differently for two people and neither can tell from
    the file."""
    import json

    from xdog.flow.builder.io import load_any
    from xdog.flow.bundle import build_bundle

    (tmp_path / "skills" / "house-style").mkdir(parents=True)
    (tmp_path / "skills" / "house-style" / "SKILL.md").write_text(
        "---\nname: house-style\ndescription: local\n---\n\nNEVER use tabs.\n", encoding="utf-8"
    )
    (tmp_path / "wf.json").write_text(json.dumps({
        "name": "s", "provider": "p", "defaults": {"model": "m"}, "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "go",
                   "skills": ["house-style"], "outputs": ["o"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"o": "r"}}],
    }), encoding="utf-8")

    wf = load_any(str(tmp_path / "wf.json"))          # validates, so it resolved
    from xdog.agent.skills import render_skill_body, resolve_skills

    local = resolve_skills(["house-style"], [tmp_path / "skills"])
    assert local and "NEVER use tabs" in render_skill_body(local[0])

    build_bundle(wf, tmp_path / "bundle", base_dir=tmp_path)
    assert (tmp_path / "bundle" / "skills" / "house-style" / "SKILL.md").exists(), (
        "left behind, an installed bundle sends a shorter prompt than the same "
        "workflow run from its directory, and that shows up as worse output "
        "rather than as an error"
    )


def test_the_definition_does_not_carry_where_it_was_loaded_from() -> None:
    """`base_dir` is a fact about a run, not about the workflow.

    On the definition it would serialize a machine path into a shareable
    artifact and make two identical workflows unequal because of their
    directory — the same category error as a workflow declaring its own
    filesystem access.
    """
    import dataclasses

    from xdog.flow.models import WorkflowDef

    fields = {f.name for f in dataclasses.fields(WorkflowDef)}
    assert "base_dir" not in fields, sorted(fields)
