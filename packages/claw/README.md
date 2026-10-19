# xdog-claw

> Platform scope: **Linux only**. Non-Linux adapters and native-platform support
> are outside the current product scope; historical audit evidence is not a support promise.

**Persistent multi-channel assistant application.**

A local-first gateway with one logical main Agent per group, optional durable Goals,
supervised task Agents, long-term memory, and JSON/JSONL conversation persistence.
Eyes provides opt-in observations and scheduling through the existing private group
ACP inbox; SQLite is used for durable intake/output, optional Goal journals and
optional memory-search indexes.

## Application and SDK boundaries

Claw is an application, not a separately supported public Python SDK for embedding
its gateway or group/session runtime. Use `xdog-claw` CLI/configuration, the terminal
and messaging channels, and the explicitly enabled private ACP ingress below.
The `xdog.claw` root package does not re-export runtime classes. Classes such as
`Orchestrator`, `GroupRuntime` and `AgentSession` in internal modules are application
wiring, not a stable embedding contract.
Its session methods adapt the shared runtime for the gateway; they are not a second
client SDK alongside Coding's SDK.

The reusable Agent SDK is **`xdog.agent`**. Its MCP client
(`xdog.agent.mcp.McpManager`), extension API (`xdog.agent.extensions.ExtensionAPI`)
and child supervision (`xdog.agent.supervision.ChildSupervisor`) live in that
layer. SDK/embedding examples below refer to those shared interfaces or explicitly
to Coding's SDK, not to a public Claw SDK.
Claw retains its product controls for MCP, trusted extensions, children, Goals,
memory and history; its internal adapters apply group ownership and tool policy.
The native gateway socket and opt-in ACP endpoint are private local communication
surfaces, not a hosted API or permission bypass. Native clients must negotiate
capabilities and preserve ownership, cancellation and no-blind-replay fences.

## Interactive terminal

`uv run xdog-claw tui` connects to the configured gateway using the main terminal
buffer and native scrollback. It shares Coding's Unicode multiline editor and
bounded layout. Native todos summarize transient work; optional feature display
belongs to Python extensions, not native Goal state. Queued messages keep their
full text while only a compact preview appears.

Ctrl+L repaints the visible UI without clearing scrollback, the draft, or focus.
The terminal cursor is visible in the focused editor and hidden while inspecting
details or approving a tool; returning to input restores it at the draft position.
Streaming updates use bounded FIFO batches to keep input responsive.

Ctrl+O opens recent tool/reasoning details. Left/Right changes entries and Page
Up/Page Down scrolls. Escape closes autocomplete/details before cancelling a
run. Cancellation and busy rejection restore submitted and queued text ahead
of a newer draft; input submitted during cancellation stays editable. Ctrl+Z
suspends on POSIX. Remote coding and fullscreen application modes are excluded.
The client waits for a fresh session handshake before sending on reconnect.
Offline submissions stay in the editor. Connection loss restores active/queued
text for **explicit** resubmission; it never replays uncertain requests itself.
A restored draft does not undo completed tool effects: review before resending.
`/status` preserves active-run timing; `/reset` and `/clear` are blocked while busy.
EOF, terminal errors and exit cancel socket work, including blocked reads/drains
and reconnect waits.

See [terminal verification](../../docs/tui-pi-parity.md) for tests and limitations.

The native Unix-socket gateway remains, but remote parent-draft negotiation/control
and extension custom/form/frame/terminal-hook transports are removed. Primary
snapshots are local client save/recovery operations, not gateway requests. Coding's
former `--rpc` JSON-lines frontend is removed too; this does not remove the private
extension-worker protocol, MCP JSON-RPC or Coding's separate ACP session ingress.

## Eyes and ACP callbacks

Opt in to a private Unix ACP endpoint on the existing gateway:

```bash
xdog-claw gateway start --foreground \
  --acp-socket "$HOME/.local/state/xdog/claw/acp/agent.sock"
```

Point Eyes' **generic ACP handler** at that socket with `session: "group:main"`
and `delivery: "enqueue"`, from the group's exact workspace. The stable group
address follows its current AgentSession; it is not a TUI or exact transcript
address. Inputs first enter a durable inbox, then automatically use the existing
Orchestrator queue and tool policies. No message approval, second Agent loop, or
TUI connection is needed. Standard ACP prompt is also supported and waits for
its actual result. Interrupted execution is not automatically replayed.

See [Claw/Eyes ACP design and setup](../../docs/claw-eyes-acp.md) for definition
examples, scope/receipt/cancel semantics, recovery and limits. ACP is disabled by
default; stopping Eyes does not stop Claw or retract accepted messages.

## Scheduling: gateway-managed Eyes

Install managed observation with `pip install 'xdog-claw[eyes,acp]'`. Eyes is
an optional dependency; croniter is owned by Eyes (`croniter>=2.0,<7`), not a
direct Claw dependency. The scheduler source is in Eyes; Claw owns its
group-scoped observation intent and Core input/output receipts.

The group-scoped `eyes` tool exposes `create(name, source, instruction)`,
`list()`, `status(observer_id)`, `cancel(observer_id)` and `cancel_notifications(observer_id)`. `source` is an original Eyes source object.
The host binds the current group's workspace/private ACP endpoint and destination;
callers cannot redirect observers into other groups. When that tool is enabled,
prefer it over manually wiring a standalone CLI registration. The old `task` tool
and old timer engine are removed. A registered `eyes` tool alone does not mean
management is enabled; disabled operations return a clear configuration error.
Standalone Eyes CLI/ACP setup above remains available and unchanged; Coding keeps
its exact session/project/branch semantics, unlike Claw's active group conversation.

The agreed host configuration is opt-in (`eyes_enabled: bool = False`):

```yaml
eyes_enabled: true
```

With the default `false`, no ACP endpoint or observer is automatically started.
When enabled, the gateway reuses configured `acp_socket_path` or owns the private
`data_dir/acp/agent.sock` endpoint, with no extra daemon. The default tool registry
registers `eyes`; disabled operations must report an explicit error. Trusted tool
context binds `_eyes` to the host, never to a model-selected host. These settings
are managed by this gateway and joined during shutdown. Explicit standalone ACP
opt-in is unchanged.

Managed time recovery must retain an unadmitted `at` through downtime without
silently losing it, while interrupted or unknown execution must **not** be
automatically replayed. Execution status and report-delivery status must remain
separate; queued receipt is not completed work. Cancelling an owned pending
callback requires explicit input withdrawal; stopping observation alone leaves
accepted execution/reporting intact. `cancel_notifications` stops unsent reports
without stopping observation or execution. Each actual assistant message uses
Core output receipts and the original creating input's recipient/context, even
before the whole turn finishes; there is no final aggregated report resend.
Observer status mirrors those receipts, not a separate sending state machine.
Unsent outputs become `cancelled`; already delivered or unknown sends retain
those facts. An unavailable original recipient leaves output pending without
broadcasting to another peer or running the model again. No operation undoes
started effects. Ordinary file observation must not automatically resume after
gateway restart. See `docs/observer-message-output-verification.md` for the
source-runtime evidence and remaining cold-artifact gate.

Eyes intervals have a monotonic **fixed phase**, not a delay after completion.
The **2026-10-18 decision** removes the legacy task scheduler and all migration
compatibility: no `tasks_file` configuration and no reading or importing
`scheduled_tasks.json`, whether Eyes is enabled or disabled. Old user data stays
untouched: do not delete, rewrite, back up for migration, or convert it into Eyes
records or confirmation states. Users may explicitly request new Eyes observers;
never automatically translate old schedules or replay missed periods.

Eyes scheduler sources and generic historical transcript labels remain; the implicit Goal runner is removed.
See [Scheduling with Eyes](SCHEDULER.md) for the tool contract, source examples,
recovery requirements and bounded storage limits. A known not-sent report can
recover without a second Agent run; unknown channel effects are not automatically
retried. The revised removal scope is **in implementation/verification**; earlier
regression evidence applies to the superseded migration implementation, not final
acceptance of this decision. Current status is tracked in
[`claw-eyes-scheduler-requirements.json`](../../docs/claw-eyes-scheduler-requirements.json).

## Optional group-owned Goals

Claw is a long-term personal assistant; task autonomy is optional. Install the
optional distribution with `pip install 'xdog-claw[goals]'`. Package presence
provides the Goal capability automatically; there is **no Goal enable setting**.
The built-in `goals` skill is discovered just like Flow/Eyes, without copying or
registration, and its full body loads through the existing skill mechanism.
Without the package, ordinary chat/memory/tools/history open no Goal storage.

Availability is not delegation: ordinary conversation, loading a skill, opening
a TUI or receiving a callback does not create a Goal or worker. An explicit user
outcome is still required before the Agent creates a contract.

The same independent GoalService is used by Coding and Claw. Goal owns its private
leased journal at `<data_dir>/groups/<id>/goals/journal.sqlite3`; Claw owns ordinary
input, execution receipts and conversation history, not Goal tables. The packaged
[goals skill](../goal/src/xdog/goal/skills/goals/SKILL.md) and its
[worker](../goal/src/xdog/goal/roles/worker.md)/[reviewer](../goal/src/xdog/goal/roles/reviewer.md)
roles describe explicit planning, assignment, reporting, acceptance and review.
Derived workspace files are projections, not execution authority.

For the shared Python display, include `goal_ui` in the existing trusted global
`extensions` list. It loads directly from the Goal package; no copy or separate
Goal enable flag. `/ext-goal_ui-goals [GOAL_ID | page OFFSET]` queries the actual
group-owned read-only view, while `/goals` remains skill loading. The generic
shared TUI ExtensionView renders its status text; the main TUI knows no Goal
schema or lifecycle. Context rotation rebinds the fresh parent to the same group
service. Actual child runtimes do not inherit its grant, and unloading/crashing
the display does not cancel work, notifications or observations. See the source
and installed runtime evidence in `docs/shared-goal-extension-verification.md`
and `docs/observer-message-output-verification.md`; final whole-objective audit
and whole-branch review remain in progress.

A submitted worker report is not an accepted task; an accepted task is not a
completed Goal. **Only `request_complete` starts independent review.** Ordinary
turn end, idle and active Goal state never start a reviewer or a new model loop.
The reviewer has actual readonly tools and must cover original conditions and
fresh evidence. Criteria changes and user-pause release require actual consumed
human input; event provenance and anonymous steering cannot grant that authority.

Notices use stable original IDs and the existing durable group Inbox without
message approval. Tool execution keeps the existing readonly/MCP/extension policy.
Group ownership survives context rotation; original terminal child execution can
be inspected/accepted without constructing an offline Agent or replaying work.
Unfinished stored Goals do not suppress idle resets; actual running children still
require cancel/wait through the generic execution owner.

Goal state, worker cancellation, observation cancellation and notification
cancellation are separate explicit operations. No adapter restores/cancels Eyes
because a Goal changes status. Old Goal JSON is refused byte-preserving when the
installed capability is attached; ordinary chat without the package does not read
it. No migration or compatibility backend is provided. Goal UI uses the same
Python extension through generic shared TUI views, not a native Goal widget. The broader refactor's
[design](../../docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md) and
[acceptance checklist](../../docs/claw-assistant-core-requirements.json) track the
remaining cleanup, current-artifact audit and whole-branch verification gates.

## Durable intake and explicit input control

Ordinary messages share the group Inbox and wait behind its single execution
owner; busy messages never implicitly steer a running Agent. Claw's internal
`GroupConfig` retains only real daily/idle reset settings and
`max_queued_messages` unfinished-input capacity (pending/claimed/running,
default50). Retired queue mode,
debounce, per-group concurrency and trigger-pattern arguments are rejected, not
ignored. The live global `max_concurrent_agents` background admission semaphore
is unchanged. Explicit identified steer/follow-up and their checkpoints/queue
consumption modes belong to `xdog.agent`, not a second Claw queue. Eyes detector
debounce is a separate source feature and remains supported.

## Messaging replies

Messaging channels such as WeChat send each completed assistant text message as
it becomes available, including progress messages between tool calls. They do
not wait for the whole agent turn or resend an aggregated response at the end.
Reasoning, tool arguments/results, and empty messages are not sent as replies.
Long messages still use the normal channel chunking. WeChat's typing indicator
stays active until the turn finishes.

This is message-level delivery, not token streaming. WeChat polls for new
messages independently while the agent works. A FIFO inbox holds up to 50
pending messages, and a single worker dispatches them in arrival order through
the orchestrator. A later arrival does not change the active message's reply
recipient or typing indicator. When the inbox fills, polling pauses until space
is available rather than dropping messages.

The inbox is in-memory, not a durable job queue: stopping/restarting the gateway
cancels the active handler and discards pending messages. Wait for replies
before restarting; messages left pending may need to be resent.

## Image generation with xdog-ai

The `generate_image` agent tool and `xdog-claw generate-image` command generate
local image assets through `xdog.ai.Runtime.image_generation()`. Authentication,
model capabilities, and wire protocols belong to `xdog.ai`; claw only confines
paths to its workspace and saves the returned images. The image model is
independent of the agent's main chat model. Neither model has a hardcoded default;
choose both explicitly during onboarding.

Configure the tool and its model:

```bash
uv run xdog-ai login antigravity
uv run xdog-claw onboard
```

After selecting the primary model, **Tool Setup** lists the registered tools.
Enter their names or numbers separated by commas, `all`, or `none`.
If you enable `generate_image`, the same step requires an image model chosen
from the synced catalogue. Only models whose image-output support is explicitly
true (`image_generation`) are offered; image-reading support alone is not enough.
If none are available, leave the tool disabled and authenticate/sync a supporting
provider before rerunning onboarding.

Onboarding saves `enabled_tools` and `image_model` in `config.yaml`. For example,
after explicitly selecting these values:

```yaml
model: copilot/gpt-6-astra
enabled_tools:
  - filesystem
  - bash
  - generate_image
image_model: antigravity/gemini-3.1-flash-image
```

`enabled_tools: []` disables every agent tool. Old configurations without the
field retain the previous ordinary tool set, but image generation is opt-in.
The wizard preserves existing channel configuration and unrelated groups.
Both the agent tool and `xdog-claw generate-image` use the configured image
model. The LLM cannot override it in tool arguments. Image support is checked
again before generation; no arbitrary chat model is used as a fallback.

No proxy server, proxy key, or `CLIPROXY_*` environment variables are used.
The gateway and login command must run as the same OS user to use the same
XDOG credential store. Restart an existing gateway to load this tool change.
Reference images and prompts are sent to the selected provider.

Generate a UI mockup from a shell or another coding agent's shell tool:

```bash
uv run xdog-claw generate-image \
  "Design a Chinese WeChat coffee shop home screen with warm neutral colors" \
  --workspace . --output-dir design --aspect-ratio 9:16 --image-size 1K
```

Rerun onboarding to select a different image model. Use `--config PATH` to
choose another configured profile. For an explicit one-off provider/model
selection independent of claw's tool settings, the standalone command is
`xdog-ai image antigravity gemini-3.1-flash-image "your prompt" --output-dir design`.

Edit or use an existing image as a reference:

```bash
uv run xdog-claw generate-image \
  "Keep this layout, improve spacing, and change the accent color to green" \
  --workspace . --reference design/home.png --output-dir design/variants
```

The command prints JSON with absolute `paths` and optional accompanying `text`.
Actual files have unique names and extensions matching PNG, JPEG, or WebP data;
existing assets are not overwritten. The agent tool takes the same options,
with `reference_images` as an array. It resolves paths against the group's
workspace. All reference and output paths must stay within that workspace.

Defaults are `generated-images`, portrait `9:16`, and `1K`. Supported sizes are
`1K`, `2K`, and `4K`, subject to the model's capabilities. Up to four local
reference images are accepted, each at most 10 MiB. Requests have a 180-second
deadline; claw saves at most four images and 32 MiB of output per call.
Failed requests are not retried
automatically because image generation may consume quota.

This saves assets locally; it does **not** upload them as WeChat picture
messages. A missing provider login, unsupported image model, or upstream
refusal is reported as a tool error rather than falling back to text chat.

## Part of xdog

