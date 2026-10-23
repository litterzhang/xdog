---
title: Reference
---

A reference to the current workflow JSON schema, the type system, condition operators, the
runtime container a run returns, the `xdog-flow` CLI, and every check that runs
at load time. A workflow is one JSON object executed directly by the runtime or
compiled to Python — both agree field-for-field.

## JSON schema

### Workflow (top level)

A workflow is one JSON object with a valid entry frontier. An empty graph is
not a runnable workflow; declare nodes and their explicit edges. `entry` is
optional — when omitted, the run starts from every node that depends only on `$in` (multi-entry, run in
parallel).

| Field | Required | Meaning |
|---|---|---|
| `name` | default `""` | Human-readable workflow name; shown in diagrams and the runtime container. |
| `provider` | default `""` | LLM provider id used to build the default agent stream (e.g. copilot). |
| `entry` | optional | Id of the first node to run; when unset, derived from the `$in` frontier (multi-entry). |
| `defaults.model` | default `""` | Fallback model for any agent node that does not set its own model. |
| `state` | default `{}` | Object of initial values, exposed as the output ports of the `$in` source. |
| `nodes` | default `[]` | The list of node objects (see below). |
| `edges` | default `[]` | The list of edge objects wiring node ports together. |
| `tools` | default `{}` | Custom-tool manifest: `{tool_name: "module.path:callable"}`. |
| `in_schema` | inferred | Explicit JSON Schema properties for `$in`; otherwise inferred from typed consumers. |
| `max_concurrency` | default `0` | Maximum static frontier nodes running concurrently; `0` is unlimited. |
| `fan_max_concurrency` | default `0` | Per-fan-group dynamic instance cap; independent from the outer frontier cap. |
| `schedule` | optional | Timer or hook declaration consumed by `xdog-flow scheduling install`; ignored by the one-shot engine. |

### Node

A node is an agent (SDK or external CLI), script (Python), human signal gate,
or opaque subflow. `type` defaults to agent. An agent node must not set `code` or
`run`; a script node must set exactly one of them.

| Field | Required | Meaning |
|---|---|---|
| `id` | required | Unique node id. `$in` and `$output` are reserved and rejected. |
| `type` | default `"agent"` | `"agent"`, `"script"`, `"human"`, or `"subflow"`. |
| `model` | optional | Agent only: overrides `defaults.model` for this node. |
| `system_prompt` | default `""` | Agent only: system prompt; `{{ $.port }}` reads this node's inputs. |
| `prompt` | default `""` | Agent only: user prompt; `{{ $.port }}` is a JSONPath into this node's inputs. |
| `tools` | default `[]` | SDK agent only: names of built-in or manifest tools to expose. |
| `skills` | default `[]` | Explicitly activate named skill bodies for this agent node. |
| `inherit` | optional | SDK agent only: `{"from": "node_id"}` selects another agent's saved context, or itself across a loop; tools/skills remain node-selected. |
| `signal` | default `""` | Human only: named signal required to proceed. |
| `web_search` | default `false` | SDK agent only: enable the built-in `web_search` tool. |
| `web_search_model` | optional | SDK agent only: a distinct browsing model for `web_search`. |
| `code` | optional | Script only: inline source defining exactly one ctx-first function. |
| `run` | optional | Script only: a `"module.path:callable"` reference imported at run time. |
| `subflow` | optional | Subflow only: the child workflow — an inline object or a `"./child.json"` path string. Ports are derived from the child's signature; do not declare `inputs`/`outputs`. |
| `retry` | optional | `{max, backoff}` retry policy; `max` counts retries after the first attempt. |
| `on_error` | default `"fail"` | `"fail"` aborts the run; `"isolate"` records the branch failure and skips its descendants. |
| `deterministic` | default `false` | Reuse recorded output by node id and input hash; not an exactly-once effect guarantee. |
| `backend` | optional | Agent only: `"claude-cli"` or `"codex-cli"`; absent uses the in-process SDK. |
| `allowed_tools` | default `[]` | CLI agent only: forwarded to Claude when nonempty; Codex has no per-tool allowlist in the current adapter. Not a sandbox guarantee. |
| `mcp_servers` | default `{}` | CLI agent only: opaque MCP server specs with `${ENV_VAR}` interpolation. |
| `inputs` | default `[]` | Input ports: a bare required string name or `{name, schema, required}`. |
| `outputs` | default `[]` | Output ports in the same canonical forms. For an agent, no outputs or one plain string schema uses text; other contracts are structured (SDK `submit_result` or CLI native schema output). |

