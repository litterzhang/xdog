"""CLI agent backend (docs/cli-agent.md) — model + loader (Phase 2).

An agent node may set ``backend`` (claude-cli/codex-cli) to run via an external
CLI instead of the SDK. A CLI agent node needs no provider, may narrow the CLI's
toolset with ``allowed_tools``, and may declare ``mcp_servers`` (an opaque
pass-through spec). Provider is required only when an SDK agent node is present.
"""

from __future__ import annotations

import json
import pathlib
import stat
import subprocess
import sys
import types
from typing import Any

import pytest
from xdog.flow.builder.serialize import workflow_to_dict
from xdog.flow.codegen import generate
from xdog.flow.errors import WorkflowValidationError
from xdog.flow.executor import execute
from xdog.flow.loader import parse_workflow, validate_workflow


def _cli_wf(**node_extra: Any) -> dict[str, Any]:
    node = {"id": "a", "type": "agent", "backend": "claude-cli", "prompt": "hi", "outputs": ["out"]}
    node.update(node_extra)
    return {
        "name": "cli",
        "entry": "a",
        "nodes": [node],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "result"}}],
    }


def test_pure_cli_workflow_needs_no_provider() -> None:
    validate_workflow(parse_workflow(_cli_wf()))  # no raise, no provider


def test_sdk_agent_without_provider_rejected() -> None:
    d = {
        "name": "sdk",
        "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "hi", "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "result"}}],
    }
    with pytest.raises(WorkflowValidationError, match="no 'provider'"):
        validate_workflow(parse_workflow(d))


def test_unknown_backend_rejected() -> None:
    with pytest.raises(WorkflowValidationError, match="unknown backend"):
        validate_workflow(parse_workflow(_cli_wf(backend="gpt-cli")))


def test_allowed_tools_without_backend_rejected() -> None:
    d = {
        "name": "x",
        "provider": "copilot",
        "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "hi", "allowed_tools": ["Read"], "outputs": ["o"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"o": "r"}}],
    }
    with pytest.raises(WorkflowValidationError, match="require a CLI"):
        validate_workflow(parse_workflow(d))


def test_mcp_servers_without_backend_rejected() -> None:
    d = {
        "name": "x",
        "provider": "copilot",
        "entry": "a",
        "nodes": [
            {"id": "a", "type": "agent", "prompt": "hi", "mcp_servers": {"gh": {"url": "https://x"}}, "outputs": ["o"]}
        ],
        "edges": [{"from": "a", "to": "$output", "map": {"o": "r"}}],
    }
    with pytest.raises(WorkflowValidationError, match="require a CLI"):
        validate_workflow(parse_workflow(d))


def test_mcp_server_spec_must_be_object() -> None:
    with pytest.raises(WorkflowValidationError, match="must be an object"):
        parse_workflow(_cli_wf(mcp_servers={"gh": "not-an-object"}))


