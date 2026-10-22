# Flow — Typed Workflows for Humans and Coding Agents

> Platform scope: **Linux only**. Non-Linux adapters and native-platform support
> are outside the current product scope; historical audit evidence is not a support promise.

**Flow is a local-first, typed workflow format and compiler for developers and
Coding Agents. Design repeatable agent workflows visually or generate them with
AI, validate the JSON artifact, then run, compile, or schedule it as standalone
Python.**

Flow exists for two primary workflows:

1. **Developer authoring.** Developers compose fixed, repeatable workflows through
   the TUI today and a Web UI in the future, then run them on demand or install a
   timer/hook schedule.
2. **Agent authoring.** Claude Code, Codex, or another Agent turns a successful,
   repeatable process into a constrained `workflow.json`; `xdog-flow validate`
   supplies precise feedback so the Agent can repair it before human review.

Both paths produce the same Git-friendly artifact:

```text
Human TUI / Web UI ─┐
                     ├─> workflow.json -> validate -> run / generate / scheduling
Coding Agent + Skill ┘
```

The JSON workflow is the product's canonical intermediate representation. The
TUI/Web UI and Coding Agents are editors; the frontier executor is its interpreter;
codegen is its standalone Python backend; scheduling is an optional deployment
adapter. Flow is deliberately not a hosted low-code platform or a general-purpose
distributed workflow service.

## Product principles

- **Human/Agent symmetry:** people and Agents edit the same format.
- **Git-native:** workflows are readable files, not opaque database records.
- **Validate before execute:** ports, schemas, conditions, loops, and subflows fail
  early with actionable errors.
- **Interpret equals compile:** local execution and generated Python share one
  frontier transition kernel.
- **Local-first deployment:** no control plane is required; a workflow can become
  one Python artifact and an optional systemd schedule.
- **Fixed workflows, not open-ended autonomy:** Flow crystallizes processes that
  have become stable enough to repeat, inspect, and maintain.

---

## Installation and agent tool

Requires Python 3.12+. Install into the **same Python environment as the host**:

```bash
python -m pip install xdog-flow
```

Installing `xdog-flow` registers its provider through `xdog.capabilities` with
**application** scope. Coding and Claw discover
it through the generic installed-capability mechanism, without a Flow-specific
adapter. The package declares `skill_names=("flow",)` and supplies the packaged skill
and a single `flow` tool. A skill declaration advertises availability, not activation.
The packaged skill is discoverable through SkillManager; its full body is loaded
only when explicitly activated, not by capability registration. Registration,
discovery and skill loading do not start processes or execute workflows. Flow has **no daemon**.

### `flow` tool contract

Prefer the `flow` tool for agent-driven CLI operations. Its parameters are:

| Parameter | Meaning |
|-----------|---------|
| `arguments: list[str]` | `xdog-flow` command and argument tokens, **excluding** the executable name; not a shell command string. |
| `cwd` (optional) | Absolute **process working directory**; distinct from the workflow's `--workspace`. |

Example tool parameters:

```json
{"arguments": ["validate", "workflow.json", "--json"], "cwd": "/absolute/project"}
```

An authorized run uses the same tool:

```json
{"arguments": ["run", "workflow.json", "--input", "topic=typed workflows"], "cwd": "/absolute/project"}
```

Each invocation starts an independent CLI process with the host's same-environment
Python interpreter and the fixed module invocation `-m xdog.flow`. It does not
use a shell or allow selection of another executable/module. Shell expansion,
pipes and redirection are not interpreted.

The result is JSON `{exit_code, stdout, stderr}`: an integer exit code and two
string streams. A CLI's `--json` output remains a string inside `stdout`, not a
replacement for this envelope. Nonzero CLI exits are returned with their actual
code and diagnostics, not presented as success. Output is bounded; exceeding the
limit terminates the invocation and explicitly reports the overflow. The tool
does **not automatically retry**. Before any authorized new invocation, inspect
the result and possible effects already performed.

Cancellation or capability shutdown terminates and reaps each invocation's owned
process group, rather than leaving CLI children running. This is process cleanup,
not rollback of effects or removal of an explicitly installed schedule. Scheduling
uses an external scheduler such as systemd; it does not introduce a Flow daemon.

