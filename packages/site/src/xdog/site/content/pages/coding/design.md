---
title: Design
---

Coding composes ai + agent + tui into a terminal coding assistant, with one
controller per active session, durable context and host-owned execution policy.

## Application composition

`core/sdk.py` resolves layered configuration and the actual model adapter, creates
a host-owned `ToolRegistry`, and builds `xdog.agent.Agent` inside an `AgentSession`.
The SDK supplies default factories for filesystem/bash/current-time tools. Installed
capabilities and caller-provided tools join that host's inventory; imports do not
register mutable process-global tools. MCP uses `xdog.agent.mcp.McpRegistry`, not
`ToolRegistry`, while Agent merges both visible inventories for the same model loop.

Interactive TUI and one-shot print mode adapt that same session core. The TUI renders
Agent events and manages local drafts/approvals, not another execution engine.
Print supports text/JSON/Markdown. Coding also has a supported session SDK; this is
distinct from Claw's application-only runtime boundary.

## Sessions, navigation and durable writes

JSON-persisted `SessionData` records messages, settings, usage, summaries and
branches. Explicit session/branch/checkpoint navigation rebuilds the selected
context under its original policy. This is a Coding feature, not Claw's historical
branch activation. Pre-turn prompt preparation and compaction use owned IO;
pre-compaction checkpoints preserve original paired tool history.

TUI, SDK, print and ACP share the session controller and writer lease. Store-anchored
locks under `<sessions>/.owners` prevent competing writers; registry/socket handles
are not restored from snapshots. Ordinary turn history commits on an owned worker
before saved state/producer completion is published. A failed or uncertain commit
fences further turns instead of replaying tools or silently overwriting newer state.
Close/reload/inspect explicitly before continuing uncertain execution.

## ACP is input, not a frontend-control protocol

`--session ID --prompt TEXT` sends to the exact live owner without constructing
another Agent. It is not `--resume-id ID`, which explicitly restores saved history.
`--acp` provides standard stdio ACP; an independently managed lightweight Unix
ingress can durably accept session input while the TUI/owner is closed, without
creating `AgentSession` or silently resuming it.

An activated owner automatically consumes pending Inbox input. There is no message
approval, human wake-up budget or UI interceptor; actual protected tool calls still
use the host's permission policy. A fixed batch preserves each input's identity,
source and attachments while sharing one response/tool stream.
`max_messages_per_turn` defaults to 10 and accepts strict integers 1..128.

Stable-ID enqueue retries handle intake ACK loss. Standard prompt outcome-unknown
is not blindly resent; started crashed execution becomes interrupted, not automatic
side-effect replay. The old JSON-lines frontend RPC is removed, independently of
retained ACP, MCP JSON-RPC and private extension-worker communication.

## Settings, models and authority

Startup model/thinking/tool selection uses explicit caller/CLI overrides, then saved
explicit session choices, project `.coding/settings.json`, global
`~/.config/xdog/coding/settings.json` and defaults. Permission mode resolves from
CLI, project and global policy; `/permissions` changes the current session only.
Trusted extension/preset configuration has its own global-only boundary, not an
arbitrary project/session code-import grant.

Model selection resolves on the actual stream adapter's metadata/catalog; thinking
uses `off|minimal|low|medium|high|xhigh` within that model's supported capabilities.
It does not silently authenticate, activate a provider or borrow another adapter's
metadata. Explicit `off` remains off. Unknown capability metadata is not a grant.

The pre-tool host gate owns approvals and readonly checks, not generic Agent tools.
Classified reads can run automatically; mutation/execute/unknown calls require
approval in default `ask`. Unattended mutation requires explicit `allow-all`.
An allowed tool list selects tools, not authorization; `[]` selects none. Session
approval caches do not authorize children, and permission gates are not an OS sandbox.

## Host-owned children

`CodingChildRuntime` and its durable store, isolated extension workers and return
leases belong to Coding. Agent supplies inert `ChildClient`/`ChildHandle` contracts
and shared data/policy helpers, not a child supervisor or execution host. The
Coding-owned child loop/thread exits while idle; a safely completed child's explicit
new message rebuilds a fresh Agent from retained history under revalidated grants.
Interrupted effects never automatically replay.

A child never copies the parent's conversation/system prompt. Task knowledge is
first-user material; child prompt/model/reasoning may be specialized. Tools and
available skills are frozen subsets of the actual live original parent's authority.
`tool_actions` narrows literal actions; capability-specific `tool_scopes` must be
explicitly supported. The host checks identity/schema/action/scope/skills at
admission, after approvals and actual executor entry. Pending parallel calls cannot
borrow later broader grants. Effective configuration, tool schema ceilings and
filesystem execution roots persist before model execution and fence continuation.
Missing legacy filesystem constraints mean deny-all, not unconfined access.

No fixed child lifetime/call/token/concurrency/retained-record quota is imposed.
Actual model capacity, per-operation protocol/image/storage bounds, permissions and
owner cancellation remain. Long loops use native compaction only with actual model
metadata and durable original-history archives. Trusted code is not OS-isolated.

## Skills, Goal and installed capabilities

Skills are discovered through Agent's generic `SkillManager` and `SKILL.md` files,
not retired Coding-specific skill/extension loaders. Session bodies belong in the
cacheable prompt prefix; turn-scoped bodies are request messages. Trusted extensions
retain hooks, commands, basic input/select/confirm dialogs and providers, not model
tools, custom renderers, forms or terminal interception.

The default skills hub contributes native `/goal` commands, an owner-bound model
tool and discoverable instructions independently. The Goal body is not active by
default; availability creates no Goal/model/worker. The main Agent plans, checks
actual task evidence, reports in `progress.md` before each continuation and judges
completion. Tasks live only in workspace JSON files. Completed remains current;
clear releases it, or a new delegated Goal may replace completed without rewriting
history. Waits support user/tasks; user waits and pauses cannot be released by
automatic inputs. Host faults do not create `wait(host)`.

Generic settled/task signals coalesce ACP reminders into the original controller,
not another Agent loop. Optional Goal task observation uses the public Eyes CLI and
shared daemon. Separately installed application-scoped Eyes/Flow tools also use
public CLI processes. Opening Eyes ensures the shared daemon but no observation;
closing detaches without stopping registrations. Flow has no daemon. Their tools
retain original selection/readonly/approval and child-delegation fences.

See [reference](/packages/coding/reference), [skills](/packages/skills) and the
[full package README](https://github.com/litterzhang/xdog/blob/main/packages/coding/README.md).
