> **历史归档，不是当前接口、待办或执行授权。**
> 原日期、结论和验证范围保留；当前入口见 [文档索引](../../README.md)。

# Core hardening and message hierarchy

Scope: existing AI/Agent/Flow/Eyes SDKs and Claw's read-only TUI presentation.
No new AI Provider configuration, supervisor, scheduler, business state store,
automatic replay, permission grants or migration compatibility layer.

Acceptance (each item requires implementation/verification evidence):

1. Agent streams: failure/cancellation reaches consumers and result waiters;
   explicit close settles the owned producer; no unobserved background failure.
2. Context: inspect the final prepared request with a category breakdown and
   honest estimates; reuse existing compaction budgets and host artifact ownership.
3. Tools: validate native schema at registration; retain typed error/effect facts
   without turning classification into permissions or permission to replay.
4. Flow: deterministic generated-graph differential tests plus explicit checkpoint
   compatibility; retain existing interpreter/codegen shared semantics and license.
5. Capability SDK: typed native Agent options; startup rollback, close/cancellation,
   retired tool identities and restore authority verified on existing owners.
6. Eyes: protocol/journal version, event ACK/recovery/backpressure/cancel contracts
   verified with real local resources; no new observation source or execution loop.
7. Claw: compact essential labels; full IDs/receipts/handoff metadata in existing
   details; canonical message bodies, identities and reply routes unchanged.
8. AI: true/false/unknown model declarations and explicit unsupported-request
   preflight; stable error kind/status/code/partial facts. No new Provider setup,
   implicit retry, provider/auth switch, automatic token-limit inference or replay.

## Implementation and verification (2026-11-04)

- Public Agent loop factories now adopt their actual producer; iteration/result
  receive the same exception. Explicit close joins cancellation and reports actual
  cleanup failures, including bounded streams and repeatedly cancelled close waiters.
- `ContextReport` / `inspect_context` reuse the final-request estimator without
  retaining prompt content. Agent exposes `last_context_report`; trusted budgets
  can be applied explicitly. Arbitrary host strings are not falsely attributed
  to individual skills, and unknown capacity remains unknown.
- Native tools use cached offline Draft 2020-12 validation, including nested,
  union, local-reference and non-finite JSON checks. MCP keeps its own dialect
  registry. `ToolFailure` records kind/effect uncertainty; `ToolArtifact` references
  host-owned output, with no automatic file storage, fetch or permission grant.
- Capability assembly options use `AgentOptions` (TypedDict/Unpack), not arbitrary
  untyped kwargs. Existing owner/lifecycle/retired-identity mechanisms are reused.
- Flow checkpoints pin graph/defaults and computed-node definitions, including
  earlier loop iterations whose frontier has reopened. A node with no retained
  computation may be repaired explicitly. Unversioned/incompatible state is
  rejected without migration or side effects. External modules still require
  host-owned version/artifact pinning. Deterministic generated DAGs and reciprocal
  interpreter/generated checkpoints are tested, including no replay of completed work.
- Claw headers retain compact identity/source/destination/status. Existing details
  retain whitelisted IDs, receipts and handoff provenance; raw host contexts and
  credentials are excluded. Transcript bodies and reply routing are unchanged.
- Model flags no longer invent support from absent metadata. Preflight rejects
  only explicit contradictions; streaming fallback already owned by Antigravity
  remains distinct from adapters requiring a streaming endpoint. Errors expose
  typed facts without deriving permissions, retries or fallback from classification.

## Core-hardening baseline verification (before compatibility removal)

Final per-package runs (not sums of repeated targeted runs):

| Package | Passed | Skipped |
| --- | ---: | ---: |
| Agent | 2252 | 15 |
| AI | 769 | 7 |
| Flow | 884 | 0 |
| Coding | 2609 | 12 |
| Claw | 2242 | 0 |
| Eyes | 551 | 0 |
| Skills | 726 | 0 |
| TUI | 813 | 0 |
| Site | 64 | 0 |
| **Total** | **10910** | **34** |