Supported tool commands are non-interactive `validate`, `run`, `generate`,
`graph`, `test`, `scheduling` and `--help`. **`build` is an interactive TUI for
manual user CLI use only**, not a tool operation. The CLI examples below retain
shell notation for manual use; agents translate them to `arguments` tokens and
consume generated output from `stdout` rather than passing shell redirection.
Direct CLI execution is a fallback only when the tool is unavailable and that
execution is permitted, not a way around a denial or failed tool call.

**Permissions and trust.** Normal host tool permissions and approvals still
apply; installing the capability or loading the skill grants no execution
permission. Workflows can execute scripts, call models and import custom code.
Even `validate` may import referenced modules, whose import-time code can have
side effects; it is neither guaranteed side-effect-free nor automatically
allowed. Review the actual operation and workflow trust, including scheduling
changes. The tool and workflow confinement guards are not an OS sandbox.

---

## JSON Schema

Data flows between nodes through **named ports** wired by **explicit edge
mappings** — `nodeA.output.x -> nodeB.input.a` — not a shared global state. Each
node declares `inputs` and `outputs` port lists; an edge's `map` says which
source output port feeds which destination input port. The workflow's `state`
block seeds the output ports of a reserved source node `$in`.

```jsonc
{
  "name": "my_workflow",          // workflow identifier
  "provider": "copilot",          // ai provider id (passed to ai.provider())
  "defaults": {
    "model": "gpt-5.6-sol"  // fallback model for nodes without model
  },
  "entry": "research",            // id of the first node to execute
  "state": {                      // seed values, exposed as output ports of "$in"
    "topic": "..."
  },
  "nodes": [
    {
      "id": "research",           // unique node identifier
      "type": "agent",            // "agent" (LLM) or "script" (Python fn)
      "model": "...",             // optional; overrides defaults.model
      "system_prompt": "...",     // system prompt for the agent
      "inputs": ["topic"],        // bare string port, or {name,schema,required}
      "prompt": "Research {{topic}}",  // {{x}} reads THIS node's input port x
      "outputs": ["research_notes"]    // output ports use the same canonical forms
    }
  ],
  "edges": [
    // data edge: source output port -> destination input port
    {"from": "$in", "to": "research", "map": {"topic": "topic"}},
    {"from": "research", "to": "write", "map": {"research_notes": "research_notes"}},

    // conditional back-edge (loop). "when" reads the SOURCE node's output ports.
    {
      "from": "review",
      "to": "write",
      "when": {"contains": {"value": "{{review_result}}", "text": "REVISE"}},
      "loop": {"max": 2}          // required for back-edges; limits iterations
    }
  ]
}
```

**Skills.** An agent node may name skills that ship inside installed packages:

```jsonc
{"id": "author", "type": "agent", "skills": ["flow"], "prompt": "..."}
```

Their instructions are prepended to the node's system prompt. A skill resolves
from two places, in this order:

1. `skills/<name>/SKILL.md` **beside the workflow file** — part of the artifact,
   like the sibling modules a `run:` node imports. It travels into a bundle.
2. any installed package that ships one — versioned with the code that installs it.

Never the machine's own skill directories. A workflow is a shareable artifact,
and one that picked up whatever happened to be on disk would behave differently
for two people with no way to tell from the file. An unresolvable name fails
`validate`, because an agent asked to produce a format it was never shown does
not fail — it produces something plausible and wrong.

**Declared, not discovered — and so loaded eagerly.** The body goes into the
system prompt when the node is built, rather than behind a "load this skill"
tool the model may call. Two-step disclosure exists to solve *which of the
hundred skills on this machine do I need*, and a workflow that names its skills
has already answered that. Making the guarantee depend on the model choosing to
read its own instructions reintroduces exactly the failure the field exists to
prevent: a node gets one shot at producing a format, and one that skips the load
produces something plausible and wrong.

The cost is real — the `flow` skill body is resent on every API call
that node makes — so declare a skill on the node that produces the artifact, not
on every node in the workflow. The saving is in not declaring it where it is not
needed, which is exact, rather than in lazy loading, which is a guess.