def test_cli_fields_parsed() -> None:
    wf = parse_workflow(
        _cli_wf(
            allowed_tools=["Read", "mcp__github__create_issue"],
            mcp_servers={
                "github": {"command": "npx", "args": ["-y", "@mcp/github"], "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}}
            },
        )
    )
    n = wf.nodes[0]
    assert n.backend == "claude-cli"
    assert n.allowed_tools == ("Read", "mcp__github__create_issue")
    assert n.mcp_servers[0][0] == "github"
    # the spec dict is stored verbatim (opaque pass-through)
    assert n.mcp_servers[0][1]["command"] == "npx"
    assert n.mcp_servers[0][1]["env"] == {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}


def test_cli_node_roundtrips() -> None:
    wf = parse_workflow(
        _cli_wf(
            allowed_tools=["Read"],
            mcp_servers={"github": {"command": "npx", "args": ["-y", "@mcp/github"]}},
        )
    )
    assert parse_workflow(workflow_to_dict(wf)) == wf


def test_mixed_sdk_and_cli_agents_ok_with_provider() -> None:
    """A workflow may mix an SDK agent (needs provider) and a CLI agent."""
    d = {
        "name": "mixed",
        "provider": "copilot",
        "entry": "sdk",
        "nodes": [
            {"id": "sdk", "type": "agent", "prompt": "hi", "outputs": ["mid"]},
            {
                "id": "cli",
                "type": "agent",
                "backend": "claude-cli",
                "inputs": ["mid"],
                "prompt": "{{mid}}",
                "outputs": ["out"],
            },
        ],
        "edges": [
            {"from": "sdk", "to": "cli", "map": {"mid": "mid"}},
            {"from": "cli", "to": "$output", "map": {"out": "result"}},
        ],
    }
    validate_workflow(parse_workflow(d))  # no raise


# --- Phase 3: CLI runner + claude adapter (interpreter) --------------------

_FAKE_CLAUDE = """#!/usr/bin/env python3
import sys, json
argv = sys.argv[1:]
prompt = sys.stdin.read()
schema = model = allowed = sysp = None
i = 0
while i < len(argv):
    a = argv[i]
    if a == "--json-schema": schema = argv[i+1]; i += 2; continue
    if a == "--model": model = argv[i+1]; i += 2; continue
    if a == "--allowedTools": allowed = argv[i+1]; i += 2; continue
    if a == "--append-system-prompt": sysp = argv[i+1]; i += 2; continue
    i += 1
env = {"input_tokens": 11, "output_tokens": 7}
if schema is not None:
    props = json.loads(schema).get("properties", {})
    obj = {}
    for k, v in props.items():
        t = (v or {}).get("type", "string")
        obj[k] = {"string":"S","integer":1,"number":1.0,"boolean":True,"array":[],"object":{}}.get(t,"S")
    env["structured_output"] = obj
else:
    env["result"] = "TEXT|model=%s|tools=%s|sys=%s|prompt=%s" % (model, allowed, sysp, prompt.strip())
print(json.dumps(env))
"""


def _install_fake_cli(tmp_path: pathlib.Path, script: str, name: str = "fake_claude") -> str:
    """Write a fake CLI executable and return its path (for FLOW_CLI_BIN)."""
    py = tmp_path / (name + ".py")
    py.write_text(script, encoding="utf-8")
    sh = tmp_path / name
    sh.write_text(f'#!/usr/bin/env bash\nexec python3 "{py}" "$@"\n', encoding="utf-8")
    sh.chmod(sh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(sh)


async def test_cli_runner_text_agent(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, _FAKE_CLAUDE))
    wf = parse_workflow(_cli_wf(model="sonnet", allowed_tools=["Read", "WebSearch"]))
    r = await execute(wf)
    out = r.runtime["out"]["result"]
    assert out.startswith("TEXT|model=sonnet|tools=Read,WebSearch|")
    assert r.runtime["tokens_used"] == 18  # 11 + 7


async def test_cli_runner_structured_agent(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, _FAKE_CLAUDE))
    d = {
        "name": "s",
        "entry": "a",
        "nodes": [
            {
                "id": "a",
                "type": "agent",
                "backend": "claude-cli",
                "prompt": "plan",
                "outputs": [
                    {"name": "title", "schema": {"type": "string"}},
                    {"name": "n", "schema": {"type": "integer"}},
                ],
            }
        ],
        "edges": [{"from": "a", "to": "$output", "map": {"title": "title", "n": "n"}}],
    }
    r = await execute(parse_workflow(d))
    assert r.runtime["out"] == {"title": "S", "n": 1}
    assert r.runtime["state"]["a"] == {"title": "S", "n": 1}


async def test_cli_runner_no_provider_needed(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    """A pure-CLI workflow runs with no provider and never touches ai/agent."""
    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, _FAKE_CLAUDE))
    wf = parse_workflow(_cli_wf())  # no provider in the dict
    assert wf.provider == ""
    r = await execute(wf)  # would fail if it tried ai.provider("")
    assert "result" in r.runtime["out"]


