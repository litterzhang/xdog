---
title: Reference
---

Application boundaries, CLI/configuration, workspace files, private local input,
output receipts and host-owned child authority. Linux is the supported platform.

## Application boundary

Claw is an application operated through `xdog-claw`, configuration, channels and
documented private protocols. The root `xdog.claw` package re-exports no runtime
classes. Internal `Orchestrator`/`GroupRuntime`/`AgentSession` adapters remain
importable for application composition/tests, not as a supported public embedding API.

Reusable interfaces are in **`xdog.agent`**: Agent, host-owned `ToolRegistry`,
`xdog.agent.mcp.McpRegistry` and extension APIs. `xdog.agent.children` supplies inert
`ChildClient`/`ChildHandle` contracts/shared data, not child supervision/execution.
Claw owns `ClawChildRuntime`/storage/extension workers on the gateway's original
loop; Coding owns its separate child runtime and session SDK.

## CLI — `xdog-claw`

Subcommands of the console script, verified against current `--help`:

| Command | Does |
|---|---|
| `onboard [--config PATH]` | Setup wizard for provider/model, workspace/identity and tool selection; no hardcoded chat/image model default. |
| `gateway start [--config PATH] [--foreground] [--acp-socket PATH]` | Start the daemon, optionally in the foreground or with an ACP address override. |
| `gateway stop [--config PATH] · gateway status [--config PATH]` | Stop or inspect the selected gateway profile. |
| `tui [--config PATH] [--group GROUP]` | Attach a terminal client; group defaults to `main`. |
| `channel login --weixin [--config PATH] [--group GROUP] [--base-url URL]` | Authenticate Weixin and bind it to a group, not a new Agent per sender. |
| `generate-image PROMPT [--config PATH] [--workspace PATH] [--output-dir PATH]` | Generate local assets with the configured image tool/model. Also accepts repeated `--reference`, `--aspect-ratio`, `--image-size 1K/2K/4K`. |
| `--version · --help` | Version or root command help. |

```bash
uv run xdog-claw onboard
uv run xdog-claw gateway start
uv run xdog-claw tui --group main
```

