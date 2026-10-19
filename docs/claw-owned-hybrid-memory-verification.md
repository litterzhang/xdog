# Optional memory and application cleanup IO — source checkpoint

Date: 2026-10-19. Continuing owned-transcript-io Task3 after62a2b1e. This does
not complete the parent47 acceptance audit or replace final installed artifacts.

## Actual runtime changes

- Frozen file evidence (text/mtime) and sorted directory snapshots are prepared
  by owned workers. Hybrid/indexer/reindex routes use that same data path; no
  new search engine or second authoritative memory store is introduced.
- Local embedding import/model preparation/inference is serialized on an owned
  worker. The API fallback stays on its original async loop and receives the
  same detached text tuple, not a caller-mutated list after local preparation.
  No fixed model/token/record budget or new business quota is added.
- Aiosqlite retains its actual physical connection worker. The existing index
  resource now serializes acquisition/schema readiness, queries and transaction
  commits; only its original loop publishes the ready connection. Actual disk
  work remains on SQLite's own worker. A canceled caller waits for acquisition
  or admitted commit; it cannot abandon a late connection. Failure closes and
  joins that actual worker, and close waits admitted operations before teardown.
- Replacement rows/tracking commit as one transaction, rolling back on failure.
  Readers cannot observe a source deleted but not yet replaced on the shared
  connection. This cache remains derived from memory files, not Goal/Core input
  authority. Index replacement does not replay a model/tool or an assistant turn.
- The native vector extension loads through public async SQLite operations on
  its connection worker, not raw sqlite3 calls on the Agent loop. Missing vector
  or BM25 dependencies retain the original keyword fallback.
- Application workspace metadata returns its already canonical root without
  another filesystem resolution. Shutdown has one joined cleanup task; caller
  cancellation cannot skip later resources. Actual input/output/child/extension
  close failures remain observable after other resources settle. Repeated close
  returns the same result without rerunning cleanup; multiple errors are retained
  together. Display failure does not roll back an already committed execution.

## Behavioral evidence

`test_owned_hybrid_memory_io.py` uses real files, aiosqlite/native SQLite
connections/transactions and worker threads. Only local-model/API boundaries
are scripted; no model weights are downloaded and no paid provider is invoked.
`test_owned_application_projection.py` uses the existing actual extension worker,
Inbox, application shutdown and resource-close calls with post-operation barriers.

Observed RED:

1. Optional file IO/acquisition/local inference:5 failed0.37s.
2. Commit cancellation, partial shared-connection view and failed replacement:
   3 failed0.32s against actual pre-fix source, then restored candidate bytes.
3. Close Future completed before physical SQLite worker exit:1 failed0.21s.
4. API fallback observed caller-mutated text instead of prepared batch:1 failed0.18s.
5. Cached workspace IO plus dropped component close errors:3 failed1.08s.
6. Real Inbox close error skipped unrelated live resources:1 failed0.81s.
7. Repeated-cancel application close skipped remaining MCP cleanup:1 failed0.77s.
8. File read/stat PermissionError was silently treated as missing optional memory:
   2 failed0.38s. Only actual FileNotFoundError now denotes absence; unavailable
   reads raise without changing committed index rows. A final full source gate
   runs after this refinement, not the earlier green suite.

Two earlier shutdown probe attempts used invalid cross-loop asyncio.Event
synchronization. They were stopped as invalid test harnesses, not counted as
production RED. The valid repeated-cancel proof uses threading.Events around the
actual extension worker close, retaining the original model/permission boundaries.

Focused source:related78 passed9.94s, strict600 before final snapshot refinement;
final new hybrid/application cases16 passed2.59s. Current full/static gates are
recorded below only after actual terminal completion, not inferred from focus.

## Actual optional dependency positive

An ephemeral `uv run --with sqlite-vec --with rank-bm25` environment uses real
sqlite-vec 0.1.9 and rank-bm25 0.2.2, without installing sentence-transformers or
calling any provider. Related source tests32 passed1.96s. A separate real
private-root index/search proves `_has_vec` is true, vector+BM25 retrieve original
committed memory text, and the actual SQLite worker is gone after close. Log:
`/data/t.Vy5SaQ/claw-owned-hybrid-native-runtime.log`. This is a source optional
path proof, not a current wheel-only consumer or final artifact parity result.

## Remaining Task3/source acceptance work

The explicit source audit still finds ACP filesystem/canonical-path work in
`acp/server.py` start/close and `acp/backend.py` target/load operations. Move those
through owned resource/data preparation without weakening private0700/0600,
existing-endpoint rejection, canonical workspace checks or peer/generation
rechecks. Native `runtime/unix_endpoint.py` has an actual owned bind helper, but
its stale-native policy must not silently replace ACP's explicit refusal policy.

`runtime/source_protocol.py` still uses plain awaited thread reads for spool
chunks, closes the spool on-loop, and only shields a late preparation once.
Its repeated-cancel/close behavior needs actual spool/read/cleanup barriers before
any fix; a one-time shield is not proof of full settlement. The TUI-only connect
preflight also has a synchronous socket exists check to inspect. These findings
keep Task3 and VERIFY-01 open; optional memory passing is not all-application IO.

The parent47 audit's source projection finding is now an actual counterexample:
ordinary `[Goal:` user input survives the real Inbox/Agent/durable transcript,
but generic source_records hides it, leaving only the assistant reply. See F-05
and `/data/t.Vy5SaQ/claw-neutral-source-audit-red.log`. A tracked regression and
removal of obsolete business filtering remain required; source/redaction/lease
rules must stay intact. No current Goal business backend or migration is needed.

Then complete Task3 source gates, parent47 source inspection, the one fresh
whole-branch review and final current build/Twine/parity/isolated consumers.
No push/merge/publish, credentials or existing user data/service changes occur.

Declared minimum-dependency compatibility is also exercised with an ephemeral
`aiosqlite==0.20.0` plus real sqlite-vec/rank-bm25: **36 passed2.64s**, including
actual physical Thread shutdown, cancelled acquisition/commit, read isolation,
API snapshot and unavailable-file cases. This supplements the base environment's
aiosqlite0.22.1 run, without changing project dependencies/lock or downloading
local embedding weights. Log: `claw-owned-hybrid-final-minimum-native.log`.

## Fresh complete source gate

Final current candidate: **6979 passed,34 skipped**,193.12s across all nine
configured test paths, using unfiltered `uv run python scripts/test.py --jobs 2`.
Claw1153/126.40s. Strict9 mypy600 files, full `ruff check packages scripts`,
Flow17, lock/diff checks passed. Logs under `/data/t.Vy5SaQ/`:
`claw-owned-hybrid-final-root9.log`, `claw-owned-hybrid-final-static.log`.
The earlier6977/193.61s snapshot predates unavailable-file refinement and is not
substituted for this final run. Original socket/terminal tests and deadlines
remain unchanged; no new xfail/filter/timeout relaxation or skipped case.

This closes the bounded optional-memory/application-cleanup counterexamples,
not Task3 or overall47 acceptance. ACP/private path and SourceSender ownership
plus actual F-05 generic source text filtering remain required work. No final
review, current artifact parity/cold consumers or goal completion is claimed.
