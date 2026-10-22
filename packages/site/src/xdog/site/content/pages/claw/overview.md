---
title: Overview
---

*One persistent logical main Agent per group, with durable Goals and memory.*

Claw is a local-first Python gateway for long-running assistance over a terminal
client or WeChat. Each group has its own workspace, permissions, conversation
history, memory and **one execution route**. Sessions are persisted context/history
units, not separate main-Agent identities. New/reset can rotate idle context without
changing the group's Goal owner; saved history is read-only, not a route to resume.

Claw is an application, not an independent public Python embedding SDK. Operate
it through `xdog-claw`, configuration and its channels; its default private ACP endpoint is
a local ingress to the same group route. Reusable Agent/MCP SDK interfaces
belong to `xdog.agent`; Claw's group/session adapters are internal application code.

## Highlights

- Optional Goal tools/skill are available when the package is installed. No task is
  created automatically. When explicitly delegated,
  the group main Agent plans, assigns, waits for and accepts task work itself;
  there is no mandatory automatic planner or second main-Agent loop.
- Goal planning, optional delegation, progress reports and completion are owned
  by the current main Agent. No required reviewer or hidden Goal execution loop.
- Child Agents have no fixed execution-time, model-call, cumulative-token,
  concurrency or retained-record quota. Permissions, actual model capacity,
  protocol/resource protections, user cancellation and shutdown still apply.
- Eyes JSON definitions can deliver file/time events to an explicitly configured
  ACP destination. Incoming messages use the existing group execution path. Eyes
  does not track downstream Agent execution or chat output.
- Memory and Goal state survive context rotation; history remains searchable and
  exportable without activating an old conversation. Interrupted effects are not
  automatically replayed.

## Goal workflow and boundaries

The independent `xdog-skills` package supplies Goal tools and the `goal` skill.
The current group main Agent plans from its actual conversation, optionally
assigns tasks through existing authorized child tools, records progress and
judges completion against evidence. Task files are authoritative and remain in
the Goal workspace. A group has at most one current Goal; completion remains
visible until an explicit clear/archive operation. Pauses and user waits are not
released by timers or untrusted external input.

Goal reminders enter the same Core Inbox through ACP. The Goal runtime manages
its own records/signals and optional Eyes CLI registration; it is not a worker
scheduler or another model loop. Context rotation does not change Goal ownership.
Old incompatible state is left untouched, not used to replay interrupted effects.

## Try it

```bash
uv run xdog-claw --help
uv run xdog-claw gateway start --foreground
uv run xdog-claw tui --group main
```

Configure an actual model/provider before starting the gateway. Linux is the
supported platform; permissions are application policy, not an OS sandbox.

## Observation and user notifications

Eyes runs in its independent user-local daemon. Application capability discovery
exposes its CLI-backed tool and discovers its packaged skill; the full skill body
is activated only when needed. Saved JSON selects destinations with an explicit
owner namespace; the current Claw group is not an Eyes routing restriction.
Flow uses the same installed capability mechanism but has no daemon.

Eyes delivery ends at the selected handler's protocol boundary. Ordinary ACP
input has no implicit chat reply recipient. When the user's task requires a
notification, the main Agent uses Claw's normal message tool for an actual
currently bound channel. No extra observer reply route or notification-cancel
protocol is created. Source stop does not retract accepted input or undo effects;
Core output receipts independently record delivered/pending/unknown outcomes.
