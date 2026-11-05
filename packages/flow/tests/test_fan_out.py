"""Dynamic fan-out (G1) — interpreter behaviour and cross-engine parity.

A ``fan_out`` edge runs its worker node once per element of the source array; each
worker output port is aggregated into an index-ordered list stored under the single
worker node id.  A downstream ``fan_in`` edge reads that list as a plain mapping.

From the scheduler's view the fan group is ONE node (one completed id, one trace
frame), so the static-graph property — and ``interpret == compile`` — holds.
See ``docs/fan-out.md``.
"""

from __future__ import annotations

import asyncio
import pathlib
import types
from typing import Any

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent
from xdog.ai.utils.event_stream import EventStream as AiEventStream
from xdog.flow.codegen import generate
from xdog.flow.executor import execute
from xdog.flow.loader import parse_workflow


def _map_reduce(n: int, cap: int = 0, worker: str = "script", fan_cap: int = 0) -> dict[str, Any]:
    """A map-reduce workflow with a runtime-sized fan-out of *n* elements.

    ``worker='script'`` uppercases each element (no LLM); ``worker='agent'`` echoes
    the stubbed model text so the same graph exercises the agent path.  ``fan_cap``
    caps how many worker instances run at once (0 = unlimited).
    """
    if worker == "script":
        work_node = {
            "id": "work",
            "type": "script",
            "inputs": ["task"],
            "code": "def w(ctx, task):\n    return task.upper()",
            "outputs": ["res"],
        }
    else:
        work_node = {
            "id": "work",
            "type": "agent",
            "prompt": "echo {{task}}",
            "inputs": ["task"],
            "outputs": ["res"],
        }
    return {
        "name": "mr",
        "provider": "copilot",
        "entry": "plan",
        "defaults": {"model": "m"},
        "state": {"n": n},
        "max_concurrency": cap,
        "fan_max_concurrency": fan_cap,
        "nodes": [
            {
                "id": "plan",
                "type": "script",
                "inputs": [{"name": "n", "schema": {"type": "integer"}}],
                "code": "def p(ctx, n):\n    return ['t' + str(i) for i in range(n)]",
                "outputs": [{"name": "tasks", "schema": {"type": "array", "items": {"type": "string"}}}],
            },
            work_node,
            {
                "id": "merge",
                "type": "script",
                "inputs": [{"name": "results", "schema": {"type": "array", "items": {"type": "string"}}}],
                "code": "def m(ctx, results):\n    return ','.join(results)",
                "outputs": ["summary"],
            },
        ],
        "edges": [
            {"from": "$in", "to": "plan", "map": {"n": "n"}},
            {"from": "plan", "to": "work", "fan_out": "tasks", "map": {"tasks": "task"}},
            {"from": "work", "to": "merge", "fan_in": "list", "map": {"res": "results"}},
            {"from": "merge", "to": "$output", "map": {"summary": "summary"}},
        ],
    }


def _stub_factory(text: str) -> Any:
    """A stream_fn_factory whose agent turns emit *text* (no real LLM)."""

    def _factory(model: str) -> Any:
        def _stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            msg = AssistantMessage(content=(TextContent(text=text),))
            stream: AiEventStream[AssistantMessage] = AiEventStream()

            async def _push() -> None:
                await asyncio.sleep(0)
                await stream.send(DoneEvent(stop_reason="stop", message=msg))
                stream.set_result(msg)
                await stream.close()

            asyncio.ensure_future(_push())
            return stream

        return _stream_fn

    return _factory


async def _interp(data: dict[str, Any]) -> dict[str, Any]:
    wf = parse_workflow(data)
    result = await execute(wf, stream_fn_factory=_stub_factory("X"))
    return dict(result.runtime)


# --- interpreter behaviour -------------------------------------------------


async def test_fan_out_three_elements() -> None:
    rt = await _interp(_map_reduce(3))
    assert rt["state"]["work"]["res"] == ["T0", "T1", "T2"]  # index-ordered list
    assert rt["out"]["summary"] == "T0,T1,T2"


