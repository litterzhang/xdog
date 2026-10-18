# Eyes scheduler source

A scheduler is an observation source, not another agent scheduler or event
handler. It uses the existing version-2 definition and data path:

```text
scheduler source → scheduler.due detector → scheduler.due event
                  → EventRouter → configured EventHandler (including ACP)
```

Eyes does not import coding/Claw/Flow, run tools, create an agent loop, invoke
system cron or install a daemon. Explicitly start/stop the observer as before.

## Three mutually exclusive schedules

Exactly one mode is required inside a scheduler source's `schedule` object.

### One-off

```json
{"id": "reminder", "type": "scheduler", "schedule": {"at": "2030-01-01T09:00:00+08:00"}}
```

`at` is an ISO-8601 datetime with an explicit UTC offset or `Z`. It emits at most
one occurrence in that registration. Validation is time-independent, but a fresh
**start** rejects a past/equal deadline before constructing any handler. A one-off
admitted on time retains its absolute deadline while receivers connect; delivery
may be late, and is never promised to be real-time. Repeating start on an already
active identical definition returns the existing registration, not another event.

### Fixed interval

```json
{"id": "heartbeat", "type": "scheduler", "schedule": {"every_seconds": 300}}
```

The interval is a finite number, at least `0.001` seconds; booleans are rejected.
Its first period starts **after all handlers connect**. It never fires immediately
just because startup/receiver negotiation was slow. Intervals use a monotonic
clock, so wall-clock adjustments do not accelerate or reset them. Observations
still run at Eyes' approximately 50-ms executor polling cadence: this is not a
high-frequency/real-time timer. A late poll emits only the latest elapsed tick,
not every missed period; the original interval phase is retained.

### Cron

```json
{"id": "daily", "type": "scheduler", "schedule": {"cron": "0 9 * * *", "timezone": "Asia/Shanghai"}}
```

Cron uses five fields: minute, hour, day of month, month, day of week. An explicit
IANA timezone is required; the host's implicit local timezone is never used.
Numeric fields, standard three-letter month/weekday names, lists, ranges, steps
and wildcards are supported. Seconds/year fields, macros, random/hash expressions
and other nonstandard extensions are rejected. The separate `timezone` field is
only valid for cron; a one-off already contains its own offset.

The directly declared `croniter>=2.0,<7` dependency calculates timezone-aware
occurrences, with bounded (eight-year) matching searches and the normal cron
OR behavior for restricted day-of-month/day-of-week fields. No-match expressions
and invalid/missing timezones fail validation, rather than falling back to an
interval. Linux timezone data must contain the chosen IANA zone.

The first cron deadline is strictly in the future after receiver initialization.
A late active poll emits only the latest matching occurrence. Next deadlines
strictly advance in UTC, so a clock rollback does not rearm an already observed
instant. Local DST behavior follows the timezone-aware cron calculation; two
local clock labels with different UTC offsets are distinct instants.

## Complete definition

Copy the shipped `xdog/eyes/examples/scheduled-review.json` and replace its
endpoint and exact ACP destination. For Claw, an explicit `group:main` address
is valid only when that group and workspace match its gateway configuration.
For coding, use its exact persisted session ID. Eyes does not discover either
harness' private registry.

```json
{
  "version": 2,
  "name": "scheduled-review",
  "sources": [{"id": "daily", "type": "scheduler", "schedule": {
    "cron": "0 9 * * *", "timezone": "Asia/Shanghai"
  }}],
  "detectors": [{"id": "due", "type": "scheduler.due", "source": "daily"}],
  "handlers": [{
    "id": "review", "type": "acp", "delivery": "enqueue",
    "connection": {"transport": "unix", "path": "/tmp/example-private/acp.sock"},
    "session": "EXPLICIT_SESSION_ID",
    "instruction": "Review the project and report findings. Follow target tool permissions."
  }],
  "routes": [{"id": "daily-review", "detector": "due", "handler": "review", "event_types": ["scheduler.due"]}]
}
```

```bash
xdog-eyes validate eyes.json
xdog-eyes start eyes.json
xdog-eyes status REGISTRATION_ID
xdog-eyes stop REGISTRATION_ID
```

File and scheduler sources can coexist. `file.change` must reference a file
source, `scheduler.due` must reference a scheduler source, and each route's event
types must match its detector. A scheduler has no file root/include/exclude fields.
A source can feed several detectors/routes; each occurrence is collected once,
then projected/routed independently.

## Evidence, pause and recovery

Each event retains its source/detector IDs and `type: "scheduler.due"`. Its
structured evidence contains `schedule_id`, a per-occurrence `occurrence_id`,
`scheduled_at`, `observed_at` and `timezone`. Times are UTC Unix seconds;
`timezone` is the cron IANA zone or `UTC` for normalized one-off/interval evidence.
There are no fabricated file paths, digests or before/after file states.

The existing router and handler delivery limits, backoff, stable retry identity,
uncertain-outcome pause, explicit reconnect and destination Inbox permissions
remain in effect. An accepted schedule does not approve any agent tool use.
An enqueue ACK means durable intake, not agent completion. Busy routes may batch
several distinct observed events into one bounded delivery, just as for files;
this does not promise one model call per timer tick.

While the observer is health-suspended or delivery-paused, scheduler cursors skip
elapsed deadlines. Resume does not emit a catch-up burst. A one-off elapsed during
that suspension is completed without delivery. Already queued deliveries retain
their original identities and existing resume/unknown-outcome rules; skipping new
occurrences never silently resends an uncertain prior ACP prompt. A delivery is
bounded at 256 distinct file paths plus scheduled occurrences and the existing
encoded-byte limit. Overflow pauses visibly, rather than growing without bound.

A consumed/skipped one-off is shown as a `completed` source; its registration
remains manageable until explicit stop. Definitions persist, but registrations
and timer cursors are not restored after process restart. Explicit restart starts
a new interval phase or next future cron deadline. An expired one-off cannot be
rearmed by restarting. No persistence, catch-up or exactly-once side-effect
guarantee beyond these existing Eyes lifecycle rules is introduced.
