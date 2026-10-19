# Optional Goals in Coding

The assistant-core development branch provides a `goals` install extra and a
strict global `goals_enabled` boolean. This capability is not enabled by merely
installing/importing Coding or Goal, loading a skill, opening a TUI, or receiving
an ACP callback. It does not turn ordinary requests into long-term delegation.
These changes are local development work, not a published release.

In Coding's trusted global settings (`$XDG_CONFIG_HOME/xdog/coding/settings.json`,
or `$CODING_DIR/settings.json`), opt in explicitly:

```json
{"goals_enabled": true}
```

The Goal distribution must also be installed (the Coding `goals` extra declares
that optional dependency). Strings/numbers/null are rejected rather than coerced;
project settings and saved session JSON cannot enable the capability. The shipped
session-scoped `goals` skill is automatically discovered whenever the package is
installed, just like Flow/Eyes, independently of `goals_enabled`. No copying or
manual registration is needed; the normal skill mechanism loads its full body
on demand. It describes the explicit workflow. Loading instructions
is not authorization to create a Goal or to run tools.

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
