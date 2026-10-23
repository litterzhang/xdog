---
title: Design
---

Claw coordinates a persistent group main Agent, host-owned children and durable
memory through one gateway and the existing Agent runtime, not another task engine.

## Application boundary and shared runtime

Claw owns application lifecycle, configuration, channels and group tool policy.
`Orchestrator`, `GroupRuntime` and `AgentSession` are internal wiring, not a public
Python embedding SDK; the root `xdog.claw` package re-exports no runtime classes.
Coding's session SDK is separate.

Reusable functionality belongs to `xdog.agent`: `Agent`, its host-owned `ToolRegistry`,
`xdog.agent.mcp.McpRegistry`, extension APIs and inert child contracts such as
`ChildClient`/`ChildHandle`. Agent owns no child supervisor, child execution loop,
store or extension-worker runtime. Claw's `core/runtime/child_agents.py` and
`ClawChildRuntime` own those resources and borrow the original gateway loop.

The native Unix socket and default private ACP endpoint are necessary local
communication paths, not a hosted control plane, arbitrary remote embedding API or
permission bypass. Negotiated capabilities and original owner/attempt/context
identities fence native controls and later callback output.

## One group, one route, separate durable facts

Native TUI, Weixin and ACP inputs enter the application-owned input service through
`Orchestrator.route_message`. Immutable envelopes freeze input identity, attachments,
host-only context and original reply bindings before application admission. The
Core Inbox is `<data_dir>/inputs/inbox.sqlite3`, not an ACP-owned receipt database
or UI queue. One `GroupController` fences the admitted input/attempt/session until
settlement; `claimed` is not `running`, and preparation failure is not execution
failure. Busy ordinary input waits rather than implicitly steering the active Agent.

Each group runtime owns workspace, tools, memory, prompt construction, transcript
storage, one cached active AgentSession and optional capability bindings. Goal
business data belongs to the skills package. JSONL transcripts and `sessions.json`
store context/history, not Goal ownership, event destinations or execution authority.

The application separately owns `<data_dir>/outputs/journal.sqlite3`. Output and
transport-attempt facts distinguish pending/sending/delivered/unknown/cancelled
from proven-not-sent attempts. Input ACK, Agent success and chat delivery are not
interchangeable. Recipients freeze with the original binding, not on restart.
Unknown sends/interrupted effects are never automatically replayed.

Native `run_ack` follows actual durable intake. Connection/ACP endpoint detachment
only detaches waiters, not accepted application execution/storage. Receiver-only
restart preserves pending/running work. Actual application shutdown/crash marks
started work interrupted; explicit original-input cancellation has its own owner.
Retries must inspect original receipts, not guess from elapsed time or an ACK loss.
These are local recovery fences, not exactly-once side-effect guarantees.

## Context rotation, not branch navigation

Idle `/new` and `/reset`, or configured daily/idle reset, rotate only the current
context. The stable group main Agent, Goal binding, selected model/reasoning,
active tools/skills and access restrictions remain; messages/counters/context-owned
extension entries start fresh. Active children must first settle. Unfinished Goals
do not disable normal daily/idle reset.

Saved transcripts/checkpoints/legacy branches remain readable/exportable.
`/resume`, `/fork`, `/branch`, `/goto` and target-session staging are removed and
explicitly rejected. Restart restores only committed current context, not an old
execution route. Compaction preserves the complete original indexed checkpoint
before replacing current history, without replaying models/tools. Coding's branch
navigation is a separate product feature.

## Group admission and child authority

The gateway's `max_concurrent_agents` (default 3) controls background **group-turn**
admission, not a child quota; human inputs bypass that background semaphore.
Group `max_queued_messages` bounds unfinished accepted inputs (default 50), never
evicting accepted records to make room. No queue-mode/debounce/trigger-pattern/
per-group-concurrency policy is restored. Explicit steer/follow-up belongs to
Agent's identified input queue/checkpoints; Eyes detector debounce is separate.

Children never copy parent conversation/system prompts. Task knowledge is first-user
material; model/reasoning and a child-owned prompt may differ. Actual parent tools
and available skills form frozen subsets. Labels, presets, `--inherit`, instructions
and saved records grant no new authority or shared approval cache.

Hosts validate actual tool identity/schema/action/scope, skills, readonly and
permissions at admission, after approval and at executor entry. Parallel pending
calls cannot borrow later broader grants. Effective configuration, tool schema
ceilings and filesystem roots persist before the model runs and fence explicit
continuation. Missing legacy filesystem metadata is deny-all, not unconfined.
Scoped capability tools are rebound only by their actual original owner; for Goal
this permits assigned-task reads/result updates, never the parent's lifecycle,
plan/report authority or unrestricted Goal tool.

