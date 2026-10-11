# UI-049 / UI-064: final named acceptance index

**Latest adjudication:** teardown-final completed22:38:48 +08 on October 11, 2026;
all14 rows pass,8200 root tests pass,1427 source members/hashes unchanged. Earlier
failed checkpoints below are historical and not the current disposition. Main owns
ledger updates; external environments remain outside this local gate.

Checkpoint: **October 11, 2026**, created by read-only source inspection while the
`settled-final` root runs. This is an index of the **existing** acceptance matrix,
not additional parity scope, a new Cartesian product, or a request for another test
run. UI-049's acceptance is the current Linux native parent/child/extension/draft
terminal matrix; UI-064's is current SDK/both-native/Claw-gateway/Coding-RPC safety,
original ownership and actual cleanup. The authoritative ledger remains main-owned.

`agent-ui-final-named-gates.json` records each row's **resolved explicit test-module
list**, named anchor tests and boundaries. Patterns are only readable shorthand for
that finite saved list; no future matching file silently expands the matrix. The
full static test-base inventory is `/tmp/ui-sidecar-20261011/final-named-gate-plan.json`.
All indexed test files matched the running `settled-final/source-before.json` when
inspected. No pytest invocation or collection was performed to build the index.

## How to adjudicate directly from the root receipt

1. Parse the completed XML using actual `module.py::Class::test[parameters]` identities.
   Every saved required module and every statically declared test base must be present;
   **every parameterization actually collected into this root must pass**. Missing,
   skipped, errored or failed required evidence stays explicitly unresolved.
2. Join the exact nodes into rows L1–L6 and S1–S8 below. The result lists node IDs,
   counts, absent bases/modules, and any nonpassing nodes. Shared nodes intentionally
   support multiple axes; row counts must not be added into an invented test total.
3. Require the whole current root's exit0 and unchanged source/test/fixture/config
   membership and hashes, plus main's actual wrapper/source-check receipts. A row
   passing inside a failed or source-changed run is a receipt, **not final acceptance**.
4. If these conditions hold, UI-049 and UI-064 can be adjudicated from this one root
   and named index. **No separate matrix test invocation is needed.** If not, the
   actionable residue is the exact failed/missing row evidence and any freeze drift,
   not an open-ended request for more vendor parity.

The joiner is `/tmp/ui-sidecar-20261011/map_named_gates.py`; running it only parses
already-produced XML and source inventory. Previewing it against the earlier XML
preserved its actual failures and correctly reported the newly added gateway test
module absent. That preview is not a current-source pass.

## UI-049 — Linux terminal coverage

Here `C`, `L`, `A`, `T` denote `packages/coding/tests`, `packages/claw/tests`,
`packages/agent/tests`, `packages/tui/tests`; the JSON uses full paths.

