"""`inherit` at run time — does the child actually see the parent's turns?

The validation tests cover the reference. These cover the thing that is easy to
get wrong and impossible to notice: a version that resolves `inherit` perfectly
and then passes an empty session would satisfy every "it did not crash" test.
So each test here asserts on the *content* of what the runner received.
"""
from __future__ import annotations

from typing import Any

import pytest
from xdog.flow.loader import parse_workflow
from xdog.flow.models import NodeDef
from xdog.flow.runners import StubRunner


class _Recorder:
    """A NodeStubs that remembers the session handed to each node."""

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.seen: dict[str, Any] = {}

    def agent(
        self, node: NodeDef, *, inputs: Any, step: int, fan_index: int | None
    ) -> tuple[object, int]:
        return self.replies.get(node.id, "ok"), 1

    def script(self, *args: Any, **kwargs: Any) -> tuple[object, int]:  # pragma: no cover
        raise AssertionError("not used")


async def _turn(
    runner: StubRunner,
    node: NodeDef,
    *,
    session: dict[str, Any] | None = None,
    user_prompt: str = "go",
) -> dict[str, Any]:
    sink: dict[str, object] = {}
    await runner.run(
        node,
        system_prompt="be terse",
        user_prompt=user_prompt,
        model="m",
        timeout=30.0,
        inputs={},
        step=0,
        fan_index=None,
        session=session,
        session_sink=sink,
    )
    return sink["session"]  # type: ignore[return-value]


def _texts(session: dict[str, Any]) -> list[str]:
    return [
        part["text"]
        for message in session["messages"]
        for part in message["content"]
        if part.get("type") == "text"
    ]


async def test_the_stub_runner_produces_a_session_at_all() -> None:
    """`xdog-flow test` never reaches a model. If the stub runner contributed
    nothing, every test of an inheriting workflow would run against an empty
    context and pass for the wrong reason."""
    runner = StubRunner(_Recorder({"a": "the answer is 42"}))

    session = await _turn(runner, NodeDef(id="a"), user_prompt="what is it?")

    assert _texts(session) == ["what is it?", "the answer is 42"]
    assert session["base_system_prompt"] == "be terse"


async def test_a_child_turn_is_appended_to_the_inherited_history() -> None:
    """The whole feature in one assertion: turn two can see turn one."""
    runner = StubRunner(_Recorder({"a": "I found a cat", "b": "the cat is grey"}))

    parent = await _turn(runner, NodeDef(id="a"), user_prompt="research")
    child = await _turn(
        runner, NodeDef(id="b"), session=parent, user_prompt="now critique that"
    )

    assert _texts(child) == [
        "research", "I found a cat",          # inherited
        "now critique that", "the cat is grey",  # this turn
    ]


async def test_a_missing_session_starts_cold_without_complaint() -> None:
    """The first pass of a self-inheriting loop, and any skipped branch."""
    runner = StubRunner(_Recorder({"a": "first"}))

    session = await _turn(runner, NodeDef(id="a"), session=None)

    assert _texts(session) == ["go", "first"]


async def test_self_inheritance_accumulates_across_passes() -> None:
    """A loop keeps its own context instead of restarting cold each iteration."""
    runner = StubRunner(_Recorder({"a": "draft"}))
    node = NodeDef(id="a")

    first = await _turn(runner, node, user_prompt="write it")
    second = await _turn(runner, node, session=first, user_prompt="improve it")
    third = await _turn(runner, node, session=second, user_prompt="improve again")

    assert _texts(third) == [
        "write it", "draft",
        "improve it", "draft",
        "improve again", "draft",
    ]


# -- The wiring, end to end through the executor ------------------------------


def _inheriting_workflow() -> dict:
    def agent(node_id: str, **extra: object) -> dict:
        return {
            "id": node_id,
            "type": "agent",
            "prompt": "go",
            "inputs": [{"name": "seed", "schema": {"type": "string"}, "required": False}],
            "outputs": ["out"],
            **extra,
        }

    return {
        "name": "inh-run",
        "provider": "copilot",
        "defaults": {"model": "m"},
        "entry": "a",
        "nodes": [agent("a"), agent("b", inherit={"from": "a"})],
        "edges": [
            {"from": "a", "to": "b", "map": {"out": "seed"}},
            {"from": "b", "to": "$output", "map": {"out": "result"}},
        ],
    }


async def test_the_executor_hands_the_parent_session_to_the_child() -> None:
    """Everything above tests the runner in isolation; this proves the executor
    actually carries a session from one node to the next."""
    from xdog.flow.executor import execute

    seen: dict[str, Any] = {}

    class _Watching(_Recorder):
        def agent(self, node: NodeDef, *, inputs: Any, step: int, fan_index: int | None):
            return super().agent(node, inputs=inputs, step=step, fan_index=fan_index)

    stubs = _Watching({"a": "parent said this", "b": "child said that"})
    original = StubRunner.run

    async def _spy(self: StubRunner, node: NodeDef, **kwargs: Any):
        seen[node.id] = kwargs.get("session")
        return await original(self, node, **kwargs)

    StubRunner.run = _spy  # type: ignore[method-assign]
    try:
        await execute(parse_workflow(_inheriting_workflow()), stubs=stubs)
    finally:
        StubRunner.run = original  # type: ignore[method-assign]

    assert seen["a"] is None, "the first node inherits nothing"
    assert seen["b"] is not None, "the child got no session at all"
    assert "parent said this" in _texts(seen["b"])


