# Claw optional shared Goal cutover — verification

Date: 2026-10-19. This is a runtime cutover milestone, **not completion of the
assistant-core objective**. Binding scope is the approved assistant-core design
and its47-item acceptance file, not the retired Claw Goal feature checklist.

## Actual runtime boundary

- The optional package supplies the built-in `goals` skill and capability
  automatically in both hosts. No Goal enable setting remains. Without the
  distribution, ordinary chat/tools/memory/history open no Goal journal or legacy
  Goal bytes. Skill discovery/loading uses the existing Flow/Eyes mechanism,
  without copying/registration. Availability never delegates work automatically.
- With the package installed, application startup opens Goal data and recovers
  committed notices without waiting for another human/TUI input. An active Goal
  alone constructs no Agent and causes no periodic model calls. The existing
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

## Task1 runtime evidence (before the user removed the Goal enable switch)

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

Task2 now isolates Claw `goals`, `eyes` and `acp` install extras. Agent also makes
its ACP SDK dependency optional; otherwise it leaked transitively into base Claw.
The existing search extra stays independent and all includes all install extras,
without creating any tasks/observers. Explicit absent Eyes/ACP requests fail with
an actionable install message before opening application services. Missing Goal
package is simply an ordinary assistant, not a fallback Goal implementation.

Cold installed evidence and final Task2 regression results are recorded below.

The same packaged Python Goal UI extension, generic authorized owner-bound
read-only views/TUI rendering, observer per-assistant-message report output and
cancellation integration, inert configuration cleanup, final47-item audit and
fresh-context whole-branch review remain required. The old native Goal display
is removed, but its shared extension replacement is not claimed implemented here.

## Task2 package-presence and cold installed evidence

The user subsequently removed the enable switch. Both hosts now detect the
installed package rather than global/project/session settings. Actual no-setting
ordinary-turn tests first failed with absent host/tool, then passed with the same
Goal capability and an empty Goal list/no autonomous children. Old flag-coercion
and project-toggle tests are retired because that setting no longer exists.
Installed skill discovery and actual user-operation authority remain independent.

Claw pending input recovery exposed concurrent startup waiters clearing their
shared initialization task before another waiter inspected it. The unchanged real
input-runtime test failed and its durable receipt recorded `NoneType.done`; the
host now captures/settles the admitted task identity rather than rereading a
mutable field. The original persisted input is processed once with the same ID.

Retaining Coding's sole callback owner also exposed synchronous navigation
blocking a different live event loop behind an admitted producer. A safe
foreign-loop test reproduced failure before the fix. Synchronous controls now
reject non-owner async loops instead of blocking; actual async slash commands
submit their synchronous mutations to the existing controller. SDK async callers
can explicitly use `asyncio.to_thread` or owner operations. No second controller
or permission bypass is introduced. Test gates are thread-safe signals instead
of cross-thread asyncio.Events; writer/admission/readonly/queue checks remain.
Package-absent transient-owner tests retain that original lifecycle separately.

Seven wheels + seven sdists at `/data/goal-cutover-dist.My8qaP` pass Twine and
runtime/resource byte parity against source, including both wheel and sdist.
Runtime member counts AI53/Agent122/Goal17/TUI89/Coding98/Claw93/Eyes37; the
`source-parity.json` in that directory records exact wheel SHA256 identities.
Goal resources/py.typed and all runtime source files are included. No distribution
was published and versions are unchanged.

Actual consumers run from outside the checkout with no runtime source injection:

- Claw `/data/t.Vy5SaQ/claw-goal-installed-szr9qqms`: first installs base Claw with
  **no Goal/Eyes/ACP SDK installed or imported**, runs ordinary filesystem write,
  durable memory write, transcript and original legacy-byte retention checks.
  Explicit missing Eyes/ACP requests fail before service startup. Adding only the
  Goal wheel then runs actual worker/context/file/report, rotation, saved terminal
  execution lookup, explicit acceptance, independent readonly review, three Core
  Inbox notices, store lease release and application reopen without replay.
  Worker4/reviewer4/parent5 scripted calls; forbidden reviewer write absent.