### Port

A port is a bare string (a required `string` port) or an object. A structured
output port lets an agent fan its `submit_result` result across several ports and
lets an edge map a sub-field with a type check.

| Field | Required | Meaning |
|---|---|---|
| `name` | required | Port name; referenced by edge maps and `{{ $.name }}` interpolation. |
| `schema` | required in object form | A JSON Schema fragment: scalar or nested `{type, properties, items, required}`. A bare string port implies `{"type":"string"}`. |
| `required` | default `true` | Input only: `false` exempts it from the must-be-fed rule for loop-carried or conditionally supplied values. |

### Edge

An edge moves data from a source node's output ports to a destination node's
input ports. An empty map is a pure control edge (ordering only). `$in` is
source-only; `$output` is sink-only.

| Field | Required | Meaning |
|---|---|---|
| `from` | required | Source node id, or the reserved `$in` source. |
| `to` | required | Destination node id, or the reserved `$output` sink. |
| `map` | default `{}` | `{source_output_port: destination_input_port}` pairs. A source key may be a JSONPath into a structured port, e.g. `"$.verdict.within_budget"`. |
| `when` | optional | A condition; the edge only fires (or feeds) when it holds. |
| `loop.max` | optional | Marks a bounded back-edge; exhausted plain loops stop normally. |
| `while` | optional | Strict bounded-loop sugar carrying `cond` and optional `max`; a still-true exhausted condition raises non-convergence. |
| `fan_out` | optional | Names the source array port whose elements run one worker instance each. |
| `fan_in` | optional | `"list"` preserves one value per instance; `"concat"` flattens array-valued instance outputs one level. |

## Type system

Every port carries a JSON Schema (a bare name implies a required string port). The
wire format is **type-native**: a port value is the live Python value — a script
node reads its inputs as that value and returns the value directly, with no
stringify/parse round-trip. An edge is type-checked at load time: the source and
destination port types must match (a JSONPath sub-field is checked against the
source schema when it can be resolved). `$in` seeds without explicit schemas are untyped, so their outgoing edges
are exempt from source-type checks. Explicit `in_schema` enables seed type checks;
fan-out from `$in` requires an explicit array schema. Consumer inference supplies
the derived subflow input signature, not universal static typing of every seed.

| Type | Python value | Empty → |
|---|---|---|
| `string` | `str` | `""` |
| `integer` | `int` | `0` |
| `number` | `float` | `0.0` |
| `boolean` | `bool` (true/1/yes/on → true) | `false` |
| `array` | `list` | `[]` |
| `object` | `dict` | `{}` |

## Interpolation & conditions (JSONPath)

A prompt or a condition operand may embed `{{ <jsonpath> }}` placeholders,
resolved against the node's inputs (or, for an edge `when`, the source node's
output ports): `{{ $.topic }}` for a whole port, `{{ $.plan.tasks[0] }}` for a
nested field. An unresolved path yields the empty string. The same jsonpath-ng
evaluator runs in both the interpreter and the generated module.

## Condition operators

An edge's `when` is a condition tree evaluated against the source node's output
ports. `value` and `text` support `{{ $.port }}` JSONPath interpolation.

| Op | JSON | Holds when |
|---|---|---|
| `equals` | `{"equals": {"value": V, "text": T}}` | interpolate(value) == interpolate(text) |
| `contains` | `{"contains": {"value": V, "text": T}}` | interpolate(text) in interpolate(value) |
| `gt` / `gte` / `lt` / `lte` | `{"gte": {"value": V, "text": T}}` | numeric compare: float(value) ≥ float(text) (an empty operand is lenient-false; a non-numeric one errors) |
| `not` | `{"not": <cond>}` | negation of one child condition |
| `and` | `{"and": [<cond>, ...]}` | all children hold |
| `or` | `{"or": [<cond>, ...]}` | any child holds |