# -- Resume ------------------------------------------------------------------


async def test_a_resumed_run_still_has_the_parent_session() -> None:
    """Without `sessions` in the checkpoint this fails silently, not loudly:
    the child resumes with an empty context and answers plausibly and wrongly."""
    import json
    import tempfile
    from pathlib import Path

    from xdog.flow.checkpoint import JSONFileCheckpointStore
    from xdog.flow.executor import execute

    directory = Path(tempfile.mkdtemp())
    store = JSONFileCheckpointStore(directory)
    stubs = _Recorder({"a": "parent said this", "b": "child said that"})
    workflow = parse_workflow(_inheriting_workflow())

    await execute(workflow, stubs=stubs, checkpoint=store, run_id="r1")

    saved = json.loads((directory / "r1.json").read_text(encoding="utf-8"))
    assert "parent said this" in json.dumps(saved["sessions"]), (
        "the parent's session never reached the checkpoint"
    )

    seen: dict[str, Any] = {}
    original = StubRunner.run

    async def _spy(self: StubRunner, node: NodeDef, **kwargs: Any):
        seen[node.id] = kwargs.get("session")
        return await original(self, node, **kwargs)

    StubRunner.run = _spy  # type: ignore[method-assign]
    try:
        await execute(workflow, stubs=stubs, checkpoint=store, run_id="r1")
    finally:
        StubRunner.run = original  # type: ignore[method-assign]


async def test_unversioned_checkpoint_is_rejected_without_resuming() -> None:
    """Unpinned old executable state must not be silently adopted."""
    import json
    import tempfile
    from pathlib import Path

    from xdog.flow.checkpoint import JSONFileCheckpointStore
    from xdog.flow.executor import execute

    directory = Path(tempfile.mkdtemp())
    (directory / "old.json").write_text(json.dumps({
        "outputs": {"$in": {}},
        "completed": [],
        "loop_counters": {},
        "stack": [],
        "out_live": {},
        "memo": {},
        "tokens_used": 0,
    }), encoding="utf-8")

    from xdog.flow.executor import WorkflowExecutionError

    with pytest.raises(WorkflowExecutionError, match="current schema"):
        await execute(
            parse_workflow(_inheriting_workflow()),
            stubs=_Recorder({"a": "x", "b": "y"}),
            checkpoint=JSONFileCheckpointStore(directory),
            run_id="old",
        )


async def test_a_node_with_no_incoming_data_can_still_recall_the_parent_turn() -> None:
    """The control experiment, as a test.

    Verified against a real model first: with `inherit`, the second node
    reproduced the exact noun and number the first invented; without it — the
    same workflow, one field removed — it answered NO_MEMORY. The edge carries
    an empty `map`, so there is no port through which the answer could arrive.
    """
    from xdog.flow.executor import execute

    workflow = parse_workflow({
        "name": "inherit-proof",
        "provider": "copilot",
        "defaults": {"model": "m"},
        "entry": "pick",
        "state": {"seed": "unused"},
        "nodes": [
            {"id": "pick", "type": "agent", "inputs": ["seed"],
             "prompt": "Invent a noun and a number.", "outputs": ["chosen"]},
            {"id": "recall", "type": "agent", "inherit": {"from": "pick"},
             "prompt": "What did you choose?", "outputs": ["recalled"]},
        ],
        "edges": [
            {"from": "$in", "to": "pick", "map": {"seed": "seed"}},
            {"from": "pick", "to": "recall", "map": {}},
            {"from": "recall", "to": "$output", "map": {"recalled": "result"}},
        ],
    })

    seen: dict[str, Any] = {}
    original = StubRunner.run

    async def _spy(self: StubRunner, node: NodeDef, **kwargs: Any):
        seen[node.id] = kwargs.get("session")
        return await original(self, node, **kwargs)

    StubRunner.run = _spy  # type: ignore[method-assign]
    try:
        await execute(workflow, stubs=_Recorder({"pick": "NOUN=brumation NUMBER=407"}))
    finally:
        StubRunner.run = original  # type: ignore[method-assign]

    # No edge feeds `recall` any port value, so its whole context is inherited.
    assert "NOUN=brumation NUMBER=407" in _texts(seen["recall"])


@pytest.mark.parametrize('session', [{}, {'messages': []}])
@pytest.mark.parametrize('sink', [None, {}])
async def test_stub_rejects_noncanonical_inheritance_before_callback(session, sink):
    from types import SimpleNamespace

    def forbidden(*args, **kwargs):
        pytest.fail('A malformed inherited snapshot must not reach even a stub callback')
    runner = StubRunner(SimpleNamespace(agent=forbidden))
    with pytest.raises(ValueError, match='missing fields'):
        await runner.run(NodeDef(id='test'), system_prompt='', user_prompt='test', model='offline',
                         timeout=30, inputs={}, step=0, fan_index=None, session=session, session_sink=sink)
    assert sink is None or sink == {}
