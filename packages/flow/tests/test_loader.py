"""Tests for flow.loader — load_workflow, parse_workflow, validate_workflow."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from xdog.flow.errors import FlowWarning, WorkflowValidationError
from xdog.flow.loader import load_workflow, parse_workflow, validate_workflow
from xdog.flow.models import EdgeDef, NodeDef, Port, WorkflowDef

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_linear_ok() -> None:
    wf = load_workflow(FIXTURES / "linear.json")
    assert isinstance(wf, WorkflowDef)
    assert wf.name == "linear-workflow"
    assert wf.entry == "a"
    assert wf.provider == "anthropic"
    assert wf.default_model == "claude-haiku-4-5"
    assert len(wf.nodes) == 3
    assert [n.id for n in wf.nodes] == ["a", "b", "c"]
    assert len(wf.edges) == 2
    assert wf.edges[0].src == "a" and wf.edges[0].dst == "b"
    assert wf.edges[1].src == "b" and wf.edges[1].dst == "c"
    assert wf.initial_state == (("result", ""),)


def test_crossing_loop_regions_are_rejected() -> None:
    wf = WorkflowDef(
        name="crossing-loops",
        provider="copilot",
        entry="a",
        nodes=tuple(NodeDef(id=node) for node in ("a", "x", "p", "b", "y")),
        edges=(
            EdgeDef(src="a", dst="p"),
            EdgeDef(src="x", dst="p"),
            EdgeDef(src="p", dst="b"),
            EdgeDef(src="p", dst="y"),
            EdgeDef(src="b", dst="a", loop_max=2),
            EdgeDef(src="y", dst="x", loop_max=2),
        ),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FlowWarning)
        with pytest.raises(WorkflowValidationError, match="cross without nesting"):
            validate_workflow(wf)


def test_cyclic_edge_without_loop_max_raises() -> None:
    data = {
        "name": "cyclic",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "a"},  # back-edge, no loop.max
        ],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="loop.max"):
        validate_workflow(wf)


def test_cyclic_edge_with_loop_max_ok() -> None:
    data = {
        "name": "loop-ok",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "a", "loop": {"max": 3}},
        ],
    }
    wf = parse_workflow(data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FlowWarning)  # unconditional loop; G6 warning tested elsewhere
        validate_workflow(wf)  # should not raise
    assert wf.edges[1].loop_max == 3


def test_loop_non_integer_max_raises_validation_error() -> None:
    """A non-integer loop 'max' raises WorkflowValidationError, not a raw ValueError."""
    data = {
        "name": "loop-bad-max",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "a", "loop": {"max": "abc"}},
        ],
    }
    with pytest.raises(WorkflowValidationError, match="must be an integer"):
        parse_workflow(data)


def test_duplicate_node_ids_raises() -> None:
    data = {
        "name": "dup",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}, {"id": "a"}],
        "edges": [],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="Duplicate"):
        validate_workflow(wf)


def test_edge_unknown_dst_raises() -> None:
    data = {
        "name": "bad-dst",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a"}],
        "edges": [{"from": "a", "to": "z"}],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="dst"):
        validate_workflow(wf)


def test_output_sink_unknown_source_port_raises() -> None:
    """An $output edge whose source port does not exist fails validation."""
    data = {
        "name": "out-bad",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a", "outputs": ["r"]}],
        "edges": [{"from": "a", "to": "$output", "map": {"nope": "result"}}],
    }
    with pytest.raises(WorkflowValidationError, match="output port"):
        validate_workflow(parse_workflow(data))


def test_non_optional_unfed_port_still_raises() -> None:
    """Marking one port optional does not excuse a sibling required port."""
    data = {
        "name": "opt-mixed",
        "provider": "anthropic",
        "entry": "a",
        "nodes": [{"id": "a", "inputs": [{"name": "ok", "schema": {"type": "string"}, "required": False}, "required"]}],
        "edges": [],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="'required' is not fed"):
        validate_workflow(wf)


def test_two_unconditional_edges_into_one_port_raises() -> None:
    """Two producers feeding the same input port unconditionally is ambiguous.

    This is the clash the flat shared-state model used to allow silently (two
    nodes writing the same key, last-writer-wins by completion order).  The port
    model rejects it at validate time.
    """
    data = {
        "name": "ambiguous",
        "provider": "copilot",
        "entry": "p",
        "nodes": [
            {
                "id": "p",
                "type": "script",
                "code": "def p(ctx):\n    return 1",
                "outputs": [{"name": "z", "schema": {"type": "integer"}}],
            },
            {
                "id": "q",
                "type": "script",
                "code": "def q(ctx):\n    return 2",
                "outputs": [{"name": "z", "schema": {"type": "integer"}}],
            },
            {
                "id": "end",
                "type": "script",
                "inputs": [{"name": "v", "schema": {"type": "integer"}}],
                "code": "def end(ctx, v):\n    return v",
                "outputs": [{"name": "r", "schema": {"type": "integer"}}],
            },
        ],
        "edges": [
            {"from": "p", "to": "end", "map": {"z": "v"}},
            {"from": "q", "to": "end", "map": {"z": "v"}},
        ],
    }
    wf = parse_workflow(data)
    with pytest.raises(WorkflowValidationError, match="ambiguous producer"):
        validate_workflow(wf)


# --- typed inline script nodes (code XOR run, signature validation) ----------


def _wf_with_script(node: dict) -> dict:
    return {
        "name": "t",
        "provider": "copilot",
        "entry": node["id"],
        "nodes": [node],
        "edges": [],
    }


def test_inline_script_parses_typed_io() -> None:
    from xdog.flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(
        {
            "name": "t",
            "provider": "copilot",
            "entry": "add",
            "state": {"a": "3", "b": "4"},  # inputs reachable from initial state
            "nodes": [
                {
                    "id": "add",
                    "type": "script",
                    "code": "def add(ctx, a, b):\n    return a + b",
                    "inputs": [
                        {"name": "a", "schema": {"type": "integer"}},
                        {"name": "b", "schema": {"type": "integer"}},
                    ],
                    "outputs": [{"name": "sum", "schema": {"type": "integer"}}],
                }
            ],
            "edges": [
                {"from": "$in", "to": "add", "map": {"a": "a", "b": "b"}},
            ],
        }
    )
    validate_workflow(wf)  # must not raise
    n = wf.nodes[0]
    assert n.input_names == ("a", "b")
    assert n.input_ports == (Port("a", schema={"type": "integer"}), Port("b", schema={"type": "integer"}))
    assert n.output_names == ("sum",)
    assert n.output_ports == (Port("sum", schema={"type": "integer"}),)
    assert n.code is not None


def test_inline_bad_syntax_raises() -> None:
    from xdog.flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(
        _wf_with_script(
            {
                "id": "x",
                "type": "script",
                "code": "def x(ctx):\n    return (",
                "outputs": ["y"],
            }
        )
    )
    with pytest.raises(WorkflowValidationError, match="invalid code"):
        validate_workflow(wf)


def test_script_code_and_run_both_set_raises() -> None:
    from xdog.flow.loader import parse_workflow, validate_workflow

    wf = parse_workflow(
        _wf_with_script(
            {
                "id": "x",
                "type": "script",
                "code": "def x(ctx):\n    return ''",
                "run": "m:f",
                "outputs": ["y"],
            }
        )
    )
    with pytest.raises(WorkflowValidationError, match="not both"):
        validate_workflow(wf)


# --- custom tool manifest ----------------------------------------------------


def _wf_with_tools(tools: dict[str, str], node_tools: list[str]) -> dict[str, object]:
    return {
        "name": "tw",
        "provider": "fake",
        "defaults": {"model": "m"},
        "entry": "a",
        "tools": tools,
        "nodes": [{"id": "a", "type": "agent", "model": "m", "prompt": "p", "tools": node_tools}],
        "edges": [],
    }


def test_validate_unknown_tool_name_fails_fast() -> None:
    wf = parse_workflow(_wf_with_tools({"reverse": "mytools:make_reverse"}, ["revrese"]))
    with pytest.raises(WorkflowValidationError, match="unknown tool 'revrese'"):
        validate_workflow(wf)


# --- Multi-output agent validation + port-type whitelist -------------------


def _agent_wf(node: dict[str, object]) -> dict[str, object]:
    """A minimal single-agent workflow around *node* (seeded with a topic input)."""
    return {
        "name": "agent-wf",
        "provider": "copilot",
        "entry": "n1",
        "defaults": {"model": "m"},
        "state": {"topic": "x"},
        "nodes": [node],
        "edges": [{"from": "$in", "to": "n1", "map": {"topic": "topic"}}],
    }


def test_validate_multi_output_agent_ok() -> None:
    # A multi-output agent needs no separately-authored schema: its ports ARE the
    # schema, so it validates fine without any ``output_schema`` key.
    node = {
        "id": "n1",
        "type": "agent",
        "inputs": ["topic"],
        "prompt": "go",
        "outputs": [{"name": "a", "schema": {"type": "string"}}, {"name": "b", "schema": {"type": "array"}}],
    }
    wf = parse_workflow(_agent_wf(node))
    validate_workflow(wf)  # no raise


# --- Cycle detection (#4) --------------------------------------------------


# --- Edge type consistency (2a) --------------------------------------------


def _typed_two_node_wf(src_out_type: str, dst_in_type: str) -> dict[str, object]:
    """A -> B where A's output port and B's input port carry the given types."""
    return {
        "name": "typed",
        "provider": "copilot",
        "entry": "a",
        "defaults": {"model": "m"},
        "state": {"seed": "x"},
        "nodes": [
            {
                "id": "a",
                "type": "script",
                "inputs": [{"name": "seed", "schema": {"type": "string"}}],
                "code": "def a(ctx, seed):\n    return seed",
                "outputs": [{"name": "oa", "schema": {"type": src_out_type}}],
            },
            {
                "id": "b",
                "type": "script",
                "inputs": [{"name": "ib", "schema": {"type": dst_in_type}}],
                "code": "def b(ctx, ib):\n    return ib",
                "outputs": ["ob"],
            },
        ],
        "edges": [
            {"from": "$in", "to": "a", "map": {"seed": "seed"}},
            {"from": "a", "to": "b", "map": {"oa": "ib"}},
        ],
    }


def test_validate_edge_type_mismatch_fails() -> None:
    wf = parse_workflow(_typed_two_node_wf("array", "integer"))
    with pytest.raises(WorkflowValidationError, match="type mismatch"):
        validate_workflow(wf)


# --- Sub-field mapping validation (2b-1) -----------------------------------


def _subfield_wf(map_obj: dict[str, str], dst: str = "b") -> dict[str, object]:
    """A (object port 'plan') -> B, with the given edge map (source keys may be dotted)."""
    return {
        "name": "subfield",
        "provider": "copilot",
        "entry": "a",
        "defaults": {"model": "m"},
        "state": {"seed": "x"},
        "nodes": [
            {
                "id": "a",
                "type": "script",
                "inputs": [{"name": "seed", "schema": {"type": "string"}}],
                "code": "def a(ctx, seed):\n    return {'owner': seed}",
                "outputs": [{"name": "plan", "schema": {"type": "object"}}],
            },
            {
                "id": "b",
                "type": "script",
                "inputs": [{"name": "who", "schema": {"type": "string"}}],
                "code": "def b(ctx, who):\n    return who",
                "outputs": ["ob"],
            },
        ],
        "edges": [
            {"from": "$in", "to": "a", "map": {"seed": "seed"}},
            {"from": "a", "to": dst, "map": map_obj},
        ],
    }


# --- Sub-field TYPE checking (2b-2) ----------------------------------------


def _typed_subfield_wf(plan_schema: dict[str, object], src_key: str, dst_type: str) -> dict[str, object]:
    """A (structured port 'plan') -> B, mapping *src_key* into a *dst_type* input."""
    return {
        "name": "typed-subfield",
        "provider": "copilot",
        "entry": "a",
        "defaults": {"model": "m"},
        "state": {"seed": "x"},
        "nodes": [
            {
                "id": "a",
                "type": "script",
                "inputs": [{"name": "seed", "schema": {"type": "string"}}],
                "code": "def a(ctx, seed):\n    return {}",
                "outputs": [{"name": "plan", "schema": plan_schema}],
            },
            {
                "id": "b",
                "type": "script",
                "inputs": [{"name": "s", "schema": {"type": dst_type}}],
                "code": "def b(ctx, s):\n    return s",
                "outputs": ["ob"],
            },
        ],
        "edges": [
            {"from": "$in", "to": "a", "map": {"seed": "seed"}},
            {"from": "a", "to": "b", "map": {src_key: "s"}},
        ],
    }


_OBJ_SCHEMA: dict[str, object] = {"type": "object", "properties": {"n": {"type": "integer"}}}
_ARR_SCHEMA: dict[str, object] = {"type": "array", "items": {"type": "string"}}


def test_validate_subfield_type_mismatch_fails() -> None:
    # plan.n is integer; wired into a string input -> mismatch
    wf = parse_workflow(_typed_subfield_wf(_OBJ_SCHEMA, "$.plan.n", "string"))
    with pytest.raises(WorkflowValidationError, match="type mismatch"):
        validate_workflow(wf)


# --- Strict interpolation (P6-2) -------------------------------------------


def _agent_prompt_wf(prompt: str, inputs: list[object]) -> dict[str, object]:
    """A single agent node with the given prompt + declared input ports, fed by $in."""
    return {
        "name": "strict",
        "provider": "copilot",
        "entry": "a",
        "defaults": {"model": "m"},
        "state": {"topic": "x"},
        "nodes": [{"id": "a", "type": "agent", "prompt": prompt, "inputs": inputs, "outputs": ["out"]}],
        "edges": [{"from": "$in", "to": "a", "map": {"topic": "topic"}}],
    }


def test_strict_prompt_unknown_root_fails() -> None:
    # {{ $.nope }} — no such input port on the node
    wf = parse_workflow(_agent_prompt_wf("say {{ $.nope }}", ["topic"]))
    with pytest.raises(WorkflowValidationError, match="is not a declared input port"):
        validate_workflow(wf)


# --- Dynamic fan-out (G1) --------------------------------------------------


def _map_reduce_wf(mutate=None) -> dict:
    """A valid map-reduce workflow: plan -> (fan_out) work -> (fan_in) merge."""
    data = {
        "name": "mr",
        "provider": "copilot",
        "entry": "plan",
        "state": {"topic": "x"},
        "nodes": [
            {
                "id": "plan",
                "type": "script",
                "inputs": ["topic"],
                "code": "def p(ctx, topic):\n    return [topic + str(i) for i in range(3)]",
                "outputs": [{"name": "tasks", "schema": {"type": "array", "items": {"type": "string"}}}],
            },
            {"id": "work", "type": "agent", "prompt": "do {{task}}", "inputs": ["task"], "outputs": ["res"]},
            {
                "id": "merge",
                "type": "script",
                "inputs": [{"name": "results", "schema": {"type": "array", "items": {"type": "string"}}}],
                "code": "def m(ctx, results):\n    return ','.join(results)",
                "outputs": ["summary"],
            },
        ],
        "edges": [
            {"from": "$in", "to": "plan", "map": {"topic": "topic"}},
            {"from": "plan", "to": "work", "fan_out": "tasks", "map": {"tasks": "task"}},
            {"from": "work", "to": "merge", "fan_in": "list", "map": {"res": "results"}},
            {"from": "merge", "to": "$output", "map": {"summary": "summary"}},
        ],
    }
    if mutate is not None:
        mutate(data)
    return data


def test_fan_out_map_reduce_validates() -> None:
    validate_workflow(parse_workflow(_map_reduce_wf()))  # no raise


def test_fan_out_on_non_array_port_raises() -> None:
    def mut(d: dict) -> None:
        d["nodes"][0]["outputs"] = ["tasks"]  # plain string, not array

    with pytest.raises(WorkflowValidationError, match="must be an array output"):
        validate_workflow(parse_workflow(_map_reduce_wf(mut)))


def test_fan_in_from_non_worker_raises() -> None:
    def mut(d: dict) -> None:
        d["edges"][1].pop("fan_out")  # work no longer a fan-out worker

    # Removing fan_out makes the plan->work edge a plain array->string mismatch;
    # fix the worker input to array so the fan_in-source check is what fires.
    def mut2(d: dict) -> None:
        d["edges"][1].pop("fan_out")
        d["nodes"][1]["inputs"] = [{"name": "task", "schema": {"type": "array"}}]

    with pytest.raises(WorkflowValidationError, match="is not a fan-out worker"):
        validate_workflow(parse_workflow(_map_reduce_wf(mut2)))


# --- G6: unconditional-loop warning ----------------------------------------
