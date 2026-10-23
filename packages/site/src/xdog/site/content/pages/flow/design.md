---
title: Design
---

How Flow turns one typed JSON artifact into a workflow that developers can edit
visually and Coding Agents can generate, validate, and repair. For the exact
field-by-field schema and validation rules, see the
[Reference](/packages/flow/reference).

## Node-private ports, not shared state

A flow workflow is a graph of nodes connected by edges. Data does not travel
through a shared global dict — each node declares typed input and output ports,
and each edge carries an explicit mapping that says which source output port
feeds which destination input port.

Because the wiring is spelled out rather than implied by matching key names, the
graph can be validated before it runs: unknown ports, ambiguous unconditional
producers feeding one input, or an unfed required input all fail fast at load time.

## One frontier execution model

The interpreter and generated module execute the same frontier transition kernel.
A node becomes ready when its ordinary predecessors complete; the complete ready
frontier runs concurrently in declaration order. Condition-enabled edges provide
inputs, while false edges do not leak values into a destination.

All bounded back-edges entering one destination form a conditional AND loop join:
every source must complete in the current generation and every guard must hold
before that destination runs again, exactly once. Each edge keeps its own `max`
and strict-`while` behavior. Dynamic `fan_out` remains one frontier node whose
runtime instances use a separate concurrency cap and preserve input order.

## One runtime container in, workflow outputs out

Two reserved synthetic nodes bracket every run. The workflow's state block is
exposed as the output ports of a source node called `$in`, so the same graph
runs with different inputs without editing the JSON. Nodes wire their output
ports to a sink node called `$output` with ordinary edges, and those collected
key/value pairs are the workflow's result — flushed the moment each feeding node
finishes, so a looped writer's latest value wins.

`execute()` returns an `ExecResult` whose `runtime` holds `ctx`, `stack`, `state`,
`in`, `out`, failures, memo, and token usage. Process boundaries (`xdog-flow run`
and generated Python) print a stable envelope instead:

```json
{
  "success": true,
  "message": "Workflow completed",
  "output": {},
  "context": {
    "workflow": "...",
    "runId": null,
    "startTime": "...",
    "endTime": "...",
    "durationMs": 42,
    "tokensUsed": 0,
    "lastNode": "..."
  }
}
```

## Typed ports and JSON Schema

Every port carries a **JSON Schema** — a scalar (`{"type": "integer"}`) or a
nested object/array (`{"type": "object", "properties": {…}}`). The wire format is
**type-native**: a port value is the live Python value (int, float, bool, list,
dict), not a stringified form, so structure flows between nodes intact and a
downstream node reads a real object. A script node sees its inputs coerced to the
declared top-level type; nested structure is validated (by fastjsonschema), not
re-stringified.

An input port is `required` by default. Marking it `required: false` exempts it
from the rule that every declared input must be fed by an edge — that is how a
loop-carried value, absent on the first pass and supplied only by the back-edge,
stays internal instead of leaking into the workflow's user-facing inputs.

## JSONPath data flow

A prompt reads a port — or a field inside one — with JSONPath:
`{{ $.plan.tasks[0] }}` pulls the first task out of a structured `plan` port. An
edge map can do the same on the source side: `"map": {"$.verdict.within_budget":
"flag"}` wires a nested boolean straight into a downstream input, and the
loader type-checks that sub-field against the source port's schema. Both the
interpreter and the generated module resolve paths through one shared
`jsonpath-ng` evaluator, so interpolation and conditions mean exactly the same
thing on both run paths.

## Two ways to run: interpret or compile

The same JSON can execute directly or compile to a Python module with inlined
runtime helpers and static graph metadata.
Codegen embeds workflow-specific node functions, static graph metadata, and the
exact same frontier transition kernel used by the interpreter. It does not
translate the graph into a second BFS/for-loop control-flow implementation.

Cross-cutting behavior stays aligned: enabled-edge input assembly, retries,
structured output, memoization, token budgets, failure isolation, fan-out, and
coherent frontier-batch checkpoints. A cross-engine parity suite enforces that
both paths produce the same node state and `$output`.

This does not make every artifact dependency-free. Generated SDK nodes import
`xdog.agent`/`xdog.ai`; subflow functions import `xdog.flow.executor` and
`xdog.flow.loader` to run an embedded child definition. Custom imports and
third-party libraries remain dependencies. `--portable` vendors selected sources
(including `xdog.flow` for subflow parents), not a recursive closure over every
child/custom dependency. Provision and test the actual artifact before deployment.

## Structured output and web search

An agent's structured output is **derived from its typed `outputs`** — not a
separate `output_schema` field. No outputs or one plain `{"type": "string"}`
output uses assistant text. Other contracts use structured values: a single
structured port receives its schema's native value directly (array/object/scalar),
while multiple ports receive fields of a port-keyed object.

The SDK runner uses the existing `xdog.agent.Agent` loop and injects
`submit_result` with generic `result_schema` and caller-owned `result_sink`.
Successful validation writes `result_sink["result"]`; invalid submission leaves
the sink unchanged so the model can correct it. Generated SDK nodes use the same
contract. Old `flow_output_schema` / `flow_result_sink` keys are rejected.
External CLI backends instead use native schema flags and parse structured CLI
output; they do not receive the in-process SDK tool or sink.

