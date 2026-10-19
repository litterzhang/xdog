# Claw memory/image IO and Weixin shutdown — source checkpoint

Date: 2026-10-19. Part of owned-transcript-io Task3, not final47 acceptance.

## Evidence-backed source changes

- Six actual memory get/write variants (manager present/absent, get/daily/memory)
  originally opened files on their owner loop. Keyword search cancellation also
  escaped before the actual file read completed: RED7/0.19s. Tool reads and writes
  now use the existing owned worker helper. Shared manager write locking keeps
  read/check/append serialized across tools, without altering the existing
 2200-character capacity policy, authority, path checks or result text.
- The real append cancellation test uses the actual application's original
  GroupController reservation, tool instance and file. Repeated cancellation
  retains that fence until append settles; the original text appears exactly
  once. The concurrent two-tool case retains one allowed append and one capacity
  rejection. Existing permission/read-only behavior is not changed by worker IO.
- Generated-image path/stat operations were on-loop. Actual reference reads and
  saved-image writes could continue after cancellation returned: RED3/0.26s.
  Path preparation, references and final recheck/save now use owned workers;
  actual scripted provider execution stays on the original async path. Repeated
  caller cancellation joins the actual work and cleanup, without another image
  request. Real output PNG bytes and original quota/permission rules are retained.
- The first full-nine run did not pass: its Claw Weixin restart case hung during
  disconnect. Live stacks pinned cancelling `weixin-dispatch` to its next
  `inbox.get`, while monitor/channel/application shutdown waited on it. The
  callback had settled cancellation during presentation cleanup. Only our own
  blocked test worker was terminated after confirming this exact live state;
  the original runner recorded **one failed,1119 Claw passed**,357.58s. This is
  retained as failed evidence, not timeout expiry or a successful run.
- A deterministic monitor reproducer with the original two-second deadline
  failed both event-stop and direct-cancel variants (RED2/4.25s). Monitor now sets
  an internal stopping token before canceling its children; a callback returning
  normally cannot admit the next FIFO item. A single actual cleanup task joins
  children and closes owned API resources despite repeated caller cancellation.
  A further held-handler test proves no premature monitor release. Accepted
  durable application inputs remain recoverable; no queue drain/replay is added.

Only the provider/API boundaries and physical post-IO barriers are controlled.
The files, resources, private roots, original controller and shutdown are real.
No paid provider, credentials, network or existing user service/data is used.

## Current bounded verification

- Memory tool/fallback/flush and ownership cases:24 passed0.97s before adding the
  original-controller append and concurrent-capacity regressions.
- Image regressions plus existing actual image tool suite:42 passed0.41s.
- Monitor/Weixin intake/owned-channel suite:24 passed2.76s before adding repeated
  monitor cleanup cancellation.
- Final combined new memory/image/monitor plus existing image/intake cases:
  **74 passed2.92s**.

Fresh full-nine/static results are appended only after the actual commands
terminate. The previous terminated-worker run is never counted as current green.

## Open scope

Task3 remains active. Optional hybrid-memory/indexer filesystem operations, lazy
local embedding loading/encoding and SQLite connection acquisition/close require
actual ownership inspection and proofs, as do remaining cached metadata path
projections. Current source still has direct reads in the optional path; default
keyword tests cannot prove that optional path safe. The parent47 source audit,
single final whole-branch review and current artifacts/isolated Goal-only,
Coding/Claw/ACP/native consumers remain mandatory after required fixes.
No push/merge/publish occurs; old artifact snapshots are not current proof.

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