The root `scripts/test.py` run passed eight package suites and exposed one stale
Claw test referencing the removed raw banner. That assertion was updated and the
entire Claw suite rerun successfully. The entire Flow/AI suites were also rerun
after their final contract checks; the table uses those final runs.

- Ruff across `packages`: passed. Strict all-nine mypy: 692 source files passed
  (existing unused-override notes are non-failing).
- Shipped Flow examples: 17 passed. Codegen tests ruff-check generated modules;
  12 deterministic small DAGs compare full interpreter/generated output. Reciprocal
  checkpoints run both directions and verify completed side effects are not replayed.
- Eyes' existing full suite verifies private real Unix/SQLite daemon recovery,
  compatible control versus journal formats, ACK loss/idempotency, accepted-input
  capacity, cancelled CLI subprocesses, SIGKILL recovery and unknown delivery
  non-replay. No extra service owner or duplicate recovery mechanism was added.
- Capability lifecycle/assembly suites cover startup rollback, concurrent/cancelled
  startup and close, retired identities, actual original permissions on restore,
  additive selections and discoverable-but-inactive skills.
- AI/Agent/Flow wheels and sdists built; twine checks passed. Those wheels plus
  TUI were installed offline in an isolated venv. With Python `-I` outside the
  checkout, capability composition, schema/errors, an actual Flow CLI subprocess,
  and an offline Agent request/context report all passed.

Verification used synthetic models and private test resources; it does not claim
live Provider interoperability, production gateway restart or real channel sends.
Old unversioned Flow checkpoints are intentionally rejected, not migrated. External
module contents still require host pinning; artifact storage/retrieval remains
host-owned. No commits, tags, pushes, login or message replay were performed here.

The table above records the core-hardening baseline; current compatibility-removal
evidence is recorded below. This document is not a Goal, task runner or
execution authority.

## Compatibility removal (2026-11-04)

- Removed unversioned Agent restore, LegacySnapshot/decoder and top-level snapshot
  aliases. Invalid/old data is rejected atomically; there is no migration.
- Removed private default-converter wrappers, historical external-event decoder
  and Coding projection/rendering adapters. Current session-input contracts remain.
- Flow SDK, codegen and test stubs all consume/produce current SDK snapshots; no
  versionless branch or test-only old snapshot representation remains.
- ErrorEvent has one typed failure representation (explicit unknown by default).
  Native generation no longer infers endpoints from old cache/adapter fields.
- Removed Claw's private selector/status handles and v1/v2 history rendering fork;
  retained current string/typed-content union and normal permission checks.
- Provider startup/update catalogs use the same required registration identity.
  Coding defaults to medium and rejects retired thinking aliases.

Actual multi-wire model/MCP protocol implementations are not retired API aliases;
removing SDK compatibility does not remove supported real protocol transports.
Historical audit documents remain explicitly historical; no user data is erased.
Verification completed:

- Root `uv run python scripts/test.py`: all nine test paths passed. Final per-package
  aggregate is **10,923 passed, 34 skipped**, using the latest full Flow run of
  **892 passed** (including four extra stub rejection cases added after root
  collection). No old-path acceptance tests remain for the retired contracts.
- `uv run ruff check packages`: passed. Strict all-nine mypy: **691 source files**
  passed. Shipped Flow workflow examples: **17 passed**.
- AI/Agent/Flow/TUI wheel and sdist builds plus Twine checks passed. Fresh isolated
  installation outside the checkout verifies canonical round-trip, atomic rejection
  of empty/versionless/alias snapshots, absence of the removed event module,
  explicit native protocol declarations, typed unknown errors and the real Flow
  stub codec. Wheel inspection verifies deleted source/aliases are not packaged.
- Existing user storage/configuration was not edited or migrated. Old partial
  snapshots/field aliases and thinking aliases are intentionally unsupported.
  This is a breaking SDK-contract cleanup, not a production gateway restart or
  a live Provider/channel test. No commit, push or release was performed.

Status: current-interface cleanup implemented and verified.
