"""Tests for flow.checkpoint — CheckpointStore and executor resume behaviour."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest
from xdog.flow.checkpoint import CheckpointInterceptor, JSONFileCheckpointStore
from xdog.flow.executor import ExecResult, execute
from xdog.flow.models import EdgeDef, NodeDef, Port, WorkflowDef

# ---------------------------------------------------------------------------
# Checkpoint interception boundaries
# ---------------------------------------------------------------------------


def test_interceptor_does_not_persist_failed_mutation() -> None:
    snapshots: list[dict[str, object]] = []
    interceptor = CheckpointInterceptor(lambda: {"value": 1}, snapshots.append)

    def fail() -> None:
        raise RuntimeError("no commit")

    with pytest.raises(RuntimeError, match="no commit"):
        interceptor.intercept("batch", fail)
    assert snapshots == []


# ---------------------------------------------------------------------------
# JSONFileCheckpointStore unit tests
# ---------------------------------------------------------------------------


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    store = JSONFileCheckpointStore(tmp_path)
    snap: dict[str, Any] = {"outputs": {"$in": {"x": "1"}}, "completed": ["node1"], "stack": [], "out_live": {}}
    store.save("run-abc", snap)
    loaded = store.load("run-abc")
    assert loaded == snap


# ---------------------------------------------------------------------------
# Frontier-batch checkpoint boundaries
# ---------------------------------------------------------------------------


async def test_executor_rejects_incomplete_checkpoint_schema(tmp_path: Path) -> None:
    store = JSONFileCheckpointStore(tmp_path)
    store.save("old", {"outputs": {"$in": {}}, "completed": []})
    wf = WorkflowDef(
        name="strict-checkpoint",
        provider="",
        entry="a",
        nodes=(NodeDef(id="a", type="script", code="def a(ctx):\n    return None"),),
        edges=(),
    )
    with pytest.raises(Exception, match="does not match the current schema"):
        await execute(wf, checkpoint=store, run_id="old", workspace=tmp_path)


class RecordingCheckpointStore:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []

    def save(self, run_id: str, snapshot: dict[str, Any]) -> None:
        _ = run_id
        self.snapshots.append(copy.deepcopy(snapshot))

    def load(self, run_id: str) -> dict[str, Any] | None:
        _ = run_id
        return copy.deepcopy(self.snapshots[-1]) if self.snapshots else None


async def test_parallel_siblings_share_one_checkpoint_boundary() -> None:
    store = RecordingCheckpointStore()
    wf = WorkflowDef(
        name="parallel-boundaries",
        provider="",
        entry="a",
        nodes=tuple(
            NodeDef(id=node, type="script", code=f"def {node}(ctx):\n    return {node!r}", output_ports=(Port("x"),))
            for node in ("a", "b", "c", "d")
        ),
        edges=(
            EdgeDef(src="a", dst="b"),
            EdgeDef(src="a", dst="c"),
            EdgeDef(src="b", dst="d"),
            EdgeDef(src="c", dst="d"),
        ),
    )

    await execute(wf, checkpoint=store, run_id="parallel")

    assert len(store.snapshots) == 3
    assert set(store.snapshots[1]["completed"]) == {"a", "b", "c"}


async def test_successful_sibling_is_committed_before_batch_failure() -> None:
    store = RecordingCheckpointStore()
    wf = WorkflowDef(
        name="partial-batch-boundary",
        provider="",
        entry="root",
        nodes=(
            NodeDef(id="root", type="script", code="def root(ctx):\n    return None"),
            NodeDef(id="ok", type="script", code="def ok(ctx):\n    return 'done'", output_ports=(Port("x"),)),
            NodeDef(id="bad", type="script", code="def bad(ctx):\n    raise RuntimeError('boom')"),
        ),
        edges=(EdgeDef(src="root", dst="ok"), EdgeDef(src="root", dst="bad")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        await execute(wf, checkpoint=store, run_id="partial")

    assert len(store.snapshots) == 2
    assert set(store.snapshots[-1]["completed"]) == {"root", "ok"}
    assert store.snapshots[-1]["outputs"]["ok"]["x"] == "done"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Executor resume test — skip already-completed nodes
# ---------------------------------------------------------------------------


def test_resume_skips_completed_nodes(tmp_path: Path) -> None:
    """Run-1 completes node1 then fails; run-2 resumes and skips node1."""
    counter_file = tmp_path / "node1_count.txt"
    counter_file.write_text("0")
    asyncio.run(_run_resume_test(tmp_path, counter_file))


async def _run_resume_test(tmp_path: Path, counter_file: Path) -> None:
    store = JSONFileCheckpointStore(tmp_path / "ckpts")
    run_id = "test-resume"

    counter_path_repr = repr(str(counter_file))

    node1_code = f"""
def node1(ctx, x: str) -> str:
    _p = {counter_path_repr}
    _n = int(open(_p).read().strip()) + 1
    open(_p, 'w').write(str(_n))
    return "node1_out"
"""

    node2_fail_code = """
def node2(ctx, val: str) -> str:
    raise RuntimeError("intentional failure")