Every `{{ $.key }}` operand root is checked at load time against the source node's
output ports (strict interpolation), so a typo fails validation.

## Runtime container and process result

The Python `execute()` API returns an `ExecResult` with an internal `runtime` container. CLI and generated
Python process boundaries print a stable envelope:

```json
{
  "success": true,
  "message": "Workflow completed",
  "output": {},
  "context": {
    "workflow": "demo",
    "runId": null,
    "startTime": "...",
    "endTime": "...",
    "durationMs": 42,
    "tokensUsed": 0,
    "lastNode": "finish"
  }
}
```

Failures use the same shape with `success:false`, an error `message`, empty
`output`, and a non-zero process exit code. The `ExecResult.runtime` container is:

| Key | Contents |
|---|---|
| `ctx` | Last-node runtime metadata: `{step, node_id, workflow_name}`. Scripts receive a `RuntimeContext` with those fields plus workspace/grant metadata, not workflow input/state data. |
| `stack` | A per-node delta trace: one `{step, node, in, out}` frame per run; a looped node appears once per pass. |
| `state` | Real-node outputs only: `{node_id: {port: value}}` — excludes `$in` and `$output`. |
| `in` | The `$in` seed: the workflow's state, with any run-time input overrides applied. |
| `out` | The `$output` map: the key/value pairs collected from edges targeting `$output`. |
| `failed` | Isolated failures: `{node_id: error}` for branches captured by `on_error:isolate`. |
| `memo` | The determinism ledger: `memo_key(node, input hash)` → output ports, for deterministic reuse. |
| `tokens_used` | Cumulative agent tokens spent this run; enforced against `execute(max_tokens=…)`. |

## CLI — `xdog-flow`

Commands that take a workflow accept `.json` or `.svg` with embedded JSON.
Scheduling lifecycle commands use installed names, not workflow files; `test`
also accepts a companion suite or directory. Use `COMMAND --help` for flags.

- **`xdog-flow validate <config>`** — Load and validate a workflow; prints OK or the first error. `--json` reports a machine-readable error envelope. Loading may import custom code; it is not guaranteed side-effect-free.
- **`xdog-flow run <config>`** — Execute a workflow and print the structured `success/message/output/context` result envelope.
  - `--dry-run` — Replace SDK streaming with `DRYRUN:<model>`; does not stub scripts/CLI backends and may not satisfy structured-result contracts.
  - `--input K=V` — Seed or override a `$in` value (repeatable; split on the first `=`). Values parse as JSON when possible, otherwise remain strings.
  - `--provider X` — Override the AI provider.
  - `--timeout N` — Per-node wall-clock timeout in seconds (default 120).
  - `--workspace DIR`, `--confined`, `--allow-path DIR` — Work directory and cooperative confinement/grants, not an OS sandbox.
  - `-v / --verbose` — Show flow's DEBUG logs — node execution and loop firing.
- **`xdog-flow generate <config>`** — Compile the workflow to Python with inlined helpers; runtime imports still apply.
  - `-o / --output FILE` — Write to a file instead of stdout.
  - `--portable -o DIR` — Package selected sources: SDK nodes/tool manifests select `xdog.ai`/`xdog.agent`, and subflow parents additionally vendor `xdog.flow`. Inspect child/custom dependencies; this is not a hermetic closure. `--offline` downloads selected wheels at build time.
- **`xdog-flow graph <config>`** — Render the workflow graph.
  - `--mermaid` — Emit a Mermaid flowchart.
  - `--svg` — Emit an SVG document with the workflow JSON embedded.
- **`xdog-flow build <config>`** — Open the interactive TUI builder (created if the file is missing).
- **`xdog-flow scheduling install <config>`** — Build a portable bundle and install a timer/hook scheduler; supports `--name` and `--dry-run`.
- **`xdog-flow scheduling list`** — List local registry entries, not verified live next-fire times.
- **`xdog-flow scheduling stop <name>`** / **`start <name>`** — Disarm/rearm an installed schedule; stop also ends a timer service run. Both support `--dry-run`.
- **`xdog-flow scheduling uninstall <name>`** — Remove units, bundle, and registry entry; supports `--dry-run`.
- **`xdog-flow test <target>`** — Run a workflow's companion `*.test.json` suite. Accepts a workflow (finds the sibling suite), a suite file, or a directory to sweep. Exits 1 on failure.
  - `--case NAME` — Run a single case.
  - `--allow-script-stub` — Permit `scripts` stubs (script nodes run for real by default).
  - `-v / --verbose` — Show the node trace for passing cases too.

