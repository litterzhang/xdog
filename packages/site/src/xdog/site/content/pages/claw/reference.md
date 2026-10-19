---
title: Reference
---

The top-level API, the workspace files that shape the prompt, the `xdog-claw` CLI, and
the input queue modes.

## Top-level API

Exported from `claw/__init__.py`.

| Name | Kind | Purpose |
|---|---|---|
| `Orchestrator` | class | Single dispatch path over all groups. |
| `GroupRuntime` | class | One group's workspace, memory, goals, tools. |
| `AgentSession / TurnResult` | class | The cached current context and a turn result; not separate group owners. |
| `MessageQueue` | class | Per-group input queueing. |
| `TranscriptStore / SessionManager` | class | Persistence of conversations. |
| `build_system_prompt / init_workspace` | function | Assemble the prompt / seed the workspace files. |
| `ClawConfig / GroupDef` | type | Global and per-group configuration. |

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
| `.xdog/goals/<goal_id>/` | Group-owned conditions, plan, host status and task/review evidence with distinct writers. |

## CLI — `xdog-claw`

Subcommands of the claw console script (`cli/cli.py`).

| Command | Does |
|---|---|
| `onboard` | Setup wizard: provider login, model, agent name, workspace. |
| `gateway start [--config] [--foreground]` | Start the gateway daemon. |
| `gateway stop · gateway status` | Stop / inspect the gateway. |
| `tui [--group]` | Attach a terminal client to a group. |
| `channel login --weixin [--base-url]` | Authenticate the WeChat channel. |

## Queue modes

How a burst of input to one group is folded together.

| Mode | Behaviour |
|---|---|
| `collect` | Batch pending inputs into the next turn. |
| `steer` | Deliver input at an SDK/tool checkpoint, not an abort or rollback. |
| `steer-backlog` | Steer, keeping a bounded backlog of the rest. |


## Context and read-only history

A group has one logical main Agent. `/new` and `/reset` rotate idle context without
deleting its Goals; active child work must first settle. Unfinished Goals suppress
automatic daily/idle resets. Runtime/gateway context identities fence stale requests,
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

## Goal tool and roles

The packaged
[claw-goals skill](https://github.com/litterzhang/xdog/blob/main/packages/claw/src/xdog/claw/skills/claw-goals/SKILL.md)
contains exact tool examples and worker/reviewer instructions. Main-Agent actions
include `create`, `list`, `status`, `add_task`, `assign_task`, `report_task`,
`update_task`, `request_complete`, `update`, `wait`, `pause`, `resume` and
`abandon_goal`. Goal `resume` continues the group's durable obligation; it is not
historical-context activation. Only actual user input can authorize condition changes.

Use `assign_task` for a host-bound worker or main-Agent executor. Each task/attempt
has its own report identity and increasing revision. A `submitted` report requires
main-Agent acceptance; completing tasks does not complete the Goal. The host starts
an independent reviewer for requested/normally stopped Goal work and checks every
condition, reviewer identity, revision and evidence freshness. Scripts/tests supply
evidence, not automatic completion verdicts. No mandatory planner takes over after
creation, and ordinary chat is not forced through Goal review.

The host explicitly restores its Goal-owned file observers when Eyes is enabled;
ordinary file observers do not automatically restart. All callbacks use
`group:<gid>` and exact workspace validation through the existing ACP/inbox. Events
are untrusted hints requiring report rereads. Internal `notify_only` behavior comes
from trusted host records, not provenance, and avoids per-event WeChat broadcasts.

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
[objective](https://github.com/litterzhang/xdog/blob/main/docs/claw-goals-objective.md),
[design](https://github.com/litterzhang/xdog/blob/main/docs/claw-session-goals-design.md)
and [checklist](https://github.com/litterzhang/xdog/blob/main/docs/claw-session-goals-requirements.json).
Linux is the supported platform; this reference does not claim non-Linux validation
or completed acceptance gates.
