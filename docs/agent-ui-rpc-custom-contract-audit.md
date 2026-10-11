# UI-042: finite RPC custom/child-terminal acceptance audit

Checkpoint: 2026-10-10. This is a subordinate contract matrix, **not additional
requirements**. UI-042 counts once in `agent-ui-requirements.json`. Original scope
and final Linux gates remain unchanged. No Pi wire compatibility or non-Linux
native validation is claimed. Client-reported painting is not server-observed
physical terminal painting.

## Implemented contracts with current evidence

| Contract | Behavior and acceptance | Evidence |
| --- | --- | --- |
| Independent admission | Parent/child custom, overlay and child terminal capabilities require exact integers; invalid mixed hello causes no partial grant/revocation | `test_rpc_capability_atomicity.py`, `test_rpc_child_terminal.py` |
| Frame/input protocol | Owned worker/broker renders viewport lines/cursor; only current delivered revision/geometry/generation and fresh sequence can admit input; non-null direct completion denied | `test_rpc_custom_views.py`, `test_rpc_child_custom.py` |
| Nested ownership | Actual broker admission protects nested questions/custom views before frontend delivery; return requires fresh parent frame; ancestor null cancel cascades | `test_rpc_custom_views.py`, `test_rpc_child_custom.py` |
| Overlay ownership | Separate hide/show/focus/unfocus authority; hidden/non-capturing input refused; control changes invalidate frame; fresh generation required | `test_rpc_overlay.py`, `test_rpc_child_overlay.py` |
| Enhanced keyboard and paste | Declared press/repeat/release phases; only explicit press completes fixture; Unicode multiline paste stays one callback, no command execution | `test_rpc_custom_keyboard.py` |
| Background repaint | Actual parent/child worker state updates repaint without fabricated input; old revisions refused, final callback records one explicit input | `test_rpc_custom_refresh.py`, Agent `test_extension_custom_refresh.py` |
| In-flight and obsolete frames | Successful input admission retires receipt; superseded rendering cannot restore it while next render pending | `test_rpc_custom_views.py`, `test_rpc_child_custom.py`, Agent `test_extension_custom.py` |
| Queue and output refusal | 16-frame/2MiB delivery ceiling; scoped deduplicated capacity failure cancels original view; rejected output never creates cached grant | `test_rpc_custom_delivery.py` |
| Approval isolation | Both custom paths deny interactive admission during authoritative unpainted parent/child approval; failed admission preserves frame and sequence | `test_rpc_custom_delivery.py` |
| Child terminal transformation | Data-only editable keys/atomic paste use child's original registered hook; owner/epoch/worker generation, before/after approval/dialog gates; no queue submission/model turn | `test_rpc_child_terminal.py` |
| Terminal cancellation | Early cancellation returns one receipt with zero hooks; in-flight cancel/deadline settles owned worker; late approval blocks result, no replay | `test_rpc_child_terminal.py`, `test_rpc_child_terminal_lifecycle.py` |
| Actual Linux pipe lifecycle | Parent/child unread 120KiB frame and child held terminal hook quit/EOF/closed-output/deadline exit with exact owned worker reaping | `test_rpc_custom_backpressure.py`, `test_rpc_child_terminal_lifecycle.py` |

Test paths above are under `packages/coding/tests/` unless explicitly marked Agent.
The earlier combined command in `/tmp/ui042-current-contract.log` ran these files
plus Agent custom/custom-refresh tests: **221 passed, zero skips**, 147.55 seconds.
The final wider579-case command below supersedes that earlier checkpoint.
Current repository Ruff/all-seven strict mypy486: `/tmp/ui042-final-static.log`.
Those are requirement evidence, not requirement counts or final root regression.

## Acceptance actions (all verified; within UI-042, not additional IDs)