**Path-subflow caveat:** current `validate --json` parses without the file base
directory, so a valid `"subflow": "./child.json"` can report `invalid-subflow`.
Use ordinary `validate FILE`; inline children do not need that workaround. SVG
loading also omits the base directory during child parsing: prefer the JSON
source or inline children. These are parser-path limitations, not child failures.

### Installed Flow tool

The installed `flow` capability contributes a skill and tool at **application
scope**. Discovery/registration does not activate the skill body, grant permission
or start a workflow/process. A node's `skills` list explicitly activates its body.
Flow has **no daemon**.

Tool parameters are CLI argv, excluding the executable, plus optional absolute
process `cwd`:

```json
{"arguments": ["validate", "/project/workflow.json", "--json"], "cwd": "/project"}
```

The fixed same-environment invocation is Python `-m xdog.flow`, without a shell.
Non-interactive `validate`, `run`, `generate`, `graph`, `test`, `scheduling` and
help are supported; interactive `build` is manual-only. `cwd` is not a workflow
`--workspace` or a permission grant. Results are `{exit_code, stdout, stderr}`;
CLI JSON stays a string inside `stdout`. Inspect the actual code/diagnostics.
Cancellation/close settles owned CLI processes, not rollback or uninstall of an
accepted schedule. Output is bounded; interrupted effects are not automatically
retried. Normal host permissions apply, including for imports during validation.

### Structured SDK result and tool registry

The SDK runner and generated SDK code inject `submit_result` through Agent's
existing tool loop, with generic `result_schema` and caller-owned `result_sink`.
Successful validation writes `result_sink["result"]`; failure leaves it unchanged
for correction. A single structured port receives the submitted array/object/scalar
directly; multiple ports require an object keyed by port name. Old
`flow_output_schema` / `flow_result_sink` context keys are rejected. Authors use
canonical typed `outputs`, not a separate JSON `output_schema` field.

The executor owns a fresh registry with demo `echo`, SDK defaults `bash`,
`filesystem`, `current_time`, and opt-in `submit_result`. Nodes select ordinary
tools explicitly; structured submission is injected when needed. Trusted custom
tool manifests can add `module:attribute` objects/factories. Imports do not mutate
a global runtime inventory. CLI nodes use external tool/MCP configuration instead.

### Workflow tests — `xdog-flow test`

A workflow is a program, so it needs tests. Flow uses a companion suite rather than
embedding tests in the production artifact:

```text
release_readiness.json
release_readiness.test.json
```

```bash
uv run xdog-flow test packages/flow/examples/release_readiness.json --allow-script-stub
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
uv run xdog-flow test packages/flow/examples/release_readiness.json \
  --allow-script-stub --case "critical finding blocks the release"
```

**Only the boundaries a test cannot reason about are stubbed.** Agent turns (SDK and
CLI backends alike), human signals, and whole subflow nodes; script nodes are opt-in
behind `--allow-script-stub`, since stubbing deterministic logic hides the thing
under test. Edges, conditions, loops, fan-out, fan-in aggregation, mappings, type
coercion, retry and `$output` collection all run for real — a case cannot degrade
into "I mocked the chain and asserted my own mocks".

**Stubs are injected at the provider call, not at the node output.** Prompt
interpolation has already run, and the stubbed value still passes through the node's
required-field check and `to_state` coercion. So a stub is validated by the same code
that validates a real model response, and a broken `{{$.path}}` is still broken.
Because the stub runner answers *every* agent node, no provider or CLI is ever
constructed; missing agent stubs fail instead of reaching a live backend.
Unstubbed scripts/imports still have real filesystem/process/network effects.
The following is an excerpt, not a complete Release Radar suite; use the checked-in
companion file for the collector stub, all required finding fields and assertions.

