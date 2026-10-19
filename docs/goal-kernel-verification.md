# Portable Goal kernel — explicit review and role verification

Date: 2026-10-19. This is a kernel milestone, not completion of the assistant-core refactor.

## Delivered

- GoalService starts a reviewer only on explicit `request_complete`. Ordinary
  status, idle, task reporting and turn end cannot complete a Goal. The independent
  execution must return ordered findings for every original criterion, actual
  evidence and reasons; a script exit code or parent verdict is insufficient.
- Goal owns bounded descriptor-relative no-follow file fingerprints. Changed
  evidence/criteria/attempts, unsafe paths, symlinks and capacity overflow reject
  completion. Invalidated reviewers retain their actual execution references;
  replacement requires settlement or explicit cancellation, not implicit cleanup.
- Worker `task_report` and role `goal_context` bind immutable attempt/review scope.
  Context reads original authoritative history, never mutable derived workspace
  files. Parent tools expose explicit actions, not worker impersonation or raw
  review verdict submission. Host-provided user authority cannot be supplied in JSON.
- Session-scoped packaged skill and separate worker/reviewer instructions load
  without creating storage, activating a host or starting a model. Derived views
  are off-loop disposable projections; failures cannot replace authoritative facts.
- AgentToolResult.from_text is a small typed public helper, allowing Goal tools to
  depend directly only on Agent/stdlib rather than importing AI internals.

## Evidence

- Focused Goal62 + Agent result helper5: **67 passed** (5.43s).
  `/data/t.Vy5SaQ/goal-kernel-final-focused.log`.
- Root nine-package runner: **6967 passed,34 skipped**,130.90s, all9 paths completed.
  `/data/t.Vy5SaQ/goal-kernel-final-root9.log`.
- Strict nine-namespace mypy: **592 source files**, no issues.
  `/data/t.Vy5SaQ/goal-kernel-final-strict9.log`.
- Ruff packages/artifact verifier, lock check, diff check; **17 Flow examples passed**.
- Rebuilt AI/Agent/Goal wheels **and** sdists in `/data/goaldist.QAIsm5`;
  all6 pass Twine, runtime/resource source-byte parity (53/122/17 members),
  Goal Requires-Dist only Agent, py.typed and skill/role resources present.
  Initial Twine glob accidentally included source-parity.json; corrected to explicit
  wheel/sdist patterns and all6 checked successfully; no artifact fault suppressed.
- Fresh wheel-only environment `/data/t.Vy5SaQ/goal-only-installed-eayyhblh`:
  actual leased storage/history/recovery; then real Agent/ChildSupervisor/tool IO,
  explicit task acceptance, separate independent reviewer, completed Goal, accepted
  notices and derived views. **Two distinct Agents, five scripted model calls**;
  zero provider requests. No Claw/Coding/TUI/Eyes/Flow/site installed or imported.
  `/data/t.Vy5SaQ/goal-kernel-installed-final.log`.

The external consumer host copied by the artifact verifier is test/example code,
not a second Agent loop inside Goal. This proves portability of the kernel and
SDK integration, **not** real Claw/Coding adapter adoption.

## Still required

Actual opt-in Claw and Coding adapters with unchanged tool policies; removal of
active legacy Claw GoalManager/Tracker/implicit turn-end review; same Python Goal
UI extension through generic owner-bound read-only views; native Goal UI removal;
observer per-assistant-message output integration; full47 acceptance audit and a
fresh-context whole-branch review. The overall goal remains active. No push,
merge, publish, provider access or user service/data changes were performed.
