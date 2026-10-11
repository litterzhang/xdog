# Final UI-053 / UI-054 verification reconciliation

Date: **2026-10-11**. This is supporting evidence, not a central-ledger update.
**Current local final gate passed at22:38:48 +08 on October 11, 2026:**8200 tests,
zero failures/skips, unchanged1427-member source snapshot and all14 named Linux/
safety rows passing. Main owns ledger adjudication; the five external prerequisites
and deferred native-platform evidence remain separate. The chronological failed
attempts below remain retained as failures. No duplicate full-root run is performed
by this verification sidecar. After diagnostic-root completion, the user explicitly authorized
the bounded test-only geometry repairs and naming bootstrap instrumentation below;
these are not production repairs or a final root gate.

## Historical pre-integration residual: UI-043 custom delivery

**Superseded after integration:** main accepted the complete local canonical contract
in `agent-ui-custom-delivery-contract.md`; see the final UI-053 signoff below. The
following paragraphs retain the earlier pre-staging requirement and roadmap, not a
current claim that these branches are still unimplemented.

The user confirmed that canonical custom-message active `steer` / `follow_up` delivery
and explicit idle triggering are part of the original UI-043 scope. **No waiver or
user-message alias substitution is accepted. UI-043 remains 待实现 until resolved.**

The inspected pre-staging source exposes `request_context_message(...,
deliver_as='next_turn')` and rejects other delivery values. Reviewed user-message and
user-queue APIs do not by themselves retain canonical custom type/content/display/details.
The verified Pi0.73.1 `sendCustomMessage` implementation distinguishes:

1. `nextTurn`: queued for the next user prompt, without triggering a turn.
2. Streaming `steer` / `followUp`: canonical custom messages at the corresponding
   delivery boundary.
3. Idle with `triggerTurn`: a model turn beginning from the custom message.
4. Idle without triggering: retain the canonical custom message immediately without
   a model turn, rather than silently relabeling this as next-turn delivery.

The custom record may be projected to an LLM-compatible message with explicit provenance;
that does not permit changing its canonical identity into an ordinary user message.
Original owner, review, lifecycle, cancellation and no-replay requirements still apply.
These are finite branches of the already confirmed contract, not new IDs or invented
open-ended vendor parity.

Implementation ownership: the context worker is preparing a tested patch in the isolated
`/tmp/ui-custom-delivery-stage/work` copy, targeted for **17:40 +08 on October 11, 2026**.
The target is not a passing result. It must be reviewed and integrated before the final
fresh source snapshot and complete root pass. This sidecar does not modify that patch
or the running workspace's code/tests.

## Current complete-root run: diagnostic, not final frozen acceptance

Main started a complete root invocation with no deselection and these opt-ins:

- `XDOG_MCP_OFFICIAL_SDK_ROOT=/tmp/xdog-mcp-pass68.lw1gcs`
- `XDOG_TEST_CODEX_CLI=1`
- `XDOG_TEST_CLAUDE_CLI=1`

Evidence locations:

- `/tmp/ui-final-20261011/pytest.log`
- `/tmp/ui-final-20261011/pytest.xml` (authoritative outcomes when written on completion)
- `/tmp/ui-final-20261011/source-before.json`
- Main's execution session14664.

The initial source manifest contains1415 entries. A sidecar check at **17:06:04 +08**
found those entries present and hash-identical. It checked the named entries, not the
absence of additional files. The user subsequently explicitly invalidated the freeze
for the confirmed ACK repair, involving production and test changes. A check at
**17:07:59 +08** observed changes in the existing manifest entries for Claw `gateway.py`
and TUI `components/extension_custom.py`; this observation is not an exhaustive repair
manifest. Main's before/during/after manifests and file membership remain authoritative.

The run continues naturally and must not be interrupted. Its outcomes are useful
pre-repair/mixed-source diagnostics, **not UI-055 final frozen-pass evidence**, even if
later progress appears green. Progress `F` markers are not yet exact diagnoses; a saved
7879-node preflight list predates a few added tests, so ordinal-based names are tentative.
Only the completed log/XML will establish the actual failing/skipped nodes and reasons.
No second collection or duplicate root run was started by the sidecar.

## Completion sequence retained without scope reduction