```json
{
  "cases": [
    {
      "name": "critical finding blocks the release",
      "inputs": {"repo": "/fixture/repo", "base_ref": "main"},
      "agents": {
        "plan_checks": {"checks": [{"name": "security", "focus": "auth"}], "rationale": "..."},
        "audit": [
          {"when": {"check": {"name": "security"}}, "then": {"finding": {"severity": "critical"}}},
          {"then": {"finding": {"severity": "low"}}}
        ]
      },
      "subflows": {"report": {"final_report": "# Blocked", "quality_score": 9, "report_feedback": "ok"}},
      "expect": {
        "output": {"risk": {"status": "blocked", "release_allowed": false}},
        "calls": {"audit": 3, "report": 1}
      }
    }
  ]
}
```

A stub is authored as the node's **output ports** — either a constant, or an ordered
rule list where the first match wins. Three selectors, each stable under concurrency:

| Selector | Means | Why it is deterministic |
| --- | --- | --- |
| `when` | deep-subset match on the activation's inputs | a value match, so fan-out completion order is irrelevant |
| `index` | the instance's position in the fanned array | the source array, not arrival order |
| `round` | which activation of the node this is, 1-based | fan instances share a round; loops are sequential |

Use `when` for fan-out and `round` for loops — that is how a case pins down loop
termination: score below the gate on round 1, above it on round 2.

`expect` takes exactly one outcome — `success` (the default), `error: "<substring>"`,
or `paused: "<human node id>"` — plus two partial assertions. `output` is a deep
subset of `$output`; `calls` maps node id to invocation count and collapses four
questions into one number:

| Question | Written as |
| --- | --- |
| did this node run? | `{"report": 1}` |
| was this branch skipped? | `{"revise": 0}` |
| how many fan instances? | `{"audit": 3}` |
| how many loop iterations? | `{"critique": 2}` |

