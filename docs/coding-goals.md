# Optional Goals in Coding

Coding reuses the optional `xdog-skills` distribution through its `skills` extra:

```bash
pip install 'xdog-coding[skills]'
```

There is **no Goal enable setting**. If the package is installed, its built-in
`goal` skill is automatically discovered like Flow/Eyes and the actual session
owner attaches the Goal tool/data capability. No copying, registration, project
setting or saved-session toggle is needed. Without the package, ordinary Coding
chat/tools/history work without importing Goal or opening its journal.

The existing skill mechanism loads full session-scoped instructions on demand.
Installation, skill loading and tool availability are not user delegation to start
a Goal, worker or independent reviewer. Normal chat never silently becomes a Goal.
The same session controller owns initialization, committed-notice recovery and
teardown; import or opening a UI/ACP ingress does not create another owner.
These changes are local development work, not a published release.

## Execution and storage

The parent uses the packaged Goal tool to create a delegated contract, add tasks,
assign existing supervised children, inspect and explicitly accept reports, wait,
and explicitly request independent completion review. Submitted reports are not
accepted tasks, and accepted tasks are not completed Goals. Status/idle/turn end
never starts an independent reviewer. A reviewer has actual readonly tool access
and must cover every original acceptance condition with fresh evidence.

Goal owns a separate private leased journal under the session data parent's
`goals/` directory, bound to the durable inbox namespace, exact session, branch
and workspace. Coding owns ordinary input, execution receipts and history, not
Goal state tables. Unknown/old/corrupt/wrong-owner Goal data is refused, not
upgraded, imported or silently replaced. Derived workspace views are disposable.

Notifications always enter the existing durable session Inbox with stable IDs
and canonical-content checking. They are consumed without message approval and
can wait while the parent is busy or no TUI is open. Their source labels and
provenance never grant user authority. The normal Coding tool policy is unchanged:
default ask/readonly remains fail-closed for mutation; unattended mutation requires
explicit allow-all. Bound report/context capabilities cannot authorize filesystem,
MCP or extension execution. A queued callback cannot revise criteria or release a
user pause just by claiming to be a user.

## Lifetime

The same SessionController remains the sole execution owner. When the optional
business capability is opened, SDK execution uses that existing controller's
supported private callback loop and retains it across individual SDK awaits. It
is not a second Agent loop, daemon, CLI callback or receiver. SDK callers must
`dispose()` their AgentSession when finished; actual children and Goal commits
settle before its owner loop/lease is released.

Idle navigation is allowed even with unfinished stored Goals; old Goals do not
move into a new session or branch. Running/settling actual resources still need
the existing cancel/wait discipline. On reopen, known terminal execution records
can support explicit acceptance without worker replay; unknown/crashed work is
interrupted and never automatically re-executed. UI visibility/unload does not own
business execution. Goal state, worker cancellation, observation cancellation and
notification cancellation remain separate explicit operations.

## Verification and remaining cutover

`scripts/verify_coding_goal_artifact.py <distribution-directory>` verifies actual
wheel-only Coding execution, first without Goal installed, then with the optional
Goal wheel. Only model transport is scripted; child Agents, tool IO/permissions,
Goal storage, independent review, durable notifications and session intake are real.
See `docs/coding-goal-adapter-verification.md` for recorded evidence. No provider,
user service, real credentials or receiver is used.

Claw's equivalent adapter/legacy Goal removal, shared read-only extension views,
same packaged Goal Python UI extension and final architecture acceptance remain
separate required milestones of this refactor; this document does not claim them.

Synchronous session-control methods called from a live non-owner async event loop
reject rather than block that loop behind admitted work. Submit an owner control
or use `await asyncio.to_thread(session.restore_branch, branch_id)` as appropriate;
this does not create another Agent/controller. Shutdown/disposal still waits for
the existing controller, children and actual Goal commits to settle.

## Optional shared display

With the Goal package installed, list `goal_ui` in trusted global `extensions`
(the same existing extension configuration, not another Goal enable flag):

```json
{"extensions": ["goal_ui"]}
```

The package resource is loaded directly; no copying or registration is needed.
`/goals [GOAL_ID | page OFFSET]` queries it, while `/goal` remains the
built-in skill command. The actual session/branch owns its read-only view. Existing
context navigation clears/replaces the old generic extension runtime rather than
moving Goal data. The main TUI uses its generic shared ExtensionView and has no
Goal widget or lifecycle policy. UI unload/failure affects display only; actual
workers, stored contracts and durable notices keep their independent lifetimes.
See `docs/shared-goal-extension-verification.md`; cold installed UI proof follows.
