---
title: Design
---

Claw coordinates a persistent group main Agent, its task Agents and durable memory
through one gateway and the existing Agent runtime—not another task/Agent engine.

## Application boundary and shared runtime

Claw owns application lifecycle, configuration, channels and group policy. The
reusable SDK is `xdog.agent`, including its MCP client, extension API and child
supervision. Claw adapts those facilities internally; `Orchestrator`, `GroupRuntime`
and `AgentSession` are implementation wiring, not a separately supported public
Claw embedding API. The root `xdog.claw` package does not re-export runtime classes.
Coding's SDK remains distinct.

The product entry points are the `xdog-claw` CLI, configuration and channels. The
native gateway socket serves the local terminal client; optional private ACP serves
explicit group-addressed inputs. These necessary communication paths retain
capability/ownership checks and do not create a general-purpose remote control
plane or imply a public Python runtime embedding contract.

## One gateway and one route per group

The long-lived gateway (`core/runtime/gateway.py`) exposes a private Unix socket
(`0600`) for `message`, `reset`, `status`, `ping` and negotiated native controls.
The `Orchestrator.route_message` path serves TUI, channels, canonical ACP inputs
and Goal system input under the same group ownership/queue policy. Each
`GroupRuntime` owns its workspace, transcript store, memory, Goals, skills, tools
and one cached active AgentSession.

A session is a context/history persistence unit, not a second main-Agent identity.
Idle `/new` or `/reset` can rotate context; Goals remain with the group, and active
children must first settle. Unfinished Goals suppress automatic daily/idle resets.
Historical `resume`, `fork`, `branch` and `goto` controls and target-session staging
are removed and explicitly rejected. Saved transcripts/checkpoints/legacy branch
files remain readable/exportable, never executable routes. Restart restores only
the committed current context and does not replay interrupted model/tool effects.
Coding's independent branching features are unchanged.

## Group admission is not a child quota

Background group turns use the gateway's configured `max_concurrent_agents`
admission semaphore (default 3) and per-group locks. User input bypasses that
background semaphore so autonomous work does not hold human input behind it.
Queue modes—`collect`, `steer`, `steer-backlog`—and debounce/backlog protections
control how input is retained or delivered at checkpoints, not tool rollback.

Within a group, the main Agent manages child concurrency, progress, waiting,
cancellation and closure. Child runtime timeout, fixed model-call count,
cumulative-token quota, fixed active-child count and retained-record count limits
are removed. Ending a parent reply does not close children still working. Long
child tool loops reuse native compaction with durable original-history checkpoints
when required model metadata is available; no cumulative-history limit replaces
the removed runtime caps.

Tool permissions, inherited read-only scope, actual model context/output/vision
capacity, bounded protocol frames, per-message/storage chunks, image/input resources
and labeled display retention remain. Tool/extension RPC deadlines and owned
cancellation/shutdown are distinct from a removed child lifetime timeout. Trusted
Python/process code is not an OS sandbox.

## Optional reusable Goals

Claw is a long-term personal assistant with optional task autonomy. Goals default
disabled. The independent `xdog-goal` service owns leased storage, historical
contracts, reports/reviews and notification outbox. Claw's thin host binds a stable
group/workspace/inbox owner to existing supervised children and Core durable
intake; Coding uses the same service with its intentionally exact session/branch
binding. No GoalManager, implicit planner, turn-end review or Gateway Goal loop
remains. Scripts/tests are evidence, never completion verdicts.

A submitted report needs explicit parent acceptance; completing a Goal requires
explicit request_complete and a fresh independent readonly review of each original
condition. Idle context rotation is not blocked by unfinished Goal data. Original
terminal child execution remains inspectable after rotation without constructing
or replaying an offline Agent. User authority follows actual consumed human input,
not provenance, source labels, anonymous steering or extension display.

## Eyes and channels

Explicit Eyes observers deliver source-neutral events through the private
`group:<gid>` ACP endpoint and existing Core Inbox/GroupController. Neither ACP nor
channels project Goal policy into input. Goal notices use the same durable input
path, not a separate handler host or main-Agent loop. There is no message approval;
existing tool authorization still applies. Observation, notification and worker
cancellation remain explicit independent Agent decisions. No Goal status implicitly
stops/restores another resource. Unknown effects are not blindly replayed.

## Prompt and memory

`build_system_prompt` separates a cacheable static base from mutable workspace,
memory and generic skill/context content. The workspace holds `IDENTITY.md`, `AGENTS.md`, `SOUL.md`,
`USER.md`, `MEMORY.md` and a one-time `BOOTSTRAP.md`. Session-scoped skills belong
to the cacheable prefix; turn-scoped skill messages do not rewrite that prefix.

`MemoryManager` combines daily logs, long-term memory and search. Optional search
uses sentence-transformers/sqlite-vec vectors plus BM25; keyword search remains a
fallback. Compaction archives original context and rebuilds the current one; its
saved checkpoints are historical evidence, not a branch switch operation.

See the shared Goal skill in `packages/goal` and the assistant-core design and
47-item acceptance checklist in the repository. Shared Python extension UI,
packaging isolation and final whole-branch acceptance remain separate gates;
this page does not claim those unfinished milestones are complete.
