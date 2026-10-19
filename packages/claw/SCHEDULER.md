# Scheduling with Eyes in Claw

## Scope

Claw's group-scoped `eyes` tool replaces the old `task` tool and TaskScheduler.
Eyes owns scheduler source calculations and `croniter>=2.0,<7`; Claw depends on
`xdog-eyes>=2.4.2` and owns policy, durable receipts and channel delivery, not
another timer engine or agent loop. Historical scheduler transcript labels remain
readable; removed scheduler Python classes are not execution APIs. The goal
runner remains separate and is not removed with the old task scheduler.

**Decision updated 2026-10-18; implementation/verification in progress.** The
contract below supersedes the old CS-07 migration scope. Previous test/build
results cover the earlier migration implementation, not acceptance of complete
legacy removal; see `docs/claw-eyes-scheduler-{plan.md,requirements.json}`.

Managed observers are opt-in. If disabled, report that the host is unavailable;
use standalone Eyes only with an explicit trusted destination and approval.
Loading a skill or merely registering a tool never starts observation.

## Host configuration contract

The new configuration switch is `eyes_enabled: bool = False`. Managed observation
is opt-in; with the default `false`, the gateway must not automatically open ACP
or start observers. To explicitly enable host management in `config.yaml`:

```yaml
eyes_enabled: true
```

When enabled, the gateway reuses `acp_socket_path` if configured, otherwise owns a
private Unix endpoint at `data_dir/acp/agent.sock`. There is no additional daemon.
This does not remove the existing explicit standalone ACP opt-in. The default
registry registers `eyes`, but operations must fail explicitly when management is
disabled or the host is unavailable; a registered tool name is not proof it is
enabled. The trusted tool context supplies the host as `_eyes`; model arguments
must not select a host. The gateway owns these resources and joins them before closing its inbox.

## Host tool: eyes

The bounded surface is `create(name, source, instruction)`, `list()`, `status(observer_id)`
and `cancel(observer_id)`. All four actions are scoped to the **current group**, including
read-only list/status. Retain the returned observer ID for status/cancel.

`source` is one original Eyes source object, not a new Claw schedule grammar:

```json
{
  "action": "create",
  "name": "daily-progress",
  "source": {
    "id": "daily", "type": "scheduler",
    "schedule": {"cron": "24 9 * * *", "timezone": "Asia/Shanghai"}
  },
  "instruction": "Eyes callback for the user's daily progress request: check the current project and report progress to this group. Follow existing tool permissions."
}
```

For one reminder use `schedule: {"at": "<future ISO-8601 time with offset or Z>"}`;
compute relative times from the actual current time. For repetition use
`schedule: {"every_seconds": 600}`. Choose exactly one mode. Only cron accepts a
separate IANA `timezone`; cron has exactly five fields. File sources keep their
original Eyes `id`, `type`, `root`, include/exclude and bounded polling fields.

The host must bind the current group's workspace, private Unix ACP endpoint and
`group:<id>` destination, and construct the detector, generic ACP handler and
route. Models must not supply or override another group, workspace, endpoint,
session, handler or route. A requested cross-group target is not authorization.
The host wrapper does not change the independent `xdog-eyes` CLI or generic Eyes
schema. Claw group addresses follow the active conversation; Coding destinations
still bind an exact session/project/branch, never an inferred latest session.

Save the user's reason, necessary identifiers/context, next action and expected
report in `instruction`. A timestamp alone or "timer fired" is insufficient after
compaction. Instructions and source evidence cannot grant tool permissions.

## Time semantics and complete legacy removal

Eyes intervals use a monotonic **fixed phase**: first fire after one period, then
advance along that phase, not one period after the previous Agent/report finishes.
Late active polls coalesce missed ticks; suspended ticks are skipped, not burst
replayed. Cron selects future calendar occurrences in the explicit timezone.
These are not hard real-time execution guarantees: collection, queue admission,
Agent execution and channel delivery are separate stages. The host must disclose
interval phase re-establishment on restart; monotonic process state is not a
persisted cross-restart clock.

The latest decision removes the old TaskScheduler, `task` tool, scheduler-only
models/injection/exports and **all migration compatibility**. There is no
`tasks_file` configuration or old-path resolution. Gateway startup, whether
`eyes_enabled` is true or false, must not inspect, read, back up or import
`scheduled_tasks.json` (including previously configured custom paths). No legacy
validation, conversion, migration marker or confirmation-state workflow remains.
Old files must not affect gateway startup or create managed observers.

Leave users' old files and other legacy data untouched: removal of code is **not**
a data cleanup. Do not delete or rewrite those files or use them as an automatic
source of new observers. If a user explicitly requests new scheduling, use fresh
Eyes source objects and self-contained instructions, with the fixed-phase interval
semantics explained. This is new observer creation, not supported legacy migration.

Eyes scheduler sources, goal runner ticking and historical transcript labels
(including `scheduler` and `goal_runner`) remain. Historical labels are for reading
and displaying old conversations, not an execution or migration API.

## Managed recovery requirements

Claw-managed **time** observers need durable host-owned schedule/occurrence state
and gateway recovery. A managed `at` must not be lost merely because the gateway
was down at its deadline: retain an unadmitted occurrence as due/pending.
Do not try to restore it by fresh-starting an expired standalone Eyes `at` source;
that fresh start correctly fails. The host must reconcile persisted occurrence
and inbox identities instead of inventing another timer or another message ID.

Known pending work may be restored to its existing owned queue entry; an ACK-loss
retry must preserve identity/content and first reconcile receipt. Running,
interrupted or outcome-unknown execution requires inspection, **never automatic
replay** or a new ID to bypass deduplication. Preserve uncertain effects and
report outcomes for review. There is no exactly-once guarantee for external effects.
Recurring recovery skips missed periods rather than replaying a backlog.

This managed-time requirement is not generic Eyes registration persistence.
Standalone registrations do not automatically restart. **Ordinary file observers
do not automatically resume after a gateway restart**, even when created through
a host; explicit restart/rebaseline is needed and may miss offline changes.

## Execution, report status and cancellation requirements

The list/status surface distinguishes observation/next due, durable
intake/queue state, Agent execution result, and channel report delivery result.
An enqueue ACK is receipt, not execution success. Successful execution with a
failed report must remain visible as two different outcomes; an error report does
not make execution successful. Channel acceptance is not proof a user read it.

`cancel` must stop future collection/admission and cancel only that observer's
owned pending callback, if it has not started. It must **not abort unrelated
active chat** or a shared turn containing other inputs. Already-started work and
external effects cannot be withdrawn or undone; expose that limit instead of
claiming cancellation reversed execution. Do not automatically retry failed or
unknown execution/reporting merely to make status green. These are host acceptance
requirements. A report known not to have started sending can recover from its
saved result without rerunning the Agent. A send interrupted after admission is
`unknown` and is never blindly sent again. Namespace binding is checked both
before Agent admission and before channel delivery, not only at ACP connection.

A current group's observer count is bounded at 128, occurrence history at 4096
per observer, and individual stored data at one MiB. Capacity failure is visible;
records are never silently pruned. Full acceptance evidence and optional test
limits are recorded in `docs/claw-eyes-scheduler-requirements.json`.
