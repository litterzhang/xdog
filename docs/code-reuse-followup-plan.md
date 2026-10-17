# Code reuse follow-up: all six review findings

Date: 2026-10-17. **Complete; 0 remaining: 0待实现,0待验证,0缺外部环境,6已完成,0延期.**
This is a new six-item goal. Do not reopen or double-count the completed RF-001..003/UI goals.
Baseline main `b649282` includes the completed but uncommitted previous refactor.
Its dirty files are privately backed up before any new edits in
`/tmp/xdog-reuse-followup/initial-dirty-files.tar.gz`, with initial patch and hashes.

Authoritative scopes, acceptance and evidence: `code-reuse-followup-requirements.json`.

| ID | Work | Priority |
| --- | --- | --- |
| RR-01 | Cancellation-safe owned spool resource context manager;actual context/provider callers migrated;fix repeated-cancel worker escape/late tempfile cleanup | First,P1 |
| RR-02 | One shared default Agent/standalone-loop message projection | P2 |
| RR-03 | Shared AI terminal-result wrapper for Anthropic Messages/OpenAI Responses | P2 |
| RR-04 | One common native parent-draft presenter with small host-policy adapters | P2 |
| RR-05 | Pure shared MCP consent/auth response normalization;remove duplicate guard | P2 |
| RR-06 | Shared cross-loop dispatch mechanism with explicit owner-specific admission/lifecycle policy | Last,P2 |

RR-01 starts with deterministic failing tests from the confirmed private review probe.
Resource-producing work must retrieve and close late results even after repeated cancellation;
ordinary `run_owned` delivery cannot replace that resource owner. Preserve file/receiver cleanup,
manifest/checksum/order/IDs and provider generation/registration/auth/close fences.

For RR-02..06 characterize behavior before extraction. Keep custom converter hooks,protocol
wire/auth separation,native approval/context/view leases,image budgets/quit barriers,OAuth
consent/credential security and cross-loop admission/remote cancellation/settlement semantics.
Do not introduce a giant application base,merge different ACP group/session policies,make Eyes
or standalone TUI depend on Agent,or collapse distinct error-vs-cancel precedence contracts.

All6must have real consumers and per-item proof. Then freeze source and tests and run all8
configured `scripts/test.py` paths with the existing authorized independent MCP SDK enabled,
Ruff,strict mypy all8namespaces,17Flow examples,offline lock,fresh offline all-package build
and source/README/API/import/artifact checks. Keep Linux terminal/chat/transport/permissions,
crash/durable-no-replay,SDK snapshots/skills/compaction and Flow/licensing invariants. Do not
weaken retained tests;disclose optional external CLI skips. Non-Linux native validation is out
of scope,not a blocker and not claimed passed.

Proceed autonomously without routine approval prompts. Preserve unrelated files and all
prior uncommitted changes;no stash/reset/clean/add-A. No installs,live provider/credentials,
service restart,destructive migration,version/tag/release or automatic commit/push.

## RR-01 implementation checkpoint

The new actual-caller regression reproduced3failures before source edits: context
transfer released ownership during repeated cancel (both late success/error),and
provider spool's late successful tempfile was not explicitly closed. A fourth
late-error provider case already retained cancellation correctly.

`extensions.transfer.owned_spool` now owns creation plus delivery: it settles the
real task despite repeated cancellation,retrieves its late result,and explicitly
closes it. Context transfer and provider upload both use the context manager;
provider file-close-before-stream-close and provider-lock cleanup remain intact.

43focused tests passed,including13new resource/actual-caller cases: repeated cancel,
late errors,normal consumer failure,entry failure,explicit close,context roundtrip,
chunk/checksum manifests,provider upload errors and early consumer close. Targeted
Ruff and strict mypy(2sourcefiles)passed. Prior dirty files remain intact except the
intentionally updated extension runtime;stage is empty.

This is not final acceptance. RR-01 is待验证;RR-02..RR-06 are待实现. All6remain until
implementation and final complete regression/artifact gates are satisfied. Next:
shared default message conversion,then protocol terminal wrapper,UI presenters,
consent normalization and cross-loop mechanics. Evidence:
`/tmp/xdog-reuse-followup/rr01/report.json`.