**Ports.** A required string port may use the bare shorthand (`"topic"`). Typed,
structured, or non-required ports use the canonical object form
`{"name": "sum", "schema": {"type": "integer"}, "required": true}`. Values are
stored as native JSON/Python types and scripts receive values described by the schema.

**Validation** rejects: an input port fed by no edge mapping; a `map` referencing
a port that doesn't exist on the source/destination; and — critically — **two
unconditional edges feeding the same input port** (the ambiguous-producer clash
that a shared global state used to allow silently).

### Execution model

Both `xdog-flow run` and generated modules execute the same frontier state
machine:

1. Seed the frontier from `entry` (or all derived entry nodes).
2. Run the ready frontier concurrently in node declaration order.
3. When a node completes, evaluate its outgoing edges against that node's output.
4. A destination with ordinary predecessors waits for every predecessor to
   complete; only condition-enabled edges contribute mapped input values.
5. When no activation is ready or running, graph execution is complete.

A bounded back-edge is written `loop` or `while`; they differ only at the bound.
`loop` stops (`success: true`) and reports `context.stoppedBy` naming the edge
that ran out; `while` raises non-convergence (`success: false`, exit 1). Use
`while` when failing to converge is a failure, `loop` when the bound is a budget.
`context.lastNode` is descriptive (last node to complete, unstable under
concurrency); `stoppedBy` is the authoritative reason a run ended.

Bounded back-edges with the same destination form a conditional **AND loop
join**. All member source nodes must complete in the current generation and all
member conditions must hold before the destination runs again, exactly once.
Each edge keeps its own `max` and strict-`while` behavior, so different bounds and
mixed plain/strict members are supported.

The generated module embeds this frontier scheduler and static graph metadata;
it does not translate loops into a separate Python `for` control-flow model.

### Condition operators

| Operator | Shape | Meaning |
|----------|-------|---------|
| `contains` | `{"contains": {"value": "<haystack>", "text": "<needle>"}}` | `text` is a substring of `value` |
| `equals` | `{"equals": {"text": "...", "value": "..."}}` | `text == value` |
| `gt` / `gte` | `{"gte": {"value": "{{ $.score }}", "text": "0.8"}}` | numeric comparison |
| `lt` / `lte` | `{"lt": {"value": "{{ $.score }}", "text": "0.8"}}` | numeric comparison |
| `not` | `{"not": <condition>}` | logical negation |
| `and` | `{"and": [<c1>, <c2>]}` | all conditions must hold |
| `or` | `{"or": [<c1>, <c2>]}` | any condition must hold |

On an edge, `{{key}}` in a condition reads the **source node's output port** `key`.

---

## CLI subcommands

### validate

Check a workflow definition for errors without running its node execution graph.
Loading and validation may still import referenced custom modules and execute
their import-time code; this is not a side-effect-free or automatically allowed
operation.

```bash
xdog-flow validate examples/refine_loop.json
# OK: refine_loop
```

`--json` reports the **whole** per-node and per-edge pass in one envelope, each
error carrying a stable `code`, the node or edge it belongs to, and a `hint`
where the repair is not obvious from the message. The prose form stops at the
first failure, which costs an authoring Agent one round trip per mistake:

```bash
xdog-flow validate broken.json --json
```
```json
{
  "ok": false,
  "path": "broken.json",
  "workflow": "refine-loop",
  "errors": [
    {"message": "Node 'critic' references unknown tool 'no_such_tool'. …",
     "code": "unknown-reference", "node": "critic"},
    {"message": "Edge 'draft'->'critic': source has no output port 'x'",
     "code": "unknown-reference", "edge": {"from": "draft", "to": "critic"}},
    {"message": "Node 'draft': input port 'topic' is not fed by any edge mapping",
     "code": "graph-incomplete", "node": "draft",
     "hint": "Add an edge whose map targets it, or mark the port {\"required\": false}."}
  ]
}
```

Exit status is 1 when `ok` is false, either way. A read or parse failure is still
a single error — there is no graph yet to say more about.

#### Error codes

`code` is what a caller branches on. Messages are written for humans and get
reworded; matching on them makes every rewording a silent breaking change.

