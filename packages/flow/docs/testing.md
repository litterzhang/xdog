# Workflow tests — `xdog-flow test`

A workflow is a program, so it needs tests. Flow's answer is a companion suite:

```text
release_readiness.json
release_readiness.test.json
```

Commands below assume the repository root after `uv sync`:

```bash
uv run xdog-flow test packages/flow/examples/release_readiness.json --allow-script-stub  # sibling suite
uv run xdog-flow test packages/flow/examples/ --allow-script-stub  # companion suites
uv run xdog-flow test packages/flow/examples/release_readiness.json \
  --allow-script-stub --case "critical finding blocks the release"
```

Exit status is 1 if any case fails, so it drops straight into a pre-commit hook or
a CI step.

## What is stubbed, and what is not

Only the boundaries a test cannot reason about are stubbed:

| Boundary | Stubbed via | Default |
|---|---|---|
| Agent turns (SDK **and** CLI backends) | `agents` | required |
| Human gates | `signals` | required to pass a gate |
| Subflow nodes | `subflows` | optional |
| Script nodes | `scripts` | **opt-in** — `--allow-script-stub` |

Everything else runs for real: edges, conditions, loops, fan-out, fan-in
aggregation, mappings, type coercion, retry, isolation, `$output` collection. Those
are the parts a workflow test is supposed to be testing, so they are deliberately
not stubbable. A case cannot degrade into "I mocked the whole chain and asserted my
own mocks".

Script stubbing is behind a flag for the same reason. It exists for scripts with
external dependencies — one that shells out to `git`, or hits the network — and the
flag keeps that choice visible in a diff instead of buried in a JSON file.

### Where the stub is injected

At the provider call, not at the node output. Prompt interpolation has already run
by then, and the value the stub returns still goes through the node's required-field
check and `to_state` coercion. Two consequences worth knowing:

* **A stub is validated by the node's own declared contract.** A multi-port agent
  stub missing a field fails with the same error a real model reply would produce.
  There is no separate schema check in the test layer that could drift.
* **A broken prompt is still a broken prompt.** `{{$.snapshto}}` is not hidden by
  the stub.

Because the stub runner answers *every* agent node — whatever its `backend` — no
provider or external agent CLI is ever constructed. An agent node with no stub
fails loudly instead of falling back to a real backend. This is **not a no-effects guarantee for the whole suite**:
unstubbed scripts and import-time custom code can still read/write files, launch
processes or use the network. Review them or supply permitted script stubs.
`run --dry-run` is different: it replaces SDK streaming only, does not replace
CLI backends/scripts, and may not satisfy structured-result contracts.

## Suite shape

```json
{
  "workflow": "./release_readiness.json",
  "cases": [
    {
      "name": "critical finding blocks the release",
      "inputs": {"repo": "/fixture/repo", "base_ref": "main"},
      "signals": ["approve_release"],
      "max_tokens": 8000,

      "agents": { "...": "..." },
      "subflows": { "...": "..." },
      "scripts": { "...": "..." },

      "expect": { "...": "..." }
    }
  ]
}
```

`workflow` is optional — it defaults to the sibling file, which is what the naming
convention is for. Name it explicitly if a suite lives somewhere else.

## Stubs

The following snippets are fragments of a case object. A stub is always authored
as the node's **output ports**. Either a constant:

```json
"agents": {
  "plan_checks": {
    "checks": [{"name": "security", "focus": "auth"}],
    "rationale": "security-sensitive change"
  }
}
```

or an ordered rule list, first match wins:

```json
"agents": {
  "audit": [
    {"when": {"check": {"name": "security"}}, "then": {"finding": {"severity": "critical", "...": "..."}}},
    {"then": {"finding": {"severity": "low", "...": "..."}}}
  ]
}
```

A rule with no selector is the default branch and must come last.

### Selectors

| Selector | Means | Determinism |
|---|---|---|
| `when` | deep-subset match on the activation's **inputs** | a value match, so fan-out completion order is irrelevant |
| `index` | the instance's position in the fanned array | the source array, **not** arrival order |
| `round` | which activation of the node this is, 1-based | fan instances share a round; loops are sequential |

All supplied selectors must hold. They compose — `{"round": 2, "index": 0}` is the
first instance of the second activation.

Pick `when` for fan-out (instances differ by their item) and `round` for loops
(iterations usually differ only by a long text input that is awkward to match).

```json
"critique": [
  {"round": 1, "then": {"quality_score": 5, "feedback": "cite evidence", "report": "v1"}},
  {"then": {"quality_score": 9, "feedback": "No changes needed.", "report": "v2"}}
]
```

