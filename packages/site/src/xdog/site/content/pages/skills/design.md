---
title: Design
---

## Instructions, domain data and host execution

`xdog-skills` depends on Agent and the standard library. It is an inert hub, not
an execution harness. The `goal` skill describes planning, task decomposition,
optional worker delegation, file updates, progress assessment and acceptance.
Those semantic decisions belong to the current main Agent.

Goal's installed provider contributes native commands, actual model tools,
owned storage/reminder resources and optional readonly views through generic
SDK interfaces. Coding/Claw retain their controller, approvals, child runtime
and message routes. Extensions do not contribute model tools.

## Durable state, not runtime authority

The leased registry binds one current Goal/history to the actual owner. Tasks
have one authority: `workspace/.xdog/goals/<goal-id>/tasks/*.json`. `plan.md` stores
the main Agent's plan; `progress.md` stores version-checked progress reports.
Expected-version task updates prevent lost writes. History/snapshots never grant
a worker additional tools, lifecycle authority or writable parent context.

A delegated child may receive a scoped `goal` tool for explicitly assigned tasks.
It updates its own task result through that tool; this grants no Goal lifecycle,
parent report or permission authority. A host without children can do the work
on its main Agent.

## Reminders do not make semantic decisions

Generic owner/settled signals and optional Eyes task-file events enter a durable
coalescer. When appropriate, Goal submits a stable-ID ACP reminder to the original
main Agent's existing input route. ACP transports; the skill instructs; the main
Agent decides what to do. Eyes uses its independent daemon and public CLI, not
an embedded observer service.

Before continuation the main Agent reports progress relative to the previous
round. Repeated no progress without a viable approach should lead it to pause,
not loop blindly. Pause stops proactive reminders and its owned observation;
accepted input, tools and children remain host-owned. User waits bind a concrete
question and are not released by timers or untrusted events. Completed Goals stay
current until explicit clear or a new Goal; there is no newly enterable host-wait state.