1. Finish the current diagnostic root and enumerate its actual failures/skips.
2. Verify the confirmed ACK repair and any other confirmed regression under their owners.
3. Review the isolated custom-delivery patch and integrate it only with its required
   canonical identity, delivery, review and ownership evidence.
4. Establish a **new** complete source/test/fixture/config manifest, including newly
   added files, and run a fresh complete root test invocation with all intended opt-ins.
5. Compare full manifest key sets and hashes before/during/after that final invocation;
   enumerate its exact JUnit outcomes and skip reasons. Do not reuse the invalidated
   original manifest as a freeze receipt.
6. Main completes final static/distribution/example, Linux terminal and safety gates.

A scheduling target is not authority to omit the residual or call it passed. A full
root test count is not a count of fulfilled requirements.

## Baseline and skip interpretation

The historical comparison date remains **2026-10-09**. The sidecar's official OpenAI
search/retrieval attempt returned no usable documentation (direct features-page HTTP403),
so there is no newly verified/latest Codex documentation claim. The supplied historical
baseline, actual local client mock evidence, and independently integrity-checked published
Pi0.73.1 archive are distinguished in
`agent-ui-sidecar-reconciliation-2026-10-11.md`.

`agent-ui-sidecar-skips-2026-10-11.json` records33 exact default-environment skipped nodes
from an earlier **selected** run; every one passed with the available explicit local
opt-ins in the44-case gate. That file is not the final root skip list. The historical
29-skips log lacks exact nodes/reasons; no guessed list is presented as historical proof.

On final XML arrival, each actual skip will be recorded with its exact node, reason,
category, stable requirement IDs, and any genuinely current frozen-source opt-in receipt.
Unknown reasons will remain explicitly unmapped until their source is inspected.
Zero final skips would mean an empty exact skip list—not proof of external deployment,
physical-power-loss or deferred non-Linux native conformance.

## Unchanged independent requirements

- UI-041's finite editor-write, parent menu-read and parent after-run naming changes have
  selected worker/native/RPC/CLI/physical evidence in
  `agent-ui-command-menu-name-scope-audit.md`; this does not replace final safety/root gates.
- UI-050: a named additional Secret Service implementation/profile, not another claim
  about the already tested GNOME implementation.
- UI-052: explicitly authorized real provider/model/decoder interoperability beyond the
  existing offline/owned and system-decoder checks.
- UI-057: an authorized independent deployed MCP/OAuth target/version/credential lifecycle,
  not the already available pinned independent SDK fixture.
- UI-058: a controlled disposable power-loss/equivalent storage-fault setup; QEMU/dmsetup
  binaries alone do not establish it and process SIGKILL is not physical power loss.
- UI-059: controlled two-client desktop clipboard/image-protocol acceptance. GNOME/Wayland
  exists locally; absence of a desktop must not be claimed. A suitable owned test session
  and image-capable terminal roundtrip have not been established.
- UI-060–063 remain deferred native non-current-Linux validation, not passed and not an
  excuse for omitting any current-Linux gate.

## Diagnostic naming failure investigation (17:25 +08 checkpoint)

The raw worker's reconstructed prefix (`/tmp/ui-root-prefix-reconciled.txt`) places
ordinal1331 at
`packages/agent/tests/test_extension_name_after_run.py::test_real_worker_tool_result_error_discards_original_name_proposal[False]`.
This is a stronger locator than the superseded provider-auth estimate, but the
completed root traceback is still required for diagnosis. Ordinal2948 recovery-write
cancellation is owned by the raw worker, not Flow.

Under explicit user authorization, without interrupting or duplicating the root:

- The exact suspected naming node passed: **1 passed / 1.35s**,
  `/tmp/ui-sidecar-20261011/name-root-targeted.log`.
- Its immediate predecessor message-request file and complete naming test file passed:
  **24 passed / 7.57s**, `name-root-neighbors.log`.
- All seven checked files match the root-start SHA256 manifest: the naming test and
  worker fixture, `name_request.py`, `invocation.py`, `runtime.py`, `api.py`, and
  `worker.py`. Exact hashes are in `name-root-targeted-source.json`. This bounded
  comparison is not a claim that the entire running root is frozen.

