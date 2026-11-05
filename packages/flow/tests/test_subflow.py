"""Sub-workflow (G5) — interpreter behaviour and cross-engine parity.

A ``type="subflow"`` node runs an inline child workflow as one opaque node via the
same ``flow.executor.execute()``.  Its ports are derived from the child's signature
(not declared).  Both engines call the same ``execute()`` on the child, so child
semantics are byte-identical by construction.  See ``docs/subflow.md``.
"""

from __future__ import annotations

import asyncio
import pathlib
import types
from typing import Any

import pytest
from xdog.flow.codegen import generate
from xdog.flow.executor import execute
from xdog.flow.loader import load_workflow, parse_workflow


def _child() -> dict[str, Any]:
    """A script-only child: uppercases topic -> verdict (no LLM)."""
    return {
        "name": "up",
        "provider": "copilot",
        "entry": "u",
        "state": {"topic": "x"},
        "nodes": [
            {
                "id": "u",
                "type": "script",
                "inputs": [{"name": "topic", "schema": {"type": "string"}}],
                "code": "def u(ctx, topic):\n    return topic.upper()",
                "outputs": ["verdict"],
            }
        ],
        "edges": [
            {"from": "$in", "to": "u", "map": {"topic": "topic"}},
            {"from": "u", "to": "$output", "map": {"verdict": "verdict"}},
        ],
    }


def _parent(seed: str = "hello") -> dict[str, Any]:
    """plan -> subflow(child) -> pub, threading a seed string through."""
    return {
        "name": "main",
        "provider": "copilot",
        "entry": "plan",
        "state": {"seed": seed},
        "nodes": [
            {
                "id": "plan",
                "type": "script",
                "inputs": [{"name": "seed", "schema": {"type": "string"}}],
                "code": "def p(ctx, seed):\n    return seed + '!'",
                "outputs": ["topic"],
            },
            {"id": "review", "type": "subflow", "subflow": _child()},
            {
                "id": "pub",
                "type": "script",
                "inputs": [{"name": "verdict", "schema": {"type": "string"}}],
                "code": "def pub(ctx, verdict):\n    return '[' + verdict + ']'",
                "outputs": ["done"],
            },
        ],
        "edges": [
            {"from": "$in", "to": "plan", "map": {"seed": "seed"}},
            {"from": "plan", "to": "review", "map": {"topic": "topic"}},
            {"from": "review", "to": "pub", "map": {"verdict": "verdict"}},
            {"from": "pub", "to": "$output", "map": {"done": "result"}},
        ],
    }


# --- interpreter behaviour -------------------------------------------------


async def test_subflow_interpreter_e2e() -> None:
    wf = parse_workflow(_parent("hello"))
    r = await execute(wf, stream_fn_factory=lambda m: None)
    # hello -> plan 'hello!' -> child uppercases 'HELLO!' -> pub '[HELLO!]'
    assert r.runtime["state"]["review"] == {"verdict": "HELLO!"}
    assert r.runtime["out"]["result"] == "[HELLO!]"


# --- cross-engine parity (interpret == compile) ----------------------------


async def test_subflow_interpret_equals_compile() -> None:
    wf = parse_workflow(_parent("world"))
    interp = await execute(wf, stream_fn_factory=lambda m: None)

    src = generate(wf)
    compile(src, "<gen>", "exec")  # compiles

    mod = types.ModuleType("_gen_sub")
    exec(compile(src, "<gen_sub>", "exec"), mod.__dict__)  # noqa: S102
    await mod.main()  # type: ignore[attr-defined]
    gen_runtime: dict[str, Any] = mod._RUNTIME  # type: ignore[attr-defined]

    assert gen_runtime["state"] == dict(interp.runtime["state"])
    assert gen_runtime["out"] == dict(interp.runtime["out"])


def test_path_subflow_child_is_valid_python_literal() -> None:
    """Canonical booleans in an embedded path child must use Python literals."""
    example = pathlib.Path(__file__).parent.parent / "examples" / "essay_writer.json"
    source = generate(load_workflow(example))
    module = types.ModuleType("_essay_writer_literal")
    exec(compile(source, "<essay-writer>", "exec"), module.__dict__)  # noqa: S102
    child = module._CHILD_compose  # type: ignore[attr-defined]
    points = child["nodes"][0]["inputs"][1]
    assert points["required"] is True
    assert "'required': True" in source


# --- path-reference children (independent child files) ---------------------


def _write_child(dir_: pathlib.Path) -> pathlib.Path:
    import json

    p = dir_ / "child.json"
    p.write_text(json.dumps(_child()), encoding="utf-8")
    return p


def test_subflow_path_ref_loads_and_runs(tmp_path: pathlib.Path) -> None:
    """A "subflow": "./child.json" path ref resolves, derives ports, and runs."""
    import json

    from xdog.flow.loader import load_workflow

    _write_child(tmp_path)
    parent = _parent("hi")
    # replace the inline child with a path reference to the sibling file
    review = next(n for n in parent["nodes"] if n["id"] == "review")
    review["subflow"] = "./child.json"
    parent_path = tmp_path / "parent.json"
    parent_path.write_text(json.dumps(parent), encoding="utf-8")

    wf = load_workflow(parent_path)
    review_node = next(n for n in wf.nodes if n.id == "review")
    # ports derived from the referenced child, child inlined
    assert [p.name for p in review_node.input_ports] == ["topic"]
    assert [p.name for p in review_node.output_ports] == ["verdict"]
    assert review_node.child is not None and review_node.child.name == "up"

    r = asyncio.run(execute(wf, stream_fn_factory=lambda m: None))
    assert r.runtime["out"]["result"] == "[HI!]"


def test_subflow_path_ref_missing_file(tmp_path: pathlib.Path) -> None:
    import json

    from xdog.flow.errors import WorkflowValidationError
    from xdog.flow.loader import load_workflow

    parent = _parent()
    next(n for n in parent["nodes"] if n["id"] == "review")["subflow"] = "./nope.json"
    p = tmp_path / "parent.json"
    p.write_text(json.dumps(parent), encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="not found"):
        load_workflow(p)


def test_subflow_path_ref_cycle_rejected(tmp_path: pathlib.Path) -> None:
    """A -> B -> A subflow reference is caught at load time."""
    import json

    from xdog.flow.errors import WorkflowValidationError
    from xdog.flow.loader import load_workflow

    a = {"name": "a", "provider": "copilot", "entry": "s", "state": {"seed": "x"},
         "nodes": [{"id": "s", "type": "subflow", "subflow": "./b.json"}],
         "edges": [{"from": "$in", "to": "s", "map": {"seed": "seed"}},
                   {"from": "s", "to": "$output", "map": {"result": "result"}}]}
    b = {"name": "b", "provider": "copilot", "entry": "s", "state": {"seed": "x"},
         "nodes": [{"id": "s", "type": "subflow", "subflow": "./a.json"}],
         "edges": [{"from": "$in", "to": "s", "map": {"seed": "seed"}},
                   {"from": "s", "to": "$output", "map": {"result": "result"}}]}
    (tmp_path / "a.json").write_text(json.dumps(a), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(b), encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="cyclic subflow"):
        load_workflow(tmp_path / "a.json")


def test_subflow_path_ref_needs_base_dir() -> None:
    """A path ref cannot be resolved by bare parse_workflow (no base dir)."""
    from xdog.flow.errors import WorkflowValidationError

    parent = _parent()
    next(n for n in parent["nodes"] if n["id"] == "review")["subflow"] = "./x.json"
    with pytest.raises(WorkflowValidationError, match="base directory"):
        parse_workflow(parent)
