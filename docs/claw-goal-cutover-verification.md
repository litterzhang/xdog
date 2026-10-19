# Claw optional shared Goal cutover — verification

Date: 2026-10-19. This is a runtime cutover milestone, **not completion of the
assistant-core objective**. Binding scope is the approved assistant-core design
and its47-item acceptance file, not the retired Claw Goal feature checklist.

## Actual runtime boundary

- Global strict `goals_enabled` defaults false. Ordinary Claw chat, tools, memory
  and transcript ownership remain Core responsibilities. Disabled Goal does not
  open its journal or read legacy Goal bytes. Installed `goals` is a built-in
  packaged skill through existing Agent namespace discovery, regardless of this
  flag; no copying/registration or separate skill-discovery framework exists.
- Explicit enabled application startup opens the Goal data capability and recovers
  already committed notices without waiting for another human/TUI input. An active
  Goal alone constructs no Agent and causes no periodic model calls. The existing
  actual session receives its bound tool when needed.
- `ClawGoalHost` uses the same GoalService/GoalStore/role tools as Coding. Its
  owner is stable Core inbox namespace/group/canonical workspace; Coding remains
  exact session/branch. The journal is independently leased under the group data
  directory, not Core input/history tables. Legacy `goals.json` is refused without
  creating an empty replacement journal or changing the legacy file.
- Actual ChildSupervisor workers/reviewers receive single-use host admission and
  tools bound to the actual execution and selected tool-object identity. Labels
  do not grant authority. Narrow readonly report/context calls do not grant file
  writes or bypass existing MCP/extension checks. Human operation authority follows
  actual identified consumed human messages, not canonical source/provenance text.
- Submitted report, accepted task and completed Goal are distinct. Only explicit
  `request_complete` requests independent review. Status, wait, idle and turn-end
  do not request review. Notification, observer and worker cancellation remain
  separate explicit actions; there is no Goal-to-Eyes file watcher projection.
- Notices enter the existing application-owned durable Inbox with stable IDs and
  exact content checks. No ACP or CLI callback, new main Agent/controller/tool
  loop or message approval. Headless recovery uses that same input owner.
- Context rotation does not transfer ownership or veto on unfinished Goals. Actual
  active children still block generic context replacement. Saved original worker
  refs resolve terminal facts from validated off-loop ChildStore data in their
  original group transcript/branch, without constructing or replaying an Agent.
- Shutdown joins already admitted capability opening before closing input/storage,
  then joins real children before closing Goal. Once stopping, application startup
  cannot open later group capabilities or begin later channel connections.

The former GoalManager/Tracker/review/workspace/report/notification modules,
Claw-specific Goal tools and role resources, Gateway Goal timer/jobs, turn-end
wrapper, prompt summary and native Goal UI have been removed. Native generic todo
and extension/tool UI remain. Historical transcript source labels remain read-only
history metadata, not an executable compatibility backend.

## Replaced tests and retained coverage

These tests described a deliberately removed public surface; they are not merely
excluded from collection to make a failing suite green:

| Retired test files (`packages/claw/tests/`) | Replacement or deliberate removal |
| --- | --- |
| `test_goals.py`, `test_task_reports.py`, `test_goal_journal_safety.py` | Independent `packages/goal/tests/test_{store,service,tools,review,notifications}.py`: strict journals, reports, revisions, evidence, explicit completion and outbox; actual Claw `test_goal_host.py` lifecycle |
| `test_goal_process_recovery.py`, `test_goal_context_rotation_receipts.py` | `test_goal_host_recovery.py`: original real terminal worker refs across rotation/reopen, group isolation, byte-preserving legacy refusal and real leased open/close settlement |
| `test_group_goal_gate.py`, `test_goal_user_steering.py` | Actual Claw host human-vs-canonical and identified-vs-anonymous consumed steering tests; readonly bound-report test; independent Goal tools/review tests; existing generic child/extension/MCP/permission tests remain |
| `test_gateway_goal_wake.py`, `test_goal_job_dispatch.py` | Implicit planner/tick behavior intentionally removed. New ordinary-status/no-review, headless persisted-notice startup recovery and no-Agent/no-polling tests exercise the requested replacement |
| `test_goal_owned_observers.py`, `test_goal_eyes_end_to_end.py` | Goal-owned watcher creation/implicit restoration/projection intentionally removed. New independent Goal/source/notification controls test, existing managed Eyes/observer recipient/report/intake suites remain |
| `test_claw_goals_packaging.py` | Retired Claw skill/roles are no longer shipped. Goal package actual discovery/loading and role consumption tests cover new resources; cold installed Claw two-host packaging evidence follows separately |

`test_group_context_controls.py` keeps real generic child-busy, workspace,
transcript and rotation checks, removing unfinished-Goal veto assertions. Existing
Eyes tests keep actual source/input/report assertions while dropping old Goal tick
calls. Native inline tests retain generic todo/layout behavior, not a nonexistent
Goal widget. No noop GoalManager/tick facade is kept for obsolete tests.

## Evidence for this runtime milestone

Only model transport is scripted in the new host tests; GroupRuntime,
Orchestrator, Core Inbox, GroupController, ChildSupervisor/factory, filesystem,
report/context tools, Goal storage and independent review are real.

- Actual Claw host/recovery: **27 passed**,9.58s. Includes a real GoalStore.open
  paused after SQLite lease acquisition: single/multiple-group RED2 demonstrated
  premature shutdown; joining admitted initialization and fencing late admission
  made both pass, and the same real lease reopens after stop.
- Full root runner, all nine configured test paths: **6775 passed,34 skipped**,
 134.25s (`claw-goal-cutover-root9-current.log`). Claw1010, Coding1250/12 skipped,
  Agent1892/15 skipped, Flow813, AI729/7 skipped, TUI686, Site42, Eyes290, Goal63.
  This run includes the race fix; subsequent unused native icon/header deletions
  and package-discovery assertions are additionally checked by focused suites.
- Strict mypy across all9 packages: **587 source files**, no issues. Ruff packages
  and existing Goal/Coding artifact verifiers passed. Flow shipped examples:
  **17 passed**. Lock and diff checks passed.

Full logs are local evidence under `/data/t.Vy5SaQ/`; no paid provider, actual user
service, user data changes, push, merge or publication was used.

## Not yet proven / remaining work

Claw base still declares mandatory Eyes/ACP dependencies at this milestone;
optional extras and cold installed Claw evidence are the next task. Previously
built artifacts are not claimed to match this modified Claw source. Rebuild and
run the same Goal wheel through both actual hosts before accepting packaging.

The same packaged Python Goal UI extension, generic authorized owner-bound
read-only views/TUI rendering, observer per-assistant-message report output and
cancellation integration, inert configuration cleanup, final47-item audit and
fresh-context whole-branch review remain required. The old native Goal display
is removed, but its shared extension replacement is not claimed implemented here.
