# Eyes transport recovery

## Failure and boundary

The old daemon cancelled an observation and stored `paused` after a recoverable
ACP disconnect. Restart recovery correctly ignored paused registrations, but
this combined a temporary transport failure with an explicit user control and
left GitHub/file observations stopped indefinitely.

The fix belongs entirely to Eyes. Claw/Coding continue to accept ordinary ACP
input; there is no host-specific reconnect service, second model loop or extra
observer journal.

## State and recovery

- `paused` is written only by explicit pause. A recoverable handler fault leaves
  the durable registration `running` and presents `degraded` with per-handler
  error and `retry_after_seconds`.
- Reconnect uses bounded exponential backoff from 0.25 to 30 seconds. Health work
  uses owned async tasks on the existing daemon loop. IO never holds the control
  admission lock. A slow/offline target does not block unrelated registrations.
- A live observation keeps collecting into its original bounded pending/backlog
  state. The original delivery ID/content remains immutable after an attempt.
  Overflow is explicit; evidence is not silently discarded to keep polling.
- Cold activation keeps its existing handler-readiness requirement, including
  scheduler startup. It retries an absent receiver without changing the saved
  baseline or requiring a user resume. Once connected, checkpoint restoration
  detects changes made during downtime.
- Healthy reconnect does not re-register, rebaseline, release a user pause,
  navigate a host session, request execution permission or start an Agent.

## Actual binding and effect safety

`HandlerSnapshot.binding` is explicit stable endpoint identity, separate from
mutable diagnostic `details`. Its data is copied before publication. A handler's
actual trusted implementation declares `retry_safe` (default false); JSON labels
cannot supply it. The daemon maps that declaration to actual routes, keeping the
executor unaware of ACP or host policy.

ACP enqueue pins the receiver's Inbox namespace/branch and resolved directory.
Reconnect revalidates that original scope and the durable handler binding before
allowing delivery. ACK loss may recover with the exact original message ID and
payload, relying on the receiver's durable deduplication rather than assuming a
send did not happen. The new `retry` delivery result explicitly makes no
proven-not-sent or execution-success claim. Safe in-flight checkpoints can retry
that same identity after daemon restart.

Ordinary prompt outcomes, invalid receipts, explicit receiver refusals, binding
changes, terminal deliveries and overflow are not automatically replayed. They
remain inspectable as `needs-review`, not an invented user pause. An enqueue
label with a handler that has not declared safety still cannot replay unknown
work. Events-mode publication retains its existing durable deduplication rules.

Pause, cancel and shutdown retire the actual original active owner, cancel/join
its health/reconnect work and settle connection cleanup. Binding IO checks the
same live owner at its real storage entry. A late check cannot write another
owner's binding, overwrite pause/cancel or rearm collection.

## Verification

`test_daemon_reconnect.py` uses real private Unix ACP ingress and current durable
inboxes, without constructing a model/AgentSession. Coverage includes collection
while the target is down, immutable backlog, automatic same-receiver recovery,
changed Inbox rejection, daemon restart while the receiver is offline, same-ID
safe in-flight recovery, and explicit pause/cancel during reconnect. Generic
handler tests cover finite backoff and stable binding independent of diagnostic
counters. Existing prompt tests retain the no-replay effect assertion.

Verification completed:

- Complete Eyes suite: **559 passed**.
- Canonical full workspace command, `TMPDIR=/var/tmp uv run python scripts/test.py`:
  **10,861 passed, 34 skipped**, all nine test paths passed in 652.99 seconds.
- Ruff, strict mypy across all nine packages (**688 source files**) and
  `git diff --check` passed.
- Eyes wheel/sdist built and passed Twine; an offline isolated wheel environment
  imported Eyes without Agent/Claw/Coding and started/stopped its own private
  zero-registration daemon through the public CLI.

An earlier global four-worker run reported two failures and one setup error in
cold-start timing tests (10,858 passed). Those three cases passed unchanged in a
separate run (4.70 seconds); the prescribed balanced package runner then passed
the entire suite. No startup timeout or assertion was changed for this fix.
These are local results, not a GitHub CI result.

## Initial production verification (before source coverage recovery)

The known `claw-main-github` / `xdog-feedback` registration was explicitly resumed
through the normal public CLI after restarting Eyes. Its ID, source checkpoint
and original receiver binding were retained; no journal was edited and no new
baseline was established. ACP is ready again. Its three GitHub sources separately
report an observation gap since October 30; the registration therefore remains
`degraded`. Reconnecting transport does not erase that gap or establish that all
missed activity was recovered. No blanket automatic release of previously paused
registrations was introduced.


## Source coverage recovery follow-up

The GitHub source previously rejected a poll before network IO whenever its old
success watermark was outside lookback. Since it never advanced that watermark,
this error was permanent even with a healthy ACP connection. That gate is now
removed: acquisition uses the configured finite window and the original detector
checkpoint, including known active runs. It is not a new initial baseline.

Successful acquisition persists `coverage_gap` with a count and first/latest
interruption evidence (reason, previous success, resumed time and window start).
The first gap remains visible through later successes, failed requests and restart.
Status exposes `coverage_warnings` separately from `source_errors`; historical
coverage alone neither degrades current healthy acquisition nor triggers another
acquisition-health failure after restart. Pagination/capacity/parse failures still
retain the old success watermark, and recovery never claims a complete backfill.

Regression coverage uses a real private daemon/control socket/journal and a finite
GitHub HTTP fixture. It restarts after simulated multi-day downtime, verifies an
existing record update and a genuinely new record against the original baseline,
checks unchanged-record suppression, then restarts again to verify persistent
coverage evidence without duplicate events or false source errors. Unit tests
cover Issues/comments/Actions, clock regression, repeated gaps, initial/recovery
failures, capacity/parse/HTTP errors, normal-window boundaries and old active runs.

Follow-up local verification (November 5, 2026): all **573 Eyes tests passed**
(`TMPDIR=/var/tmp uv run pytest packages/eyes/tests -q`, 176.31 seconds). Repository
Ruff and strict mypy across all nine packages also passed (688 source files).
Eyes wheel/sdist build and Twine checks passed. This follow-up did not rerun the
entire workspace test suite; the prior full-workspace result above predates the
source coverage fix.


Production follow-up: restarted only the shared Eyes daemon through the public
CLI (with the user's proxy environment). The original registration and next-poll
schedule were retained, with no pause/resume, re-registration or journal edits.
On **November 5, 2026 at 09:07:47 Asia/Shanghai**, Issues, comments and Actions all
completed fresh HTTP 200 acquisition: registration/source state `watching`, no
source errors, ACP handler `ready`. Each source retains `gap=true` with the exact
prior October 30 success watermark in its first coverage entry. This verifies
current collection recovery, not a complete historical backfill, model execution
or user-channel notification delivery.