Objects match as subsets, arrays must be the same length, and `bool` only matches
`bool`. On failure only the deepest differing path is printed, with a trace marking
each node `stub` or `ran`. Two further failures are about the suite rather than the
workflow: no rule matched a call (the activation's inputs are printed), and a
selector that never fired (a stale `round: 3` against a two-iteration loop).

Authoring mistakes are caught at load time — a stub aimed at a missing node, at the
wrong node type, or setting an undeclared output port never reaches execution.

Nodes *inside* a subflow are deliberately not stubbable: a child workflow carries its
own `.test.json`, so the parent asserts the composition and the child asserts its own
internals. `xdog-flow test` runs the interpreter only — `interpret == compile` is
flow's own invariant, guarded by flow's own suite, and binding your cases to codegen
output would couple them to something they should not know about.

### CLI agent nodes

An agent node may set `"backend": "claude-cli"` or `"codex-cli"` to run its turn by
shelling out to a coding-agent CLI instead of the in-process SDK:

- **No provider** — the CLI owns auth; a workflow whose agent nodes are all
  CLI-backed omits `provider` (it is required only for an SDK agent node).
- **`allowed_tools`** — forwarded to Claude's `--allowedTools` when nonempty.
  Codex has no corresponding per-tool allowlist in this adapter. An empty list
  does not prove deny-all tooling or an enforced sandbox.
- **`mcp_servers`** — an opaque per-server spec flow converts into the CLI's MCP
  config, with `${ENV_VAR}` secret interpolation (the JSON carries the reference,
  never the token).
- The CLI binary is found on `PATH`; `FLOW_CLI_BIN` (or `FLOW_CLI_BIN_CLAUDE_CLI`)
  overrides it. Both engines shell the same command, so `interpret == compile` holds.

### Scheduling

A top-level `schedule` block declares deployment configuration, activated only by
`xdog-flow scheduling install`; the one-shot engine ignores it:

- **`{"mode": "timer", "every": "15m"}`** or `{"mode": "timer", "cron": "0 9 * * 1-5"}`
  — a systemd user timer runs the bundle on schedule; there is no automatic
  CLI crontab/nohup fallback.
- **`{"mode": "hook", "signal": "s", "listen": {"type": "http", "path": "/hooks/x", "port": 8787}}`**
  — an external event delivers `signal` to a fresh run (routed by one shared,
  systemd-supervised listener). Pair it with a `human` node on the same signal.

Every firing is an independent `python <bundle>` run — the engine is unchanged.
Timer installation provisions a uv environment by default; `--no-venv --python`
selects an existing interpreter. `--no-start` skips immediate **timer** arming but
still enables later login/boot startup; it does not disable hook setup.

Hook deployment currently needs operator review: the rendered listener unit names
`flow.scheduler.listener` instead of `xdog.flow.scheduler.listener`, and its
interpreter/spawn path uses `/usr/bin/python3`, not the timer bundle's uv interpreter.
The live listener serves HTTP on loopback (default 8787), returning 202 after
spawning, not a durable execution receipt or workflow result. File router helpers
and accepted file/socket metadata do not start file watchers or socket activation.
No built-in HTTP authentication option is enforced. Schema acceptance is not
verified transport deployment; keep the listener private.

`--dry-run` previews units/actions without installation or provisioning, but still
loads trusted workflow code. Stop/uninstall cannot undo prior effects. Do not
store secrets in `schedule.inputs`: they are serialized into configuration;
CLI MCP `${ENV_VAR}` handling is a separate runtime feature.

### Generated-module run-time overrides

A generated module reads a few environment variables at run time, so a compiled
workflow (or a `--portable` bundle run with `python .`) stays overridable without
regenerating. Some mirror `run` flags; others are generated/API-only controls:

- `FLOW_INPUTS='{"days": 2}'` — a JSON object merged per-key into `$in` (mirrors `--input`).
- `FLOW_PROVIDER=copilot` — override the provider (mirrors `--provider`).
- `FLOW_MAX_TOKENS=100000` — abort once cumulative agent tokens pass the ceiling.
- `FLOW_RUN_ID` + `FLOW_CHECKPOINT_DIR` — enable checkpoint/resume.
- `FLOW_SIGNALS=go,ready` — deliver human-node signals (how a hook run passes its gate).
- `FLOW_CLI_BIN` (or `FLOW_CLI_BIN_CLAUDE_CLI`) — override the CLI binary a CLI agent node shells out to.

Generated modules do not implement `run --dry-run`; passing `--dry-run` to
`python workflow.py` does not make it model-free. SDK imports, subflow imports
of `xdog.flow`, third-party libraries and custom dependencies must be provisioned.

## Validated before it runs

Loading a workflow runs every check below; any failure raises before a single
node executes. This is what lets diagrams and codegen trust the graph.

| Rule | Triggered by |
|---|---|
| Node id must be non-empty | A node object has an empty id. |
| Node id `$in` / `$output` is reserved | A real node tries to claim a reserved id. |
| Duplicate node ids | Two nodes share the same id. |
| Entry node not found in nodes | `entry` names a node that does not exist. |
| Agent node must not set `run` / `code` | An agent node carries script-only fields. |
| Script must set exactly one of `code` or `run` | A script node sets both or neither. |
| Script `run` must match `module.path:callable` | A malformed run reference. |
| Script inline code invalid / must define one function | Inline code fails to parse or has ≠1 function. |
| Script function's first parameter must be `ctx` | The inline function signature does not start with ctx. |
| Script params != declared inputs | The function parameters and the declared input ports disagree. |
| References unknown tool | A node names a tool that is neither a built-in nor in the manifest. |
| Tool manifest ref must match `module.path:callable` | A malformed custom-tool reference. |
| Edge src `$output` / dst `$in` not allowed | Using the sink as a source, or the source as a sink. |
| Edge src / dst not found in nodes | An edge names an endpoint that does not exist. |
| Source/destination has no such port | An edge map names a port a node doesn't declare. |
| Cycles must be bounded | A detected cycle lacks a valid `loop.max` or strict `while` bound/condition. Node declaration order alone does not define a back-edge. |
| Input port is fed by N unconditional edges | Two always-on producers target one input (ambiguous). |
| Input port is not fed by any edge mapping | A `required` input port has no feeder. |

Current topology restrictions also reject nested fan-out, fan workers inside
loops, nested subflows, subflows inside loops and subflows as fan workers. A fan
group is one checkpoint unit: incomplete groups can rerun all instances on
explicit resume. A child has a namespaced checkpoint where configured, not
parent-owned partial child scheduling. None of this promises exactly-once effects.
`context.lastNode` is descriptive under concurrency; `context.stoppedBy`, when
present, records the authoritative bounded-stop reason.