1. **Structured renderer end-to-end — VERIFIED 2026-10-10:** actual layout worker
   runs through parent and child RPC owners in inline/fullscreen/positioned-overlay
   modes. Row/column/box/viewport/layer output, Unicode/cursor/narrow resize,
   viewport horizontal/vertical scroll and nested focus verified in
   `packages/coding/tests/test_rpc_structured_layout.py`. The test exposed RPC's
   ordinary sanitizer removing ZWJ and widening frames; fixed with vetted custom
   control removal preserving whole graphemes. Six owner/presentation cases and a
   sanitizer regression pass; related current73-test integration matrix is
   `/tmp/ui042-layout-integration.log` (120.39s, zero skips).
2. **Current Linux terminal client — VERIFIED 2026-10-10:** owned private tmux
   frontend paints actual received parent/child layout frames using shipped TUI
   components; physical keys/paste exercise Unicode, layers, narrow resize,
   nested question return, independent child approval and parent recovery.
   Observed visible hardware cursor, positive painted revisions and final editor
   cursor; exact termios restoration, no main-buffer clear or alternate-buffer
   entry verified. `test_rpc_physical_terminal.py`: current fixture2 passed in
   `/tmp/ui042-physical-current-fixture.log` (9.12s); structured/keyboard/physical
   group15 passed in `/tmp/ui042-physical-verified.log` (38.01s).
   This is an owned test frontend, not a shipped remote client or arbitrary
   third-party client validation; physical overlay/fullscreen placement remains
   represented by separate native terminal requirements, not claimed here.
3. **Final integrated contract closure — VERIFIED 2026-10-10:** inspected
   public `CAPABILITIES`, `RpcExtensions` custom/dialog/editor handler scopes,
   `RpcChildren` child custom/overlay/terminal negotiation and original-owner
   envelopes, actual JSONL command routing and broker completion/cleanup rules.
   Every named contract in this audit maps to actual implementation and tests.
   Complete current `packages/coding/tests/test_rpc*.py` and
   `packages/agent/tests/test_extension_custom*.py` command: **579 passed, zero
   skips**, 334.55 seconds, `/tmp/ui042-final-rpc.log`. Includes physical Linux
   parent/child RPC terminal tests, not just parser/helper coverage. Before/mid/
   after snapshot confirms all981 source/test/TOML/MJS files unchanged:
   `/tmp/ui042-final-source-{before,mid,after}.json`. Ruff/all-seven strict mypy486
   clean (`/tmp/ui042-final-static.log`); six current Coding modules byte-verified
   in wheel/sdist `/tmp/ui042-final-wheels`. No new production changes were made
   during this verification.

UI-042 status is **已完成** for the named applicable RPC contract. All three finite
acceptance actions are verified. This does not claim arbitrary client compliance,
Pi wire compatibility, a shipped external frontend, non-Linux native validation,
or current-source full-root regression. The native-only/remote boundary is explicit:
frame callbacks execute in owned workers; the RPC consumer renders vetted frame
lines/cursor/placement and reports truthful painting. It cannot borrow tool or
session/editor permissions. The test frontend proves current Linux terminal
behavior, not other clients' truthful painting.

UI-039 separate transient/custom event integration acceptance was verified on
2026-10-10 with actual parent/child callback contributions and frozen581-case RPC
matrix (`/tmp/ui039-final-rpc.log`); no second implementation requirement was created. Final whole-goal Linux
terminal/safety/lifecycle and frozen root regression remain UI-049/UI-055/UI-064.
Do not reopen repeated historical broad OPEN clauses without a new concrete
contradiction or an explicit missing contract; real regressions should reopen the
stable ID with evidence, never silently change its scope.

## Independent outstanding owned invocation

Old pytest PID340375 / owned CLI PID340485, handle73617, remain confirmed live at
this checkpoint. Their old unbounded reader waits for a refused source admission.
Do not interrupt/restart under the user's current instruction; do not claim cleanup
or a passing outcome for that invocation. New bounded lifecycle checks establish
only their own outcomes. UI-038 closeout and final lifecycle gates retain this issue.
