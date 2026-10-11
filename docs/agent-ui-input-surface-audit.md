# UI-036: named extension input-surface acceptance matrix

Current reconciliation: **2026-10-11** (see the final section). The dated
2026-10-10 checkpoints below are historical: their unresolved reference action is
superseded by the pinned primary package inspection and explicit release-observation
implementation. Subordinate to UI-036; rows are contracts, not new requirement IDs.
Preserve the original goal and final Linux regression; no OS sandbox claim.

## Current surfaces and boundaries

| Surface | Owning implementation and behavior | Evidence |
| --- | --- | --- |
| Coding native parent prompt | Original encoded editable press/repeat key spans, atomic paste, FIFO ordering, explicit recovery/pause; protected controls never transformed | `packages/coding/tests/test_terminal_input_ui.py`, shared `test_prompt_terminal.py` |
| Claw native parent prompt | Same contract through negotiated gateway/session/connection/input lease; stale requests/reconnect cannot replay | `packages/claw/tests/test_terminal_input_ui.py` |
| Native selected-child steer/follow-up composer | Uses child's own worker, child/form lease; sibling/back/resize revokes input; no parent borrowed authority | Both clients' `test_terminal_input_ui.py`; physical child terminal tests |
| Coding RPC parent editor data | Independent `terminal_input`, current terminal/session/worker identities, one owned job; only returns editing data | `packages/coding/tests/test_rpc_parent_terminal.py` |
| Coding RPC child editor data | Independent `child_terminal_input`, child owner/epoch and worker generation; no implicit child input submission | `packages/coding/tests/test_rpc_child_terminal.py` |
| Custom view keys/paste/resize | Own named custom protocol, opt-in enhanced press/repeat/release and painted frame completion | UI-042 completed finite audit; no duplicate implementation count |
| Approvals/dialogs/navigation/start-settings/host recovery | Remain host-owned; prompt hooks cannot borrow these input surfaces or forge grants | Native/RPC approval/modal/owner tests, UI-042 finite matrix |
| Terminal protocol reports/graphics/control bytes | Filtered before prompt interception; no raw terminal writer authority | TUI protocol/stdin tests; protected transformation negatives |
| Print/headless without editor | Hooks do not gain imaginary interactive terminal/editor surfaces | Existing runtime silent UI contract and explicit unavailable handling |

Prompt release events are not edit actions: native parent/selected-child prompt
interceptors discard release before hook dispatch without disturbing held FIFO input.
RPC editable-key data refuses release on input AND transformed output; repeat stays
encoded. Custom-view release remains independently supported only when declared.
Atomic paste retains its data boundary and cannot become raw controls.

Current native/shared/RPC key/lifecycle contract group127 passed:
`/tmp/ui036-release-final.log` (72.53s). Both-client current Linux physical prompt /
selected-child pause/recovery/resize/native-buffer/termios regression6 passed:
`/tmp/ui036-release-terminal.log` (14.59s). Static seven-namespace mypy487 and Ruff
clean: `/tmp/ui036-release-current-static.log`. Passing cases prove named contracts,
not every possible terminal or arbitrary upstream raw API.

## Finite remaining actions before closing UI-036

1. Map historical “broader raw surfaces” to concrete original/reference API contracts
   and actual exported handlers. Resolve any explicitly missing surfaces by implementation;
   do not silently reinterpret arbitrary raw interception as edit-only equivalence.
   Existing protected surfaces remain required safety restrictions, not permission
   to discard missing applicable APIs. Custom contracts stay mapped to UI-042 once.
2. **Physical RPC parent/child editor data — VERIFIED 2026-10-10:** owned Linux
   tmux frontend applies actual hook results through shipped PromptTerminalInterceptor
   and InputPanel, observing Unicode/cursor, repeat original encoding, release zero
   dispatch, held-hook Escape cancellation, paused safe recovery and exact termios /
   native main-buffer restoration. Actual worker input log proves phases/paste;
   parent editing invokes no model; child producer is settled on exit.
   `test_rpc_physical_editing.py`2 passed, `/tmp/ui036-physical-edit.log` (5.33s).
   Related current88-test matrix `/tmp/ui036-physical-edit-final.log` (69.96s,
   no skips). This is an owned test frontend, not a shipped arbitrary RPC client.
