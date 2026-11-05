# Eyes observation contracts and pushed events

## Goal and ownership

Sources acquire **content**, detectors decide **changes/conditions**, routes choose
handlers, handlers perform delivery. No source selects a recipient, calls a model,
or makes a Goal/business verdict. No detector performs network/file IO.

This change completes the third batch alongside Source/Detector composition. It
uses the existing independent daemon, CLI, checkpoint and immutable delivery
outbox; there is no additional service, public Python client, global topic bus,
host dependency, or automatic daemon replacement.

## Data contracts

A source can expose several views of one acquisition. `contracts.py` declares the
finite built-in contracts; the loader validates required data semantics, not an
allowlist of source/detector product-name pairs.

| View | Producers | Consumers |
| --- | --- | --- |
| Records (stable key, complete bounded JSON, evidence URL/time) | file, HTTP, Feed, GitHub, scheduler, event | record.change, value.condition, event.received |
| Files (path, digest, size) | file | file.change |
| Occurrences (schedule identity/time) | scheduler | scheduler.due |
| Acquisition health | file, HTTP, Feed, GitHub | availability |

File records default to `{path,digest,size}`. `format: "json"` exposes the parsed
file JSON as each record's value, keyed by relative path, from the **same no-follow
read** as its digest. JSON parsing is bounded, off-loop and never fetches resources.
Extracted JSON has an aggregate one MiB bound. `file.change` still observes byte
changes, even if parsed values are equivalent.

Scheduler records expose occurrence fields; event records expose submitted JSON.
These are streams, not collections. No event does not imply failure or removal.
An idle pushed source is not an acquisition-health probe. Feed/GitHub windows do
not prove deletion either. `removals: true` requires a complete collection (file or
HTTP); incompatible combinations fail validation before registration.

`compare_fields` belongs **only to record.change**, at most 32 unique JSON Pointers.
No cached source-level comparison exists. Each detector maintains an independent
baseline and compares its own selected fields. Complete latest evidence is retained
when only ignored fields change. Missing projected fields fail the acquisition;
all participating record detectors stage their updates before publishing them and
the source cursor is retained on failure. A configured impossible projection needs
configuration repair/new registration, not a model retry loop.

Snapshot sources establish an initial baseline. Streams process the first input:
record.change emits created on its first key and updated on subsequent changed
values; value.condition emits a transition from false on its first matching input.
`event.received` emits each newly acquired record without requiring a changed value.
It works with every record source, not only event. Use record.change rather than
received when polling unchanged content must not trigger notifications. Conditional HTTP 304 is not a
new record acquisition. Routes should fan out an event rather than duplicating
identical detectors. Record detectors keep bounded state; streams with entity
comparisons should configure a stable entity key and sufficient `max_records`.

## Event registration and publication

See `packages/eyes/src/xdog/eyes/examples/job-events.json`. Register the complete
JSON normally, then publish to its exact event source:

```bash
xdog-eyes --owner my-project start /absolute/path/job-events.json
xdog-eyes --owner my-project publish \
  --observer OBSERVER_ID --source jobs \
  --event-id validation-123 --event-file /absolute/path/event.json
```

For that packaged example, event.json can contain:

```json
{"job_id":"recipe-123","status":"failed","error":"Recipe validation rejected the output"}
```

Its source key_pointer is /job_id. A simpler source without key_pointer accepts
any finite JSON object and uses the producer event ID as its record key.
The payload is a finite bounded JSON object. CLI accepts `--event-file -` for
stdin. The `eyes` model tool exposes the same operation as `action: "publish"`,
with owner, observer_id, source_id, event_id and saved absolute event_path. It uses
only the public CLI; no host/group/session adapter is introduced.

An event source optionally selects an entity key through `key_pointer`; otherwise
the stable producer event ID is the record key. Event ID deduplicates publications;
entity key compares successive facts about the same entity. These identities are
not interchangeable. Publish cannot create observers/sources or modify routes,
handlers, destinations, permissions, or business state through its payload.

## Durability, bounds and lifecycle

- Identity is `(registration ID, source ID, producer event ID)`, in the caller's
  existing owner namespace. Owner is management metadata within the same UID,
  **not** authentication.
- Canonical JSON digest makes unchanged retries idempotent, including ACK loss.
  Reusing an accepted ID with different content fails. JSON object order does not
  change identity. ID is at most 256 UTF-8 bytes.
- Admission commits the payload/hash/time before returning `accepted`. No model,
  detector or handler completion is implied. A duplicate receipt reports whether
  the input is still accepted or processed; `status` exposes intake counts.
- Per source: max_bytes defaults to 64 KiB (cap 128 KiB), max_pending_events 128
  (cap 4096), max_pending_bytes 4 MiB (cap 16 MiB), max_events 10000 (cap 100000).
  A full queue rejects a **new** publication without accepting it. Retry the same
  ID later. Output backpressure stops absorbing more pushed inputs for that source.
