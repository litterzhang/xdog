# Shared Goal UI — actual owner binding verification

Date: 2026-10-19. This records Task3 source/runtime verification. The same rebuilt
installed extension is still a separate Task4 gate; observer streaming/configuration
cleanup/final47-item audit and whole-branch review remain required.

## What actually runs

Coding and Claw adapters register the same `GoalView` and packaged `goal_ui` worker
on their real parent `ExtensionRuntime`. `GoalDisplay` receives only GoalService,
ViewRegistry, notify and validity ports: no whole harness/session/controller/TUI.
It bridges readonly storage to its original owning loop, fences scope changes,
and tracks/settles actual data reads before the business host releases storage.
It never owns or closes that service and never controls children/observers/notices.

- Coding binds exact original session/branch/workspace/inbox namespace. Existing
  navigation closes/replaces the original generic extension runtime; the new
  branch/session has its own Goal store and grant. Neither late reads nor cached
  old Goal output transfer to the new owner.
- Claw binds its current parent view to stable group-owned Goal data. Context
  rotation closes the old generic extension runtime, retires its data binding
  and binds the fresh parent view to the same service. Retired displays settle
  promptly and release their old conversation references, not only at app stop.
- Actual child factories construct independent extension runtimes. Reusing the
  same configured `goal_ui` ID does not copy the parent's registry grant. Child
  query returns unavailable, not the parent's Goal list.
- Committed Goal changes emit coalesced generic hints. Query/refresh calls no
  model and constructs no Agent. Both actual host snapshots render through the
  existing `xdog.tui.components.extension_view.ExtensionView`; there is no native
  Goal widget/schema/state branch. Generic renderer now normalizes line breaks
  before truncation so an untrusted status cannot inject terminal rows.

## Display failure is not business validation failure

A real running Claw child exposed a coupling: the old generic “any failed plugin”
validation veto treated a dead UI worker as a failed parent guard, preventing the
child report. The actual crash regression was RED before the fix.

An explicitly validated manifest can now declare `display_only: true`. This is a
restricted generic contribution profile, **not a Goal enable switch or a Goal-ID
exception**. The host refuses tools/model providers, pre/post/context/input and
non-display lifecycle hooks, conversation entries, rich host-action proposals and
session-control/export dialogs. Only text/query commands, session_start/end and
view_changed plus bounded declarative UI are available. No guard authority is
created and tool permissions are not relaxed. The declaration is read before
checking its entry module so an otherwise valid known display missing its code
remains a display failure. Unknown/unreadable/invalid identity/profile declarations
remain required/fail-closed; they are not silently classified as harmless UI.

Default and existing validation extensions remain required. Mixed-plugin tests
show that a failed display does not veto work, but the actual required guard still
blocks a write, and its failure still blocks even a read. New/late critical
registrations by a display are rejected before becoming callable. Goal's worker
contains only display contributions and declares this profile in its own manifest.

## Concrete tests

Only model transport is scripted. Existing SDK/Core Inbox/GroupController/Agent/
ChildSupervisor/factories, role context/filesystem/report tools, Goal journals,
actual stdio worker and generic TUI renderer are used.

- Actual Coding session **and branch** navigation refuse/read-clear old owner
  output; new store is genuinely empty. Claw rotation preserves original group
  Goal and displays it from the fresh parent only. Read/view queries leave model
  call counts unchanged and do not create actual child work.
- Both hosts unload and **kill the actual UI process** while a real Goal worker
  is held in a scripted transport gate. The actual child retains execution,
  reads the actual workspace output, submits its bound report, and original
  durable notices reach succeeded Core/Coding inbox receipts with no message
  approval. Goal remains active/unaccepted, not implicitly completed/cancelled.
- Those actual children query their own configured same-ID extension and cannot
  read the parent Goal view. UI failure/unload does not cancel/disable business
  Goal service or notifications; no filesystem permission weakening is used.
- Actual SQLite transaction thread is gated after a real read/connection cleanup.
  Both hosts begin real shutdown; the original Goal lease remains held and an
  independent opener gets BlockingIOError. Releasing the actual read lets close
  settle, after which the same real store can be reopened. Neither store close,
  data lease nor execution factory is mocked.
- A weak-reference test after actual old extension-thread settlement found a
  retired display retaining the old conversation until app shutdown (RED). Owned
  asynchronous retirement now joins its data reads and releases that reference.
- Standalone TUI tests use generic plugin rows only (no Goal/Agent import), testing
  zero/narrow widths, Unicode, terminal controls and clearing the current snapshot.

Focused actual owner/display/profile/renderer suite: **28 passed**,12.50s.
All9 unfiltered paths, `scripts/test.py --jobs 2`: **6836 passed,34 skipped**,
158.68s. Agent1929/15 skipped, Coding1251/12 skipped, Claw1013, TUI691, Goal78,
Flow813, AI729/7 skipped, Eyes290, Site42. Strict9 mypy594 source files and Ruff
packages pass. Final Flow/lock/diff gates are recorded with the task completion.

Logs under `/data/t.Vy5SaQ/`: `goal-ui-real-hosts-{red,green1}.log`,
`goal-ui-display-failure-red.log`, `goal-ui-display-profile-{red,bootstrap-red}.log`,
`goal-ui-retired-display-red-settled.log`, `goal-ui-bindings-{profile-current,root9,strict9-current}.log`.
Prior default4 concurrent-suite timing observations remain documented, not claimed
fixed; no test/timeouts/selection were relaxed here.

Root filesystem became full during a shell here-document before a test was
written; that attempt is not a RED test. Continued with Bash and temporary/cache
directories under `/data`, without deleting user caches/data. No user service,
paid model, push, merge, publication or version bump occurred.