The set is small on purpose. Ninety-nine checks in the loader map to eighteen
codes, because what you *do* about a failure falls into far fewer buckets than
the number of ways to reach one — `unknown-reference` covers a missing node, a
missing port and an unknown tool alike, since the repair is the same shape in
all three.

| Code | Means | Typical repair |
|---|---|---|
| `unknown-reference` | A name that should resolve doesn't | Fix the name, or add what's missing |
| `duplicate-or-reserved-id` | Two things claim one name, or a name is reserved | Rename |
| `unknown-field` | A field the format doesn't define | Usually a typo |
| `missing-required` | A required field is absent | Add it |
| `wrong-shape` | Right field, wrong JSON type | Object vs list vs scalar |
| `invalid-value` | Right shape, value out of range or off-enum | Pick a legal value |
| `type-mismatch` | An edge joins ports whose types can't carry one value | Convert, or change a schema |
| `invalid-schema` | A port's type or JSON Schema is uninterpretable | Fix the schema |
| `graph-incomplete` | No entry, an unreachable node, an unfed input port | Add an edge, or mark it optional |
| `ambiguous-input` | One port fed by several edges that could both fire | Add a `when`, or drop one |
| `invalid-loop` | Unbounded cycle, bound without guard, crossing regions | Add `loop.max` / `when` |
| `invalid-fanout` | A fan-out/fan-in rule is broken | Restructure the topology |
| `node-kind-conflict` | A node carries a field of a different kind | Remove it, or change `type` |
| `invalid-script` | Script `code` won't parse, or its signature disagrees | Match params to declared inputs |
| `invalid-template` | A prompt or condition references a root that won't exist | Fix the `{{$.…}}` path |
| `invalid-subflow` | A child workflow won't resolve, or is cyclic | Fix the path or the cycle |
| `provider-required` | An SDK agent node with no provider | Set `provider`, or use a CLI `backend` |
| `invalid-schedule` | The `schedule` block isn't a valid timer or hook | Fix `every` / `cron` / `listen` |

These strings are API: new ones may appear, existing ones will not be renamed
or removed. `xdog.flow.error_codes.ALL_CODES` is the authoritative list.

### run

Execute a workflow and print a stable result envelope containing `success`, `message`,
collected `output`, and timing/token `context`.

```bash
# Live execution using the provider declared in the JSON
xdog-flow run examples/refine_loop.json

# Override the provider from the command line
xdog-flow run examples/refine_loop.json --provider anthropic

# Offline dry-run (no LLM calls; nodes echo "DRYRUN:<model>")
xdog-flow run examples/refine_loop.json --dry-run
```

### test

Run a workflow's companion `<name>.test.json` suite. Only the boundaries a test
cannot reason about are stubbed — agent turns, human signals, whole subflow nodes
(and script nodes behind `--allow-script-stub`). Edges, conditions, loops, fan-out,
coercion and `$output` collection all run for real, because those are what the test
is for.

```bash
xdog-flow test examples/release_readiness.json --allow-script-stub
xdog-flow test examples/                      # every *.test.json under a directory
```

Stubs are injected at the provider call, so prompts are still interpolated for real
and a stubbed value is validated by the node's own output contract — the same code
that validates a live model response. No provider is constructed for agent stubs,
but real script nodes and imported custom code may still have side effects,
including network access. Tests require the same trust and permission review as
other workflow operations. See `docs/testing.md`.

### generate

Compile the workflow to a self-contained Python module.

```bash
xdog-flow generate examples/refine_loop.json -o workflow.py
python workflow.py
```

Generated output structure:

```python
"""refine_loop — generated workflow module."""

import asyncio
# Standalone runtime helpers + node functions are inlined here.

_OUT = {"$in": {"topic": "..."}}
_FRONTIER_SPEC = {
    "nodes": ("research", "write", "review"),
    "entries": ("research",),
    "edges": {...},
    "loop_groups": {"write": (...)},
}

async def node_research(...): ...
async def node_write(...): ...
async def node_review(...): ...

async def main() -> None:
    await _run_generated_frontier()

if __name__ == "__main__":
    asyncio.run(main())
```

The static metadata and node functions are workflow-specific; the inlined
frontier transition kernel is the same implementation used by the interpreter.