Inspection confirmed the independent name-only UI hint, original-owner lease, typed
ToolResult error discard, and separate producer-settlement barrier are present.
No naming repair has been justified yet. No assertions were removed, no timeout was
increased, and no startup-flakiness explanation is asserted without the traceback.
Isolated green receipts do **not** erase the diagnostic-root failure.

## Completed diagnostic root and authorized test repairs (18:09 +08)

The completed diagnostic XML has **8063 distinct cases: 8040 passed, 23 failed,
zero skipped**. Exact failures and XML/log SHA256 values are retained in
`/tmp/ui-sidecar-20261011/diagnostic-root-reconciliation.json`; the exact skip map
for this invocation is therefore empty. Its mixed-source/freeze-invalidated status
has not changed. Neither these counts nor the selected repairs below are final
UI-054/055 acceptance. No unknown skips were silently classified as external blockers.

### Six Claw status-visibility failures

All three rich MCP schema cases (`array`, `error`, `content`) and all three local
resource-watch cases (`modern`, `legacy`, `modern-end`) failed while waiting for
visible `"state": "connected"`. Their captured preserved panes **already contain
that exact connected field**, but it is outside the last24 rows: rich cases have
46 lines with the field at zero-based21, watch cases47 lines with the field at22.
`Terminal.wait` polls `visible()` but includes `pane()` (preserved scrollback) on
failure. Thus the captured connected state is not evidence that the visible check
was false or that connection needed a longer timeout. Machine-readable verification
is in `terminal-geometry-evidence.json`, including the failure-source SHA256.

Authorized repair: only the two named test regions in
`packages/coding/tests/test_terminal_acceptance.py` enlarge Claw to100×40 for the
connection status check, then **restore80×24 immediately after it**. This restoration
also applies to the previously corrected official-SDK case. Coding geometry is
unchanged. All subsequent interaction, structured output, form/completion, watch,
no-read/no-model, cursor, scrollback and terminal-restoration assertions remain
unchanged. No timeout increase or scrollback-based substitute for visibility was used.

All14 affected parameterizations, including Coding and Claw and the explicitly
enabled independent official SDK, passed with **zero skips in33.41s**:
`terminal-geometry-repair.log` / `terminal-geometry-repair.xml`.

### Naming failure: bootstrap, not proposal/CAS

Exact failure0 is `runtime.tools()[0]` raising `IndexError` after initialization
returned with no tools. It happens **before** the naming tool callback, review, or
producer/CAS assertions. Inspection establishes why the original error was hidden:
`ExtensionRuntime._initialize_locked` contains a load exception by failing and
disconnecting the extension; the manager removes the failed client, while the test
previously indexed the empty tools tuple without checking the bootstrap receipt.
The root traceback does **not** retain the underlying load exception. Consequently
neither a timeout nor a particular transport/import/catalog fault is proven.

The original test now explicitly requires active extension state, exactly one tool,
a connected error-free client, and no bootstrap/disconnect failures before execution.
Test-local wrappers retain the actual load traceback and client error/stderr/returncode
before disconnect removes the client. Original review, typed ToolResult error, and
producer-settlement assertions are unchanged. No production code, timeout, retry, or
failure handling policy was changed.

Controlled evidence:

-20 fresh original test invocations, alternating both error-result branches, passed;
 each owned runtime initializes once, and the controller stops at the first failure
 rather than retrying. `name-bootstrap-controlled.log`; each case took0.841–0.868s.
-The original naming file plus its immediate message-request predecessor passed
 **24 tests / zero skips /7.32s**, `name-bootstrap-diagnostic-tests.log/xml`.
-A separate injected-bootstrap-failure diagnostic check proved the strengthened
 assertion actually retains the exception, client error, stderr and failed snapshot,
 without retrying or passing the failure. `name-bootstrap-diagnostic-injected.log`.
 This injected error is **not** claimed as the root's original cause.

**Residual:** the original bootstrap cause remains unreproduced and unavailable in
the old receipt. Instrumentation improves the next failure's evidence; it is not
a production fix or authority to erase failure0. Final fresh root acceptance is
still required under main's new complete source snapshot.

## Fresh final invocation and UI-053 reconciliation signoff

