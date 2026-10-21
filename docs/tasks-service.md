# Tasks — a service that uses flow as its worker

Status: **idea, not designed** · Audience: whoever picks this up · Prerequisite:
[`scheduling.md`](../packages/flow/docs/scheduling.md) and
[`expressiveness.md`](../packages/flow/docs/expressiveness.md)

## The idea

A user creates a task — on a web UI or a WeChat miniprogram — and a service gets
it done by running flow workflows. The service owns task state; flow does the
work.

Three things are meant to be AI-driven, and they are genuinely different jobs:

1. **Authoring.** No workflow exists that does what the task needs, so an agent
   writes one.
2. **Routing.** A workflow already exists that fits, so an agent picks it and
   maps the task onto its input ports.
3. **Scheduling.** What runs next, for whom, in what order — decided by a
   workflow whose nodes are agents, reading priority, user capacity and whatever
   else the operator cares about.

The third is the one worth building the service for. The first two are useful;
the third is the claim.

---

## Why the third one is the interesting part

A scheduler is normally the least inspectable component in a system: a priority
queue plus accumulated heuristics, in code, changed by deploys. Expressing it as
a workflow makes each decision a graph with typed ports that was validated before
it ran, and each run leaves a trace that says which node decided what.

That is flow used on itself. If the format is good enough to encode "how work
gets allocated across users", that is a stronger argument for it than any number
of ETL examples — and if it is *not* good enough, building this is how we find
out, which is worth almost as much.

Concretely, a scheduling pass might be:

```text
$in ──> gather_state ──> assess_capacity ──┐
   (script: queue,       (agent: who is    ├──> allocate ──> $output
    users, SLAs)          overloaded?)     │    (agent: what
                       classify_urgency ───┘     runs next)
                       (agent: what is
                        actually urgent?)
```

`gather_state` is a script — deterministic, no model. The agents do the judging.
`allocate` emits a typed decision the service executes. The whole thing runs on
a timer through `xdog-flow scheduling install`, which already exists.

---

## What already exists

Most of the substrate is built. Worth knowing before designing:

| Need | What covers it |
|---|---|
| Run a workflow on a schedule | `schedule` block + `xdog-flow scheduling install` (systemd timers / hooks) |
| Survive a crash mid-task | Checkpointing — `run_id` + `JSONFileCheckpointStore`, resumes at the frontier batch |
| Let an agent write a workflow | The `flow` skill, shipped in the `xdog-flow` wheel |
| Tell an agent *why* its workflow is wrong | `validate --json` — 18 stable error codes and repair hints, built for exactly this loop |
| Long-running multi-agent process | `xdog-claw` — an orchestration runtime with groups, queues, memory |
| Carry context between agent steps | `inherit` (1.1.0) |
| Bound a retry/refine loop | `loop` / `while` with `max`, validated for termination |

The gap is the service: task records, user accounts, the two front-ends, and the
loop that turns a task into a workflow run and back into a status.

---

## The tension to resolve before building

flow's stated product principle is **"fixed workflows, not open-ended
autonomy — flow crystallizes processes that have become stable enough to
repeat."** An AI that writes a fresh workflow per task is the opposite of that.

This is not fatal, but it must be decided rather than drifted into. Two honest
positions:

- **Authoring is a build step, not a run step.** The AI drafts a workflow, it is
  validated, and *a human approves it into a catalogue*. Routing then picks from
  the catalogue. Tasks are served by workflows that were reviewed once and reused
  many times, which is exactly the stated principle — the AI just removes the
  blank page.
- **Authoring is a run step, with a blast radius.** A generated workflow runs
  immediately but only within a sandbox: no `bash`, a token budget, a wall clock,
  and a tool allow-list per task class. The filesystem half of that is
  [workspace confinement](./workspace-confinement.md), which does not exist yet —
  and note that today a `script` node runs unrestricted Python, so this position
  is not currently available at all.

The first is the smaller claim and almost certainly the right start. The second
is the more exciting demo and the one that will produce an incident.

Either way: **a generated workflow must pass `validate` before it runs**, and
that is already free.

---

## The parts that will actually be hard

Not the CRUD. These:

**Deciding when a task is done.** A workflow returns an envelope with
`success: true`. That says the graph completed, not that the user's problem was
solved. Something has to judge completion, and if that something is an agent it
can be wrong in both directions — closing a task that is not done is worse, since
the user has stopped watching. This is what user feedback is for; see below.

**The scheduler's authority.** If an agent decides what runs next, it can starve
a user, misjudge urgency, or thrash. It needs bounds it cannot argue with: a
maximum wait before a task is escalated regardless, a per-user floor, and a
deterministic tiebreak. Encode those as script nodes around the agent, not as
instructions inside its prompt — an instruction is a suggestion, an edge is not.

**Cost.** Three AI roles plus the task's own agents means a trivial task can cost
several model calls before any work happens. Routing should be cheap and should
short-circuit: exact matches to a known task class skip the agent entirely.

**Idempotency across the boundary.** The service owns the task record; flow owns
the run. A crash between "run finished" and "status written" must not double-run
a task with side effects. flow's checkpoint gives at-least-once per batch, so the
service needs its own dedupe key.

**WeChat's constraints.** The miniprogram cannot hold a connection for a
long-running task. This is a notify-on-completion design from the start, and
`xdog-claw` already has a WeChat channel with the IP-whitelist and template-
message realities worked out.

---

## User feedback — the only ground truth in the system

The user can say whether the task was *actually* done, and leave a comment. This
is a small feature and the most important one in the document, because
everything else is the system marking its own homework: the workflow's
`success: true` means the graph completed, a completion-judging agent is a guess,
and the router's confidence is a guess about a guess. The user's answer is the
only signal that comes from outside.

