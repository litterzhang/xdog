# Dynamic fan-out and ordered fan-in

Fan-out maps **one node** over an array whose length is known only at runtime.
The fan group remains one frontier activation; it does not create new graph
nodes or per-instance checkpoint entries. Interpreter and generated Python use
the same observable mapping/reduction semantics.

## Authoring

On the producer-to-worker edge, `fan_out` names an array output port and `map`
connects that array to the worker's **single-item** input:

```json
{
  "from": "plan", "to": "work",
  "fan_out": "tasks",
  "map": {"tasks": "task"}
}
```

Declare `plan.tasks` with `{"type": "array", "items": ...}` and `work.task`
with the matching element schema. Other mapped values on the edge are shared
across instances. Fan-out directly from `$in` requires an explicit array
`in_schema`; the runtime seed's shape alone is not the type declaration.

The worker-to-collector edge selects a reducer:

```json
{
  "from": "work", "to": "merge",
  "fan_in": "list",
  "map": {"result": "results"}
}
```

These are edge fragments, not complete workflows. The collector input must
accept the resulting array. See `packages/flow/examples/release_readiness.json`
for a complete workflow and its companion suite for deterministic fan stubs.

## Reducers and ordering

| Reducer | Instance outputs | Collector value |
| --- | --- | --- |
| `list` | `"a"`, `"b"` | `["a", "b"]` |
| `list` | `[1, 2]`, `[3]` | `[[1, 2], [3]]` |
| `concat` | `[1, 2]`, `[3]` | `[1, 2, 3]` |

Both preserve **input index order**, regardless of completion order. `concat`
is supported now, not a future feature; it requires whole array-valued worker
ports and flattens exactly one level. Use `list` for one value per instance.
An empty input runs the worker zero times and produces `[]`; one item still
produces a one-element list with `list`, never a scalar.

The runtime stores each worker output as an index-ordered list under the worker's
ordinary node/port identity. There are no public `work#i` output keys or separate
trace frames/checkpoints for each instance. `fan_in` applies the chosen reducer
when assembling the collector's inputs.

## Concurrency

- `max_concurrency` limits outer frontier work; the whole fan group holds one
  outer slot.
- `fan_max_concurrency` limits instances inside a fan group. `0` (default) means
  unlimited; set a positive bound for large arrays or provider rate limits.

The inner limiter is separate: instances do not reacquire the outer semaphore,
so an outer cap of one does not self-deadlock. Neither cap creates a distributed
worker pool or grants tool permission.

## Validation and limitations

The loader checks source array schema, mapped element compatibility, collector
schema, reducer shape and topology. It rejects nested fan-out, a fan worker
inside a bounded loop, fan-out on a loop back-edge, `fan_in` from a non-worker,
and subflow nodes used as fan workers. A worker is a single supported node, not
an arbitrary subgraph.

Checkpoint granularity is the **whole group**. If a group has not completed at
the saved frontier boundary, explicit resume can rerun all its instances; there
is no instance-level resume. For external effects, make operations idempotent or
reconcile before retrying. A completed model/tool effect is not undone by a failed
fan group.

## Verify without model calls

From the repository root:

```bash
uv run xdog-flow validate packages/flow/examples/release_readiness.json
uv run xdog-flow test packages/flow/examples/release_readiness.json --allow-script-stub
```

Tests stub agent turns, including CLI backends. Script stubs require the explicit
flag; unstubbed scripts/imports still run for real. The repository's
`packages/flow/tests/test_fan_out.py` checks interpreter/codegen parity for empty,
single-item and multi-item inputs. Compilation parity does not remove generated
runtime dependencies; see [subflows](subflow.md) and [agent backends](cli-agent.md).
