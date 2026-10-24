"""flow.executor — parallel (readiness-based) workflow executor."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import inspect
import json
import logging
import re
import sys
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xdog.agent import ToolRepo
from xdog.agent.core import StreamFn, WebSearchFn
from xdog.agent.workspace import script_bound
from xdog.flow.checkpoint import CheckpointInterceptor, CheckpointStore
from xdog.flow.coerce import to_python, to_state
from xdog.flow.conditions import evaluate
from xdog.flow.errors import WorkflowBudgetExceeded, WorkflowExecutionError, WorkflowPaused
from xdog.flow.events import EventCallback, FlowEvent, NodeFailed, NodeFinished, NodeStarted
from xdog.flow.frontier import (
    build_frontier_spec,
    complete_batch,
    exhausted_edge_label,
    isolate_nodes,
    new_frontier_state,
    replay_completed,
    restore_loop_activations,
    take_ready,
)
from xdog.flow.interpolate import interpolate, jsonpath_get
from xdog.flow.models import (
    IN_NODE_ID,
    OUT_NODE_ID,
    EdgeDef,
    NodeDef,
    WorkflowDef,
    agent_submits_object,
    edge_identities,
)
from xdog.flow.preview import preview_ports
from xdog.flow.runners import AgentRunner, CliRunner, NodeStubs, SdkRunner, StubRunner, cli_adapter_for
from xdog.flow.runtime import RuntimeContext

logger = logging.getLogger(__name__)

ScriptResolver = Callable[[str], Callable[..., Awaitable[str]]]
ScriptFn = Callable[..., Any]


async def _wrap_sync(value: Any) -> Any:
    """Wrap a plain (non-awaitable) value in a coroutine for use with asyncio.wait_for."""
    return value


def _retry_bounds(node: NodeDef) -> tuple[int, float]:
    """Return ``(max_attempts, backoff)`` for a node's retry policy (1 attempt if none)."""
    return 1 + (node.retry.max if node.retry else 0), (node.retry.backoff if node.retry else 0.0)


async def _retry_wait(node_id: str, attempt: int, max_attempts: int, backoff: float) -> None:
    """Sleep + log before the next retry, if any attempts remain after *attempt* (0-based)."""
    if attempt + 1 >= max_attempts:
        return
    delay = backoff * (attempt + 1)
    logger.debug("Retrying node %r (attempt %d/%d) after %s", node_id, attempt + 1, max_attempts, delay)
    await asyncio.sleep(delay)


def _memo_key(node_id: str, ins: dict[str, object]) -> str:
    """Return a deterministic memo key for (node_id, input namespace).

    codegen emits an equivalent inline f-string (see ``_memo_hit_block``); the two
    forms are kept in lock-step by the interpret==compile parity tests rather than a
    shared helper — the generated module must stay self-contained (no import back
    into flow's runtime), so the digest logic is intentionally expressed twice.
    """
    digest = hashlib.sha256(json.dumps(ins, sort_keys=True).encode()).hexdigest()
    return f"{node_id}:{digest}"


def _mapping_root(sport: str) -> str:
    """Root output-port name of a mapping source key (mirrors loader._jsonpath_root).

    ``$.plan.tasks`` -> ``plan``; a bare ``tasks`` -> ``tasks``.  Used to spot the
    fan_out array port among a fan-out edge's mapping sources.
    """
    body = sport[2:] if sport.startswith("$.") else (sport[1:] if sport.startswith("$") else sport)
    return re.split(r"[.\[]", body, maxsplit=1)[0]


def _fan_concat(value: object) -> object:
    """Flatten one level for ``fan_in='concat'`` while preserving item order."""
    if not isinstance(value, (list, tuple)):
        return value
    flat: list[object] = []
    for item in value:
        if isinstance(item, (list, tuple)):
            flat.extend(item)
        else:
            flat.append(item)
    return flat


def _incoming_fan_out_edge(node_id: str, in_edges: Mapping[str, list[EdgeDef]]) -> EdgeDef | None:
    """The single fan_out edge feeding *node_id*, or None (the loader forbids >1)."""
    for edge in in_edges.get(node_id, []):
        if edge.fan_out is not None:
            return edge
    return None


def _resolve_script_fn(node: NodeDef, base_dir: Path | None) -> ScriptFn:
    """Resolve a script node's callable from inline ``code`` or a ``run`` ref.

    Inline code is ``exec``'d and the single top-level function returned.  A
    ``run`` ref is imported with *base_dir* (the workflow's own directory)
    temporarily on ``sys.path`` so a workflow bundles its sibling ``.py`` files.
    """
    if node.code is not None:
        namespace: dict[str, Any] = {}
        exec(node.code, namespace)  # noqa: S102 - inline workflow code, trusted author
        funcs = [v for v in namespace.values() if inspect.isfunction(v)]
        if len(funcs) != 1:
            raise WorkflowExecutionError(f"Script node {node.id!r}: code must define one function")
        return funcs[0]
    if node.run is None:
        raise WorkflowExecutionError(f"Script node {node.id!r}: no code or run")
    module_name, _, func_name = node.run.partition(":")
    if base_dir is not None:
        sys.path.insert(0, str(base_dir))
        importlib.invalidate_caches()
        try:
            module = importlib.import_module(module_name)
        finally:
            with contextlib.suppress(ValueError):
                sys.path.remove(str(base_dir))
    else:
        module = importlib.import_module(module_name)
    fn: ScriptFn = getattr(module, func_name)
    return fn


@contextlib.contextmanager
def _importable(base_dir: Path | None) -> "Iterator[None]":
    """Keep *base_dir* importable for the duration of the block.

    `_resolve_script_fn` puts it on ``sys.path`` only while it imports, so a
    module that imports a sibling *inside* its function body used to fail with a
    bare ``No module named`` -- but succeeded once compiled, because a bundle run
    as ``python <dir>`` has that directory on ``sys.path`` for its whole life.
    Same workflow, different answer depending on how it was run.
    """
    if base_dir is None:
        yield
        return
    entry = str(base_dir)
    added = entry not in sys.path
    if added:
        sys.path.insert(0, entry)
    try:
        yield
    finally:
        if added:
            with contextlib.suppress(ValueError):
                sys.path.remove(entry)


