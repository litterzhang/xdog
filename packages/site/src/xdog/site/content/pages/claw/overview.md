---
title: Overview
---

*One persistent logical main Agent per group, with durable inputs, memory and Goals.*

Claw is a local-first Linux assistant **application**, not a public Python gateway/
session SDK. Operate it through `xdog-claw`, configuration, TUI/Weixin channels and
the documented private local protocols. Reusable Agent/MCP interfaces belong to
`xdog.agent`; child execution belongs to each application's host runtime.

Each group owns a workspace, memory, context/history and one main-Agent execution
route. Session files are context storage, not additional Agent identities or event
destinations. Idle new/reset rotates context without moving the group's Goal;
saved transcripts and legacy branches are read-only history, not executable routes.

## Highlights

- Native TUI, Weixin and default private Unix ACP intake share the application-owned
  Core Inbox and the same group controller. Busy ordinary input is not implicit steering.
- Durable input receipts and separate output/send-attempt receipts distinguish
  accepted input, Agent execution and user-channel delivery. Unknown/interrupted
  effects are never automatically replayed.
- `xdog-skills` is included by default. Native Goal commands/tools are available
  independently of the optional readonly display worker; the skill body is not
  active by default. Availability creates no Goal/model/worker.
- The current main Agent plans, optionally delegates, reports progress, checks task
  evidence and judges Goal completion. There is no mandatory reviewer or Goal-owned
  planner/worker scheduler.
- Host-owned children have independent task knowledge, prompts, models and state,
  with frozen parent tool/skill subsets and persisted schema/filesystem ceilings.
  No fixed child lifetime/call/token/concurrency/retained-record quota is imposed.
- Installed Eyes uses the shared user-local daemon and public CLI; Flow uses public
  CLI argv without a daemon. Discovery alone starts no observation/workflow.
- Memory and Goal binding survive context rotation. History remains searchable
  and exportable without activating an old conversation.

## Try it

```bash
uv run xdog-claw onboard
uv run xdog-claw gateway start
uv run xdog-claw tui --group main
```

Choose an actual configured model/provider during onboarding. For foreground
operation use `gateway start --foreground`; use `--config PATH` on the relevant
subcommand to select another profile. See [reference](/packages/claw/reference).

## Goal workflow and lifetime

The group main Agent plans from its actual conversation, saves `plan.md`, works
itself or uses existing authorized child tools, and records an assessment in
`progress.md` before initial work and every continuation. Task JSON files are the
sole task-state authority; the Goal registry owns binding/history and reminders,
not a second Agent loop or automatic acceptance verdict.

One current Goal includes waiting, paused and completed states. Completed remains
current/viewable until explicit clear or a newly delegated Goal atomically replaces
that completed binding, preserving history. Paused/waiting work cannot be silently
overwritten. User waits require genuine authorized user evidence; task waits need
the main Agent's check and versioned activation. Automatic inputs cannot release
user waits, historical host waits or user pauses. Pause stops proactive reminders
and only Goal's own Eyes registration until explicit user resume, not accepted
inputs/tools/children or unrelated observations. Context rotation changes none of
this ownership. See [skills](/packages/skills) and [design](/packages/claw/design).

## Observation and user notifications

Eyes' saved JSON selects sources, detectors, handlers and explicit destinations.
Its caller-selected owner is management metadata, not authentication or a restriction
to the creating group/workspace. Eyes tracks observation/handler delivery, never
later Agent execution or chat output. Closing a host binding detaches without
stopping registrations; cancelling collection does not retract accepted target work.

Ordinary ACP input has **no implicit chat reply recipient**. Enqueue ACKs durable
intake; standard prompt returns execution identifiers/stop reason without assistant
message chunks. Results can be read explicitly from receipts. The TUI defaults to
suppressing live automatic ACP completions, not hiding history.

When notification is required and permitted, the Agent uses `message` to select
**one currently bound channel of the current group per message**. There is no
arbitrary recipient/group, broadcast, creator-reply capture or observer notification
route. A saved Goal question is not a sent message; output receipts remain independent
of Goal lifecycle. See [messaging reference](/packages/claw/reference) and the
[ACP reply contract](https://github.com/litterzhang/xdog/blob/main/packages/claw/docs/acp-input-replies.md).