async def test_cli_runner_nonzero_exit_raises(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    from xdog.flow.errors import WorkflowExecutionError

    bad = """#!/usr/bin/env python3
import sys
sys.stderr.write("boom")
sys.exit(3)
"""
    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, bad, name="bad_claude"))
    with pytest.raises(WorkflowExecutionError, match="exited 3: boom"):
        await execute(parse_workflow(_cli_wf()))


# --- Phase 4: codex adapter ------------------------------------------------

_FAKE_CODEX = """#!/usr/bin/env python3
import sys, json
argv = sys.argv[1:]
prompt = sys.stdin.read()
schema_file = model = None
i = 0
while i < len(argv):
    a = argv[i]
    if a == "--output-schema": schema_file = argv[i+1]; i += 2; continue
    if a == "-m": model = argv[i+1]; i += 2; continue
    i += 1
if schema_file is not None:
    props = json.loads(open(schema_file).read()).get("properties", {})
    obj = {}
    for k, v in props.items():
        t = (v or {}).get("type", "string")
        obj[k] = {"string":"S","integer":1,"number":1.0,"boolean":True,"array":[],"object":{}}.get(t,"S")
    text = json.dumps(obj)
else:
    text = "CODEX|model=%s|prompt=%s" % (model, prompt.strip().replace(chr(10), "\\\\n"))
# JSONL event stream
print(json.dumps({"type": "thread.started"}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 9}}))
"""


def _codex_wf(**node_extra: Any) -> dict[str, Any]:
    node = {"id": "a", "type": "agent", "backend": "codex-cli", "prompt": "hi", "outputs": ["out"]}
    node.update(node_extra)
    return {
        "name": "cx",
        "entry": "a",
        "nodes": [node],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "result"}}],
    }


async def test_codex_text_agent(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("FLOW_CLI_BIN_CODEX_CLI", _install_fake_cli(tmp_path, _FAKE_CODEX, name="fake_codex"))
    wf = parse_workflow(_codex_wf(model="gpt-5-codex"))
    r = await execute(wf)
    assert r.runtime["out"]["result"].startswith("CODEX|model=gpt-5-codex|")
    assert r.runtime["tokens_used"] == 14  # 5 + 9 from JSONL usage


async def test_codex_folds_system_prompt_into_prompt(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    """codex has no system-prompt flag; the adapter folds it into stdin."""
    monkeypatch.setenv("FLOW_CLI_BIN_CODEX_CLI", _install_fake_cli(tmp_path, _FAKE_CODEX, name="fake_codex"))
    wf = parse_workflow(_codex_wf(system_prompt="You are terse.", prompt="ping"))
    r = await execute(wf)
    # the fake echoes the prompt; the system prompt must appear folded in
    assert "You are terse." in r.runtime["out"]["result"]
    assert "ping" in r.runtime["out"]["result"]


async def test_codex_structured_agent(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("FLOW_CLI_BIN_CODEX_CLI", _install_fake_cli(tmp_path, _FAKE_CODEX, name="fake_codex"))
    d = {
        "name": "s",
        "entry": "a",
        "nodes": [
            {
                "id": "a",
                "type": "agent",
                "backend": "codex-cli",
                "prompt": "plan",
                "outputs": [
                    {"name": "title", "schema": {"type": "string"}},
                    {"name": "n", "schema": {"type": "integer"}},
                ],
            }
        ],
        "edges": [{"from": "a", "to": "$output", "map": {"title": "title", "n": "n"}}],
    }
    r = await execute(parse_workflow(d))
    assert r.runtime["out"] == {"title": "S", "n": 1}


# --- Phase 5: codegen CLI agent + parity (interpret == compile) ------------


async def test_cli_agent_interpret_equals_compile(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    """A CLI agent node produces identical output through execute() and codegen."""
    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, _FAKE_CLAUDE))
    d = {
        "name": "p",
        "entry": "a",
        "nodes": [
            {
                "id": "a",
                "type": "agent",
                "backend": "claude-cli",
                "model": "sonnet",
                "prompt": "hi",
                "allowed_tools": ["Read"],
                "outputs": [
                    {"name": "title", "schema": {"type": "string"}},
                    {"name": "n", "schema": {"type": "integer"}},
                ],
            }
        ],
        "edges": [{"from": "a", "to": "$output", "map": {"title": "title", "n": "n"}}],
    }
    wf = parse_workflow(d)
    interp = await execute(wf)
    src = generate(wf)
    compile(src, "<g>", "exec")
    mod = types.ModuleType("_gen_cli")
    exec(compile(src, "<g>", "exec"), mod.__dict__)  # noqa: S102
    await mod.main()  # type: ignore[attr-defined]
    gen = mod._RUNTIME  # type: ignore[attr-defined]
    assert gen["out"] == dict(interp.runtime["out"])
    assert gen["state"] == dict(interp.runtime["state"])


def test_cli_agent_codegen_calls_subprocess() -> None:
    src = generate(parse_workflow(_cli_wf(backend="claude-cli", allowed_tools=["Read"])))
    assert "_run_cli_agent('claude-cli'" in src
    assert "create_subprocess_exec" in src  # the inlined CLI helper


def test_non_cli_workflow_emits_no_subprocess() -> None:
    """A script/SDK workflow's module must not contain the CLI subprocess helper call."""
    d = {
        "name": "plain",
        "provider": "copilot",
        "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "hi", "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "result"}}],
    }
    src = generate(parse_workflow(d))
    assert "_run_cli_agent('" not in src  # no CLI node -> no CLI call emitted (helper may be defined)


