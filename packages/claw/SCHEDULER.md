# Built-in task scheduling

The `task` tool uses the **running Orchestrator's TaskScheduler service**. It does
not open a second store or load global configuration. `tasks_file` selects the
store; otherwise it is `data_dir/scheduled_tasks.json`. Standalone tool invocations
without a scheduler and current group context fail explicitly. All tool actions
are scoped to the current group, including list and cancel; `group_id` may be
omitted but cannot target another group.

## Tool inputs

Exactly one schedule is required:

```json
{"action":"schedule","prompt":"Check progress and report back","run_at":"2026-10-13T09:24:00+08:00"}
```

`run_at` is a **future ISO-8601 timestamp with an explicit UTC offset**. It is a
one-off, not an annual cron plus instructions to cancel itself.

```json
{"action":"schedule","prompt":"Report daily progress","cron":"24 9 * * *","timezone":"Asia/Shanghai"}
{"action":"schedule","prompt":"Report progress","interval_seconds":600}
```

Cron is five-field, requires an explicit IANA timezone and the declared `croniter`
dependency. Invalid schedules or missing dependencies fail closed; there is no
60-second substitute. The first cron occurrence is strictly after creation.
Intervals first run after one interval, not immediately on creation.

```json
{"action":"list"}
{"action":"cancel","task_id":"task-example"}
```

## Execution and delivery

- Creation is immediately visible to the current scheduler; no restart is needed.
- The gateway's existing 30-second poll dispatches due tasks. This is **not** a
  hard real-time alarm: execution begins on a subsequent poll and after admission
  to the normal per-group Agent queue/global background concurrency limit.
- Each scheduled input retains its report callback while waiting for the queue.
  It is not steered into an unrelated active turn. A slow task does not block
  scheduler polling or consume global capacity merely while waiting for its group.
- Cancellation prevents future admission, including tasks waiting in the queue.
  Already-started turns and their external effects cannot be undone.
- Successful completion requires a successful Agent turn **and** report delivery
  through a channel that accepts the group. Weixin recipient/transport/application
  errors propagate; no matching channel is a delivery failure. Delivery means the
  channel accepted the send, not that the recipient read it.
- `list` includes `status`, `delivery_status`, `next_run`, `last_run`, and
  `last_error`. `last_run` is updated only on confirmed success. An error notification
  can itself be delivered while execution status remains failed.
- A one-off becomes disabled after an attempt, whether successful or failed.
  Failures are not automatically retried because tools may already have performed
  side effects. Inspect the error before explicitly scheduling replacement work.
- Recurring tasks advance to the next future occurrence after each attempt;
  missed occurrences are coalesced, not replayed in a burst. A failed attempt does
  not overwrite the last successful `last_run`.

## Storage and recovery

The scheduler serializes mutations and atomically replaces a single JSON mapping
from task ID to record. The old tool's `{"tasks":[...]}` envelope has one explicit
migration. Old cron records without a timezone are preserved but disabled with an
error, rather than guessing UTC/local time or executing immediately. Corrupt
stores are rejected, not silently replaced with an empty task list.

A persisted `running` task found at startup becomes disabled/interrupted with
unknown delivery status, requiring review. Migration/recovery changes are persisted.
This is a **single-process service**, not a multi-process shared database. External
file edits are not its live control API: use the task tool/service. There is no
exactly-once guarantee for external side effects; a crash or partial multi-channel
send can leave effects whose outcome requires manual inspection.
