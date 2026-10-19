# Claw application IO audit — status, bootstrap and cancellation source checkpoint

Date: 2026-10-19. This is a Task3 **checkpoint**, not Task3/final47 completion.
The prior preparation/history milestones remain verified separately; no current
cold artifacts or whole-branch review is claimed here.

## Actual source changes

- Native status captures runtime identity/model/path on its real loop and gets
  detached readonly `StatusData` from a history worker. The actual store lock
  makes metadata/history coherent, and the existing compact history/usage codecs
  remain the only projection policy. A narrow readonly metadata lookup port runs
  off-loop; the worker does not mutate Agent/Session/Runtime state. Identity is
  checked again before response publication. No Agent/model is created for status.
- Native ping work is bounded and serialized per connection, outside its reader.
  Ordinary control/receipt/cancel requests remain readable while history IO is
  held. Teardown cancels and joins actual status jobs before application storage
  leases close. IO failure is explicit status-unavailable, not fake empty history.
  A genuinely absent committed current pointer can give a capability-only
  snapshot without waiting on uncommitted first-context creation; after-await
  owner validation still prevents borrowing a later context.
- The application bootstrap acquires still-unpublished model/group/workspace/
  storage resources on a worker and hands them to the original owner loop.
  Actual Agent/consumer/capability execution starts only after that handoff.
  Partial registered resources or a late result after cancellation are joined/
  closed on the real owner, never published as a receiver. Durable current data
  and instruction roots validate before optional search-worker allocation.
- PID read/write cleanup uses owned IO. Saved startup model selection is read
  off-loop. Skill/navigation/image/compaction and related existing off-loop
  work now uses the existing owned-work helper instead of cancellable plain
  `await to_thread`, so an actual worker cannot outlive released group ownership.
  Explicit history-commit and bootstrap Tasks keep their result-adoption paths.
- Existing model/thinking symlink ownership checks now precede absence/fallback:
  a dangling link is rejected rather than silently choosing configured defaults.
  Actual symlink and missing target remain untouched. No format migration,
  namespace change, Goal switch, message approval or business quota is added.

## Observed behavioral tests

The new status/native/auxiliary/startup tests call actual stores, controllers,
Gateways and private Unix peers. Real reads/fsyncs and worker joins are delegated;
only model/network boundaries and precise IO fault/delay boundaries are controlled.

- Status history reads on the actual Agent loop were RED2/0.98s. Off-loop reads
  now preserve original history/session and no extra model/Agent while the same
  reader serves a lightweight control request before release. A failed real
  read returns unavailable and preserves original history. Disconnect plus
  repeated shutdown cancellation holds the actual input-store lease until that
  reader exits; another real opener is denied, then succeeds after settlement.
- Cold inventory's existing responsiveness test caught waiting on a history lock
  even when no committed pointer/history existed. That capability-only case is
  now immediate without inventing an empty dataset for a known owner.
- Real skill/navigation reads returned after caller cancellation and released the
  actual group operation prematurely (RED2/0.73s). Repeated cancellation now waits
  the original worker and performs no later selection mutation or model call.
- Real startup file-open/fsync/PID observations were on-owner (RED0.57s). Startup
  IO now runs off-loop, and a held real resource build cancelled repeatedly must
  settle/close the late resources without publishing a socket, PID or Agent.
- Real saved model/thinking dangling links were treated as defaults (RED2/0.73s).
  Native index/selection regressions now reject them while preserving valid data.

Focused status/native/preparation/receipt/image44 pass9.26s; auxiliary/status/
preparation/compaction26 pass5.94s; current startup/status/auxiliary/index24
pass2.53s; earlier startup/status/native27 pass5.80s. These are source checkpoints,
not the final full suite or broader audit verdict. Current unfiltered all-nine source checkpoint: **6924 passed,34 skipped**,
189.29s; Claw1098/125.55s (`claw-owned-application-root9.log`). Strict all9
mypy599 files, Ruff packages, Flow17, lock/diff pass. Command
`uv run python scripts/test.py --jobs 2`, with the existing short
`TMPDIR=/data/t.Vy5SaQ`, `UV_CACHE_DIR=/data/t.Vy5SaQ/uv-cache`,
`TMUX_TMPDIR=/data/t.Vy5SaQ`. This includes all original socket/terminal tests
and deadlines; it does not prove the explicit remaining audit items below.

## Explicit remaining audit, not silently omitted

Task3 is not done merely because those tests pass or a search finds no plain
`await to_thread` in three runtime files. Still inspect/verify:

1. Native socket directory/bind/mode/PID ordering, optional Weixin account/channel
   startup and cleanup, and dynamic group/resource construction paths. Preserve
   endpoint confidentiality and no publication-before-resource-settlement.
2. Missing authoritative current pointers vs derived historical catalog recovery;
   an archive is never executable authority and unknown state must not be
   initialized over or treated as permission release. Preserve valid shared
   native metadata and explicitly read-only legacy archives.
3. Actual same-group history-commit/status concurrency, remaining image/context/
   compaction and sidecar IO/late failures, and readonly access status callsites.
4. Partial resource-allocation failure paths and all original tool/readonly/MCP/
   required-extension boundaries. No optional display failure becomes a guard
   bypass or a reason to stop unrelated business capability work.

Then finish the original47-item audit, one independent whole-branch review and
current final source/artifact gates. Old wheel snapshots predate these changes.
No push/merge/publish, paid provider or existing user data/service mutation is
performed or authorized by this checkpoint.
