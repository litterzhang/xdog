# UI-043 original-parent after-run active-tool selection (2026-10-11)

Subordinate scope/evidence only; the central ledger remains MAIN-owned. No blanket
Pi API or transport compatibility claim is made.

## Positive contract

The pinned Pi interface inspected for this work declares extension-wide
`setActiveTools`. XDOG's existing command-only mutation path was insufficient for
an original parent tool/hook request. The new path supports tool, pre/post-tool,
submitted-input, event and context-transform callbacks through an independently
installed `active_tools_request_handler` and exact host feature flag. Terminal,
provider/auth, custom-render/input and child callbacks do not borrow that grant.

`set_active_tools(names, revision)` is an explicit full-review proposal. For a
non-command callback it sends `after_run: true`; True means staged, never applied.
`get_active_tools()` can reuse the already-owned invocation tool inventory's
captured configured/active names and selection revision outside commands. This is
explicitly an admission snapshot, not a new live body-bearing dialog or an
inspection of another Agent. Existing command reads retain their host semantics.

`AfterRunToolSelections` creates no producer/model/tool loop. It receives the
existing host producer identity and `alive`, `settled`, `successful` proofs used
by the after-run machinery. An invocation lease stages the proposal only after
callback success. `take()` releases it at most once, only after the **whole original
producer** has settled successfully. The host then applies its existing idle,
durable, revision-checked selection path. A callback returning successfully, an
AgentEnd event, or a temporarily idle Agent alone is not producer settlement.

The review includes exact configured/current/requested names and original revision.
Unknown names, forged host projections, changed inventory/revision, worker/session/
producer handoff, callback failure/isError, producer failure/cancellation,
capability withdrawal and failed persistence cannot authorize a stale selection.
Existing permission policy and child-profile ceilings remain unchanged. Explicit
reset-to-default never means restoring names withdrawn from the configured
inventory.

## Host integration

- Coding native/RPC helpers and host hooks are MAIN-owned and use their existing
  original worker-thread / prompt-task + Agent-producer settlement barriers and
  canonical durable selection CAS.
- Claw `after_run_tools.py` reuses the existing after-run connection lifecycle and
  shared gateway producer observer; it never acquires compaction or naming
  authority. Its grant is independently negotiated as
  `extension_active_tools_after_run`, with its own generation and tools-only
  pending-review cancellation. The original gateway task and Agent producer must
  finish and release their group reservation before a new reserved idle selection
  commit calls `AgentSession.select_active_tools`.
- Claw tests explicitly disable both the compaction grant and the old command
  active-tools grant while proving the new reviewed parent-run selection works.
  Native dialogs retain whole-proposal, focus/paint and repeat-key safety checks.
- Legacy `toolselector.illegal` fixture now accepts a valid original captured read
  when available and checks its session/revision against tool inventory. It still
  asserts mutation refusal when no reviewed after-run handler was granted.

## Finite child limitation

An independent child may inspect only its own captured, profile-filtered configured
and active names/revision. This patch does **not** grant child direct selection.
The current supervised child is a one-task ephemeral Agent: canonical settlement
captures recovery, releases the Agent, and closes its extension owner; there is no
idle durable child-selection host for a post-producer CAS. Mutating the parent,
resurrecting a completed child, or restoring names dropped by its parent ceiling
would not implement that operation safely. Existing child spawn/profile controls
remain explicit and bounded by the parent ceiling. A new persistent child
reconfiguration contract is not silently claimed, invented or deferred under a
vague parity label.

## Focused verification

- Shared helper/runtime/worker/Agent barrier: **43 passed**, **12.64s**,
  `/tmp/ui043-active-tools-shared-current.log`. Includes real original Agent tool
  execution followed by additional model work and an explicitly held outer
  producer-cleanup barrier; the old idle CAS refuses mid-run mutation.
- Real Claw socket/UI/worker/producer/storage paths: **8 passed**, **15.07s**,
  `/tmp/ui043-active-tools-claw-first.log`: apply, deny, withdrawal, producer error,
  callback failure, isError, inventory drift and durable-save failure.
- A combined current gate includes these groups, MAIN's native/RPC host cases,
  original active-tool selection tests, both hosts' durable selection suites,
  command UI regressions and after-run compaction independence. Its final result
  is recorded below after process completion.

Earlier shared red evidence is retained: the first edit did not match the existing
API text and left the setter command-only, and its new handler was initially
missing from the `has_ui` calculation. Both were corrected in the owned regions.
The actual-Agent test also needed a final result future on its synthetic provider
stream; the corrected test preserves the full cleanup barrier and idle-CAS
assertions. No safety assertion or test expectation about mutation refusal was
weakened.

## Final slice handoff (16:53 +08)

The combined current named-contract gate exited **0** with **152 passed, no skips,
99.80s**, `/tmp/ui043-active-tools-integrated.log` (tool handle 94007). It includes
shared callbacks and the real Agent cleanup barrier, Coding native/RPC producer
hosts, Claw actual socket/UI/storage paths, original selection tests, both hosts'
durable selection and command UI regressions, and after-run compaction independence.

Focused changed-path Ruff is clean:
`/tmp/ui043-active-tools-ruff-current.log`. The all-seven strict check examined
**548 source files** and reported only two errors outside this slice in
`coding/core/compaction/compaction.py:94–95` (a callable/string `text` local-variable
collision); these were handed to MAIN rather than edited outside ownership.
`/tmp/ui043-active-tools-final-static.log` retains that result. A test-line wrapping
lint error from the same checkpoint was corrected and Ruff rerun clean; no behavior
or assertion changed.

Implementation is frozen for integration before 17:00. The parent callback
selection contract is implemented and verified at this boundary. Direct ephemeral
child durable selection remains the precise limitation described above; it is not
silently redirected to the parent. The central ledger and final full-root/static/
build/example gates remain MAIN-owned. No dependency install, live-service restart,
unrelated process interruption, or worktree reset was performed for this slice.

At **16:55:41 +08**, the repeated all-seven strict check is **clean, 548 source
files** (`/tmp/ui043-active-tools-handoff-types.log`), after MAIN resolved the
previously reported out-of-scope compaction typing error. No active-tools source
change was needed. The earlier red log remains historical evidence, not a current
blocker.