- Accepted IDs/hashes are never evicted. `max_events` bounds the lifetime ledger,
  including processed inputs. At that bound, explicitly create a new registration (new definition name or CLI --key);
  there is no hidden TTL that converts retries into duplicate effects.
- Executor consumes input in persisted sequence order per source. Detector state,
  source position, immutable pending deliveries and payload retirement commit in
  **one existing journal transaction**, before any handler sees the output.
  Processed payloads are removed from intake; hashes/receipt identities remain.
- Failure before that commit leaves the input available after recovery. Failure
  after commit recovers the original output identities, never recomputes/resends
  unknown external effects. Existing handler outcome-unknown quarantine applies.
- Pause rejects new IDs and retains accepted inputs. Duplicate receipts remain
  readable during pause/cancel. Resume processes retained inputs. Cancel stops the
  observer, retains receipts and does not undo downstream accepted work. No implicit
  resume or automatic replay of interrupted external effects is introduced.
- Closing a publish CLI wait cannot undo accepted input. Daemon restart with
  `recover: true` restores the existing executor; `--no-recover` needs explicit
  resume. Neither daemon startup nor publish starts a second Agent loop.

These are distinct facts: **input accepted → detector processed → handler delivered
→ target Agent executed → user notified**. Eyes only owns the first three.

## Compatibility and scope

New behavior negotiates `observation-contracts-v1` / `event-sources-v1` before
mutating an older live daemon. It is not stopped/replaced automatically. Existing
file/scheduler/first-batch normalized defaults retain their representation.
Previously ineffective removals=true on remote windows now fails validation explicitly.
Unreleased second-batch source-level compare_fields is removed, not supported as
an alias; move it explicitly to each intended detector. No old journal versions
are migrated. The current journal gains an additive private source_events table.

No third-party source plugin system, shell source, public webhook, notification
policy, automatic repair, business workflow scheduler, or rule language is added.
Applications may optionally invoke the public CLI; none must depend on Eyes.

## Acceptance

1. One acquisition feeds independent field/condition detectors; ignored metadata
   remains in evidence, and missing comparison fields do not advance its cursor.
2. Record detectors operate across file, HTTP/Feed/GitHub, scheduler and event;
   invalid semantic combinations fail before registration.
3. Event publication validates owner/source/finite bounds; unchanged retries are
   duplicates, conflicting IDs fail, capacity rejects without acceptance.
4. Checkpoint and input retirement are atomic; pause/resume and process restart
   retain accepted input and output identities. Unknown delivery is never replayed.
5. Public CLI and model tool publish through the same independent daemon; event
   evidence follows ordinary routes into ACP without granting authority.
6. Existing file/scheduler/remote/Goal integrations, packaging, strict typing and
   workflow interpreter/codegen regressions continue to pass.

## Verification record — 2026-10-29

- `uv run python scripts/test.py`: all nine configured test paths exit 0,
  **10,550 passed / 34 skipped**.
- Final Eyes suite, including later contract/real-file/feature-negotiation cases:
  `uv run pytest -c pyproject.toml packages/eyes/tests -q -n 0 --timeout=120
  --timeout-method=thread -o cache_dir=.pytest_cache/eyes-third-final`:
  **535 passed**. This overlaps the workspace run; totals must not be added.
- Full `uv run ruff check packages` and strict mypy for all nine packages pass
  (**665 source files**). `git diff --check` passes.
- Public Flow example CLI validation/execution: **17 passed**. Workspace Flow tests
  include the existing interpreter/codegen regression coverage.
- Final wheel/sdist build succeeds. New contract/config/intake/event modules,
  packaged example and skill were byte-checked against source in both artifacts;
  no XDOG host/runtime dependency was added to Eyes.
- Public CLI validate succeeds for event, HTML, Feed and Releases examples.
- Real private Unix/SQLite tests cover queue/ledger/byte capacity, conflicting IDs,
  owner/source fences, missing keys, atomic rollback, pause/resume, original output
  identity across restart, and SIGKILL after publication ACK with queued input.
- The actual thin model tool invokes the public CLI and routes pushed evidence to
  a test ACP peer. Payload instruction/session fields cannot override its configured
  instruction or target. A local event traverses daemon → ordinary ACP → original
  Coding main Agent using a deterministic model stub; ask permissions remain intact,
  and external content never enters its system prompt. No host adapter was changed.

All daemons, peers and endpoints used by these tests are explicitly isolated.
The shared user daemon/gateway was not restarted, and no user observations were
created. No real model, user messaging channel, production credential or long-term
remote service was used. No commit, push, tag or release is implied by these checks.
