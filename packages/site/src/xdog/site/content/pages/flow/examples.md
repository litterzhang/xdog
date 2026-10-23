---
title: Examples
---

<!-- ASCII diagrams below are generated verbatim from xdog.flow.graph.to_ascii_diagram
     over the shipped packages/flow/examples/*.json. Regenerate if an example changes. -->

Workflows ship with Flow (`packages/flow/examples/*.json`), from small focused
patterns to the flagship Release Radar. Selected examples are runnable on the
[HaveFun](/havefun/flow) page; every artifact is also available to the CLI, TUI,
Coding Agent skill, codegen, and scheduler. Commands here assume the repository
root after `uv sync`; live execution is separate from validation and tests.

## Agent Calculator (script → agent + bash)

Two nodes: `make_problem` (a script node) turns the typed integer inputs `a` and
`b` into an arithmetic string like `"347 + 895"`; `solve` (an agent node with the
bash tool) is told not to do the math in its head — it shells out to compute the
expression and replies with the integer.

```
┌────────────────────────┐
│ make_problem [script] *│
└────────────┬───────────┘
             │ problem
        ┌────┘
┌───────▼──────┐
│ solve [agent]│
└──────────────┘
```

**What running it produces:** `make_problem` builds the expression from the
inputs, then `solve` runs a bash command and returns the answer (e.g. a=12,
b=30 → answer `"42"`). A dry-run only exercises the wiring; a real run has the
agent actually compute via bash.

## Generator ↔ Critic (bounded refine loop with web search)

Two agents in a feedback loop: `draft` writes a concise answer to a topic;
`critic` fact-checks it with the `web_search` tool and replies APPROVE or
REVISE + notes. A bounded loop edge (`critic→draft`, when the feedback contains
REVISE, loop≤2) sends the notes back so `draft` can improve the answer.

```
┌────────────────┐
│ draft [agent] *│◄───┬───critic↺draft [feedback contains:{{feedback}} loop≤2]
└────────┬───────┘    │
         │ answer     │
        ┌┘            │
┌───────▼───────┐     │
│ critic [agent]│─────┘
└───────────────┘
```

**What running it produces:** `draft` produces an answer, `critic` web-searches
to verify it; if it says REVISE the answer is rewritten and re-checked, up to
twice. Exhausting this plain `loop` stops successfully with `context.stoppedBy`;
it does not prove that the critic approved the answer. This is the canonical
generate-and-critique multi-agent pattern.

## Essay Writer (a sub-workflow as one node)

Three nodes, but the middle one is a whole workflow. `brief` (an agent) turns a
question into a thesis and three supporting points; `compose` is a **subflow** node
that references `./essay_compose.json` — a reusable draft → critique → revise triad
authored as its own independently runnable workflow; `wrap` (a script) counts the
words and gates on the critic's score. The `compose` node declares no ports: its
`{thesis, points}` inputs and `{final_essay, score}` outputs are *derived* from the
child's signature.

```
┌──────────────┐
│ brief [agent]│
└───────┬──────┘
        │ thesis, key_poin
        └─┐
┌─────────▼────────┐
│ compose [subflow]│
└─────────┬────────┘
          │ final_essay, s
        ┌─┘
┌───────▼──────┐
│ wrap [script]│
└──────────────┘
```

**What running it produces:** `brief` sets the argument; the `compose` child runs
its own draft → critique → revise internally as one opaque step, returning the
polished essay plus the critic's score; `wrap` reports the word count and whether
the score cleared the bar. The child is a complete workflow — you can `run` it on
its own. Both engines run `compose` by calling the same `execute()` on the child,
so `interpret == compile` holds by construction. Generated subflows still depend
on `xdog.flow`; embedding child JSON is not a dependency-free deployment. See the
[*A Workflow as a Node*](/blog/a-workflow-as-a-node) post for the design.

## Flow Release Radar (local repository audit)

`release_readiness.json` is the flagship demonstration of Flow's product vision:
an Agent-authored, human-reviewable workflow that audits this local xdog
repository and declares a Monday timer. It does not run automatically merely
because the example or skill is installed.

```text
collect_repo [deterministic script]
        │ snapshot
        ▼
plan_checks [SDK Agent + filesystem/bash]
        │ checks[]
        ▼ fan_out
 audit#0 … audit#N [parallel SDK Agents]
        │ findings[]
        ▼
score_risk [deterministic policy]
        │
        ▼
report [subflow]
  compose → critique → revise ↺
        │
        ▼
     $output
```

It combines:

- local Git inspection with a deterministic script;
- in-process SDK Agents using `filesystem` and `bash` tools;
- dynamic fan-out over Agent-planned review dimensions;
- deterministic release scoring (`ready` / `review` / `blocked`);
- a path-referenced report subflow with compose/critique/revise and a bounded loop;
- typed schemas, structured Agent output, frontier-batch checkpoints, and a weekly
  systemd timer declaration.

```bash
uv run xdog-flow validate packages/flow/examples/release_readiness.json
uv run xdog-flow graph packages/flow/examples/release_readiness.json --mermaid
uv run xdog-flow generate packages/flow/examples/release_readiness.json -o release_readiness.py
uv run xdog-flow scheduling install packages/flow/examples/release_readiness.json --dry-run
```

The workflow defaults to `/data/workspaces/pyspace/xdog`, but `repo` and
`base_ref` are ordinary `$in` values and can be overridden per run. Review the
`schedule.inputs` defaults separately before installing a real schedule.
Generation does not run the workflow: the generated module needs its SDK, Flow
subflow and custom dependencies when executed. `--dry-run` above is an installer
preview, not a live run. Use ordinary `validate` for path-referenced subflows;
the current `validate --json` path omits their base directory.

### Its test suite

`release_readiness.test.json` sits beside it and covers the graph without calling a
model. Agent turns are stubbed by output port, the `report` subflow is stubbed
whole — it has its own `release_report.test.json` — and `score_risk` runs for real,
because the risk policy is exactly what the case is asserting. The repository
collector is explicitly script-stubbed with `--allow-script-stub`; no repository
or model is needed. The following is an **excerpt** showing the audit selector
and expectations, not a complete runnable suite. Use the checked-in companion
files for complete stubs and schema-valid finding objects.

```json
{
  "cases": [
    {
      "name": "critical finding blocks the release",
      "agents": {
        "audit": [
          {"when": {"check": {"name": "security"}}, "then": {"finding": {"severity": "critical"}}},
          {"then": {"finding": {"severity": "low"}}}
        ]
      },
      "expect": {
        "output": {"risk": {"status": "blocked", "release_allowed": false}},
        "calls": {"audit": 3, "report": 1}
      }
    }
  ]
}
```

```bash
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
```

`release_report.test.json` pins the review loop from the other side: one case scores
below the gate then above it, another passes on the first read so `revise` never
runs, and a third never satisfies the reviewer so the loop stops at its `loop.max`
bound. Writing that third case is what surfaced a real bug in the example — an
unconditional `compose → revise` edge that let the quality gate be bypassed.

## Depins Enrichment (an Agent with commit access)

`depins_enrich/` is the one example that is not a teaching device. It runs on a
small server every four hours, points a coding Agent at a live Flask site, and
lets it commit and push its own work — but only if a chain of deterministic
checks admits it.

```text
precheck ─(refused)──────────────────────────► skipped ──► $output
    │
    └─(proceed)─► build ─► scope ─► guards ─► gate ─► validate ─► decide
                                               ▲                    │
                                               │                    ├─(approved)──► commit
                                               │                    │
                                               │                    ├─(unfixable)─► revert
                                               │                    │
                                               └───── fix ◄─────────┘
                                             while note != "", max 3
```

Three Agents (`build`, `validate`, `fix`) and eight script nodes. Everything
between `build` and `commit` is deterministic code whose job is to *not believe*
the Agent: `guards` imports the live registries out of the repository the Agent
just edited and diffs them against a ledger, so a no-op cycle, a runaway cycle
and a duplicate are all set-differences rather than heuristics; `gate` runs ruff
against a pre-captured baseline, then pybabel, then a sweep of every route the
app exposes.

Four things it is worth reading the JSON for:

- **`precheck` is both the gate and the input hydrator.** Every downstream node's
  inputs flow out of it, so a refusal leaves nothing with an enabled incoming
  edge and the chain is skipped structurally — no `if proceed:` anywhere.
- **A node never raises to mean "stop the cycle."** It returns a verdict and the
  edges decide. A raise means something is actually broken.
- **`decide` is a node, not conditions scattered on the write edges.** That gives
  `commit` and `revert` exactly one gated predecessor each. The version without
  it had `commit` reachable while `approved` was false.
- **The repair back-edge is a `while`, not a `loop`.** A fixer that has not
  converged in three attempts has left a broken tree; the run fails rather than
  stopping quietly on it.

Its script nodes use `run:` references to sibling modules, so it is a directory
rather than a lone JSON file. That case exposed bundling and codegen defects
that inline-`code` examples had hidden. See
[Letting an Agent Commit](/blog/letting-an-agent-commit) for that story.

Its six-case suite covers every terminal state and needs no repository,
virtualenv or network:

```bash
uv run xdog-flow test packages/flow/examples/depins_enrich/ --allow-script-stub
```

## Other shipped patterns

- `trip_planner.json` — structured Agent outputs and JSONPath sub-field mappings.
- `cli_triage.json` — Claude Code CLI backend plus deterministic routing.
- `digest_timer.json` — cron-based timer scheduling.
- `triage_hook.json` — HTTP hook configuration for a human signal; review the
  current listener deployment limits in the [Reference](/packages/flow/reference#scheduling).

## Run them

```bash
# SDK-only example: stub streaming; the arithmetic script still runs
uv run xdog-flow run packages/flow/examples/agent_calculator.json --dry-run --input a=12 --input b=30

# Render the diagram
uv run xdog-flow graph packages/flow/examples/refine_loop.json
```

Or open [HaveFun](/havefun/flow) to run a supported example in the browser.

`run --dry-run` does not stub external CLI backends or scripts and may fail a
structured SDK node's submission contract. Generated Python does not implement
that switch. Tests stub all agent turns, but unstubbed scripts/imports remain real;
review workflows before execution. HaveFun is for trusted/local use, not hostile
workflow submissions.