def _script_stub_value(node: NodeDef, ports: dict[str, object]) -> object:
    """Reshape a test stub's port dict into what the script itself would return.

    Stubs are always authored as ``{port: value}``; a script with 0/1 output ports
    returns a bare value.  Reshaping here (rather than storing the dict directly)
    keeps the stub on the production coercion path — ``_coerce_script_output`` still
    applies ``to_state`` and still rejects a shape the node did not declare.
    """
    declared = [p.name for p in node.output_ports]
    unknown = sorted(set(ports) - set(declared))
    if unknown:
        raise WorkflowExecutionError(
            f"Node {node.id!r}: stub sets undeclared output port(s) {', '.join(unknown)}; "
            f"declared: {', '.join(declared) or '(none)'}"
        )
    if len(declared) <= 1:
        return ports.get(declared[0]) if declared else None
    return ports


@dataclass(frozen=True)
class ExecResult:
    """Result of executing a workflow.

    ``runtime`` is a single container describing the whole run:

    - ``ctx``   — runtime info of the last node to run: ``{step, node_id, workflow_name}``.
    - ``stack`` — per-node execution trace; one delta frame ``{step, node, in, out}``
      pushed each time a node runs (a looped node appears multiple times).
    - ``state`` — accumulated real-node outputs only: ``{node_id: {port: value}}``
      (excludes ``$in`` and ``$output``).
    - ``in``    — the workflow's initial values (the reserved ``$in`` source).
    - ``out``   — the collected workflow outputs (edges targeting ``$output``).
    """

    runtime: dict[str, Any]


