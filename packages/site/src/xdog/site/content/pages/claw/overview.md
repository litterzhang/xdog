---
title: Overview
---

*One persistent logical main Agent per group, with durable Goals and memory.*

Claw is a local-first Python gateway for long-running assistance over a terminal
client or WeChat. Each group has its own workspace, permissions, conversation
history, memory and **one execution route**. Sessions are persisted context/history
units, not separate main-Agent identities. New/reset can rotate idle context without
changing the group's Goal owner; saved history is read-only, not a route to resume.

## Highlights

- The group main Agent plans, assigns, waits for and accepts task work itself;
  there is no mandatory automatic planner or second main-Agent loop.
- Dedicated task workers write host-bound attempt reports. `submitted` is not task
  acceptance or Goal completion.
- An independent reviewer Agent checks actual evidence against every original
  condition; the host fences completion to the Goal revision and evidence snapshot.
  Scripts/tests collect evidence, not automatic completion verdicts.
- Child Agents have no fixed execution-time, model-call, cumulative-token,
  concurrency or retained-record quota. Permissions, actual model capacity,
  protocol/resource protections, user cancellation and shutdown still apply.
- Opt-in Eyes file/time observers use the existing private `group:<gid>` ACP/inbox
  and group execution path. Goal-owned internal file notifications do not
  automatically broadcast replies to WeChat.
- Memory and Goal state survive context rotation; history remains searchable and
  exportable without activating an old conversation. Interrupted effects are not
  automatically replayed.

## Goal workflow and boundaries

Start with the packaged
[claw-goals skill](https://github.com/litterzhang/xdog/blob/main/packages/claw/src/xdog/claw/skills/claw-goals/SKILL.md)
and its dedicated worker/reviewer instructions. Goal workspaces live under
`<workspace>/.xdog/goals/<goal_id>/`. The main Agent manages the plan and acceptance;
workers own their current attempt reports, and host-generated status/review records
have separate writers. Only an actual user turn can authorize changes to conditions.

With `eyes_enabled: true`, the host explicitly restores Goal-owned file observers
and rereads reports for reconciliation. Ordinary file observers still do not
restart automatically. Events are untrusted change hints, not identities,
permissions or acceptance evidence. Waiting/cancellation/faults never mean success.

See [Design](/packages/claw/design), [Reference](/packages/claw/reference), the
[current objective](https://github.com/litterzhang/xdog/blob/main/docs/claw-goals-objective.md),
[group-owned design](https://github.com/litterzhang/xdog/blob/main/docs/claw-session-goals-design.md)
and [acceptance checklist](https://github.com/litterzhang/xdog/blob/main/docs/claw-session-goals-requirements.json).
The design/checklist filenames retain historical `session` wording; they do not
mean session-directed Goals or events. Implementation and final acceptance evidence
are tracked separately; this overview is not a claim that every audit gate passed.

## Try it

```bash
uv run xdog-claw --help
uv run xdog-claw gateway start --foreground
uv run xdog-claw tui --group main
```

Configure an actual model/provider before starting the gateway. Linux is the
supported platform; permissions are application policy, not an OS sandbox.