| Row | Original acceptance axis | Named evidence and boundary |
| --- | --- | --- |
| L1 | Both native parents: permission/input, Unicode/paste, resize, scrollback, focus and exceptional return | **All cases in `C/test_terminal_acceptance.py`**, including `test_actual_coding_cli_permission_paste_resize_and_exit`, `test_actual_claw_tui_with_isolated_socket`, reconnect/no-resubmit, scrolled-clear, external-editor success/failure/interrupt, transcript/fullscreen and child paths. `test_compact_layout.py`, `test_tui_usability.py`, `test_argument_completion_ui.py` are additional named UI/assertion support, not mislabelled as physical PTY runs. |
| L2 | Independent native children, image/draft recovery and original-editor reviews | `C/test_draft_durability_terminal.py`, `test_remote_parent_terminal.py`, both Coding and Claw `test_editor_write_terminal.py`, and actual-host `test_child_image_submission.py` / `test_child_image_admission.py`. L1 additionally includes both-client child start/approval/config/model/preset/image/stopped-copy/return physical cases. |
| L3 | Extensions, reviewed messages/queues/custom delivery, after-run controls | Every explicitly resolved current `C/test_*terminal.py` and `L/test_*terminal.py`: includes command/dialog/form/rich result and parent recovery, editor writes, user/queued/context messages and images, canonical custom idle/active delivery, active-tools, compaction and naming. This saved finite expansion also overlaps the specialized rows; it is not inferred coverage from an old six-test pass. |
| L4 | MCP/provider native review and terminal restoration | Explicit MCP roots/sampling/context/elicitation/task/modern-browser terminal modules, Claw sampling/elicitation counterparts, Coding/Claw provider-OAuth terminal modules and extension-provider-auth terminal module. Root's local SDK/auth-fixture opt-ins must really execute. External provider/deployment/desktop conformance is not inferred. |
| L5 | Actual RPC parent/child terminal data, physical rendering and shutdown recovery | `C/test_rpc_physical_terminal.py::test_actual_rpc_physical_frame_cursor_resize_nested_approval_and_restore`; `test_rpc_physical_editing.py::test_actual_rpc_edit_data_paints_unicode_repeat_release_cancel_and_restore`; parent-draft physical cases; child-terminal lifecycle close/EOF/quit/deadline; `test_terminal_release_physical.py`. Parent/child and release-mode combinations come from actual parameterized node receipts. |
| L6 | Shared terminal lifecycle/renderer/width and abnormal handoff | `T/test_terminal_lifecycle.py`, `test_main_buffer_renderer.py`, `test_tui_rendering.py`, `test_keys.py`, `test_prompt_terminal.py`, `test_stdin_buffer.py`, `test_wrap_ansi.py`, `test_utils.py`, `test_external_editor.py`. Named anchors include raw failure/EOF restoration, external callback restoration and temporary-fullscreen exception/stop. Backend tests support, not replace, L1–L5 physical host evidence. |

The inspected private-tmux `Terminal.finish` verifies actual `TERMIOS-RESTORED=True`,
the recorded termios result, no scrollback-clear, no unowned main-buffer clear or
alternate-screen entry, bracketed-paste exit, and balanced explicitly permitted
fullscreen transitions. Its resize helper requires cursor re-query and completed
repaint; editor/cursor helpers observe actual tmux cursor state. Individual physical
cases perform their own Unicode/paste/narrow/resize/review/cancellation actions.
This is not a claim that every conceivable action cross-product is required or tested.

Actual RPC editing tests inspect typed Unicode paste, repeat/release observations,
held-hook cancel, original parent/child receipts and terminal restoration. Actual
editor-write physical cases check full review, narrow repaint, unchanged model-call
count, original draft and post-denial recovery. Desktop clipboard/image protocol
acceptance remains UI-059; test-owned clipboard fixtures are not desktop acceptance.

## UI-064 — safety/lifecycle/transport coverage