### Three states, not two — and a fourth that is not a state

| | Meaning | Who says it |
|---|---|---|
| `run_completed` | The graph finished without failing | flow |
| `confirmed` | The user says their problem is solved | the user |
| `rejected` | The user says it is not | the user |
| *(no answer)* | **Unknown.** Not success. | nobody |

Conflating the first two is the failure this service exists to avoid. A task
should be able to sit in `run_completed` and `rejected` at once — the workflow
did run, and it did not help. That combination is the most valuable record in the
system, because it is the only place a working pipeline and an unsolved problem
are visible together.

**Silence is not consent.** The strong temptation is to treat "no feedback after
three days" as success, because it makes the dashboard green. It means the user
was satisfied, or gave up, or never saw the notification, and those are very
different. Model it as unknown and count it separately. A system whose success
metric is mostly inferred from silence will look excellent and be unfalsifiable.

### Comments are evidence, not instructions

A rejection comment says *why*, and the temptation is to feed it straight back
into the agent that chose or wrote the workflow. Resist that at the single-item
level: one comment is one data point, and an agent that adjusts on one comment
will thrash between users with opposite preferences.

Two safe uses, one unsafe:

- **Safe — reopen with context.** The comment goes into the next run's input, or
  into the session an `inherit`ing retry node starts from. That is scoped to the
  one task where the evidence came from.
- **Safe — aggregate.** "This workflow has been rejected 6 of the last 20 times
  it was routed to" is a fact about the catalogue that should reach a human, and
  should probably stop the router choosing it.
- **Unsafe — silent adaptation.** Rewriting a catalogue workflow, or shifting the
  router's behaviour globally, on the strength of individual comments. If that is
  wanted, it belongs behind the same human approval gate as authoring.

### What rejection should do

`rejected` is a routing decision, not a re-run. The comment is the input to it:

- *"It did the wrong thing"* → route differently, or author something new.
- *"It did the right thing badly"* → same workflow, retry with the comment in
  context — the `inherit` case, where the retry node starts from the failed
  attempt's session and can see what it already tried.
- *"This needs a person"* → escalate. A task class that is rejected repeatedly is
  a task class that should not be automated yet, and the service should be able
  to say so.

Bound it: a task that has been rejected twice stops being retried automatically.
Otherwise a task the system cannot do becomes a loop that costs money forever —
the same reason flow requires `loop.max` on every back-edge.

### When to ask

Too early and the user has not looked; too late and they have moved on. WeChat
makes this concrete rather than theoretical: a template message can be sent once,
so the timing is a real decision with a real cost. Probably: ask on completion,
ask once more after a delay if unanswered, then stop and leave the task
`unknown` rather than nagging or assuming.

### Why this closes the loop

Each of the three AI roles is currently unfalsifiable, and feedback is what makes
each one measurable:

| Role | Currently judged by | Made falsifiable by |
|---|---|---|
| Authoring | "the workflow validated and ran" | did the task it was written for get confirmed |
| Routing | the router's own confidence | confirmation rate per (task class → workflow) pair |
| Scheduling | nothing at all | rejections correlated with how long a task waited |

The scheduling row is the one to watch. A scheduler that is starving a class of
user produces confirmations that arrive late or not at all, and no internal
metric will show that — the queue drains, the graphs complete, everything looks
healthy. Feedback plus wait time is the only way that becomes visible.

---

## Sketch of a shape

Nothing here is decided.

```text
Web UI ─┐                         ┌── run_completed (flow says so)
        ├─> Tasks service ──> task ├── confirmed / rejected (user says so)
WeChat ─┘         │               └── comment
                  │                        │
                  │        feedback ───────┘
                  │           reopens, escalates, or aggregates
                  ├── route: which workflow?  ──> catalogue
                  ├── author: none fits       ──> agent + flow skill
                  │                                    │
                  │                             validate --json ──┐
                  │                                    ▲          │ errors
                  │                                    └──────────┘ repair loop
                  │
                  └── schedule: a flow workflow on a timer, deciding
                      what runs next across all users
                                  │
                                  v
                        xdog-flow run <workflow> --input ...
                                  │
                        checkpoint + status callback
```

The service is deliberately not a workflow engine. It is a task store, two
front-ends, and a dispatcher. Everything that looks like execution logic belongs
in a workflow, including — especially — the scheduling.

---

## Smallest thing worth building first

Not the whole service. The claim is "scheduling can be a workflow", so test the
claim:

1. A hand-written scheduling workflow with agent nodes, run on a timer, reading a
   queue from a JSON file and writing an allocation back.
2. A catalogue of three hand-written task workflows and a routing agent that
   picks between them.
3. Only then the task service, the front-ends, and AI authoring.

Step 1 alone answers whether a workflow is a reasonable place to put scheduling
judgement. If it is not, everything after it changes.

---

## Open questions

- Where does the task record live? The service needs a database, which is the
  first thing in this stack that is not a file — worth being honest about, since
  "no database" is currently a product claim.
- Is the catalogue a directory of `workflow.json` in Git, or rows in that
  database? Git keeps the Git-native property and gives review for free.
- Does a task get its own workflow *run*, or does one run handle a batch? Batching
  is cheaper and much harder to attribute when it fails.
- Multi-tenancy: one flow process per user, or one shared with capacity limits?
  The scheduling workflow's job description changes completely between the two.
- What does a user see while a task is running? The trace is the honest answer
  but it is not a product surface yet.
- Does a rejection reopen the *same* task or open a linked one? Reopening keeps
  the history together; a linked task keeps the metrics honest, since a task
  reopened four times is not the same as four tasks that each worked.
- Who sees the aggregate rejection rate per workflow, and what are they expected
  to do about it? An unread metric is not a control.
