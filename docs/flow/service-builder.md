# An unattended workflow that builds a service from an idea

Status: **working example** — `packages/flow/examples/service_builder/` ·
Related: [`../archive/flow/tasks-service.md`](../archive/flow/tasks-service.md)

Input: one paragraph. No human nodes, no approval gate. Every hour a timer fires,
one agent decides the next increment, builds it, and the checks decide whether it
counted. It stops itself when the criteria are met — or when it stops getting
anywhere.

```
run 1     survey → charter                          (acceptance criteria + a check command)
run 2..n  survey → propose → implement ⇄ verify → record
once done survey → (nothing)                        no tokens spent
```

## The hour is structural, not advisory

`propose` is told to size the increment to about an hour. That alone would be a
suggestion, so the schedule enforces it:

```jsonc
"schedule": {"mode": "timer", "every": "1h", "timeout": "50m"}
```

`timeout` becomes the unit's `TimeoutStartSec`: the run is killed at fifty
minutes whatever it is doing. The prompt says so plainly, because a model that
knows it will be cut off writes differently from one that does not — it finishes
one behaviour rather than starting three.

## What replaces the human

Removing the approval gate removes the thing that catches a bad plan, so the
loop has to catch itself. Four properties, all decided by code in
`builder_ops.py`, none by a prompt:

**A criterion closes only when the checks pass *and* the slug exists.** An agent
that names a criterion the charter does not contain closes nothing. This is the
unattended failure mode specifically: with a person reading output, an invented
criterion is caught immediately; without one, it silently completes the project.

**Absence of a check is a failure.** No `verify.txt`, or an empty one, reports
`passed: no`. If "nothing checked it" and "it passed" agreed, the loop would
terminate fastest on a project that never wrote a test.

**Two runs that change no source file halt it.** The fingerprint covers the
workspace minus `ACCEPTANCE.md`, `JOURNAL.md` and `state.json`, which change
every run by construction — counting them would mean the bound never fires.

**Four runs that meet no criterion halt it too.** This is the subtler stall and
the one that costs money: every run edits files, so the idle counter never
trips, but nothing is ever achieved. A diff every hour and nothing to show for
it. Higher than the idle bound because real work can legitimately span runs.

Halting is a state in the workspace, not a person's attention. Once halted or
complete, `survey` returns `active: no` and the run ends there — **no model is
called**. That matters because nobody uninstalls the timer on a finished
project; the finished state has to be free.

## Why each run starts cold

An agent node's context does not survive a run, which forces every durable fact
into `ACCEPTANCE.md`, `JOURNAL.md` and the source. A run that "remembers" why it
did something is a run whose reasoning cannot be reviewed or resumed.

Within a run it is the opposite: `implement` inherits from **itself** across the
repair loop, so a second attempt remembers the first rather than re-deriving it.
Continuity where it is cheap and inspectable; amnesia where it would hide state.

## Running it

```bash
xdog-flow run service_builder.json --workspace ./my-service    # writes the charter
xdog-flow run service_builder.json --workspace ./my-service    # one increment
```

Or install it and walk away:

```bash
xdog-flow scheduling install service_builder.json
```

**It cannot be `--confined`.** `verify` shells out to run the project's tests,
which is exactly what the audit hook cannot follow. A workflow that builds
software has to run software. The workspace still bounds every path the hook
*can* see.

## What this does not do

"Builds the service automatically" invites more belief than the design supports.

- **It builds what the charter says**, and the charter is written by a model from
  one paragraph, with nobody checking it. This is the single largest risk in the
  unattended version, and it is unmitigated by design — you asked for no human,
  and that is what removing the human costs. Reading `ACCEPTANCE.md` once after
  run 1 buys back nearly all of it.
- **It cannot tell a passing suite from a good one.** The same process writes the
  tests and the code. `verify` proves internal consistency, not fitness.
- **Its ceiling is one increment per run.** Anything needing a coordinated change
  across several criteria will fail, burn a barren run, and eventually halt.
- **Halting is the safe outcome, not a solved problem.** The loop stops with work
  outstanding and a journal explaining how far it got. That is the correct
  behaviour for something running unwatched, and it is not the same as finishing.

The honest framing: this automates the *loop*, not the *judgement*. It is a
tireless junior who reads the charter, does the next thing, runs the tests,
writes down what happened, and downs tools when it stops making progress instead
of thrashing until someone notices.