def test_cli_agent_generated_module_ruff_clean(tmp_path: pathlib.Path) -> None:
    src = generate(parse_workflow(_cli_wf(allowed_tools=["Read"])))
    p = tmp_path / "cli_mod.py"
    p.write_text(src, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--line-length", "120", str(p)],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"ruff failed:\n{r.stdout}\n{r.stderr}"


# --- Phase 6: MCP server config + ${ENV} interpolation ---------------------

_FAKE_CLAUDE_MCP = """#!/usr/bin/env python3
import sys, json
argv = sys.argv[1:]
sys.stdin.read()
mcp = None
i = 0
while i < len(argv):
    if argv[i] == "--mcp-config": mcp = argv[i+1]; i += 2; continue
    i += 1
cfg = open(mcp).read() if mcp else "NONE"
print(json.dumps({"input_tokens": 1, "output_tokens": 1, "result": cfg}))
"""


async def test_mcp_config_generated_with_env_interpolation(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, _FAKE_CLAUDE_MCP, name="fc_mcp"))
    monkeypatch.setenv("GITHUB_TOKEN", "secret-tok")
    wf = parse_workflow(
        _cli_wf(
            mcp_servers={"github": {"command": "npx", "env": {"TOKEN": "${GITHUB_TOKEN}"}}},
        )
    )
    r = await execute(wf)
    cfg = r.runtime["out"]["result"]
    assert "secret-tok" in cfg  # resolved from env
    assert "${" not in cfg  # the reference is gone (no plaintext secret in the spec)
    assert '"mcpServers"' in cfg


async def test_mcp_config_unset_env_fails_fast(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    from xdog.flow.errors import WorkflowExecutionError

    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, _FAKE_CLAUDE_MCP, name="fc_mcp2"))
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    wf = parse_workflow(_cli_wf(mcp_servers={"gh": {"env": {"T": "${NOPE_TOKEN}"}}}))
    with pytest.raises(WorkflowExecutionError, match="NOPE_TOKEN"):
        await execute(wf)


