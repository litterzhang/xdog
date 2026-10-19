# Claw ordered durable history commit — source milestone

Date: 2026-10-19. This records the owned-transcript-io plan's Task2 implementation
and actual proof scope, **not** the entire47-item acceptance or rebuilt artifacts.
Startup/status/MCP/image/other application IO audit, current artifacts and the
single whole-branch review remain mandatory.

## One transaction, one original owner

The existing Session captures a frozen `HistoryCommit`: original metadata/group/
store/branch, JSON message data, original logical cursor, channel/source/usage and
whether this completed turn increments metadata. No Agent/Session/Runtime or tool
callback is sent to a worker. Worker code checks original current binding and
native active-index identity, then compares the semantic original history prefix,
not only its length. The existing materialization retains unchanged disk-only
compaction/custom metadata; independent messages and tool/result pairs stay
independent rows, with their source/channel/usage data.

Under the existing TranscriptStore lock and process ownership, the existing
NavigationJournal protects the actual JSONL file, catalog and active index.
Actual history data is flushed/fsynced/replaced, metadata is flushed/replaced and
namespace/commit marker settlement completes before store cache publication.
Only the original loop then adopts the actual cursor/SessionMeta. Synchronous
fixture/composition persistence uses the same transaction, not a second append
engine. Production ordinary paths no longer call append_turn/increment_turn or
synchronous Session persistence.

Actual reads acquire the same store lock: another worker cannot expose pre-marker
history replacements as committed data. This is transaction isolation, not a claim
that every existing async status caller already offloads its IO; that application
callsite work remains the explicit Task3 gate.

## Failure and cancellation

- Real IO errors undo all three managed files through the existing journal;
  cached metadata remains the original snapshot. If rollback fails, the original
  store is fenced recovery-required and keeps the complete journal/backups.
- A failed batch marks the original conversation uncertain. Generic exception/
  cancel fallback cannot append/retry that batch. Later execution is refused
  before model admission; automatic daily reset cannot silently wash away the
  fault. Readonly inspection/explicit recovery and other independent capability
  lifetimes are not turned into implicit Goal/Eyes cancellation policy.
- Repeated caller cancellation joins the actual commit. If it really committed,
  its cursor/metadata are adopted once before cancellation propagates; Core can
  then record interrupted execution without pretending durable history vanished.
- Validated prior **committed** marker cleanup is reconciled before a subsequent
  history batch. Prepared/unknown transactions still require reopen; no automatic
  execution/tool replay or repair/conversion of unknown user formats is introduced.
- On real process death, original interrupted attempts are never automatically
  rerun, whether recovery restores the old files or retains a committed history.

## Actual tests, not fake persistence

`packages/claw/tests/test_owned_transcript_commit.py` currently contains11 cases:

1. Observe the real fsynced inode via `/proc/self/fd`, including a pre-rename
   history image. Both actual user/assistant JSONL roles and sentinel payloads
   must be present. A metadata summary or commit marker cannot satisfy it. The
   original pipeline returned succeeded with no such data sync (behavioral RED).
2. A real bash write happens once, then metadata replacement fails. All managed
   bytes and cache/cursor/counter roll back; the real external effect stays, and
   a later message cannot rerun the model/tool even when daily reset is due.
3. Hold an actual post-index-write/pre-marker commit. Original receipt remains
   running; cache/cursor/counter remain old; a real reader waits. Repeated cancel
   retains the actual group owner. Release commits once, cursor/counter adopt,
   receipt becomes interrupted with the original attempt, history reopens intact.
4. Preserve exact hidden summary/compaction/custom metadata, channels and usage;
   reject an equal-length but different semantic prefix and a repeated old batch.
5. Real rollback-storage failure leaves journal/backups and blocks store access.
   Real reopen restores exact original bytes and stable inbox namespace/failed
   attempt, without another model call.
6. A real committed-marker unlink fault leaves the first turn successful; the
   next batch cleans known committed bookkeeping without duplication or false
   rollback. The initial test targeted a backup unlink even though cleanup removes
   the marker first; that assumption was corrected before accepting its RED.
7–11. A separate real interpreter runs an actual Agent/bash tool and exits the
   whole process at history/catalog/index replacement or immediately before/after
   the commit marker. Before-marker recovery restores exact old files; after-marker
   recovery retains all four actual message/tool/result rows and metadata. A fresh
   application sees the original interrupted receipt/attempt and never invokes
   its forbidden model transport. Exactly two original scripted model requests
   and one actual bash effect remain. No fake backend/store/close is substituted.

Focused11 pass5.53s; related owned preparation/navigation/access recovery51
pass9.72s. Current final unfiltered nine paths: **6914 passed,34 skipped**,186.63s,
Claw1088/121.59s (`claw-owned-transcript-final-root9.log`). Strict all9 mypy597
files, Ruff packages, Flow17 and lock/diff pass. Actual integration/compaction/
SDK/Goal/observer/native-focused55 pass32.91s; corrected Eyes error+receipt
conditions plus actual history/output cases30 pass15.11s.

The initial whole-nine run had two existing Eyes tests that polled only channel
failure then immediately asserted execution succeeded. Streaming may settle
before history/receipt (by design); the tests now wait for both original delivery
condition and terminal execution, retain the final succeeded/one-model assertions,
and keep the original deadline. No runtime streaming was delayed to fit the old
assumption, and that failed run is not counted as the final gate.

Command `uv run python scripts/test.py --jobs 2`, with short
`TMPDIR=/data/t.Vy5SaQ`, `UV_CACHE_DIR=/data/t.Vy5SaQ/uv-cache`,
`TMUX_TMPDIR=/data/t.Vy5SaQ`. Earlier default4 timing/full-root constraints remain
documented; no package/test/physical terminal/socket coverage was excluded. All real IO boundaries
are delegated; only model/network boundaries and explicit storage faults are
controlled. No existing deadline/socket/terminal test is removed or relaxed.

## Whole-turn descriptor probe and remaining gates

The original whole-turn probe was RED with12/12 fsync on-owner; preparation moved
it to two on-owner writes. With this transaction it now passes with **23 actual
fsync calls, zero on the Agent/event-loop owner**:
`/data/t.Vy5SaQ/claw-owned-transcript-whole-probe-green.log`.
That covers this actual ordinary path, not all application startup/status/image/
MCP or custom-context/compaction cancellation paths. The expanded fsync count is
real journal/history/namespace durability, not deleted work or a new business
quota. No hardware power-loss/exactly-once tool guarantee is claimed.

Task3 must finish the remaining async application/recovery audit. Afterwards
return to the parent47-row audit and one whole-branch review, then build current
runtime/resource wheels+sdists and run actual isolated both-host/Goal-only/ACP/
native/output consumers. Earlier cold snapshots predate these runtime changes
and cannot substitute. Overall goal remains active; no push, merge, publication,
paid provider or existing user data/service mutation is authorized.