async def test_fan_out_empty_array_is_empty_list() -> None:
    """N=0 runs the worker zero times; the reduce is []."""
    rt = await _interp(_map_reduce(0))
    assert rt["state"]["work"]["res"] == []
    assert rt["out"]["summary"] == ""


async def test_fan_out_capped_concurrency_no_deadlock() -> None:
    """cap=1 must not self-nest the semaphore (the fan node holds one outer slot)."""
    rt = await _interp(_map_reduce(3, cap=1))
    assert rt["out"]["summary"] == "T0,T1,T2"


async def test_fan_out_agent_worker() -> None:
    """The agent path fans out too — each instance echoes the stubbed text."""
    wf = parse_workflow(_map_reduce(3, worker="agent"))
    result = await execute(wf, stream_fn_factory=_stub_factory("PONG"))
    assert result.runtime["state"]["work"]["res"] == ["PONG", "PONG", "PONG"]


# --- cross-engine parity (interpret == compile) ----------------------------


async def _run_generated(src: str) -> dict[str, Any]:
    """Exec a generated module with stubbed provider/stream and return its _RUNTIME."""
    import xdog.agent.helpers as _agent_helpers
    import xdog.ai as _ai

    def _stub_stream_fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
        msg = AssistantMessage(content=(TextContent(text="X"),))
        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            await asyncio.sleep(0)
            await stream.send(DoneEvent(stop_reason="stop", message=msg))
            stream.set_result(msg)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    original_sfp = _agent_helpers.stream_fn_from_provider
    original_provider = _ai.provider
    _agent_helpers.stream_fn_from_provider = lambda _p: _stub_stream_fn  # type: ignore[assignment]
    _ai.provider = lambda _name: object()  # type: ignore[assignment]
    mod = types.ModuleType("_gen_fan")
    try:
        exec(compile(src, "<gen_fan>", "exec"), mod.__dict__)  # noqa: S102
        await mod.main()  # type: ignore[attr-defined]
        return dict(mod._RUNTIME)  # type: ignore[attr-defined]
    finally:
        _agent_helpers.stream_fn_from_provider = original_sfp  # type: ignore[assignment]
        _ai.provider = original_provider  # type: ignore[assignment]


@pytest.mark.parametrize("n", [0, 1, 3])
async def test_fan_out_interpret_equals_compile(n: int) -> None:
    """The map-reduce output is identical through execute() and the generated module."""
    data = _map_reduce(n)
    wf = parse_workflow(data)

    interp = await execute(wf, stream_fn_factory=_stub_factory("X"))
    src = generate(wf)
    compile(src, "<gen>", "exec")  # generated module compiles

    gen_runtime = await _run_generated(src)
    assert gen_runtime["state"] == dict(interp.runtime["state"])
    assert gen_runtime["out"] == dict(interp.runtime["out"])


@pytest.mark.parametrize(
    ("n", "expected"),
    [(0, ""), (1, "T0,t0"), (3, "T0,t0,T1,t1,T2,t2")],
)
async def test_fan_in_concat_flattens_one_level_with_parity(n: int, expected: str) -> None:
    """concat flattens each fan instance's array once, preserving instance order."""
    data = _map_reduce(n)
    data["nodes"][1] = {
        "id": "work",
        "type": "script",
        "inputs": ["task"],
        "code": "def w(ctx, task):\n    return [task.upper(), task.lower()]",
        "outputs": [{"name": "res", "schema": {"type": "array", "items": {"type": "string"}}}],
    }
    data["edges"][2]["fan_in"] = "concat"
    wf = parse_workflow(data)

    interp = await execute(wf, stream_fn_factory=_stub_factory("X"))
    generated = await _run_generated(generate(wf))

    assert interp.runtime["state"]["work"]["res"] == [
        [f"T{i}", f"t{i}"] for i in range(n)
    ]
    assert interp.runtime["out"]["summary"] == expected
    assert generated["state"] == dict(interp.runtime["state"])
    assert generated["out"] == dict(interp.runtime["out"])