- Coding and independent Goal-only runs use that **same Goal wheel**. Coding runs
  ordinary SDK input without Goal first, then actual default-ask worker/report/
  acceptance/independent-review/three durable notices; reviewer mutation is blocked.
  Goal-only uses two real supervised Agents, five scripted model calls and no
  installed/imported Claw/Coding/TUI/Eyes/Flow/site.
- Five wheel-only ACP environments exercise Eyes-only, generic Agent stdio/Unix,
  Coding ingress/owner, Claw Core Inbox/endpoint restart, generic output recovery
  and Native busy intake/cancellation. The ACP SDK is explicitly installed for
  these transport tests; it is no longer an incidental mandatory Agent dependency.

Logs: `goal-presence-final-installed-{claw,coding,kernel,acp}.log`,
`goal-presence-artifacts-{final-build,twine,parity}.log` under `/data/t.Vy5SaQ/`.
The first whole-repository package-presence snapshot was red and was stopped after
read-only diagnosis of the specific deadlocked owned test process. A subsequent
complete run found five async API/command adaptation failures; none is represented
as a successful final suite. Targeted owner/permission/prompt/lifecycle42 and
controller/inbox40 checks pass after the adaptation. Final fresh all9 evidence is
recorded separately once the run terminates successfully.

### Observed verification limitations

One full snapshot (`goal-presence-root9-verified.log`) observed the unchanged Agent
`test_owned_sigkill_history_feedback_and_queue_boundary_never_replays[after-checkpoint-True]`
missing its existing8-second subprocess boundary receipt deadline. No Agent runtime,
test or timeout was modified or relaxed; the full checkpoint file passes unchanged
in the combined81-case isolated regression. This is an observed reproduction/timing
limitation, **not a claim that an Agent crash-recovery defect was diagnosed/fixed**.
That same whole run's14 Coding failures came from incomplete old skill test doubles;
the helper now supplies real owner fields and production owner lookup is deferred
for read-only commands (including the existing help-without-session contract).
The combined skill/access/prompt/checkpoint81 suite passes. All failed snapshots
are retained separately, not counted as successful final evidence.

## Current final Task2 gates

- `TMPDIR=/data/t.Vy5SaQ PYTEST_ADDOPTS='-o faulthandler_timeout=45' uv run python scripts/test.py --jobs 2`:
  **all nine unfiltered paths green,6767 passed/34 skipped,156.81s**. Agent1892/15
  skipped, Eyes290, Goal63, Flow813, Coding1245/12 skipped, TUI686, AI729/7 skipped,
  Site42, Claw1007. Same package worker counts, tests and deadlines; only maximum
  simultaneous package processes is2 instead of the default4. Diagnostic
  faulthandler does not skip or relax a test. Log `goal-presence-root9-jobs2.log`.
- Strict9 mypy **587 files**, Ruff packages and all three Goal artifact verifiers,
  Flow shipped examples **17 passed**, lock/diff checks passed. Final affected
  Coding rebuild and14-artifact Twine/source parity were repeated after the last
  read-only dispatcher correction. No runtime source edit follows that parity.
- Latest same-wheel cold consumers: Claw/Coding/Goal-only/ACP logs named above;
  exact environment paths are printed in each final JSON receipt. They are
  independently installed and import runtime only from their environment prefix.

### Concurrent-run limitation retained

In addition to the Agent deadline observation, a default4-process full snapshot
(`goal-presence-root9-current.log`) observed
`test_unix_prompts_and_enqueue_share_one_durable_batch[cancel_waiter-False]`
missing its existing5-second response deadline. All14 unchanged ACP batch tests
then passed in isolation3.60s; the all9 --jobs2 run also passes every case. No
batch/cancel implementation, assertion or timeout was changed for that observation.
This verifies the complete suite at the recorded concurrency, **not a fix or
explanation of either default4 timing/reproduction failure**. Default4 behavior
must be reconsidered at final whole-objective audit/review, not hidden by claiming
those failed snapshots green. No test was deselected or xfailed.

Final cold environment receipts: Coding
`/data/t.Vy5SaQ/coding-goal-installed-kxawkdwm`, Goal-only
`/data/t.Vy5SaQ/goal-only-installed-weyfxtz6`, ACP environments
`/data/t.Vy5SaQ/xdog-acp-isolated.cok8w8uq`. These use the current parity-checked
artifacts, not the earlier Coding-stage artifact directory.