3. Run final integrated named native/gateway/RPC/runtime/terminal input matrix after
   action1/2; record scope-matched evidence and reopen stable IDs on contradictions.

UI-036 remains 待实现 pending action1 contract reconciliation; do not keep inventing
an undefined “broader” successor after every passing slice. Final whole-goal Linux
safety/terminal/frozen full-root gates remain independent requirements.

## Runtime shortcut boundary update (2026-10-10)

Parent/child RPC editing validation now considers the actual owned runtime shortcut
inventory through native effective conflict/override/disable rules on both input
and transformed output. Current60-test gate `/tmp/ui036-shortcuts-final.log`
includes actual workers and physical editor regression. Primary Pi fetch was
unavailable (web returned no content; curl connection reset), so action1 remains
unresolved, not satisfied by guessed upstream behavior. Action2 remains verified;
action3 final integrated matrix still pending. No additional requirement IDs.


## Integrated current Linux input checkpoint (2026-10-10)

After UI-038 owned hang cleanup and receipt hardening, the current combined named
native Coding/Claw, shared editor/stdin, runtime worker, RPC parent/child lifecycle,
keymap, approval-admission-revision and physical RPC editing matrix passed
**178 cases, no skips, 117.29s**: `/tmp/ui036-current-integrated-input.log`.
Command includes `test_extension_terminal_input.py`, `test_prompt_terminal.py`,
`test_stdin_buffer.py`, both hosts' `test_terminal_input_ui.py`,
`test_rpc_parent_terminal.py`, `test_rpc_child_terminal.py`,
`test_rpc_child_terminal_lifecycle.py`, `test_rpc_terminal_keymap.py`,
`test_rpc_terminal_approval_revision.py`, `test_rpc_physical_editing.py`.
Tool handle62744 exited0; this is real integrated execution, not a sum of prior runs.

Action3 has current named-contract integrated evidence, but must be rerun after
any action1 implementation. Action1 remains unresolved: another bounded primary
Pi fetch returned connection reset (no fetched content, no latest/parity claim).
No undefined new raw surface was invented, no protected host input was exposed,
and UI-036 remains 待实现 until historical/reference API reconciliation is grounded.
This checkpoint does not substitute for UI-049/055/064 final whole-goal gates.

## 2026-10-11: finite primary-contract reconciliation and implementation

### Primary evidence, not an assumed upstream API

GitHub/raw GitHub/web requests again returned no usable content or connection reset.
A bounded **read-only package fetch**, not an installation or execution, recovered
primary published source from the npm registry:

- `@mariozechner/pi-coding-agent@0.73.1`, repository directory
  `packages/coding-agent`, manifest `gitHead`
  `781152fc24841dc54b22284514604048ebe5e2c9`.
- Its matching `@mariozechner/pi-tui@0.73.1` publication reports the same commit.
- Both downloaded archives matched the SHA-512 `dist.integrity` in their registry
  manifests. This checks archive integrity; it is not a claim of independent
  publisher-signature verification, an installed upstream runtime, latest-version
  conformance, or Pi wire compatibility.
- Local retained sources/manifests: `/tmp/ui036-reference-20261011/`.
  Archive SHA-256:
  - coding-agent: `7bf5d492670c04fd7c599dee7e6eaabff964084affd216766107e6741df7a2e1`
  - tui: `f0613239611aeb923fa9482b12366d95b884beb32c1b6f85d57e9686c448d541`

The concrete source chain is `types.d.ts` (`TerminalInputHandler`,
`ExtensionUIContext.onTerminalInput`, lines 49–77), `interactive-mode.js`
(`addExtensionTerminalInputListener`, lines 1499–1511), and `tui.js`
(`addInputListener`, lines 291–298; `handleInput`, lines 378–428).
`terminal.js` forwards framed key data and bracketed paste, consumes keyboard
negotiation replies, and supplies resize separately to the TUI.

