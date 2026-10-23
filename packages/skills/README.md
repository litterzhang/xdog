# xdog-skills

Common medium-level skills with isolated domain support and extension code. The
hub ships **goal** and **plan**. Flow and Eyes keep their independent engines and
packaged skills; they are not copied here. The distribution depends directly only
on `xdog-agent` and stdlib, not a model harness or Eyes runtime.

Coding and Claw include `xdog-skills` by default; their `skills` extra remains
compatible. Other consumers can install the hub directly. Metadata discovery/import
is inert. The installed Goal capability independently contributes native commands,
the actual owner-bound `goal` tool and discoverable skill metadata. There is no
Goal enable switch. **The skill body is not active by default**: load it through
the host's existing skill mechanism when needed. Discovery/installation/ordinary
chat creates no Goal, model, worker or observation; availability is not delegation.

## Goal: records and reminders, not an agent runner

The current main Agent knows the conversation and owns planning, optional child
assignment through the host's existing tools, task reconciliation and completion.
The [goal skill](src/xdog/skills/skills/goal/SKILL.md) explains that workflow. A host
without subagents can do everything on its main Agent. Goal has no worker/reviewer
runner, mandatory independent review or attempt/report acceptance protocol.
Existing host permissions, readonly and tool approvals remain authoritative.

Native commands are:

```text
/goal create <outcome>
/goal status
/goal pause [reason]
/goal resume
/goal clear [reason]
/goal history
```

The `goal` model tool accepts these `action` values:
`create`, `status`, `summary`, `tasks`, `history`, `update`, `plan`, `report`,
`task_update`, `wait`, `activate`, `pause`, `resume`, `complete`, `clear`.
`plan(content=...)` saves the main Agent's plan, not another model call; omit
content to read it. `complete(summary=...)` records the main Agent's evidence-based
judgment against the original outcome/criteria. A final prose answer alone does
not save completion. Actual artifacts and verification, not a worker's claim or
automatic script verdict, determine the Agent's assessment.

Each stable owner has at most **one current Goal**, including waiting, paused and
completed. Coding binds the exact session/branch; Claw binds the stable group,
not its rotating context or session files. `/new` in Claw does not move the Goal.
Completed remains current and visible through `/goal`; a newly delegated Goal may
atomically replace that completed binding while preserving completed history.
Only explicit clear releases the binding without replacement. Unfinished Goals
cannot be silently overwritten. Clear preserves artifacts/history and does not
cancel unrelated observations, accepted inputs, tools or children.

## Plans, tasks and progress

GoalStore owns a leased `xdog.goal.registry.v2` registry with current owner binding,
revision history and an immutable reminder outbox. Claw stores it at
`<data_dir>/groups/<id>/goals/registry.sqlite3`. Artifacts belong under
`<workspace>/.xdog/goals/<goal-id>/`:

- `plan.md`: the main Agent's plan.
- `tasks/<task-id>.json`: the **only task-state authority**, not duplicate DB/todo
  projections. Status is pending/in_progress/blocked/completed/cancelled.
- `progress.md`: the **only progress-report authority**, human-readable and
  structured history in one atomic, version-checked Markdown file.
- `eyes.json`: generated data-only observation configuration for this task directory.

Workers maintain only assigned tasks, including blockers/results/evidence on both
success and failure. The main Agent checks that evidence and records its own
acceptance task/report, not overwriting another worker's status as acknowledgement.
Reassign explicitly before taking over; keep one writer per task. Use
`task_update` with the observed `expected_version` (0 creates); on conflict re-read
and reconcile, never blindly retry. No task text or worker label grants authority.

Before initial work and **every continuation**, including ACP reminders and user
resume, the main Agent first checks actual identity/lifecycle and task evidence,
then assesses progress against its prior report. If waiting, it must satisfy and
activate that wait before doing more Goal work. It saves the assessment using the
native `goal` tool before execution; a report is not a direct Markdown edit or
worker submission. Example tool arguments for the first report:

```json
{"action":"report","goal_id":"EXACT_CURRENT_GOAL_ID","report":{
  "expected_version":0,"progress":"baseline",
  "summary":"Initial assessment of actual state",
  "completed":[],"evidence":[],"blockers":[],
  "next_steps":["First permitted, verifiable action"]}}
```

Later reports use the latest report version and `advanced`, `unchanged` or
`regressed`, judged by the Agent, not file counts or a script. Required arrays may
be empty; entries must be nonempty strings. Errors/CAS conflicts are failures,
not empty history or permission to reset version 0. If report persistence is
unavailable/denied, explain the host/capability blocker without bypassing it or
continuing unreported work. Repeated no progress with no viable new approach
should lead the Agent to report blockers and pause, not an automatic round limit.

Status/summary default to counts, bounded previews, latest report and separate
execution/observation/delivery facts. Use `tasks` for filtered/single-task reads.
Large results use `xdog.goal_data_page.v1` fragments: follow the exact `next_query`,
restarting if the snapshot hash changes. Do not repeat successful mutations just
to retrieve more output. Domain API `status(detail=True)` retains full-history
reads; this is not a public Claw embedding SDK.

### Restricted child delegation

Coding and Claw children receive frozen subsets of actual parent tools and
available skills. Explicitly select `goal` with the current Goal and existing
assigned task IDs in child configuration:

```json
{"tools":["goal"],"skills":[],"tool_scopes":{
  "goal":{"goal_id":"EXACT_CURRENT_GOAL_ID","task_ids":["assigned-task"]}}}
```

This is child settings data, not a standalone worker-spawn API. The actual parent
must have the live Goal tool and grant the scope under its host policy. The child
can read one granted task or CAS-update its status/summary/evidence, never create
or reassign tasks, plan/report for the whole Goal or control its lifecycle.
Execution tools require separate grants. Readonly still forbids task writes.
Existing child results can be recorded while waiting/paused without releasing
that state; completion/clear/replacement fences old scopes.