Main started the fresh complete root at **2026-10-11 18:15:44 +08**, session80895:
`/tmp/ui-final-20261011/final/pytest.log` and `pytest.xml`, with
`/tmp/ui-final-20261011/source-final.json` containing1426 entries. All other worker
tests settled before it began. No source/test changes or duplicate root are permitted.
The original18:20 deadline could not cover completion; the user was informed and
the goal remains active. A progress percentage is not a completion receipt.

The sidecar checked all1426 named entries unchanged while preparing final source
reconciliation. This named-entry check does not independently prove absence of new
files; main's final full-membership freeze comparison remains authoritative.

**UI-053 finite mapping is signed off** in
`agent-ui-upstream-contract-signoff-2026-10-11.md`. AST-derived54 public API method/
property names, package exports/direct-module types and pinned Pi public declaration
exports are recorded in `ui053-final-public-exports.json`. The historical baseline
remains2026-10-09; no latest documentation or native Pi execution is claimed. Earlier
UI-043 canonical custom and UI-044 OAuth pending statements are superseded by actual
integrated owner evidence, not waived or replaced by aliases. Main owns ledger changes.

Main reported final Ruff, strict seven-namespace non-incremental mypy549, eight
wheel/sdist byte+RECORD+metadata checks, extracted seven-namespace/entrypoint imports,
17 examples and git diff check exiting0. Sidecar read the corresponding final log
tails in `/tmp/ui-final-20261011/`; those receipts are separate from the still-running
full-root result. No skip disposition is inferred before the final XML is complete.

## Completed fresh-root attempt: exact UI-054 mapping (19:15 +08)

The invocation started18:15:44 finished with **8186 unique tests:8183 passed,
3 failed,0 errors,0 skipped**, pytest exit1, **3478.99s (57:58)**. The final log also
records source-check exit1. This is a completed full invocation with no deselection,
but **not final frozen acceptance**.

`agent-ui-final-skips-2026-10-11.json` is the durable exact current skip map:
**`skipped_nodes: []`**. The parser resolved actual module/class node identities
without importing or collecting tests; all8186 XML nodes match the8186 verbose-log
nodes exactly. All33 nodes skipped in the earlier selected default-environment gate
are present and **passed** in this opted-in root; the JSON retains each node, previous
reason/category/stable requirement IDs and its current result. The historical29
count-only skip receipt still has no recoverable exact nodes/reasons and is explicitly
recorded as unavailable, not replaced by the current33 or an invented list.

Exact failures (not skips or external waivers):

1. `packages/coding/tests/test_compact_layout.py::test_permission_shows_all_options_and_moves_highlight`
   — expected one Unicode selection marker, observed zero.
2. `packages/coding/tests/test_tui_usability.py::test_light_theme_uses_terminal_foreground`
   — background styling returned unchanged plain text.
3. `packages/coding/tests/test_tui_usability.py::test_permission_has_box_and_keeps_all_choices_at_normal_height`
   — expected rounded Unicode box corner, observed ASCII `+` corner.

These belong to the current-Linux terminal/verification gates UI-049/054/055/064,
not new vendor-parity requirements. No sidecar repair, targeted rerun, timeout change
or assertion weakening was performed during this fresh root. The original naming
bootstrap cases and all14 repaired rich-schema/resource-watch cases passed here;
that does not establish the lost earlier bootstrap exception's cause.

The sidecar's named-entry SHA256 check at **19:13:46 +08** agrees with main's
`final/source-check.json`: two test files changed against the1426-entry manifest,
`test_compact_layout.py` and `test_tui_usability.py`. Their repairs/next validation
are main-owned; this sidecar made no code/test changes. Those observations are not
a claim of full file-membership immutability or that traceback source-line text
from edited files is the exact source loaded earlier.

Machine receipts:

- `/tmp/ui-sidecar-20261011/final-root-reconciliation.json` — exact failures, skips,
  prior-default joins, XML/log hashes and node-set comparison. The log hash was
  refreshed after the wrapper appended its actual exit/source-check receipts.
- `/tmp/ui-sidecar-20261011/final-source-named-check.json` — named-entry drift check.
- `agent-ui-final-skips-2026-10-11.json` — current mapping retained in the repository.

