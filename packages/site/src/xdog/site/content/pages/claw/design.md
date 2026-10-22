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
native gateway socket serves the local terminal client; default private ACP serves
explicit group-addressed inputs. These necessary communication paths retain
capability/ownership checks and do not create a general-purpose remote control
plane or imply a public Python runtime embedding contract.

## One gateway and one route per group

The long-lived gateway (`core/runtime/gateway.py`) exposes a private Unix socket
(`0600`) for `message`, `reset`, `status`, `ping` and negotiated native controls.
The `Orchestrator.route_message` path serves TUI, channels, canonical ACP inputs
and Goal system input through the same durable input service and group execution owner. Each
`GroupRuntime` owns its workspace, transcript store, memory, Goals, skills, tools
and one cached active AgentSession.

A session is a context/history persistence unit, not a second main-Agent identity.
Idle `/new` or `/reset` can rotate context; Goals remain with the group, and active
children must first settle. Unfinished stored Goals do not suppress automatic
daily/idle resets.
Historical `resume`, `fork`, `branch` and `goto` controls and target-session staging
are removed and explicitly rejected. Saved transcripts/checkpoints/legacy branch
files remain readable/exportable, never executable routes. Restart restores only
the committed current context and does not replay interrupted model/tool effects.
Coding's independent branching features are unchanged.

## Group admission is not a child quota

Background group turns use the gateway's configured `max_concurrent_agents`
admission semaphore (default 3) and per-group locks. User input bypasses that
background semaphore so autonomous work does not hold human input behind it.
Ordinary messages are committed to one durable group Inbox with bounded pending
admission; busy input never implicitly steers the current turn. Explicit identified
steer/follow-up input belongs to `xdog.agent`, including its consumption modes and
checkpoint receipts. No separate Claw queue policy or debounce controls remain;
Eyes file-detector debounce is a different live source feature.

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

Claw is a long-term assistant with optional task autonomy. Installed `xdog-skills`
provides Goal tools/skill without silently creating a Goal. The current group
main Agent plans from its conversation, optionally delegates through existing
authorized child tools, reports progress and judges completion. There is no
mandatory independent reviewer, hidden planner or Goal-owned child runner.

Goal owns its leased registry, authoritative workspace task files and reminder
outbox. Claw contributes its real stable group owner and existing Core intake;
Coding intentionally binds its own session/branch. User waits and pauses are not
released by untrusted events. Rotation does not replay interrupted effects.

## Capabilities, Eyes and channels

Coding/Claw use the same `xdog.agent.capabilities.CapabilityAssembly` to compose
actual tool identities and skill selections. A capability may provide tools,
skills, runtime resources or extension hooks; it need not provide all of them.
The SDK creates no second execution owner. Hosts own scope lifecycle, tool grants,
read-only/approval policy and parent/child authority. Packaged skill discovery
does not activate a body or grant a tool. Flow/Eyes bodies are activated on demand.

Eyes' application binding ensures its independent shared daemon through the CLI;
Flow's application tool invokes the CLI without a daemon. Saved Eyes JSON defines
sources, routing and destinations. The owner label is a management namespace, not
an authorization token or restriction to the current group/workspace.

Configured ACP deliveries enter ordinary Core intake and the existing group
main Agent. An intake ACK does not prove execution or user notification. Eyes
owns no model watcher, creator reply capture or extra output route. The main
Agent uses normal messaging when notification is needed. Source cancellation,
accepted input, Agent execution and user-channel output remain separate facts.
Unknown effects are never blindly replayed.

## Prompt and memory

`build_system_prompt` separates a cacheable static base from mutable workspace,
memory and generic skill/context content. The workspace holds `IDENTITY.md`, `AGENTS.md`, `SOUL.md`,
`USER.md`, `MEMORY.md` and a one-time `BOOTSTRAP.md`. Session-scoped skills belong
to the cacheable prefix; turn-scoped skill messages do not rewrite that prefix.

`MemoryManager` combines daily logs, long-term memory and search. Optional search
uses sentence-transformers/sqlite-vec vectors plus BM25; keyword search remains a
fallback. Compaction archives original context and rebuilds the current one; its
saved checkpoints are historical evidence, not a branch switch operation.

See the shared Goal skill in `packages/skills` and the assistant-core design and
47-item acceptance checklist in the repository. Shared Python extension UI,
packaging isolation and final whole-branch acceptance remain separate gates;
this page does not claim those unfinished milestones are complete.