That case is what pins down loop termination: the first review fails the gate, the
second passes it. Make both rounds score low instead and the loop runs to its
`loop.max` bound — which `calls` will then report.

### Optional `tokens`

A rule may declare `"tokens": 5000`. Combined with a case-level `max_tokens`, that
makes the budget breaker testable:

```json
{"then": {"finding": {"...": "..."}}, "tokens": 5000}
```

## Expectations

```json
"expect": {
  "success": true,
  "output": {"risk": {"status": "blocked", "release_allowed": false}},
  "calls": {"audit": 3, "revise": 0}
}
```

**Outcome** is a closed three-way choice; exactly one applies, and `success` is the
default when none is given:

| | Holds when |
|---|---|
| `"success": true` | the run completes |
| `"error": "<substring>"` | the run fails and the message contains that substring |
| `"paused": "<node id>"` | the run pauses at that human node |

There is no `success: false` — a failure and a pause are different events with
different evidence, and naming which one you expect is the point.

**`output`** is a deep-subset match on `$output`. **`calls`** is a partial map of
node id to invocation count. One number covers what would otherwise be four
separate assertions:

| Question | Written as |
|---|---|
| did this node run? | `{"report": 1}` |
| was this branch skipped? | `{"revise": 0}` |
| how many fan instances? | `{"audit": 3}` |
| how many loop iterations? | `{"critique": 2}` |

`calls` counts *invocations*, so a fan-out node reports its instance count, not its
activation count. Current validation rejects fan workers inside bounded loops;
the count does not imply support for that topology.

### Matching rules

One rule, shared by `when` and `expect.output`:

* **object** — expected keys must be present and match recursively; extra keys in
  the actual value are ignored.
* **array** — same length, element-wise recursive match. Length is significant
  because "how many items came out of the fan" is usually the thing under test.
* **scalar** — `==`, except that `bool` only matches `bool` (otherwise Python's
  `True == 1` would let `"release_allowed": 1` silently pass).

## Failures

```text
$ xdog-flow test examples/release_readiness.json --allow-script-stub

examples/release_readiness.test.json
  critical finding blocks the release          FAIL   5 nodes, audit x3

    expect.output.risk.status
      expect  'review'
      actual  'blocked'

    expect.calls.audit
      expect  2
      actual  3

    trace
      step 0   collect_repo         stub
      step 1   plan_checks          stub
      step 2   audit                stub x3
      step 3   score_risk           ran
      step 4   report               stub

0 passed, 1 failed
```

Only the deepest differing path is printed, not both whole objects. The trace marks
each node `stub` or `ran`, which is usually the fastest route to the cause.

Two failures are about the *suite* rather than the workflow:

* **No rule matched a call.** The activation's full inputs are printed alongside
  every rule's selector, so you can see why none fit.
* **A selector never fired.** A `round: 3` against a loop that ran twice is almost
  always a stale case rather than an intentional one, so it fails rather than
  quietly falling through to the default. Nodes that never ran are exempt — keeping
  a stub for a branch this case does not take is fine.

## Authoring errors are caught before anything runs

Loading validates the suite against the workflow:

* a stub aimed at a node that does not exist, or at the wrong node type
* a stub setting an output port the node never declared
* `paused` naming something that is not a human node
* `calls` naming a node that does not exist
* unknown keys anywhere

Without these a typo falls through to a default rule and the case still "passes"
while asserting nothing.

## Relationship to subflows

Nodes *inside* a subflow are not individually stubbable — a subflow is stubbed
whole:

```json
"subflows": {
  "report": {"final_report": "# Release blocked", "quality_score": 9, "report_feedback": "No changes needed."}
}
```

That is the intended decomposition: a child workflow carries its own `.test.json`,
so the parent asserts the composition and the child asserts its own loop. Stubbing
child nodes from the parent would duplicate coverage and bind the parent's cases to
the child's internal structure.

An unstubbed subflow still runs its child for real — with the same stubs object
attached, so an agent node in there fails with "no stub" rather than reaching a
provider.

## What this does not do

* **It does not test generated code.** `xdog-flow test` runs the interpreter only.
  `interpret == compile` is flow's own invariant, guarded by flow's own test suite;
  binding your cases to codegen output would couple them to something they should
  not know about.
* **It does not assert on prompts.** Prompts are built for real but not currently
  matchable.
* **It does not replace running the workflow.** A suite proves the graph behaves;
  only a real run proves the prompts work.
