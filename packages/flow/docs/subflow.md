# Subflows: typed composition and generated dependencies

A subflow calls a complete child workflow as **one opaque parent node**. The
parent maps values into the child's `$in`; the child runs its own graph and
returns its explicit `$output`. The child's nodes are not expanded into the
parent's frontier scheduler.

## Authoring forms

A node can reference a child file:

```json
{"id": "compose", "type": "subflow", "subflow": "./essay_compose.json"}
```

Or put the complete child workflow object in `subflow` directly. File references
resolve relative to the **parent workflow file's directory**, not the CLI's cwd.
A dict parsed without a base directory cannot resolve file references. Cyclic
file references fail during loading.

The loader reads a file reference into the same in-memory child representation
as an inline object. Codegen embeds that child's definition as a dict literal;
it does not need to reopen the child JSON at runtime. This does not inline child
Python code or collect every custom module/dependency automatically.

**CLI caveat:** `validate --json` currently parses without the file base directory,
so path-referenced children can fail with "needs a base directory" even when the
workflow is valid. Use ordinary `validate FILE` as below. SVG loading likewise
does not provide a base directory while parsing path children; prefer the JSON
source or an inline child. These are loader-path limitations, not child runtime
failures.

## Derived ports

Do **not** author `inputs` / `outputs` on a subflow node. They are derived from
the child:

- Inputs come from `workflow_input_schema(child)`: explicit child `in_schema`,
  otherwise inference from the schemas of consumers fed by `$in`. Explicit
  schema is authoritative, not merged with inferred entries. Conflicting or
  uninferable keys are absent from the derived signature; declare `in_schema`
  for keys the parent must feed.
- Outputs come from the child's explicit `$output` mappings and their schemas.
  Internal child ports are not automatically exported.

The parent wires these derived ports with normal edges, checked by normal port
and schema validation. For example:

```json
{"from": "plan", "to": "compose", "map": {"thesis": "thesis", "points": "points"}}
```

This is an edge fragment. See `packages/flow/examples/essay_writer.json` and
`essay_compose.json` for the complete parent/child pair. A child file can also be
validated and tested independently.

## Runtime, checkpoints and limits

Both interpreter and generated node functions call the **same**
`xdog.flow.executor.execute()` for the child and project child `runtime["out"]`
back into the parent node's output ports. Token use bubbles into the parent;
child trace/context is nested rather than merged into the parent's graph.

Where checkpointing and a parent run ID are supplied, a child derives its own
`parent_run_id::node_id` identity. The parent sees one completed entry. On explicit
resume, an incomplete subflow runs from its child checkpoint if available,
otherwise from the start. This is coarse recovery, not per-child-step scheduling
by the parent or an exactly-once side-effect guarantee.

Current validation rejects nested subflows, a subflow inside a bounded loop,
a subflow as a fan-out worker, and node-local script code/prompts/tools on a
subflow. No recursive or whole-subgraph fan-out execution is implied.

## Generate is not universally standalone

Generated parent modules explicitly import:

```python
from xdog.flow.executor import execute
from xdog.flow.loader import parse_workflow
```

The embedded child still runs through Flow at runtime. Install compatible
`xdog-flow` and all child/custom dependencies in the target environment, or
inspect and provision an appropriate portable bundle. Non-subflow modules may
inline Flow helpers, but SDK nodes still import `xdog.agent` / `xdog.ai`, and
custom tool manifests/imports can add other dependencies. **Behavioral parity is
not universal dependency freedom.**

`generate --portable` vendors `xdog.flow` for subflow parents and selects other
vendored packages/requirements according to the parent workflow. That selection
is not a recursive dependency closure over arbitrary children: do not assume
that every child SDK dependency or custom module is included. Verify the actual
bundle in the target environment. `--offline` downloads selected third-party
wheels during building; it does not infer missing child dependencies.

```bash
# From repository root; validation/generation do not run model turns.
uv run xdog-flow validate packages/flow/examples/essay_writer.json
uv run xdog-flow generate packages/flow/examples/essay_writer.json -o /tmp/essay_workflow.py
# Executing that generated file is a real run and may call models.
```

## Tests and deployment

A parent's companion test can stub the **whole** subflow's output ports. Test
child behavior in the child's own suite. An unstubbed child executes with the
test boundary overrides, so missing agent stubs fail rather than reaching a
provider; scripts/imports remain real. See [testing](testing.md).

For scheduled deployment, review [scheduling](scheduling.md), provision the
actual generated imports, and verify in isolation before arming a timer.
Generated-output licensing and vendored-package licensing are separate; see
[LICENSE-EXCEPTION.md](../../../LICENSE-EXCEPTION.md).
