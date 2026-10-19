# Claw dynamic group and native reset IO — source checkpoint

Date: 2026-10-19. Continuation of owned-transcript-io Task3; this document is
not final assistant-core acceptance or evidence for current installed artifacts.

## Runtime ownership

- First Weixin input acquires still-unpublished group resources on an owned
  worker. The original event loop publishes the runtime exactly once; concurrent
  inputs for the same group share that publication, then enter the existing
  durable Inbox/GroupController. The factory never constructs an Agent.
- Caller cancellation waits for the actual resource factory, closes late MCP
  and optional memory resources, and never accepts the cancelled input. Shutdown
  fences publication and joins admitted initializers before releasing stores.
  Unsafe dynamic IDs containing path separators, NUL or oversized identities
  are rejected before construction. This is not an additional execution owner.
- Native reset is a tracked control job, so its original reader can answer
  lightweight status while disk IO is held. It uses the same application context
  operation as explicit new-context navigation, with the original group fence.
  Descriptor/history checks and the original pointer are revalidated before IO;
  session disposal stays on the actual owner loop, not a worker.
- Reset preparation cancellation keeps the old session. Cancellation after an
  admitted reset waits for its real commit without replay, then exposes only the
  committed current pointer. Readonly access is inherited in both cases. An
  unknown/missing pointer with known archives cannot authorize a reset.
- Pure child/MCP descriptors fail before optional memory allocation. Actual
  fallible runtime construction precedes acquiring optional search workers, so
  even credential-backend rejection does not orphan a real MemoryManager.

## Evidence

`packages/claw/tests/test_owned_dynamic_group_io.py` uses actual resource
construction, workspace/store IO, Inbox execution, optional memory allocation,
barriers after the real constructor, and original-loop scripted model calls.
`packages/claw/tests/test_owned_reset_io.py` uses real private Unix peers and
metadata writes, plus preparation/commit barriers with repeated caller cancel.
No user service, external network or real provider is used.

- Original first-group/native-reset IO probes: RED3, on-owner actual disk IO.
- Child configuration allocation probe: RED1, real MemoryManager allocated
  before descriptor rejection. Additional credential-backend probe also RED1
  (10 other dynamic cases passed), then moving optional allocation last fixed it.
- Dynamic/startup/context focused suite: 37 passed in4.18s.
- Native reset including both cancellation boundaries: 4 passed in1.49s.

Fresh full-nine/static source results are recorded below after actual terminal
completion; they must not be inferred from these focused cases.

## Remaining work

Subsequent actual memory/image/monitor probes found and fixed additional IO and
shutdown boundaries; see `docs/claw-owned-tool-io-verification.md`. Task3 still
requires optional hybrid-memory/lazy embedding/SQLite acquisition and cached
projection IO inspection. A plain awaited `to_thread` is not proof of actual
cancellation settlement. After those checks and any necessary fixes, run current
all-nine/static gates
and return to the parent47 requirement audit, the single whole-branch review,
and fresh artifacts plus actual isolated consumers. No prior artifact snapshot
is current proof. No push/merge/publish or user data/service mutation is performed.

## Fresh source checkpoint result

All9 unfiltered `uv run python scripts/test.py --jobs 2`: **6961 passed,34
skipped**,190.25s; Claw1135/124.89s. Current strict9 mypy600 source files,
`uv run ruff check packages scripts`, Flow17, `uv lock --check` and diff checks
passed. Logs: `claw-owned-controls-memory-image-root9.log`,
`claw-owned-controls-memory-image-static.log`, `claw-owned-full-scripts-ruff.log`
under `/data/t.Vy5SaQ/`. All original physical terminal/socket tests and deadlines
remain; no timeout/xfail/filter was introduced. The earlier hung/terminated-worker
run remains failed evidence. This is a source checkpoint, not Task3 completion,
final review, current artifacts or whole-goal acceptance.