## Delivery authorization update — 2026-10-17

User now explicitly requests commit and normal push to main **after finishing**
all6items/local gates,and tracking the exact commit's GitHub build/test workflows.
This supersedes the earlier no-auto-commit/push restriction for these related RF
and RR changes only. Keep unrelated files unstaged;no version bump/tag/release or
service restart. Before delivery check remote main;no force push. Verify pushed
SHA,monitor CI,investigate/fix failures and obtain actual green build/test evidence
before marking the goal complete. Never use pending/queued/skipped as passed.

## RR-02..05 implementation checkpoint

Candidates now share the default message projection (both Agent and standalone
loop), protocol terminal-result wrapper (without sharing wire/auth parsing),
parent draft presenter (with host-local scope/approval/retention/focus adapters),
and pure OAuth consent reply normalizer (host detach/transport remain local).
Characterization/focused counts respectively: 23/56, 36passed+2earlyclose failures/47,
30/143, 52/52. Early-close ownership now explicitly closes the protocol iterator,
never a borrowed client. All candidates await complete integration/delivery gates.
RR-06 is being implemented separately in shared cross-loop dispatch and its real
owner call sites. Accurate status: 6 remaining, 1待实现,5待验证; no acceptance claimed
from focused tests alone. Evidence directories: /tmp/xdog-reuse-followup/rr02..rr05.

## Implementation freeze and delivery checks

All six candidates are implemented and awaiting final acceptance (0待实现,
6待验证,0已完成,0缺外部环境,0延期;6 remaining). RR-06 migrates all four
remote dispatch sites while preserving owner-local admission/loop shutdown and
same-loop semantics. Its characterization exposed5admission/disposal failures;
58focused and329selected regression cases pass after the extraction/hardening.

Final source/test freeze is recorded in
`/tmp/xdog-reuse-followup/final/frozen-package-hashes.json`. The complete8-path
regression runs with the independent MCP SDK enabled, alongside all-package static,
example,lock,build and isolated artifact gates. Existing GitHub baseline CI has
an Eyes batch-settlement timeout under investigation and a secret-scanner API
rate-limit/false-license failure. The scanner now receives the existing read-only
job token;scanning stays enabled. These are delivery subchecks,not extra copies
of the six review requirements. Exact pushed-SHA CI must pass before acceptance.

## Frozen local validation — 2026-10-17

All local gates pass on the frozen package sources/tests:

| Gate | Result |
| --- | --- |
| All8configured test paths; independent MCP SDK enabled | 6177passed,0failed,7existing optional external CLI skips;117.48seconds |
| Ruff | Pass |
| Strict mypy,all8namespaces | 550sourcefiles,no errors |
| Flow examples | 17passed |
| Offline lock | Pass |
| Fresh offline builds | 9distributions,18artifacts |
| Isolated artifact verification | 22module imports;source/README/API/retired-module/owned-work/projection/stream/presenter/consent/dispatch wiring and repeated-cancel cleanup behavioral probes pass |
| Frozen package inputs | 1345files unchanged |
| Unrelated initial dirty inputs | 7recorded hashes unchanged |

No retained test sources were weakened or deselected. The original Eyes failure
was not reproduced in91targeted runs or the complete suite;all targeted receipts
show7successes in1/5/1batches. Do not claim a root cause or speculative fix. Local
Python3.13results are not proof for the failing Python3.12runner;fresh GitHub CI
is still required. The secret scanner authentication fix preserves full-history
scanning and existing rules. Evidence:
`/tmp/xdog-reuse-followup/final/` and `/tmp/xdog-reuse-followup/ci/eyes/REPORT.md`.

All6remain待验证 until authorized main delivery and exact-SHA CI succeed. No
version bump,tag,release or service restart is included. Unrelated
`docs/agent-ui-features.md` and `workflows/` remain outside the staged changes.

## First remote CI checkpoint

Implementation committed/pushed as `16b8737b50be1d8ac5d90ccd082e77e663e8f285`.
GitHub run39031103173 passed Tests,Lint,Types,Example suites,Flow wheel and secrets.
The next Eyes artifact check failed because it still demanded zero dependencies,
contradicting the already-merged ACP SDK dependency. The assertion now requires
exactly the approved SDK version/compatibility bounds and rejects any extra
requirements,including XDOG harness dependencies. The frozen wheel passes this
check locally;no dependency or runtime code was changed. The original Eyes
end-to-end timeout did not recur remotely. Complete green exact-SHA CI remains
required;these are delivery corrections,not additional refactoring requirements.

