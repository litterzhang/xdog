---
title: Reference
---

The `xdog-coding` CLI, interactive controls, permission policy and Coding session SDK.
Linux is the supported platform. Permissions are host policy, not an OS sandbox.

## CLI — `xdog-coding`

One command, not subcommands. Positional `FILES...` include file contents in the
initial context. Current `--help` exposes:

| Flag | Does |
|---|---|
| `-m / --model MODEL_ID` | Select a model on the actual configured adapter; use its available catalog. |
| `-r / --resume` | Open the current-directory session selector, newest first; not automatic latest-session execution. |
| `--resume-id ID` | Restore one saved session directly, including print usage. |
| `-p / --prompt TEXT` | Send an initial prompt non-interactively. |
| `--print · --output-format FORMAT` | Print output as `text`, `json` or `markdown`. |
| `--working-dir DIRECTORY · --config FILE` | Select the working directory or settings file. |
| `--thinking-level LEVEL` | `off`, `minimal`, `low`, `medium`, `high`, `xhigh`; actual model support still applies. |
| `--permission-mode MODE` | `ask`, `ask-all`, `allow-all`, `deny`. |
| `--list-models · --pick-session` | List available models or open the interactive current-directory session selector. |
| `--acp` | Run standard stdio ACP; optional `--session ID` pins it to an existing owner. |
| `--session ID --prompt TEXT` | Send through ACP to an exact live session, not history restoration. |
| `--session ID --enqueue --message-id ID --prompt TEXT` | Persist stable-ID input without starting ACP/model/Agent; return a durable intake receipt. |
| `--verbose · -h / --help` | Verbose logging or command help. |

```bash
uv run xdog-coding
uv run xdog-coding --list-models
uv run xdog-coding -r
uv run xdog-coding --print --output-format json --thinking-level off --prompt "Summarize this repository"
```

`--resume`/`--pick-session` cannot combine with `--print`; use `--resume-id ID`.
Exact-session routing cannot override the target's model, thinking, permissions,
cwd or config, and cannot combine with `--resume-id`. Enqueue additionally requires
a stable message ID, cannot combine with other modes/configuration overrides, and
returns intake status rather than execution success. Same-ID/content enqueue retry
is safe for ACK loss, not a guarantee of exactly-once tool effects.

## Model, settings and permission policy

The TUI's `/model` browser and `/model MODEL_ID` use the owning adapter's actual
catalog and capability metadata. They do not silently log in, sync catalogs or
activate another provider. Reasoning choices are model-dependent; selecting a model
or skill does not expand tool permissions.

Model/thinking/active-tool choices use caller/CLI overrides over saved explicit
session choices, project `.coding/settings.json`, then global
`~/.config/xdog/coding/settings.json`. Permission startup policy resolves CLI >
project > global. `allowed_tools: []` selects no tools; absent/null falls through
to a lower layer. This selection is separate from authorization.

| Mode | Policy |
|---|---|
| `ask` | Default. Classified filesystem reads/current time and exact host-bound inspection may run automatically; mutating/execute/unknown calls ask. |
| `ask-all` | Ask before every tool call, including otherwise exempt child correspondence. |
| `allow-all` | Explicit trusted unattended execution; original readonly, identity and delegation ceilings still apply. |
| `deny` | Allow classified safe reads/exempt existing-child correspondence; reject other protected calls. |

Default `ask`/`deny` also exempt the actual host-bound child tool's list/presets/
inspect/wait/steer/follow-up. This grants no authority to the child's subsequent
tool calls. Spawn/cancel/close, prompt changes and queue management are not exempt.
Interactive approvals offer allow once, allow for this session **for the same exact
call**, or deny. Without an approver, protected unattended calls fail closed unless
`allow-all` was explicitly selected. `/readonly` remains a separate host restriction.

## Slash commands

Core commands and native TUI controls include:

| Command | Does |
|---|---|
| `/help [QUERY] · /keys` | Search available commands or inspect effective keybindings. |
| `/model [MODEL_ID] · /thinking [LEVEL]` | Inspect/select model or supported reasoning; levels match the current CLI vocabulary. |
| `/permissions [status or MODE] · /readonly on/off/status` | Inspect/change current-session approval policy or separate readonly scope. |
| `/compact [FOCUS] · /compact cancel · /clear` | Compact history, cancel native compaction, or clear conversation history. |
| `/session · /status · /sessions · /tree` | Current metadata/status or saved-session/branch browsers. |
| `/resume ID · /new` | Activate saved history or create a session under current ownership checks. |
| `/fork [INDEX] · /branch [SESSION] ID · /goto INDEX` | Explicit Coding branch/checkpoint navigation; mutating route changes require idle ownership. |
| `/name [NAME or -]` | Inspect/name the current session; `-` clears its explicit name. |
| `/steer TEXT · /follow-up TEXT · /queue` | Explicit in-run input and queue inspection/control; not cancellation/rollback. |
| `/inbox list [OFFSET] · /inbox show ID` | Read automatic Inbox records without starting work. |
| `/inbox cancel ID · /inbox retry ID` | Separately fenced intake cancellation or explicit interrupted-work retry. |
| `/agents · /agents start TASK · /agents inspect ID` | Host-owned child roster, start (readonly by default), and retained-source inspection. |
| `/tools [QUERY] · /mcp · /extensions` | Tool inventory, explicit MCP connections and trusted extension management. |
| `/skills · /unload [NAME or all]` | Discover skills or deactivate bodies; loading never grants tools. |
| `/goal create OUTCOME · /goal status · /goal pause [REASON] · /goal resume · /goal clear [REASON] · /goal history` | Installed native Goal controls, independent of the optional readonly display worker. |
| `/quit · /exit` | Settle owned resources and leave. |