The reference listener supports continue/consume/transform, ordered listeners and
unsubscribe. It executes **before** the focused-component release filter. Therefore
key-release **observation** was a concrete missing applicable behavior; silently
calling an edit-only hook equivalent to the upstream raw listener would be wrong.
The author-declared listener is interactive-only; XDOG's parent/child RPC input-data
services remain its own negotiated protocol, not claimed upstream parity.

### Mapping every applicable named contract

| Reference/historical contract | Actual XDOG contract / disposition |
| --- | --- |
| Continue, consume, transform, ordered listener chain, unsubscribe | Existing worker/runtime chain retained; explicit release observers continue across workers unless a callback consumes. Stale unsubscribe cannot remove a later registration of the same callback. |
| Original terminal key encoding, including enhanced repeat | Existing native parent and selected-child editable key spans; RPC editable data checks both input/output with current configured bindings and effective shortcuts. |
| Release reaches raw listener before component edit filtering | **Implemented:** separately opted-in observation of encoded editable-key releases on parent prompt and selected-child steer/follow-up composer. Legacy hooks still receive zero releases. No release can become a press, edit, submission or terminal write. |
| Bracketed paste data | Existing atomic `paste` kind; paste containing release-looking escapes is still paste, never a release event or control stream. |
| Focused custom component keyboard/paste, enhanced press/repeat/release | Existing named custom-view protocol, scoped worker/component and independent negotiation, already counted under UI-042. No duplicate raw observer or second callback pass is installed there. |
| Resize | Reference resize callback is separate from raw input; XDOG custom resize is already UI-042, while physical prompt resize revokes queued/held input leases. No invented raw `resize` event is claimed. |
| Focus and owner changes | Host lease revocation, not a raw callback with borrowed authority. Session/child/form/connection/worker handoff cannot redirect a delayed result. |
| Approval, confirmation/dialog controls, navigation/start/settings and recovery | Existing host-owned restrictions retained, as required by the original bounded input audit. Raw hooks do not intercept protected controls or inherit another surface's grants. |
| Terminal reports, graphics, unknown control data / arbitrary terminal writer | Protocol filtering and host key classification keep these outside extension prompt dispatch. The broader upstream unfiltered listener is **not** claimed as unrestricted byte-for-byte authority. No writer is granted. |
| Print/headless | No terminal surface is invented. The exported runtime data primitive alone does not create a focused editor or permission authority. |

There is no additional named mouse/focus/raw-resize registration in the inspected
reference interface. These are not silently marked implemented; the supported
contracts above, existing custom protocol, and explicit protected-host boundary
are the finite comparison. The old unspecified “broader raw” statement must not
be repeatedly reintroduced after this concrete reconciliation.

### New public/transport contract

```python
# Existing default remains key + paste only.
api.on_terminal_input(callback)

# Only explicitly registered event kinds invoke this callback.
unsubscribe = api.on_terminal_input(callback, events=('key', 'paste', 'release'))
```

`release` contains the original encoded release and the same bounded `surface`
metadata (`prompt` or `child-prompt`). Callback `None` / `consume: false` continues
observers; `consume: true` stops that chain. **Any `data` result is invalid for a
release, including a result that also says consume.** The final host-facing release
receipt is consumed, contains no editing data, and can never be restored as draft
text through terminal recovery. Native interceptors preserve bounded inspectable
originals on errors and cancel pending submission controls; RPC returns an explicit
failure without editing data. Neither path automatically replays input.

Registration is bounded and explicit. Live add/remove metadata is propagated from
the current worker; no restart is needed. Event declarations use `terminal_events`
in runtime metadata/snapshots and `events` in child terminal registries. Legacy
empty/default snapshots retain their earlier shape; absent event declarations do
not opt a host into release dispatch.