## Remote timing-sensitive MCP test correction

Run39031655542 failed the retained MCP timeout/cancel test at an ordinary final
reply under the same200ms deadline as its deliberately delayed call. No source
changed between the preceding green Tests step and this run. No production
cross-loop defect was established. The single retained test and offline peer
now use explicit admission/release/late-response-ID receipts instead of sleeps;
the real200ms timeout remains around the held call,with the usual2s for healthy
IPC. Exactly two original delayed calls,two exact cancellation IDs,late-ID
isolation,empty pending state and owned close are asserted. Other peer modes
are unchanged. This is deterministic harness hardening,not a dropped or weakened
requirement. Focused MCP stdio+cross-loop67passed;all final gates are rerun after
this new source/test freeze. See `/tmp/xdog-reuse-followup/ci/mcp/REPORT.md`.
The previous assertion that existing test files were unmodified described the
first frozen candidate only;this correction changes1retained test file+1fixture.

The post-harness frozen gates all pass again:6177tests+7optional CLI skips
(all8paths,independent MCP SDK enabled;120.34seconds),Ruff,strict mypy550files,
17Flow examples,offline lock,18fresh artifacts and22isolated wheel imports plus
all shared API/behavior probes.1345package inputs remained frozen throughout.

## Final acceptance — 2026-10-17

**Exactly6completed requirements,0remaining.** Pending implementation0,pending
verification0,missing external environment0,deferred0. Historical checkpoints
above are superseded by this acceptance and the authoritative JSON ledger.

| ID | Accepted reusable implementation |
| --- | --- |
| RR-01 | Owned spool resource context manager shared by real context/provider transfers;repeated cancellation joins work and closes late resources. |
| RR-02 | One default structured/custom message projection for Agent and standalone loop;sync/async overrides remain. |
| RR-03 | One AI-only protocol terminal-message wrapper;wire/auth remain separate;explicit early-close iterator cleanup. |
| RR-04 | One native parent-draft presenter;Coding/Claw retain host permission,focus,scope,retention and quit policies. |
| RR-05 | One pure consent-reply normalizer;both native publishers retain host ownership/detach and credential boundaries. |
| RR-06 | One remote dispatch/actual-cleanup settlement helper;all four owners retain admission/start/close policies,with tested disposal/admission hardening. |

Final local frozen gates:6177passed,7existing optional external CLI skips,
Ruff,strict typing550sourcefiles,17Flow examples,offline lock,18fresh artifacts,
22isolated wheel imports plus all shared API/behavior checks.1345package files
remained frozen. Existing independent MCP SDK was enabled locally. Test harness
hardening retains all existing timeout/cancel/late-ID/no-replay assertions;
exact receipt checks are stronger,not retries/deselection. No production deadline
was changed. Seven initially recorded unrelated dirty file hashes are preserved.

Implementation `16b8737`,CI metadata correction `c37bf07`,and deterministic test
harness `82a0f98` were normally pushed to main. **GitHub run39033187499 on exact
commit82a0f9876f39eab3afd2ac2a51e862d33689fbe9 is successful:** both `test` and
`secrets`,including every test,lint,type,example,Flow/Eyes build/data/metadata,
inter-package requirement and full-history secret-scan step. No pending/skipped
job was reported as passed. The baseline Eyes timeout did not recur;no unproven
production fix was claimed. Final evidence-only commit is monitored separately.

Production touches32sourcefiles with111net fewer lines;11new test files cover
shared contracts. No runtime dependency,new feature deletion,second Agent/service,
permission expansion,version bump,tag,release or service restart. Non-Linux native
validation remains outside scope and is not claimed passed. Unrelated
`docs/agent-ui-features.md` and `workflows/` were never staged.

Evidence: `/tmp/xdog-reuse-followup/final/`,
`/tmp/xdog-reuse-followup/ci/harness-result.json`;per-item scopes,acceptance and
classification counts are in `code-reuse-followup-requirements.json`.