### graph

Print an ASCII topology map, a Mermaid diagram, or an SVG.

```bash
xdog-flow graph examples/refine_loop.json
# research -> write -> review --(REVISE, max 2)--> write

xdog-flow graph examples/refine_loop.json --mermaid
xdog-flow graph examples/refine_loop.json --svg > diagram.svg
```

The `--svg` output uses **Graphviz** (via `pydot` + the system `dot` binary) for
automatic layout — ranked levels, routed edges, fan-out for parallel branches,
and nodes colour-coded by type (agent vs script). If `dot` is not installed,
`to_svg` transparently falls back to a dependency-free hand-drawn renderer, so
SVG output always works (just plainer). The SVG also embeds the workflow JSON,
so it stays re-openable in `xdog-flow build` (see below).

### build

Open an interactive terminal builder to create or edit a workflow visually.
This is for the user to launch manually from the CLI; the `flow` tool does not
support `build`.

```bash
xdog-flow build my_workflow.json      # opens the TUI (creates the file if missing)
xdog-flow build my_workflow.svg       # same, but persists as an editable SVG (see below)
```

**Layout.** The builder is a **two-panel** UI. The left panel stacks three
boxed blocks — **Graph**, **Nodes**, and **Edges** — and `Tab` cycles which one
is focused (the focused box is highlighted). The right panel follows the focus:

| Focused block | Right panel shows |
|---------------|-------------------|
| **Graph** | the live ASCII flow diagram (boxed nodes + arrows/loops) |
| **Nodes** | the selected node's details (id, type, model/prompt/tools, or script `code`/`run` + typed I/O) |
| **Edges** | the selected edge as `src → dst`, its guard/loop, and the **parameter flow** (which state key the source produces and the destination consumes) |

Keys inside the builder: `Tab` switch the focused block, `a` add an agent node,
`s` add a script node, `j`/`k` (or arrows) move the selection **within the
focused block** (nodes when Nodes/Graph is focused, edges when Edges is
focused), `d` delete the focused element (the selected edge in the Edges block,
otherwise the selected node), `p` edit the selected node's prompt (type, `enter`
to commit, `escape` to cancel), `e` connect an edge (choose the destination,
`enter`), `w` save (only when the workflow is valid), `q` quit. A footer shows
the current `[mode·focus]` and a validation status line, so wiring mistakes
(unreachable inputs, loop edges missing a bound) surface as you edit. Saved
files are immediately runnable with `xdog-flow run` / `validate` / `generate`.

**SVG as an editable document.** If the path ends in `.svg`, saving writes a
**rendered diagram that also embeds the full workflow JSON** (like draw.io) — the
file is both a picture you can open in any browser AND its own source, so
`xdog-flow build my_workflow.svg` reloads and keeps editing it. The embedded
JSON (in an SVG `<metadata id="flow-workflow">` element) is the source of truth;
the drawing is derived. `xdog-flow graph <file> --svg` prints the same document.

The builder is split into a headless, fully-unit-tested core
(`flow.builder.model` + `flow.builder.actions` — every edit re-validates) and a
thin TUI shell (`flow.builder.app`). The shell — plus `flow.graph.to_svg` and
`flow.builder.svg_doc` — was **generated by a flow workflow**
(`examples/builder_codegen.json` / `examples/svg_codegen.json`: design →
implement → autofix → verify(ruff + mypy --strict + contract test) → review,
looping on failure) — flow dogfooding its own codegen against real, type-checked
targets.

---

## Declared input ports

Agent and script nodes declare their input ports via `"inputs"`. Each input port
**must be fed by an incoming edge's `map`** — checked statically at validate time.
Prompt `{{x}}` and script arguments read the node's own input port `x` (they are
port-local, not a global lookup).

```jsonc
{
  "id": "enrich",
  "type": "agent",
  "inputs": ["record"],            // input port, fed by an edge map
  "prompt": "Enrich:\n\n{{record}}",
  "outputs": ["enriched"]
}
// wired by, e.g.:  {"from": "pull", "to": "enrich", "map": {"record": "record"}}
```

If an input port is fed by no edge mapping, `xdog-flow validate` raises a
`WorkflowValidationError` immediately.

