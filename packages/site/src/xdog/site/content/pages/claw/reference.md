---
title: Reference
---

Application boundaries, the `xdog-claw` CLI, workspace files, private local
communication, durable intake and explicit Agent input controls.

## Application boundary

Claw is operated as an application through its CLI, configuration, terminal and
messaging channels. It does not offer a separately supported public Python SDK
for embedding its gateway or group/session runtime. The root `xdog.claw` package
does not re-export runtime classes. Internal runtime/session adapters remain
importable for application composition and tests, not as a public API contract or
recommended direct embedding entry point.

For reusable Python Agent functionality, use **`xdog.agent`**. Its Agent runtime,
MCP client (`xdog.agent.mcp.McpManager`), extension API and child supervision are
shared SDK facilities; their existence does not make Claw an independent SDK.
Coding's SDK is separate and is not removed by this boundary.

## Private local communication

- **Native gateway Unix socket**: the terminal client's private transport for
  messages, status, reset and negotiated product controls. Capabilities, request/
  connection/group ownership and context revisions fence control requests. It is
  not a general public embedding API; unsupported controls must not be guessed.
- **Optional Unix ACP ingress**: enable explicitly with `acp_socket_path` or
  `gateway start --acp-socket PATH` (also host-owned when `eyes_enabled: true`).
  ACP uses explicit `group:<id>` addresses and exact workspace validation. Prompt
  and negotiated durable enqueue enter the existing group inbox/queue and active
  Agent, not a second loop or a historical-session activation route.
- Pending ACP inputs survive receiver restart; interrupted execution and uncertain
  effects are not automatically replayed. No socket/capability bypasses tool policy
  or grants approval. There is no hosted control plane or public TCP endpoint.

See the [ACP setup and recovery boundaries](https://github.com/litterzhang/xdog/blob/main/docs/claw-eyes-acp.md)
for the private endpoint contract and receipt/cancellation limits.

## Workspace files

The files under a group's workspace that shape the system prompt. Editing them (or
running `onboard`) changes the agent.

| File | Role |
|---|---|
| `IDENTITY.md` | Who the agent is — the Name line is the agent's name. |
| `AGENTS.md` | Operating instructions / behavioural rules. |
| `SOUL.md` | Persona and voice. |
| `USER.md` | What the agent knows about the user. |
| `MEMORY.md` | Long-term memory snapshot. |
| `BOOTSTRAP.md` | One-time first-run setup, consumed then deleted. |
| `.xdog/goals/<goal_id>/` | Disposable Goal projections; authoritative state is the optional package's private journal under `data_dir/groups/<id>/goals/`. |

## CLI — `xdog-claw`

Subcommands of the claw console script (`cli/cli.py`).

| Command | Does |
|---|---|
| `onboard` | Setup wizard: provider login, model, agent name, workspace. |
| `gateway start [--config PATH] [--foreground] [--acp-socket PATH]` | Start the gateway daemon. |
| `gateway stop · gateway status` | Stop / inspect the gateway. |
| `tui [--group]` | Attach a terminal client to a group. |
| `channel login --weixin [--base-url]` | Authenticate the WeChat channel. |
| `generate-image PROMPT [--workspace PATH] [--output-dir PATH]` | Generate local image assets with the configured image model. |

## Ordinary intake and explicit Agent input

Ordinary TUI, Weixin and ACP messages commit to the same durable group Inbox
before acknowledgment. Busy input waits for the existing group execution owner;
it is not silently steered into an in-flight turn. Input IDs deduplicate intake,
while execution attempts and channel-output receipts remain separate facts.
`max_queued_messages` bounds unfinished accepted inputs (pending/claimed/running,
default50); accepted messages are never evicted to make room. Claw does not batch independent messages into one turn
or expose collect/steer-backlog/debounce queue policies.

Explicit steer/follow-up uses the existing identified `xdog.agent` queue and safe
checkpoints. It is neither cancellation/rollback nor a second Claw queue. The
Agent's native input modes remain independent of group admission settings.

