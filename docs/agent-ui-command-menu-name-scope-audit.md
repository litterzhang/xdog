# UI-041 finite command-menu and after-run naming closeout

Checkpoint: **2026-10-11**, sidecar implementation stopped before the requested final
freeze. The central ledger is unchanged by this document. The historical comparison
baseline remains **2026-10-09**, supplemented by the publisher Pi0.73.1 tarball whose
integrity and gitHead are recorded in `agent-ui-sidecar-reconciliation-2026-10-11.md`.
No latest-release, installed Pi, or vendor wire-compatibility claim is made.

## Read-only actual host menu

Pi's published `getCommands` is an extension-wide action, not a command-only context
method. The finite XDOG delta is implemented through the existing `get_command_catalog`
API and a **separate `ExtensionRuntime.command_menu_handler`**, default unavailable.
This is the actual host's terminal-menu metadata, not a promise of identical Pi rows or
that every terminal command can execute through another API.

- Exact parent command/tool/event/pre/post/input/context/custom-input admission; caller
  context cannot forge the host flag, original invocation, session, or child identity.
- Existing command fallback remains compatible. General reads require the independent
  handler; editor/session dialog grants alone do not provide this read capability.
- Provider, setup, raw-terminal interception, custom-render, and silent/headless paths
  are unavailable. A child receives **no synthesized parent menu**: no native/RPC child
  binding is installed. A separately supplied child-owned SDK menu is validated as that
  child's explicit binding, never inferred from its parent's slash commands.
- All reads keep bounded32-entry pages, query/membership revisions, copied results and
  original handler/session/invocation revalidation after awaits. No command executes,
  no composer is read, and no tool permission or session mutation is granted.
- Coding native reads its actual menu provider. RPC's independently negotiated
  `extension_command_catalog` reads the actual client's supplied menu, not server guesses.
- Claw binds a `(alive, generation)` menu host to the actual parent run or command and
  restores the old handler afterward. Its menu grant is independent from session-mutation
  support. Wrong-session/group replies, another connection, source changes and
  withdrawal/re-enable are refused without adopting a new owner.

### Custom input focus bug found during verification

The initial native custom-input tests stalled after the nested metadata read. The
read-only request passed through normal modal replacement and unfocused the suspended
custom parent; the terminal still referenced that same object, so ordinary focus
restoration did not reactivate it. Both native hosts now service **only this read-only
menu case** before deactivating the parent panel. This does not weaken nested broker
ownership or painting fences. RPC still retires the prior frame receipt after nested
metadata; the test explicitly repaints and reconfirms rather than reusing a stale key.

Evidence: new Agent `test_extension_menu_scope.py`, Coding `test_rpc_menu_scope.py`
(including an actual CLI subprocess), `test_menu_scope_ui.py` (both native hosts), Claw
`test_command_menu_scope.py`, and `fixtures/extensions/menureader/`. The old menu denial
assertion now explicitly removes only the new independent handler, proving that old
editor/dialog grants cannot substitute for it; the denial assertion was not deleted.

Final selected menu/old-capability/custom regression gate: **108 passed, zero skips,
106.18s**, `/tmp/ui-sidecar-20261011/menu-integrated.log` and `menu-integrated.xml`.
Initial fixture mistakes (selecting the wrong fixture tool, omitting the RPC session ID,
waiting for a nonexistent `turn_end` instead of `message_end`, and incorrectly forbidding
metadata in an explicit ToolResult) are retained in earlier logs, not production defects.
The real native focus failure and its narrow fix are recorded separately.

## Reviewed original-parent naming after producer settlement

The published Pi name setter is also extension-wide. The former command-only XDOG
review remains intact; `set_session_name` now additionally supports the explicitly bound
**original parent tool/hook** proposal. It never renames a parent from a child, and does
not provide model/history/composer mutation or a second tool loop.

The new name path has its own `name_request_handler`, exact method admission, bounded
canonical proposed/expected names and `name_after_run` marker. The original captured name
is read-only invocation metadata. The full boolean review includes both names and warns
that callback completion is not producer settlement. `True` means **staged**, not saved.