| Host | Additional opt-in | Scope and revocation |
| --- | --- | --- |
| Coding native | Current owning runtime registers `release` | `PromptTerminalInterceptor.release_available`; parent or the selected child's own runtime. Physical focus/resize, child/form changes, pause/cancel and registry withdrawal invalidate held input. |
| Claw native/gateway | `terminal_release_input: 1` **and** owning worker registers `release` | Independent exact-version negotiation, connection-owner rotation on change, existing session/worker/input sequence/child leases; old peers cannot send release jobs. |
| Coding RPC parent | `terminal_release_input: 1`, existing `terminal_input: 1`, owning registration | Current terminal owner/session/revision/worker guards; withdrawal cancels the owned job, single failure receipt, no data replay. |
| Coding RPC child | `child_terminal_release_input: 1`, existing child controls/input grants, owning registration | Current child owner/epoch/worker and terminal capability generation; withdrawal cancels the job without submitting child input. |

Native and RPC release admission normalize the parsed phase **only for checking
protected bindings**, including remapped keys and effective extension shortcuts;
the original release encoding is what the observer receives. Press/repeat edits,
paste and release observation remain different operations. Existing approval/dialog
pre- and post-admission fences apply unchanged.

### Verification checkpoint and integration boundary

Implementation and focused evidence are complete; final combined current-source
input verification is recorded below when its process exits. The central ledger
is MAIN-owned and was not edited by this worker. Whole-goal Linux safety/full-root
freeze gates remain independent; non-Linux native verification is still the only
already-deferred platform scope, not newly waived by this work.

- First combined named runtime/shared/native/gateway/RPC/lifecycle/keymap/approval/
  physical-RPC matrix: **259 passed**, no skips, **233.31s**,
  `/tmp/ui036-release-observer-integrated.log`.
- Custom/key/capability independence regression: **118 passed**, no skips,
  `/tmp/ui036-release-custom-boundaries.log`.
- Existing physical Linux Coding/Claw parent/child input regression: **6 passed**,
  **235 deselected**, **15.99s**, `/tmp/ui036-release-legacy-native-physical.log`.
- New actual native terminals, both hosts × parent/selected-child: **4 passed**,
  **10.09s**, `/tmp/ui036-release-native-physical-current-verified.log`. Original
  release bytes, zero editing, held-release Escape cancellation, safe parent draft
  recovery, no extra model admission, exact termios/native-buffer return are checked.
- Physical RPC editor data, parent/child × legacy/opt-in: **4 passed**, **12.51s**,
  `/tmp/ui036-release-observer-physical-current.log`. Uses the shipped interceptor
  and editor in the owned test frontend, not an arbitrary third-party client.
- Scoped strict type check: **381 source files**, clean,
  `/tmp/ui036-release-observer-static.log`; focused changed-path Ruff clean.

Earlier red logs remain retained. Isolated bare `pytest` invocation could not import
the repository's existing `packages.*` test helpers; root `uv run --no-sync python -m pytest` provides the correct root import path without installing anything. Early
new physical-test failures involved an off-viewport JSON field, a child not yet at
the model boundary, and matching stale pause text/returning before the child browser
had disappeared (letting Escape combine with the next key). The verified tests wait
for actual current UI states; no implementation fallback or safety assertion was
removed to make them pass.

The final-checkpoint precursor also caught one newly added fixture using hyphenated
command IDs, which the existing extension API correctly rejects. The fixture now
uses underscore IDs; its real-worker live-release unsubscribe/reregister check
passes (`/tmp/ui036-release-live-registry-current.log`). This was not a product
registration failure. Current all-seven strict type checking passes **520 source
files** (`/tmp/ui036-release-final-types.log`); the earlier unrelated Claw gateway
local-variable typing error was corrected by its owning worker.

### Final current named-contract gate (2026-10-11)

The final process exited **0**: **264 passed**, no skips, in **200.92s**,
`/tmp/ui036-release-current-verified.log` (tool handle 7853). This is one combined
execution, not a sum: runtime registration/release validators, shared FIFO/recovery,
stdin framing, both native hosts and Claw protocol, parent/child RPC and lifecycle,
configured keys/shortcuts, approval admission revisions, physical RPC legacy/opt-in,
and new physical native both-host parent/child release tests.

**UI-036's three finite actions are now implemented and verified at this named
contract boundary.** No concrete remaining raw-input contract from this audit is
open. MAIN may reconcile the stable ledger status; this worker did not edit it.
Whole-goal freeze/full-root/Linux safety gates remain required and independent.