async def test_fan_in_concat_can_feed_output_directly_with_parity() -> None:
    """A fan worker may flatten its array results directly into $output."""
    data = _map_reduce(2)
    data["nodes"] = data["nodes"][:2]
    data["nodes"][1] = {
        "id": "work",
        "type": "script",
        "inputs": ["task"],
        "code": "def w(ctx, task):\n    return [task.upper(), task.lower()]",
        "outputs": [
            {
                "name": "res",
                "schema": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
            }
        ],
    }
    data["edges"] = [
        data["edges"][0],
        data["edges"][1],
        {"from": "work", "to": "$output", "fan_in": "concat", "map": {"res": "results"}},
    ]
    wf = parse_workflow(data)

    from xdog.flow.loader import workflow_output_schema

    assert workflow_output_schema(wf)["properties"]["results"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    interpreted = await execute(wf, stream_fn_factory=_stub_factory("X"))
    generated = await _run_generated(generate(wf))

    assert interpreted.runtime["out"] == {"results": ["T0", "t0", "T1", "t1"]}
    assert generated["out"] == dict(interpreted.runtime["out"])


def test_fan_in_list_output_schema_wraps_worker_value() -> None:
    """list publishes an aggregate array schema around the per-instance schema."""
    from xdog.flow.loader import workflow_output_schema

    data = _map_reduce(2)
    data["nodes"] = data["nodes"][:2]
    data["edges"] = [
        data["edges"][0],
        data["edges"][1],
        {"from": "work", "to": "$output", "fan_in": "list", "map": {"res": "results"}},
    ]
    wf = parse_workflow(data)
    assert workflow_output_schema(wf)["properties"]["results"] == {
        "type": "array",
        "items": {"type": "string"},
    }


# --- fan concurrency limiter -----------------------------------------------


def _conc_tracking_map_reduce(n: int, fan_cap: int, probe: str) -> dict[str, Any]:
    """Map-reduce whose worker records peak in-flight count into *probe* file."""
    code = (
        "async def w(ctx, task):\n"
        "    import asyncio, json, os\n"
        f"    p = {probe!r}\n"
        "    st = json.load(open(p)) if os.path.exists(p) else {'cur': 0, 'max': 0}\n"
        "    st['cur'] += 1\n"
        "    st['max'] = max(st['max'], st['cur'])\n"
        "    json.dump(st, open(p, 'w'))\n"
        "    await asyncio.sleep(0.03)\n"
        "    st = json.load(open(p))\n"
        "    st['cur'] -= 1\n"
        "    json.dump(st, open(p, 'w'))\n"
        "    return task.upper()\n"
    )
    d = _map_reduce(n, fan_cap=fan_cap)
    d["nodes"][1] = {"id": "work", "type": "script", "inputs": ["task"], "code": code, "outputs": ["res"]}
    return d


async def test_fan_cap_limits_peak_concurrency(tmp_path: pathlib.Path) -> None:
    probe = str(tmp_path / "conc.json")
    wf = parse_workflow(_conc_tracking_map_reduce(8, fan_cap=2, probe=probe))
    await execute(wf, stream_fn_factory=_stub_factory("X"), workspace=tmp_path)
    import json

    peak = json.loads(pathlib.Path(probe).read_text())["max"]
    assert peak == 2, f"fan_cap=2 should hold peak at 2, saw {peak}"


async def test_fan_cap_parity_holds(tmp_path: pathlib.Path) -> None:
    """A capped fan still produces identical output through both engines."""
    data = _map_reduce(5, fan_cap=2)
    wf = parse_workflow(data)
    interp = await execute(wf, stream_fn_factory=_stub_factory("X"))
    src = generate(wf)
    assert "('res',), 2)" in src  # the fan_cap literal reaches _drive_fan positionally
    gen_runtime = await _run_generated(src)
    assert gen_runtime["state"] == dict(interp.runtime["state"])
    assert gen_runtime["out"] == dict(interp.runtime["out"])