An agent node can also enable a built-in `web_search` tool, optionally naming a
distinct browsing model (some models don't browse, so a workflow can run the
node on one model and search with another). Tools beyond the built-ins are
declared in a JSON manifest of `module:function` references, loaded at both run
and generate time.

## Testing: stub the boundary, run the graph

The same seam that lets an agent node choose an SDK or CLI backend is where a test
stub goes. A companion `<name>.test.json` installs a stub runner for **every** agent
node, so no provider or external agent CLI is constructed; a missing stub fails
instead of falling back to a live backend. Human signals and whole subflow nodes
are stubbable the same way, and script nodes only behind an explicit opt-in. Unstubbed scripts and import-time
custom code remain real and can access files, processes or the network; tests
are not a general no-effects mode.

The placement matters more than the mechanism. The stub answers *after* prompt
interpolation and *before* output parsing, so a stubbed value still passes through
the node's required-field check and type coercion — it is validated by the same code
that validates a live model response, and a typo'd `{{ $.path }}` still fails. What
remains un-stubbed is everything a workflow test is actually for: edges, conditions,
bounded loops, fan-out and its aggregation, mappings, retry, and `$output`
collection.

Selection never depends on completion order — a rule matches on input values, on a
fan instance's array position, or on which activation of the node it is — so a
fan-out case with three concurrent instances resolves identically every run.

## Human and Agent authoring

The interactive terminal builder (`xdog-flow build`) edits the graph, script
functions, and tool declarations while round-tripping the canonical JSON. A
future local Web UI will edit the same file — not a second database-owned model —
and add graph forms, validation, execution, structured results, scheduling, and
run inspection.

Installed capabilities contribute a discoverable skill and `flow` tool at
application scope. Discovery is inert: explicit activation loads the skill body,
and the host's normal permissions govern tool calls. A workflow node's own
`skills` list is explicit activation, not passive capability discovery.

The `flow` tool passes argv to the public CLI without a shell and returns
`{exit_code, stdout, stderr}`. It supports non-interactive operations, not the
manual `build` TUI. There is no Flow daemon or second Agent loop. Cancellation
settles the owned CLI process group, not rollback or uninstall of accepted
schedules; uncertain effects require reconciliation before any new invocation.

Coding Agents are another editor for the same artifact. The Flow skill gives them
examples and authoring rules; precise validation errors support a create → validate
→ repair → preview → human-review loop. Git remains the collaboration and history
layer.

The same definition renders as plain text, layered ASCII, Graphviz SVG (with a
dependency-free fallback), or Mermaid.

## Deliberately single-machine — a kernel, not a platform

Flow schedules one graph in a local asyncio runtime: `pip install`, then
`asyncio.run(execute(wf))` — no hosted server, broker or database service is
required. Tools and CLI nodes can still spawn subprocesses, and checkpoints and
schedule registries can persist local state. That is a
chosen boundary, not an unfinished one. Distributed execution is a non-goal:
building it would mean rewriting the executor's core (a node is a closure over
shared in-process state, not a serialisable task) and would force a trade
against the interpret==compile guarantee that preserves shared semantics in the
generated module.

When a run must scale across machines, embed flow as a **library** — run a flow
graph as one unit of work inside a durable engine like Temporal — rather than
asking flow to become that engine. Keeping the kernel small, type-checked, and
compilable is the value a heavyweight platform cannot offer; competing with
Temporal on distribution would forfeit it. The resilience features flow does
ship (retry, checkpoint/resume, isolation, human-in-the-loop, deterministic
reuse) are all the single-machine, in-kernel kind.

## Non-goals (deliberately out of scope)

- **Distributed execution.** The executor is one asyncio event loop in one
  process; there is no cross-machine worker pool, queue, or per-graph scheduling
  — by design. Cross-machine scale belongs to a durable engine flow is embedded
  in, not to flow itself.
- **Multi-tenancy & auth.** flow is a kernel/library, not a hosted service.
  Isolation, authn/authz, and quotas per tenant are the host environment's job.
  HaveFun runs trusted code; its execution guard is not an OS sandbox.
- **In-engine scheduling daemon.** `xdog-flow scheduling` installs timers and hook
  listeners around generated bundles; the frontier engine itself remains a
  one-shot executor.
- **Compensation / rollback.** Saga-style compensation earns its keep in
  distributed, long-running, cross-service flows — which flow deliberately isn't.
  `on_error:isolate` records a failed branch and skips its descendants, not
  automatic compensation. Scripts receive typed input kwargs and runtime metadata,
  not ambient access to `runtime.failed`. Design explicit effect reconciliation.
- **External telemetry export.** Metrics are aggregated in-kernel by
  `MetricsCollector`; pushing them to OpenTelemetry / Prometheus is left to the
  caller, who consumes the P3 event stream and forwards it — keeping exporter-
  specific dependencies outside the core. Flow itself has runtime dependencies.