A safely completed child's explicit new message rebuilds a fresh Agent from saved
history under current original-owner checks. Idle cleanup does not stop the gateway
loop. Parent reply completion does not close still-working children. No fixed child
runtime/call/token/concurrency/retained-record quota exists; actual model capacity,
protocol/image/per-operation storage guards and owner cancellation remain. Long
loops use native compaction only with actual model metadata and durable original
history archives. Trusted Python/process code is not an OS sandbox.

## Default Goal capability, optional delegated work

Claw includes `xdog-skills` by default. Native `/goal` commands, the owner-bound tool
and skill metadata are independent of full-body activation; the Goal body is **not
active by default**. Ordinary chat/installation creates no Goal or worker/model.
The current main Agent plans, works/delegates using existing tools, checks evidence,
saves progress before each continuation and judges completion. No Goal-owned
planner/worker/reviewer loop or independent acceptance protocol exists.

Goal owns its registry/binding/history/reminder outbox; workspace `tasks/*.json`
is the only task-state authority. One current Goal belongs to the stable group,
including completed/waiting/paused. Completed remains visible; explicit clear
releases it, or a new delegated Goal may replace completed while preserving history.
Unfinished bindings are never silently overwritten.

New waits support only user/tasks. User waits require `question`, `impact` and
`resume_when` and genuine authorized user evidence; task waits require the main
Agent's evidence check and versioned `activate`. Automatic inputs cannot release
user waits, historical host waits or user pauses. New `wait(host)` is rejected;
host/tool/delivery failures are separate runtime facts. Pause stops reminders and
only Goal's Eyes registration until explicit user resume, not accepted work or
unrelated observations. Generic settled/task signals coalesce ACP reminders into
the original Core Inbox/main Agent under its unchanged tool policy.

## Capabilities, Eyes and message routing

The shared `CapabilityAssembly` composes installed provider tools/skills/resources
on real group/application owners, not another execution owner. Discovery/import is
inert; bodies load on demand, and skill loading grants no tools. Extension workers
retain hooks/commands/basic dialogs/providers, not model tools or UI interception.

Eyes' application binding ensures its shared user-local daemon via the public CLI;
Flow invokes public CLI argv without a daemon. Their tools return actual
`exit_code`/`stdout`/`stderr`; failure/cancellation is not proof no effect occurred.
Eyes JSON defines sources/routes/destinations; owner is management metadata, not
authentication or a restriction to the current group/workspace. Claw owns no
observer journal, event relay, creator-reply capture or report tracker. Goal separately
manages only its own task observation through the same CLI boundary.

Ordinary ACP inputs have no default chat recipient. Standard prompt returns a stop
reason/identifiers without assistant-message chunks; selected receipt reads expose
bounded saved results. TUI `acp_updates: 0` suppresses live automatic completions,
not history. Ordinary channel messages keep their captured reply bindings.

For explicit notification, `message.channels` lists live host bindings, then
`message.send` selects one currently bound channel of the current group per message.
The host revalidates and freezes its recipient; arbitrary recipients/groups and
broadcast are rejected. Sending changes neither default reply routing nor Goal
state. Optional `reference` correlates real output facts, such as a saved question ID,
not authority. Persisting a question, intake ACK and delivered output remain distinct.

## Prompt and memory

`build_system_prompt` separates a cacheable static base from mutable workspace,
memory and environment content. The workspace holds `IDENTITY.md`, `AGENTS.md`,
`SOUL.md`, `USER.md`, `MEMORY.md` and a one-time `BOOTSTRAP.md`. Session-scoped skill
bodies belong in the cacheable prefix; turn-scoped skill messages do not rewrite it.
Goal facts come through the optional capability, not hardcoded per-turn policy.

Memory combines daily logs, long-term Markdown and optional vector/BM25 search;
keyword search remains the fallback. Compaction flushes durable memory, summarizes
through the LLM and archives/replaces older context. Original history remains
readable evidence, not a branch-switch or tool-replay operation.

See [reference](/packages/claw/reference), [skills](/packages/skills),
[package README](https://github.com/litterzhang/xdog/blob/main/packages/claw/README.md)
and [assistant-core acceptance](https://github.com/litterzhang/xdog/blob/main/docs/claw-assistant-core-requirements.json).
Acceptance records distinguish verified behavior from remaining integration limits;
this page does not claim live-channel, whole-product or non-Linux verification.