UI-053 baseline/export/owner reconciliation remains signed off with its historical
limits. UI-054 **this invocation's mapping** is complete; the final frozen passing
acceptance/goal is not. Zero skips does not satisfy the five independent external
requirements or deferred non-current-Linux native verification.

## Authoritative presentation-final attempt (started19:17:22 +08)

Main confirmed the three preceding presentation failures were test assumptions under
the actual owned runner's `TERM=dumb NO_COLOR=1`, not a production UI regression.
`agent-ui-presentation-test-environment-audit.md` records the unchanged exact target's
color-terminal pass/dumb-terminal failure, the owned-process environment observation,
and strengthened Unicode/forced-ASCII/dumb and light/colorless cases. All original
selection/option/summary/height assertions remain. The corrected scope passed66 tests
under the **same dumb/no-color environment**; sidecar read that receipt without
starting another test. No production, timeout, skip or permission change was made.

The new authoritative complete root started **2026-10-11 19:17:22 +08**, main
session34411, under that same environment. Its evidence is isolated at
`/tmp/ui-final-20261011/presentation-final/`: `pytest.log`, `pytest.xml`,
`source-before.json` and `source-check.json`. The earlier8186-node failed/source-
changed attempt and its exact mapping remain preserved, not overwritten or renamed
a pass. The current invocation must complete before final results are attributed.

The sidecar's read-only19:22:40 source receipt independently enumerates the same
1426-file source/test/fixture/config scope, including files omitted by git-ignore
listing, and confirms identical keysets and hashes. Enumeration rules and limits
are retained in `/tmp/ui-sidecar-20261011/presentation-final-source-monitor-before.json`.
The completion monitor will require XML, actual wrapper exit codes and main's source
check, then repeat that enumeration and map every final outcome/skip and all33 prior
default-skip nodes. No duplicate collection/root, code/test edits or process control
are performed. UI-058 remains plan-only pending user laboratory authorization; no
heavy VM or destructive storage work is introduced.

### Presentation-final freeze explicitly invalidated (20:00 +08)

The user confirmed two independently reproduced test-ordering defects and approved
changes to exactly five test/fixture files while the root continued naturally.
At20:00:11, the sidecar's read-only hash check matches that exact changed-path set:

- `packages/agent/tests/fixtures/mcp_subscription_peer.py`
- `packages/agent/tests/test_mcp_subscriptions.py`
- `packages/agent/tests/test_mcp_combined_watch.py`
- `packages/agent/tests/test_mcp_catalog_watch.py`
- `packages/coding/tests/test_child_image_submission.py`

No production-path hashes changed among the1426 manifest entries. Main owns these
repairs; the sidecar did not modify code/tests or start any tests.

`agent-ui-subscription-test-receipt-audit.md` records that a completed cancellation
pipe write is not the peer's processed/logged receipt. A30ms delayed fixture
reproduced the unchanged cancellation-count assertion; the fixture's ordered
read-only status response now proves zero modern/legacy subscriptions before the
log count is asserted. Exact counts, ownership and no-resource-read checks remain,
with additional slow-peer coverage:88 related cases passed, no timeout increase.

`agent-ui-child-image-test-readiness-audit.md` records the independently traced
8ms Enter while initial roster/presets were busy, before the roughly50ms receipt.
The existing busy guard correctly rejected that input; no automatic replay or
guard bypass was introduced. One positive-workflow readiness barrier now awaits
both idle panel and no pending child controls under the existing deadline. The
delayed private20-case and repository20-case receipts retain full image/review/
persistence/queue/parent-owner assertions, with no production change.

These are independent repair receipts, not substitutes for the current root's
completed traceback or a fresh all-source gate. The19:17:22 invocation is now
**diagnostic/source-changed**, even if later tests pass. UI-056's updated artifact
proof is reopened by main. The final XML/skip/node mapping is still pending actual
completion; the old and new attempts are not combined into a fictitious full pass.

### Completed presentation-final: exact outcome and skip mapping (20:16 +08)

The19:17:22 attempt completed normally: **8191 unique tests,8188 passed,3 failed,
0 errors,0 skipped**, **3511.20s (58:31)**. The wrapper records pytest exit1 and
source-check exit1. It was not interrupted and is not final acceptance.