async def test_mcp_config_parity(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    """Both engines generate byte-identical MCP config from the same spec."""
    monkeypatch.setenv("FLOW_CLI_BIN_CLAUDE_CLI", _install_fake_cli(tmp_path, _FAKE_CLAUDE_MCP, name="fc_mcp3"))
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    wf = parse_workflow(_cli_wf(mcp_servers={"github": {"command": "npx", "env": {"TOKEN": "${GITHUB_TOKEN}"}}}))
    interp = await execute(wf)
    src = generate(wf)
    mod = types.ModuleType("_gen_mcp")
    exec(compile(src, "<g>", "exec"), mod.__dict__)  # noqa: S102
    await mod.main()  # type: ignore[attr-defined]
    assert mod._RUNTIME["out"] == dict(interp.runtime["out"])  # type: ignore[attr-defined]


def test_claude_mcp_config_helper() -> None:
    import os as _os

    from xdog.flow.runners import claude_mcp_config

    _os.environ["_FLOW_TEST_TOK"] = "xyz"
    cfg = claude_mcp_config((("gh", {"command": "npx", "env": {"K": "${_FLOW_TEST_TOK}"}}),))
    parsed = json.loads(cfg)
    assert parsed["mcpServers"]["gh"]["env"]["K"] == "xyz"


def test_codex_mcp_config_helper() -> None:
    from xdog.flow.runners import codex_mcp_config

    toml = codex_mcp_config((("gh", {"command": "npx", "args": ["-y", "srv"]}),))
    assert "[mcp_servers.gh]" in toml
    assert 'command = "npx"' in toml
    assert 'args = ["-y", "srv"]' in toml


# --- Phase 6b: conditional SDK imports + lean bundle -----------------------


def test_pure_cli_module_imports_no_agent_ai() -> None:
    src = generate(parse_workflow(_cli_wf()))
    assert "import ai" not in src
    assert "from agent" not in src
    assert "class ToolRepo" not in src
    assert "async def _run_agent" not in src


def test_script_only_module_imports_no_agent_ai() -> None:
    d = {
        "name": "s",
        "entry": "a",
        "nodes": [
            {
                "id": "a",
                "type": "script",
                "inputs": [{"name": "x", "schema": {"type": "string"}}],
                "code": "def a(ctx, x):\n    return x.upper()",
                "outputs": ["y"],
            }
        ],
        "edges": [{"from": "$in", "to": "a", "map": {"x": "x"}}, {"from": "a", "to": "$output", "map": {"y": "r"}}],
        "state": {"x": "hi"},
    }
    src = generate(parse_workflow(d))
    assert "import ai" not in src and "from agent" not in src


def test_sdk_module_still_imports_agent_ai() -> None:
    d = {
        "name": "sdk",
        "provider": "copilot",
        "entry": "a",
        "nodes": [{"id": "a", "type": "agent", "prompt": "hi", "outputs": ["out"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"out": "r"}}],
    }
    src = generate(parse_workflow(d))
    assert "from xdog import ai" in src and "from xdog.agent import Agent" in src
    assert "from xdog.agent import Agent, ToolRepo" in src
    assert "class ToolRepo" not in src


def test_pure_cli_bundle_drops_ai_agent(tmp_path: pathlib.Path) -> None:
    from xdog.flow.bundle import build_bundle

    out = build_bundle(parse_workflow(_cli_wf()), tmp_path / "bundle")
    vendor = out / "_vendor"
    assert not (vendor / "ai").exists()
    assert not (vendor / "agent").exists()
    reqs = (out / "requirements.txt").read_text()
    assert "httpx" not in reqs and "pydantic" not in reqs
    assert "jsonpath-ng" in reqs


# --- regression: real claude nests tokens under "usage" (found by dogfooding) ---

_FAKE_CLAUDE_REAL_ENVELOPE = """#!/usr/bin/env python3
import sys, json
sys.stdin.read()
# mirror the REAL claude -p --output-format json envelope: tokens under "usage"
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "result": "hi there",
    "usage": {"input_tokens": 123, "output_tokens": 45},
    "total_cost_usd": 0.01,
}))
"""


async def test_cli_runner_reads_tokens_from_usage(tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    """Real claude puts tokens under env['usage'], not top-level (regression)."""
    monkeypatch.setenv(
        "FLOW_CLI_BIN_CLAUDE_CLI",
        _install_fake_cli(tmp_path, _FAKE_CLAUDE_REAL_ENVELOPE, name="fake_claude_real"),
    )
    r = await execute(parse_workflow(_cli_wf()))
    assert r.runtime["out"]["result"] == "hi there"
    assert r.runtime["tokens_used"] == 168  # 123 + 45 from usage, not 0
