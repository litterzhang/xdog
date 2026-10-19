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
it through `xdog-claw`, configuration and its channels; explicitly enabled ACP is
a private local ingress to the same group route. Reusable Agent/MCP SDK interfaces
belong to `xdog.agent`; Claw's group/session adapters are internal application code.

## Highlights

- Optional Goal tools/skill are available when the package is installed. No task is
  created automatically. When explicitly delegated,
  the group main Agent plans, assigns, waits for and accepts task work itself;
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
  and group execution path. Source stop and notification stop are independent
  explicit operations; source labels do not choose reply recipients.
- Memory and Goal state survive context rotation; history remains searchable and
  exportable without activating an old conversation. Interrupted effects are not
  automatically replayed.

## Goal workflow and boundaries

The same independent `xdog-goal` package supplies Claw/Coding tools, storage,
worker reporting and explicit independent review. The shared `goals` skill describes
its workflow. Installing/loading it is not autonomy delegation. Goal truth belongs
to its leased journal; workspace files are derived views. Only actual consumed
human input grants criteria/user-pause controls; untrusted events are data.

Claw's group owner survives context rotation. Existing supervised children and the
Core Inbox execute work and queued notices with no second main-Agent loop or
message approval. Submitted reports need explicit acceptance, and only an explicit
request_complete starts a reviewer. Goal state never implicitly cancels observers,
notifications or workers. Old Goal formats are refused, not migrated. See Design
and Reference plus the repository's assistant-core47-item checklist for remaining
shared extension UI, packaging and final acceptance gates.

## Try it

```bash
uv run xdog-claw --help
uv run xdog-claw gateway start --foreground
uv run xdog-claw tui --group main
```

Configure an actual model/provider before starting the gateway. Linux is the
supported platform; permissions are application policy, not an OS sandbox.
