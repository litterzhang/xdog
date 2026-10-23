# Workflow execution, typed data and current limits

This is the current usage reference, not an unimplemented gap/remediation plan.
Flow is a single-machine workflow interpreter and Python code generator. Both
paths share the frontier transition kernel; behavioral parity does **not** mean
that every generated module is dependency-free.

## Ports and edges, not shared state

Every node reads its own `inputs` and writes its own `outputs`. Bare port names
mean required string ports; structured/typed/optional ports use objects:

```json
{"name": "score", "schema": {"type": "number"}, "required": true}
```

The workflow's `state` seeds synthetic source `$in`; it is not a global mutable
namespace visible to all nodes. Synthetic sink `$output` collects only explicit
mappings. An edge maps **source output names to destination input names**:

```json
{"from": "review", "to": "publish", "map": {"score": "quality"}}
```

An empty map is a control dependency. Prompts read the receiving node's inputs;
conditions read the source node's outputs. JSONPath can select nested fields,
e.g. `{{ $.plan.tasks[0] }}` or an edge source `"$.verdict.within_budget"`.
Missing output leaves are omitted rather than invented.

The loader checks referenced nodes/ports, required inputs, ambiguous producers,
schema compatibility, templates, loops, fan topology and child signatures before
execution. Optional inputs (`required: false`) may be absent on an initial loop
pass. `$in` type checks use explicit `in_schema` where supplied; untyped seeds
are not a general static-type guarantee. Complex JSONPath expressions may not be
fully statically typable. Runtime coercion/schema validation remains necessary.

## Readiness and joins

Entry nodes are derived from ordinary predecessors unless `entry` explicitly
selects one node. Ready nodes run concurrently, subject to `max_concurrency`.
An ordinary join waits for all ordinary predecessors; only enabled conditional
edges contribute mapped values. A completed activation evaluates outgoing edges
against that activation's outputs. No work ready/running means graph completion.

Independent branches can run in parallel; this is not distributed execution.
Errors normally fail the workflow. Per-node `retry` and `on_error: "isolate"`
are explicit policies, not compensation or exactly-once effects.

## Conditions and bounded loops

Supported operators are `contains`, `equals`, numeric `gt`/`gte`/`lt`/`lte`, and
`not`/`and`/`or`. For example, a review back-edge can use:

```json
{
  "from": "review", "to": "draft",
  "map": {"feedback": "feedback"},
  "while": {
    "cond": {"lt": {"value": "{{score}}", "text": "0.8"}},
    "max": 2
  }
}
```

This is an edge fragment; nodes must declare/wire the corresponding ports.
`loop` combines `when` with `loop.max`; `while` carries its own `cond` and `max`
and must not be combined with `when` or `loop`. If omitted, a `while` bound uses
the implementation's safe ceiling of 100. Cycles require a finite bound. `loop` and `while` use the same
bounded back-edge mechanism but differ on exhaustion:

- `loop`: successful bounded stop, with `context.stoppedBy` identifying the edge;
- `while`: non-convergence is failure (CLI exit 1).

Multiple bounded edges entering the same destination form an **AND loop join**:
all member sources must complete in the current generation and all conditions
must hold before one reactivation. Each edge keeps its own bound and strictness.
Codegen embeds the same frontier logic, not a separate lexical Python loop.
`context.lastNode` is descriptive and unstable under concurrency;
`context.stoppedBy` is the authoritative stop reason when present.

## Structured results and context

Typed `outputs` determine structured agent results. SDK nodes inject the generic
Agent `submit_result` tool with `result_schema` and caller-owned `result_sink`;
success writes `result_sink["result"]`. A single structured port accepts its
schema's native value; multiple ports split a port-keyed object. External CLI
nodes use their native structured-output flags. See [agent backends](cli-agent.md).

History is not ambient workflow state. Nodes start cold unless they explicitly
use `inherit`; tools/skills remain selected by the receiving node. Script nodes
receive typed inputs as keyword arguments and a `RuntimeContext` first argument.
Filesystem audit/confinement checks are cooperative guards, not an OS sandbox.

## Composition and dynamic mapping

- [Fan-out](fan-out.md): one static worker node runs once per runtime array item.
  `list` keeps one result per item; `concat` flattens array results one level.
  Order is input-index order, not completion order. `fan_max_concurrency` is a
  dedicated inner limiter, separate from the outer scheduler's cap.
- [Subflows](subflow.md): an inline child or JSON file reference becomes one
  opaque node with derived typed ports. Both engines call `xdog.flow.executor`
  for the child. The child is not expanded into the parent scheduler.
- Human nodes wait for explicit signals; scheduled hooks can supply
  `FLOW_SIGNALS`. This is separate from ACP durable input intake in Eyes hosts.

Current loader restrictions include nested fan-out, fan workers inside loops,
nested subflows, subflows inside loops and subflows used as fan workers. Dynamic
whole-subgraph fan-out is not supported.

## Checkpoints and generated dependencies

Checkpoints commit at coherent frontier-batch boundaries, not after each parallel
sibling or fan instance. Incomplete fan groups rerun as a whole on explicit
resume. A subflow has its own namespaced checkpoint when one is supplied. Treat
resumption/retry of effectful nodes as potentially repeated work, not rollback or
exactly-once execution. Interpreter/generated checkpoints are not promised to be
interchangeable.

Generated modules inline scheduler/helpers and static graph metadata. They still
need runtime dependencies: SDK nodes use `xdog.agent`/`xdog.ai`, subflow nodes use
`xdog.flow`, and custom imports remain custom dependencies. Portable bundles
vendor selected sources, not a hermetic environment. Inspect requirements and
verify the actual artifact in its deployment environment.

## Model-free verification

From the repository root:

```bash
uv run xdog-flow validate packages/flow/examples/refine_loop.json --json
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
```

Tests run the interpreter with stubbed agent boundaries, not generated code.
Flow's own cross-engine tests guard parity. Scripts/imports still require trust;
`--dry-run` is not a universal no-effects mode. See [testing](testing.md).