## Context and read-only history

A group has one logical main Agent. `/new` and `/reset` rotate idle context without
deleting its Goals; active child work must first settle. Unfinished stored Goals
do not suppress automatic daily/idle resets. Runtime/gateway context identities fence stale requests,
not route events to another main Agent.

`/sessions` and `/tree` inspect history; **Enter/v** opens read-only source. `/` searches,
Right/Left inspects/returns, `r` refreshes and Escape closes. The browser has no
activation or `n` new-context shortcut. `/transcript saved SESSION_ID [BRANCH_ID]`,
`/transcript branch BRANCH_ID` and `/transcript timeline [SESSION_ID]` retain their
negotiated group-scoped read-only boundaries, with checksummed source and reviewed
copy/export/share. Saved source never substitutes for a failed live/archived read.

`/resume ID`, `/fork`, `/branch` and `/goto` historical execution controls are removed
and explicitly rejected. Legacy branch/checkpoint files are not deleted or activated.
Startup restores only the current committed context; uncertain requests and
interrupted side effects are not automatically replayed. Coding branching is unchanged.

## Optional Goal tool and roles

Installing the optional Goal distribution makes the capability and built-in skill
available automatically, with no enable setting. Availability is not delegation. The
same `xdog-skills` service/skill is reused by Coding. Parent actions include create,
list/status, add_task, assign, accept_task, revise, wait/pause/resume, abandon,
cancel_task, cancel_notifications and request_complete. Workers have separately
bound task_report/context tools. Submitted is not accepted, and only an explicit
request_complete starts independent readonly review. Turn end is not acceptance.

Claw's owner is the persistent group/workspace/inbox binding, not its current
conversation. Context rotation cannot move or delete Goals; original terminal
child execution supports explicit acceptance without offline replay. Actual human
input authorizes condition changes/user-pause release; labels cannot. Goal state,
worker control, source control and notification control remain independent.
The native Goal widget and implicit Gateway Goal runner are removed. Shared Python
extension UI and final architecture acceptance remain separately tracked.

## Child settings and resource boundaries

```text
/agents start --label Inspector --instructions "Inspect only" --tools filesystem,current_time --fresh -- TASK
/agents start --preset inspector -- TASK
```

The settings shape is `label`, `instructions`, `tools`, `inherit_context`, `model`,
`thinking`, `preset`; named presets are explicit data-only trusted configuration.
They grant no tool approval, role authority, recursion or provider activation.
Dedicated Goal workers/reviewers use the host-bound Goal workflow, not a plain
child label/preset claiming to be a privileged role.

The main Agent manages child concurrency/waiting/cancellation/closure. There is no
fixed child runtime timeout, call count, cumulative-token quota, active-child count
or retained-record count. Removed `--timeout`/`--calls`/`--tokens` flags and child
`timeout`/`max_model_calls`/`max_tokens` keys are rejected for new work; historical
values are inert. Provider per-response output/context limits remain separate.
Parent reply completion does not close working children; supported long loops use
native compaction and durable original-history checkpoints.

Permissions/read-only/MCP/extension policy, actual model capacity, bounded protocol
frames, per-message/chunk storage, image/input resources, display limits and
RPC operation deadlines remain. They are not task-lifetime quotas or an OS sandbox.
User cancellation, shutdown cleanup and no-blind-replay crash recovery still apply.
The gateway's background **group-turn** admission policy is separate from child
concurrency; human inputs keep priority.

Current scope and acceptance evidence are tracked in the
[objective](https://github.com/litterzhang/xdog/blob/main/docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md),
[design](https://github.com/litterzhang/xdog/blob/main/docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md)
and [checklist](https://github.com/litterzhang/xdog/blob/main/docs/claw-assistant-core-requirements.json).
Linux is the supported platform; this reference does not claim non-Linux validation
or completed acceptance gates.
