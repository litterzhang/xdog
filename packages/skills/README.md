# xdog-skills

Common medium-level skills with isolated domain support and extension code. The
hub ships **goal** and **plan**. Flow and Eyes keep their independent engines and
packaged skills; they are not copied here.

Coding and Claw include `xdog-skills` by default. Their `skills` extra remains
compatible. Other consumers can install the hub directly. Namespace discovery
lists the skill metadata without copying files or starting a model, host, store
or observer. Full skill bodies load on demand. There is no Goal enable switch;
availability is not user delegation.

## Goal: records and reminders, not an agent runner

The current main Agent knows the conversation and owns planning, optional worker
assignment using the host's existing tools, task reconciliation and completion.
The [goal skill](src/xdog/skills/skills/goal/SKILL.md) explains that workflow. A host
without subagents can do everything on its main Agent.

The trusted installed native Goal extension provides:

```text
/goal create <outcome>
/goal status
/goal pause [reason]
/goal resume
/goal clear [reason]
/goal history
```

The `goal` model tool provides create/status/summary/tasks/history/update/plan/report/task_update/wait/
pause/resume/complete/clear. `plan` saves the main Agent's plan, not another model
call. `complete` records its own evidence-based judgment, not a forced independent
review. There are no Goal worker/reviewer runners, role tools or attempt/report
acceptance protocol. Existing permissions and approvals still apply.

Each stable main-Agent owner has at most **one current Goal**, including paused or
waiting Goals. Completed/cleared Goals release the binding but keep history. Clear
does not delete task files, cancel unrelated Eyes observers or undo accepted work.

## Data and events

GoalStore owns a leased `xdog.goal.registry.v2` registry with owner binding, Goal
revision history and immutable reminder outbox. Task state has ONE authority:
`<workspace>/.xdog/goals/<goal-id>/tasks/<task-id>.json`. Workers update their assigned
files; SDK mutations use expected versions and atomic no-follow writes. The local
`python -m xdog.skills.goal.task_cli ... list|update` helper shares this CAS protocol
under the caller's existing tool permissions. The plan is `plan.md`; errors are not empty results.

Before every continuation, the main Agent compares actual results with its previous
report and calls `goal(action="report", report={...})` BEFORE doing more work.
`progress.md` is the sole report authority, with human-readable and structured
history in one atomic, version-checked Markdown file. Baseline/advanced/unchanged/
regressed are Agent judgments, not script verdicts. With repeated no progress and
no viable new approach, the Agent should report blockers and pause. No fixed round
threshold or automatic progress judge exists.

`/goal status` and the model status default to counts, bounded previews, latest
report and distinct execution/observation/delivery facts. Use `tasks` for filtered
or single-task reads, and `plan` without content for reading. Snapshot byte pages
retain query and content hashes. SDK `status(detail=True)` keeps full history reads.

Generic host settled/available events trigger a check. When continuation is
appropriate, the runtime sends a stable-ID automatic message through ACP enqueue
to the same main Agent and original Inbox/controller. ACP delivers; the skill
instructs; the main Agent decides. Both settled and task-file signals enter one
durable coalescer. An accepted pending/running reminder prevents another send;
new changes remain dirty until consumed. Recovery inspects original receipts, not
idle time, and known-not-sent retries retain original ID/content. New waits support only
user or tasks; historical host waits stay readable and require explicit user conversion.
A user wait, historical host wait or user pause cannot be washed into an automatic
task wait and resumed. Execution/delivery failures are separate durable runtime
facts, never automatic Goal state changes. Interrupted/unknown effects are not
replayed; unresolved delivery uncertainty remains fenced even across pause/resume. No fixed polling loop, hidden tool approval or second Agent is created.

The runtime generates `eyes.json` and uses the installed Eyes CLI to own a daemon
registration observing only this Goal's task JSON directory. It imports no Eyes
SDK, runtime, loader, or daemon protocol. Events arrive through an owned
`events --follow` JSONL process; only a durable Goal commit permits a separate CLI ACK. Configuration/state
files are excluded. Pausing stops both proactive continuation and this owned
registration; only explicit resume restores them and reconciles changed task
files. Pause does not cancel accepted inputs, tools, children or other observers.
Clear stops only the owned registration. Host retirement/close only detaches its
CLI stream (`suspend=False`), allowing daemon-owned offline observation to remain;
Goal business stops use `suspend=True`. Missing Eyes or
an unavailable actual owner endpoint is reported, never silently installed or
retargeted. Goal depends directly only on Agent/stdlib; optional Eyes operations
cross the real CLI process boundary, not a Python SDK.

Old `journal.sqlite3` and `goals.json` stay untouched, with their locations reported
in status/history; no old worker/reviewer is restored. They are not silently
converted into a new current Goal.

The optional packaged `goal_ui` worker remains readonly and exposes `/goals` via
explicit global extension selection. Default `/goal` commands/model tools use the
native installed capability, not this display worker, so a failed UI cannot own
the business lifetime. Native `/goal` explicitly overlaps only its skill command;
the skill itself remains discoverable/loadable. Builtins and other extension
owners cannot be shadowed.

See `docs/goal-host-faults.md` for the current no-host-wait/fault/receipt contract,
`docs/goal-optimization-design.md`, `docs/goal-optimization-requirements.json`,
`docs/goal-simple-design.md`, `docs/goal-simple-requirements.json` and the current
verification record. Earlier Goal kernel/adapter/reviewer records are historical.