Hosts recheck identity/schema/action/scope, skill grants and permissions at
admission, after approval and actual executor entry. Effective configuration,
tool schema ceilings and filesystem roots persist before model execution;
continuation revalidates against them, never borrowing later broader authority.
Saved records are data, not restored registry handles or approval caches.
Interrupted/unknown effects are not automatically replayed. See the
[Coding](../coding/README.md#child-authority-and-persisted-constraints) and
[Claw](../claw/README.md#child-authority-and-persisted-constraints) host contracts.

### External-worker task helper

Only when explicitly permitted and scoped delegation is absent, an external
worker/no-delegation host can use the local task helper through its normally
permission-gated shell tool. Do not silently replace a rejected child grant with
filesystem/shell access. Use the exact absolute workspace and Goal ID returned by
the original owner, never infer them from event/task text:

```bash
uv run python -m xdog.skills.goal.task_cli --workspace /exact/owner/workspace --goal-id EXACT_ID list
uv run python -m xdog.skills.goal.task_cli --workspace /exact/owner/workspace --goal-id EXACT_ID update \
  --task-id assigned-task --expected-version 1 \
  --data '{"status":"in_progress","summary":"Working","evidence":[]}'
```

The helper shares task lock/CAS/no-follow atomic writes and only lists or updates
**existing** tasks. It does not inspect the current host binding or grant authority;
the caller must verify both. It has no report/plan/lifecycle/worker commands.
`.xdog/goals/` can contain private routing/evidence: do not commit/publish it or
change project ignore rules without the user's request.

## Waiting, pause and question delivery

`active` means eligible for main-Agent work, **not** that an Agent is running.
First record a local blocker on the affected task and continue permitted independent
work; an information limitation or tool denial is not automatically a Goal-wide wait.

| State | Continuation rule |
| --- | --- |
| `waiting(tasks)` | Only a task cause prompts a check; the main Agent inspects actual evidence and uses `activate` with exact `goal_id`, observed `version` and a nonempty reason when satisfied. File changes never automatically activate or accept work. |
| `waiting(user)` | Only for a genuine business decision or indispensable material when no independent work can advance. Save nonempty `question`, `impact`, `resume_when`; actual authorized user input must satisfy that condition before versioned `activate`. Time/task/automatic input cannot release it. |
| `paused` | Proactive reminders and Goal's owned Eyes registration stop until explicit user resume. Accepted work/tools/children remain independent. Resume is not the action for a satisfied wait. |
| completed/cleared | No execution/reactivation of historical work; create a new delegated Goal instead. |

New `wait(host)` is rejected. Historical host waits remain readable/fenced and
require explicit user conversion; they are never rewritten automatically into task
waits. Host approval belongs only to the existing host permission system, not a
Goal question or invented authorization command. Execution/delivery failures are
durable runtime facts, not automatic Goal business-state changes.

Saving `wait(user)` persists a question, **not its delivery**. Read status for the
actual `question.id`. If an authorized host proactive message tool supports a
correlation reference, it may use that ID; it grants no routing authority. The
optional readonly `question.delivery` reports actual host output facts. Unavailable/
untracked is not proof of no send; unknown/partial/cancelled is not delivered.
Neither sending nor reading receipts releases the wait. Identical wait retries keep
the question ID; do not resend merely because an ACK was lost. In Claw, automatic
ACP inputs default to no chat reply target; see [explicit messaging and question
delivery](../claw/docs/acp-input-replies.md).

## Events, Eyes and recovery

Generic host settled/available and task-file signals feed one durable coalescer.
When appropriate it sends stable-ID automatic ACP enqueue reminders to the same
main Agent and original Inbox/controller. ACP delivers; the skill instructs; the
main Agent decides. There is no UI interception, second Agent loop, hidden tool
approval or polling-based progress judge. Task waits do not generate blind idle
reminders. Pending/running accepted reminders fence another send; newer changes
remain dirty until consumed. Recovery inspects original receipts, not elapsed idle
time; known-not-sent retries keep original ID/content. Unknown/interrupted effects
are never automatically replayed, including across pause/resume.

Optional task observation uses the **installed public Eyes CLI** and its shared
user-local daemon; Goal imports no Eyes SDK/runtime/loader/private daemon protocol.
It owns just one registration for its task JSON directory, excluding configuration/
state files. An owned `events --follow` JSONL process consumes signals; only after
durable Goal signal commit is a separate CLI ACK allowed. Missing Eyes or original
owner endpoint is reported, not auto-installed or retargeted.

Pause stops that registration and proactive reminders; explicit resume reconciles
changed task files. Clear/completion stop only Goal's registration, never unrelated
observations or accepted downstream work. Host retirement/close detaches its CLI
stream (`suspend=False`), leaving daemon-owned offline observation; Goal business
stops use `suspend=True`. Shutdown settles actual operations, ACP/observation
resources and storage in owner order. This is separate from ordinary chat delivery.

Legacy `journal.sqlite3` and `goals.json` stay untouched, with their locations
reported in status/history. They are not silently upgraded or used to restore old
worker/reviewer effects.

The optional explicitly selected `goal_ui` display exposes `/goals` and remains
readonly. Default `/goal` commands/tools use the installed native capability, not
that worker. Native `/goal` overlaps only its own discoverable skill command;
builtins/other owners cannot be shadowed. UI failure cannot own Goal lifetime.

See [host faults](../../docs/goal-host-faults.md),
[optimization design](../../docs/goal-optimization-design.md),
[optimization acceptance](../../docs/goal-optimization-requirements.json),
[simplified design](../../docs/goal-simple-design.md) and
[baseline acceptance](../../docs/goal-simple-requirements.json).
Earlier kernel/adapter/reviewer records describe retired designs, not this contract.