Session/model/skill/policy/navigation changes retain their actual idle/child-owner
checks. The old JSON-lines frontend-control `--rpc` mode is removed; private
extension-worker RPC, MCP JSON-RPC and separate ACP intake remain.

## Automatic input and Goal

The independent receiver is explicit:

```bash
uv run python -m xdog.coding.acp.ingress_main --help
```

It can persist inputs while owners are offline, without constructing an Agent.
An activated owner automatically consumes pending messages. Fixed batches use
`max_messages_per_turn` (default 10; strict integer 1..128), preserving independent
input identities/sources/attachments with one shared response stream. Acceptance
needs no message-approval dialog; protected tools still use the policy above.
Unknown/interrupted effects are not automatically replayed.

`xdog-skills` is a default dependency. Its Goal skill body is discoverable, **not
active by default**. Goal is bound to the exact Coding session/branch. The main
Agent plans and accepts evidence, saves `progress.md` reports before continuing,
and updates authoritative task JSON. Completed remains current/viewable; clear
releases it, or a new delegated Goal may replace completed while keeping history.

New waits support only user/tasks. User waits require `question`, `impact`,
`resume_when` and actual authorized user evidence; task waits require evidence-based
versioned `activate`. Automatic inputs cannot release user waits, historical host
waits or user pauses. Pause stops reminders and only Goal's observation until
explicit user resume, not accepted tools/children. A saved question is not delivered
output. See [skills](/packages/skills) and the
[Goal skill](https://github.com/litterzhang/xdog/blob/main/packages/skills/src/xdog/skills/skills/goal/SKILL.md).

## Child configuration and authority

```text
/agents start --label Inspector --instructions "Inspect only" --tools filesystem,current_time --fresh -- TASK
/agents start --preset inspector -- TASK
```

The second example requires an explicitly configured `inspector` preset. Global
`child_presets` are data-only templates, not project/session code or permission
grants. `--inherit` inherits access policy, never history or approvals.

| Field | Meaning |
|---|---|
| `label · instructions · system_prompt` | Child label, first-user task material and child-owned prompt content; no parent system/history copy or permission grant. |
| `tools` | Frozen delegable parent tool subset; `[]` selects none, null selects the host's delegable set. |
| `skills` | Available parent skill subset; default `[]` selects none, null selects the available catalog. |
| `tool_actions` | Narrow multi-action tools to literal actions, not shell patterns or wildcards. |
| `tool_scopes` | Explicit supported capability scope for selected tools; unsupported scopes fail closed. |
| `inherit_context` | Must be false; true is rejected for new work. `--fresh` explicitly selects this default. |
| `model · thinking · preset` | Actual adapter model/reasoning override or configured preset; no fallback authentication or extra authority. |

`--system-prompt TEXT` and `--skills slug1,slug2` are supported child flags;
actions/scopes use JSON settings. A Goal child scope selects the exact current
Goal and existing assigned task IDs; it allows only task reads and CAS updates of
status/summary/evidence, not planning/reports/lifecycle or task creation/reassignment.
Readonly still forbids writes.

The host rechecks actual tool identity/schema/action/scope, skills and permission
ceilings at admission, after approval and executor entry. Effective configuration,
tool schema ceilings and filesystem roots persist before model execution; explicit
continuation cannot borrow later broader grants. Missing legacy filesystem metadata
means deny-all, not unconfined. Child approvals/state are independent; saved records
restore no live registry handles or approvals. No fixed lifetime/call/token/
concurrency/retained-record quota replaces these real limits.

## SDK entry points

Coding exposes a session SDK through `xdog.coding.core.sdk`:

| Name | Purpose |
|---|---|
| `create_agent_session(options)` | Synchronously create/restore an owner-bound session and return `CreateSessionResult`. |
| `CreateSessionOptions` | Working directory, model/thinking options, explicit restore/configuration and overrides. |
| `CreateSessionResult` | `.session` and optional `.model_fallback_message`. |
| `AgentSession` | Conversation/event/persistence wrapper over the shared Agent runtime. |
| `await session.aclose()` | Idempotent close that settles actual child/capability/extension/MCP cleanup. |

Use `await session.aclose()` in `finally`. Synchronous `dispose()` requests the
same close and returns a concurrent Future; do not block an async caller loop with
`.result()`. Async applications can offload synchronous creation via
`asyncio.to_thread`. This is Coding's SDK, not a promise of public Claw runtime
embedding or Agent-owned child execution.

See the [package README](https://github.com/litterzhang/xdog/blob/main/packages/coding/README.md)
and [ACP routing protocol](https://github.com/litterzhang/xdog/blob/main/docs/acp-session-routing.md)
for detailed user configuration and retained APIs.