`agent-ui-presentation-final-skips-2026-10-11.json` is the separate durable map for
this invocation. It preserves the earlier8186-node attempt by reference rather than
overwriting that result. All8191 exact XML node identities match the8191 verbose-log
identities, including test classes and parameterizations. The exact skipped-node list
is again **empty**. All33 previously default-skipped opt-in nodes passed here; each
node's prior reason/category/requirement IDs and current receipt is retained. The
historical29 count-only skipped identities remain unavailable and are not invented.

Actual completed failures:

| Exact node | Actual traceback/capture | Stable owning scope |
| --- | --- | --- |
| `packages/agent/tests/test_mcp_subscriptions.py::test_owned_stdio_watch_changes_shared_legacy_and_exact_cleanup_no_resource_read[modern]` | Two `subscriptions/listen` log records but only one `notifications/cancelled` at the assertion. This matches the independently reproduced missing peer-processing receipt, not an inferred transport regression. | UI-025/048/054/055/064 |
| `packages/coding/tests/test_child_image_submission.py::test_actual_child_file_review_start_queue_persistence_and_parent_draft_ownership[68-claw]` | `UI wait failed: mode=confirm, error=, queue=None, pending=[]`; retain the independently traced earlier Enter-before-roster readiness defect without claiming this final timeout snapshot alone proves its timing. | UI-015/049/054/055/064 |
| `packages/coding/tests/test_terminal_acceptance.py::test_actual_registered_argument_tab_enter_inserts_only_and_native_parent_restore[claw]` | Missing `"executed": 0`; captured pane instead includes `Extension controls require idle group ownership`. This is not the previous connected-field-off-viewport failure and is not waived or attributed to a cause without investigation. | UI-035/049/054/055/064 |

All eight strengthened parameterizations covering the preceding permission-highlight,
light-theme and normal-height permission-box failures **passed** under this run's
same dumb/no-color environment. Their exact nodes are included in the new map.
Passing those cases does not erase the three different failures above.

At20:16:02, the sidecar's independent after-check confirmed the same1426 scoped
source/fixture/config file identities, no added/missing members, and exactly the
five previously authorized test/fixture hash changes. No production hash drift was
observed in that scope. Main's source-check agrees. Before/after enumeration rules,
manifest hash and exact changed paths are retained in
`/tmp/ui-sidecar-20261011/presentation-final-source-monitor-{before,after}.json`.

`/tmp/ui-sidecar-20261011/presentation-final-root-reconciliation.json` holds XML/log
hashes, exact node correspondence and full failure text. Hashing waited for the
wrapper's actual exit/source-check receipts rather than merely XML creation.
UI-054 **this attempt's mapping** is complete; UI-049/055/064 final passing acceptance
and main's reopened UI-056 artifact gate are not supplied by this failed/mixed-source
attempt. No sidecar code/test edits, additional tests, heavy VM work or ledger changes
were made. The five external prerequisites and non-current-Linux deferrals remain
separate from both zero-skip results.

## New authoritative settled-final attempt (20:35:48 +08)

Main started session46758 at **2026-10-11 20:35:48 +08** with a new1427-file
source/test/fixture/config snapshot under `/tmp/ui-final-20261011/settled-final/`.
Root Ruff, strict seven-namespace549-source mypy, diff check, eight artifact pairs
with byte/metadata/RECORD/import checks and17 examples passed preflight; the wrapper
records `PREFLIGHT_EXIT_CODE=0`. The failed8191-node attempt remains diagnostic.

The argument-completion owner handoff in `/tmp/claw-argument-diagnostic/HANDOFF.md`
records two distinct confirmed issues: a frontend cancel enqueue was not its actual
settlement receipt, and an unstarted gateway task's canceled reply preceded its sole
done-callback reservation release. The narrow gateway fix waits for the original
unstarted task/done callback before replying; started cancellation stays nonblocking
and the original release owner is retained. The physical test now uses a test-only
nonce-correlated observer of the actual frontend request/result/cancel settlement,
not an out-of-band host-idle snapshot. The existing6s bound and admission guards
remain. The four exact owner paths are:

- `packages/claw/src/xdog/claw/core/runtime/gateway.py`
- `packages/claw/tests/test_argument_cancel_settlement.py`
- `packages/coding/tests/fixtures/terminal_client.py`
- `packages/coding/tests/test_terminal_acceptance.py`

Owner receipts:15 gateway cases,6 physical cases, and three independently delayed
physical passes. This includes a **production gateway lifecycle fix**, unlike the
earlier presentation/subscription/child-image test-only corrections; these histories
are not conflated. The handoff preserves red proof and discloses overwritten batch
trace artifacts rather than inventing their old contents. No new tool loop, retry,
timeout increase or permission relaxation is claimed.

The sidecar read that handoff without running tests. Its20:38:36 independent
enumeration confirms identical1427 scoped file identities and hashes against the
new manifest; `settled-final-source-monitor-before.json` records exact rules/hash.
The read-only monitor will wait for XML **and actual exit/source-check receipts**,
then repeat source membership/hash checks and map all final nodes/skips. Main owns
UI-056's reclosed artifact status and any aggregate ledger disposition. No further
code/test edits are planned; the sidecar makes none. UI-058 authorization remains
unanswered, with no VM/storage experiment undertaken.

### Settled-final completed: source stable, one cleanup failure (21:35 +08)

The20:35:48 attempt completed normally in **3410.67s (56:50)**: **8200 unique cases,
8199 passed,1 failed,0 errors,0 skipped**. Pytest exit1; source-check exit0. Independent
before/after enumeration confirms exactly1427 scoped members, no added/missing files
and no hash changes, agreeing with main's source check. This is **source-stable but
not a passing final gate**.

The actual failure is
`packages/coding/tests/test_argument_completion_ui.py::test_actual_both_client_argument_editor_selects_without_submission_or_side_effects[select-coding]`.
Its traceback is in cleanup: test line193 calls `session.extensions.aclose()`, then
`ExtensionRuntime.aclose` calls `McpManager.aclose`; manager line1169 schedules
`loop.stop` via `call_soon_threadsafe` on a closed loop, raising `RuntimeError: Event
loop is closed`. No selection failure or previous Claw cancellation cause is inferred
from the test name. The delegated diagnostic worker/main owns any repair.

The durable exact mapping is `agent-ui-settled-final-skips-2026-10-11.json`:
**`skipped_nodes: []`**. XML and verbose-log sets match all8200 exact nodes; all33
previous default-skipped opt-in nodes passed. Historical29 count-only skips remain
explicitly unavailable. The prior8063/8186/8191 attempts remain their own evidence,
not pooled into a fictitious all-pass result.

The user's requested named UI-049/064 coverage is now explicit in
`agent-ui-final-named-gates.md` / `.json`. All14 rows,360 named modules and required
static test bases are present in this completed XML; no coverage module/base is
missing. Twelve rows have only passes; L1 and S2 share the single cleanup failure.
Exact per-row nodes and counts are in
`/tmp/ui-sidecar-20261011/settled-final-named-gate-results.json`. No separate matrix
run is needed: a subsequent frozen passing root can be adjudicated against this
finite existing index, including any repair regression under its existing row.

UI-053 finite reference/export reconciliation remains signed off; UI-054 this run's
mapping is complete. UI-049/055/064 passing acceptance is not supplied by source
stability alone. The five independent external prerequisites, unanswered UI-058
laboratory authorization and non-current-Linux deferrals are unchanged. The sidecar
performed only read-only inspection/XML/source joins and supporting docs updates;
no test run, code/test edit, process interruption, VM work or central-ledger change.

## New authoritative teardown-final attempt (21:41:35 +08)

Main's fresh preflight passed, then the complete8200-case root started on
**2026-10-11 at21:41:35 +08**, PID1835490/session22491. Evidence is isolated in
`/tmp/ui-final-20261011/teardown-final/`; `agent-ui-active-checkpoint.md` is the
current durable main checkpoint. The prior source-stable but failed8200-case
`settled-final` receipt is retained separately.

The completed traceback matched the delegated fixture-teardown diagnosis. Main
applied only six test lines in `test_argument_completion_ui.py` **after** that run
completed: await the original completion threads before direct fixture async cleanup,
matching native teardown ownership. The integrated six-file family passed62 cases
in14.83s. `agent-ui-argument-teardown-audit.md` preserves the cause and boundaries;
no production change or timeout/assertion weakening is asserted for this correction.