For foreground operation, use `gateway start --foreground` instead. Image generation
must first be enabled and an image-output model selected during onboarding; it saves
local files, not chat image uploads. See the
[image/configuration details](https://github.com/litterzhang/xdog/blob/main/packages/claw/README.md).

## Configuration and workspace files

Default config is `~/.config/xdog/claw/config.yaml`, honoring XDG paths or `CLAW_DIR`.
`--config PATH` is a subcommand option, not a root model/permission override.

| Configuration | Role |
|---|---|
| `model · image_model` | Explicit configured chat and image models; image generation is independently opt-in. |
| `enabled_tools` | Host tool selection; `[]` disables tools, absent/null uses defaults. Selection grants no permission. |
| `groups` | Operator-defined group identity/workspace and model/reasoning options. |
| `gateway.max_concurrent_agents` | Background group-turn admission (default 3), not child concurrency. |
| `gateway.daily_reset_hour · gateway.idle_reset_seconds` | Current-context rotation settings; default daily reset is 04:00 UTC. |
| `data_dir · socket_path · acp_socket_path` | Data/native-socket paths and override of default private ACP input address. |
| `mcp_servers · mcp_credential_backend` | Explicit MCP configuration and selected credential storage backend. |
| `extensions · extension_dir · extension_settings` | Trusted extension selection and settings; not model-tool registration. |
| `child_presets` | Explicit operator-owned data-only child templates; no permissions/provider activation. |

No Goal/Eyes enable switch, retired task-scheduler `tasks_file`, queue-mode/debounce/
trigger-pattern or per-group-concurrency setting is a current feature. Old
`scheduled_tasks.json`/observer journals are not imported, migrated or replayed.
Create Eyes definitions only for explicitly requested work.

Workspace files shape the prompt and memory:

| File | Role |
|---|---|
| `IDENTITY.md` | Agent identity/name. |
| `AGENTS.md` | Operating instructions and behavioral rules. |
| `SOUL.md` | Persona and voice. |
| `USER.md` | User context. |
| `MEMORY.md` | Long-term memory snapshot. |
| `BOOTSTRAP.md` | One-time first-run setup, consumed then deleted. |
| `.xdog/goals/<goal-id>/plan.md` | Main-Agent plan. |
| `.xdog/goals/<goal-id>/progress.md` | Main-Agent versioned progress assessments. |
| `.xdog/goals/<goal-id>/tasks/*.json` | The sole task-state authority. |

Private application storage is separate from these artifacts:

| Path under `<data_dir>` | Owns |
|---|---|
| `inputs/inbox.sqlite3` | Application input envelopes/receipts and execution attempts. |
| `outputs/journal.sqlite3` | Independent outgoing messages and transport-attempt facts. |
| `groups/<id>/goals/registry.sqlite3` | Goal binding, revision history and reminder outbox, owned by the skills capability. |

## Private local communication and durable intake

- **Native Unix socket**: TUI messages/status/reset and negotiated controls. Actual
  connection/group/input/attempt/session/context ownership fences every operation.
  `run_ack` follows durable intake, not execution success. Staged submissions retain
  original IDs/images across ACK loss; unsent editable drafts remain local memory.
- **Default private Unix ACP endpoint**: `<data_dir>/acp/agent.sock`, overridden with
  `acp_socket_path` or `gateway start --acp-socket PATH`. Addresses use `group:<id>`
  and the actual advertised endpoint/session. Enqueue can omit cwd; an explicit cwd
  must match the existing group workspace. Standard prompt requires load-session cwd.
- **One Core Inbox**: ordinary TUI/Weixin/ACP inputs persist immutable envelopes
  before acknowledgement, including original reply bindings. Busy messages wait;
  they never implicitly steer. The Weixin poller durably accepts supported inputs
  before advancing its upstream cursor or volatile FIFO handoff.
- **Separate output facts**: pending/sending/delivered/unknown/cancelled outputs and
  proven-not-sent attempts do not equal Agent status. Original recipients are frozen;
  unknown sends are never automatically replayed.

`GroupConfig.max_queued_messages` bounds unfinished accepted inputs
(pending/claimed/running, default 50); accepted records are not evicted to make room.
It is distinct from removed queue policies and the background group-turn semaphore.
Claw does not batch independent ordinary messages into one turn. Explicit
steer/follow-up uses Agent's identified queue/checkpoints, not another Claw queue or
rollback of tools already started.

Connection/endpoint detachment detaches waiters, not accepted application execution
or storage. Receiver-only restart preserves pending/running work. Application
shutdown/crash marks started execution interrupted, never automatic model/tool
replay. Explicit original-input cancellation/retry remains separately fenced.
No socket grants tool approval or exactly-once effects. See
[ACP setup/recovery](https://github.com/litterzhang/xdog/blob/main/docs/claw-eyes-acp.md).

## ACP result reads and explicit notification

Ordinary ACP input has no default chat reply target. `xdog/enqueue` ACKs durable
intake; standard `session/prompt` returns stop reason and `_meta.xdog` identifiers,
**without agent-message chunks**. Results remain in receipts/transcripts.
TUI `acp_updates: 0` suppresses live automatic completions; explicit `1` is diagnostic,
not chat routing or a new recipient. Ordinary TUI/Weixin user replies keep their
original captured destinations.

`xdog/inbox_status` selected by `message_id` returns bounded `response`,
`response_offset` and `response_next_offset`. Each page is at most 16,384 Unicode
characters; offset is a strict integer 0..1048576. Follow the next offset until
null, restarting if `attempt_id` changes across explicit retry. List pages omit
response bodies. Reads never invoke a model, resend output or release Goal waits.

For an authorized notification, use these **`message` tool arguments**, not CLI
subcommands. First list bindings in the current operation:

```json
{"action":"channels"}
```

Then choose an exact returned ID:

```json
{"action":"send","channel":"EXACT_RETURNED_CHANNEL_ID","text":"Verified result"}
```

One send targets **one currently bound channel of the current group**. Arbitrary
group/recipient fields, multicast or routing inferred from ACP/Eyes labels are
rejected. The host revalidates the listed binding and freezes its recipient.
Sending returns independent `message_id`/`status`/per-output receipts; it does not
change the ordinary reply route or end the turn. Do not repeat sent text as an
ordinary reply or retry unknown sends. Optional `reference` only correlates facts,
for example a real saved Goal `question.id`, never grants authority. A child needs
an explicit permitted message-tool grant with the same group fence.

See the [complete ACP reply contract](https://github.com/litterzhang/xdog/blob/main/packages/claw/docs/acp-input-replies.md).

## Context and read-only history

A group has one logical main Agent. `/new` and `/reset` rotate idle context, not
Goals/identity; active children must first settle. Model/reasoning, active tools/
skills and access restrictions remain, while messages/counters/context-owned
extension entries start fresh. Unfinished Goals do not disable daily/idle reset.

`/sessions` and `/tree` inspect history; **Enter/v** opens readonly source. `/`
searches, Right/Left inspects/returns, `r` refreshes and Escape closes. There is no
activation or browser `n` new-context shortcut. `/transcript saved SESSION_ID
[BRANCH_ID]`, `/transcript branch BRANCH_ID` and `/transcript timeline [SESSION_ID]`
retain their negotiated group-scoped readonly source/copy/export/share boundaries.
Failed reads never substitute another live/archived source.

`/resume ID`, `/fork`, `/branch` and `/goto` execution controls are removed and
explicitly rejected. Legacy checkpoints/branches stay readable, never activated.
Startup restores only committed current context; uncertain requests/interrupted
effects are not automatically replayed. Coding branch navigation is unchanged.

## Goal tool and lifecycle

Claw includes `xdog-skills` by default: native `/goal` controls and the actual
owner-bound model tool need no enable flag/display worker. The skill body is
available but **not active by default**. Availability creates no Goal/model/worker.

| Command/action | Purpose |
|---|---|
| `/goal create OUTCOME · /goal status · /goal history` | Create a genuinely delegated outcome, inspect current state or read history. |
| `/goal pause [REASON] · /goal resume · /goal clear [REASON]` | Pause proactive work, explicitly resume user pause, or release current binding without deleting artifacts. |
| Model `plan · task_update · report · complete` | Save the main Agent's plan, CAS task updates, versioned progress assessment and evidence-based completion. |
| Model `wait · activate` | Save a genuine user/tasks wait; main Agent releases a satisfied wait with exact Goal ID/observed version/reason. |

The main Agent plans, optionally delegates through authorized host tools, reconciles
authoritative task JSON and reports in `progress.md` before initial work/every
continuation. There is no mandatory reviewer or Goal-owned worker/acceptance loop.
One current Goal belongs to the stable group across `/new`/reset. Completed remains
current/viewable; a newly delegated Goal may atomically replace completed while
preserving history. Only explicit clear releases it without replacement; unfinished
bindings cannot be overwritten. Legacy Goal files remain untouched/reported.

New waits support only `user` and `tasks`. User waits need nonempty `question`,
`impact`, `resume_when` and actual authorized user evidence; task changes trigger
checks, not automatic activation/acceptance. New `wait(host)` is rejected; host/tool/
delivery faults are separate runtime facts. Automatic input cannot release user
waits, historical host waits or user pauses. Pause stops reminders and only Goal's
own Eyes registration until explicit user resume, not accepted inputs/tools/children.

Saving a question is not delivery. Read status for the real `question.id`, and use
an authorized explicit send when needed. Optional readonly `question.delivery`
reflects host output facts: unavailable/untracked is not proof of no send, and
unknown/partial/cancelled is not delivered. Output/read receipts never release waits.
See [skills](/packages/skills) and the
[Goal skill](https://github.com/litterzhang/xdog/blob/main/packages/skills/src/xdog/skills/skills/goal/SKILL.md).

## Child settings and authority

```text
/agents start --label Inspector --instructions "Inspect only" --tools filesystem,current_time --fresh -- TASK
/agents start --preset inspector -- TASK
```

The preset example requires explicit operator `child_presets.inspector` configuration.
Starts are readonly by default; `--inherit` selects current host access policy, not
parent history or approval grants.

| Settings field | Meaning |
|---|---|
| `label · instructions · system_prompt` | Child label/task material/child-owned prompt; no parent conversation/system inheritance or permission grant. |
| `tools` | Frozen subset of actual live parent delegable active tools; `[]` selects none, null selects the delegable set. |
| `skills` | Subset of available parent skill bodies; default `[]` selects none, null selects the available catalog. |
| `tool_actions` | Literal action narrowing for multi-action tools, not wildcards/shell matching. |
| `tool_scopes` | Explicit supported capability scope for selected tools; no unrestricted fallback. |
| `inherit_context` | Must be false; new true values are rejected. `--fresh` makes this default explicit. |
| `model · thinking · preset` | Actual-adapter routing/reasoning or operator template; no increased permissions/authentication fallback. |

`--system-prompt TEXT` and `--skills slug1,slug2` are supported child flags.
Actions/scopes use JSON settings. Named presets grant no recursion, tool approval,
privileged role or provider activation. Children never receive a writable parent
history/system/policy manager; approvals and extension state are independent.

For Goal, an explicit scope selects current `goal_id` and existing assigned
`task_ids`. It grants only per-task reads/CAS status/summary/evidence updates, never
creation/reassignment, whole-Goal reports/plans or lifecycle control. Readonly still
forbids writes. Existing child results may be saved during Goal wait/pause without
releasing it; completion/clear/replacement fences old scopes.

Actual identity/schema/action/scope/skills and permissions are checked at admission,
after approvals and executor entry. Effective configuration, tool schema ceilings
and filesystem roots persist before model execution. Continuation revalidates the
original live owner and saved ceilings, never borrowing broader later grants.
Missing legacy filesystem metadata means deny-all, not unconfined access. Saved
records restore no live registry handles or approval caches.

The main Agent manages children; no fixed lifetime/call/token/concurrency/record
quota exists. Removed `--timeout`/`--calls`/`--tokens` and child `timeout`/
`max_model_calls`/`max_tokens` keys are rejected for new work; historical values are
inert, not restored budgets. Actual model context/output/vision limits, permissions,
protocol/image/per-operation storage bounds and operation deadlines remain.
Parent reply completion does not close working children; native compaction preserves
original history durably when model metadata supports it. Cancellation/shutdown
settle actual owners, never automatically replay interrupted effects.

## Installed Eyes and Flow tools

Installed optional packages contribute application-scoped tools/discoverable skills
through `CapabilityAssembly`. Discovery/import is inert; bodies activate on demand,
never grant tools or auto-install a package. Selection/readonly/permissions and
frozen child grants still apply.

Eyes uses the shared independent user-local daemon, normally
`~/.local/share/xdog/eyes` (honoring `XDG_DATA_HOME`). Opening the binding ensures
daemon availability but no observation; closing detaches without stopping accepted
registrations. Tool calls invoke the public CLI:

```json
{"action":"create","owner":"my-watches","definition_path":"/absolute/project/eyes.json"}
```

Every action needs caller-chosen `owner`. `create` additionally requires an absolute
saved `definition_path`, not `workspace`; status/pause/resume/cancel use returned
`observer_id`. JSON handlers select destinations, not the current group/workspace.
Owner is management metadata, not authentication. Enqueue resolves existing target
cwd; explicit cwd asserts consistency, while standard prompt requires load-session
cwd. Eyes tracks observation/handler delivery, not Agent/chat completion. No
`cancel_notifications`, observer journal or creator-reply route exists in Claw.

Flow has no daemon. Its tool invokes public CLI argv, not a second workflow API:

```json
{"arguments":["validate","/absolute/project/workflow.json","--json"]}
```

Optional `cwd` must be an absolute existing directory. Noninteractive validate/run/
generate/graph/test/scheduling/help are supported; `build` remains manual interactive
CLI only. Both tools return actual `exit_code`/`stdout`/`stderr`; nonzero exit is
failure. Workflow code/imports/models/scheduling may have effects even during
validation, so host authorization still applies. Cancellation settles the owned
CLI wait/process, not proof no durable registration/effect was accepted. Unknown/
interrupted effects are not blindly retried.

See [Eyes](/packages/eyes), [Flow](/packages/flow),
[full package README](https://github.com/litterzhang/xdog/blob/main/packages/claw/README.md)
and [current acceptance](https://github.com/litterzhang/xdog/blob/main/docs/claw-assistant-core-requirements.json).
These are host policy/recovery boundaries, not an OS sandbox or proof of live
external-channel, non-Linux or complete whole-product validation.