---

## Structured output (`output_schema`)

When a node declares `"output_schema"`, the agent **must** call the built-in `submit_result`
tool before finishing.  The executor validates the call and stores the result as a JSON string
in the node's (single) output port.

```jsonc
{
  "id": "enrich",
  "type": "agent",
  "outputs": ["enriched"],
  "output_schema": {               // field name -> JSON type
    "category": "string",
    "token": "string",
    "summary": "string"
  }
}
```

Reading the result downstream:

```python
import json

enriched = json.loads(result.outputs["enrich"]["enriched"])
print(enriched["category"])   # "IoT / Wireless Infrastructure"
```

If the agent finishes without calling `submit_result`, the executor raises
`WorkflowExecutionError("did not submit a result")`.

---

## Script nodes

A **script node** runs a plain Python function (`def` or `async def`) instead of
an LLM agent. Its signature is **`f(ctx, <input ports by name>) -> output`**: the
first parameter is always `ctx` (a `RuntimeContext`), and each declared input port
arrives as a keyword argument, coerced to its declared type. The return value is
coerced back and stored in the node's output port (a node with multiple output
ports returns a dict keyed by port name). Ports are **typed** with JSON types
(`string`/`integer`/`number`/`boolean`/`array`/`object`).

Two code sources — a workflow is self-contained either way:

**Inline `code`** (fully decoupled — the JSON carries the function):

```jsonc
{
  "id": "add",
  "type": "script",
  "code": "def add(ctx, a, b):\n    return a + b",
  "inputs": [{"name": "a", "schema": {"type": "integer"}}, {"name": "b", "schema": {"type": "integer"}}],
  "outputs": [{"name": "sum", "schema": {"type": "integer"}}]
}
// wired by:  {"from": "$in", "to": "add", "map": {"a": "a", "b": "b"}}
```
The input ports `a`/`b` arrive as ints (from `"3"`/`"4"`), so `sum` is `"7"`
(not `"34"`). See `examples/pure_script.json`.

**Ref `run`** (imports a `.py` sitting next to the workflow file — JSON + sibling
`.py` = a portable bundle; the workflow's own directory is put on `sys.path` for
the import, not the global path):

```jsonc
{ "id": "prep", "type": "script", "run": "myscript:prep",
  "inputs": [{"name": "topic", "schema": {"type": "string"}}], "outputs": [{"name": "brief", "schema": {"type": "string"}}] }
```

