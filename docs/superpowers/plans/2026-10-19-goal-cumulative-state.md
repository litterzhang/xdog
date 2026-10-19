# Goal cumulative state and bounded data access implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans inline. Continue the approved parent final-acceptance Task2, not a new goal. No per-task reviewer; retain the one final wholebranch review.

**Goal:** Remove a confirmed hidden lifetime/data quota while preserving bounded, exact, owner-bound tool transfers and immutable original role context.
**Architecture:** Authoritative Goal snapshots/revisions have no aggregate business byte quota. Existing individual field/report/evidence/transport safety limits stay. Large tool values use explicit byte pages with immutable selector/version/checksum/offset and bounded readable canonical-JSON text; small values remain the same complete JSON response. A mutation exceeding wire capacity still returns a successful committed receipt, never an error that suggests replay. Original role context paging cannot select owner/Goal/attempt/revision. No schema migration or alternate business store.
**Tech Stack:** Existing frozen models, leased SQLite/owned workers, hashlib JSON, pytest/mypy/Ruff.
**Spec:** `docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md` sections3/9/11; GOAL-13/VERIFY-01/02. Parent `2026-10-19-claw-final-acceptance.md` Task2.

## Global Constraints
- Same checkout, no worktree/push/merge/publish/paid models/user services/data changes; use Bash and /data/t.Vy5SaQ temporary/cache roots.
- No Goal enable switch, no fixed cumulative child/task/report count or business lifetime cap. No increase to existing individual report/notice/context/image safety bounds.
- Preserve actual host permissions, independent review/criteria/attempt evidence checks, original owner and role-bound tool identities, no message approval/side-effect replay.
- Keep current Goal journal/snapshot schema identity and exact validation. No migration/dual read or automatic conversion of unknown data.
- Parent full47/review/current artifacts remain mandatory. No subplan completion claims for entire goal.

## Review Focus
1. Large committed mutation must not masquerade as a failed action merely because complete output exceeds a transport frame.
2. Page reads after later mutations must keep the requested immutable version, exact bytes/checksum and owner; no torn multi-page snapshot.
3. Worker/reviewer paging can read only the original bound context/section and must not choose another Goal/version/host.
4. Byte limits remain per wire page/individual field, not a new cap on total records or Goal age; DAG validation must not impose Python recursion depth as task count.
5. Large serialization/decoding must not block actor loops or release store ownership before admitted work settles; corruption/owner failures preserve original bytes.

### Task 1: Authoritative aggregate snapshots are not lifetime quotas
**Files:** goal codec/models/store and `test_cumulative_state.py`.
**Interfaces:** `json_text(maximum=None)` is internal authoritative aggregate encoding, default bounded calls remain. encode/decode_goal exact fields/format/finite primitive validations unchanged. put prepares detached snapshot on owned worker. DAG validation is iterative, keeps task order and rejects cycles/missing dependencies.
- [ ] Actual >1MiB valid Goal put/get/reopen and reverse-ordered >recursion-depth DAG tests; run and observe RED at current aggregate cap/recursion, not missing API.
- [ ] Remove only aggregate snapshot byte cap; preserve individual fields/report and notice/artifact caps. Offload large put preparation with original owned IO (no mutable caller references).
- [ ] Iterative graph validation, no count limit/pruning; retain strict cycle tests.
- [ ] Actual roundtrip/reopen/rollback/corrupt tests plus Goal suites, strict/Ruff. Commit bounded fix; task-done focused command. Transport remains Task2, not falsely complete here.

### Task 2: Exact bounded tool data access and committed receipts
**Files:** Goal data page helper, tools/service as required, tests/README and packaged skill guidance.
**Interfaces:** Tool frame remains256KiB. Pages contain format, selector (Goal/version/section), sha256, byte offset/total, readable ASCII JSON-fragment payload/next_offset, raw page <=64KiB. Strict integer offsets/limit, immutable historical version for parent read. Parent goal read action can select only its existing owner journal; role context accepts section/offset/limit only and remains original pinned version. Large mutation status returns explicit committed success + stable read descriptor; list pages/large context use exact continuation, not silent truncation/error. Small existing results unchanged.
- [ ] Actual >256KiB committed mutation currently produces tool error; actual large original role context/list exceed frame. Write and observe RED on existing APIs before adding actions.
- [ ] Implement common exact data-page response helper with finite JSON, version/digest/scope envelope, bounded payload and rejection of invalid offset/limits. No arbitrary file/host method capability.
- [ ] Parent read action with explicit version; bounded receipt for large mutations; role section continuation without owner/Goal/attempt/version selection. Make all aggregate value encoding owned worker work.
- [ ] Reconstruct exact bytes across all pages, mutate current Goal mid-read, verify pinned version/criteria, cross-owner/forged role queries refused, small existing APIs unchanged and no action replay/model calls.
- [ ] Update descriptions/README/skill to explain continuation, no new flag or automatic delegation. Final Goal plus actual Coding/Claw role suites, all9/strict/Ruff/Flow17/lock/diff; commit and task-done. Return parent47 audit and single final review, then fresh artifacts.
