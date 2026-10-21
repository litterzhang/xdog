# Goal status and minimal intervention — 2026-10-21

## Scope

Fix the recent Claw audit without putting Goal business policy in Coding/Claw,
adding a second executor, changing tool permissions or altering a live Goal.
This applies to the shared installed Goal in both hosts. Native status remains
an external observation; the bound model tool addresses the executing main Agent.

| ID | Behavior and acceptance | Status |
|---|---|---|
| GSI-01 | A disabled Goal (user/host wait, pause, completed/cleared, or no current binding) must not advertise a historical deferred check as current delivery. Show `delivery=idle` with the reason and retain the check in `last_delivery`. Preserve actual accepted/pending/running/succeeded/unknown/unavailable facts, outbox IDs, wake signals and lifecycle. | Implemented, targeted tests passed |
| GSI-02 | Model `goal.status`/`summary` must not tell the executing main Agent to wait for itself. Suggest useful work for active Goals, or inspect evidence for a task wait. External status keeps actual host-busy/backpressure guidance. User/host waits, pauses, terminal states and unknown transport results still take precedence; reads never activate or grant permissions. | Implemented, targeted tests passed |
| GSI-03 | Claw's generic prompt must not require a parallel todo checklist. Prefer an existing authoritative task list; Goal skill identifies its task files as authority. No Goal-specific dispatch or schema is added to Claw. | Implemented, targeted tests passed |
| GSI-04 | Skill prioritizes existing materials, independent work and permitted alternatives; a blocked task alone does not imply a whole-Goal wait. Ask one consolidated business/material question only when genuinely necessary. Approval belongs to the existing host system, never an extra Goal request; a host without interactive approval must not be told to authorize tools through chat. Real capability blockers use host wait if nothing can advance. No denial bypass, invented consent or escape from an existing wait/pause. Reminder text follows the same permission boundary. | Implemented, targeted tests passed |

The four behavioral requirements above are implemented with targeted evidence.
Release verification includes the full Linux regression, static checks, a clean
wheel build/cold installation, version/lock checks and exact main/tag validation.
CI/publication outcomes are recorded from their actual workflow runs, never
inferred from the number of local tests.
Non-Linux native validation is deferred, not passed or a release blocker.

## Implementation and evidence

- `GoalService` projects status at read time, without mutating runtime diagnostics
  or durable receipts. `last_delivery` is a historical check, not pending work.
- Model tool assembly selects `for_agent=True` internally. This is not an exposed
  model argument, permission grant or lifecycle operation. Native commands and
  readonly Goal views retain external-observer semantics.
- Tests cover an actual deferred check followed by wait-before-idle through the
  runtime, native command and model tool, without creating another reminder;
  transport outcomes and protected lifecycle states remain intact.
- Packaged-skill and built-host-prompt tests check the shipped instructions. They
  are instruction-contract checks, not a claim that every model will choose an
  optimal plan or that a script judges business completion.
- Source policy is unchanged: Coding owns approvals, Claw owns its configured
  capability boundary, and Goal records blockers rather than inventing an
  authorization channel.

## Regression prerequisite

Full-suite validation reproduced the separate extension/MCP shutdown race observed
in 2.4.6 CI: cancelling asyncio tasks did not stop their default-executor IO, yet
the owner loop/thread could exit before that IO settled. The owner now explicitly
joins its default executor before closing its loop. This changes resource
settlement only, not Goal scheduling or tool permissions; the existing real
persistence/cancellation assertions are retained. A deterministic executor
settlement test complements the original Claw integration case.