`ctx` exposes `ctx.inputs` (this node's input ports as a mapping),
`ctx.workflow_name`, and `ctx.node_id`.

**Validation** at load time: a script node sets exactly one of `code`/`run`;
inline `code` must compile and its function must be `ctx`-first with parameter
names matching the declared input ports.

> **Security:** inline `code` is `exec`'d — it runs arbitrary Python. Only load
> workflows from a trusted author. (This is a local authoring tool, not a service.)

---

## Shared agent context (`inherit`)

An agent node normally starts cold: a fresh agent, one turn, discarded. `inherit`
starts it from another agent node's session instead — the messages and the system
prompt — so "research, then critique your own findings" does not have to
re-establish the context through a port.

```jsonc
{
  "id": "critique",
  "type": "agent",
  "inherit": { "from": "research" },
  "system_prompt": "You are now a harsh reviewer of your own work.",  // overrides
  "prompt": "Critique the findings you just produced."
}
```

The edge from `research` to `critique` can carry an empty `map`: it establishes
order and reachability, while the session carries the context. Tools are never
inherited — a node's capabilities stay readable from the node itself.

**A node may inherit from itself.** In a loop that is the point: the node keeps
its own context across iterations, so a reviser remembers what it already tried.
On the first pass there is no session yet, which is not an error — the same
lenient-on-first-pass rule as a non-required loop-carried port.

**Strict at load, lenient at run.** The reference is checked before anything
runs: it must name an existing agent node, declared earlier or itself, that
always executes. Two rejections are worth knowing because they otherwise fail
silently — a `deterministic` source returns memoised ports *without running*, so
it never produces a session at all; and a fan-out worker runs N times under one
node id, so "the" session is ambiguous. What is lenient is only a *missing*
session for a reference already known to be sound.

CLI backends (`claude-cli`, `codex-cli`) cannot take part in either direction:
the CLI owns its own session, and flow can neither read nor seed it.

## The workspace, and confining a run to it

**Every run has a workspace.** It defaults to `<workflow dir>/runtime`, and it is
where this run's files belong. That is on by default. `--confined` is the
separate question of how hard the edges are.

```bash
xdog-flow run wf.json                       # workspace, enforced for scripts
xdog-flow run wf.json --confined            # and unauditable calls refused
xdog-flow run wf.json --workspace ./scratch
xdog-flow run wf.json --allow-path ~/data   # grant another tree (repeatable)
```

### The two node kinds are held to it differently, on purpose

| | How | Enforced? |
|---|---|---|
| **agent** | workspace + granted dirs are named in its system prompt, with an instruction not to go outside them | **No.** A promise the model keeps. |
| **script** | a PEP 578 audit hook refuses reads and writes outside the bound | **Yes**, against code that is not trying to escape |

**An agent node is told, not checked** — every agent node, whatever tools it
declares. Keying the briefing off the tool list would be a guess: a custom tool
can touch the filesystem, so can an MCP server, and a model can name a path for a
downstream node to use. There is no chokepoint where every possible tool's file
access could be inspected, so the honest thing is to state where the files go and
say plainly that nothing verifies it. (When a run is `--confined`, the built-in
`filesystem` tool *does* enforce an allowlist — but that is one tool, not the
node.)

**A script node is audited.** `ctx.workspace` tells the script where it is —
being refused for writing outside a directory you were never told about is a
trap, not a rule, and relative paths are no help since nodes run concurrently and
the executor cannot `chdir` on one script's behalf:

```python
def report(ctx, rows):
    (ctx.workspace / "out.csv").write_text(...)   # allowed
    open("/etc/passwd").read()                    # PermissionError
```

Reads from the interpreter's own trees stay open — otherwise no script could
`import` anything.

The bound covers a script node's **whole `code`**, not just its function body.
Any other top-level statement runs too — at import time, in the compiled engine,
before `main()` is called — so it is held to the same bound. Otherwise a workflow
could put its filesystem access at module level and step around the hook
entirely.

One deliberate asymmetry: with `run: module:callable`, the **call** is bounded
and the **import is not**. Importing a module from disk is the same trust as any
other dependency, whose import-time code is equally unbounded. Inline `code` is
different because it is text carried inside the workflow — the part that may have
been generated.

### What `--confined` adds

Some calls leave the region a hook can see: `subprocess`, `os.system`, `ctypes`.
Unconfined they are allowed. Under `--confined` they are refused where they
happen, naming the call:

```
subprocess.Popen cannot be audited, and this run is confined.
Remove it, or run without --confined.
```

And a workflow whose *agent* nodes leave the process is refused before anything
runs — the `bash` tool, or a CLI backend. Those are not a list of bad things;
they are the boundary of what an in-process hook can reach.

### Containment, not a sandbox

The hook runs in the executor's own interpreter, where it is a reachable, mutable
object. A script that *wants* out can reassign the closure cell holding the roots
or overwrite the hook's `__code__`, in about two lines each — tested, not assumed;
immutable roots and hiding the name do not help. What it stops completely is the
realistic failure: a script that wanders, builds a path from bad input, or was
written without knowing where it was allowed to write.

If you need a bound that holds against a script written to defeat it, that needs
a child process and an OS bound — scoped in
[`docs/script-node-confinement.md`](../../docs/script-node-confinement.md).

### Subflows

A subflow inherits its parent's workspace, grants and confined flag, so a child
writes where its parent writes instead of nesting a `runtime/runtime/`. The child
applies these same rules to its own nodes; nothing inspects it more deeply.

### What it refuses, and why that is the point

`--confined` will not run a workflow it cannot actually confine:

| Refused | Reason |
|---|---|
| a `script` node with inline `code` | it runs unrestricted Python in the executor's own process, so no path check is ever consulted |
| the `bash` tool | a shell is a general-purpose escape |
| a CLI backend | the subprocess owns its own filesystem access |

A `script` node using `run: module:callable` is fine — it imports reviewed code
from disk rather than executing text carried inside the workflow. Subflows are
checked too, since an inline script buried in a child is exactly as unconfinable
and much easier to miss.

Refusing is what makes the flag mean something. Running anyway would confine
nothing while looking like it had.

### What this is and is not

It is **containment**: every filesystem access in a confinable workflow goes
through a tool that checks the path against the allowlist, resolving symlinks so
a link out of the workspace is caught by where it lands. That stops the
realistic failure — an agent that wanders into `~/.ssh`.

It is **not a sandbox**. Nothing here restricts the process at the OS level; the
guarantee comes entirely from the refusals above holding. Confining a run
properly would mean a subprocess plus Landlock or `bwrap`, which is a much larger
change — see [`docs/workspace-confinement.md`](../../docs/workspace-confinement.md).

## Per-node tools

Agent nodes can declare a `"tools"` list.  Each name is resolved from the
`ToolRegistry` at execution time:

```jsonc
{
  "id": "analyze",
  "type": "agent",
  "tools": ["echo"],                 // resolved via ToolRegistry
  "system_prompt": "You are an analyst.",
  "prompt": "Analyse: {{prepped}}",
  "outputs": ["analysis"]
}
```

### ToolRegistry

The executor ships a default registry pre-loaded with the `echo` built-in:

```python
from flow.tools import default_registry

registry = default_registry()
```

Register custom tools before calling `execute()`:

```python
from agent.core import AgentTool
from flow.executor import execute

my_tool = AgentTool(name="my_tool", ...)
registry = default_registry()
registry.register(my_tool)

result = await execute(wf, tool_registry=registry)
```

The generated module calls `_REGISTRY.resolve(("tool_name",))` at runtime,
so the same registry API applies to compiled workflows too.

---

## Current examples

The checked-in examples are executable. The flat ones are mirrored into
`skill/examples/` as templates for an authoring Agent to imitate.

- `agent_calculator.json` — typed agent output and arithmetic scripting.
- `refine_loop.json` — conditional review/revision loop with a non-required
  feedback input.
- `trip_planner.json` — structured planning and nested field mappings.
- `essay_writer.json` / `essay_compose.json` — opaque subflow composition.
- `cli_triage.json` — coding-agent CLI backend.
- `digest_timer.json` / `triage_hook.json` — timer and hook scheduling.
- `release_readiness.json` / `release_report.json` — SDK-agent release radar for
  this local repository, with filesystem/bash tools, dynamic fan-out, deterministic
  scoring, a report subflow, review loop, and weekly scheduling.
- `release_readiness.test.json` / `release_report.test.json` — their test suites:
  fan-out stubs selected by input value, a loop pinned to its `loop.max` bound, and
  a whole subflow stubbed out.
- `depins_enrich/` — a **case study**, not a template: the workflow that actually
  runs unattended every four hours against a live site, writing real commits. It is
  a directory rather than a single file because it is the only example whose script
  nodes are `run:` references to sibling modules, and the only one where an Agent's
  work is admitted by a deterministic gate and repaired in a bounded `while` loop.
  See `examples/depins_enrich/README.md`.

```bash
xdog-flow validate examples/refine_loop.json
xdog-flow run examples/refine_loop.json --dry-run
xdog-flow graph examples/refine_loop.json --mermaid
xdog-flow generate examples/refine_loop.json -o workflow.py
python workflow.py
xdog-flow test examples/ --allow-script-stub
```

---

## Licence

Copyright (c) 2026 HugeMan <942295.xyz>

flow is licensed under the **GNU Affero General Public License v3.0 or later**
(see [LICENSE](../../LICENSE)).

**Workflows you compile with it are not.** `xdog-flow generate` inlines parts of
flow's own runtime into its output, so without an explicit carve-out the AGPL
would follow those copied portions into every compiled workflow. The
[flow Generated Output Exception](../../LICENSE-EXCEPTION.md) grants you the
right to convey generated modules, portable bundles, scheduling units and
workflow definitions under terms of your choice, including proprietary and
commercial ones.

In short: **use it, and what it produces is yours; fork it or offer it as a
service, and share your changes.**
