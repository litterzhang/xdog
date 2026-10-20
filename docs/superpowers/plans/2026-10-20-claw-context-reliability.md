# Claw context reliability implementation plan

Spec: `docs/superpowers/specs/2026-10-20-claw-context-reliability-design.md`

Execute inline in the current checkout. User explicitly waived further approval
and worktree creation. No commits/push/releases are part of this goal. Every
behavior change starts with a regression that fails for the intended reason.

## Task 1: Unavailable transcripts

Files: persistence/transcript_store.py, session_preparation.py, runtime/group.py;
new tests/test_context_reliability.py and relevant existing persistence/IO tests.

1. RED: known missing current/archive, malformed/nonregular/unreadable content;
   valid empty file; original pointer retained on automatic/explicit reset.
2. Implement explicit unavailable errors and safe regular-file reads; ensure
   preparation checks current availability before rotation (owned IO).
3. GREEN: new tests + transcript_owner_integrity, owned_session_io,
   owned_transcript_commit, session_catalog and readonly navigation tests.

Produces: a fail-closed transcript availability boundary consumed by reset and
compaction. Unknown transcript lookup errors explicitly; unknown branch remains
None, known missing branch is unavailable.

## Task 2: Zoned timestamp validation

Files: persistence/transcript_store.py; tests/test_context_reliability.py.

1. RED: naive created_at, last_active, resumed_at; malformed optional resumed_at.
2. Validate on index load, keep bytes unchanged; preserve UTC/offset support.
3. GREEN: new timestamp tests and existing daily/idle reset tests.

## Task 3: Context preference inheritance

Files: transcript_store.py, navigation_journal.py; new reset inheritance tests.

1. RED: automatic and explicit reset retain model, model-bound reasoning,
   selected tools/skills and access; clear opaque extension entries/history.
2. Copy validated preference sidecars under the existing context-create journal;
   extend the strict journal allowlist for these managed file types.
3. Fault injection: no partially published pointer/preference owner; invalid
   original preferences do not trigger configured-default fallback.
4. GREEN: reset/model/tools/skills/access/Goal rotation tests and owned IO tests.

Consumes Task 1 availability and Task 2 valid metadata.

## Task 4: Group lifecycle ownership

Files: runtime/group.py, runtime/orchestrator.py and lifecycle tests.

1. RED: actual group close settles child/Goal/extension/MCP/memory ownership,
   repeated cancellation cannot skip cleanup, errors propagate after all close.
2. Expose group lifecycle methods; keep application input/output close ordering.
3. Align sync internal helper invariants with async preparation; no new framework.
4. GREEN: shutdown, retired extensions, group controller, owned IO and Goal tests.

## Task 5: Compaction durable transactions

Files: transcript_store.py (or small persistence helper), navigation_journal.py;
compaction/history transaction tests.

1. RED: checkpoint catalog/sidecar failure leaves original discoverable; replacement
   catalog failure cannot publish partial history; reopen recovers without replay.
2. Journal checkpoint and replacement, stage cache publication, fsync names before
   commit markers. Keep valid extra checkpoint when later replacement fails.
3. GREEN: compaction, owned transcript commits and navigation recovery tests.

Consumes Task 1 reads and Task 3 journal allowlist; no reset/Goal semantics change.

## Task 6: Verification and review

Run Ruff, affected strict typing, Claw/Agent/Skills/TUI tests and workspace command
`uv run python scripts/test.py`. Review entire uncommitted diff against spec,
including unsafe fallbacks, cancellation/error publication, sidecar allowlist,
branch availability and actual recovery boundaries. Record exact results and
remaining limitations in docs/claw-context-reliability-ledger.md. Mark goal complete
only when all five implementation requirements are verified.