| Row | Original acceptance axis | Named evidence and boundary |
| --- | --- | --- |
| S1 | Read-only auto-admission; mutating default-deny/explicit approval or allow-all | `C/test_permissions.py` exact read-only/no-approver/invalid-mode/exact-call/deny-cancel/closed-loop cases; `C/test_planning.py::test_actual_sdk_structured_plan_read_only_and_explicit_execution_retains_policy` retains ask/allow-all policy; RPC permission response while running; both real child factories and Claw planning gateway. Generic tools remain tools, not a security sandbox. |
| S2 | Original extension callback, registration, lease/revision, typed-error and host-grant ownership | Explicitly resolved `A/C/L test_extension_*.py`, actual command/hook/tool lifecycle UI modules, Agent/Coding/TUI argument-completion modules. Includes callback success versus `ToolResult.isError`, full review before commit, provider/render/headless denial, original selected-child binding, disposal/replacement and no parent/sibling borrowing. |
| S3 | Child supervision/admission, queue/history durability and stopped recovery | `A/test_child_*.py`, `test_supervision*.py`, both host `test_child_*.py`, Coding `test_rpc_child*.py`. Named checkpoint anchors require persistence before public receipt, cancel/close reaching and awaiting the actual writer, foreign/corrupt rejection and owned SIGKILL no-replay. SIGKILL is not UI-058 physical-power-loss proof. |
| S4 | Canonical inputs/custom queues, producer settlement and reviewed host CAS | Agent/loop/identified-input/context tests; Claw orchestrator; Coding context persistence/compaction and queue controls; both-host after-run and active-tools tests; canonical custom delivery; both-host session staging. Acceptance distinguishes queued/admitted/consumed/completed, typed custom identity, original producer/loop and expected revision/name rather than transport success. |
| S5 | Claw gateway/Coding RPC closed or slow peer, EOF/quit, responsive cancellation and reply delivery | **All explicitly resolved Coding `test_rpc_*.py`** plus Claw gateway, terminal protocol, parent-draft gateway and `test_argument_cancel_settlement.py`. Its unstarted original-task release-before-reply and started held-cleanup/responsive-reader tests are named anchors. Default-off capabilities, original connection/session/generation, full result delivery and sole cleanup owner remain required. |
| S6 | MCP/OAuth host and peer ownership, reviews, task/watch/helper cleanup | Agent MCP/OAuth modules and both-host MCP modules, native OAuth/provider-OAuth/credential configuration tests, Claw sampling/elicitation counterparts. Covers roots, sampling, elicitation, tasks, modern/legacy transports, owned SDK, denied/revoked/stale callbacks, no implicit resource/model access and actual helper/peer cleanup. Mocked non-Linux backends or local providers do not establish external/native-platform conformance. |
| S7 | Private draft/source/image bytes and filesystem worker lifetime | TUI child drafts/saved source/parent receiver; Coding RPC parent drafts/interlocks/physical receiver; typed image lifecycle/admission/submission; Claw parent-draft gateway; Agent image context. Require integrity/revision/original ownership, actual cancellation settlement, no premature spool/store release, retained uncertain effects and no replay. |
| S8 | Protected editors/review/render/raw-input owners, ACK generation and helper cleanup | TUI extension/terminal/editor/URL/external-editor modules; both shared and Claw custom-ACK regressions; Coding RPC approval-revision and both-host terminal-input UI. Full-painted review/one-use/current owner, protected approval/editor phases and old-ACK/new-generation isolation are required; raw interception is not permission or composer authority. |

## Actionable remaining acceptance at this checkpoint

The running root has reported
`test_argument_completion_ui.py::test_actual_both_client_argument_editor_selects_without_submission_or_side_effects[select-coding]`
as failed. It is explicitly included in **L1 and S2**, not hidden outside the matrix.
Averroes owns diagnosis. Until the actual completed traceback, any required repair,
and a frozen passing full receipt establish acceptance, UI-049/064 remain awaiting
adjudication. No extra test suite is demanded solely because this index was produced.

The five external prerequisites UI-050/052/057/058/059 and deferred non-current-Linux
native evidence UI-060–063 are separate. This index neither closes them nor treats
their absence as permission to skip currently available Linux safety/terminal checks.

## Settled-final completed XML adjudication (21:35 +08)

The completed **8200-node** root has8199 passes and one failure, no skips/errors.
All1427 scoped source/test/fixture/config members and hashes remain unchanged;
source-check exit0, pytest exit1. All360 named modules and all required static test
bases appear. There are **no missing modules or bases** in any row. Exact parameterized
node lists are in `/tmp/ui-sidecar-20261011/settled-final-named-gate-results.json`;
the immutable per-run plan is `settled-final-named-gate-plan.json` beside it.