The named matrix is reused without adding any row, module, anchor or test-base
obligation. The sidecar verified that only this one test-file binding changed
between the settled-final and teardown-final1427-entry manifests, and all14 rows/
360 modules remain identical. Exact rebind evidence is
`/tmp/ui-sidecar-20261011/teardown-final-plan-refresh.json`; the immutable current
plan is `teardown-final-named-gate-plan.json` beside it. The repository's named-gates
JSON now references that plan; prior per-run plans/results are not overwritten.

At21:43:09, the sidecar independently enumerated the exact same1427 members and
hashes using the recorded freeze's file types, excluded directories and symlink
policy. It will repeat this check only after final XML and actual wrapper exit/source
receipts. The runner's read-only `ui_live_failure` plugin writes exact failed node,
phase, duration and traceback to `failures-live.jsonl`; inspection confirmed it does
not retry, recollect or modify outcomes. The sidecar monitors these receipts without
starting tests, editing package/tools source, controlling processes or starting VMs.
The five external prerequisites and deferred native platforms remain independent.

## Authoritative teardown-final passed: final local adjudication

The complete root started **2026-10-11 21:41:35 +08** and its wrapper finished at
**22:38:48 +08**. Actual result: **8200 passed,0 failed,0 errors,0 skipped**,
**3430.89s (57:10)**. `PYTEST_EXIT_CODE=0`, `SOURCE_CHECK_EXIT_CODE=0`, and preflight
exit0 are recorded. `failures-live.jsonl` is empty. This result is not a sum of
selected tests or previous runs; the exact8200 XML and verbose-log node sets match.

`agent-ui-teardown-final-skips-2026-10-11.json` is the authoritative durable exact
current mapping. Its skipped-node list is **`[]`**, and all33 previously default-
skipped opt-in nodes passed in this same frozen invocation. Their individual prior
reasons/categories/stable IDs and actual current receipts are retained. The historical
29 count-only skip identities remain explicitly unavailable; no list is invented.

Independent source checks at21:43:09 and22:39:01 enumerate the same1427 scoped
source/test/fixture/config members and confirm identical hashes, no additions and
no missing members. The recorded enumeration matches the runner's source-freeze
file-type/directory/symlink policy; main's own source-check also records no changes.
This does not claim immutability of unrelated files outside that declared scope.

All **14 named UI-049/UI-064 rows,360 modules and static test bases** are present
and passing. L1 now has315 passing nodes; S2 has1362; every other row also has only
passes. There are no missing modules/bases and no skipped or failed row evidence.
Exact node lists and the immutable current source-bound plan are in
`/tmp/ui-sidecar-20261011/teardown-final-named-gate-results.json` and
`teardown-final-named-gate-plan.json`. Shared rows deliberately overlap and are not
added as independent test counts. No separate matrix test invocation is needed.

Final sidecar disposition for main:

- **UI-049:** named current-Linux terminal matrix passes on this frozen source.
- **UI-054:** exact current node/skip mapping complete, with historical limitations
  explicitly retained and available local opt-ins executed.
- **UI-055:** full root exit0 with unchanged scoped source membership/hashes.
- **UI-064:** named current permissions/ownership/lifecycle/transport matrix passes.
- **UI-053:** earlier finite historical-baseline/public-export/owner reconciliation
  remains signed off; no latest-upstream or native-Pi execution claim.

These receipts support main's adjudication of the four remaining local verification
requirements. Main retains central-ledger authority. Existing preflight evidence for
Ruff, seven-namespace strict no-incremental mypy549,8 wheel/sdist pairs with byte/
metadata/RECORD checks, extracted imports and17 workflow examples is tied to this
unchanged snapshot. No additional test run was started by this sidecar.

**Overall goal completion is not claimed.** UI-050/052/057/058/059 still need their
independent authorized real environments/evidence; UI-060–063 remain deferred native
non-current-Linux verification, not passed. In particular, zero skips does not create
provider/deployment/desktop/storage-fault proof or authorize a VM/laboratory action.
Previous failed/source-changed attempts, diagnostic causes and repair receipts remain
in the documents and their separate JSON maps.