async def execute(
    wf: WorkflowDef,
    *,
    stream_fn_factory: Callable[[str], StreamFn] | None = None,
    timeout: float = 120.0,
    tool_repo: ToolRepo | None = None,
    script_resolver: ScriptResolver | None = None,
    base_dir: Path | None = None,
    inputs: Mapping[str, object] | None = None,
    web_search_fn_factory: Callable[[str], WebSearchFn] | None = None,
    checkpoint: CheckpointStore | None = None,
    run_id: str | None = None,
    on_event: EventCallback | None = None,
    max_concurrency: int | None = None,
    workspace: Path | None = None,
    allow_paths: Sequence[Path] | None = None,
    confined: bool = False,
    signals: set[str] | None = None,
    max_tokens: int | None = None,
    stubs: NodeStubs | None = None,
) -> ExecResult:
    """Execute a workflow definition with parallel fan-out/fan-in.

    A node is *ready* when all its non-loop predecessors have completed.
    All currently-ready nodes are launched concurrently via ``asyncio.gather``.
    A fan-in node waits until every upstream has finished.

    Linear graphs (no fan-out) behave identically to the previous implementation.

    Parameters
    ----------
    wf:
        The workflow definition to execute.
    stream_fn_factory:
        Optional factory ``(model_id) -> StreamFn``.  When provided it is
        used instead of the real ai provider — useful for injecting fakes
        in tests.
    timeout:
        Per-node wall-clock timeout in seconds.
    tool_repo:
        Registry of :class:`~agent.core.AgentTool` objects available to agent nodes.
        Defaults to :func:`~flow.tools.default_repo`.
    script_resolver:
        Optional override mapping a script node's ``'module:func'`` run-ref to its
        callable. When omitted, script nodes resolve via the built-in
        :func:`_resolve_script_fn` (inline ``code`` is ``exec``'d; a ``run`` ref is
        imported with the workflow's own directory on ``sys.path``). Inline-``code``
        nodes always use the built-in resolver regardless of this override.
    inputs:
        Optional run-time values for the ``$in`` source's output ports.  These
        override the workflow's ``state`` defaults key-by-key (a key absent from
        ``state`` simply seeds a new port), so the same workflow can run with
        different inputs without editing the JSON.
    web_search_fn_factory:
        Optional factory ``(model_id) -> async (query) -> str`` used to back the
        built-in ``web_search`` tool for agent nodes that set ``web_search``.
        Defaults to one built from the real ai provider.  Injectable for tests.
    stubs:
        Test-mode overrides for the workflow's non-deterministic boundaries (see
        :class:`~flow.runners.NodeStubs`).  When supplied, *every* agent node is
        answered by the stubs and no provider or CLI is constructed; subflow and
        script nodes may be stubbed too, and anything not stubbed runs for real.
    """
    from xdog.flow.tools import default_repo as _default_repo
    from xdog.flow.tools import register_workflow_tools

    if tool_repo is None:
        tool_repo = _default_repo()
    if wf.tool_refs:
        register_workflow_tools(wf, tool_repo, base_dir)
    # Build SDK wiring only when the workflow actually has an SDK agent node — a
    # pure-CLI (or script-only) workflow needs no provider and must not touch ai.
    # Test mode never builds it: the stub runner answers every agent node.
    _needs_sdk = stubs is None and any(n.type == "agent" and n.backend is None for n in wf.nodes)
    if stream_fn_factory is None and _needs_sdk:
        import xdog.ai as ai
        from xdog.agent.helpers import stream_fn_from_provider

        provider = ai.provider(wf.provider)
        default_stream_fn = stream_fn_from_provider(provider)

        def _factory(model: str) -> StreamFn:
            return default_stream_fn

        stream_fn_factory = _factory

        if web_search_fn_factory is None:
            from xdog.agent.helpers import web_search_fn_from_provider

            def _ws_factory(model: str) -> WebSearchFn:
                return web_search_fn_from_provider(provider, model)

            web_search_fn_factory = _ws_factory

    # Shared mutable output store: outputs[node_id][port] = value.  Seeded with
    # the reserved $in source carrying the workflow's initial values; run-time
    # ``inputs`` override those defaults key-by-key.  Only modified while holding
    # _state_lock or before/after concurrency.
    # Every run has a workspace: the directory a relative path resolves inside,
    # and where a node's output belongs. It defaults to `<workflow dir>/runtime`
    # so a workflow run from two places still writes to one predictable spot. It
    # It is created lazily, by `script_bound`, so a workflow with no script node
    # leaves no directory behind.
    #
    # Confinement is the separate, opt-in half. A workspace says where "here" is;
    # `confined=True` says the walls are real, and then the workspace plus any
    # granted trees are the whole allowance. Without it the workspace is a
    # convention, which is the historical behaviour every existing caller relies
    # on.
    # `<workflow dir>/runtime` when we know where the workflow is, and nothing
    # otherwise. Falling back to `Path.cwd()/runtime` invented a location out of
    # the process's ambient state and created it — so a library call with no
    # base_dir left a `runtime/` directory in whatever directory the caller
    # happened to be in. That is the same mistake as a tool defaulting its cwd
    # to wherever the program was launched.
    _workspace = workspace if workspace is not None else (
        base_dir / "runtime" if base_dir is not None else None
    )
    _confinement_roots: list[Path] | None = None
    if confined:
        # With no workspace the bound is the granted paths alone -- possibly
        # empty, which denies everything. That is the right way round: a run
        # asked to be confined with nowhere to work should refuse writes, not
        # silently fall back to some directory nobody named.
        _confinement_roots = [
            *([_workspace] if _workspace is not None else []),
            *(allow_paths or ()),
        ]

    seed: dict[str, object] = dict(wf.initial_state)
    if inputs:
        seed.update(inputs)
    outputs: dict[str, dict[str, object]] = {IN_NODE_ID: seed}
    # Dumped agent sessions, node id -> session, for nodes that declare `inherit`.
    # A missing entry means "start cold": the first pass of a self-inheriting
    # loop has none yet, and a skipped branch never produces one.  That is not an
    # error — the reference itself was already checked at load.
    sessions: dict[str, dict[str, object]] = {}
    _state_lock = asyncio.Lock()
    _signals: set[str] = signals if signals is not None else set()
    tokens_used = 0

    # Semaphore for concurrency cap (None = unlimited).
    _eff_cap = max_concurrency if max_concurrency is not None else wf.max_concurrency
    _sem: asyncio.Semaphore | None = asyncio.Semaphore(_eff_cap) if _eff_cap > 0 else None

    # Per-node execution trace (delta frames) and a monotonic step counter.  Both
    # are only touched while holding _state_lock (as each node records its frame).
    stack: list[dict[str, Any]] = []
    step_counter = 0
    # Live workflow output: updated the moment a node feeding ``$output`` finishes,
    # so a looped writer's latest value wins and the result is progressive.
    out_live: dict[str, object] = {}

    node_map = {n.id: n for n in wf.nodes}
    edge_ids = edge_identities(wf)

    # edges_from: src -> list[EdgeDef], with parallel internal hash ids.
    edges_from: dict[str, list[EdgeDef]] = defaultdict(list)
    edge_ids_from: dict[str, list[str]] = defaultdict(list)
    # in_edges: dst -> list[EdgeDef], with matching authored ids.
    in_edges: dict[str, list[EdgeDef]] = defaultdict(list)
    edge_ids_into: dict[str, list[str]] = defaultdict(list)
    edge_by_id: dict[str, EdgeDef] = {}
    for edge, edge_id in zip(wf.edges, edge_ids, strict=True):
        edge_by_id[edge_id] = edge
        edges_from[edge.src].append(edge)
        edge_ids_from[edge.src].append(edge_id)
        in_edges[edge.dst].append(edge)
        edge_ids_into[edge.dst].append(edge_id)

    def _source_ports(node_id: str) -> dict[str, object]:
        """Output ports available for reading from *node_id* (empty if not yet run)."""
        return outputs.get(node_id, {})

    def _strict_loop_error(edge: EdgeDef) -> WorkflowExecutionError:
        return WorkflowExecutionError(
            f"while loop {edge.src!r}->{edge.dst!r} did not converge within"
            f" {edge.loop_max} iterations (condition still true)"
        )

    def _build_inputs(node_id: str, enabled_edge_ids: tuple[str, ...]) -> dict[str, object]:
        """Assemble inputs from $in plus edges enabled for this activation.

        Walks in authored edge order so later enabled mappings retain the existing
        deterministic override rule. False/unreached forward edges never leak data.
        """
        enabled = set(enabled_edge_ids)
        ins: dict[str, object] = {}
        incoming = in_edges.get(node_id, [])
        incoming_ids = edge_ids_into.get(node_id, [])
        for edge, edge_id in zip(incoming, incoming_ids, strict=True):
            if edge.src != IN_NODE_ID and edge_id not in enabled:
                continue
            src_ports = _source_ports(edge.src)
            for sport, dport in edge.mapping:
                # The source key is a JSONPath resolved against the source node's
                # output ports: a plain port (``plan``) or a nested field
                # (``$.verdict.within_budget``).  A miss leaves the port unfed.
                resolved = jsonpath_get(src_ports, sport)
                if resolved is None:
                    continue
                # fan_in="concat": the worker's port is a list-of-arrays; flatten it
                # one level into a single flat list (vs "list" which passes it through).
                if edge.fan_in == "concat":
                    resolved = _fan_concat(resolved)
                ins[dport] = resolved
        return ins

    # Track which nodes have finished
    completed: set[str] = set()
    # Per-edge loop fire counter (loop_max edges only), keyed by internal hash id.
    edge_by_id = {edge_id: edge for edge, edge_id in zip(wf.edges, edge_ids, strict=True)}
    loop_counters: dict[str, int] = {}
    # Determinism memo ledger: memo_key(node_id, inputs) -> output ports (persisted in checkpoint).
    memo: dict[str, dict[str, object]] = {}

    # -----------------------------------------------------------------------
    # Checkpoint restore — only when both checkpoint store and run_id given.
    # -----------------------------------------------------------------------
    _ckpt_active = checkpoint is not None and run_id is not None

    if _ckpt_active:
        assert checkpoint is not None and run_id is not None  # narrow types
        snap = checkpoint.load(run_id)
        if snap is not None:
            expected_keys = {
                "outputs",
                "completed",
                "loop_counters",
                "stack",
                "out_live",
                "memo",
                "tokens_used",
            }
            # `sessions` is optional so a checkpoint written before `inherit`
            # existed still resumes — it simply carries no sessions, which is
            # the same state a run has before any agent node has spoken.
            if set(snap) - {"sessions"} != expected_keys:
                raise WorkflowExecutionError("checkpoint does not match the current schema")
            if "sessions" in snap and not isinstance(snap["sessions"], dict):
                raise WorkflowExecutionError("checkpoint sessions must be a mapping")
            if not isinstance(snap["outputs"], dict) or not isinstance(snap["completed"], list):
                raise WorkflowExecutionError("checkpoint outputs/completed have invalid types")
            if not isinstance(snap["loop_counters"], dict) or not isinstance(snap["stack"], list):
                raise WorkflowExecutionError("checkpoint loop_counters/stack have invalid types")
            if not isinstance(snap["out_live"], dict) or not isinstance(snap["memo"], dict):
                raise WorkflowExecutionError("checkpoint out_live/memo have invalid types")
            if not isinstance(snap["tokens_used"], int):
                raise WorkflowExecutionError("checkpoint tokens_used must be an integer")

            outputs = {str(k): dict(v) for k, v in snap["outputs"].items() if isinstance(v, dict)}
            sessions = {
                str(k): dict(v)
                for k, v in (snap.get("sessions") or {}).items()
                if isinstance(v, dict)
            }
            completed = {str(node_id) for node_id in snap["completed"]}
            lc_raw = snap["loop_counters"]
            loop_counters = {
                str(edge_id): count
                for edge_id, count in lc_raw.items()
                if edge_id in edge_by_id and isinstance(count, int)
            }
            stack = [dict(frame) for frame in snap["stack"] if isinstance(frame, dict)]
            out_live = {str(k): v for k, v in snap["out_live"].items()}
            step_counter = len(stack)
            memo = {str(k): dict(v) for k, v in snap["memo"].items() if isinstance(v, dict)}
            tokens_used = snap["tokens_used"]

            # A strict while may have checkpointed its final completed node just
            # before the scheduler detected non-convergence.  Re-evaluate terminal
            # strict loops on restore so a fully-completed checkpoint cannot turn a
            # previous non-convergence failure into a successful resumed result.
            for edge_id, count in loop_counters.items():
                edge = edge_by_id[edge_id]
                if (
                    edge.loop_strict
                    and edge.loop_max is not None
                    and count >= edge.loop_max
                    and edge.when is not None
                    and evaluate(edge.when, _source_ports(edge.src))
                ):
                    raise _strict_loop_error(edge)

    def _checkpoint_snapshot() -> dict[str, object]:
        """Materialize the checkpoint schema.

        ``sessions`` joined the original seven when agent nodes gained
        ``inherit``: without it, resuming a run would hand the child an empty
        context and produce a plausible, wrong answer rather than an error.
        """
        return {
            "outputs": outputs,
            "sessions": sessions,
            "completed": list(completed),
            "loop_counters": dict(loop_counters),
            "stack": stack,
            "out_live": out_live,
            "memo": memo,
            "tokens_used": tokens_used,
        }

    def _persist_checkpoint(snapshot: dict[str, object]) -> None:
        if not _ckpt_active:
            return
        assert checkpoint is not None and run_id is not None
        checkpoint.save(run_id, snapshot)

    checkpoint_interceptor = CheckpointInterceptor(_checkpoint_snapshot, _persist_checkpoint)

    async def _reserve_step() -> int:
        """Reserve the next monotonic step number (under the state lock)."""
        nonlocal step_counter
        async with _state_lock:
            step = step_counter
            step_counter += 1
        return step

    async def _record_frame(node_id: str, ins: dict[str, object], step: int) -> None:
        """Append a delta trace frame for a just-run node, and flush its outputs.

        Records the node's step, assembled input namespace, and its output ports.
        A looped node produces one frame per iteration, so the trace shows the
        refinement history.  If the node feeds ``$output``, its mapped keys are
        written to the live workflow output immediately (a looped writer's latest
        value wins).  Must be called after the node's outputs are stored.
        """
        async with _state_lock:
            node_ports = dict(outputs.get(node_id, {}))
            stack.append({"step": step, "node": node_id, "in": dict(ins), "out": node_ports})
            for edge in edges_from.get(node_id, []):
                if edge.dst != OUT_NODE_ID:
                    continue
                if edge.when is not None and not evaluate(edge.when, node_ports):
                    continue
                for sport, okey in edge.mapping:
                    # ``sport`` may be a bare port ("verdict") or a JSONPath into a
                    # structured port ("$.verdict.within_budget"); jsonpath_get
                    # resolves both (mirrors codegen's $output flush).
                    head = _mapping_root(sport)
                    if head in node_ports:
                        resolved = jsonpath_get(node_ports, sport)
                        if resolved is not None:
                            if edge.fan_in == "concat":
                                resolved = _fan_concat(resolved)
                            out_live[okey] = resolved

    def _emit(ev: FlowEvent) -> None:
        """Deliver a lifecycle event to the caller's callback (best-effort)."""
        if on_event is None:
            return
        try:
            on_event(ev)
        except Exception:
            pass

    def _emit_started(node_id: str, step: int, ins: Mapping[str, object]) -> None:
        """Announce an activation along with a preview of what it was handed."""
        _emit(NodeStarted(node_id=node_id, step=step, inputs_preview=preview_ports(ins)))

    def _out_preview(node_id: str) -> str:
        """Preview the ports a node just stored.

        Read from the store rather than passed in: every NodeFinished is emitted
        after the node's outputs are committed, so threading the value through
        fifteen call sites would only create ways for them to disagree.
        """
        return preview_ports(outputs.get(node_id, {}))

    async def _fan_instance(
        node: NodeDef, node_id: str, ins: dict[str, object], step: int, fan_index: int
    ) -> tuple[dict[str, object], int]:
        """Run ONE fan-out instance's core work; return (output-port dict, tokens).

        Pure w.r.t. the run store — no storing, no events, no retry.  Reuses the
        same pure node functions as the non-fan path so a worker behaves identically
        whether or not it is fanned.
        """
        if node.type == "script":
            out = await _node_script(node, node_id, ins, step, fan_index)
            return out, 0
        value, tokens = await _node_agent(node, node_id, ins, step, fan_index)
        # Project the agent's returned value into its output-port dict (same rule
        # as _store_agent_output, but pure — returns instead of mutating outputs).
        projected: dict[str, object] = {}
        if node.output_ports:
            if agent_submits_object(node):
                if not isinstance(value, dict):
                    raise WorkflowExecutionError(
                        f"Node {node_id!r}: multi-output agent must submit an object, "
                        f"got {type(value).__name__}"
                    )
                for p in node.output_ports:
                    if p.name not in value:
                        raise WorkflowExecutionError(f"Node {node_id!r}: submitted result is missing field {p.name!r}")
                    projected[p.name] = to_state(value[p.name], p.type)
            else:
                projected[node.output_ports[0].name] = value
        return projected, tokens

    async def _run_fan_node(
        node: NodeDef,
        node_id: str,
        fan_edge: EdgeDef,
        step: int,
        enabled_edge_ids: tuple[str, ...],
    ) -> None:
        """Run *node* once per element of the fan_out source array (G1).

        Aggregates each output port across instances into an index-ordered list and
        stores it under the single ``node_id`` — so the scheduler, trace (one frame),
        and checkpoint (one completed id) are unchanged.  The worker's instances run
        fully in parallel (matching codegen's ``asyncio.gather``); the fan node holds
        one outer concurrency slot, its instances do not re-acquire it.
        """
        nonlocal tokens_used
        async with _state_lock:
            shared = _build_inputs(node_id, enabled_edge_ids)
        # The fan_out array lives on the source node's output port named by the
        # mapping whose root is fan_edge.fan_out; that mapping's dst is the worker's
        # per-element input port.
        src_ports = _source_ports(fan_edge.src)
        items: list[object] = []
        worker_port = ""
        for sport, dport in fan_edge.mapping:
            if _mapping_root(sport) == fan_edge.fan_out:
                raw = jsonpath_get(src_ports, sport)
                items = list(raw) if isinstance(raw, (list, tuple)) else []
                worker_port = dport
                break

        _emit_started(node_id, step, shared)
        _t0 = time.monotonic()

        # Dedicated per-fan limiter: cap how many instances run at once WITHOUT
        # touching the scheduler semaphore (re-acquiring it here would self-nest
        # and deadlock at cap=1).  0/negative = unlimited.  Both engines apply the
        # same cap so interpret == compile holds.
        _fan_cap = wf.fan_max_concurrency
        _fan_sem: asyncio.Semaphore | None = asyncio.Semaphore(_fan_cap) if _fan_cap > 0 else None

        async def _one(i: int) -> tuple[dict[str, object], int]:
            ins_i = {**shared, worker_port: items[i]}
            if _fan_sem is None:
                return await _fan_instance(node, node_id, ins_i, step, i)
            async with _fan_sem:
                return await _fan_instance(node, node_id, ins_i, step, i)

        results = await asyncio.gather(*[_one(i) for i in range(len(items))])

        # Aggregate: each output port becomes an index-ordered list of per-instance values.
        agg: dict[str, object] = {p.name: [r[0].get(p.name) for r in results] for p in node.output_ports}
        total_tokens = sum(r[1] for r in results)
        async with _state_lock:
            outputs[node_id] = agg
        # One trace frame for the whole fan (its 'in' shows the fanned array).
        await _record_frame(node_id, {worker_port: items} if worker_port else {}, step)
        async with _state_lock:
            tokens_used += total_tokens
        _emit(NodeFinished(
            node_id=node_id, step=step,
            duration_s=time.monotonic() - _t0,
            tokens=total_tokens,
            instances=len(items),
            output_preview=_out_preview(node_id),
        ))

    async def _run_subflow_node(node: NodeDef, node_id: str, ins: dict[str, object], step: int) -> None:
        """Run a ``type="subflow"`` node (G5) as ONE opaque nested workflow.

        Calls the same ``execute()`` on the inline child, mapping the parent's
        ``ins`` into the child's ``$in`` and the child's ``runtime["out"]`` back
        into this node's derived output ports.  The child owns its own scheduler,
        checkpoint (a node-qualified run-id), trace, tokens, isolation, and
        semaphore — the parent scheduler sees one node that completes once.
        Mirrors codegen's ``node_X`` that calls ``execute()`` on the embedded child.
        """
        nonlocal tokens_used
        assert node.child is not None
        _emit_started(node_id, step, ins)
        _t0 = time.monotonic()

        # Test mode: a stubbed subflow is answered whole — the child never runs.
        # This is the intended decomposition, since a child workflow carries its own
        # .test.json; nodes *inside* a child are deliberately not stubbable.
        if stubs is not None:
            stubbed = stubs.subflow(node, inputs=ins, step=step)
            if stubbed is not None:
                projected_stub = {p.name: stubbed[p.name] for p in node.output_ports if p.name in stubbed}
                async with _state_lock:
                    outputs[node_id] = projected_stub
                await _record_frame(node_id, ins, step)
                _emit(NodeFinished(node_id=node_id, step=step, duration_s=time.monotonic() - _t0, tokens=0,
                                   output_preview=_out_preview(node_id)))
                return

        # Parent ins -> child $in (by the derived input-port names).
        child_inputs = {p.name: ins[p.name] for p in node.input_ports if p.name in ins}
        # Remaining token budget (None = unlimited); the child's own breaker trips
        # if it overruns, and we re-check after it returns.
        child_budget: int | None = None
        if max_tokens is not None and max_tokens > 0:
            child_budget = max(0, max_tokens - tokens_used)
        child_run_id = f"{run_id}::{node_id}" if run_id else None

        child_result = await execute(
            node.child,
            stream_fn_factory=stream_fn_factory,
            timeout=timeout,
            tool_repo=tool_repo,
            script_resolver=script_resolver,
            base_dir=base_dir,
            inputs=child_inputs,
            web_search_fn_factory=web_search_fn_factory,
            checkpoint=checkpoint,
            run_id=child_run_id,
            max_tokens=child_budget,
            # The workspace and its grants travel into the child, so a subflow
            # writes where its parent writes rather than inventing a
            # `runtime/runtime/` of its own.  The child applies the same rules to
            # its own nodes; nothing here inspects it more deeply than that.
            workspace=_workspace,
            allow_paths=allow_paths,
            confined=confined,
            # Inherited so an UNSTUBBED child can never construct a real provider:
            # the stubs object knows only the parent's node ids, so any agent node
            # in here raises "no stub" instead of reaching the network.
            stubs=stubs,
        )
        child_out = child_result.runtime.get("out", {})
        child_tokens = child_result.runtime.get("tokens_used", 0)
        assert isinstance(child_out, dict)

        # Child $output -> this node's derived output ports.
        projected: dict[str, object] = {p.name: child_out[p.name] for p in node.output_ports if p.name in child_out}
        async with _state_lock:
            outputs[node_id] = projected
        await _record_frame(node_id, ins, step)
        async with _state_lock:
            tokens_used += child_tokens if isinstance(child_tokens, int) else 0
        _emit(NodeFinished(
            node_id=node_id, step=step,
            duration_s=time.monotonic() - _t0,
            tokens=child_tokens if isinstance(child_tokens, int) else 0,
            output_preview=_out_preview(node_id),
        ))

    async def _run_node(node_id: str, step: int, enabled_edge_ids: tuple[str, ...]) -> None:
        """Execute one scheduler activation and store its output ports."""
        # Skip nodes already completed (resume from checkpoint).
        if node_id in completed:
            return
        node = node_map[node_id]

        # Dynamic fan-out (G1): if a fan_out edge feeds this node, run it once per
        # element of the source array and aggregate each output port into an
        # index-ordered list.  From the scheduler's view this is still ONE node.
        _fan_edge = _incoming_fan_out_edge(node_id, in_edges)
        if _fan_edge is not None:
            await _run_fan_node(node, node_id, _fan_edge, step, enabled_edge_ids)
            return
        async with _state_lock:
            ins = _build_inputs(node_id, enabled_edge_ids)

        # Determinism memo hit: reuse stored output keyed by (node_id, hash(inputs)).
        if node.deterministic:
            _mk = _memo_key(node_id, ins)
            if _mk in memo:
                _emit_started(node_id, step, ins)
                _t0_memo = time.monotonic()
                async with _state_lock:
                    outputs[node_id] = dict(memo[_mk])
                await _record_frame(node_id, ins, step)
                _emit(NodeFinished(node_id=node_id, step=step, duration_s=time.monotonic() - _t0_memo, tokens=0,
                                   output_preview=_out_preview(node_id)))
                return

        if node.type == "human":
            if node.signal in _signals:
                # Signal present — instant approval pass
                _emit_started(node_id, step, ins)
                _t0_human = time.monotonic()
                approval_val = "approved"
                async with _state_lock:
                    outputs.setdefault(node_id, {})
                    if node.output_ports:
                        outputs[node_id][node.output_ports[0].name] = approval_val
                await _record_frame(node_id, ins, step)
                _emit(NodeFinished(node_id=node_id, step=step, duration_s=time.monotonic() - _t0_human, tokens=0,
                                   output_preview=_out_preview(node_id)))
            else:
                raise WorkflowPaused(node_id, node.signal)
            return

        if node.type == "subflow":
            await _run_subflow_node(node, node_id, ins, step)
            return

        if node.type == "script":
            logger.debug("Running script node %r", node_id)
            _t0 = time.monotonic()
            _emit_started(node_id, step, ins)

            max_attempts, backoff = _retry_bounds(node)
            last_exc: BaseException | None = None
            script_out: dict[str, object] = {}
            for attempt in range(max_attempts):
                try:
                    script_out = await _node_script(node, node_id, ins, step)
                    last_exc = None
                    break
                except Exception as exc:  # not BaseException: never retry Cancelled/KeyboardInterrupt
                    last_exc = exc
                    await _retry_wait(node_id, attempt, max_attempts, backoff)
            if last_exc is not None:
                _emit(NodeFailed(
                    node_id=node_id, step=step,
                    duration_s=time.monotonic() - _t0,
                    error=f"{type(last_exc).__name__}: {last_exc}",
                ))
                raise last_exc
            if script_out:
                async with _state_lock:
                    outputs.setdefault(node_id, {}).update(script_out)
            await _record_frame(node_id, ins, step)
            async with _state_lock:
                if node.deterministic:
                    memo[_memo_key(node_id, ins)] = dict(outputs.get(node_id, {}))
            _emit(NodeFinished(node_id=node_id, step=step, duration_s=time.monotonic() - _t0, tokens=0,
                                   output_preview=_out_preview(node_id)))
            return

        # agent node — driver: retry the pure _node_agent, then store + budget.
        agent_max_attempts, agent_backoff = _retry_bounds(node)
        agent_last_exc: BaseException | None = None
        agent_step = step
        _t0_agent = time.monotonic()
        _emit_started(node_id, agent_step, ins)
        output_value: object = ""
        total_tokens = 0

        for agent_attempt in range(agent_max_attempts):
            try:
                output_value, total_tokens = await _node_agent(node, node_id, ins, agent_step)
                agent_last_exc = None
                break
            except Exception as exc:  # not BaseException: never retry Cancelled/KeyboardInterrupt
                agent_last_exc = exc
                await _retry_wait(node_id, agent_attempt, agent_max_attempts, agent_backoff)

        if agent_last_exc is not None:
            _emit(NodeFailed(
                node_id=node_id, step=agent_step,
                duration_s=time.monotonic() - _t0_agent,
                error=f"{type(agent_last_exc).__name__}: {agent_last_exc}",
            ))
            raise agent_last_exc

        async with _state_lock:
            _store_agent_output(node, node_id, output_value)

        await _record_frame(node_id, ins, agent_step)
        async with _state_lock:
            nonlocal tokens_used
            tokens_used += total_tokens  # before the checkpoint so the saved total is current
            if node.deterministic:
                memo[_memo_key(node_id, ins)] = dict(outputs.get(node_id, {}))
        _emit(NodeFinished(
            node_id=node_id, step=agent_step,
            duration_s=time.monotonic() - _t0_agent,
            tokens=total_tokens,
            output_preview=_out_preview(node_id),
        ))
        # Budget circuit-breaker: abort once the cumulative total passes the ceiling.

    def _coerce_script_output(node: NodeDef, node_id: str, value: Any) -> dict[str, object]:
        """Coerce a script's return value into its output-port dict (no store)."""
        ports = node.output_ports
        if len(ports) <= 1:
            if not ports:
                return {}
            p = ports[0]
            return {p.name: to_state(value, p.type)}
        # Multi-output script: the function must return a dict keyed by port name.
        if not isinstance(value, dict):
            raise WorkflowExecutionError(
                f"Script node {node_id!r}: declares {len(ports)} output ports so must return a dict"
            )
        return {p.name: to_state(value[p.name], p.type) for p in ports}

    async def _node_script(
        node: NodeDef,
        node_id: str,
        ins: dict[str, object],
        step: int,
        fan_index: int | None = None,
    ) -> dict[str, object]:
        """Run a script node's core work once; return its output-port dict.

        Pure with respect to the run store: it resolves the function, builds the
        typed kwargs from *ins*, calls it (async or sync), and coerces the return
        into the output-port dict.  Storing, retry, memo, and events are the
        driver's job (the caller).
        """
        if stubs is not None:
            stubbed = stubs.script(node, inputs=ins, step=step, fan_index=fan_index)
            if stubbed is not None:
                return _coerce_script_output(node, node_id, _script_stub_value(node, stubbed))
        # Inline `code` is exec'd inside the bound: its top-level statements are
        # part of the script, carried in the workflow file, and ran unbounded
        # until this was tested. A `run:` ref is imported outside it -- that is
        # loading a module from disk, with the same trust as any other
        # dependency, and its import-time code is no more bounded than theirs.
        _code_roots = [base_dir] if base_dir is not None else []
        if node.code is not None:
            with script_bound(
                _workspace, allow_paths or (), confined=confined, code_roots=_code_roots
            ):
                fn = _resolve_script_fn(node, base_dir)
        elif script_resolver is not None:
            fn = script_resolver(node.run or "")
        else:
            fn = _resolve_script_fn(node, base_dir)
        ctx = RuntimeContext(
            step=step, node_id=node_id, workflow_name=wf.name,
            workspace=_workspace, allow_paths=tuple(allow_paths or ()), confined=confined,
        )
        kwargs = {p.name: to_python(ins.get(p.name, ""), p.type) for p in node.input_ports}
        # A script node is the one place flow runs code it cannot otherwise see,
        # so it is the one place an audit hook is worth its cost.  The bound is
        # applied whether or not the run is confined -- `--confined` only adds the
        # refusal of calls the hook cannot follow.  `base_dir` is readable because
        # a `run: module:callable` node has to import itself from there.
        # Writes: only the workspace and the paths a human granted. `base_dir` is
        # deliberately not among them -- it is wherever the .json happens to live,
        # so granting it would let a workflow in a home directory write across it.
        # It is a *code* root instead: a module may import its siblings, which is
        # the same trust that resolved this node, while data files sitting beside
        # the workflow stay outside the bound.
        with _importable(base_dir), script_bound(
            _workspace, allow_paths or (), confined=confined, code_roots=_code_roots
        ):
            raw = fn(ctx, **kwargs)
            value = await asyncio.wait_for(
                raw if inspect.isawaitable(raw) else _wrap_sync(raw), timeout=timeout
            )
        return _coerce_script_output(node, node_id, value)

    _sdk_runner_cache: list[SdkRunner] = []
    _cli_runner_cache: dict[str, CliRunner] = {}

    def _runner_for(node: NodeDef) -> AgentRunner:
        """Select the agent backend for *node*.

        In test mode (``stubs`` supplied) the stub runner answers for **every** agent
        node, whatever its ``backend`` — so no provider or CLI is ever constructed
        and a test cannot reach the network.
        """
        if stubs is not None:
            return StubRunner(stubs)
        if node.backend is not None:
            runner = _cli_runner_cache.get(node.backend)
            if runner is None:
                runner = CliRunner(cli_adapter_for(node.backend))
                _cli_runner_cache[node.backend] = runner
            return runner
        if not _sdk_runner_cache:
            assert stream_fn_factory is not None
            assert tool_repo is not None
            _sdk_runner_cache.append(
                SdkRunner(
                    stream_fn_factory=stream_fn_factory,
                    tool_repo=tool_repo,
                    web_search_fn_factory=web_search_fn_factory,
                    workspace=_workspace,
                    allow_paths=tuple(allow_paths or ()),
                    skill_dirs=([base_dir / "skills"] if base_dir is not None else []),
                    confine_to=_confinement_roots,
                )
            )
        return _sdk_runner_cache[0]

    async def _node_agent(
        node: NodeDef,
        node_id: str,
        ins: dict[str, object],
        step: int,
        fan_index: int | None = None,
    ) -> tuple[object, int]:
        """Run an agent node's core work once; return (output_value, tokens).

        Node-generic part: interpolate the prompts and resolve the model, then
        delegate the turn to the node's selected backend runner (SDK by default).
        Retry, store, budget, and events are the driver's job.
        """
        sys_prompt = interpolate(node.system_prompt, ins)
        user_prompt = interpolate(node.prompt, ins)
        model = node.model or wf.default_model
        logger.debug("Running node %r with model %r", node_id, model)
        runner = _runner_for(node)
        inherited = sessions.get(node.inherit.from_node) if node.inherit else None
        session_sink: dict[str, object] = {}
        result = await runner.run(
            node,
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            model=model,
            timeout=timeout,
            inputs=ins,
            step=step,
            fan_index=fan_index,
            session=inherited,
            session_sink=session_sink,
        )
        captured = session_sink.get("session")
        if isinstance(captured, dict):
            async with _state_lock:
                sessions[node_id] = captured
        return result

    def _store_agent_output(node: NodeDef, node_id: str, output_value: object) -> None:
        """Store an agent's output value into its port(s) (caller holds _state_lock)."""
        if not node.output_ports:
            return
        if agent_submits_object(node):
            if not isinstance(output_value, dict):
                raise WorkflowExecutionError(
                    f"Node {node.id!r}: multi-output agent must submit an object, "
                    f"got {type(output_value).__name__}"
                )
            for p in node.output_ports:
                if p.name not in output_value:
                    raise WorkflowExecutionError(
                        f"Node {node.id!r}: submitted result is missing field {p.name!r}"
                    )
                outputs.setdefault(node_id, {})[p.name] = to_state(output_value[p.name], p.type)
        else:
            outputs.setdefault(node_id, {})[node.output_ports[0].name] = output_value

    def _transitive_successors(node_id: str) -> set[str]:
        """Return all nodes reachable from node_id (forward edges only, not loop edges)."""
        visited: set[str] = set()
        queue = [node_id]
        while queue:
            cur = queue.pop()
            for edge in edges_from.get(cur, []):
                if edge.loop_max is not None:
                    continue
                if edge.dst == OUT_NODE_ID:
                    continue  # sink, not a runnable node
                if edge.dst not in visited:
                    visited.add(edge.dst)
                    queue.append(edge.dst)
        return visited

    # --- Unified frontier scheduler ---------------------------------------
    frontier_spec = build_frontier_spec(wf)
    frontier_state = new_frontier_state(frontier_spec, completed)

    def _stopped_by() -> dict[str, str] | None:
        """Why the run ended, or None when it simply ran out of work.

        A plain ``loop`` that hits its bound stops silently, which otherwise
        looks exactly like a natural completion — same ``success: true``, same
        empty ``$output``. Naming the edge makes the two distinguishable.
        """
        label = exhausted_edge_label(frontier_spec, frontier_state)
        return {"reason": "loop_exhausted", "edge": label} if label else None

    # Isolation tracking: nodes whose failure was captured rather than propagated,
    # plus a dict mapping node_id -> error string for all isolated failures.
    isolated: set[str] = set()
    failed: dict[str, str] = {}

    def _resolve_outgoing(node_id: str) -> dict[str, bool]:
        resolved: dict[str, bool] = {}
        for edge, edge_id in zip(
            edges_from.get(node_id, []),
            edge_ids_from.get(node_id, []),
            strict=True,
        ):
            if edge.dst == OUT_NODE_ID:
                continue
            resolved[edge_id] = edge.when is None or evaluate(edge.when, _source_ports(edge.src))
        return resolved

    # Reconstruct transient forward reachability from the existing checkpoint fields.
    # Loop transitions are not replayed: loop_counters already encode committed fires.
    if _ckpt_active and completed:
        for node in wf.nodes:
            if node.id in completed:
                replay_completed(
                    frontier_spec,
                    frontier_state,
                    node.id,
                    _resolve_outgoing(node.id),
                )
        restore_loop_activations(frontier_spec, frontier_state, loop_counters)

    while True:
        ready = take_ready(frontier_spec, frontier_state)
        if not ready:
            break  # quiescence is normal graph completion

        scheduled: list[tuple[str, int, tuple[str, ...], int]] = []
        for node_id, epoch, enabled_edge_ids in ready:
            step = await _reserve_step()
            scheduled.append((node_id, epoch, enabled_edge_ids, step))

        if _sem is not None:
            _cap_sem = _sem

            async def _run_capped(
                node_id: str,
                step: int,
                enabled_edge_ids: tuple[str, ...],
            ) -> None:
                async with _cap_sem:
                    await _run_node(node_id, step, enabled_edge_ids)

            results = await asyncio.gather(
                *[
                    _run_capped(node_id, step, enabled_edge_ids)
                    for node_id, _epoch, enabled_edge_ids, step in scheduled
                ],
                return_exceptions=True,
            )
        else:
            results = await asyncio.gather(
                *[
                    _run_node(node_id, step, enabled_edge_ids)
                    for node_id, _epoch, enabled_edge_ids, step in scheduled
                ],
                return_exceptions=True,
            )

        deferred_exception: BaseException | None = None
        successful: list[tuple[str, int, dict[str, bool]]] = []
        for (node_id, epoch, _enabled_edge_ids, _step), result in zip(
            scheduled,
            results,
            strict=True,
        ):
            if not isinstance(result, BaseException):
                successful.append((node_id, epoch, _resolve_outgoing(node_id)))
                continue
            if isinstance(result, (WorkflowPaused, WorkflowBudgetExceeded)):
                if deferred_exception is None:
                    deferred_exception = result
                continue
            node_def = node_map[node_id]
            if node_def.on_error == "isolate":
                failed[node_id] = f"{type(result).__name__}: {result}"
                scope = {node_id} | _transitive_successors(node_id)
                isolated.update(scope)
                isolate_nodes(frontier_state, scope)
            elif deferred_exception is None:
                deferred_exception = result

        def _settle_frontier_batch() -> None:
            strict_edge_id = complete_batch(
                frontier_spec,
                frontier_state,
                successful,
                loop_counters,
            )
            if strict_edge_id is not None:
                raise _strict_loop_error(edge_by_id[strict_edge_id])
            frontier_completed = frontier_state["completed"]
            assert isinstance(frontier_completed, dict)
            completed.clear()
            completed.update(str(node_id) for node_id in frontier_completed)

        if successful:
            checkpoint_interceptor.intercept("frontier-batch", _settle_frontier_batch)
        elif isinstance(deferred_exception, WorkflowPaused):
            checkpoint_interceptor.commit("human-pause")

        if max_tokens is not None and max_tokens > 0 and tokens_used > max_tokens:
            raise WorkflowBudgetExceeded(tokens_used, max_tokens)
        if deferred_exception is not None:
            raise deferred_exception

    last = stack[-1] if stack else {}
    runtime: dict[str, Any] = {
        "ctx": {
            "step": last.get("step", -1),
            "node_id": last.get("node", ""),
            "workflow_name": wf.name,
        },
        "stack": stack,
        "state": {k: v for k, v in outputs.items() if k != IN_NODE_ID},
        "in": outputs[IN_NODE_ID],
        "out": out_live,
        "failed": failed,
        "memo": memo,
        "tokens_used": tokens_used,
        # Why the run ended, when it was not simply "everything finished".
        "stopped_by": _stopped_by(),
    }
    return ExecResult(runtime=runtime)