| Row | Passing nodes | Failed nodes | Missing module/base |
| --- | ---: | ---: | ---: |
| L1 | 314 | 1 | 0 / 0 |
| L2 | 53 | 0 | 0 / 0 |
| L3 | 181 | 0 | 0 / 0 |
| L4 | 52 | 0 | 0 / 0 |
| L5 | 24 | 0 | 0 / 0 |
| L6 | 149 | 0 | 0 / 0 |
| S1 | 73 | 0 | 0 / 0 |
| S2 | 1361 | 1 | 0 / 0 |
| S3 | 561 | 0 | 0 / 0 |
| S4 | 240 | 0 | 0 / 0 |
| S5 | 811 | 0 | 0 / 0 |
| S6 | 1209 | 0 | 0 / 0 |
| S7 | 181 | 0 | 0 / 0 |
| S8 | 327 | 0 | 0 / 0 |

These counts overlap and must not be summed. **L1 and S2 reference the same single
failure**, not two defects: the Coding argument-completion `select` case failed in
its `finally` block awaiting `session.extensions.aclose()`. The actual traceback
reaches `McpManager.aclose`, scheduling `loop.stop` on an already closed loop;
`RuntimeError: Event loop is closed`. It is not a failed selection assertion or the
previous Claw unstarted-cancel refusal. The owning diagnostic worker/main must resolve
that lifecycle failure; this source-stable but failed invocation cannot close UI-049
or UI-064. No other missing matrix has been inferred.

Actionable next acceptance is a reviewed lifecycle repair, updated source/artifact
correspondence where applicable, and a frozen full root whose XML clears this named
index. Re-indexing any added regression tests under these existing rows is source
bookkeeping, not another test run or expanded feature scope.

### Teardown-final binding refresh

The same14-row/360-module matrix is bound to the new21:41:35 run's1427-entry
manifest. Only `packages/coding/tests/test_argument_completion_ui.py` changed its
source hash; every row, resolved module, anchor and static test-base list is identical.
`/tmp/ui-sidecar-20261011/teardown-final-plan-refresh.json` proves the narrow refresh.
The active immutable plan is `teardown-final-named-gate-plan.json`; the earlier
settled-final plan/result remain preserved. This is source bookkeeping only—no
new collection, test invocation or extra acceptance scope. Final XML adjudication
is pending this run's actual exit/source receipts.

## Final passing adjudication — teardown-final

Actual full-root receipt:8200 passed,0 failed/errors/skips, pytest/source-check exits0.
All1427 scoped source identities and hashes remained unchanged. The exact same14-row/
360-module plan was used; all expected modules/static bases and all observed root
parameterizations are present and pass. The previously failing L1/S2 cleanup case
now passes in this full gate. No new test matrix or scope was added.

| Row | Passing nodes | Failed/skipped/missing module/base |
| --- | ---: | --- |
| L1 | 315 | 0 / 0 / 0 / 0 |
| L2 | 53 | 0 / 0 / 0 / 0 |
| L3 | 181 | 0 / 0 / 0 / 0 |
| L4 | 52 | 0 / 0 / 0 / 0 |
| L5 | 24 | 0 / 0 / 0 / 0 |
| L6 | 149 | 0 / 0 / 0 / 0 |
| S1 | 73 | 0 / 0 / 0 / 0 |
| S2 | 1362 | 0 / 0 / 0 / 0 |
| S3 | 561 | 0 / 0 / 0 / 0 |
| S4 | 240 | 0 / 0 / 0 / 0 |
| S5 | 811 | 0 / 0 / 0 / 0 |
| S6 | 1209 | 0 / 0 / 0 / 0 |
| S7 | 181 | 0 / 0 / 0 / 0 |
| S8 | 327 | 0 / 0 / 0 / 0 |

Do not sum overlapping rows. `agent-ui-teardown-final-skips-2026-10-11.json` retains
the root/source receipts and row summary;
`/tmp/ui-sidecar-20261011/teardown-final-named-gate-results.json` contains exact
per-row node identities. UI-049/UI-064's named local acceptance is supported without
another test run. Main owns requirement status; the five external prerequisites
and native non-current-Linux deferrals remain distinct and are not waived.
