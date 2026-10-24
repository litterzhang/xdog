---
title: Design
---

Agent owns one conversation/tool loop. Applications own admission, permissions,
resource lifetimes, durable storage and child execution. There is no second loop
inside tool registration, skill discovery or capability assembly.

## One loop and explicit request adapters

`agent_loop` streams a model, validates tool calls, executes them, appends results
and repeats until the turn ends. Its internal outer/inner scheduling handles
follow-ups and steering; these are not separate Agent runtimes. Tool execution
is parallel by default, with serial preparation/finalization hooks.

`StreamFn` decouples the loop from provider authentication and wire formats.
`stream_fn_from_provider()` bridges the actual AI provider/runtime and its cached
model metadata, or a test double. Agent does not choose fallback credentials.
`AgentState` is replaced rather than mutated in place; events and input queues
let hosts observe/control execution without taking ownership from the Agent.

## Tool definitions, bindings and MCP are distinct

`AgentTool` supplies an async executor. `ToolDef`, `Param` and `@action` offer a
declarative multi-action layer. A host constructs its own `ToolRepo`, which
creates actual bash/filesystem/current_time tools by default; `builtin=False`
starts empty. `register(*tools)` atomically registers actual tools and returns a
close receipt; explicit `unregister(name)` is required before replacement.
`select(names)` preserves actual identity in a new subset. `publish(agent)`
updates ordinary inventory only at the original idle owner. Imports do not mutate
a global registry or authorize tool use.

`submit_result` is opt-in through its concrete tool constructor. Bare Agent
supplies no default shell/filesystem tools. Injected `web_search_fn`/`embed_fn`
add their respective tools. The host's pre-tool policy must authorize the actual
call after transformations; generic tools are not a security boundary.

`McpRegistry` separately owns configured MCP connections and fenced proxies.
Agent consumes it via `mcp_registry=...`, merging visible tools for the same
model/tool loop and hooks. MCP proxies do not enter ordinary `ToolRepo`.
Declaring a server is inert; connection is explicit. Duplicate names fail rather
than overriding another source. `set_tools()` replaces only ordinary inventory;
publication never widens an explicit active-tool selection. Agent does not close
a borrowed MCP registry, load its credentials from snapshots or replay calls.

## Capabilities and skills do not grant authority

Trusted installed `xdog.capabilities` entry points declare optional tools,
skills, resources and extension hooks on actual application/session/group owners.
Discovery is inert. Configure `CapabilitySet(scope, tool_repo=..., skill_repo=...)`
then `.select(names).open(host)` before startup: only selected providers open.
Application scope may omit host; session/group need actual owner ports.
`CapabilityAssembly.create_agent(tool_repo=..., skill_repo=...)` adds repos by
union to capability and Set contributions; supplemental empty selections cannot
remove those contributions. Conflicts fail; there are no separate enable lists.
Native Agent keeps `tools` / `skills`. Close Agents before their Sets; assembly
owns no extra runtime, permission gate, child scheduler or event loop.

`SkillRepo(builtin=True, packaged=None)` discovers packaged skills by default;
`.register(directory=path, name="host")` adds local sources and is chainable.
Different same-slug definitions and directory-name conflicts fail, rather than
silently overriding another source. `from_packaged` / `select` / `merge` are SDK
operations, not custom host loaders. The read-only `skill` entry returns metadata
and instructions on demand without activating them or granting permissions.
Flow/Eyes/Goal bodies are not activated by default. Explicit `active_skills` /
`set_active_skills()` still control session/turn lifetimes independently of tools.
Extensions retain hooks, commands, basic UI and providers, not model tools.

Opening selected Eyes ensures its independent daemon; closing detaches, not daemon
stop. Flow CLI execution does not inherit host sandbox, approval hooks or tool
allowlists; workflow execution is trusted code, not OS isolation.

## Snapshots and opt-in compaction

`dump()` exports versioned JSON at a settled checkpoint; `restore()` validates
before atomic adoption without starting a model/tool. Hosts reinject adapters,
tools, skill repos, hooks, parent handles, MCP registries and compaction policy.
Snapshot data does not import code or restore authority. For long histories,
`dump_metadata()`/`restore_parts()` separate metadata from bounded message parts
while retaining complete tool pairing. Recovery dumps are diagnostic, not replay
authority for unconfirmed effects. Snapshot events are not disk-commit ACKs.

Automatic compaction defaults off (`CompactionConfig.auto=False`). Explicit opt-in
uses the executing adapter's real limits or trusted host capacity/output reserves,
not a guessed fallback window. Manual `compact()` requires idle, paired history
and does not enable auto mode. Summary/cleanup failure retains original history;
archival and persistence are host-owned. Compaction manages context, not task
lifetime or cumulative work budgets.

## Cancellation and children

Steering/follow-up queues are Agent-owned; identified inputs expose selected and
consumed states. `abort()` is a cooperative cross-thread request. On the original
loop, `cancel_and_wait()` cancels/joins the producer; `aclose()` settles owned
cleanup. Cancellation cannot kill blocked native code, undo tool effects, revoke
approval or justify automatic replay.

`ChildClient`, `ChildHandle`, `ChildBackend`, `ChildSpec` and `ParentHandle` are
inert SDK contracts/data access. Coding owns its child runtime/loop; Claw owns its
child runtime and borrows its application loop. Their stores, workers and grants
are not an Agent SDK supervisor service. Children require current parent-subset
authority; saved records, role labels and skill text grant no access.

See [reference](/packages/agent/reference),
[registry contract](https://github.com/litterzhang/xdog/blob/main/docs/host-owned-tool-registry.md)
and [capability SDK example](https://github.com/litterzhang/xdog/blob/main/docs/sdk-capability-assembly.md).