This package is one piece of [xdog](https://github.com/litterzhang/xdog), a
local-first toolkit for building, running and scheduling LLM workflows. The
centrepiece is [`xdog-flow`](https://pypi.org/project/xdog-flow/) — a typed
workflow format and compiler.

Documentation: **https://xdog.942295.xyz**

## Licence

Copyright (c) 2026 HugeMan <942295.xyz>

GNU Affero General Public License v3.0 or later — see
[LICENSE](https://github.com/litterzhang/xdog/blob/main/LICENSE).

### TUI file references

With an updated gateway, the TUI completes `@name` relative to the **group's
workspace**, not its launch directory. `@src/`, `./`, `../`, `~/`, and absolute
paths browse directories. Quote spaces (`@"my file.py"`); Up/Down selects and
Tab/Enter inserts without sending the draft. Escape dismisses, Tab reopens.
References are text: completion does not read contents or grant tool permission.

Scanning and matching proceed in resumable bounded batches with a private
SQLite name index, without a total entry cutoff. Progress/fallback status stays
within the editor row budget. Git supplies nested ignore/negation rules and
repository/global excludes; tracked names remain eligible despite ignores.
Non-repositories use private temporary Git metadata, never a new workspace `.git`.
Untracked generated/hidden names and symlink directory traversal are omitted.
Explicit paths bypass ignore rules, not tool permissions. Missing/failing Git
is reported as basic-exclusion fallback, not silently claimed as ignore support.

Results refresh after three seconds, removing deleted/newly ignored names. Git
hooks/fsmonitor commands are disabled and repository metadata is not modified.
Older gateways without workspace metadata retain slash completion but do not
guess a filesystem root. See `docs/agent-ui-gap-plan.md` for evidence and the
remaining UI/capability goal.

### TUI keybindings and external editor

The client reads `$XDG_CONFIG_HOME/xdog/claw/keybindings.json`, or
`$CLAW_DIR/keybindings.json`, on startup. Map actions to key strings or lists;
`[]` disables an action. For example:

```json
{"send": "ctrl+s", "newline": "alt+n", "details": "ctrl+t", "external_editor": "f4"}
```

Bindings replace defaults for each action. Invalid/conflicting/duplicate mappings
are rejected together with a UI diagnostic and default fallback. Bare printable
shortcuts are rejected. **F1** or **`/keys`** lists all effective actions and keys;
hints reflect the current map. Restart to reload. Escape and Ctrl+L are reserved;
approval/dialog keys stay contextual. Modified shortcuts depend on terminal support.

**Ctrl+G** (default) or **`/editor`** edits the current composer using `$VISUAL`,
then `$EDITOR`, then `vi`. Quoted arguments are supported, without shell evaluation;
the private temporary filename is appended. Use a GUI editor's wait option when
needed. This is a local user preference, not a gateway- or agent-selected command.
When available, the group's workspace is the editor's working directory.

The client releases terminal ownership before launching the editor. Return updates
only the draft, never sends it; failure, interruption, or invalid/oversized output
preserves the old draft/cursor. The mode-0600 UTF-8 file (maximum 1 MiB) is deleted
after use; symlink/FIFO replacements are rejected. Terminal settings are restored
even if a failing editor leaves raw mode enabled. Prompt focus is required.

Gateway work and connection events continue in the background while UI polling is
paused. The usual connection fencing and uncertain-request no-replay policy still
apply after return. Interrupting the editor is not an agent abort or rollback.
Linux/private-tmux tests cover the workflow; other Linux desktop editor/IME behavior
remains unverified.

### Steering and queue controls

Enter retains the client-owned FIFO of new full turns. During a ready owned run,
**Alt+S / `/steer TEXT`** delivers at the next Agent SDK checkpoint and
**Alt+F / `/follow-up TEXT`** delivers after active tools/steering finish, within
that Agent run. Neither choice interrupts a running tool or implies rollback.
The shortcuts are configurable (`steer`, `follow_up`, `queue`).

**Ctrl+Q / `/queue`** lists stable local `qN` and runtime `iN` IDs. Commands are
`/queue edit ID TEXT`, `/queue remove ID`, and `/queue move ID up/down`. Moves stay
within the delivery lane. Runtime changes are acknowledged by the gateway and
refuse already selected/consumed entries; failed edit text remains in the draft.
Edited queued `/command` text is literal input, not an automatic gateway command.

The updated gateway advertises input-control protocol version 1. Older gateways
are not sent unknown controls; explicit input stays editable with an upgrade
notice. Controls are scoped to this connection's active run/group, not another
connection or channel's work. Acceptance is distinct from admission to agent
history, and queue operations preserve the active timer.

Cancellation waits for consumed/returned receipts before restoring runtime text.
Disconnect, missing/malformed final receipts or uncertain edits preserve text for
manual inspection, **never automatically replay it**. Inspect conversation and
prior tool effects before explicitly resending. Local and runtime queues each
allow 50 pending messages; runtime text is limited to 64 KiB. Agent SDK receipts are
bounded individual frames, not one huge text snapshot. Rejected oversized or
malformed protocol frames do not cancel an unrelated owned run.

These controls concern the primary composer/current parent run. The child-input
queue viewer is read-only; child queue mutation/recovery stays in the main-Agent/
SDK path described below.


### Gateway-owned read-only history and context rotation

**Alt+R / `/sessions`** opens the group's searchable archived-session browser;
`/tree` inspects historical checkpoints and legacy branch records, not executable
routes. `/` searches, Up/Down/PgUp/PgDn navigates, Right inspects a tree, Left returns,
**Enter or v opens read-only source**, `r` refreshes, and Escape exits search/closes
without changing the draft. There is no history-browser activation or `n` new-context
shortcut. Shortcuts are configurable (`sessions`, `tree`); tree is unbound by default.

`/name` inspects the current session name; `/name NAME` sets a durable plain-text
name and `/name -` clears it. Names take precedence over automatic summaries in
session-browser titles/search; they never replace conversation content or model
instructions. Unicode is retained (256 UTF-8 bytes maximum); line/terminal controls
and bidi overrides are rejected. Name changes require an idle current owner, make no
model/tool calls, and do not activate a session or rewrite its history.

Claw requires independently advertised `session_rename: 1` support and an actual
active session (`/new` creates one). Names use the gateway group owner and journaled
catalog/index updates. Failed/unadmitted writes retain the old name; admitted disk
work settles before the group lock is released. A lost receipt is never replayed:
reconnect and inspect `/name` before retrying. Naming receipts update metadata only,
without replaying history or discarding the current draft/view/output selection.

Native `/name` **inspection** separately negotiates `session_name_read: 1` and
queries the gateway's committed metadata, including another client's rename.
It never substitutes the footer cache. Reads may run during an active model turn;
they do not reserve execution, create an Agent, load history or reset run timers.
While a metadata journal/navigation owner is settling, a bounded background read
waits without blocking the socket reader. Escape cancels that read first, not the
active turn. When context changes, refresh/reconnect synchronizes the group's
current context; it never activates an archived conversation. Failed, withdrawn,
canceled or disconnected reads do not replay or fall back to cached metadata. The reply is an owner-bound snapshot, not a continuous name watch.

`/new` and `/reset` rotate the current context, not the group's logical main Agent
or its Goals. Changes require an idle **group**, not just an idle client; active
children must settle first. New-context controls carry the expected active-context
identity and reject stale or historical target fields. `/resume ID`, `/fork`,
`/branch` and `/goto` are removed; the gateway and runtime explicitly reject their
old route-switching operations rather than merely hiding them in the UI.

A durable catalog retains archived conversations instead of losing metadata on
reset. Older orphan JSONL archives are recovered only inside an explicitly owned
group directory, never guessed across shared stores. Existing branch/checkpoint
files remain untouched and readable as historical evidence; they cannot become
the active execution route. Group workspace/model policy stays group-owned.

After context rotation, correlated metadata and bounded history chunks synchronize
the visible context/editor recall without clearing native scrollback or the draft.
The client waits for history completion before sending. Reconnect synchronizes only
the group's current context and never replays uncertain inputs or control requests;
inspect state before explicitly retrying. History inspection and context controls
make no model calls, and metadata I/O stays off-loop. Linux/tmux is tested;
other Linux desktop/IME behavior remains unverified.

### Image drafts and clipboard inputs

`/attach PATH` attaches a local PNG/JPEG/WebP file to the editable draft;
`/attachments` lists IDs/metadata, with `preview ID`, `remove ID`, and `clear`
subcommands. Quote paths containing spaces. Relative paths use the advertised
workspace when available, otherwise the explicit local client's cwd. Preview
owns keyboard/paste and Escape returns to the draft; it never submits an image.
Enter also supports image-only input.

**Ctrl+V / `/paste`** explicitly invokes local clipboard helpers off-thread:
Linux Wayland `wl-paste` or X11 `xclip`. No implicit
clipboard polling occurs. Missing helpers/errors/timeouts preserve the draft; ordinary
terminal text paste still works. Late results remain editable and are not sent;
`/attachments clear` also discards pending results.

An updated gateway advertises attachment protocol version 1 and model image
capability/limits. Unknown/text-only capability, invalid advertised limits, or an
older gateway rejects attachments visibly. Limits are **4 images per draft**,
**10 MiB per image**, **40 MiB retained** across composer/queues, with stricter
model limits honored. Header/MIME/dimension checks (100 megapixels) are not a full
image decoder/security boundary. Image bytes become typed SDK input and are
persisted in JSONL transcripts; visible history carries counts, not base64 bodies.
Keep transcripts private.

Images travel in bounded checksum-verified chunks, not an oversized single Unix
socket frame or workspace file. Upload IDs/cache belong to the connection;
terminal request rejections release recognized refs, and disconnect drops that
cache. Local/identified queue edits/reorders retain image identity; `/queue` shows
counts. Uncertain writes, rejected inputs and unconsumed cancellation receipts
restore the client's owned bytes/text for inspection without replay on reconnect.
Recovery may exceed ordinary input caps to avoid losing data; remove excess before
sending, and inspect prior effects/context before retrying. Linux/private-tmux and
real Unix-socket runtime tests use controlled helpers/providers. Actual desktop
clipboard and terminal-specific behavior outside these Linux fixtures are not claimed verified.

### Transcript search and fullscreen inspection

**Alt+H / `/transcript [QUERY]`** browses all retained client-visible source text:
user/assistant prose, reasoning, retained tool arguments/results and notices,
without an additional browser/Markdown preview cutoff. Updated gateways advertise
full-source protocol version 1: opening/refreshing the browser retrieves untruncated,
redacted source in bounded chunks instead of relying on the old display projection.
That projection remains compact for native rendering; its 16 KiB result, argument
and part-count caps do not constrain the full-source browser. Older gateways retain
an explicitly labeled limited-source fallback and are not sent unknown controls. `/` edits a literal,
Unicode-case-insensitive query; Enter completes editing and **n / Shift+N** moves
between every non-overlapping occurrence, even repeated hits on a long line.
Matched long lines pan to the selected occurrence.

Up/Down, PgUp/PgDn and Home scroll; Left/Right changes source entry;
Shift+Left/Right pans horizontally. **v** marks a whole-line range (navigation
extends it), Shift+Up/Down starts a range, and **a** selects the complete source
entry across pages. Escape clears selection/search editing before closing;
Ctrl+C closes the viewer without aborting the active run. **r** refreshes source
while retaining position where possible and clears selection; **End** refreshes
and goes to the latest source. Immutable snapshots prevent active streaming from
silently changing the selected content.

**f / `/fullscreen`** opts into a full-height alternate-screen viewport;
`/transcript fullscreen`, `refresh`, and `close` are also available. The configurable
`transcript` action defaults to Alt+H; `fullscreen` is unbound by default. Returning
restores the main-buffer painter and queries the actual cursor after resize, so
native scrollback is not replayed or cleared. Draft/cursor, attachments, queues
and run timers survive. Session replacement/clear closes the old browser, and
exit/EOF/failure cleans screen/terminal ownership. This is local inspection: it
sends no gateway/model request and never replays work on reconnect.

The source is the retained visible context (including gateway history replay), not
hidden runtime prompts, foreign sessions, or image base64 bodies; use `/sessions`
for archives. Native terminal selection remains available; application selection
is whole-line/entry based. Copy/export/share is tracked separately. Linux tmux is
verified; other Linux desktop terminals and IME remain unverified.


Full source is an immutable snapshot of the accepted current conversation, including
uncapped live tool progress and partial assistant content. Runtime capture does not
call a model, execute tools, create a session or change its head. Channel-hidden
internal inputs and binary image bodies are omitted; image counts remain visible.
Existing credential-redaction heuristics apply without text/array/part cutoffs, but
are not a guarantee that arbitrary sensitive prose is removed. Client-only notices
appear as a prelude to canonical source, rather than replacing or duplicating its
messages. Use `/sessions` to activate another archive before reading its source.

Both sender and receiver use private temporary spools, 64 KiB raw blocks, ordered
indexes, declared byte counts and a final SHA-256 check. Only a complete verified
snapshot reaches the browser; a failed/canceled transfer leaves the prior snapshot
with explicit feedback. Redaction, file I/O, decoding and indexing run outside the
terminal loop. There is one coalescing source owner per connection, with write
backpressure; stale request IDs, connection generations, sessions and same-session
head revisions cannot apply an older response to a successor context. Closing the
viewer cancels only its read, not model/tool work. Source errors/timeouts are not
replayed or automatically retried. Query and draft survive loading, and End waits
for refreshed source rather than targeting the old clipped dataset.

Temporary spools are private/local, not workspace files or a hosted control plane.
Disk/parse/checksum failures are visible. Kernel-blocked filesystem calls cannot be
forcibly interrupted; cleanup retains ownership until they settle. Disconnect
aborts owned SDK work before waiting for source cleanup. Linux Unix-socket/tmux
behavior is verified; other Linux desktop terminal validation remains separate.


### Copy, private export and explicit local sharing

**Alt+C / `/copy [response|selection|entry|conversation|all]`** copies a captured
source snapshot, defaulting to the latest assistant response. A marked browser
selection is used by Alt+C when present. In the transcript browser, **y** copies
selected lines or the current entry; **e** chooses a local export destination and
**s** reviews a local share artifact (selection if marked, otherwise conversation).
These gestures never submit a prompt or execute a tool. Clipboard helpers are
`wl-copy` on Linux Wayland or `xclip` on X11. No helper means an
explicitly reported private temporary text file fallback, not silent OSC52 output.
Helper errors/timeouts are not retried because clipboard outcome may be uncertain.
The OS clipboard can expose text to other applications.

`/export PATH [text|md|json] [scope]` creates a private captured transcript file;
without PATH it opens a destination/review panel. Default scope is all retained
source, including tool/reasoning/notices. Existing files, final-path symlinks and
directories are never replaced. Parent directories must already exist. Creation
uses a private temporary file in that directory followed by exclusive atomic
publication; failures/cancellation before commit leave no partial destination.
Binary image bodies are not embedded: visible source has attachment indicators.

`/share PATH [scope]` always reviews before saving a portable **local HTML file**.
Default scope is user/assistant conversation, excluding tool output, reasoning and
system notices. Explicit selection/entry/all scopes can include private roles and
are listed in the review. Common credential patterns are redacted, but this is
heuristic—not a confidentiality guarantee. Review the preview and destination,
scroll with pages/arrows, Tab to edit the path, and press plain Enter to save only
when the complete review/confirmation is visible. Tiny geometry, resize races,
modified Enter, paste and unrendered/loading previews cannot confirm. Escape or
Ctrl+C cancels the output dialog, not agent work. An active Coding tool approval
has higher focus priority and does not grant sharing permission.

The HTML escapes source text and contains no scripts, remote resources, forms,
links or upload behavior; a restrictive CSP is embedded. Distributing the artifact
is your separate explicit action. No hosted service/control plane is required.
All preparation, helper execution and file I/O is off the terminal thread, with one
owned operation at a time. Source is immutable even if streaming continues. A
session/context change cancels an uncommitted review; already committed clipboard/
file changes cannot be rolled back or automatically replayed. `/copy cancel`,
`/export cancel` or `/share cancel` cancels a pending output operation.

Claw requires a ready updated full-source gateway. Prompt actions fetch source
before output; failed/stale/disconnected reads never copy/export a clipped or old
fallback. Selection/entry actions use only a settled verified browser snapshot.
Draft/cursor, queue/attachment ownership, native scrollback and terminal modes are
preserved. Linux tmux and controlled real clipboard helpers are verified; desktop
clipboard ownership outside these Linux fixtures remains unverified. Private
fallback files persist until you delete them. Kernel-blocked filesystem operations
cannot be forcibly interrupted; helper success may transfer clipboard ownership
to the platform's normal persistent clipboard process.

### Group-owned MCP stdio tools (first transport slice)

`config.yaml` accepts a `mcp_servers` mapping of server ID to `command`, argv
`args`, optional `env`, `timeout`, and `allow_tools`. No server auto-starts:
`/mcp connect SERVER` explicitly trusts/launches the configured command in the
selected group's workspace. `/mcp`/`list`, `refresh SERVER` and `disconnect SERVER`
show/manage protocol/tool state through correlated gateway controls. Connection/
schema mutations require an idle **group**, not merely an idle client. The gateway
retains its group reservation while management settles. Older gateways receive no
unknown operation; uncertain controls are not replayed on reconnect—inspect status.

Connections are group-owned across cached conversation sessions and close at
runtime shutdown/disconnect command. Tools enter that group's one `xdog.agent` runtime.
External execution defaults to **blocked** even after discovery; explicitly set
`allow_tools: true` for a trusted server to authorize unattended tool use. This can
permit filesystem/network effects outside built-in tools; permission gates are not
an OS sandbox. Server read-only annotations do not override host policy. Command
trust and tool-use authorization are separate, and configured env values are not
printed in status. Explicit public-PKCE OAuth is documented below; removed optional
client input/task capabilities are not a future dispatch surface. Stdio negotiation,
validation and cancellation limits are documented in the Agent README.

Group MCP configuration also accepts `transport: http`, an explicit HTTPS `url`,
and optional `headers`/`headers_env` credential mappings. `/mcp connect SERVER`
uses the same group owner and idle reservation as stdio, with unchanged explicit
`allow_tools` policy. Current stateless and legacy Streamable HTTP JSON/SSE, header
mirrors/session lifecycle and bounded no-replay behavior are shared with Coding.
Authorization failures and redirects never trigger automatic credentials, downgrade
or forwarding. See the Agent README for the public-PKCE profile, configured Roots,
retained request continuations/SSE and explicit no-POST-replay limits.


### MCP browser and inert context previews

`/mcp` (or `/mcp browse`) opens a focused server browser. Nothing connects
implicitly. Select a server and use **c/d/r** to connect/disconnect/refresh;
**u/t/p** lists resources/templates/prompts. **/** filters, arrows navigate,
and **Enter** retrieves a preview, never model input. Template selection opens
an editable URI: resolve expressions explicitly before reading. Prompt selection
opens a JSON object of named-string arguments; Enter retrieves the prompt only.

In preview, **i** inserts text into the existing editable draft with an untrusted
MCP label; a separate normal submit is required to send it. Binary insertion is
refused, not silently converted/truncated. **e** starts the existing reviewed JSON
export, retaining base64/MIME data. Export remains local, requires confirmation,
and uses the selected workspace rather than guessing the TUI launch directory.
**Escape** returns to servers/closes; **Ctrl+C** closes the browser. These gestures
never grant external tool approval or invoke `tools/call` by themselves.

Claw reads use the group manager and a connection-owned coalescing worker. Slow
reads do not block ping/cancel controls. Preview/catalog IPC is checksummed and
chunked, with group/session/revision fencing and owned temporary-file cleanup.
Browser close cancels the active read; late responses cannot update a successor.

The retained MCP surface includes explicit public-PKCE authorization, selected
credential storage, inert previews and manual catalog refresh below. Native
manual completion and optional Sampling/Elicitation/Tasks/Roots-management controls
are removed, not pending browser features. Binary/image resources are exportable,
not directly attached to model input. Broader third-party interoperability is not
inferred from these local controls.


Legacy HTTP MCP tools now support bounded cursor-based response recovery: an
interrupted SSE response can be retrieved with GET/`Last-Event-ID`, retaining the
original request identity and timeout. No second tool POST, automatic approval or
connection replay occurs. Concurrent calls keep separate cursors; cancellation,
auth/redirect/session errors and reset/exhausted cursors fail without replay.
Current stateless HTTP never sends recovery GETs. See the Agent README for limits.


### Configured MCP Roots and retained transport continuations

Roots are **configured disclosure metadata only**. A server config may include
`"roots": [{"uri":"file:///absolute/workspace","name":"Workspace"}]`, at most
16 validated local absolute file URIs. Nothing enumerates the filesystem, infers
Roots from cwd, grants tool access or creates an OS sandbox. Explicit connect
admits that configured disclosure to the selected peer; disconnect withdraws it.
There is no mutable Roots picker, enable/replace review or Roots-change UI.

Legacy `roots/list` answers stay within the configured connection ceiling. Modern
2026-07-28 `input_required` responses remain supported for owned `tools/call`,
`prompts/get` and `resources/read`: only configured Roots and bounded opaque
`requestState` continuations are accepted. Original request arguments and identity
remain fenced; continuation rounds, embedded inputs, bytes and deadlines are
bounded. An unsupported sibling input rejects the round before any partial Roots
disclosure. Disconnect/cancel settles the original owner, not a successor.

**Sampling, Elicitation and MCP Tasks are removed** from Agent MCP SDK/native/gateway
registration, capability advertisement and review/control surfaces. Legacy
requests are refused; modern embedded unsupported families/task augmentation fail
closed, with no callback, model/tool work or partial completion. The former
Sampling/Elicitation/Tasks/Roots-management keys are not replacement controls.
Basic extension input/select/confirm and provider OAuth callbacks are separate
retained interfaces, not MCP Elicitation.

Ordinary modern Roots/request-state continuations do not authorize retrying an
uncertain operation. Active resource/catalog/combined subscriptions are removed.
Ordinary response-scoped SSE and request telemetry remain. Legacy cursor-based
**GET** recovery retains the
original identity/deadline and never repeats a tool **POST**; current stateless
HTTP does not acquire legacy GET resumption. No disconnect, failed response or
continuation silently reconnects, replays POSTs or expands configured disclosure.


### Task planning and host read-only scope

The builtin **`xdog-planning`** skill uses ordinary conversation or task documents
for objectives, dependencies, acceptance criteria and evidence-backed progress.
It does not create a separate plan database, inspector, execution mode or model
loop. The former `/plan`, `PlanPanel` and `update_plan` UI/tool APIs are removed.
List it with `/skills`; load it explicitly with `/skills load xdog-planning`.

Use the native host control separately:

```text
/readonly on
/readonly status
/readonly off
```

With host read-only scope on, only local `filesystem` read/ls/grep/find and
`current_time` tool calls are eligible; shell, workspace writes, MCP/extension
tools and child-control tools are blocked. Normal permission policy still applies
to eligible calls. Cached approvals, `allow-all`, skill text, model arguments and
delayed approvals cannot release or bypass this restriction. Keep planning notes
in the conversation while read-only; writing a task document still needs the
normal write permission outside that scope.

Only an explicit user `/readonly off` releases the host restriction. It does
**not** change permission mode, `allow_tools`, extension trust or child access,
and it grants no tool approvals or automatic execution. Access changes require
the original idle owner with child/storage work settled, persist before publication,
and do not submit a model turn. Claw additionally fences controls to its ready
original group/session/connection; uncertain requests are not replayed.

Access state for the current context survives gateway restart. Historical saved
planning/access state is read-only compatibility data, not authority to switch an
execution route or release a retained restriction. Malformed state fails closed;
new/reset controls do not grant tool approval. These are host policy gates, not an
OS sandbox or a restriction on arbitrary trusted Python code.

### Trusted Python extensions (runtime API v1)

Extensions now run in owned Python subprocesses, using the existing bounded stdio
transport/lifecycle owner rather than a second agent loop. They are **trusted local
code with your full user privileges**. Process ownership contains accidental output,
timeouts and cleanup; it is NOT an OS sandbox or filesystem/network confinement.
Imports, hooks and user-invoked commands can themselves have effects. Tool-use
permission is separate from trusting extension code.

Enable only selected IDs in user/global configuration, not project settings:

```json
{"extensions": ["sample"], "extension_dir": "/home/user/.local/share/xdog/extensions",
 "extension_settings": {"sample": {}}}
```

Each selected directory contains `extension.yaml` (`name: sample`,
`entry_point: main`) and `main.py`. No recursive project scanning, installation,
package-manager call or workspace-authorized Python import occurs. Relative roots
resolve against the global configuration directory, not the project cwd. Global
names activate lazily at the first agent turn, `/extensions`, or extension command.

```python
from xdog.agent.extensions import ExtensionAPI

def setup(api: ExtensionAPI):
    async def echo(params, context):
        return "echo: " + params.get("text", "")
    async def hello(args, context):
        api.notify("Hello from the explicitly trusted extension")
        return "hello: " + args
    api.tool("echo", "Echo text", {"type": "object", "properties": {
        "text": {"type": "string"}}}, echo)
    api.command("hello", "Say hello", hello)
    api.status("ready", "Extension ready")
    api.status("summary", "Bounded status text")
```

Tools get collision-safe `ext_...` aliases; slash commands are namespaced as
`/ext-sample-hello ARGS`. The live completion provider reflects current commands.
`/extensions [list|reload NAME|unload NAME]` manages ONLY globally trusted IDs and
requires idle ownership. Commands do not silently submit an agent prompt. API v1
supports sync/async callbacks, session/turn/message/tool lifecycle events, pre-tool
veto/parameter transforms, post-tool text transforms, bounded notifications and
simple text status. Legacy Python pre/post/stop hook types remain compatible.

Rewritten tool arguments are schema-validated and checked by the host's ORIGINAL
permission and read-only gates AFTER transformation. A plugin cannot use the API
to grant tool approval/change host read-only scope, or mask a denied/failed call as success.
UI text is sanitized and row-bounded; direct plugin prints never paint the host
terminal. Timed-out/failed/canceled callbacks close their owned process, with no
replay. An unavailable selected extension makes pre-tool validation fail closed
until explicit unload/reload. Shutdown runs bounded stop hooks and reaps normal
owned process groups; cancellation cannot roll back already completed effects.

The supported extension surface is intentionally bounded, not full Pi API
compatibility. Durable entries/context hooks, typed tool/command results, registered
shortcuts and session-owned providers are documented below. Arbitrary custom
rendering/forms/layouts, raw terminal interception/observation and host editor/session
mutation APIs are removed. Code can deliberately escape normal process ownership;
do not load untrusted code.

Claw additionally requires `extension_allow_tools: [sample]` in its trusted config
to let the agent call a plugin tool. Default tool use remains blocked after loading.
Configuration `extensions`, `extension_dir` and `extension_settings` use the same
names/shape in `config.yaml`. Cached sessions own their processes; group/session
controls, run-correlated UI events and gateway-loop delivery fence stale output.
Existing queued/scheduled work follows normal group ownership and host policy.


### Durable extension entries and context hooks

Extensions can now call `api.append_entry("note", {"text": "durable data"})` from
an active callback. Host-owned JSON entries are bounded, independently namespaced
per extension and committed before the callback receipt is returned. Subsequent
callback context contains that extension's `context["entries"]`; unload/reload
retains it. Restart restores the current context's durable snapshot, not process
globals. New/reset rotates the context; archived session/branch snapshots cannot
be activated. Retired owners cannot publish writes into a successor context.
Close invalidates late writes, so shutdown callbacks should not append entries.

Register `api.context(callback)` to transform the ephemeral message list before
EACH model invocation. The callback receives serialized typed user/assistant/tool
content plus its entry snapshot and returns a message list. It may inject summaries
or filter complete history batches, but does not edit the canonical transcript,
system prompt, static cache prefix, host read-only scope or tool permissions. Coding also
composes an existing SDK transform before its extension pipeline.

```python
def setup(api):
    async def remember(args, context):
        api.append_entry("note", {"text": args})
        return "Saved entry"
    async def model_context(messages, context):
        notes = [row["data"]["text"] for row in context["entries"] if row["kind"] == "note"]
        if notes:
            messages.append({"role": "user", "content": [
                {"type": "text", "text": "Context note: " + notes[-1]}]})
        return messages
    api.command("remember", "Persist a note", remember)
    api.context(model_context)
```

Unknown/system roles, unsupported parts, invalid images/signatures and incomplete/
invented tool-result batches fail explicitly, not by silently dropping content.
No-op transforms preserve original typed messages. Images and signed thinking/tool
parts survive chunked full-context transfers beyond the individual extension-worker
RPC frame limit.
Transfers are checksummed, order/identity fenced and spooled off-loop, with a 128 MiB
explicit context limit, 64 KiB blocks and an absolute whole-transfer deadline.

Entry capacity is 128 entries across a session, 16 KiB per entry and 512 KiB total.
Invalid/corrupt stored state fails instead of quietly resetting it. Persistence
failure keeps the previous snapshot. Cross-loop cancellation settles committed
metadata/file ownership before the host releases its lifecycle reservation; it
cannot roll back an already completed write. Context-hook failures disable the
extension and do not trigger another model call or automatic replay.

Durable entries and context hooks do not grant editor/session mutation, raw terminal
interception or custom-layout authority. Registered shortcuts, basic dialogs, rich
results and session-owned providers have their separate contracts below. Extension
code is still trusted code with the user's privileges, not an OS sandbox.

Claw loads the current context's independent atomic extension-entry sidecars at
startup; transcript-catalog recovery cannot discard them. Invalid sidecars are
explicit errors, not empty state. Legacy session/branch sidecars remain historical
data: they do not offer branch activation or worker restoration into an alternate
route. Context rotation retires old workers; startup rebuilds workers only for the
current group context and its trusted extension configuration.


### Interactive extension dialogs

Active extension callbacks now support `await api.select(title, choices)`,
`await api.input(title, message="", default="")` and
`await api.confirm(title, message)`. Optional `timeout` is bounded to 0.1–600 s.
Select/input return `None` on cancel/no UI; confirm returns `False`. Cancel answers
this dialog only—it is not tool approval, permission revocation or automatic turn
submission. Trusted extension code decides what to do with the answer.

The worker suspends the callback and returns an owned pending-callback/dialog
receipt. The host displays a focused basic dialog, then resumes that SAME callback
with the validated answer; it does not rerun a command/tool to obtain the answer.
Multiple sequential dialogs work. Without an interactive host, canceled values are
returned immediately rather than hanging unattended execution.

Basic dialogs sanitize control codes, scroll long details, preserve the main editor
draft and input cursor, and require a rendered visible question/action before Enter can
answer. Select/confirm pastes do not submit; tiny/resize/release/repeat/unpainted
selection input cannot confirm. Escape/Ctrl+C cancel the dialog. Tool approval and
output review retain higher focus priority. An affirmative extension confirmation
never grants the underlying agent tool permission or switches host read-only scope.

Dialog IDs are host-generated and scoped to their session/request/run/connection.
Timeout, abort, context rotation, disconnect and shutdown settle/cancel pending futures;
late/foreign answers cannot resume a successor's callback. Claw command waits are
connection-owned background jobs so the socket can still process UI replies and
other controls while the group remains reserved. Context-hook dialogs still share
the context transfer's absolute deadline; the UI does not renew model/transport
budgets. Callback effects remain trusted code, not OS-enforced isolation.

These are basic input/select/confirm dialogs, not plugin forms or a layout API.
Custom renderers, nested dialogs, extension overlays and terminal interception are
removed. Registered command shortcuts and submitted-input hooks remain separate;
none of these interfaces grants tool permission or full Pi API compatibility.


### Supervised child agents

The TUI supports `/agents start TASK` (read-only), `/agents start --inherit TASK`,
`/agents list` and `inspect/steer/follow-up/cancel/wait/close ID` controls (steer and
follow-up take trailing text). Parents expose `agent_spawn`/`agent_control` tools;
children cannot recursively delegate through `agent_spawn` or `agent_control`.

Children share their group's workspace, model/options and selected tools. Inherited
access cannot exceed current parent read-only restrictions or configured MCP/extension
allow-tools gates. Independent child extension workers/entries/tool executors do not
borrow the parent's stateful tool proxies; parent validation/context hooks still
retain authority. Read-only children permit only known read-only filesystem calls
and current time. User starts require idle group ownership, while inspect/cancel/
wait controls remain reader-responsive even if the parent waits for a child.

The main Agent manages child concurrency, progress, waiting, cancellation and
closure. There is **no fixed child execution timeout, model-call count, cumulative
token quota, active-child concurrency cap or retained-record count cap**. A parent
turn ending does not close still-working children. Long tool loops use native Agent
compaction with durable original-history checkpoints when the adapter supplies the
required model metadata; they are not stopped by a cumulative-history budget.

Permissions, actual model context/output/vision limits, bounded protocol frames,
single-message/persistence-chunk guards, input/image collection checks and labeled
display retention limits remain. They protect individual operations/resources,
not a hidden task-lifetime quota. Tool/extension RPC deadlines, explicit user
cancellation and host shutdown still settle their actual owners; no permission or
OS sandbox is implied. Socket jobs and replay receipts remain bounded. Expected
context checks fence stale controls; active children prevent context/schema changes
and automatic daily/idle resets. Daemon shutdown settles child owners before shared
extension/MCP resources close.

Current-context child records persist final results, isolated extension entries and
chunked complete transcript history; legacy branch records remain historical data,
not another execution route. Interrupted work is never restarted automatically.
Closing archives a record without deleting its evidence; it is not needed to free a
fixed record quota. Corrupt catalogs disable delegation with an explicit repair
diagnostic, not the parent UI. Group/channel/memory/skill services execute on
their original host loop; shared parent validation stays serial and cannot borrow
a parent dialog lease.
`/agents` opens a live roster and focused complete-source browser/search/selection,
with visible child-specific control forms, opt-in fullscreen and scoped reviewed
copy/export/share. Child source has its own checksummed/coalescing 64 KiB transport
and a 2 MiB display-source limit; controls remain socket-responsive during reads.
Escape restores the roster and parent draft/caret. Child extension dialogs use
independently owned select/input/confirm continuations when the origin UI is attached;
complex extension forms/renderers are removed.
This is host policy/resource ownership, not OS-enforced sandboxing or rollback of
remote/trusted callback effects.


Running child tools expose their latest reported progress through the same child
source/search view. It is explicitly live/pending, not a final result. Transient
progress has a separate 512 KiB escaped-JSON / 128-source display-retention budget;
clipping/omission is labeled and does not abort valid execution. Completed canonical
tool results replace progress; transient snapshots are not inserted into model history
or persisted as fabricated results. Source IDs preserve concurrent/reused-call ownership.


Child extension select/input/confirm dialogs now display the child identity and
resume the original suspended callback with actual user answers. An extension yes
is NOT tool approval or a host read-only transition. Input/select cancel returns None;
confirm cancel returns False. Detaching/exiting the origin UI settles pending
questions and subsequent child questions use canceled values; callbacks/work are
not replayed or automatically adopted after reconnect. Parent inherited validation
still uses silent no-UI answers to avoid borrowing its separate dialog reservation.
Complex extension forms are removed; basic child dialogs retain these ownership and cancellation checks.


### Child specialization and resource boundaries

`/agents start --label Reviewer --instructions "Inspect only" --tools filesystem,current_time
--fresh -- TASK` specializes a child without
changing parent permissions. Options precede the task; `--` preserves a task that
starts with flags. Plain tasks preserve quotes/newlines. `--tools none` gives the
child no executable tools; otherwise selected names must exist in its inherited tool
set and cannot include recursive delegation tools. Unknown/malformed flags
fail visibly. Instructions append to, rather than replace, parent restrictions.

In `/agents` press `n`, then Ctrl+G for settings JSON. Supported keys are `label`,
`instructions`, `tools` (array or null for inherited tools), `inherit_context`,
`model`, `thinking` and `preset`. Enter applies the settings draft only;
start review requires every page to be painted before confirmation. Resize invalidates
that review. This confirmation does NOT approve tool use. Effective settings persist
with child results and appear in complete child source. The removed `--timeout`,
`--calls`, `--tokens` flags and `timeout`/`max_model_calls`/`max_tokens` child config
keys are rejected on new starts; historical saved values are inert compatibility
data, not restored limits. Actual model output/context options remain separate from
a removed cumulative child token quota; usage is not exact billable accounting.
`--fresh` omits canonical history/child entry inheritance, not mandatory parent
validation/context or the shared workspace. Model/reasoning inherit by default; explicit overrides now use the actual adapter.
Named presets now use explicit user configuration. Claw specialized
starts require its advertised `child_config` capability, avoiding silent old-gateway
settings loss.


### Child model and reasoning overrides

`/agents start --model provider/model --thinking low --fresh TASK` selects child
routing/reasoning independently. Settings JSON accepts `model` (null inherits) and
`thinking` (null inherits, `off` disables, or advertised minimal/low/medium/high/xhigh).
Choices resolve on the SAME request adapter's cached active catalog; no automatic
provider activation/login/catalog sync/fallback occurs. Plain custom adapters without
metadata reject overrides. Claw requires `child_config: 2` for these fields.

Requested and effective routing/reasoning/limits persist separately in child source.
Selected capabilities/window/output limits apply, while parent/child read-only,
approval/external-tool gates remain unchanged. Same-model aliases preserve explicit
host native-tool overrides. Switching to a nonreasoning model disables inherited
reasoning; unsupported positive choices fail visibly. Model switches omit inherited/
transformed provider continuity and old reasoning from the CHILD copy, leaving parent
provenance/history intact. Transformed images are validated; oversized switched-model
contexts are checked against the actual model window/output allowance. Native
compaction can maintain later tool-loop context; it is not exact token accounting
or permission to silently drop invalid images or switch providers. Parent model/options/
settings/grants never change. Named presets now use explicit user configuration.


### Named child presets

Define `child_presets` in Coding's user/global settings JSON or Claw's operator YAML
using this descriptor shape:

```json
{
  "child_presets": {
    "reviewer": {
      "description": "Inspect and report without workspace mutations",
      "config": {
        "instructions": "Review requested code; return findings with file locations.",
        "tools": ["filesystem", "current_time"],
        "inherit_context": false
      }
    }
  }
}
```

Browse `/agents presets`, or use `/agents start --preset reviewer -- TASK`. Explicit flags override template fields. Ctrl+P selects a template in a new
child form, Ctrl+G adjusts the draft, and full expanded settings/task review still
precedes execution. Selection itself makes no model request. Templates cannot encode
access/permission changes, code/imports, recursion or provider activation. Starts
remain read-only unless inherited access is explicitly selected separately; all host
gates remain active.

Frozen catalogs are bounded to 32 entries / 256 KiB raw and expanded data. Expanded
settings/provenance persist; catalog edits never replay historical work. Coding
project/session data cannot activate presets, and invalid global preset data retains
parent permission settings. Claw invalid operator templates fail without fallback;
preset controls require `child_config: 3`. Old schemas receive supported inherited
fields only; chosen nondefault routing/template settings are never silently dropped.


### Interactive MCP authorization

`/mcp authorize SERVER` opens endpoint/resource/client/scope/TLS-trust consent; in
`/mcp` use `a` on the selected server. Page through EVERY field before Enter confirms.
Resize/tiny/unpainted/repeat/paste input cannot authorize. The resulting reviewed URI
is for your browser, not code/token paste into chat. `y` explicitly copies after full
URI review; no browser or model opens automatically. `c` cancels a pending callback,
`s` reads state, and Escape leaves the view and cancels pending ownership.

Authorization alone does not connect/register tools or approve execution. Return to
servers and `c` to connect explicitly. Server-list `s` inspects authorization,
`l` reviews saved restore and `x` reviews saved-credential erase. Inside authorization
status (not the server list), `f` reviews memory/saved forget and disconnect, never
remote revocation. Existing tool approvals,
Claw allow_tools/read-only and child/model rules remain active. Parent drafts/caret and
terminal modes survive. Tokens remain private in memory until a separate explicit
save to the selected Linux credential backend. Refresh/restore are explicit, with
no automatic retry or replay.

Consent is scoped to view/session/context and, in Claw, its original socket/group.
Another connection cannot use a known receipt. Leaving, canceling, disconnecting or
changing the scope rejects late credential commitment and settles pending listeners;
reconnect does not adopt a waiting flow. Already completed resource credentials may
survive closing the success view. Claw requires advertised `mcp_auth` controls; schema
changes/forget still require idle ownership. Pending HTTP jobs do not block socket
reads. Only pre-registered public-PKCE profiles are supported; client-metadata/DCR,
confidential methods and private-key JWT are removed. Explicit refresh and both
Linux storage choices below remain supported without method/store fallback.


### Authorization metadata discovery

Resource denial challenges now retain their `resource_metadata` pointer and
operation-specific scopes for an explicit subsequent authorization, without replaying
failed MCP POSTs/tools. Quoted multi-scheme headers are parsed with bounds; malformed
or ambiguous challenges are not silently discarded. Without a pointer, path-specific
then root protected-resource metadata are tried. Issuers use ordered OAuth and OIDC
well-known candidates; only absent GET routes (404/405), never bad identity/200 data,
redirects or token/code errors, permit fallback.

Pointer metadata uses no existing MCP credentials/cookies. Its origin must be the
configured resource/issuer or an explicitly approved HTTPS origin in the optional
OAuth `metadata_origins` array (at most eight origin-only URLs). This approval does
not change the pinned issuer or authorization/token endpoint trust. Resource/issuer
identities still match exactly. Challenge scopes are operation-specific and take
priority without assuming they are listed in `scopes_supported`; the configured
scopes remain an operator ceiling. Scope-less listings are displayed as no request,
and over-ceiling requirements fail rather than silently expanding access. Actual
review data carries discovery URLs and scope-source provenance. Consent/permissions/
leases/explicit connect remain separate. Client-metadata and dynamic registration
are removed; explicit public refresh and both retained Linux stores do not imply
universal external-provider interoperability.


### Pre-registered public-client boundary

MCP OAuth accepts only an existing **pre-registered public client ID**, S256 PKCE
and token authentication **`none`**. `registration: "pre-registered"` and
`token_auth_method: "none"` are the defaults and the only supported values.
Metadata-client-ID/hosted-client-document registration, dynamic registration,
confidential client-secret methods and private-key JWT/signing are removed.
Unsupported profiles fail closed before authentication/network work; no secret
source is read, signer started, registration POST sent or method/store fallback
attempted. There is no remote client-registration management API.

Pinned **resource/issuer metadata discovery** remains part of explicit public-PKCE
authorization; it is not client metadata registration. Resource, issuer, scopes,
TLS trust, loopback callback and actual endpoint review still apply. Authorization
never connects tools or grants their permission. Explicit reviewed refresh and
credential save/restore below remain independent operations, not automatic login
or recovery of an unsupported profile. Old records bound to removed authentication/
registration methods are not silently migrated or downgraded to public grants.


### Explicit in-memory OAuth refresh

Refresh is an operator action, never a background token retry. If the issuer
supplied a refresh token, open authorization status and press **r**, review the
resource, actual grant scopes, pinned endpoints, client ID and public method
`none`, then confirm with a fresh physical Enter. This sends one resource-bound
refresh POST, not a new browser login or client registration. Claw requires
`mcp_refresh:1`; unsupported gateways cannot submit it.

On an `xdog.agent.mcp.McpManager` instance, Agent MCP SDK hosts use
`manage_auth("remote", "refresh_prepare")` followed by
`manage_auth("remote", "refresh", review_id)` under the same live consent lease.
Receipts are single-use and expire after five minutes. Refresh uses the previous
grant's scopes, not the broader configured ceiling. Valid replies may narrow,
never widen, scopes. A new refresh token replaces the prior one only after reply
validation; omission retains the old token only after a valid response.

The refresh capability is consumed before exchange. Cancellation, lost/invalid
responses or revoked ownership discard it rather than replay an uncertain token.
**c** cancels a pending refresh; leaving/disconnecting settles owned work and
fences late commits. A still-valid old access token may remain usable after
refresh failure; authorize again if needed. An existing saved snapshot is removed
before rotating refresh, so restart cannot replay stale credentials. A completed
replacement grant is saved only by another explicit reviewed save.

Refresh never replays MCP requests, connects tools, changes approvals or falls
back to another client/authentication/storage method. The two retained Linux
credential backends are documented separately; neither implies startup restore,
automatic refresh, remote revocation or universal issuer interoperability.


### MCP JSON Schema and typed structured output

Current-protocol MCP results now retain **any finite JSON value** in structuredContent:
objects, arrays, strings, numbers, booleans and explicit null. Missing content is
tracked separately from null by `structured_content_present`; advertised output
schemas must be satisfied. Legacy negotiated profiles still require object results.
A successful text summary no longer hides canonical structured data: when no exact
canonical JSON text block is already present, a labelled structured-output block is
added to the tool content used by the model, full-source viewer and persistence.
Compact rows may still collapse it; **Ctrl+O** or the transcript viewer inspects it.

JSON Schema defaults to 2020-12; recognized declared draft-04/06/07/2019-09/2020-12
semantics are used rather than silently substituting another dialect. Local pointers,
anchors/dynamic references and bundled resource IDs resolve only inside the advertised
document. External documents are never fetched, including through an ID/base change.
Unknown dialects, unresolved references and unsupported required vocabularies fail
closed. Unreferenced examples/defaults remain data; explicitly referenced targets
are checked as schemas. Normal nullable unions, boolean property schemas and
conditionals are supported. MCP arguments remain objects; model parameters get an
object-root intersection while validation keeps all raw advertised constraints.

MCP uses an optional sync/async AgentTool argument validator before permission hooks
and validates effective arguments again immediately before the remote call. Coding
and Claw extension pre-hooks use the same check when they rewrite arguments. Existing
permission/read-only/allow_tools rules still decide execution; a schema/readOnly hint
never approves a call. Coding's parameter prompt formatter now handles boolean/union
property declarations rather than crashing on a valid schema.

The validation helper is local, owned and cancellable, started only for explicitly
connected tool schemas. It runs isolated Python imports (`-I`) with only the trusted installed/vendored
library root explicitly added, without a second
agent/model loop or schema URL retrieval. A **three-second** deadline includes queue
wait/validation; timeout/cancel kills and reaps that exact helper, including a child
still being created. A later explicit validation may start a fresh local helper;
remote MCP calls are never retried by it. Serialized schema caching is capped at
32 entries/4 MiB. Schema/value bytes, depth and node counts are bounded; these are
application limits, **not** OS-enforced filesystem/network/memory sandboxing.

Bounded JSON tool details survive save/reload and read-only historical source in Claw, preserving
typed structured output and null-presence flags. Live/non-JSON handles stay transient.
Transcripts may contain sensitive structured values: keep them private. Actual current/
legacy peers, both app permission paths and Linux/private-tmux inspectors are verified;
provider-specific full-schema acceptance and mixed-dialect/native-platform
interoperability are not inferred. Format-assertion/custom required vocabularies,
ambiguous HTTP header mappings through schema composition, richer content/client
capabilities and external credential/provider interoperability gaps remain
open. Earlier object-only/current structured-result limits are superseded by this pass.

Schema packaging includes a direct `referencing` dependency and the portable SDK
bundle dependency list. The helper resolves its own trusted library root, so vendored
bundles work without accepting project/PYTHONPATH shadow modules. An actual vendored
helper test covers this; generated-output/vendored-package licence boundaries are
unchanged. Lock updates only declare the already-resolved dependency, not unrelated
version upgrades.

### Rich extension tool results

Trusted extension tools are no longer text-only. Existing string callbacks are
unchanged. A callback may return a typed `AgentToolResult`, a content/structuredContent/
details object, or use the validated builder:

```python
return api.result([
    {"type": "text", "text": "Result summary"},
    {"type": "image", "data": png_base64, "mimeType": "image/png"},
], structured=[False, 0, None], details={"count": 0, "enabled": False})
```

Text and PNG/JPEG/WebP images remain typed through the one existing agent loop,
model context, tool UI and transcript persistence. Structured JSON (including
explicit null) remains canonical inspectable content, not hidden behind a summary.
Extension metadata is separate: `extension_details_present` distinguishes omission
from explicit null and `extension_details` retains the exact bounded JSON value.
It is not converted to a model message or interpreted as host permissions. Typed
AgentToolResult content/details is accepted too; structured data uses the builder
or explicit structuredContent envelope.

Rich tool data is snapshotted and independently checked at worker and host boundaries.
Unknown content kinds/extra fields, invalid image base64/MIME, nonfinite/non-JSON
values, coercions and oversized frames fail rather than silently discard data.
Limits: 64 content blocks, four images, 2 MiB decoded per image, 1 MiB per rich text
block, 256 KiB each for structured data/details, and 3 MiB for the encoded rich
result envelope. These are explicit API/frame limits, not OS-enforced sandboxing.
No file/URI is implicitly opened and no image is automatically saved or attached
as a new user prompt. Raise an exception for tool errors; rich isError/audio/resource
payloads are not silently guessed in this API version. Commands remain bounded text.

Coding approvals, Claw extension_allow_tools, read-only and child inherited permission
restrictions still gate execution. Text-only post-hooks do not replace or drop a
result containing images. Legacy text callbacks/post-hooks retain their behavior.
Both UIs display tool images without printing base64. Claw now forwards final tool
image parts (previously missing from its display events), with a bounded live image
budget. Larger generic tool images are visibly marked as omitted from the live
preview; original data stays in canonical model/transcript history, not in an
oversized socket line. The new rich-extension envelope fits that transport budget.

Independent real workers, denied/allowed live app paths, typed persistence, inherited
child output, malformed/unsupported results, large-preview notices and both private-
tmux image/JSON inspectors are verified. These typed tool results do not grant
extension editor/session mutations, custom rendering or terminal interception.
Audio/resource/error-image interoperability, remaining MCP/credential and native-
platform gaps remain separate from the retained tool-result contract.

### Rich extension command artifacts

Extension command callbacks now accept the same typed text/image/structured JSON/
details result builder as tool callbacks. Legacy string commands remain strings.
The shared `xdog.agent` extension runtime's `runtime.command()` returns either str
or a frozen `ExtensionCommandResult` with extension/command identity, a validated
result snapshot and safe summary.
For example, a command can return:

```python
return api.result([
    {"type":"text","text":"Command report"},
    {"type":"image","data":png_base64,"mimeType":"image/png"},
], structured=[False, 0, None], details={"enabled":False})
```

Both interactive clients append a **read-only command artifact**, not a tool result
or agent message. Images render through the existing image component; base64 is not
printed as prose. **Ctrl+O**/the transcript viewer inspects the complete text, typed
JSON and details. Transcript copy/export can inspect its textual system-source
record. Original image bytes and typed metadata remain owned in the local artifact;
this does not pretend a text-only transcript export includes binary images.

Returning `/quit`, a URI or other command-looking text is data, never executed.
Rich artifacts cannot trigger a model prompt, mutate the main draft, approve tools
or change host read-only scope or permission modes. The prior draft/focus and normal approval
priority remain; callbacks still require trusted activation/idle ownership. Results
are fenced by command/session/connection identity; canceled/stale/foreign output is
not appended to a new owner and callbacks are not replayed. Artifacts are local UI
results, not newly persisted canonical agent-history entries or automatically saved
files. Legacy command/builtin skill prompt behavior is unchanged.

Claw independently negotiates `extension_command_results: 1` in hello/pong. Its
controlled response carries a validated artifact separately from the safe output
summary. Older viewers get a visible update-required summary without image bytes,
not a dict stringification containing base64; no callback is rerun to degrade it.
New clients reject unnegotiated/malformed artifacts rather than guessing semantics.
The rich envelope's existing byte/count/image/JSON limits apply, keeping control
responses below the receiving transport budget.

Independent real command workers, both production UI paths, permission/draft/history
non-mutation, system-source inspection, stale-owner rejection, older-viewer behavior,
cancellation and malformed returns are tested, plus both private-tmux image/JSON
inspectors and parent terminal restoration. Returning an artifact does not grant
editor/session/navigation mutations, custom rendering/layouts or raw terminal access;
those extension interfaces are removed. Read-only command inspection, provider
registration and explicit artifact export/reload have their own contracts below.

### Lossless command artifact export and read-only reload

`/export PATH artifact` captures the latest **owned** rich command artifact, including
original image bytes, text, structured JSON and typed details. It is private JSON
only (`json` may be explicit); implicit binary copy/share or lossy text/Markdown
artifact export is refused. Supplying a path does NOT bypass review. The existing
output panel shows image MIME/size/checksum and a no-redaction/sensitive-binary warning,
without rendering base64. Enter writes only the reviewed immutable snapshot.

Files use the existing private 0600, same-directory staging/fsync and no-overwrite
commit path. Cancel, hidden/tiny/resize review, owner/context changes and stale jobs
cannot authorize an unreviewed write. A commit already made is not remotely undone;
cancellation is not rollback. No upload, provider request, tool call or automatic
artifact save is introduced. Ordinary textual transcript exports retain their prior
semantics and are not falsely labelled binary exports.

`/artifact PATH` explicitly reads a bounded regular artifact file off the UI thread
and appends a read-only image/JSON command display. It does not activate extensions,
execute stored command names/text/URIs, change permissions or host read-only scope, or send input to
the model. The 4 MiB file envelope is versioned as xdog.extension-command/v1; its
SHA-256 covers canonical validated identity/content/metadata. Checksums detect
corruption, NOT authenticity: treat files as untrusted data. Unsupported versions,
tampered/malformed data, image/MIME/type failures and FIFO/nonregular inputs are
refused. Original image bytes and false/zero/null/omission remain exact on reload.

Artifacts are selected by session/connection/history epoch. Old-owner results cannot
be exported or appended to a new owner; loading snapshots publishes only under the
captured context. A read can be canceled by closing/changing owner, suppressing late
publication. An OS-blocked filesystem thread cannot be forcibly interrupted; these
checks/time/memory bounds are not OS-enforced sandboxing or filesystem confinement.
Loaded artifacts become owned display data and can be re-exported after a new review.

Both actual clients and private-tmux workflows verify binary save/reload, explicit
review, private mode/no overwrite, owner fencing and no-model/draft non-mutation.
The shared `xdog.agent.extensions.command_result` helpers
`artifact_bytes`/`artifact_from_bytes`/`read_artifact` expose the same validated codec.
Artifact export/reload does not restore removed extension custom rendering, host
mutations or terminal interception. Further MCP/credential and native-platform
interoperability remains separate work.

### Command session metadata

Read-only command metadata never opens or activates a session browser.

### Failed extension inventory and cancellation

Failed/disconnected/unloaded worker registrations withdraw from the current tool
inventory. Historical alias identities and stale-executor/argument guards remain;
withdrawal does not authorize an old approved call or unload a mandatory validator.
Projection settles on the owning Agent/command/child loop, including callback
errors and cancellation. Other healthy workers/native tools remain unchanged.
Child refreshes retain their explicit selected-tool subset. A post-hook failure
preserves the actual tool result instead of replacing it with invented success.

Failure catalog updates are descriptive, owner-scoped metadata: they cannot adopt
a different session, activate/reload a worker or approve tool execution. Old or
foreign cached contributions never become current merely because a callback failed.
Reload/unload remain explicit user actions; uncertain callbacks are not replayed.

Claw advertises `extension_command_cancel: 1`. Escape cancels the active owned
extension control after the most specific dialog/view closes. The original request,
group and session lease identify the cancellation; the pending command remains
until its worker settles. Canceled controls refresh validated failure metadata, not
model limits/results. Before-admission cancellation also releases the owned group
reservation and returns a cancellation receipt. Unsupported gateways are not sent
guessed controls, and disconnect/uncertain operations are never replayed.

### Terminal command help

`/help` lists the current terminal command/completion menu, including UI-only
controls and already registered extension commands. `/help QUERY` matches command
names or descriptions case-insensitively. It does not execute callbacks, load an
extension, submit model work or grant tool approval; availability is still checked
when a command is actually invoked. Explicit unload/failure/menu changes appear
on the next help request. Descriptions are inert single-line text and terminal
controls are stripped. Oversized/invalid menus fail explicitly, never masquerade
as a complete truncated list.

Help may be inspected during active work without resetting its timer or replacing
the draft. Claw help reflects the current TUI completion snapshot, not an invented
live runtime/catalog capability on older gateways. Use `/extensions` to refresh
extension status explicitly. Coding's headless help retains its separate Skills
section and also supports query filtering/known registered commands.

### Registered command argument completion

Trusted Python commands may register an independent bounded data callback:

```python
async def complete(prefix, context):
    return [{"value": "release", "label": "Release", "description": "Argument data"}]
api.command("deploy", "Deploy command", execute_deploy, complete=complete)
```

The shared Agent extension runtime's `runtime.command_completions()` exposes
current registration identities. Coding's SDK and Claw's internal application
session adapter delegate `complete_command(command, prefix, identity)` to that
runtime, querying the corresponding worker without executing its command callback
or model. For Claw this is gateway-owned application wiring, not a public Python
embedding entry point.
The callback has no interactive UI/editor/navigation/session-mutation authority.
Items are plain single-line text (32 unique entries/64 KiB result); the prefix and
value/label/description bounds are validated on both sides. Failed/timed-out
uncertain callbacks settle/reap their workers and withdraw current declarations;
no retry/replay or fallback callback is implied.

Replacement/disposal changes the command completion identity. Stale references
and registration changes during callback execution yield no completion data;
an old disposer cannot remove a replacement. Coding's SDK wrapper and Claw's
internal session adapter settle their owning Agent's tool projection if that
worker fails.

Both interactive parent editors now query registered argument callbacks in the
background. Suggestions replace only the cursor-local token (including its right
suffix); quoted whitespace stays inside that token, with quotes passed literally
rather than shell-expanded. **Tab or Enter inserts without submitting**. A subsequent
Enter executes the typed command only through its normal command/permission path.
Pending queries cannot turn Enter into an accidental command submission. Escape
dismisses the suggestions and ignores their late result; Tab reopens explicitly.

One query is in flight plus one coalesced latest draft. Original session/context,
command registration, full draft and cursor are revalidated before inserting.
Changed cursor/draft/registration/connection results never overwrite the current
editor. Claw separately negotiates `extension_argument_completion: 1`, runs the
query under the actual idle group/worker owner, and does not activate a cold session
or borrow the UI-command/model approval context. Previous gateways return unavailable.

Escape dismissal, loss of editor focus, hidden suggestions and application exit
now signal the owned completion task immediately. One cancellation is sent per
in-flight owner; its slot remains held until actual cleanup settles, so repeated
Escape cannot interrupt worker reaping or launch a successor early. Uncertain
callbacks are not replayed: their worker is reaped and current registrations are
withdrawn; explicit reload is required if the extension remains desired. Coding
settles its owned completion threads before retiring the extension manager at exit.
Claw separately negotiates `extension_argument_cancel: 1` and pins cancel messages
to the original request/connection/group/session. Older cancel-unsupported peers
retain the bounded deadline fallback and never receive guessed cancellation.

Argument menus now render each supplied `label` as presentation and show its actual
insertion `value` in brackets when they differ, alongside the description. Duplicate
labels are allowed but duplicate insertion values are not; selection and replacement
stay keyed to the value. Unicode/narrow layouts use the shared bounded renderer.
Changing only a label refreshes presentation without selecting a different value.
Unsafe/oversized label or description data rejects the entire result, not a partial
menu. Coding reconstructs owned row copies after immutable cross-thread delivery.

Completion callbacks may read `context["argument_context"]` when the current
caller supports full argument context. It is a copied dict with `arguments`
(the captured text after the command), `cursor`, `start` and `end` (Unicode
code-point offsets relative to that argument body). The token prefix is checked
against `arguments[start:cursor]`; this enables later-argument suggestions based
on preceding choices without reading the rest of a session or granting editor
mutation authority. Host selection still replaces its original cursor-local span.
Callback edits to this dict do not change the editor or its captured request.

Prefix-only queries through the Agent extension runtime or legacy callers explicitly
supply `None` for that context; they cannot impersonate a full draft. New Claw editor queries independently negotiate
`extension_argument_context: 1`. Unnegotiated/invalid/foreign-scope metadata is
refused before callback admission. Withdrawing support cancels captured contextual
jobs (including before task startup), refuses late contextual output and does not
silently replay the query as prefix-only. A user may explicitly edit/Tab to request
new data under the remaining capability. Existing prefix-only callbacks remain
supported; `argument_input=` is optional on Coding's SDK wrapper and Claw's internal
session adapter, without making the latter a public embedding API.

More expressive suffix semantics and broader argument API interoperability remain
open. Callback-supplied spaces/quotes are literal replacement data; the host
never silently adds shell quoting or interprets the resulting argument. Independent
child prompt completion is not claimed. Native file/slash-name completion is retained.

### Extension terminal command-menu inspection

```python
page = await api.get_command_catalog(query="", offset=0, revision="")
# Continue using page["next_offset"] and page["revision"].
```

A live native host may bind read-only menu inspection to an eligible owned command,
tool, lifecycle/pre/post-tool, submitted-input or context callback. It reads the
same sanitized terminal menu used by `/help` and completion, in 32-entry pages:
normalized name/description, source label, session identity, revision and explicit
pagination only. Host availability and exact invocation/session/child admission
remain authoritative; a detached/headless or unsupported callback gets `None`,
not another owner's menu. Independent child callbacks cannot borrow the parent's
menu/editor. Provider/setup callbacks receive no menu control authority.

Worker-supplied routing/host identity is refused. The host's trusted invocation
identity is separate from plugin request fields and carries no mutation grant.
Changed menu/session/registration invalidates a former revision and returns
`status="stale"`, no entries and no next page. Restart at offset zero explicitly;
there is no silent merge, command execution, model input or automatic replay.
Claw separately negotiates `extension_command_catalog:1` and its generation;
old session/inspection support does not substitute for that exact bridge.
Session/tree catalogs below retain their separately scoped read-only contract.


### Read-only extension session metadata

`await api.get_session_name()` returns the actual owning session's name, or
`None` when unavailable; an empty name is not a borrowed parent value.
`get_session`, `get_model`, `get_context_usage`, `get_active_tools` and
`get_tool_inventory` retain bounded, owner-projected read-only data. Session/tree
and command catalogs remain read-only; `has_pending_messages` is a boolean query,
not a queue control API. Callback admission, role and session/child provenance
remain checked; queries neither start a model nor grant permissions.

Extensions cannot read/replace the unsent editor, rename/navigate sessions,
select tools, request compaction or enqueue prompt/context/follow-up input through
host control APIs. Native USER `/name`, `/model`, `/compact`, `/sessions` and
`/tools` remain independent. Business tool/provider/command/hook registration and
basic input/confirm/select/status remain supported.

Claw's read-only session bridge uses `extension_session:1` and
`extension_session_inspection:1`; terminal command-menu inspection separately uses
`extension_command_catalog:1` and its generation. No editor/navigation/mutation
capability or separate extension name/active-tools flag is needed. Native
`session_name_read` and `tool_inventory_selection` controls remain independent.
Session/tree catalogs read the bound backend directly; listing them does not grant
plugin navigation authority.

### Command session/tree catalogs

Interactive extension commands can now read actual host navigation metadata without
opening a dialog or activating context:

```python
first = await api.list_sessions(query="project")
if first is not None and first["has_more"]:
    second = await api.list_sessions(query="project", offset=first["next_offset"], revision=first["revision"])
tree = await api.get_session_tree()  # current host session
branch = await api.get_session_tree(target_id, branch_id=branch_id)
```

Pages contain scope, owner_session, resolved tree session_id/branch_id, query,
offset/total, at most 50 entries, has_more/next_offset and membership revision.
Entries are inert bounded id/title/detail/kind/session/branch/checkpoint/depth/active
metadata and safe checkpoint previews—not full hidden conversations or system
prompts. Text/type/ID/offset/count/frame validation rejects malformed responses
rather than coercing them. Page encoding is capped at 256 KiB, query at 1000 UTF-8
bytes and catalog traversal at one million entries.

Command session pages use stable identity order, not the native browser's recency
order, so the command's own durable entries cannot move sessions between pages.
Pass the returned revision with continuation calls to refuse a changed membership/
owner/query/target rather than mix catalogs. It is a membership/topology identity
revision, NOT a frozen full-history/metadata version; previews/active flags may be
fresh on each read. Restart at offset zero after a None refusal. Leaving revision
empty explicitly requests a fresh current page. Native browser behavior is unchanged.

Coding reads the existing session-manager/tree sources off-thread under captured
command/session context; Claw reads the owning GroupRuntime catalog, not a client
cache or launch-directory guess. Claw tree targets remain restricted to that group.
Host catalog operations preserve their actual filesystem owner until creation/read
settles on cancellation, then refuse late adoption. An OS-blocked thread cannot be
forcibly killed; these limits/ownership checks are not an OS sandbox.

Only actual interactive extension-worker command invocations receive catalog
authority. Tools/hooks/context/child/unattended callbacks return None; original worker
invocation and host extension/command/group/session ownership are independently
checked. Missing/foreign/unreadable targets or membership changes yield unavailable
without disabling the extension or replaying reads. Invalid plugin request/response
codecs still fail closed. No model turn, permission/read-only mutation or session activation is introduced.

Real workers, both live hosts' 61-entry multi-page catalogs, search/tree/branch
previews, revision ordering/changed membership, foreign-group refusal, cancellation
settlement and production terminal catalog commands are verified. These catalogs
are read-only: they do not expose the removed navigation/editor/session mutation,
custom-rendering or terminal-interception APIs. MCP/credential/native-platform
interoperability remains subject to its own limits.

### Runtime-registered extension shortcuts

Trusted extensions can register a shortcut and its actual command callback:

```python
async def inspect_model(args, context):
    model = await api.get_model()
    return model["id"] if model is not None else "Model metadata unavailable"
api.shortcut("inspect", "alt+k", "Inspect the current model", inspect_model)
```

Registration contributes the namespaced `/ext-EXTENSION-NAME` command plus a
canonical default key; pressing it uses the EXISTING interactive command owner,
not another tool loop or arbitrary input interception. Plugins must already be
explicitly trusted/activated; a key does not discover/load an extension. Callbacks
retain normal idle/child/tool-policy/session/connection checks and only the retained
read-only command host queries and basic dialogs. No model turn or tool approval
is implicitly created by a shortcut itself.

**`/keys`** lists effective extension keys and disabled conflicts. Built-in and USER
editor bindings always win. When different extensions claim one effective key,
ALL colliding shortcuts are disabled (manual commands remain available). Typing,
unmodified editor/dialog keys, Escape, Ctrl+C/D/L/Z and confirmation/recovery routes
cannot be captured. Release/repeat events cannot duplicate callback invocation.
Registration capacity is 32 per extension/64 across active registrations.

The existing `keybindings.json` can remap or unbind a registered shortcut using its
full lowercase command name, for example:

```json
{"ext-keyed-compose":"alt+x","ext-keyed-blocked":[]}
```

Multiple explicit keys may be provided as a list. Overrides still reject duplicate/
built-in collisions and reserved routes. Configuring an inactive command does NOT
activate a plugin; `/keys` reports it unavailable. Changes take effect when the
client loads its keymap, like existing prompt bindings. Terminal encodings can make
some Ctrl/Alt combinations indistinguishable from Enter/Tab; choose an unambiguous
key or a terminal with extended keyboard support. Alt+K/Alt+X are physically tested.

Dispatch is confined to the ready idle prompt: approvals, extension dialogs,
viewers, completion pickers, child/MCP panels, overlays, output reviews,
active commands/model work and closing/disconnected owners retain priority. Unload/
failure/reload updates registrations; stale keys cannot adopt a new callback. Coding
now refreshes actual registration snapshots after extension commands even if a plugin
publishes no status or notices. Claw separately advertises `extension_shortcuts: 1`;
missing support is both dispatch-disabled and reported in effective help.

Real workers, both UI command/editor paths, user remap/unbind/inactive overrides,
reserved/default/user/inter-extension conflicts, contextual suppression, repeat,
load/reload/unload and both private-tmux default/remapped key paths are verified.
Arbitrary terminal interception and custom renderers are removed, not pending
shortcut features. Provider registration and read-only host queries retain their
separate contracts; MCP/platform interoperability is not implied by a shortcut.

### Submitted-input extension hooks

Trusted extensions can register `api.on_input(callback)` for newly admitted human
input, before its response model call or canonical user-message commit:

```python
async def prepare(text, metadata, context):
    # metadata: prompt/steer/follow_up source, active channel and image metadata.
    if text.startswith("reject:"):
        return {"action":"reject","reason":"Explain why this input was refused"}
    return {"action":"transform","text":"Prepared input: " + text}
api.on_input(prepare)
```

None or `{ "action": "continue" }` leaves text unchanged; a string is shorthand
for transformed text. Explicit transform/reject objects have exact typed fields;
unknown/handled/image/command-action payloads are NOT guessed. Text is limited to
1 MiB UTF-8, rejection reasons to 2000 bytes and callbacks to 32 per extension.
Callbacks run in deterministic registration/activation order in their actual
owned worker. Missing/malformed/failed/timed-out hook decisions refuse admission
and quarantine the failed worker; known failed filters cannot silently pass raw
input until explicitly unloaded/reloaded. A normal explicit rejection keeps the
extension active. Callback side effects are not rollback and are not replayed.

The Agent's optional `input_transform` hook now covers initial input, identified
steering/follow-ups and plain explicit continuation before canonical admission.
Transforms preserve queued identity/recovery receipts: consumption happens only
when the transformed message is committed. Rejected selected identified input
remains recoverable with original text/images; plain rejected steering/follow-up
messages enter an explicit-recovery quarantine (`agent.drain_rejected_messages()`),
not a live queue that could replay them on the next prompt. Initial rejected
input belongs to its caller, not a fabricated assistant/provider response. Hook
cancellation is settled before idle/owner release. Preexisting Coding SDK input
hooks are composed rather than replaced.

The extension API sees text plus image MIME/encoded-size metadata, never original
base64. Its output cannot silently remove/replace images: transformed messages keep
original typed attachments/timestamps. All later model capability, tool argument,
approval, host read-only and child permission gates remain authoritative. A transformed
string resembling `/quit`/a URI stays model input data, not a reparsed host command.
Editor/session/navigation mutation authority is absent from both hook and command APIs.

Both interactive clients show the actual transformed user text/queued consumption,
with visible notices. Initial refusal restores original draft/attachments without
model input or an automatic replay. Coding uses the current worker-owned image
batch for recovery, not a potentially lagging UI active-image pointer. Claw applies
its existing per-connection/run input-ID normalization/fencing to transform/refusal
receipts too. Child agents use their own extension worker/input history, not parent
history. Claw scheduler/goal/system initial inputs are explicitly NOT human input
hooks; queued human steering/follow-up remains intercepted. Channel metadata is the
active turn channel, not a claim of a separate queued sender's identity.

Real workers, generic initial/steer/follow-up/continuation/abort receipts, both live
clients' typed attachments/canonical history/refusal/recovery, actual queued follow-up
images/consumption, child isolation/system exclusion and private-tmux transform/
refusal recovery are tested. Submitted-input transformation and the handled outcomes
below remain supported; neither exposes raw-key observation/interception, custom
rendering or host mutations. MCP/credential/native-platform interoperability remains
subject to its own limits.

Pre-turn compaction of PREVIOUS history retains its existing lifecycle; the input
hook is a pre-admission/user-response check, not a veto of independent maintenance
work. Failed reload of a previously registered input filter remains failed/required,
not an implicit unload: only successful reload or explicit unload changes that
policy. Coding print mode reports a refused input as a nonzero error, not a
fabricated successful model answer. The removed Coding frontend RPC has no error
or completion-receipt interface.

Terminal verification also caught a generic resize race during output review:
painting after dimensions changed during tick callbacks could skip the next loop's
cursor query. That paint is now deferred until the normal input loop re-anchors.
Existing visible-confirmation/query assertions were retained, not weakened.

### Explicit handled-input outcomes

Submitted-input hooks may now return an explicit handled decision with bounded
feedback, for example:

```python
if text.startswith("local-help:"):
    return {"action":"handled","feedback":"Extension handled this request; no model answer was generated"}
```

Feedback is required, nonempty and at most 64 KiB UTF-8; extra image/command/action
fields are rejected. Handled is NOT rejection and NOT canonical/model history
admission. It stops the remaining input-hook chain for that request and emits a
separate `InputHandledEvent` logical-consumption receipt with original source/ID/
attachments. Identified inputs are removed exactly once; they do not also emit
InputConsumedEvent or return for automatic replay. `xdog.agent` SDK callers can
inspect/drain handled receipts with `agent.drain_handled_inputs()`.

If all newly supplied input is handled, the host does not invoke the response model
with old history just to obtain an answer. Other genuinely queued unhandled requests
may still run normally, preserving ONE_AT_A_TIME steering boundaries. Explicit
`agent.prompt([])` retains its preexisting old-context continuation semantics: it
contains no submitted request to intercept. If one request is handled and a later
request rejects/fails, the handled receipt remains committed rather than falsely
rolled back; remaining original input stays recoverable. Trusted callback side
effects are not an OS sandbox or rollback transaction.

Original image bytes are never given to the hook, and handled decisions cannot
silently discard attachments. Both interactive clients retain them in the editable
composer without restoring/replaying the consumed text. Queue/run/connection ID
fences apply to the handled receipt too. Feedback is labelled system/extension data,
not fabricated assistant prose or tool approval. Permission, host read-only, tool and model
capability gates are unchanged; returned command-looking feedback is never executed.

Print mode outputs explicit feedback and returns success for handled-only input;
Coding JSON output emits a valid system/input_handled envelope (optional real
assistant output is separate), not a counterfeit model message. Claw sends a
content-free final signal after actual handled feedback, releasing run ownership without synthesizing a response. Other
non-TUI channels receive extension feedback as channel output, not model generation.

Child supervision retains labelled input feedback as separate bounded metadata
(64 entries/64 KiB), with zero model calls when the task is handled. It survives child
record reload and appears as SYSTEM feedback in source inspection, not invented
user/assistant history. Parent conversations remain unchanged. New shipped clients
are jointly verified; older-viewer handled-outcome interoperability remains unproven
rather than being inferred from JSON accepting extra event types.

Generic initial/queued/old-history/queue-mode receipts, both live initial and queued
image paths, child result/source metadata, text/JSON completion and production
terminal model-call counters are tested. Plugin terminal key/paste/release
interception and observation APIs are removed, as are custom renderers and extension
host mutations. Handled outcomes preserve the ordinary permission and ownership
boundaries; remaining MCP/credential/platform interoperability is separate.


### MCP browsing and manual refresh

Active resource/catalog/combined notification watches are removed from the Agent MCP SDK,
transports and native UI. There are no watch/unwatch methods, editable subscription
filters, watch jobs or background subscription listeners. Former **w** resource
watch and **Alt+T/P/U/W** catalog/combined controls are not supported.

Ordinary MCP tools, resource/prompt catalogs, explicit reads/completions and manual
refresh remain. Passive tool-catalog invalidation on supported legacy notices only
marks cached definitions dirty; it does not fetch a catalog/resource, register tools
or start a model turn. Configured Roots and exact request-state continuations,
response-scoped SSE, cancellation and bounded legacy GET recovery remain separate
transport features. They never authorize uncertain tool POST replay or automatic
reconnect. Normal MCP chat/tool traffic and private extension-worker JSON-RPC are
not removed by this simplification.

### Explicit saved MCP OAuth credentials

Completed resource-bound OAuth grants can now be saved **only after a separate
reviewed confirmation**. In the authorization status view, **p** prepares save.
From the MCP server list, **l** prepares explicit restore and **x** prepares
saved-credential erase. Every action pages the issuer/resource/client/scopes/endpoints,
TLS trust, destination and storage disclosure before Enter becomes available.
No token/code/secret paste counts as consent; old or detached receipts cannot commit.

The default `mcp_credential_backend: "file"` stores a **private local plaintext
JSON file, not encrypted/keychain storage**.
Coding uses its user data directory's `mcp-credentials`; Claw uses the group's
private data directory. This implementation requires verified local POSIX ownership
and 0700-directory/0600-file permissions. Unsupported platforms and unsafe paths,
symlinks, hard links, ownership/mode/type/size violations fail closed rather than
claiming equivalent protection. Credential filenames are resource hashes; writes
are bounded, fsynced and atomically replaced through pinned directory-relative I/O.

Nothing loads at startup. Explicit restore validates exact resource, configured
issuer/pre-registered client/public method/scopes,
certificate-file content binding and expiry, then admits the credential to memory.
It performs no metadata lookup, token POST, connection, model turn or tool grant.
MCP client-secret methods are not supported; provider credentials remain a
separate worker/vault contract. Access/refresh credentials have
no public diagnostic representation and never enter transcripts or review payloads.

Saved access expiry uses bounded wall-clock values and is converted back to an
in-memory deadline on restore. Expired access can preserve its saved refresh grant,
but refresh still needs its own live reviewed action. Before a potentially rotating
refresh POST, an existing saved snapshot is removed, so restart cannot replay an
old refresh token after uncertain rotation. Rotated credentials are **not** saved
implicitly; explicitly review/save the new grant if desired.

**x** erases the saved credential only (not a live token or remote grant). The existing
authorization-status **f** forget/disconnect review erases both memory and saved
state; neither action
claims remote revocation. Save/erase cancellation revokes storage admission and waits
for the actual writer to settle; if an operation committed before cancellation,
inspect local state rather than automatically retrying it.

Agent MCP SDK callers can opt in with `McpManager(..., credential_dir=...)` and prepare/confirm
`credentials_save`, `credentials_restore`, or `credentials_forget` through the
existing owned `manage_auth` API. A manager without a credential directory remains
memory-only. Actual client restart/consent and private-tmux tests cover the file
backend. File permissions are not an OS sandbox.

#### Opt-in Linux Secret Service storage

Set the top-level `mcp_credential_backend: secret-service` in
`~/.config/xdog/claw/config.yaml`, then restart the gateway to load configuration.
The default remains `file`; each group has a separate native storage namespace.

Agent MCP SDK hosts select
`McpManager(..., credential_dir=..., credential_backend="secret-service")`.
The directory is used only to derive a stable namespace; this backend creates no
local token files. Moving that directory changes which saved credentials are found.

Prerequisites are Linux, `/usr/bin/python3` with OS-provided GI bindings and the
Secret 1/libsecret typelib, an existing `DBUS_SESSION_BUS_ADDRESS`, and an
**already running Secret Service with an unlocked default collection**. XDOG does
not install prerequisites, start the vault, create a collection, unlock it or
launch an authorization prompt. Unlock your vault externally before storage actions.
An encrypted Secret Service session is required; at-rest protection is supplied
by your vault, not guaranteed by XDOG.

The same explicit save/restore/forget reviews apply, with a **NATIVE SECRET SERVICE**
storage notice. No startup restore or implicit save is added. Invalid backend names,
missing prerequisites, locked/ambiguous records and malformed data refuse without
falling back to plaintext files or initiating login. The owned helper has a bounded
10-second operation timeout; cancellation terminates and reaps it. A mutation that
was already admitted may have committed: inspect the vault before retrying; XDOG
never automatically replays an uncertain mutation.

Tests cover actual encrypted save/read/forget, locked-vault refusal, manager
restart/consent and both application UI owners against an isolated owned GNOME
keyring service, plus helper timeout/cancellation. This is not a native-backend
tmux test or proof of every Secret Service implementation. Credential storage is
Linux-only: `file` and `secret-service` are the supported choices. Removed native
backend names are rejected without reading credentials or silently selecting file
storage. Neither Linux backend is an OS sandbox; broader service interoperability
is not implied by the isolated service tests.


### Session-owned extension providers

Explicitly trusted extensions can declare provider callbacks during setup:

```python
def setup(api):
    async def stream(model, context, options):
        yield {"type": "text", "text": "local response"}
        yield {"type": "done", "stop_reason": "stop"}
    api.register_provider("local", [{
        "id": "example", "name": "Example", "context_window": 32000,
        "max_tokens": 2048, "supports_tool_calls": False,
    }], stream)
```

Activate the extension explicitly, inspect `/model`, then select its full alias
`ext-EXTENSION-PROVIDER/MODEL`. Registrations belong to that session's owned worker,
not the global AI provider registry; aliases cannot replace built-in names. Context,
input modalities, reasoning/tool flags, output bounds and optional pricing are
validated cached metadata. Catalog inspection starts no model request. Explicit
configured/saved alias activation and provider authentication are documented below;
neither permits implicit login or another provider's fallback.

The callback receives lossless JSON context/options through a bounded checksummed
transfer. It returns an async iterator of typed `text`, `thinking`, `tool_call`,
`usage` and explicit `done` events. Adjacent text/thinking chunks coalesce by default,
with the latest continuity signature; `new_block: true` starts a separate block.
Tool calls are data routed through the SAME existing agent validation/permission
loop, never executed by a provider event. Calls not offered in the actual context,
duplicate IDs, malformed/nonfinite data, quota violations and missing completion
fail rather than granting authority or using a fallback provider.

Four registrations/extension, 32 models/registration, 64 session models, 4,096 stream
events and 8 MiB emitted content bound this callback path. Output options are capped
by declared model limits. Only one owned stream/input transfer runs per worker.
Provider callbacks cannot borrow dialogs/editor/session/command authority. Cancel
settles the actual in-flight worker RPC; uncertain callbacks are quarantined/reaped,
not replayed. Unload/reload/close generations fence results and make the explicit
alias unavailable rather than silently routing to a built-in.

Child model overrides use the independently created CHILD extension worker and
retain actual model context/vision/reasoning and protocol/resource guards and parent tool
restrictions; selecting a child provider does not switch the parent's model.
Existing primary routing and native/global provider registration are unchanged.
Owned worker/client/child and production Linux/private-tmux model-selection paths
verify this behavior without paid model calls. Additional native/embedding/search/
provider interoperability and native-platform validation remain separately bounded;
explicit callback authentication and vault storage are described below.


### Explicit extension provider OAuth and retained vault storage

Trusted provider declarations can retain a private `ProviderOAuth` binding through
`api.register_provider(..., oauth=...)`. Login/refresh/key/model callbacks execute
inside the owned extension worker; credentials and callback objects are not public
catalog/UI data. Generic `ProviderAuth` bindings remain separate. These provider
callbacks are not MCP registration methods or an Elicitation capability.

Both native hosts expose `/extensions auth list` or `menu`, explicit `login ID`,
`refresh ID` and `logout ID`, plus `/login [EXTENSION_PROVIDER_ID]` as a thin
selection/login alias. Every operation requires its original idle session/provider
registration and a separate owned review; URL opening is a distinct reviewed user
gesture, never automatic. Sensitive replies go only to the original worker, not
model history or a replacement question. Cancellation/reload/disconnect retires
that owner without automatic callback replay. Login/refresh does not grant tools,
select a model or change permissions. Logout clears worker memory, not saved data.

Provider-defined OAuth callbacks are independent of MCP's public-PKCE profile.
Env/resolver credentials remain private and lazy. A grant that expires while an
async key callback is suspended cannot admit a business stream; failure does not
silently log in, refresh, replay a callback or choose another provider.

Provider credential persistence is retained through explicit
`/extensions auth save ID secret-service /ABSOLUTE/NAMESPACE`, with equivalent
`restore` and `forget` actions. This is **Linux Secret Service vault only**, with
an explicit backend/absolute namespace and provider source/profile binding. It
shares the owned helper's no-start/no-unlock, cancellation/receipt and no-replay
protections. No automatic save/restore/login occurs; missing/locked/unsafe vaults
refuse. There is **no plaintext file fallback** for provider credentials. MCP's
separately selected `file` or `secret-service` credential backend does not change
this provider-vault contract.


### Configured and saved extension-model activation

Configured or saved `ext-EXTENSION-PROVIDER/MODEL` aliases now activate their
explicitly trusted extension workers before readiness/first use. They do not go
through unresolved-model fallback and are not rewritten to a built-in model.
Untrusted/missing/failed declarations fail explicitly and release bootstrap owners.
Metadata activation invokes no provider callback or model request.

Coding's synchronous SDK factory also works when called from an existing async
loop, using one owned metadata bootstrap rather than nested event-loop execution.
CLI `--model`, saved resume (`--resume-id`/session selection), and in-session saved
activation restore the declared limits, tool/image metadata and normalized reasoning
choice. New runtime activation occurs once; ordinary explicit unload is never an
implicit reload. Bootstrap errors are reported cleanly by the CLI.

Claw activates configured aliases before its owned gateway reports readiness. Its
explicit model selection is saved atomically in a session-owned model sidecar;
gateway restart restores the current context's alias and corresponding worker/model
limits; this does not offer historical-session activation. Selection
persistence completes before the live model is changed. Invalid/symlinked/excessive
saved model files or unavailable trusted registrations fail, not choose a primary
provider. Image metadata comes from the active extension registration, not a global
catalog guess. Existing primary-provider routing is unchanged.

Owned SDK/actual socket/restart tests and production Linux/private-tmux startup and
resume paths verify zero built-in/model calls during activation and correct first
provider use/native recovery. This supersedes the earlier initial-alias activation
exclusion. Additional dynamic/native/provider-auth and host APIs, interoperability
and native-platform validation remain on their original open audit rows.

### Parent-session reasoning controls

`/thinking` inspects the current parent model's effective reasoning preference and
available levels; `/thinking off|LEVEL` changes it without submitting a turn.
The TUI and gateway negotiate this control explicitly. Changes require the current
group/session owner, an idle parent and no active child work. Non-off levels use
cached metadata on the actual stream adapter; no provider discovery/authentication
or model call is performed. Unsupported levels fail without changing preferences.

Successful changes atomically persist a bounded session/model-bound sidecar before
updating live options. Restart restores only a matching model's setting and validates
advertised capabilities. Changing models does not borrow a preference bound to a
different model. New sessions retain their own configured/default settings.
Pre-commit cancellation revokes admission and settles owned disk work; a lost
post-commit acknowledgement may leave a committed effect, so live/persisted settings
stay aligned on the same owner without retry. Inspect `/thinking` after an uncertain
connection outcome rather than automatically resending the command.

The parent setting does not grant tools, change read-only/approval restrictions or
start/replay child work. Reasoning appears in the status row, and the existing
native-scrollback terminal mode remains the default. Older gateways reject this
control visibly instead of sending the command as agent input.

### Configured native model selection

`/model` now inspects configured native models alongside session-owned extension
aliases; `/model ID` resolves/selects a streaming chat model on the **same actual
adapter**. Short names are persisted/routed as that adapter's canonical model ID.
Unknown, inactive, non-chat or unavailable models fail without provider activation,
login, catalog sync or fallback. The TUI requires the negotiated native-model
capability instead of sending unsupported commands as conversation input.

Selection persists per session, updates context/prompt/tool capabilities, and
restores on restart only if the configured adapter still provides that model.
New conversations begin with the group's configured choice, not the prior
conversation's mutable selection. Reasoning remains model-bound. Cancellation
settles admitted disk work; uncertain committed settings are not automatically
replayed or rolled back. Existing parent/child/read-only/tool permission gates apply.

Catalog transport uses an allowlisted public summary rather than serializing whole
`Model` objects: authentication headers, endpoint URLs and protocol internals are
not exposed through model inspection. Cached catalogs are bounded and duplicate or
reserved-namespace native identities fail explicitly rather than silently shadowing
extension models. These controls do not submit a turn; only a subsequent explicit
prompt invokes the selected model.


### Configured tool browser

**`/tools [QUERY]`** opens a read-only browser of the actual owning Agent's
configured tool names, descriptions and full JSON schemas. Search includes names,
descriptions and schema text—not just the visible viewport. `/`, n/N, arrows/pages,
selection and **f** full-height viewing reuse the existing source browser;
**r** explicitly refreshes and Escape returns to the unchanged parent draft/cursor.
Copy/export/share use the captured tool-definition records, never conversation
history as a fallback. Existing explicit output review/private-storage policy applies.

The summary separates configured definitions from the model API gate and explicitly
states that listing a tool is NOT execution approval. Unsupported/text-only models
can inspect their local definitions without sending API tools. Browsing does not
execute or enable tools, register MCP/extension definitions, or submit model input.
Snapshot refresh is explicit, not automatic during a run.

Coding reads metadata on one bounded background worker; its headless `/tools` command
prints complete, query-filtered JSON without truncating the first inventory page.
Claw separately negotiates `tool_inventory:1` and reads the gateway's actual group/
session Agent, with request correlation, cancellation, timeout, capability withdrawal
and session-owner invalidation. Cold inspection may initialize the group's local
session cache and returns its actual session ID, but performs no model/tool turn or
idle/daily reset. Older gateways report unavailable—no client footer/schema guess.

Full definitions remain bounded by the existing 2,048-tool/512-KiB inventory limit.
Unavailable/invalid/changing owners are reported explicitly rather than displayed
as an empty or approved tool set. Definitions are public model-facing data; authors
must not place credentials in schemas. This is Linux/private-tmux evidence, not
complete CLI capability parity or every Linux desktop terminal.

### Native skill selection

The TUI exposes the current group's skill catalog and selection without asking the
model to call a tool:

```text
/skills                   # list available skills and the active selection
/skills load SLUG          # select an installed/shared/group skill
/skills unload SLUG        # deselect it
/skills unload all         # clear the selection
```

Changes require an idle owning group/session, including its children. Selection is
current-context metadata, persisted separately from transcript bodies. Archived
branch selections cannot be activated; a new context starts with its own selection. A missing sidecar means no selection; malformed metadata is rejected.

Session-scoped skill instructions are included in the cacheable system prefix.
Turn-scoped instructions are supplied only in the current request's messages,
not stored in conversation history, and expire after the attempted turn (including
failure or cancellation). Selection does not submit a model request, execute a tool,
or grant permissions. It leaves the parent editor draft untouched.

Controls require a negotiated skill-capable gateway. If a receipt is lost,
disconnected, or timed out, an admitted change may already have completed; use
`/skills` after reconnection to inspect the actual selection before retrying.
Mutating requests are never automatically replayed.

### Explicit context compaction

Use `/compact [focus instructions]` to request compaction of the actual owning
group/session, even below the automatic compaction threshold. The optional focus
is limited to 16 KiB. This is a negotiated gateway control, not a user message sent
to the normal agent turn. An older gateway cannot silently reinterpret it as input.
Small histories report a no-op without a summary request.

The summary request uses the selected session adapter/model, including registered
extension providers, and exposes no tools. Claw's existing best-effort durable-memory
flush remains governed by the normal compaction tool/read-only hooks. Failed, empty,
incomplete, or unavailable summaries preserve the original context instead of
replacing it with an unavailable-summary placeholder. Archives are written off-loop,
and committed summary metadata restores as clean conversation context.

Parent and child work must be idle. Drafts and permissions are unchanged. Escape or
`/compact cancel` cancels the owned request; cancellation before the job starts has
an explicit receipt. Admitted atomic history commits settle disk and in-memory
state before releasing group ownership, even under repeated cancellation. A lost
receipt, timeout, or reconnect does not replay compaction. Inspect `/transcript`
(and explicitly refresh an already-open viewer) before retrying an uncertain
operation. Default native scrollback is preserved rather than rewritten.

Trusted group extensions can use `session_before_compact` to continue, veto, or
provide a bounded custom summary, and `session_compact` to observe committed
context. Both manual gateway requests and automatic pre-turn compaction use the
same session-owned contract. Complete typed preparation is transferred without
preview clipping; retention boundaries, conflict/failure handling, and immutable
callback copies are validated. Source/details metadata persists in tagged summary
entries and durable historical checkpoints, including restart of the committed
current summary context; historical checkpoint activation is not supported. Neither hook
borrows TUI command/dialog/navigation authority or grants tool permissions. See the
Agent package's owned compaction extension documentation for the exact contract.

### Inspecting context from before compaction

Each successful manual or automatic compaction creates a durable **Before
compaction #N** branch containing the complete original JSONL conversation,
including the owning session's unsaved live tail. It is indexed before current
context is replaced. A checkpoint failure leaves current context unchanged; a
later replacement failure may leave an extra safe checkpoint but does not produce
a successful compaction receipt. Admitted writes and publication remain owned
through repeated cancellation, with no automatic replay.

Use `/tree` to find the checkpoint and **Enter/v** to inspect it read-only.
Search/copy/export uses that saved source without replacing the active context,
restoring permissions/extension workers, reverting workspace files or submitting a
model turn. There is no checkpoint activation, return-head route or `/goto`. The optional `recovery_branch_id` in committed compaction
metadata identifies the source checkpoint.

Markdown conversation archives now have random unique suffixes, are created
exclusively rather than overwritten, and use private `0600` permissions on POSIX.
Even simultaneous same-second compactions preserve their separate archive files.
These Markdown artifacts are supplementary human-readable archives; lossless
historical source uses the typed checkpoint, not parsed Markdown. Neither archive
format is a command to resume an old execution route.

### View saved context without activating it

Select a saved session/branch in `/sessions` or `/tree` and press **v** for a
read-only full-source view; **Enter** also views without activation. Explicit equivalents:

```text
/transcript branch BRANCH_ID
/transcript saved SESSION_ID [BRANCH_ID]
```

Saved reads go through a separately negotiated gateway capability, the actual
active group/session lease, and an explicit target session/branch scope. A saved
session must belong to the selected group. Reads never resume context, reset the
session, submit a model/tool turn, or change the draft. Old gateways report
unavailable, not live or clipped source disguised as archived content.

Full text/reasoning/tool source uses the existing checksummed bounded-chunk
transport and redaction. Every transfer frame must match its saved scope; late,
foreign-owner, disconnected, or capability-withdrawn reads are not admitted.
Refresh is explicit (**r**), fullscreen remains optional (**f**), and copy/export/
share uses only the displayed saved snapshot with the normal reviewed destination
workflow. Loading/unavailable views cannot export another conversation as fallback.
Closing returns to the active parent draft. This is individual saved-source browsing,
not an automatically merged/deduplicated timeline of every branch.

### No saved-route activation

Claw does not stage or construct a target AgentSession to resume, branch, fork or
return to a historical checkpoint. Those runtime and gateway controls, the target
session staging module and their UI activation paths are removed. Existing saved
transcripts, provider/access/extension metadata and checkpoints remain available
for read-only inspection and ordinary export; they cannot replace the group's
current owner or start another main Agent. Coding's independent route/branch
features are unchanged.

Necessary new/reset operations retain idle/group ownership and cancellation
settlement. They rotate only the current context, preserve group Goals, and never
turn an uncertain control receipt into an automatic replay. Restart restores the
committed current context, not a user-selected archived route.

### Durable metadata and legacy journal recovery

The persistence layer retains its durable undo journal for managed metadata writes
and recovery of existing interrupted transactions. This crash-consistency mechanism
is not a current historical-route switching API. It can cover session, historical
checkpoint, entry, access, catalog and active-pointer files. Private checksummed backups
are flushed before the prepared manifest is published. Managed replacement names are
synced before a committed marker is published. The store recovers interrupted work
**before** loading any session index or exposing access/extension state:

- A prepared transaction restores the prior complete files and removes newly created
  targets. Backup copies remain intact throughout replay, so another interruption
  during recovery can be retried safely.
- A committed marker retains the complete new state and finishes cleanup; a failed
  housekeeping cleanup is not reported as a failed/rolled-back managed commit.
- All backup identities/checksums and managed destination names are validated before
  any recovery writes. Corrupt/missing/unowned recovery data blocks opening the store,
  rather than silently dropping metadata or exposing a mixed state.
- On POSIX, an advisory navigation lock prevents another process from recovering a
  live transaction. It is not a sandbox. Normal runtime files retain their original
  permissions when restored; backup/journal artifacts are private.

Ordinary in-process failures still restore cache state as well as managed files. If
rollback storage is temporarily unavailable, the live store refuses further session
access and retains its journal/backups. Repair storage and reopen/restart the store
for recovery; never delete the journal merely to make the error disappear.

Recovery was verified with abrupt offline process termination at managed-write and
recovery boundaries. This improves the previous multi-file interruption limitation;
it is not a claim about failing hardware, filesystems that ignore durability requests,
or untested Linux storage power-loss behavior. Directory fsync and advisory-lock guarantees
require the documented Linux filesystem semantics. Trusted extension external side
effects remain outside this journal; recovery never authorizes model/tool replay or
an archived route's activation.

### Exact slash-command submission

The shared prompt editor now preserves a completely typed command when Enter is
pressed, rather than automatically submitting a longer neighboring suggestion.
This applies to native and live registered extension commands, including mixed-case
input. Up/Down plus Enter still explicitly chooses a suggestion, and Tab still
inserts it. Text/history/catalog changes invalidate that prior selection intent.
Withdrawn command suggestions cannot redirect submission to another action. Existing
file completion, paste, focused-control ownership, and permission behavior are unchanged.

### Search combined session history without navigation

`/transcript timeline [SESSION_ID]` opens a read-only provenance graph of current
context and all saved branches in the selected group's session. Timeline support is
negotiated separately from saved-source support; older gateways report unavailable,
not live source as an archive substitute. The existing active-owner/revision lease,
explicit target scope, checksums, bounded transfer blocks, cancellation and reviewed
copy/export/share handling remain in effect. No model/tool turn or branch activation
is performed; explicit refresh captures a new snapshot.

Deduplication requires recorded parent-prefix lineage or a verified compaction
recovery link. Source labels and opaque IDs remain visible. Independent roots,
repeated later messages and divergent tool results are not collapsed merely because
text matches. Generic source redaction and system-input filtering remain applied;
binary/signature bodies are not displayed. This is a branch-provenance graph, not a
claimed total chronological ordering of parallel branches.

Limits are explicit and non-clipping: 256 sources including the head, 20,000 messages
per source, 200,000 raw references, and 20,000 unique source records. Invalid/cyclic/
mismatched lineage or capacity failure makes the read unavailable; no partial graph
or different live context is exported as fallback. Individual saved-source viewing
remains available for larger sessions. Active owner/context/permissions/draft stay intact.

### Complete rendered-context image admission

The shared Agent loop now checks typed images in the **actual rendered Context**
after configured context hooks/message conversion and before every model adapter
request. Retained user images, tool-result images and explicitly hook-added images
all count against available model image limits. The validator never prunes images
or silently converts them to text to make a request succeed. Rejection retains
canonical messages/tool results and produces an explicit error.

Metadata comes only from the configured stream adapter's cached `resolve_model`
interface; registered providers use the same contract. Declared positive count,
byte and media limits and advertised image input are enforced. This does NOT apply
the four-new-attachment composer cap to a model advertising a larger whole-context
limit. Unspecified byte limits use the existing neutral 32 MiB payload safety bound,
not an invented vendor limit. Base64/typed MIME checks are not a complete raster
decoder or a guarantee of provider wire-format conformance.

Validation runs off the asynchronous loop. Recheck the same adapter's image profile
after validation: changed/disappeared/malformed metadata refuses the request,
without guessing successor limits or replay. A cooperative abort during validation
also prevents the adapter request after byte checks settle.

Opaque SDK streams (or transparent wrappers returning no metadata) remain
caller-owned: the validator does NOT borrow another/global provider's catalog or
claim that unknown limits passed. Such callers need to supply their own capability
contract/validation. Native/registered metadata-backed app paths are exercised;
unknown-provider/raster/native-platform interoperability remains open.


### Read-only child input inspection

The child roster's **q** (or **Ctrl+Q** from its transcript view) opens a bounded
snapshot of the owning child's pending inputs or stopped recovery metadata.
**Up/Down** selects a row, **Enter** inspects its full metadata, **r** explicitly
refreshes, and **Escape** returns. Delivery lane, checkpoint selection, attachment
count and `initial-task`/`crash-snapshot`/`durable-return` uncertainty are visible.
This native view has no editor, reorder/remove/return control, recovery-copy button
or permission-confirmation action. Paste cannot submit or adopt a draft.

The ordinary main-Agent `agent_control` tool retains `action="queue"` with a typed
JSON `text` request. The `xdog.agent` SDK
`ChildSupervisor.control("queue", ...)` also remains.
Their edit/remove/move/text-return and explicit stopped recovery operations use the
real owner loop and existing identity, revision, selected-input, image-byte and
write-before-publication safeguards. They are not native UI actions and do not
bypass the host's normal tool policy or host read-only scope.

Claw's `child_queues:1` now exposes list-only native inspection. Native queue packets
other than `{"action":"list"}` and retired recovery/binary-transfer packets are
refused before backend mutation or image work. No capability implies queue-writing
authority. Necessary child cancel/wait/lifecycle controls remain separate.

### Explicit stopped-child recovery through Agent/SDK

After a child settles, only identified inputs not admitted to canonical history
are retained with its durable record; consumed follow-ups are not offered again.
Loading records does not construct an Agent or restart work. Malformed, duplicate,
oversized or invalid typed attachments refuse load rather than silently disappear.
Existing bounds remain 32 inputs, 16 KiB text each and bounded aggregate/record/image
retention. Persistence failure is visible, not a truncated success receipt.

The native **q** view is inspection only. Explicit main-Agent/SDK queue action
`recover` uses the original child/input ID and queue revision to return a
non-destructive text copy; `recover_images` handles typed image copies, and
`recover_task` handles an unadmitted initial task. A lost/canceled copy receipt
cannot erase the durable source or automatically resubmit it. `remove`/`remove_task`
are distinct explicit discard operations, persisted before publishing removal;
cancellation waits for actual writer settlement, without claiming rollback.

The main-Agent tool returns image parts as `ImageContent` with bounded receipt
metadata, never raw base64 in model-facing text or a silently lossy text fallback.
An `xdog.agent` SDK embedder owns later adoption/submission and its policy. Inspect
prior history and workspace effects before deciding whether to run a recovered copy.

### Child rendered-image admission

Child requests now validate the **complete rendered image context** even when the
child uses the parent's default model or only overrides thinking. This includes
retained user/tool images and images added by context hooks. The final guard
resolves the actual child adapter's current model metadata, checks streaming-chat
and image capabilities, image-count/byte/media limits and typed base64 payloads,
and refuses a model profile change during admission before calling the adapter.
A withdrawn model is not revived from the earlier selection snapshot. Metadata-less
custom adapters remain supported for text, but cannot send a child image context
without an advertised model profile; no images are silently converted to text.

Retained-context counts use the advertised model limits, **not** the four-image
composer limit. Unspecified counts do not impose that per-draft limit on retained
history. Neutral payload, per-message/persistence-chunk and image collection
resource guards remain; complete child history is chunked without a cumulative
execution-history quota. Offline tests cover both actual application child factories, retained
user/tool images and a real owned context-hook worker. Existing private-terminal
child approval/recovery checks are separate compatibility evidence, not an image
attachment UI test. Native child start/steering/follow-up image drafts and typed submission are
implemented below. Image queue return/stopped recovery and remaining draft
retention/rendering/transport requirements stay OPEN.


### Reviewed child image drafts

The native child editor now supports real image attachments for **start**, **steer**
and **follow-up**. In a child task form, **Ctrl+O** opens a literal file-path entry
(relative to the workspace); **Ctrl+V** explicitly reads the clipboard;
**Ctrl+B** opens the child attachment list (Enter previews, x removes, c clears).
These controls act on the child draft only. The parent text, cursor and attachments
are not borrowed or changed. File/clipboard reads run outside the UI loop, and
images stay editable until a separately painted confirmation review.

The review pages the task, access, configuration, image identity/dimensions/size
and SHA256 digest. Every page must be painted before physical Enter can admit
work; pasted newlines/repeated or unpainted Enter cannot confirm. Image-only tasks
and image-only steering/follow-ups are supported. Successful admission clears the
child draft only after the actual owner returns matching text, image digests and
MIME types. Missing/mismatched/failed receipts retain the exact draft; an uncertain
receipt is disclosed again on reopening. Reopening never automatically submits.

The normal input limits are four PNG/JPEG/WebP images per draft, 10 MiB each.
Editor retention is shared with other local attachments; the child runtime has
its own 40 MiB retained-image and serialized-collection guards. Images no longer
consume the 512 KiB **text/metadata** single-message/chunk resource allowance;
that allowance is not a cumulative child-history limit. Initial images are saved
before child execution and preserved when factory setup fails; canonical and
unadmitted queue images persist without truncation. Reload does not restart work.
Switched-model context preflight no longer treats base64 transport as plain text
but uses a rough image allowance alongside the conservative text guard. This is
not exact provider token accounting or a promise that any image fits any model.
Final actual-context model validation, permissions and protocol/resource guards still apply.

`xdog.agent` SDK callers can pass `images=tuple[ImageContent, ...]` to `ChildSupervisor.start`
or its steering/follow-up `control` methods. The application child APIs propagate
these same typed inputs. No file URI, remote fetch or implicit text fallback is
introduced. Claw requires the gateway's exact `child_images:1` capability and sends
bytes through the existing checksummed chunk uploads. Large canonical image bodies
are omitted from socket control receipts, explicitly marked `history_omitted`;
use the separately owned child-source reader for complete **display** history,
not raw binary/canonical data. Successful image refs are released; an exact replay
of a cached request returns its old receipt without rerunning or requiring those refs.

Coding's native client uses its local typed child API; there is no Coding frontend
RPC image negotiation/upload path. Claw's existing gateway image refs and exact
admission receipts remain separately owned. Capability withdrawal cancels pending
image jobs, not already admitted effects. No image capability grants tool approval
or parent-editor authority.

Offline evidence includes both actual app/UI owners with file reads and 10 MiB
payloads, the real Claw socket upload path with 2 MiB chunk uploads,
queue settlement/reload, mismatched-receipt draft retention,
and both private-tmux file/preview/review-cancel/terminal-recovery paths. Large test
payloads exercise bounded byte transport with a valid PNG header, not a hosted
pixel decoder or general model-provider interoperability.

Live queued-image return and stopped initial-task/input copies are retained only
through the permissioned main-Agent/SDK path. Native queues are read-only; ordinary
new child image submission and same-session transient child drafts remain separate.
Memory caches are bounded and never automatically adopted by another owner;
only primary drafts have the separate reviewed local snapshot flow. Child editor
inputs remain same-session transient, not cross-process disk recovery. Native desktop
clipboard/provider/decoder interoperability and uncommitted edits retain their
existing limitations.


### Typed stopped-child image recovery through Agent/SDK

`recover_images` remains an explicit SDK/main-Agent queue operation with exact
input `id` and queue `revision`. `ChildSupervisor.control` returns the verified
non-destructive `image_recovery` payload and `queue.image_receipt`; the ordinary
`agent_control` wrapper returns typed images plus receipt/count metadata. The
text-only `recover` action refuses image-bearing inputs rather than dropping bytes.
Running children use the separate owned live-return protocol, not stopped recovery.

The original record and its exact text/images/MIME types remain unchanged on copy.
Owner/revision changes during byte preparation refuse delivery; cancellation waits
for the actual owned decoder/hash worker. Image count, context and retained-byte
limits remain enforced. No child Agent/model is constructed, no old approvals are
restored and no queue input is automatically replayed.

There is no native **u** recovery action, stopped-image copy form or Claw
`child_recovery_begin/chunk/end` UI transport. The native queue view receives only
scoped metadata. Ordinary new child image submission, same-session transient child
editor drafts and primary draft recovery keep their separate contracts.

### Same-session transient child input recovery

**d** in the child roster or `/agents drafts` opens only the current session/
context's unsent child inputs. Enter opens a complete text/settings/provenance/
image-digest review, then a fresh physical Enter reopens the **same editing slot**.
Current text/images, editing settings and cursor survive a view close/reopen;
this is not a prior execution/approval receipt. Existing-child steering/follow-up
reopens only while that exact child is running. No recovery submits work, changes
the parent composer or bypasses the normal final task/access/tool review.

There is no cross-session catalog, duplicate-copy management, search, cache-delete,
disk save/load/delete or child-autosave UI. The former **s**, **x**, **r**, **/**
and **Delete** recovery-management keys are inert. `/agents saved` (including list/ID
variants) refuses immediately without a disk job, command/socket request or background persistence. Existing
`saved-child-drafts` files are neither inspected, migrated nor deleted.

The panel derives entries only from its exact current cache. Opening recovery in
a new current-context/connection scope keeps only that current cache and drops
old references rather than guessing/adopting another scope. A new application owner does not restore transient edits. Preview,
focus/geometry changes, stale source identity and replaced/closed views cannot
reuse old review proof. **p** opens the retained read-only image preview; returning
requires fresh full review. Paste, repeat/release or unpainted Enter cannot confirm.
Text/images stay within existing retention limits; closing a view does not invent
durability or silently truncate data.

This removes only child editor disk/management controls. Actual Agent child input
journals, initial-task records, stopped Agent/SDK `recover_images`/`recover_task` and durable
returned-image backups remain separate explicit recovery paths described above.
Primary `/draft save`/restore, primary autosave, disk receipts and quit safeguards
remain separate and are described below.


### Explicit initial-task recovery after setup failure

A stopped child exposes a distinct `initial-task` row when setup failed before
original task admission. Text and attachments remain available even with no child
history. Pending/admitted/handled/uncertain/discarded state is recorded separately;
a provider failure after admission does not relabel the task as unsent. Legacy or
interrupted records without sufficient evidence are **uncertain**, not proof that
no model/tool effects occurred.

The native queue is read-only and cannot copy/discard that row. The retained
main-Agent/SDK queue actions `recover_task` and `remove_task` require its original
ID/revision. Copy is non-destructive and returns typed image content when needed;
discard commits the record before publishing removal and releases initial image
bytes only after committed discard. Failed writes retain recovery, and cancellation
waits for the actual writer. Neither operation restores approvals, restarts a child
or rewrites canonical history. Ordinary edit/remove/return cannot reinterpret the
initial-task row as live queued input.

Review old history and effects before explicitly starting any replacement task.
No native task-recovery capability or binary copy transport is advertised; existing
SDK journaling and same-session transient editor recovery are not removed.

### Agent-tool-only live queued-image return

The native user interface offers neither manual live queue edit/reorder/remove/
return nor stopped task/input copy controls. Those queue operations remain in the
permissioned main-Agent/SDK backend; native queue inspection is read-only.
The main Agent can use its ordinary permissioned `agent_control` tool with
`action="queue"` and a JSON `text` request containing `return_prepare`,
`return_commit` or `return_cancel` plus the required input/revision/receipt fields.
Preparation returns typed image content and bounded token/receipt metadata; it
never dequeues and does not embed raw base64 into model-visible prose.

The tool supplies its private nonserialized supervisor owner; models cannot pick
one. Cancellation/abort/close revoke that owner. The existing token expiry,
selection/revision/receipt checks and retained-byte limits remain. Durable removal
and return-backup writes keep their existing owned settlement/no-automatic-replay
rules. A generic `xdog.agent` SDK embedder still owns typed-history retention,
persistence and ordinary tool permission hooks. A process-local token is not a promise of
exactly-once side effects across crashes.

### Concurrent return capacity and malformed queue transport

The four-slot live-return budget now includes **in-flight image preparations** as
well as prepared leases/completed receipts. Slots are reserved before asynchronous
validation yields and released on cancellation/failure. Simultaneous requests
cannot each pass the limit and create unbounded image copies. This does not add
crash durability or restore the removed native two-review return workflow.
Preparation/commit ownership remains in the permissioned Agent tool/backend.

Claw's native queue boundary accepts only the exact list request. Retired mutation/
recovery packets and malformed JSON fail without reaching backend mutation or byte
preparation. The retained Agent/SDK queue validator still checks exact action fields;
normal internal JSON-RPC and unrelated child controls are not removed.


### Image worker cancellation settlement

Image recovery/return preparation and sender decoding now wait for their actual
owned worker thread to exit when cancelled. Cancellation revokes delivery: no
receipt or binary frames are published afterward. Repeated cancellation does not
release a preparation capacity slot while decoding/hashing is still running.
Late worker errors are consumed during cancellation; ordinary results/errors are
unchanged. Python cannot forcibly stop a thread, so cancellation settlement may
outlast a transfer deadline while bounded local image work finishes. This is not
process isolation, an OS sandbox or crash durability.


### POSIX child-record directory durability

Completed child-record writes now fsync the temporary file, atomically replace
its destination, then fsync the containing directory. Newly created child storage
directories use private modes and their parent directory entries are also flushed.
Existing directory permissions are not changed. This applies to initial records,
settled results and explicit recovery/discard metadata writes.

A directory-flush failure after replacement is an **uncertain committed outcome**:
XDOG propagates the failure without rolling back or automatically rewriting. Inspect
actual persisted state before retrying. Owned directory descriptors close even on
failure. Non-POSIX platforms retain their existing behavior without an equivalent
directory-fsync claim. These flush-order tests are not physical power-loss testing.
Backend input journals and primary draft snapshots are separate layers,
described below; flushing a child record does not persist a child editor.


### Write-ahead child input snapshots

Both clients now persist a bounded child steering/follow-up snapshot **before**
acknowledging live queue acceptance. Text and images are stored in a private atomic
`.inputs.json` sidecar using the child store's file/directory flush path. Failed
writes refuse live admission. Cancellation waits for the actual admitted writer;
terminal settlement waits too, so an old writer cannot overwrite a later settled
record. No blocking filesystem writes are added to the agent event loop.

After an unclean restart, an interrupted child with a valid snapshot exposes those
inputs as **crash-snapshot / uncertain** recovery rows. Recovery still requires an
explicit permissioned Agent/SDK copy; the native view is metadata-only, and no Agent
is constructed or restarted on load. Snapshots may contain inputs already consumed
before the crash, and may precede subsequent edits/removals. Inspect history/workspace effects before any
explicit resubmission. This is conservative data retention, NOT an exact once-only
consumption journal or a claim that recovered inputs were never executed.

Settled child records take precedence over stale sidecars. The terminal record is
flushed before journal cleanup, so a crash between those steps cannot revive old
accepted inputs. An actual owned SIGKILL test verifies a previously acknowledged
2 MiB image input survives and is explicitly recoverable without agent creation;
both native clients also verify their actual acceptance writes the sidecar.
Backend journals do not persist child editor keystrokes; primary manual snapshots
and optional primary autosave are separate. Physical-power-loss and
uncertain admission/consumption boundaries retain their limits. No automatic
replay or permission escalation is introduced.


### Write-ahead live queue changes

Agent/SDK live **edit**, **remove**, **move** and text-only **return** write their
prospective queue snapshot before acknowledging the change. The runtime validates
the input/revision and delivery lane first, writes off the agent loop, then checks
the exact queue lease again before applying the live mutation. Failed writes leave
the live queue and prior disk snapshot unchanged. Cancellation waits for the actual
writer and does not falsely acknowledge a live change; terminal settlement replaces
the prospective sidecar with actual final state.

An owned SIGKILL test acknowledges an image edit and a separate removal, kills the
process, and verifies recovery retains the edited image but not the removed input.
This covers acknowledged changes, not every ambiguous boundary: if the process dies
after the prospective write but before live mutation/acknowledgment, the saved state
is explicitly uncertain. Consumption during disk work rejects the stale mutation;
there is no automatic replay. Agent-tool-owned image commits and settled
input-consumption persistence are separate stages below, not native live-return UI.
Primary draft snapshots do not remove ambiguous admission or physical-power-loss
boundaries; child editor recovery remains transient.


### Write-ahead image-return commits

The retained backend's two-phase image-return commits persist the removal snapshot
before acknowledgment. Exact owner/token/input/revision/byte-receipt validation
runs before writing and again before actual live removal. Failed writes, cancelled
writers, checkpoint selection or host revocation cannot remove the input through
a stale commit. Writer cancellation waits for actual settlement. Exact completed
commit retries return the original receipt without rewriting the journal.

An owned SIGKILL test acknowledges an image-return commit, kills the process, and
verifies the returned input is absent from the restarted queue snapshot. This does
not make native editor drafts durable; the backend's separate return backup is
described below. A crash between a prospective disk write and live mutation/
acknowledgment is still an uncertain boundary. The separate consumption hook below
is not an atomic history-plus-queue transaction. Recovery never automatically
replays work or restores old execution permissions.


### Durable returned-image handoff

Before a permissioned Agent-tool/SDK live image-return commit removes a queued
input, the child backend persists an exact conservative text/image backup in the
bounded `.returns.json` sidecar, then the prospective queue-removal snapshot.
The same owner/token/input/revision/byte receipt is rechecked before actual removal.
Failed or uncertain writes refuse removal or retain the conservative backup for
settlement. Neither native client prepares or commits a live queue return.

After interruption, validated backups appear as `durable-return / uncertain`
metadata. Terminal settlement writes them into the authoritative child record
before sidecar cleanup. Native inspection cannot copy/discard them. Explicit
main-Agent/SDK `recover_images` returns a non-destructive exact typed copy, while
`remove` is a separate revision-checked durable discard. A backup may exist even if
removal failed; previous effects are not undone. Inspect history/workspace before
explicit resubmission. Startup never reinserts backups into a live queue.

Existing bounds remain 32 backup rows / 40 MiB images, separate from pending-input
retention. Capacity exhaustion refuses new commits rather than dropping bytes.
The main-Agent wrapper emits typed images and bounded receipts; an `xdog.agent`
SDK embedder owns retention and adoption. Native child edits remain same-session transient;
this backend durability is not a child disk-save UI or an exactly-once/power-loss
guarantee.

### Return retry after partial write failure

An explicit same-lease image-return retry now reuses an identical retained backup
instead of adding a duplicate recovery row. This covers a failure after backup
replacement or during the following queue journal write. Conflicting backup identity/
content still refuses; a completed retry does not rewrite or remove twice. This is
not automatic retry permission: inspect uncertain effects/receipts before retrying.


### Awaited child input-consumption persistence

Identified child inputs now trigger an awaited host persistence hook after history
admission or explicit extension handling, before the next model request. The child
host writes its current remaining queue off the agent loop and settles the actual
writer on cancellation. A failed write stops continuation; already admitted history
or handler effects are not rolled back. Other generic Agent clients have no hook
unless explicitly configured with `input_settled` / `set_input_settled`.

An owned SIGKILL test reaches the next model boundary, verifies the consumed input
has been flushed out of the queue snapshot, kills the process, and confirms it is
not offered as queued recovery data. A crash between admission and flush remains
uncertain; this is not an atomic history-plus-queue transaction or exact-once effect
guarantee. Primary manual snapshots and opted-in primary autosave do not promise
that in-flight edits survive or prove physical-power-loss/native-filesystem
conformance; host/platform interoperability retains its own limits.


### Retained primary snapshot storage and quit settlement

The shared snapshot codec/store/I/O/autosave primitives remain for primary drafts.
Private no-follow ownership/mode checks, atomic publication, fsync, create-only
manual saves, exact-owner autosave updates and conservative recovery are unchanged.
A queued receipt remains owned until its worker exits; the terminal never joins a
live disk worker. Lock contention has a bounded three-second acquisition deadline,
not a promise about arbitrary filesystem stalls. Storage failures retain editable
source, redact diagnostics and do not automatically retry uncertain effects.

Graceful quit waits for admitted **primary** writes and pending opted-in primary
autosave. Argument completion, basic dialogs, native rendering/input and Claw's
owned name read remain usable until exit is admitted. A new storage failure cancels
deferred quit. Repeated exit requests do not duplicate teardown. Transient child
edits create no disk/background job or fictitious receipt barrier; no files are
deleted when their current-context cache references are released.


### Primary draft save and explicit recovery

Press **Ctrl+Alt+S** while the primary composer is focused to review a private,
create-only snapshot without consuming its text or attachments. `/draft save`
opens the same exact-composer review; submitting a slash command normally consumes
that command's editor text, so prefer the shortcut for an existing typed prompt.
No autosave opt-in is required. The `save_draft` action is configurable in
`keybindings.json`: `{"save_draft":"f8"}` remaps it and `{"save_draft":[]}` disables
it without a hard-coded fallback. `/keys` shows the current binding.

Save and recovery use a **standalone primary-draft surface**, never the child
browser. `/draft restore [SNAPSHOT_ID]` opens an explicit selector of at most **32**
snapshots or loads one exact ID. `/draft saved [SNAPSHOT_ID]` remains an alias;
`/draft saved list` opens the same bounded selector, not a management catalog.
Each entry discloses its ID and stored action/slot, scope and label; Enter opens
full text/provenance/image-digest review before any restore can be confirmed.
There is no primary catalog/delete/search management UI or automatic latest-slot
selection, pruning or migration. A full store reports an explicit error and keeps
the unsaved composer and existing snapshots unchanged.

Coding uses its session-manager directory's `saved-parent-drafts`; Claw uses its
local data directory's `saved-parent-drafts`. These existing stores can contain
multiple recorded scopes. Stored context strings are provenance, not a durable
workspace/group identity or an instruction to select a snapshot. IDs are not
timestamps; recovery never infers a latest snapshot from ID order or file mtime,
guesses another scope, or silently moves/deletes existing records.

Restoration copies **text/images only** into an unchanged **empty** composer.
Text, attachments, cursor, context and view ownership are checked against the
captured admission state; a nonempty or changed composer is preserved. Old
model/tools/access/configuration are not adopted, and the stored cursor is not
restored: native `set_text` places the editing cursor at the end. Nothing submits
a prompt, invokes a model or changes history/permissions. Primary text supports
64 KiB; snapshots permit four images, 10 MiB each / 40 MiB total. Existing validated
image and combined retained-byte limits remain, with no truncation or partial
recovery. Saved snapshots remain after submit/clear; this UI never deletes them.

### Optional primary autosave

`/draft autosave on` enables session-local primary-composer snapshots. It is **OFF
by default**, resets on restart and discloses PRIVATE PLAINTEXT local storage.
`off` stops future scheduling, not an already admitted write. After the existing
0.5-second debounce, exact-owner updates use only that owner's prior ID/digest;
manual saves stay create-only and externally changed snapshots are not overwritten.
A changed context creates a separate snapshot rather than adopting another owner's
update receipt. Failure stops autosave until explicit re-enable, with no automatic
retry of uncertain effects. Child drafts are not included; child autosave is removed.

Completed autosaves retain only their ID/digest receipt, not another decoded
image/text copy outside retention accounting. Native status distinguishes pending,
writing, committed and uncertain. Committed means a **polled disk receipt**, not
per-keystroke or physical-power-loss durability. Debounce and in-flight crash
windows remain; recovery after restart always requires explicit selection/review.

### Primary review, preview and quit ownership

Every task/provenance/image page must be painted at the current review width before
a fresh physical Enter can admit save/restore. Paste, repeat/release and unpainted
Enter cannot confirm. **p** previews images; **Left/Right** selects an attachment
and **Escape** returns to review. Preview performs no draft action and returning
requires fresh full review. Narrow/zero-budget or changed geometry/focus cannot
reuse earlier consent. Native USER input, shortcuts and approval safety remain.

Reads and confirmations bind the exact primary surface, context and composer.
Closing/replacing it or a focus/owner change revokes late delivery and old callbacks;
returning focus does not revive a former actor. Extension questions, output reviews,
native overlays and Coding approval prompts/waiters retain input priority. A delayed
read cannot reopen a dismissed view or steal modal focus. Escape/cancel releases
transient preview/review images without deleting files or abandoning admitted I/O.
All unrelated composer/queue/editable-image owners remain counted.

Graceful quit waits for admitted primary disk operations and flushes pending
opted-in primary autosave. Child editor recovery creates no disk operation.
Input services stay live while waiting; primary edits during the wait are flushed
when autosave is enabled. A new storage
failure cancels deferred quit and leaves the UI open for inspection. Single-use save
admission, redacted generic errors, private-path/fsync protections and no automatic
replay remain. These are bounded local snapshot/recovery guarantees, not proof of
every filesystem/power-loss behavior. Remote parent-draft control is removed; no
Coding frontend RPC or Claw gateway draft negotiation is used.


### Read-only image preview in child draft reviews

Press **p** in current-session child recovery or its full review to preview images
before a separate reviewed current-session reopen. **Left/Right** chooses attachments;
**Escape** returns to review, with fresh full painting required before confirmation.
Preview performs no draft action, file deletion or agent call. Changed sources
invalidate preview/review rather than applying an action against stale bytes.

### Literal argument suffix, quotes and escapes (UI-035)

Registered completions replace the **entire cursor-local token**, including its
suffix. The callback receives the literal prefix and (when independently
negotiated) copied `argument_context` codepoint coordinates. It can inspect the
suffix as `arguments[cursor:end]` and must return the complete literal replacement
in `value`, including any intended quotes/escapes. No automatic quoting, shell
expansion, suffix concatenation, command execution or prompt submission occurs.

Outside single quotes, a backslash protects the next character, including a
space or quote; an escaped backslash does not protect the following separator.
Inside single quotes backslashes remain literal. An unfinished quote extends the
token to the end of the line. Between arguments, completion can insert into an
empty cursor-local whitespace slot without consuming the next argument. Tab/Enter
inserts only; undo restores the original token and surrounding arguments. Existing
registration/owner/draft/cursor/cancellation fences still apply. Actual Coding and
Claw worker/socket checks and Linux private-terminal suffix/quote/escape/undo and
worker-cancellation recovery have passed; no non-Linux native validation is claimed.

### Explicit MCP arguments

After explicitly connecting a server, prompt arguments are entered as a JSON
object and resource reads use an explicitly supplied resolved URI. Submitted
input stays editable after errors or cancellation. Catalog reads, multitype
previews, insert/export and existing permission boundaries remain; manual named
argument/opaque-resource completion pickers are removed. This does not remove
the lower-level MCP completion client API or extension command argument completion.


### Basic extension interaction scope

Trusted extensions can use `api.input`, `api.confirm` and `api.select` with normal
cancellation, plus bounded notifications and simple text status. Arbitrary custom
renderers, typed plugin forms, nested dialogs, extension overlays/fullscreen frames
and widget layouts are removed. Native approval and recovery reviews remain host-
owned; they expose no plugin layout or editor/session-mutation authority. Core TUI
overlays, transcript fullscreen, Unicode, clipping, cursor and tool-approval behavior
remain.

### Application input and observer ownership

ACP namespace/status now expose the same application receipts as native intake;
there is no adapter-owned inbox/consumer. Endpoint detachment does not stop accepted
work. Core receipt and observer journals are separate private SQLite files under
`data_dir/inputs/` and `data_dir/observers/`. Cursor+outbox commits precede idempotent
ACP handoff. Namespace/attempt fences and unknown-send handling remain fail closed;
there is no old-format migration. The wider optional Goal/shared UI refactor is
tracked in `docs/claw-assistant-core-requirements.json`, not claimed complete here.

None of these controls may abort unrelated active chat or undo started effects.
