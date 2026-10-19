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

## Durable Goals: main Agent, workers and independent review

The group main Agent creates a Goal with explicit original conditions and keeps
responsibility for planning, task dependencies, assignment and report acceptance.
There is no forced LLM planner, legacy task scheduler or script-verdict engine.
Goal workspaces are under `<workspace>/.xdog/goals/<goal_id>/`; original conditions,
revisions, task/attempt identities, wait reasons and review state persist.

The main Agent manages its plan, each executor owns only its bound current report,
and the host owns status/review summaries. Dedicated worker roles and report paths
are host-bound; a label, skill body or event provenance cannot grant that authority.
`submitted` means awaiting main-Agent acceptance, not task or Goal completion.
Criteria revisions require an actual user turn, not background input.

A completion request or the Goal work's normal safe stop enters a host-created
independent reviewer Agent. It checks actual artifacts against every condition;
scripts/tests are evidence tools, never an automatic success verdict. The host
checks reviewer identity, Goal revision and evidence freshness before committing
completion. Stale/missing evidence, waiting, cancellation and faults cannot pass.
Ordinary chat is outside this Goal completion gate.

Goal work reconciles once at gateway startup, then wakes on changed obligations,
child completion or safe idle. A 30-second fallback handles fault reconciliation;
it is not model polling. Event coalescing and producer state-change checks avoid
busy-spin. User input has priority, and waiting/no progress does not launch another
main Agent.

## Eyes and channels

With explicit `eyes_enabled: true`, the gateway owns managed Eyes observers and
its private ACP endpoint. Time/file events enter the leased inbox at `group:<gid>`
with exact workspace validation, then use the same group queue and active context.
No exact-session event target, receiver-created second Agent or legacy scheduler
is involved. The `eyes` tool manages observations, not agent execution inside a
source; old task files are neither loaded nor migrated.

Goal-owned file observers are restored explicitly by the host, alongside report
rereads/reconciliation. Ordinary file observers remain opt-in and do not restart
automatically. Events are change hints: the main Agent rereads and validates the
current task, attempt and report revision. Trusted `notify_only` policy suppresses
per-event channel broadcasts; untrusted provenance cannot select that policy.
Pending/unknown/interrupted states retain their no-blind-replay boundaries.

The terminal channel and WeChat channel share the group main Agent. Goal internal
notifications do not automatically send each reconciliation reply to WeChat;
ordinary channel replies retain their existing delivery semantics.

## Prompt and memory

`build_system_prompt` separates a cacheable static base from mutable workspace,
memory and Goal content. The workspace holds `IDENTITY.md`, `AGENTS.md`, `SOUL.md`,
`USER.md`, `MEMORY.md` and a one-time `BOOTSTRAP.md`. Session-scoped skills belong
to the cacheable prefix; turn-scoped skill messages do not rewrite that prefix.

`MemoryManager` combines daily logs, long-term memory and search. Optional search
uses sentence-transformers/sqlite-vec vectors plus BM25; keyword search remains a
fallback. Compaction archives original context and rebuilds the current one; its
saved checkpoints are historical evidence, not a branch switch operation.

See the packaged
[claw-goals workflow](https://github.com/litterzhang/xdog/blob/main/packages/claw/src/xdog/claw/skills/claw-goals/SKILL.md),
[current objective](https://github.com/litterzhang/xdog/blob/main/docs/claw-goals-objective.md),
[group-owned design](https://github.com/litterzhang/xdog/blob/main/docs/claw-session-goals-design.md)
and [acceptance checklist](https://github.com/litterzhang/xdog/blob/main/docs/claw-session-goals-requirements.json)
for the role contracts and current verification status. Linux is the supported
platform; no non-Linux native validation is claimed here.