`AfterRunNames` stages through the generic `InvocationLease.defer` only after callback
cleanup and success. Typed tool `isError: true`, callback error, owner change or cancellation
cannot stage a successful action. `take()` is single-use and requires the host-supplied
original producer to be settled **and successful**. No action is encoded as a fake
compaction request.

### Actual host barriers and persistence

- **Coding native:** observes the same original producer record as the after-run
  compaction machinery, regardless of whether compaction was requested. It waits for
  the original worker thread to exit and for its success receipt. The existing native
  `/name` command then performs expected-name CAS with an original-owner guard.
- **Coding RPC:** independent default-off `extension_name_after_run`; not the existing
  command-only name capability and not a compaction grant. It observes the actual RPC
  task/outcome and waits for the Agent producer cleanup. Native `RpcNavigation.rename`
  starts only after task settlement, original ownership checks and absence of another
  busy host control (including after-run active-tool selection). `session_result` is the
  persistence receipt, not the extension callback's boolean.
- **Claw socket/native:** independent name capability/generation. Naming observes the
  shared original gateway producer record even with compaction disabled, but uses its
  own name queue/authorization. It waits for task success and release of the original
  group reservation, then acquires the normal name-control reservation and calls
  canonical `Orchestrator.rename_session` with expected-name and revocable-owner guards.
  `extension_name_result` reports the actual outcome. Closing the connection prevents
  late observer jobs from being started.
- Coding's canonical `rename_session` gained an optional owner guard checked again
  immediately before atomic replacement. Existing calls without that argument retain
  their old path. Claw forwards its guard through the existing store path. A revocation
  before replacement leaves the original name/transcript intact; once a write is admitted,
  no imaginary rollback or automatic replay is claimed.

The name-only client does not acquire editor, navigation, active-tool, compaction, or
child authority. A new producer/changed original owner invalidates pending work. If a
competing host control wins admission, a staged name can be discarded instead of writing
concurrently with it. Parent drafts and final transcript content remain untouched.

### Naming evidence and limitations

- **37 passed**, 37.73s, `name-current.log`: real worker scopes/tool errors, parent-only
  ownership, native/RPC/Claw producer barriers, failed callback/producer, withdrawal,
  name conflict, actual CLI name-only negotiation/persistence, and before-replacement
  revocation preserving the final transcript.
- **4 physical Linux cases passed**, 12.32s, `name-physical.log`: both native hosts,
  approve/deny, full review and resize, exactly one original model call (no summary or
  second turn), unchanged transcript, restored draft cursor/main buffer/termios.
- Final selected new-name plus existing naming/generation regression gate:
  **55 passed, zero skips,57.84s**, `name-final.log` and `name-final.xml`.
- Latest recorded strict seven-namespace mypy: **548 source files clean**. Root Ruff at
  that checkpoint reported six other workers' pending formatting diagnostics; those
  owners were notified, not silently edited or described as clean.

Earlier intermediate logs include missing fixture name metadata, an independent name-only
UI availability omission, a stale completed-navigation cancellation attempt, and worker
startup failures while source was being edited. These are retained as failed checkpoints.
The later same-scope gates passed; none is presented as a frozen whole-root result.

## Remaining ownership

The two finite UI-041 positive deltas above are implemented and have the listed current
scope evidence. Existing editor-write/name/menu/catalog/browser slices are not repeated
as open-ended parity work. Main owns any central status change and the final frozen
Linux terminal/safety/transport, full-root skip mapping, static/artifact/example gates.
UI-043 after-run compaction and active-tool selection, messages/context/queues remain
with their respective owners, not implicitly completed by this name integration.

The five exact external prerequisites and the non-Linux native deferrals are unchanged;
see the reconciliation sidecar and authoritative ledger. No dependency installation,
live provider/authentication, publishing, user-service restart, or full-root pytest was
performed by this sidecar.