"""

    wf = WorkflowDef(
        name="resume_test",
        provider="fake",
        entry="node1",
        default_model="unused",
        initial_state=[("x", "hello")],
        nodes=(
            NodeDef(
                id="node1",
                type="script",
                code=node1_code,
                input_ports=(Port("x"),),
                output_ports=(Port("result"),),
            ),
            NodeDef(
                id="node2",
                type="script",
                code=node2_fail_code,
                input_ports=(Port("val"),),
                output_ports=(Port("final"),),
            ),
        ),
        edges=(
            EdgeDef(src="$in", dst="node1", mapping=(("x", "x"),)),
            EdgeDef(src="node1", dst="node2", mapping=(("result", "val"),)),
        ),
    )

    # Run 1: should complete node1 (checkpoint saved) then fail on node2.
    with pytest.raises(RuntimeError, match="intentional failure"):
        await execute(
            wf, checkpoint=store, run_id=run_id,
            stream_fn_factory=lambda m: (lambda *a, **k: None), workspace=tmp_path,
        )

    node1_count = int(counter_file.read_text().strip())
    assert node1_count == 1, "node1 should have run exactly once in attempt 1"

    snap = store.load(run_id)
    assert snap is not None
    assert set(snap) == {
        "contract",
        "outputs",
        "sessions",
        "completed",
        "loop_counters",
        "stack",
        "out_live",
        "memo",
        "tokens_used",
    }
    assert "node1" in snap["completed"]

    node2_ok_code = """
def node2(ctx, val: str) -> str:
    return "done_" + val
"""

    wf2 = WorkflowDef(
        name="resume_test",
        provider="fake",
        entry="node1",
        default_model="unused",
        initial_state=[("x", "hello")],
        nodes=(
            NodeDef(
                id="node1",
                type="script",
                code=node1_code,
                input_ports=(Port("x"),),
                output_ports=(Port("result"),),
            ),
            NodeDef(
                id="node2",
                type="script",
                code=node2_ok_code,
                input_ports=(Port("val"),),
                output_ports=(Port("final"),),
            ),
        ),
        edges=(
            EdgeDef(src="$in", dst="node1", mapping=(("x", "x"),)),
            EdgeDef(src="node1", dst="node2", mapping=(("result", "val"),)),
        ),
    )

    # Run 2: node1 should be skipped (checkpoint), node2 should run.
    result = await execute(
        wf2, checkpoint=store, run_id=run_id,
        stream_fn_factory=lambda m: (lambda *a, **k: None), workspace=tmp_path,
    )
    assert isinstance(result, ExecResult)

    node1_count_after = int(counter_file.read_text().strip())
    assert node1_count_after == 1, "node1 should NOT re-run on resume"

    state = result.runtime["state"]
    assert state["node2"]["final"] == "done_node1_out"


@pytest.mark.parametrize('change', [
    'completed_body', 'reopened_completed', 'wiring', 'ports', 'inputs', 'malformed_outputs',
])
async def test_resume_rejects_drift_before_any_new_side_effect(tmp_path, change):
    from dataclasses import replace

    from xdog.flow.executor import WorkflowExecutionError

    witness = tmp_path / 'executed'
    wf = WorkflowDef(name='drift', provider='', entry='', initial_state=(('x', 'old'),), nodes=(
        NodeDef(id='first', type='script', code='def first(ctx, x):\n    return x',
                input_ports=(Port('x'),), output_ports=(Port('v'),)),
        NodeDef(id='second', type='script', code='def second(ctx, v):\n    raise RuntimeError("stop")',
                input_ports=(Port('v'),), output_ports=(Port('v'),)),
    ), edges=(EdgeDef(src='$in', dst='first', mapping=(('x', 'x'),)),
              EdgeDef(src='first', dst='second', mapping=(('v', 'v'),))))
    store = JSONFileCheckpointStore(tmp_path / 'checkpoints')
    with pytest.raises(RuntimeError, match='stop'):
        await execute(wf, checkpoint=store, run_id='run', workspace=tmp_path)
    repaired = replace(wf.nodes[1], code=f'def second(ctx, v):\n    open({str(witness)!r}, "w").write(v)\n    return v')
    resumed = replace(wf, nodes=(wf.nodes[0], repaired))
    inputs = None
    if change in {'completed_body', 'reopened_completed'}:
        if change == 'reopened_completed':
            snapshot = store.load('run')
            snapshot['completed'] = []  # A loop reopens its frontier without erasing old computations.
            store.save('run', snapshot)
        changed = replace(wf.nodes[0], code='def first(ctx, x):\n    return "changed"')
        resumed = replace(resumed, nodes=(changed, repaired))
    elif change == 'wiring':
        resumed = replace(resumed, edges=())
    elif change == 'ports':
        resumed = replace(resumed, nodes=(replace(wf.nodes[0], output_ports=(Port('other'),)), repaired))
    elif change == 'inputs':
        inputs = {'x': 'new'}
    else:
        snapshot = store.load('run')
        snapshot['outputs'] = []
        store.save('run', snapshot)
        inputs = {'x': 'old'}
    before = (tmp_path / 'checkpoints/run.json').read_bytes()
    with pytest.raises(WorkflowExecutionError, match='Checkpoint|checkpoint'):
        await execute(resumed, checkpoint=store, run_id='run', inputs=inputs, workspace=tmp_path)
    assert not witness.exists()
    assert (tmp_path / 'checkpoints/run.json').read_bytes() == before
