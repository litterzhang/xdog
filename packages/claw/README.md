# xdog-claw

**Agent orchestration runtime.**

Runs and supervises long-lived agent sessions: scheduling, a gateway, tool
memory, and persistence backed by SQLite.

## Interactive terminal

`uv run xdog-claw tui` connects to the configured gateway using the main terminal
buffer and native scrollback. It shares Coding's Unicode multiline editor and
bounded layout. Goals/todos summarize retained state; queued messages keep their
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
Linux/private-tmux tests cover the workflow; Windows and desktop editor/IME behavior
remain unverified.

### Steering and queue controls

Enter retains the client-owned FIFO of new full turns. During a ready owned run,
**Alt+S / `/steer TEXT`** delivers at the next SDK checkpoint and
**Alt+F / `/follow-up TEXT`** delivers after active tools/steering finish, within
that SDK run. Neither choice interrupts a running tool or implies rollback.
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
allow 50 pending messages; runtime text is limited to 64 KiB. SDK receipts are
bounded individual frames, not one huge text snapshot. Rejected oversized or
malformed protocol frames do not cancel an unrelated owned run.

### Read-only repository review

**Alt+D / `/diff`** opens the Git repository containing the **gateway group's
workspace**, never a guessed launch directory. This is local inspection through
the authenticated Unix-socket workspace metadata; it does not start an agent
turn or send a stage/revert operation to the gateway. Older gateways without
workspace metadata cannot open it. A workspace change closes/fences the old view.

Up/Down or Left/Right selects files; `/` filters filenames. `s`/`w` selects the
staged/working patch, `n`/`p` navigates hunks, PgUp/PgDn and Home/End page, and
Shift+Left/Right pans long lines. `r` refreshes; `d`/`b` enters an initialized
nested repository/submodule and returns. Escape closes (or first exits filter
mode), restoring the live draft and cursor; it does not abort the active run.

The file list covers staged, unstaged, renamed, deleted, conflicted and untracked
entries. Untracked text is a working preview; binaries, symlinks and non-regular
entries are explicit summaries, not unsafe reads. Patches are disk-paged and
terminal controls sanitized. Git external diff/textconv, pager, fsmonitor and
hooks are disabled. There are no apply/stage/revert/commit APIs, no model calls,
and no index/worktree modifications. Repository snapshots can change during
agent work; refresh explicitly. Temporary patch storage and Git children are
owned/cleaned on close/exit. Linux/tmux evidence and platform limits are recorded
in `docs/agent-ui-gap-plan.md`.

### Gateway-owned session browsing and branch trees

**Alt+R / `/sessions`** opens the group's searchable archived-session browser;
`/tree` shows branches and stable checkpoints. `/` searches, Up/Down/PgUp/PgDn
navigates, Right enters a tree, Left returns, Enter activates, `r` refreshes,
`n` creates a new session, and Escape exits search/closes without changing the
draft. Shortcuts are configurable (`sessions`, `tree`); tree is unbound by default.

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
active turn. A changed active session requires explicit `/sessions` refresh/resume;
failed, withdrawn, canceled or disconnected reads do not replay or fall back to
cached metadata. The reply is an owner-bound snapshot, not a continuous name watch.

`/resume ID`, `/new`, `/fork [INDEX]`, `/branch [SESSION] ID`, and `/goto INDEX`
are direct controls with one-based checkpoints. Updated gateways advertise
session-control version 1; older/offline gateways receive no unsupported operation.
Changes require an idle **group**, not just an idle client, and an expected active
session ID. The gateway retains the per-group lock while durable work settles,
scopes IDs/catalogs to the group, and refuses stale/foreign/invalid selections.

A durable catalog retains archived sessions instead of losing their metadata on
reset. Older orphan JSONL archives are recovered only inside an explicitly
owned group directory, never guessed across shared stores. Branch parentage and
return checkpoints are retained, and safe checkpoint selection cannot split tool
batches. Explicit resume is recorded so an old session does not immediately
trigger another daily reset. Group workspace/model policy remains group-owned.

After mutation, correlated metadata and bounded history chunks rebuild the
visible context/editor recall without clearing native scrollback or the draft.
The client waits for history completion before sending. Unexpected active-session
changes require explicit refresh/resume; version-1 sends carry a session lease so
a stale view cannot silently address another conversation. Uncertain operations
are not replayed on reconnect: inspect gateway state before retrying. Navigation
uses no model calls; metadata I/O is off the event loop. Linux/tmux is tested;
Windows and desktop/IME behavior remain unverified.

### Image drafts and clipboard inputs

`/attach PATH` attaches a local PNG/JPEG/WebP file to the editable draft;
`/attachments` lists IDs/metadata, with `preview ID`, `remove ID`, and `clear`
subcommands. Quote paths containing spaces. Relative paths use the advertised
workspace when available, otherwise the explicit local client's cwd. Preview
owns keyboard/paste and Escape returns to the draft; it never submits an image.
Enter also supports image-only input.

**Ctrl+V / `/paste`** explicitly invokes local clipboard helpers off-thread:
Wayland `wl-paste`, X11 `xclip`, or the macOS `osascript`/AppKit image/text adapter
below (`pbpaste` is text-only fallback if the native helper is absent). No implicit
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
clipboard/Kitty/iTerm, macOS image paste and Windows are not claimed verified.

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
verified; desktop terminals, Windows and IME remain unverified.


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
behavior is verified; desktop/macOS/Windows validation remains separate.


### Copy, private export and explicit local sharing

**Alt+C / `/copy [response|selection|entry|conversation|all]`** copies a captured
source snapshot, defaulting to the latest assistant response. A marked browser
selection is used by Alt+C when present. In the transcript browser, **y** copies
selected lines or the current entry; **e** chooses a local export destination and
**s** reviews a local share artifact (selection if marked, otherwise conversation).
These gestures never submit a prompt or execute a tool. Clipboard helpers are
`wl-copy` on Wayland, `xclip` on X11, and `pbcopy` on macOS. No helper means an
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
clipboard ownership, macOS and Windows manual validation remain open. Private
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
runtime shutdown/disconnect command. Tools enter that group's one SDK runtime.
External execution defaults to **blocked** even after discovery; explicitly set
`allow_tools: true` for a trusted server to authorize unattended tool use. This can
permit filesystem/network effects outside built-in tools; permission gates are not
an OS sandbox. Server read-only annotations do not override host policy. Command
trust and tool-use authorization are separate, and configured env values are not
printed in status. OAuth and further interactive capabilities remain unfinished
in the goal tracker. Stdio negotiation/validation/cancellation limits are documented
in the Agent README.

Group MCP configuration also accepts `transport: http`, an explicit HTTPS `url`,
and optional `headers`/`headers_env` credential mappings. `/mcp connect SERVER`
uses the same group owner and idle reservation as stdio, with unchanged explicit
`allow_tools` policy. Current stateless and legacy Streamable HTTP JSON/SSE, header
mirrors/session lifecycle and bounded no-replay behavior are shared with Coding.
Authorization failures and redirects never trigger automatic credentials, downgrade
or forwarding. See the Agent README for limits. OAuth, richer schemas and further negotiated MCP capabilities remain unfinished.


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

Remaining MCP scope includes interactive authorization/OAuth, negotiated
completion, subscriptions and richer server/tool schema
inspection. Binary/image resources are exportable, not directly attached to model
input. Real third-party endpoints and additional platforms remain unverified.


Legacy HTTP MCP tools now support bounded cursor-based response recovery: an
interrupted SSE response can be retrieved with GET/`Last-Event-ID`, retaining the
original request identity and timeout. No second tool POST, automatic approval or
connection replay occurs. Concurrent calls keep separate cursors; cancellation,
auth/redirect/session errors and reset/exhausted cursors fail without replay.
Current stateless HTTP never sends recovery GETs. See the Agent README for limits.


### Session planning workflow

Use `/plan start OBJECTIVE` to enter **read-only planning**, then send a normal
request asking the agent to build/refine the plan. The session-owned `update_plan`
tool maintains structured pending/in-progress/completed steps with revision checks;
it cannot change mode or authorize execution. `/plan` opens the focused inspector.
Arrows select and reveal steps, pages scroll long text, **r** returns to planning,
**e** reviews the execution transition, **s** reviews stopping the plan, and Escape
closes the inspector without disabling planning. The prompt shows a mode badge.

Execution requires an idle owner and a current plan revision. The inspector's
Enter confirmation works only after a visible confirmation has been rendered;
resize, busy refresh or revision changes invalidate it. Direct user commands are
`/plan execute REVISION`, `/plan revise [REVISION]` and `/plan stop [REVISION]`.
These commands only change mode: **no model turn or work is automatically started**.
Send a normal message to proceed under the host's existing tool policy.

Planning blocks shell, workspace mutations, external MCP and unknown tools in the
host pre-tool hook. Read-only filesystem calls still follow the normal policy.
Structured plan updates and automatic session/compaction persistence are managed
application metadata, not permission to modify project files. This is a tool gate,
not an OS sandbox. Durable plan state survives session reloads and branch snapshots;
invalid saved state fails rather than silently removing restrictions. Plans are
captured at branch creation, not reconstructed for every older message checkpoint.

Claw uses group-owned correlated controls with session/revision checks and idle
reservations. Its normal MCP `allow_tools` policy remains in force after execution
is selected. Old gateways disable plan controls rather than receiving unknown
operations; uncertain controls are never replayed on reconnect.

Metadata writes are serialized, committed before publication and settled during
cancellation before ownership is released; cancellation cannot undo an already
committed metadata update. Plan changes are idle-only, preserve drafts, and never
bypass approval/output-review focus. Claw's active plan also suppresses automatic
idle/daily session replacement until it is stopped or explicitly navigated away.
Its silent compaction tools retain host policy gates while managed durable-memory
flushes remain available. Other clients' plan changes are refreshed by opening the
inspector; backend revisions reject stale transitions. Desktop/platform validation
remains limited to the Linux/private-tmux evidence in the goal tracker.

Claw plan state has independent atomic session/branch sidecars, cached at runtime
startup. Transcript-catalog recovery cannot silently clear planning mode. Invalid
or corrupt plan state fails closed and is reported in gateway health without a
reconnect loop; repair it or explicitly select a new session. Invalid initial plan
commands do not implicitly create a session. Sidecars are local application state,
not tamper-proof or OS-enforced isolation.

Mode controls do not pause Claw's scheduler or other input channels. They enqueue
no new agent turn themselves, but already queued/scheduled work can continue under
the selected mode and the normal host policy.


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
    api.widget("summary", ["Declarative widget text"])
```

Tools get collision-safe `ext_...` aliases; slash commands are namespaced as
`/ext-sample-hello ARGS`. The live completion provider reflects current commands.
`/extensions [list|reload NAME|unload NAME]` manages ONLY globally trusted IDs and
requires idle ownership. Commands do not silently submit an agent prompt. API v1
supports sync/async callbacks, session/turn/message/tool lifecycle events, pre-tool
veto/parameter transforms, post-tool text transforms and bounded notices/status/
widgets. Legacy Python pre/post/stop hook types remain compatible.

Rewritten tool arguments are schema-validated and checked by the host's ORIGINAL
permission and planning gates AFTER transformation. A plugin cannot use the API
to grant tool approval/change planning mode, or mask a denied/failed call as success.
UI text is sanitized and row-bounded; direct plugin prints never paint the host
terminal. Timed-out/failed/canceled callbacks close their owned process, with no
replay. An unavailable selected extension makes pre-tool validation fail closed
until explicit unload/reload. Shutdown runs bounded stop hooks and reaps normal
owned process groups; cancellation cannot roll back already completed effects.

This is a first runtime slice, NOT full Pi API compatibility: rich custom dialogs/
renderers, shortcuts/input interception, provider registration remain on the goal tracker; durable entries/context hooks
are described below. Binary tool return
values and tools called through extension command context are not yet supported.
Code can deliberately escape normal process ownership; do not load untrusted code.

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
retains it. Session/new/branch activation follows the selected durable snapshot,
not process globals. Old owners cannot publish entry writes after navigation.
Close invalidates late writes, so shutdown callbacks should not append entries.

Register `api.context(callback)` to transform the ephemeral message list before
EACH model invocation. The callback receives serialized typed user/assistant/tool
content plus its entry snapshot and returns a message list. It may inject summaries
or filter complete history batches, but does not edit the canonical transcript,
system prompt, static cache prefix, planning mode or tool permissions. Coding also
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
parts survive chunked full-context transfers beyond the individual RPC frame limit.
Transfers are checksummed, order/identity fenced and spooled off-loop, with a 128 MiB
explicit context limit, 64 KiB blocks and an absolute whole-transfer deadline.

Entry capacity is 128 entries across a session, 16 KiB per entry and 512 KiB total.
Invalid/corrupt stored state fails instead of quietly resetting it. Persistence
failure keeps the previous snapshot. Cross-loop cancellation settles committed
metadata/file ownership before the host releases its lifecycle reservation; it
cannot roll back an already completed write. Context-hook failures disable the
extension and do not trigger another model call or automatic replay.

This closes the basic durable-entry/pre-model-context gap, not the whole extension
API row: custom renderers/dialogs, shortcuts/input interception, provider registration,
richer command context and binary tool results remain unfinished. Extension code
is still trusted code with the user's privileges, not an OS sandbox.

Claw caches independent atomic extension-entry session/branch sidecars at startup;
transcript-catalog recovery cannot discard them. Branching from a selected branch
inherits its entries rather than the current head. Invalid sidecars are explicit
errors, not empty state. Session-owned workers are recreated for branch activation.


### Interactive extension dialogs

Active extension callbacks now support `await api.select(title, choices)`,
`await api.input(title, message="", default="")` and
`await api.confirm(title, message)`. Optional `timeout` is bounded to 0.1–600 s.
Select/input return `None` on cancel/no UI; confirm returns `False`. Cancel answers
this dialog only—it is not tool approval, permission revocation or automatic turn
submission. Trusted extension code decides what to do with the answer.

The worker suspends the callback and returns an owned pending-callback/dialog
receipt. The host displays a focused bounded form, then resumes that SAME callback
with the validated answer; it does not rerun a command/tool to obtain the answer.
Multiple sequential dialogs work. Without an interactive host, canceled values are
returned immediately rather than hanging unattended execution.

Forms sanitize control codes, scroll long details, preserve the main editor draft
and input cursor, and require a rendered visible question/action before Enter can
answer. Select/confirm pastes do not submit; tiny/resize/release/repeat/unpainted
selection input cannot confirm. Escape/Ctrl+C cancel the dialog. Tool approval and
output review retain higher focus priority. An affirmative extension confirmation
never grants the underlying agent tool permission or switches planning mode.

Dialog IDs are host-generated and scoped to their session/request/run/connection.
Timeout, abort, navigation, disconnect and shutdown settle/cancel pending futures;
late/foreign answers cannot resume a successor's callback. Claw command waits are
connection-owned background jobs so the socket can still process UI replies and
other controls while the group remains reserved. Context-hook dialogs still share
the context transfer's absolute deadline; the UI does not renew model/transport
budgets. Callback effects remain trusted code, not OS-enforced isolation.

Arbitrary custom renderers/overlays, richer forms, input/shortcut interception,
provider registration and further extension command/binary capabilities remain
unfinished; these dialogs do not declare full Pi API compatibility.


### Supervised child agents

The TUI supports `/agents start TASK` (read-only), `/agents start --inherit TASK`,
`/agents list` and `inspect/steer/follow-up/cancel/wait/close ID` controls (steer and
follow-up take trailing text). Parents expose `agent_spawn`/`agent_control` tools;
children cannot recursively delegate or borrow the parent's `update_plan` owner.

Children share their group's workspace, model/options and selected tools. Inherited
access cannot exceed current parent planning restrictions or configured MCP/extension
allow-tools gates. Independent child extension workers/entries/tool executors do not
borrow the parent's stateful tool proxies; parent validation/context hooks still
retain authority. Read-only children permit only known read-only filesystem calls
and current time. User starts require idle group ownership, while inspect/cancel/
wait controls remain reader-responsive even if the parent waits for a child.

Bounds are three active children, 16 retained records, 120-second execution deadline,
16 model calls, 32,000 reported tokens and bounded transcript/input retention. Token
usage is a stop threshold, not exact provider billing; cleanup/persistence can extend
past the execution deadline. Socket jobs are bounded and start replay receipts prevent
duplicate child work on one connection. Expected-session checks fence stale controls;
active children prevent navigation/schema changes and automatic daily/idle resets.
Daemon shutdown settles child owners before shared extension/MCP resources close.

Session/branch child sidecars persist final transcripts/results and isolated extension
entries; interrupted work is never restarted automatically. Closing archives records
while freeing the retained slot. Corrupt catalogs disable delegation with an explicit
repair diagnostic, not the parent UI. Group/channel/memory/goal/skill services execute on their original host
loop; shared parent validation stays serial and cannot borrow a parent dialog lease.
`/agents` opens a live roster and focused complete-source browser/search/selection,
with visible child-specific control forms, opt-in fullscreen and scoped reviewed
copy/export/share. Child source has its own checksummed/coalescing 64 KiB transport
and a 2 MiB display-source limit; controls remain socket-responsive during reads.
Escape restores the roster and parent draft/caret. Richer child dialogs,
platform validation remains pending. Child extension dialogs now use independently owned select/input/confirm continuations when the origin UI is attached.
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
is NOT tool approval or a planning transition. Input/select cancel returns None;
confirm cancel returns False. Detaching/exiting the origin UI settles pending
questions and subsequent child questions use canceled values; callbacks/work are
not replayed or automatically adopted after reconnect. Parent inherited validation
still uses silent no-UI answers to avoid borrowing its separate dialog reservation.
Arbitrary richer extension forms and platform validation remain open.


### Child specialization and limits

`/agents start --label Reviewer --instructions "Inspect only" --tools filesystem,current_time
--fresh --timeout 60 --calls 4 --tokens 8000 -- TASK` specializes a child without
changing parent permissions. Options precede the task; `--` preserves a task that
starts with flags. Plain tasks preserve quotes/newlines. `--tools none` gives the
child no executable tools; otherwise selected names must exist in its inherited tool
set and cannot include delegation/parent-plan metadata tools. Unknown/malformed flags
fail visibly. Instructions append to, rather than replace, parent restrictions.

In `/agents` press `n`, then Ctrl+G for settings JSON. Supported keys are `label`,
`instructions`, `tools` (array or null for inherited tools), `inherit_context`,
`timeout`, `max_model_calls` and `max_tokens`. Enter applies the settings draft only;
start review requires every page to be painted before confirmation. Resize invalidates
that review. This confirmation does NOT approve tool use. Effective settings persist
with child results and appear in complete child source. Request/token caps may be
lowered, not raised above 16 / 32,000; token usage is not exact billable accounting.
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
Selected capabilities/window/output limits apply, while parent/child planning,
approval/external-tool gates remain unchanged. Same-model aliases preserve explicit
host native-tool overrides. Switching to a nonreasoning model disables inherited
reasoning; unsupported positive choices fail visibly. Model switches omit inherited/
transformed provider continuity and old reasoning from the CHILD copy, leaving parent
provenance/history intact. Transformed images are validated; oversized switched-model
contexts fail a conservative byte-based preflight with fresh/lower-output/larger-model
guidance, not automatic summarization or exact token accounting. Parent model/options/
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
        "inherit_context": false,
        "max_model_calls": 4,
        "max_tokens": 8000
      }
    }
  }
}
```

Browse `/agents presets`, or use `/agents start --preset reviewer --tokens 6000 --
TASK`. Explicit flags override template fields. Ctrl+P selects a template in a new
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
servers and `c` to connect explicitly. Server-list `s` inspects the authorization;
`f` reviews local forget/disconnect (not remote revocation). Existing tool approvals,
Claw allow_tools/planning and child/model rules remain active. Parent drafts/caret and
terminal modes survive. Tokens remain memory-only with no automatic refresh/replay.

Consent is scoped to view/session/context and, in Claw, its original socket/group.
Another connection cannot use a known receipt. Leaving, canceling, disconnecting or
changing the scope rejects late credential commitment and settles pending listeners;
reconnect does not adopt a waiting flow. Already completed resource credentials may
survive closing the success view. Claw requires advertised `mcp_auth` controls; schema
changes/forget still require idle ownership. Pending HTTP jobs do not block socket
reads. The constrained pre-registered profile from pass 27 remains: broader metadata/
registration, persistence/refresh and full external-provider/platform verification
are not claimed complete.


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
leases/explicit connect remain separate. Registration and persistence/refresh are
still pending; successful offline discovery is not proof of every external provider.


### Client registration alternatives

OAuth profiles accept `registration` with an explicit strategy:

- `pre-registered` (default): existing public client_id behavior.
- `metadata`: client_id is the operator's hosted HTTPS JSON document URL (with a
  path), and callback_port is fixed/nonzero. The issuer must advertise metadata
  support; the document's client_id/name/public method/code types and exact
  `http://127.0.0.1:PORT/callback` registration are validated without linked-asset
  fetching, redirects or implicit document hosting. No hidden DCR fallback occurs.
- `dynamic`: omit client_id. The issuer must advertise a pinned-origin HTTPS
  registration endpoint. The full review explicitly describes creating a remote
  native public-client record. Only AFTER consent does one JSON POST register the
  exact allocated callback/code/public method/scopes; a validated issued ID is used
  for the normal resource-bound PKCE flow. No secret/management token is stored/sent.

Example dynamic profile (legacy/deprecated registration is explicitly selected):

```json
{
  "issuer": "https://issuer.example",
  "registration": "dynamic",
  "client_name": "My XDOG CLI",
  "scopes": ["tools:read"]
}
```

Cancel cannot undo a remote record. Unknown outcome/invalid response consumes the
receipt, closes the listener and never replays registration; fresh review is required.
Client metadata requires operator-hosted reachable data and the supported fixed
callback, not automatic hosting or general port-variance compatibility. Same Coding/
Claw consent, lease, status/local forget, explicit connection and tool-policy gates
apply. Claw negotiates mcp_registration capability. Credential/registration persistence,
refresh, confidential client methods and remote management remain pending; offline
peer tests are not a claim that every issuer supports these profiles.


### Pre-registered confidential token methods

An OAuth profile can explicitly choose `token_auth_method: client_secret_basic` or
`client_secret_post` and `client_secret_env: MY_MCP_CLIENT_SECRET`. Default none
remains public. These methods require pre-registered client_id; metadata/DCR profiles
cannot silently adopt secrets. Literal client_secret/config-file credentials and
unimplemented methods fail rather than downgrade. Issuer method metadata must match.

The full consent shows method and environment variable NAME only. Secret values are
resolved solely from the configured source, checked before discovery/start and anew
at exchange; they are not stored on profiles/reviews/status/browser URLs or in files.
Basic correctly form-encodes both credential components before base64; POST uses its
form body, never both. Credential-bearing data is restricted to the exact reviewed
pinned HTTPS token endpoint—not resource/metadata/registration hosts. Errors remain
secret-safe. Missing/unsafe/removed sources fail closed, with no code/request replay.

Existing PKCE/resource/scope, Coding approvals, Claw allow_tools/planning and view/
connection leases remain authoritative. Claw negotiates mcp_client_auth and verifies
reviewed method/source fields before confidential start. Authorization is not tool
approval or implicit connection. Persistence/refresh, private-key/mTLS methods and
full service/platform interoperability remain future work.

### Explicit in-memory OAuth refresh

Refresh is an operator action, never a background token retry. After an issuer
actually supplies a refresh token, open the server's authorization status and press
**r**. Review every page of the resource, actual granted scopes, pinned endpoints,
client ID and authentication method/source, then press **Enter**. This sends one
resource-bound refresh POST, not a new browser login or client registration. Claw
requires gateway capability `mcp_refresh: 1`; unsupported gateways cannot submit it.

The SDK equivalents are `manage_auth("remote", "refresh_prepare")`, followed by
`manage_auth("remote", "refresh", review_id)` under the same live consent lease.
Receipts are single-use and expire after five minutes. The request uses the actual
prior grant's scopes, not the broader original configuration. Valid replies may
narrow scopes but cannot widen them. Issued refresh tokens rotate; if a valid reply
omits rotation, the prior token is retained only after that reply is validated.

The old refresh capability is consumed before exchange. Cancellation, a lost
response, invalid response or revoked owner discards it: uncertain rotation must
not replay the old token. **c** cancels a pending refresh; closing/disconnecting the
view settles its task and fences late commits. A still-valid old access token can
remain usable on refresh failure; authorize again to restore refresh capability.
Failed refresh preparation without an existing lease does not reserve the server
against another view. Local forget erases both access and refresh credentials.

Refresh never replays MCP requests, automatically connects tools, changes approvals,
registers a client or widens permissions. Credentials remain memory-only: durable
credential/registration storage and reload, further client methods and remote
revocation/management remain open. Issuers may not issue refresh tokens (the dynamic
registration request still declares the code grant only). Offline peer/terminal
coverage is not evidence of live identity-provider compatibility. This section
supersedes earlier descriptions of explicit refresh as entirely unimplemented;
automatic refresh and persistence are still not supported.

### macOS image/text clipboard adapter

Explicit **Ctrl+V** or `/paste` on macOS now selects one owned `osascript` JavaScript
for Automation helper using AppKit/Foundation. It reads PNG, JPEG or WebP binary
pasteboard types before text. TIFF-only image data is converted to PNG through
`NSBitmapImageRep`; clipboard file URLs are not opened and no application is
controlled. Unicode text and an empty clipboard use the same snapshot protocol.

The helper checks the pasteboard change count before/after the read and refuses a
changed snapshot. Source/converted images remain capped at 10 MiB, TIFF conversion
at 100 megapixels, and text at 1 MiB. Python bounds the encoded response, validates
its JSON/base64 protocol, then reuses the usual image-header/dimension and composer
admission checks. Existing off-thread timeout/cancel/reap and draft/epoch fencing
remain in force. A native error is not retried as text or silently switched to a
different clipboard backend; it never automatically sends input or grants approval.
When `osascript` is absent, `pbpaste` remains explicitly text-only fallback.

Evidence here is independent scripted AppKit-contract execution and real owned
helper subprocesses on Linux, plus both production terminal clients' Ctrl+V,
preview, image send/recovery and terminal restoration with that helper protocol.
The actual Apple JXA bridge, a real Mac pasteboard/desktop (including pasteboard
access denial), Kitty/iTerm and Windows still require platform validation. This is
an implemented adapter, not a claim that those native-platform gaps are verified.

### Explicit MCP named-argument completion

After explicitly connecting a server, open its prompt or resource-template catalog
and select an entry. In the JSON-argument/resolved-URI form, **Ctrl+Space** opens
named completion. **Ctrl+N/P** selects an advertised variable, **Tab** explicitly
requests suggestions for its current prefix, and **Up/Down** selects a returned
value. **Enter** inserts the visible selection (or typed value) into the editable
argument/URI draft only. A separate Enter in the parent form retrieves a preview;
preview insertion into the main prompt remains another explicit action. Escape
returns without retrieval or agent input. No completion runs on ordinary typing.

Requests require the server's negotiated `completions` capability, an advertised
prompt/template and named variables. They carry other edited arguments as context,
not as reserved top-level fields. Responses retain server ranking, at most 100
bounded strings, optional total count and hasMore; additional results are indicated
rather than silently truncated or automatically paginated. Narrow the prefix and
request again. Editing/changing variables cancels stale work and discards old
suggestions; scope/context/prefix echoes and existing session/view/connection leases
fence late replies. Claw requires `mcp_completion: 1` from the gateway.

Accepted variable edits used as context are retained with the selected value.
Template insertion expands RFC 6570 **scalar strings** (all operators, prefix and
scalar explode modifiers); other undefined variables are omitted as specified and
the resolved URI stays editable. Arrays/associative expansion are not accepted by
this named-string form. It never opens a returned URI or clipboard/local path:
resource preview uses the already connected MCP server only. Invalid JSON/URI
input and busy submission preserve the draft instead of consuming it on failure.

Completion is read-only data, not authorization or a tool call. No automatic
connection, token refresh, MCP call retry, model turn, permission widening or
resource subscription is introduced. Repeat Enter cannot implicitly retrieve a
preview after a suggestion is accepted. One active and one queued explicit intent per
server are allowed; excess concurrency fails visibly. A short cancellable interval
limits requests without losing fast explicit edits or retrying a POST. Results use
Claw's existing bounded checksummed source transport, not oversized control frames.

Offline current/legacy stdio and loopback HTTP peers, both live app paths and both
private-tmux terminal forms are covered. This does not establish third-party server
or non-Linux platform interoperability. Resource subscriptions, richer negotiated
client/schema capabilities, credential persistence and remaining extension/platform
work stay open; earlier notes listing completion as entirely absent are superseded.
The configured completion timeout includes queue wait, catalog and POST.
Opaque/plain-resource completion references outside advertised named templates
remain open rather than being inferred from the named-template implementation.

### Opaque resource-reference completion

Use **v** on a configured server or a resource-catalog entry to open the opaque
completion form. The selected URI is prefilled only when there is an actual catalog
entry; otherwise enter it explicitly. Supply the server-documented argument name,
current value and context as JSON, for example:

```json
{"uri":"opaque://known/ref","argument":"documented-name","value":"pre","context":{"language":"python"}}
```

No argument name is guessed from a URI, description or local file path. **Ctrl+Space**
opens the named-value picker; **Tab** explicitly requests `ref/resource` completion.
The same bounded ranking, context, cancellation, timeout and late-result fencing
apply as for prompt/template completion. Unlisted/dynamic resource references can
be completed, and completion-only servers can connect without pretending to expose
tools, prompt catalogs or resource catalogs. The server decides the meaning of the
explicit reference/name/context; this client never follows the URI.

**Enter** accepts into the JSON argument draft only. A separate Enter reviews the
selected/edited data **locally**, labelled "Operator-selected/edited" and "NOT a
resource read or resolved URI". **i** in that preview explicitly inserts the quoted
untrusted data into the preserved main draft; it still does not send a model turn.
An opaque suggestion is an argument value, not an inferred replacement URI:
there is no `resources/read`, template expansion, local path opening or implicit
connection/authorization. JSON forms now use up to three bounded editor rows so
wrapped reference/value/context fields remain inspectable; invalid forms preserve
their text rather than consuming it.

Claw additionally negotiates `mcp_resource_completion: 1`; missing support fails
before a remote request and leaves the prefix editable. This separate capability
preserves existing `mcp_completion: 1` prompt/template behavior. The selected-data
review is local in both clients, not a new gateway resource-read operation.

Independent current/legacy stdio and HTTP peers (including completion-only profiles),
both real app paths and private-tmux terminal forms verify this behavior. Invalid
completion-only capability metadata fails closed. No live third-party server,
identity-provider or non-Linux platform compatibility is inferred. This section
supersedes earlier notes that opaque/plain-resource completion was unimplemented;
subscriptions, richer schemas/client capabilities, persistence and the other
original gaps remain open.

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
permission/planning/allow_tools rules still decide execution; a schema/readOnly hint
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

Bounded JSON tool details now survive save/reload/branches in both clients, preserving
typed structured output and null-presence flags. Live/non-JSON handles stay transient.
Transcripts may contain sensitive structured values: keep them private. Actual current/
legacy peers, both app permission paths and Linux/private-tmux inspectors are verified;
provider-specific full-schema acceptance and mixed-dialect/native-platform
interoperability are not inferred. Format-assertion/custom required vocabularies,
ambiguous HTTP header mappings through schema composition, richer content/client
capabilities, subscriptions, credential persistence and extension/platform gaps remain
open. Earlier object-only/current structured-result limits are superseded by this pass.

Schema packaging includes a direct `referencing` dependency and the portable SDK
bundle dependency list. The helper resolves its own trusted library root, so vendored
bundles work without accepting project/PYTHONPATH shadow modules. An actual vendored
helper test covers this; generated-output/vendored-package licence boundaries are
unchanged. Lock updates only declare the already-resolved dependency, not unrelated
version upgrades.

### Reviewed typed extension forms

Trusted Python extensions can now use `await api.form(title, fields, message="...",
timeout=300)` in their owned command/tool callbacks. It returns a typed named
object, or **None** on cancel, timeout, headless/unattended execution or unsupported
viewer. Defaults are offered to a live user, never silently submitted headlessly.
Example:

```python
answer = await api.form("Configure task", [
    {"name": "name", "kind": "text", "required": True, "min_length": 1},
    {"name": "count", "kind": "integer", "default": 0, "minimum": 0, "maximum": 10},
    {"name": "enabled", "kind": "boolean", "default": False},
    {"name": "mode", "kind": "choice", "default": None,
     "choices": [{"label": "None", "value": None}, {"label": "Zero", "value": 0}]},
    {"name": "notes", "kind": "text", "multiline": True},
])
```

Fields support text, integer, finite number, boolean and labelled typed scalar
choices; required/default, descriptions, text length and numeric ranges are
validated independently by the host. Unknown constraints/field names, duplicate
names/typed choices and coercions fail. **False**, **0**, empty text, explicit null
choices and omitted optional fields remain distinct. Required means a field must
be present; use min_length to require nonempty text. Forms are flat/bounded (1–32
fields, 128 KiB declaration and 64 KiB answer), not arbitrary code/custom rendering
or nested JSON Schema UI. Integers are limited to the interoperable ±(2^53−1) range.

**Tab/Shift+Tab** commits/navigates fields. Text/numbers edit normally; **Space or
Left/Right** cycles boolean/choice values and unset. **Enter** adds a line in a
multiline field, otherwise opens full-form review; **Ctrl+Enter** reviews from any
field. **PgUp/PgDn** pages help/review. Every review page must paint before Enter
submits; **Ctrl+E** returns to editing and **Esc/Ctrl+C** cancels. Invalid drafts stay
editable, and resize/visibility changes invalidate prior review. Field help, limits,
requiredness and selected/omitted values remain inspectable in bounded rows.

Forms are extension input data, never host tool approval, planning/permission-mode
changes or automatic agent submissions. Coding approvals retain priority. Parent
and child callbacks use their existing session/connection/UI leases and independent
brokers; malformed/foreign answers cannot resolve a form. Cancel, disconnect and
unattended paths do not replay the callback or invent defaults.

Claw negotiates `extension_forms: 1` in the client handshake and server pong. Older
viewers receive cancellation rather than an unrenderable form; basic select/input/
confirm compatibility is unchanged. Both live parent/child paths and Linux/private-
tmux command forms are tested. Arbitrary renderers/nested layouts, interception,
provider registration, richer extension commands and native-platform validation
remain open; this pass does not waive them. No OS sandbox is added.

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

Coding approvals, Claw extension_allow_tools, planning and child inherited permission
restrictions still gate execution. Text-only post-hooks do not replace or drop a
result containing images. Legacy text callbacks/post-hooks retain their behavior.
Both UIs display tool images without printing base64. Claw now forwards final tool
image parts (previously missing from its display events), with a bounded live image
budget. Larger generic tool images are visibly marked as omitted from the live
preview; original data stays in canonical model/transcript history, not in an
oversized socket line. The new rich-extension envelope fits that transport budget.

Independent real workers, denied/allowed live app paths, typed persistence, inherited
child output, malformed/unsupported results, large-preview notices and both private-
tmux image/JSON inspectors are verified. Rich command contexts/returns, arbitrary
renderers, input interception/provider registration, audio/resource/error-image
interop, remaining MCP/credential and native-platform gaps stay open. This section
supersedes earlier text-only TOOL limits; it does not waive the remaining goal.

### Rich extension command artifacts

Extension command callbacks now accept the same typed text/image/structured JSON/
details result builder as tool callbacks. Legacy string commands remain strings.
The SDK `runtime.command()` returns either str or a frozen `ExtensionCommandResult`
with extension/command identity, a validated result snapshot and safe summary.
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
or change planning/permission modes. The prior draft/focus and normal approval
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
inspectors and parent terminal restoration. Rich command HOST ACTION CONTEXT (safe
session/navigation/editor callbacks), arbitrary rendering/layouts, interception,
provider registration, binary artifact export/persistence and remaining MCP/platform
gaps stay open. Returning an artifact does not waive those requirements.

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
execute stored command names/text/URIs, change permissions/planning or send input to
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
SDK artifact_bytes/artifact_from_bytes/read_artifact expose the same validated codec.
Remaining custom rendering/command host actions/interception/providers, further
MCP/credential and native-platform requirements remain open.

### Command-only editor host context

Trusted command callbacks can now use `snapshot = await api.get_editor()` and
`await api.set_editor(new_text, snapshot["revision"])`. The captured object contains
text, character cursor, image count and an opaque revision receipt. A replacement
returns bool; conflict/unavailable contexts return False/None without overwriting
the user's draft. Example:

```python
snapshot = await api.get_editor()
if snapshot is not None:
    applied = await api.set_editor(snapshot["text"] + "\nPrepared by command", snapshot["revision"])
```

This capability exists only during an actual interactive extension COMMAND, under
that extension's current command/session/connection owner. It is unavailable to
ordinary tool/lifecycle/context/pre-/post-hook callbacks, inherited child tools,
unattended/headless callers and unsupported viewers. A forged worker flag cannot
borrow another operation's editor context; the host checks the original RPC action
and current UI command/extension ownership independently.

Revision receipts are random, single-use and bound to the original text, cursor and
attachment identities. User edits/caret moves/attachment changes, new commands or
changed session/connection/history epoch invalidate replacement. Invalid/terminal-
control/oversized text is rejected rather than silently sanitized into a different
draft. Text replacement preserves typed attachments and normal undo behavior; it
never sends a model turn, changes permissions/planning or executes draft text/URIs.
Limit: 64 KiB UTF-8 editor snapshot/replacement and explicit bounded request timeout.

Coding's approval/output/overlay priority defers host editor service rather than
changing the draft behind another dialog. The existing owned pending callback and
validated UI-reply path carries editor requests/receipts; it is not a second agent
loop. Cancel/exit/owner changes settle or refuse requests, and callbacks are not
replayed. Claw independently negotiates `extension_editor: 1` in hello/pong; older
viewers cancel the request and leave command/editor behavior unchanged.

Both real command workers, UI CAS-success/conflict cases (including retained Coding
attachments), wrong owner/receipt/cursor/image changes, invalid control data,
headless/tool/older-viewer refusal and private-tmux typed draft transforms are tested.
Session/navigation/model host-action APIs, broader command context, custom rendering,
interception/provider registration and remaining MCP/native-platform goals stay open.
Editor access alone does not redefine or complete that larger scope.

### Command session metadata and native browser context

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

`runtime.command_completions()` exposes current registration identities, and
`await session.complete_command(command, prefix, identity)` in BOTH client SDKs
queries the corresponding worker without executing its command callback or model.
The callback has no interactive UI/editor/navigation/session-mutation authority.
Items are plain single-line text (32 unique entries/64 KiB result); the prefix and
value/label/description bounds are validated on both sides. Failed/timed-out
uncertain callbacks settle/reap their workers and withdraw current declarations;
no retry/replay or fallback callback is implied.

Replacement/disposal changes the command completion identity. Stale references
and registration changes during callback execution yield no completion data;
an old disposer cannot remove a replacement. Both client SDK wrappers settle
their owning Agent's tool projection if that worker fails.

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

Prefix-only SDK/legacy queries explicitly supply `None` for that context; they
cannot impersonate a full draft. New Claw editor queries independently negotiate
`extension_argument_context: 1`. Unnegotiated/invalid/foreign-scope metadata is
refused before callback admission. Withdrawing support cancels captured contextual
jobs (including before task startup), refuses late contextual output and does not
silently replay the query as prefix-only. A user may explicitly edit/Tab to request
new data under the remaining capability. Existing prefix-only callbacks remain
supported; `argument_input=` is optional on both SDK wrappers.

More expressive suffix semantics and broader argument API interoperability remain
open. Callback-supplied spaces/quotes are literal replacement data; the host
never silently adds shell quoting or interprets the resulting argument. Independent
child prompt completion is not claimed. Native file/slash-name completion is retained.

### Extension terminal command-menu inspection

```python
page = await api.get_command_catalog(query="", offset=0, revision="")
# Continue using page["next_offset"] and page["revision"].
```

Interactive parent COMMAND callbacks may read all entries in the same current
terminal menu used by `/help` and completion, in 32-entry pages. Entries contain
only normalized command name and sanitized description, with a terminal-menu
source label, session identity, revision and explicit pagination. This is NOT
execution approval, activation, model input or a claim that every remote command
is available. Tools/headless/independent-child callbacks return `None` instead of
borrowing the parent editor's command context.

Each response is scoped to its invoking session/command/context. Caller-selected
session/branch/action fields are refused; copied pages cannot mutate the host.
Changed menu metadata or another callback invalidates an old revision and returns
`status="stale"`, no entries and no next page. Restart at offset zero explicitly;
there is no silent partial merge, command callback execution or automatic replay.
Claw separately negotiates `extension_command_catalog: 1`; previous session/tree/
model/name/inspection capability does not substitute for that viewer support.

### Extension session names

```python
name = await api.get_session_name()        # str (including "") or None if unavailable
queued = await api.set_session_name("Release review")
queued_clear = await api.set_session_name("")
```

Interactive parent COMMAND callbacks inspect the actual session owner; Claw reads
through its reserved gateway/broker, not the viewer's cached footer. Outside an
interactive command, reads use an invocation-owned, copied metadata snapshot when
available. Tools, hooks and headless callbacks cannot borrow rename authority.
Independent children and unadmitted/stale workers never borrow a parent name;
without separately supplied owning metadata they receive `None`, not an empty name.

Setting a name opens a focus-owned, paginated host review showing the exact current
owner, expected old name and proposed new name. `True` means **queued after callback
success**, not saved. Failed/canceled callbacks discard it. The existing native
metadata owner applies the change only if the name still matches the reviewed old
name, preserving history/model/tools/permissions and the current persistence owner.
Names are plain Unicode text with the native 256-byte/control bounds. The API uses
`""` to clear; `"-"` is a literal name (unlike the native `/name -` clear shortcut).

Claw independently negotiates `extension_session_name: 1`; older/withdrawn viewers
cannot substitute model-inspection or navigation support. Missing name data/optional
frame-budget exhaustion is unavailable, never a cached parent name or a replay.
This metadata feature does not add extension history mutation or model execution.

Interactive extension COMMAND callbacks can use `await api.get_session()` for a
bounded snapshot of session_id, workspace and group_id (Coding uses an empty group).
No hidden system prompt, full conversation or foreign-session data is included.
`await api.open_sessions(tree=False)` queues the native session browser; tree=True
queues the current session's branch/checkpoint tree. A True receipt means **queued**,
not that a session was activated or that the callback has finished.

The host opens that browser only AFTER the same command callback settles successfully
under the same session/connection/history owner. Failure, cancel, stale command IDs,
owner/epoch changes and unavailable viewers discard the deferred request. Existing
Coding/Claw session providers, group/session control protocol, idle exclusion,
branch/checkpoint validation and persistence remain authoritative. The operator
must separately select/activate/create a session through the ordinary browser;
opening it never switches session, launches a model turn or changes the draft.

These APIs use the existing owned/validated pending host-request path. They are
unavailable to tool/hooks/context/child callbacks, headless execution and older
viewers. Original RPC action and active extension/command ownership are checked at
the host, independently of a worker's capability claims. Claw negotiates
`extension_session: 1` in hello/pong separately from editor/form/rich-result controls.
Unsupported viewers cancel the request rather than render an unknown session dialog.

The parent draft/focus, typed attachments and permission/planning policy remain
unchanged; closing the browser restores the editor. Both real workers, successful/
failed live commands, existing navigation regressions, stale-owner checks, tool/
headless/older-viewer refusal and private-tmux browser entry/return are tested.
This is session inspection plus explicit native browsing, NOT direct session mutation
or a paginated extension catalog API. Those host-action/context gaps, model control,
custom renderers/interception/providers and remaining MCP/platform requirements stay
open and are not waived by this increment.

### Reviewed direct command navigation

Interactive command callbacks can now request:

```python
queued = await api.navigate_session("resume", session_id=target_id)
# Also: "new", "branch" (branch_id), "goto" (zero-based index), or "fork".
```

The API queues at most one reviewed intent per command. Its bool receipt means
**queued after review**, NOT activated/committed. New requires no target fields;
resume requires an explicit session; branch requires an explicit branch; goto
requires a zero-based checkpoint index. Empty session for branch/goto means the
captured current owner. Fork is limited to the current session/head, with optional
zero-based index. IDs/field combinations and checkpoint types/ranges are validated;
returned strings/paths cannot substitute a host command.

The UI defaults to denial. Right/Space selects approval, Enter opens whole-target
review, PgUp/PgDn reviews every page and another Enter confirms. Current owner and
the exact action/session/branch/index are displayed, with a queued-not-activated
warning. Paste/repeat Enter, hidden/tiny/changed-layout review and default denial do
not approve a mutation. Normal host approval retains priority. No tool/model/
permission change is authorized by this form.

Only AFTER the original callback completes successfully under the same command/
extension/session/connection/history owner does the host dispatch the existing
native navigation control. Failure, cancel or owner change discards the intent.
Existing idle/child gates, target/group ownership, safe checkpoint/tool-boundary
checks and persisted plan/extension state remain authoritative; invalid or vanished
targets fail without a substitute selection. Claw uses its group-owned session
protocol; Coding uses its normal serialized commands. Fork saves a checkpoint
branch; branch/goto activate context according to existing client semantics.

The main draft/attachments are not submitted or discarded, permission/planning
policy is not overridden, and no provider request is launched. Claw separately
negotiates `extension_navigation: 1`; viewers supporting only metadata/browser
requests cancel direct navigation before receiving unfamiliar review semantics.
Tool/hooks/context/child/headless callers cannot borrow command authority.

Navigation and rename reviews now require a host `extension_dialog_accepted`
receipt emitted only when the broker actually delivers `True` to the callback.
A sent answer or transport ACK is not a navigation grant. Canceled/expired futures
cannot authorize a later successful callback; receipt ownership and current
session/history context are rechecked before queueing the native operation.
The TUI and gateway must both support these receipts; an older gateway without
one cannot authorize navigation/rename. Local command cancellation revokes reviewed
navigation immediately, and withdrawn navigation support also prevents dispatch.

Real current-host dispatch for all five actions, denial/failed callbacks, missing
targets, stale owners, exact-review bounds and older-viewer behavior are covered,
plus private-tmux reviewed new-session synchronization in both clients. Direct
navigation is now implemented; paginated extension session/tree catalog APIs,
model/other host context, custom rendering/interception/providers and remaining
MCP/credential/native-platform requirements stay open. No subset waives them.

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

Only actual interactive command RPCs receive catalog authority. Tools/hooks/context/
child/unattended callbacks return None; original RPC and host extension/command/group/
session ownership are independently checked. Missing/foreign/unreadable targets or
membership changes yield unavailable without disabling the extension or replaying
reads. Invalid plugin request/response codecs still fail closed. No model turn,
permission/planning mutation or session activation is introduced.

Real workers, both live hosts' 61-entry multi-page catalogs, search/tree/branch
previews, revision ordering/changed membership, foreign-group refusal, cancellation
settlement and production terminal catalog commands are verified. This supersedes
catalog APIs being listed absent; other model/host-action APIs, custom rendering/
interception/providers and remaining MCP/credential/native-platform gaps stay open.

### Runtime-registered extension shortcuts

Trusted extensions can register a shortcut and its actual command callback:

```python
async def compose(args, context):
    snapshot = await api.get_editor()
    if snapshot is not None:
        await api.set_editor(snapshot["text"] + " prepared", snapshot["revision"])
    return "Draft prepared; not submitted"
api.shortcut("compose", "alt+k", "Prepare the command draft", compose)
```

Registration contributes the namespaced `/ext-EXTENSION-NAME` command plus a
canonical default key; pressing it uses the EXISTING interactive command owner,
not another tool loop or arbitrary input interception. Plugins must already be
explicitly trusted/activated; a key does not discover/load an extension. Callbacks
retain normal idle/child/tool-policy/session/connection checks and any command host
APIs. No model turn or tool approval is implicitly created by a shortcut itself.

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
viewers, completion pickers, child/MCP/plan panels, overlays, output reviews,
active commands/model work and closing/disconnected owners retain priority. Unload/
failure/reload updates registrations; stale keys cannot adopt a new callback. Coding
now refreshes actual registration snapshots after extension commands even if a plugin
has no widget/notices. Claw separately advertises `extension_shortcuts: 1`; missing
support is both dispatch-disabled and reported in effective help.

Real workers, both UI command/editor paths, user remap/unbind/inactive overrides,
reserved/default/user/inter-extension conflicts, contextual suppression, repeat,
load/reload/unload and both private-tmux default/remapped key paths are verified.
Arbitrary input interception and custom renderers remain separate OPEN requirements,
as do provider registration, other host APIs and remaining MCP/platform work.

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
approval, planning and child permission gates remain authoritative. A transformed
string resembling `/quit`/a URI stays model input data, not a reparsed host command.
Hook callbacks cannot borrow editor/session/navigation command authority.

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
refusal recovery are tested. Arbitrary raw-key interception, handled-input dispatch,
custom renderers/providers and remaining MCP/credential/native-platform gaps stay
open; submitted text transforms do not waive those distinct requirements.

Pre-turn compaction of PREVIOUS history retains its existing lifecycle; the input
hook is a pre-admission/user-response check, not a veto of independent maintenance
work. Failed reload of a previously registered input filter remains failed/required,
not an implicit unload: only successful reload or explicit unload changes that
policy. Print mode reports a refused input as a nonzero error; RPC emits an error,
not an empty successful message_end. No synthetic assistant answer is fabricated.

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
InputConsumedEvent or return for automatic replay. SDK callers can inspect/drain
handled receipts with `agent.drain_handled_inputs()`.

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
not fabricated assistant prose or tool approval. Permissions/planning/tool/model
capability gates are unchanged; returned command-looking feedback is never executed.

Print mode outputs explicit feedback and returns success for handled-only input;
JSON mode emits a valid system/input_handled envelope (optional real assistant output
is separate). RPC emits input_handled receipts and an explicit completion marker,
not a counterfeit model message. Claw sends a content-free final signal after actual
handled feedback, releasing run ownership without synthesizing a response. Other
non-TUI channels receive extension feedback as channel output, not model generation.

Child supervision retains labelled input feedback as separate bounded metadata
(64 entries/64 KiB), with zero model calls when the task is handled. It survives child
record reload and appears as SYSTEM feedback in source inspection, not invented
user/assistant history. Parent conversations remain unchanged. New shipped clients
are jointly verified; older-viewer handled-outcome interoperability remains unproven
rather than being inferred from JSON accepting extra event types.

Generic initial/queued/old-history/queue-mode receipts, both live initial and queued
image paths, child result/source metadata, text/JSON/RPC completion and production
terminal model-call counters are tested. Raw-key interception, arbitrary custom
renderers/providers/other host capabilities and remaining MCP/credential/platform
gaps stay open. Handled outcomes no longer stay listed absent, but do not waive them.


### Custom extension views

Trusted extensions' `api.custom(title, render, on_input, ...)` now uses the shared
viewport-aware custom component, including independent child workers; inline
remains the default.
Python callbacks stay in the owned extension worker; painting only consumes bounded,
sanitized frame data. Vetted SGR styles/colors are supported, never OSC clipboard,
cursor/erase/graphics/alternate-screen commands or extension-supplied host cursor
markers. Optional carets are host-clamped to whole grapheme boundaries. Escape or
Ctrl+C cancels; redraw, suspend and exit remain host controls.

Keys/paste require a current visible frame. Hide/show, modal focus, row-budget and
geometry changes fence old revisions with view generations, including same-size
restoration. Queue/owner refusals are visible and never automatically replay input.
Completion returns bounded JSON data, not tool approval; existing permissions and
parent/child tool gates remain unchanged. No socket/worker I/O runs during painting.
Claw additionally negotiates the capability and checks original connection/group/
session/child leases; older viewers or withdrawn capabilities cancel the question.

The shared codec, broker, real workers/clients and parent/child terminal paths are
covered by offline tests. Richer custom containers and raw-key interception outside a custom view and native platform checks remain open;
supported presentations do not waive
those gaps or introduce an OS sandbox.


Custom declarations can now request `fullscreen=True`, `message="..."` and
`key_events=("press", "repeat", "release")`. Inline/press-only remains the default.
The host owns temporary alternate-screen entry/exit, approval/output/overlay
preemption, suspend/resume and native-buffer restoration. Resize re-homes only the
alternate painter; saved main rows and scrollback remain untouched. F1 switches
between sanitized paginated metadata and the custom body; metadata navigation and
paste cannot complete or feed the custom callback. Opted-in Kitty repeat/release
keys use normalized event types, but reserved host controls stay outside callbacks.
Claw requires the separately negotiated `extension_custom_rich:1`; unsupported or
withdrawn richer requests are canceled rather than silently downgraded.


Custom input callbacks can await nested `api.input/select/confirm/form/custom`
questions. The same parent component/state is cached while its child owns focus;
fresh frames are required on restoration, including fullscreen exit/re-entry.
Escape cancels the visible child. Rendering cannot create dialogs, and nested input
callbacks cannot borrow command editor/session/navigation authority. Parent IDs
come from an owned host renderer lease, never extension-supplied identifiers.
The stack is bounded to four displayed levels and eight broker questions; closing
a parent or disposing its context cascades to descendants. Claw requires separately
negotiated `extension_custom_nested:1` and the original connection/session/child
leases. Unsupported children are canceled, not silently treated as standalone roots.
Aborted uncertain callback chains are reaped rather than replayed.

### Prompt terminal-input hooks

Claw now wires trusted `api.on_terminal_input(callback)` registrations into its
actual prompt through the gateway. As in Coding, callbacks may return `None`/`{}`
to continue, `{"consume": True}` to consume, or `{"data": "..."}` to transform.
Hooks become available after trusted-worker activation, e.g. `/extensions`; lazy
startup is preserved. This supersedes the earlier missing-gateway boundary.

The viewer opts into **`terminal_input:1`**. The gateway supplies a connection-owned
opaque token and advertises strictly typed declared hook availability/worker generation in
extension snapshots. Every request/reply/cancel preserves its original group,
session, worker generation, UI input lease and monotonic input sequence. UI lease
strings are hashed to a bounded wire identity; physical focus/geometry remain in
the local lease. Foreign sockets, withdrawn capabilities, stale generations and
reused sequences cannot adopt or replay input, even after receipt eviction.

One owned callback job per connection runs independently of the socket reader.
The gateway has a four-second deadline inside the five-second host queue deadline.
Cancelled/uncertain callbacks settle/reap their original worker. Session/navigation,
connection, focus/physical-size and worker reload/unload changes reject held input
rather than applying it to a successor. Original runtime selection is captured
before scheduling, and retiring an old MCP/extension client cannot remove a newly
loaded connection or its tools. Invalid registration snapshots disable prompt
hooks and cancel held input; they cannot keep an old active-hook cache alive.

Only editable parent-prompt input visits these hooks. Terminal negotiation,
cursor/clipboard replies, approval/modal/custom/child/output surfaces, host
shortcuts and reserved cancellation controls remain host-owned. Original submits
stay FIFO behind held text without visiting hooks. Transformed keys are validated
against the current keymap and cannot synthesize submission, approval, exit or a
host handoff. Atomic paste never becomes raw key/protocol input. Hook callbacks
cannot borrow dialog/command authority or a second model loop.

**Alt+I** (`terminal_input` in `keybindings.json`) pauses/resumes hooks and shows
recovery. `/terminal-input list|restore ID|discard ID|clear|pause|resume` manages
bounded originals; explicit restore inserts text into the editable draft without
replaying controls, importing attachments, granting tools or submitting a turn.
Failures/interruption pause hooks so recovery remains accessible. Queue/recovery
limits are 32 inputs/128 KiB, with 16,000 UTF-8 bytes per intercepted item; pause
before using the editor's normal larger-paste path. Older gateways are explicitly
reported as unsupported and retain host input, not a claimed fallback parity mode.

Owned worker/socket/app tests and production Linux/private-tmux typing, atomic
paste, model/callback counts, pause, physical resize, recovery and native terminal
restoration cover this path. Independent child supervision-editor interception,
broader render/provider/host APIs and native-platform validation remain OPEN; child
forms and dialogs are protected from parent hooks, not silently intercepted by them.


### Buffered input and live hook registration

Adjacent editable key tokens from one filtered terminal frame now travel as a
bounded span, rather than one worker/socket RPC per character. Spans are split
only at whole UTF-8/enhanced-key tokens and the 16,000-byte limit; submit/handoff
controls and atomic paste keep separate FIFO boundaries. A callback's `data` may
therefore contain several characters or encoded keys. Callback counts depend on
terminal framing, **not** character count. This prevents an ordinary buffered
sentence from exhausting the 32-item queue. Oversized encoded key tokens are
refused explicitly, never silently dropped or replayed.

Workers now report live terminal-hook registration after callbacks, including
commands and nested custom-view completion. Removing the last hook with its
unsubscribe function updates runtime availability immediately. Already-held
same-owner input drains exactly once without another hook; fresh input goes
straight to the host editor. This is a normal registry change, **not** an error
fallback: lifecycle changes, malformed replies, callback failure and cancellation
still fence/reject held input. Re-registering from an extension command updates
the same worker and re-enables interception without reload/reconnect; a user's
manual pause remains respected.

Claw's matching terminal result carries an exact boolean live-registry field so
an idle viewer does not depend on a previous model-run UI subscription. The field
is validated only after original request/connection/session/sequence ownership.
Updated workers/daemon are required for live registry refresh; older senders that
omit this additive metadata retain their existing behavior and do not establish
this newer guarantee. Independent child-editor and broader raw/host APIs remain
on the open audit rows.


### Independent child steering-editor hooks

In `/agents`, **s** opens the selected child's steering draft and **t** opens its
follow-up draft (the transcript preview also offers Ctrl+S/Ctrl+T). While that
child's independent extension worker is live and attached to this UI, its own
`api.on_terminal_input` callbacks can consume/transform those editable drafts.
The surface is `child-prompt`; parent and sibling hooks do not receive this input.
No worker/model is created merely by opening the editor, and completed or
unattached children do not borrow the parent's hooks.

New-child task/settings, access selection, preset/catalog views, confirmation,
child extension dialogs and tool approvals remain host-owned. Transformed keys
cannot synthesize review, execution, approval, configuration or exit. Original
Enter opens the existing visible review; only a fresh explicit confirmation can
execute a child control. Escape from review restores the child draft rather than
discarding it, and re-entry advances the edit lease.

Held input is bound to the specific child worker, UI attachment, editing action,
form generation, focus and physical geometry. Switching siblings, leaving/reopening
the form, closing/completing a worker, overlays and resize reject stale results,
not replay them elsewhere. Alt+I remains a host pause/recovery route. Last-hook
unsubscribe and later registration update the same child worker's live state;
ordinary queued same-owner text drains once without invoking a parent hook.

Claw additionally requires **`terminal_child_input:1`** and the connection's
negotiated child UI token. Requests/results/cancellation preserve child ID/token,
parent group/session, worker generation and the existing terminal input leases.
Owned bounded registry notifications update live registration and retire completed
children without relying on the last model-run UI subscription. Foreign sockets
cannot invoke or cancel a captured child worker, and capability withdrawal fences
pending child input without disabling parent hooks.

Real child workers, both actual clients and Linux/private-tmux tests cover isolated
callback streams, unchanged parent draft/model counts, cancel/review restoration,
source ownership and native terminal recovery. This supersedes earlier statements
that all child-editor interception was missing; richer containers/host/provider APIs,
older-sender interoperability and native-platform validation remain on their open
rows. These UI/permission leases are not an OS sandbox.


### Structured custom-view containers

Trusted custom-view callbacks can now use the standalone worker-side
`xdog.tui.components.custom_layout` helpers: fixed/weighted `CustomRow` and
`CustomColumn`, `CustomPadding`, titled `CustomBox`, safe/wrapped/aligned
`CustomText`, `CustomSpacer`, and `CustomComponent` adapters for existing editable
TUI components. Return `custom_frame(layout, width, height)` from `api.custom`'s
render callback. Component state and input stay in the original extension worker;
only bounded sanitized lines and a single cursor coordinate cross the existing
frame protocol. No new model loop, host callback execution, tool approval or
terminal owner is introduced.

Whole-grapheme clipping, deterministic cell allocation, style isolation, zero-size
viewports, cycle/depth/node/byte bounds and multiple-cursor rejection are tested.
Both clients and independent children exercise an editable two-column layout,
Unicode caret, nested question/resumption and resized completion. Production
Linux/private-tmux inline AND fullscreen paths verify nested editable state,
native-buffer/termios restoration, once-only command invocation and zero model
calls. These helpers extend the previous line-array API rather than replacing it
or requiring a new gateway wire dialect. Richer arbitrary render/provider/host APIs,
interoperability and native-platform validation retain their open scope.


### Explicit MCP resource watches

In the MCP resource catalog (`/mcp`, select a server, **u**), press **w** on an
advertised resource to watch or stop watching that exact URI. Change notices show
state and a coalesced change counter. They remain visible over a resource preview
without replacing its immutable contents. Watching **never** fetches the resource,
inserts text into the composer, submits a model turn or grants a tool permission;
resource retrieval still requires an explicit operator action.

Watches belong to the actual panel/session/connection and selected MCP client.
Closing the panel, losing that owner, disconnecting or shutting down removes local
ownership and closes/unsubscribes the original transport. Late notices cannot
update another view. No watch is automatically recreated after reconnect, and no
uncertain subscribe/unsubscribe operation is replayed. Repeated same-view watch
requests reuse the existing handle; legacy shared-URI watches keep their own view
sinks and unsubscribe remotely only when the last local owner leaves.

The SDK has explicit `watch_resource`, `unwatch_resource`, and
`detach_resource_watches` methods. Resource capability must advertise an exact
boolean `subscribe: true`. The current-dialect path uses a dedicated correlated
`subscriptions/listen` lifetime with an acknowledged resource filter, update
notifications and a terminal complete/error; stateless HTTP keeps the original
POST SSE stream (no GET resumption or POST retry), and stdio retires request IDs on
cancel. Legacy transports use resources/subscribe/unsubscribe and the existing
notification channel. ACK omission, foreign URI/identity, unexpected ordering,
stream/auth/redirect errors and capacity limits fail explicitly.

Bounds: 16 transport watches/client, 32 SDK view watches/manager, finite UI/gateway
view/job maps, 8,192-byte URIs and coalesced notices at most 20 Hz/watch. HTTP reserves
connection capacity for ordinary MCP operations rather than letting watch streams
consume every connection. Payloads are change metadata only, not server content.

Owned offline peers exercise stdio and HTTP in both dialects; both real clients
and four Linux/private-tmux paths verify visible notices, explicit stop, cleanup,
zero automatic resource reads/model calls and native terminal recovery. Official
source retrieval during this pass failed (connection reset), so third-party/current
protocol conformance, additional subscription filters/capabilities, authorization
interop and native-platform checks remain OPEN. This is implemented/tested local
behavior, not a claim that the full MCP interoperability audit is complete.


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
issuer/client strategy/authentication method/environment-source name/scopes,
certificate-file content binding and expiry, then admits the credential to memory.
It performs no metadata lookup, token POST, connection, model turn or tool grant.
Client-secret environment VALUES are never stored. Access/refresh credentials have
no public diagnostic representation and never enter transcripts or review payloads.

Saved access expiry uses bounded wall-clock values and is converted back to an
in-memory deadline on restore. Expired access can preserve its saved refresh grant,
but refresh still needs its own live reviewed action. Before a potentially rotating
refresh POST, an existing saved snapshot is removed, so restart cannot replay an
old refresh token after uncertain rotation. Rotated credentials are **not** saved
implicitly; explicitly review/save the new grant if desired.

**x** erases the saved credential only (not a live token or remote grant). The existing
**f** forget/disconnect review erases both memory and saved state; neither action
claims remote revocation. Save/erase cancellation revokes storage admission and waits
for the actual writer to settle; if an operation committed before cancellation,
inspect local state rather than automatically retrying it.

SDK callers can opt in with `McpManager(..., credential_dir=...)` and prepare/confirm
`credentials_save`, `credentials_restore`, or `credentials_forget` through the
existing owned `manage_auth` API. A manager without a credential directory remains
memory-only. Actual client restart/consent and private-tmux tests cover the file
backend. File permissions are not an OS sandbox.

#### Opt-in Linux Secret Service storage

Set the top-level `mcp_credential_backend: secret-service` in
`~/.config/xdog/claw/config.yaml`, then restart the gateway to load configuration.
The default remains `file`; each group has a separate native storage namespace.

SDK hosts select `McpManager(..., credential_dir=..., credential_backend="secret-service")`.
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
tmux test or proof of every Secret Service implementation. macOS/Windows native
credential backends, broader service interoperability and native desktop validation
remain OPEN. Neither backend is an OS sandbox.


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
validated cached metadata. Catalog inspection starts no model request. Current
startup/config-only alias activation and additional provider/host APIs remain open;
this slice supports explicit activation and interactive selection, not implicit login.

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
retain existing child context/vision/reasoning/budget guards and parent tool
restrictions; selecting a child provider does not switch the parent's model.
Existing primary routing and native/global provider registration are unchanged.
Owned worker/client/child and production Linux/private-tmux model-selection paths
verify this behavior without paid model calls. Additional native/embedding/search/
provider-auth APIs, initial startup/persisted alias activation, external interop and
native-platform validation remain OPEN.


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
restart/resume restores that alias and the corresponding worker/limits. Selection
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

The parent setting does not grant tools, change planning/approval restrictions or
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
replayed or rolled back. Existing parent/child/planning/tool permission gates apply.

Catalog transport uses an allowlisted public summary rather than serializing whole
`Model` objects: authentication headers, endpoint URLs and protocol internals are
not exposed through model inspection. Cached catalogs are bounded and duplicate or
reserved-namespace native identities fail explicitly rather than silently shadowing
extension models. These controls do not submit a turn; only a subsequent explicit
prompt invokes the selected model.


### Combined MCP notification watches

From the focused MCP server list, **Alt+W** opens an editable JSON notification
filter. Choose exact boolean `toolsListChanged`, `promptsListChanged`,
`resourcesListChanged` flags and/or a `resourceSubscriptions` array of explicit
resource URIs. **Enter** starts one current-protocol stream for the selected set;
Enter again stops that same active set. Editing the JSON selects a different set.
The draft survives submission/errors; Escape goes back and closing the MCP view
stops all its watches. Alt+T/P/U singleton catalog watches and w resource watches
remain separate.

Empty/invalid/duplicate filters are refused, not inferred. Bounds are 16 URI entries,
8 KiB per URI and 64 KiB total; URI order is canonicalized. Exact full ACK matching,
view/session/connection ownership and no replay apply. These are notice-only
subscriptions: no automatic reads, catalog refresh, tool registration or model input.
Legacy combined streams are explicitly unavailable (legacy singleton watches still
work). Claw requires independently negotiated `mcp_combined_watch:1`; withdrawing it
settles combined watches without stopping unrelated singleton watches. Independent
serving-SDK stdio/HTTP and actual both-client/terminal paths are verified; broader
client-capability, external interoperability and native-platform gaps remain open.


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
native Windows/macOS or complete CLI capability parity.

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
session-wide metadata, persisted separately from transcript bodies; switching a
branch within the same session does not change it. A new session starts with its
own selection. A missing sidecar means no selection; malformed metadata is rejected.

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
flush remains governed by the normal compaction tool/planning hooks. Failed, empty,
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
entries and branches, including restoration of prior summary context. Neither hook
borrows TUI command/dialog/navigation authority or grants tool permissions. See the
Agent package's owned compaction extension documentation for the exact contract.

### Recovering context before compaction

Each successful manual or automatic compaction creates a durable **Before
compaction #N** branch containing the complete original JSONL conversation,
including the owning session's unsaved live tail. It is indexed before current
context is replaced. A checkpoint failure leaves current context unchanged; a
later replacement failure may leave an extra safe checkpoint but does not produce
a successful compaction receipt. Admitted writes and publication remain owned
through repeated cancellation, with no automatic replay.

Use `/tree` to find and explicitly restore the checkpoint. Normal navigation
retains a return head and restores planning/extension-entry context, without
submitting a model turn or changing tool permissions. The transcript viewer can
then search/copy/export the restored source. Restoration is conversation-only;
workspace files are not reverted, and normal idle/group/session/tool-boundary
checks still apply. The optional `recovery_branch_id` in committed compaction
metadata identifies the source checkpoint.

Markdown conversation archives now have random unique suffixes, are created
exclusively rather than overwritten, and use private `0600` permissions on POSIX.
Even simultaneous same-second compactions preserve their separate archive files.
These Markdown artifacts are supplementary human-readable archives; lossless
restoration uses the typed recovery branch, not parsed Markdown.

### View saved context without activating it

Select a saved session/branch in `/sessions` or `/tree` and press **v** for a
read-only full-source view. **Enter** still means activate. Explicit equivalents:

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

### Staged saved-session/provider navigation

Resume, branch restore, and checkpoint navigation now prepare a target AgentSession
before retiring the current cached owner or changing the active pointer. The stage
uses the target's saved model, reasoning, transcript, planning, extension entries,
branch child scope, and the group's existing tools/permission configuration. Startup
entry/plan writes are buffered and target UI is not published during validation.
Missing providers, failed startup, unsupported saved effort, and canceled preparation
leave the current owner intact; no provider/model fallback is attempted.

A successful admission commits target managed state, then adopts the prepared Agent
and worker once—startup is not replayed against the live target. Buffered entry writers
and planning/inspection/child-service context are rebound to the owning group and
its real gateway loop. Follow-on entries and plans persist to the active target.

Navigation is a connection-owned job rather than a blocked gateway reader operation.
On an updated gateway, Escape cancels pending navigation while other inspection
controls remain responsive. Cancellation before admission discards the prepared
owner. Once a managed commit is admitted, repeated cancellation waits for disk and
in-memory publication before releasing the group. If that commit completed, the
client synchronizes the actual new context rather than claiming a rollback or replaying
navigation. Older gateways explicitly lack the cancellation capability.

Managed multi-file navigation writes have private rollback copies and cache snapshots;
an ordinary I/O failure restores the prior target/pointer state and removes transaction
temporary files. Individual file writes remain atomic replacements. This is **not** a
power-loss-atomic multi-file database transaction; abrupt process/OS termination and
failure of rollback itself remain storage limitations. Trusted extension external
filesystem/network side effects are likewise not rollbackable or OS-sandboxed.

### Interrupted navigation recovery

Claw navigation now writes a durable undo journal before changing managed session,
branch, entry, planning, catalog, and active-pointer files. Private checksummed backups
are flushed before the prepared manifest is published. Managed replacement names are
synced before a committed marker is published. The store recovers interrupted work
**before** loading any session index or exposing planning/extension state:

- A prepared transaction restores the prior complete files and removes newly created
  targets. Backup copies remain intact throughout replay, so another interruption
  during recovery can be retried safely.
- A committed marker retains the complete new state and finishes cleanup; a failed
  housekeeping cleanup is not reported as a failed/rolled-back navigation commit.
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
or native Windows power-loss behavior. Directory fsync and advisory-lock guarantees
are POSIX-specific. Trusted extension external side effects remain outside this journal.

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
text matches. Normal goal/scheduler filtering and source redaction remain applied;
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


### Editable live child input queues

The child roster now offers `q` (or Ctrl+Q from transcript preview) to inspect the
actual owning agent's pending steering/follow-up inputs. `e` edits, `x` removes,
`k`/`j` reorders one step within the original delivery lane, and `u` returns text to
the CHILD steering/follow-up draft. Return is not submission and never overwrites
the parent editor. Every change requires complete painted target/content/revision
review and a fresh physical Enter; paste, repeat events and narrow/unpainted frames
cannot confirm it. Failed edits keep the local draft. An exact owner removal receipt,
matching the reviewed input, is required before draft restoration.

Hosts read/change the existing Agent queue on the child owner loop, not a mirrored
UI FIFO. The captured revision covers identity/order/content/delivery/checkpoint
selection and image identity. Changed/stopped queues and selected/consumed inputs
refuse mutation without automatic replay. Explicit refresh is required. Image parts
stay attached during an edit; return refuses image-bearing input because the child
form is text-only, rather than silently losing attachments. These are live input
controls, not permission approval or conversation/model-context replacement.

Claw advertises exact `child_queues:1`; older/unready/withdrawn peers cannot dispatch
queue controls. Coding RPC negotiates `child_queues:1` separately, then uses
`child_control` action `queue` with a typed `queue` object: `{"action":"list"}` or
`{"action":"edit","id":"INPUT","revision":"CAPTURED_SHA256","content":"TEXT"}`;
remove/return omit content, move instead has exact integer direction -1/+1. Keep
all original parent/revision/UI-owner/native-epoch fields. Native socket hosts use
that same JSON request in the child_control text field. Responses contain a `queue`
with child_id/revision/inputs and optional exact `returned` removal receipt. Metadata
is bounded to 32 inputs/16KiB text per input, with explicit refusal rather than
truncation. It is exact queued draft text, not heuristic-redacted display source.

Gracefully completed/canceled-child undelivered input recovery is described below;
image-capable child drafts, unclean-process crash durability, richer terminal/custom
APIs and the broader platform/interoperability goal remain separate open requirements. No automatic mutation/model call, OS sandbox or full
child-surface parity is implied by these queue controls.


### Saved stopped-child input recovery

After a child actually settles, the supervisor drains only identified inputs not
admitted to canonical history, releases the live agent, and persists those inputs
with the canceled/completed child record. Already consumed follow-ups are not copied
again. Existing records without this field load unchanged; malformed, duplicate,
oversized or invalid typed images refuse load rather than silently disappear or
restart a child. Recovery JSON is bounded to 32 inputs/16KiB text each and 512KiB
aggregate, plus the existing complete child-record persistence budget. Save failure
is explicitly reported; no partial/truncated recovery is advertised as durable.

The same `q` child input viewer now identifies saved recovery. `u` reviews a
`recover` request, then copies the exact retained text into a NEW read-only child
TASK draft after a matching recovery receipt. This does not steer a dead agent,
restore its approvals/model context, submit the draft, restart work or change the
parent editor. The backing saved input remains: a lost or canceled copy receipt
cannot erase the only durable copy. Starting a replacement task still requires the
normal whole configuration/task/access review and fresh explicit submission.

`x` is separate reviewed permanent discard. Its revision-checked removal saves the
candidate record before publishing the removed input. Failed save leaves both
memory and disk intact; admitted cancellation/shutdown settles the actual write
without inventing rollback or allowing overlapping close/discard operations.
Stopped inputs are not editable/reorderable live state; make edits in the new draft.
The API uses queue action `recover`, original input ID/revision and original parent/
child scope. The response has `stopped:true`, unchanged `inputs`, and an exact
`recovered` row. This is distinct from live `return`, which actually removes an
input and yields `returned`. Explicit consent to child queue data remains required.

Typed image parts are retained privately in saved recovery, omitted from queue
metadata except image_count. Copy to the current text-only child task draft refuses
image-bearing input rather than lose attachments. Native image-capable child drafts
and unclean SIGKILL/power-loss durability during active runs remain open; settlement
persistence and canceled-owner tests are not proof of those broader guarantees.
No auto-replay, paid provider call or OS sandbox is implied.


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
history. Existing neutral payload and child transcript/persistence budgets remain
in force. Offline tests cover both actual application child factories, retained
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
consume the 512 KiB **text/metadata** transcript budget. Initial images are saved
before child execution and preserved when factory setup fails; canonical and
unadmitted queue images persist without truncation. Reload does not restart work.
Switched-model context preflight no longer treats base64 transport as plain text
but uses a rough image allowance alongside the conservative text guard. This is
not exact provider token accounting or a promise that any image fits any model.
Final actual-context model validation and all existing permission/budget rules apply.

SDK callers can pass `images=tuple[ImageContent, ...]` to `ChildSupervisor.start`
or its steering/follow-up `control` methods. The application child APIs propagate
these same typed inputs. No file URI, remote fetch or implicit text fallback is
introduced. Claw requires the gateway's exact `child_images:1` capability and sends
bytes through the existing checksummed chunk uploads. Large canonical image bodies
are omitted from socket control receipts, explicitly marked `history_omitted`;
use the separately owned child-source reader for complete **display** history,
not raw binary/canonical data. Successful image refs are released; an exact replay
of a cached request returns its old receipt without rerunning or requiring those refs.

Coding RPC independently negotiates `child_images:1` via `rpc_hello`. Scoped
`child_control` start/steer/follow-up accepts either inline `images` or completed
`image_refs`, never both. Larger images use existing session/connection-owned
`image_chunk` uploads; successful admission consumes those references. Replies
include `accepted_input` with exact content/digests/MIME types, not image bodies.
Revocation cancels pending image jobs; it is not a rollback of already admitted work.
No image capability grants tool, approval or parent-editor authority.

Offline evidence includes both actual app/UI owners with file reads and 10 MiB
payloads, the real Claw socket upload path, an actual Coding RPC subprocess with
2 MiB chunk uploads, queue settlement/reload, mismatched-receipt draft retention,
and both private-tmux file/preview/review-cancel/terminal-recovery paths. Large test
payloads exercise bounded byte transport with a valid PNG header, not a hosted
pixel decoder or general model-provider interoperability.

**Still open:** live image-bearing queue return requires its verified removal/
receipt protocol. Stopped-input image copy is implemented in the following section;
legacy text-only recovery still refuses image inputs rather than dropping bytes. Saved
initial-task images from failed setup use the explicit recovery editor below. Local
child drafts survive hide/reopen in the same view context, but cross-reconnect or
cross-context draft browsing/recovery and unclean-crash durability are not complete.
Those retained caches are bounded and never automatically adopted into a different
owner. Native desktop clipboard/provider/decoder interoperability also remains open.


### Non-destructive stopped-child image recovery

In a stopped child's input queue (**q** in the roster), **u** now prepares a
reviewed image-aware copy. The source input and its exact revision are reviewed
before Enter. A complete, scope-bound, checksummed binary transfer and matching
owner receipt are required before the UI creates a **separate read-only child
start draft**, with `inherit_context: false`. Images, text and MIME types are kept;
no agent is constructed, model/compaction request sent, original input erased or
parent draft altered. Existing local child drafts are not overwritten. Each copy
has its own local draft slot; the local/cross-context draft browser below makes retained slots accessible,
but its memory caches are not cross-process crash recovery.

The SDK queue action is `recover_images`, with the exact input `id` and queue
`revision`. Its Python result contains the non-destructive `image_recovery` payload
and `queue.image_receipt`. The legacy text-only `recover` action still refuses
image inputs instead of dropping bytes. Running children are not recovered by this
action; **live image return still requires its own verified removal/admission
protocol** and remains unavailable.

Claw advertises exact `child_image_recovery: 1`; older gateways do not silently
receive a text fallback. Coding RPC requires independently negotiated
`child_image_recovery:1` **and** `child_queues:1`. This read/copy capability is
separate from sending new image prompts. Socket/RPC replies stream
`child_recovery_begin`, ordered `child_recovery_chunk` frames (at most 64 KiB raw),
then `child_recovery_end`; final control metadata contains no base64 image bodies.
The manifest binds content, image sizes, digests and MIME types to the requested
session/connection child lease, child ID, input ID and queue revision. Receivers
reject foreign/out-of-order/oversized/truncated/checksum/MIME mismatches, close
private spools on failure/cancel, and never manufacture a draft from partial data.
Transfers are bounded to four images, 10 MiB each / 40 MiB total, and a ten-second
sender deadline. Owner/revision changes or RPC capability withdrawal stop the copy.
The original durable input remains available for explicit fresh review/retry.

Evidence includes both actual native client owners with 10 MiB stopped-image copies,
real Claw socket framing, an actual Coding RPC subprocess with multi-MiB outbound
copies, revocation and malformed-frame checks, and both private-tmux review,
preview, no-execution and native terminal/cursor recovery paths. This is offline
Linux evidence, not general hosted image-provider or native desktop conformance.
Live image return, durable draft recovery and remaining host APIs,
active-run unclean-crash durability and remaining platform/API gaps stay OPEN.


### Local child draft browser

Use **d** in the child roster, or `/agents drafts`, to browse retained local child
start/steering/follow-up drafts and image recovery copies. The catalog includes
older session/branch/view/connection contexts, with workspace/session provenance,
CURRENT/OLD CONTEXT labels, image counts and uncertain-receipt disclosure. **/**
searches task text and provenance. This catalog lives in the current application
process; it is not on-disk draft persistence or active-run crash recovery.

**Enter** prepares a non-destructive copy into a new **read-only** start draft with
`inherit_context: false`. Old access, tool selection, instructions, model/thinking
and preset settings are disclosed but **not imported**. Text/images remain exact,
old caches stay intact, and no model/tool/session navigation is executed. A copy
gets its own draft slot; it does not overwrite an existing default task or another
edited recovery copy. Source uncertainty stays visible in the copied draft.

**r** prepares reopening only a CURRENT-context draft. Start drafts retain their
settings and saved cursor; existing-child steering/follow-up drafts require that
child to still be running. An old-context or stopped-child draft cannot resume;
explicitly copy it fresh/read-only instead. The normal model, tool, permission and
full start/input confirmation rules still apply when submitting the reopened form.

**x** prepares discarding only the selected LOCAL draft. It does not cancel an
agent, mutate a live queue, erase the durable stopped-input record or revoke tools.
Copy/reopen/discard each require all provenance/task/config/image digest pages to
be painted before physical Enter. Pasted or repeated/unpainted Enter cannot commit.
Source identity/content changes or a changed browser owner reject the action,
without replay. Discarding the local cache releases its retention accounting; a
separate fresh copy continues owning any shared immutable image data.

Both native clients wire this browser to their actual context-owned caches, so
closing auxiliary panels/reconnecting does not silently strand bytes forever in
an inaccessible old view. Old-context entries remain inspectable/copyable but do
not regain their previous execution or approval authority. Final pixel/provider/
native-desktop interoperability and durable cross-process draft recovery remain
on their existing open requirements. RPC remote draft browsing is not implemented
by this local native UI browser.


### Initial child task recovery after setup failure

The stopped child input queue now includes a distinct **initial-task** source when
setup fails before the original task is admitted. Text and initial images remain
available even if no child history was created. **u** prepares an explicit copy;
**x** prepares discarding that retained initial input only. Both actions require
the exact source/revision and full physical review. Image copies use the existing
verified binary recovery transfer and open a separate fresh read-only start draft
with `inherit_context: false`. No model, agent restart or original rewrite occurs
on copy, and parent/local sibling drafts remain untouched.

The runtime records initial admission separately: pending, admitted, handled,
uncertain or discarded. Actual user-message admission marks the initial task
admitted; an initial prompt handled by an extension is not offered as unadmitted.
A provider failure after actual admission does not reclassify that task as unsent.
Interrupted/legacy records without sufficient admission evidence are explicitly
**uncertain**, not proof of zero tool/model effects. Review the old history before
choosing to submit a recovered copy. This is not active-run power-loss queue
journaling or automatic retry.

SDK queue actions `recover_task` / `remove_task` identify the initial-task row;
ordinary edit/remove/return actions cannot accidentally reinterpret it as a live
queued message. Copy is non-destructive. Discard publishes only after the actual
persisted state change settles; failed writes leave it available, and cancellation
waits for the writer before reporting an uncertain committed outcome. Image bytes
are released from the retained initial field after explicit committed discard.

Claw advertises exact `child_task_recovery:1`; Coding RPC negotiates it independently
alongside `child_queues:1`. Initial-image copy additionally needs
`child_image_recovery:1`. Final RPC/socket control frames omit binary bodies and
use the existing manifest/chunk/end receiver plus exact task receipt. Capability
withdrawal or owner/revision change stops copy work, not the original durable task.

Offline tests cover real setup failures with/without images in both native owners,
legacy interrupted records, admitted-provider failure, failed/canceled discard,
RPC capability isolation, and both private-tmux image recovery/review/preview/draft
browse/native-terminal restoration paths. The full goal remains active: live
image return, durable local drafts, broader raw/custom/RPC APIs and native platform/
provider/credential interoperability still require implementation/evidence.


### Live image-return runtime contract (client integration pending)

The shared supervisor now provides a two-phase SDK contract for returning a live
child image input. The native and Coding RPC integrations are described below. Legacy one-step
image return still refuses lossy text conversion; use the two-phase protocol. This section
records the implemented foundation, not a claim that the UI gap is closed.

Call `ChildSupervisor.control("queue", child_id, json_request,
return_owner="explicit-host-lease")` with `return_prepare`, the input `id` and
exact queue `revision`. Preparation copies/validates bytes off the owner loop but
leaves the original queued. The result contains `image_recovery`, a matching
`queue.image_receipt`, `return_prepared` metadata and a random `return_token`.
Before removal, a host must fully receive/verify those bytes, preserve their
ownership and separately review the removal. Preparation alone grants none of
that consent and is not a dequeue operation.

`return_commit` requires the same owner, input, revision, token and exact verified
`receipt`. On the supervisor loop it removes only the same still-unselected input,
then returns `queue.returned` metadata and the matching receipt/token. Selected,
consumed, edited, stopped, foreign-owner, expired or malformed inputs refuse
without removal. `return_cancel` releases only that owner's prepared token and
leaves the original input queued. Leases expire after 30 seconds; each child has
four combined prepared/completed slots. Exact same-owner/content commit retries
within the lease return the original copied receipt, never remove another input.

Runtime tests use actual held child Agents, including 10 MiB payloads, concurrent
queue edits during byte preparation, cancelled preparation, selected/edited/stopped/
expired/foreign receipt rejection, bounded leases and immutable retry receipts.
They do **not** verify client-owned binary transport/consent/persistence, connection
revocation or terminal image return. That integration remains required; crash/power-
loss durability is not supplied by a process-local return token/cache.


### Coding RPC live image return

Coding RPC now independently negotiates `child_image_return:1` together with
`child_controls:1` and `child_queues:1`. The returned child-capability envelope
includes `child_return_owner`, separate from child/session/connection identity.
Return requests must use that current lease; withdrawing/re-enabling controls,
queues or return capability changes it, so old tokens cannot regain authority.
Other pre-existing child commands do not require this additional return-only field.

Send scoped `child_control` with action `queue`, containing `return_prepare`, the
input `id` and captured queue `revision`. The existing binary manifest/chunk/end
stream provides exact images to a verified receiver, followed by a final result
with `return_prepared`, `return_token` and `image_receipt`. The input remains queued;
stream completion is NOT removal consent. No separate stopped-image-recovery
capability or new image-prompt capability is required for this return operation.

After actually receiving/verifying and taking ownership of those bytes, the client
must separately review removal before sending `return_commit` with the same input,
revision, token, current return-owner envelope and verified `receipt`. The reply
contains `returned` metadata, not base64 bodies; exact commit retries with fresh
request IDs return the prior receipt and cannot dequeue another input. Explicit
`return_cancel` releases a prepared lease without removing its original. All original
selected/stopped/edited/expired/foreign checks apply on the actual supervisor loop.

Closed peers, revoked capabilities, cancellation or changed queue ownership stop
preparation and settle token cleanup. An operation already committed before a
transport failure is not rolled back: inspect its exact receipt/current queue;
never automatically replay work or replace parent history. An explicit live host
revocation guard is checked again at the owning-loop removal boundary.

Evidence includes an actual offline Coding CLI subprocess returning 2 MiB through
bounded native output, verified byte receipts and separate commit/retry, plus
withdrawal, closed-peer, foreign-owner and disable/re-enable tests. The RPC contract alone does NOT supply native queue buttons or cross-process
durable draft ownership. Native Coding/Claw integrations are documented below;
durable cross-process ownership remains open. Those UI/
platform/durability requirements remain OPEN; remote clients own their review and
byte-retention implementation rather than borrowing approval authority from XDOG.


Both clients now implement native live image return with a second painted removal
review and retained local backup. Claw additionally uses its negotiated socket
connection/view lease; see the following section and completion audit for limits.


### Claw socket/native live image return

Claw now supports the same **two-review live image return** as Coding: **u** in a
live child's queue prepares and verifies the byte copy without removal, then the
**SECOND removal review** requires fresh physically painted consent. A separate
read-only uncertain local backup is retained in `/agents drafts` before commit.
Successful exact removal receipts open a child-only draft with the original
text/images; cancellation leaves the queued input and parent draft untouched.
Backups are process-local memory, not crash/power-loss persistence.

The TUI explicitly negotiates `child_image_return:1` in its socket ping. Gateway
responses supply a random `child_return_owner`; return requests also carry the
exact `queue_view` token. The gateway forwards their combined lease through the
existing orchestrator/session path into the actual supervisor removal guard.
Disabling/re-enabling the capability rotates/revokes its connection lease, and
socket teardown revokes it before cleanup. A different view or old/reconnected
lease cannot commit a prepared token. Native results/verified copies are checked
against the original queue view, not adopted into a replacement panel.

Preparation uses the bounded checksummed binary recovery stream with additional
connection/view scope; image bodies never enter the final control frame. A failed
or cancelled preparation settles its token cleanup without dequeuing/restarting.
The normal selected/edited/stopped/expired checks apply again before removal.
A committed operation whose response is lost is not rolled back: inspect the
retained local backup/current queue and never automatically resubmit work.

Actual Coding/Claw native tests cover 10 MiB commit and cancellation, two reviews,
precommit backup, exact child draft and untouched parent/history/model-call count.
Claw socket tests cover foreign views, capability disable/re-enable and disconnect
with the original unremoved image preserved through stopped-input persistence.
Both private-tmux paths physically exercise live image preparation, second removal
review, returned image editor and native cursor/termios recovery. This is Linux/
offline evidence, not general provider/decoder/native-desktop interoperability.
Durable drafts/active-crash journaling, richer RPC/custom/raw host APIs and remaining
platform/credential/MCP interoperability stay on the open goal requirements.


### Concurrent return capacity and malformed queue transport

The four-slot live-return budget now includes **in-flight image preparations** as
well as prepared leases/completed receipts. Slots are reserved before asynchronous
validation yields and released on cancellation/failure. Simultaneous requests
cannot each pass the limit and create unbounded image copies. This does not add
crash durability or change the two-review consent workflow.

Claw validates typed queue request shape before examining return-specific fields.
Malformed arrays/null/scalars/missing fields or invalid JSON produce a failed
control receipt while the owned socket remains responsive; they do not disconnect
the client or admit queue/agent work.


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
directory-fsync claim. These flush-order tests are not physical power-loss testing,
and this change does not implement active-run write-ahead input journaling or
cross-process local draft persistence; those goal requirements remain OPEN.


### Write-ahead child input snapshots

Both clients now persist a bounded child steering/follow-up snapshot **before**
acknowledging live queue acceptance. Text and images are stored in a private atomic
`.inputs.json` sidecar using the child store's file/directory flush path. Failed
writes refuse live admission. Cancellation waits for the actual admitted writer;
terminal settlement waits too, so an old writer cannot overwrite a later settled
record. No blocking filesystem writes are added to the agent event loop.

After an unclean restart, an interrupted child with a valid snapshot exposes those
inputs as **crash-snapshot / uncertain** recovery rows. Copy still needs explicit
review and a fresh read-only child draft; no Agent is constructed or restarted on
load. Snapshots may contain inputs already consumed before the crash, and may
precede subsequent edits/removals. Inspect history/workspace effects before any
explicit resubmission. This is conservative data retention, NOT an exact once-only
consumption journal or a claim that recovered inputs were never executed.

Settled child records take precedence over stale sidecars. The terminal record is
flushed before journal cleanup, so a crash between those steps cannot revive old
accepted inputs. An actual owned SIGKILL test verifies a previously acknowledged
2 MiB image input survives and is explicitly recoverable without agent creation;
both native clients also verify their actual acceptance writes the sidecar.
Physical power-loss/native filesystem conformance, exact edit/remove/consumption
journaling and durable local editor drafts remain OPEN. No automatic replay or
permission escalation is introduced.


### Write-ahead live queue changes

Live child **edit**, **remove**, **move** and text-only **return** now write their
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
there is no automatic replay. Two-phase live **image** return commits and exact
consumption/handled-input journaling are not updated by this change. Those boundaries,
physical power-loss proof and durable local editor drafts remain OPEN.


### Write-ahead image-return commits

Two-phase live image-return commits now persist the prospective removal snapshot
before acknowledgment. Exact owner/token/input/revision/byte-receipt validation
runs before writing and again before actual live removal. Failed writes, cancelled
writers, checkpoint selection or host revocation cannot remove the input through
a stale commit. Writer cancellation waits for actual settlement. Exact completed
commit retries return the original receipt without rewriting the journal.

An owned SIGKILL test acknowledges an image-return commit, kills the process, and
verifies the returned input is absent from the restarted queue snapshot. This does
not persist the native editor backup: that remains memory-only. A crash between a
prospective disk write and live mutation/acknowledgment is still an uncertain
boundary, and exact consumed/handled-input journaling is not supplied here. Recovery
never automatically replays work or restores old execution permissions.


### Durable returned-image handoff

Both native clients now persist an exact conservative image/text backup **before**
a live image-return commit can remove the queued input. A bounded `.returns.json`
sidecar is flushed through the child store, then the prospective queue-removal
snapshot is saved, and only then can the same live lease commit removal. Failed
backup or queue writes refuse removal; uncertain write outcomes retain a conservative
copy for settlement. No permission, model or automatic resubmission is introduced.

After SIGKILL/restart, interrupted child records merge validated return backups.
On normal settlement, backups are included in the durable terminal record before
sidecars are removed. The stopped input browser exposes **durable-return / uncertain**
rows: explicitly copy (**u**) into a separate fresh read-only task, or explicitly
discard (**x**) after review. Copy is non-destructive, preserves exact image bytes,
and never restarts a child. Discard publishes only after its actual record write.
The source is deliberately uncertain: a backup can exist even when removal failed,
and prior tool effects are not undone. Inspect history/workspace before resending.

Backups have their own bounded collection (32 rows / 40 MiB images), separate from
queued-input retention. Capacity exhaustion refuses new return commits instead of
losing input. Record capacity accounts for the bounded canonical, queue and return
collections. No binary backup bodies are embedded in Claw control response frames;
explicit recovery uses the existing verified transfer. Returned data is not silently
reinserted into a live queue on startup.

Evidence includes actual owned SIGKILL after acknowledged image removal, exact
returned-byte recovery without Agent construction, backup/queue failure tests,
both native 10 MiB return owners and both private-tmux durable-row recovery flows.
This closes the returned-image handoff loss window, **not** general durable editor
drafts, exact consumed/handled-input journaling or physical power-loss/native
filesystem conformance. Local edited copies still live in memory until a supported
explicit persistence operation; those remaining goal requirements stay OPEN.


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
guarantee. General durable editor drafts, physical power-loss/native conformance
and remaining host/platform interoperability requirements remain open.


### Durable local child-draft storage foundation (UI integration pending)

The shared TUI package now provides a `ChildDraftStore` codec/storage foundation for
explicit private snapshots. It preserves task text, image bytes and provenance,
uses create-only publication with file/directory fsync on POSIX, and requires
owned 0700 directories / 0600 regular single-link files. Symlinks, hardlinks,
unsafe modes, oversized/invalid records, duplicate JSON fields and mismatched image
receipts refuse without partial text recovery. Exact digest checks guard discard;
cooperating writers lock the pinned directory so simultaneous saves cannot bypass
the 32-record catalog limit. Each record supports four images / 10 MiB each and
40 MiB aggregate image data with bounded metadata.

A loaded snapshot becomes an uncertain read-only fresh task with context inheritance
false. Former model/tools/access/preset/instructions are NOT applied; provenance
remains available for review. Nothing submits text, starts a child or authenticates
on save/load. Existing data is never overwritten by save. A post-publication fsync
failure can have an uncertain committed outcome; do not automatically retry/discard.
The host must run filesystem/codec work off the UI loop and separately review it.

This storage layer is **not yet wired into native application save/load/discard
controls or automatic draft persistence**. The existing `/agents drafts` catalog
still describes its in-process caches. A process roundtrip test verifies storage
and conservative recovery, but not an application restart/terminal save workflow
or physical power-loss/native-platform conformance. Those integration requirements
remain OPEN; storage code alone does not make current editor drafts durable.


### Explicit durable child-draft save/load/discard

In `/agents drafts`, **s** prepares a full reviewed save of the selected local
child draft. The disclosure includes task, provenance, old settings and image
digests; only physical Enter after all pages are painted admits the save. It
creates a private plaintext snapshot, never overwrites the source or executes work.
Coding stores these under its session directory's `saved-child-drafts`; Claw uses
its local user data directory's `saved-child-drafts`. The application parent
directory must exist; unsafe paths or unsupported private-storage primitives
refuse instead of silently falling back.

`/agents saved` explicitly reads saved snapshots off the UI loop. The draft
browser shows **SAVED PRIVATE** rows separately from CURRENT/OLD memory caches.
Saved rows cannot resume old child execution: reviewed Enter copies exact text
and images into a separate read-only start draft with context inheritance false.
No old model/tool/access configuration or permissions are adopted. **x** on a saved
row reviews deletion of that DISK snapshot only; independent local copies stay
editable. Deletion requires the exact saved digest and actual completion receipt.

One owned disk operation runs at a time; repeated actions refuse until settlement.
Graceful quit waits for its receipt rather than abandoning admitted work. A delayed
read cannot populate a replacement panel/context. Save failure keeps the local
source, while errors are redacted and uncertain post-publication effects require
explicit inspection rather than automatic replay. Disk operations/codec work run
outside UI/event loops. Loaded catalog images have a cumulative 40 MiB bound;
over-capacity reads refuse without partial recovery.

Both actual native owners are tested for reviewed save, NEW owner reload, exact
10 MiB image recovery, read-only copy, disk-only discard, unchanged parent draft,
failed save and stale-view refusal. Private-tmux workflows physically save/load
recovered image drafts and restore native cursor/termios. Separate storage tests
exercise process restart; these UI tests do not prove every platform filesystem.
**Autosave and general parent-editor persistence are not implemented by this explicit
child-snapshot feature.** Unreviewed edits remain memory-only until explicitly
saved; broader durability/platform/RPC requirements remain open.


### Saved-draft capacity recovery

`/agents saved list` now lists validated metadata (snapshot ID, provenance, image
count/bytes) without adopting drafts or retaining catalog image bodies. Records
are validated one at a time on the owned disk worker. This listing stays available
when loading all saved images would exceed the cumulative 40 MiB UI limit.

`/agents saved SNAPSHOT_ID` explicitly loads one complete snapshot for the existing
full-reviewed read-only recovery/discard browser. Invalid IDs refuse before disk
work; no URI/path is interpreted and other saved records remain unchanged. Bare
`/agents saved` retains the bounded all-snapshot load behavior. Neither catalog
listing nor single-ID loading executes agents, changes the parent draft or imports
old permissions; no oversized image is silently truncated. Autosave/general editor
persistence and broader remote/native-platform requirements remain OPEN.


### Nonblocking saved-draft worker settlement

Durable draft polling no longer joins a live worker on the UI thread. A queued
receipt remains owned until its worker actually exits, keeping the single-operation
capacity without stalling terminal frames. Failed thread startup releases ownership
and admits no disk operation; rejected delivery is not automatically replayed.
Explicit save/load/discard semantics and graceful-quit settlement remain unchanged.


### Bounded saved-draft lock contention

Saved-draft directory locking now uses nonblocking attempts with a three-second
deadline. If another cooperating process holds the lock, the operation refuses
without modifying snapshots instead of leaving save/load/discard and graceful
quit waiting indefinitely. The owned worker then reports its failure receipt;
retry is explicit after the lock is released. This bounds lock acquisition, not
arbitrary filesystem stalls or physical power-loss behavior.


### Coding RPC saved child snapshot discovery/read

Coding RPC independently negotiates `child_saved_drafts:1` alongside child controls.
Scoped `child_control` actions `saved_catalog` and `saved_read` expose the same
local private saved child snapshots as native `/agents saved`. Catalog responses
contain bounded metadata only. Reads require the exact `snapshot_id` and
`snapshot_sha256` captured from that catalog; stale/missing IDs or changed content
refuse without selecting a different snapshot or reading a supplied path/URI.

Image-bearing reads additionally negotiate `child_saved_images:1`. Exact bytes
travel through the existing checksummed binary begin/chunk/end stream, scoped to
session/revision/connection owner, snapshot ID/digest and a `saved-draft` source.
The final result contains conservative read-only/context-fresh draft metadata and
an exact image receipt, never large base64 bodies. Text-only reads need no image
capability. All filesystem/codec work runs off the RPC event loop and actual owned
workers settle on cancellation. Capability withdrawal/re-enable cancels in-flight
saved reads; stale owner generations cannot revive them.

Reading returns a copy only: it does not install parent/child history, enqueue or
submit work, invoke a model, remove snapshots or import prior access/model/tools.
Clients own review before any later submission. Tests include an actual offline
RPC subprocess reading 2 MiB, digest changes, missing image negotiation, revocation
and closed peers. Remote saved-draft save/discard, autosave/general editor durability
and remaining native-platform/host API gaps are still OPEN.


### Coding RPC two-phase saved snapshot deletion

RPC independently negotiates `child_saved_discard:1` alongside saved-draft access.
`saved_discard_prepare` requires the catalog snapshot ID/digest and returns a
30-second, connection/generation-bound `review_id` and exact metadata disclosure.
Preparation never deletes files. Clients must actually review that disclosure
before `saved_discard_confirm` echoes the exact `review`, `review_id` and boolean
`confirmed: true`. Truthy strings/numbers are not consent. `saved_discard_cancel`
revokes an unused review without changing the snapshot.

Confirmation is single-use; changed files, expired/foreign/cancelled reviews and
capability withdrawal/re-enable refuse. The actual disk removal rechecks the host
owner after acquiring its lock and validating the digest. Cancellation settles the
worker, not an automatic rollback of admitted deletion. Inspect uncertain outcomes
before explicitly preparing a fresh action. Only the saved disk file is deleted;
local editable drafts, queues and agent history are untouched. No model calls or
permission grants occur. Review capacity includes in-flight preparations and is
bounded to four. Remote save/autosave and broader platform/API gaps remain open.


### Coding RPC two-phase saved snapshot creation

RPC independently negotiates `child_saved_save:1` alongside `child_saved_drafts:1`.
`saved_save_prepare` accepts bounded `text` and an optional provenance `label`.
Images additionally require `child_saved_images:1`, supplied either as inline typed
`images` or completed session-owned `image_refs`, never both. No file/URI destination
or caller tool/model/access settings become execution authority.

Preparation owns an exact conservative read-only snapshot and returns its text,
image digests/MIME types, byte counts, SHA256 and PRIVATE PLAINTEXT disclosure with
a 30-second connection/generation-bound review ID. It publishes no file. Once
preparation succeeds, uploaded image references are consumed; the review retains
its own exact bytes. The client must separately review the receipt before sending
`saved_save_confirm` with that `review_id`, the exact echoed `review`, and actual
boolean `confirmed: true`. `saved_save_cancel` releases an unused review without
writing. Expired, cancelled, foreign or revoked reviews cannot publish snapshots.

Confirmation uses create-only private storage and rechecks live consent immediately
before publication. The review is single-use; failed/uncertain writes are not
silently retried. A post-publication flush failure may have committed: explicitly
inspect the catalog before preparing another save. No parent/child history is
changed, agent/model invoked or original upload turned into live queued input.
Review count is bounded to four, with a separate prepared-byte limit; native
save/load controls and their original private-path guards remain unchanged.

Tests include an actual RPC subprocess preparing and confirming a 2 MiB chunk-uploaded
image snapshot with zero model/history changes, exact-once review use, consent/
expiry/cancel/withdrawal checks and revocation at the publication boundary. This is
explicit RPC snapshot creation, not autosave, durable general parent-editor state,
physical client-review proof or remaining native-platform/API conformance.


### Opt-in native child-draft autosave

`/agents autosave on` enables session-local debounced child-draft snapshots in both
native clients. It is **OFF by default** and resets to off on application restart.
The command discloses PRIVATE PLAINTEXT local storage. `/agents autosave off`
stops future scheduling; an already admitted write still settles. This is not a
project-controlled setting, automatic submission or an execution permission grant.

After a 0.5-second debounce, unsent local child text/images are saved on the owned
worker. Later edits update only that owner's exact previous snapshot using a digest
check and private atomic replacement; externally changed files are not overwritten.
Failure disables autosave rather than automatically retrying uncertain writes.
Saved-catalog entries are not automatically resaved. General parent-composer text
is not included. Existing snapshot size/catalog bounds and private-path checks apply.

Recovery is explicit through `/agents saved` or `/agents saved ID` and its full
reviewed read-only/context-fresh copy workflow. Old access/model/tool configuration
never becomes recovered execution authority. Graceful quit requests a pending draft
flush and refuses exit while its worker is settling. An actual owned SIGKILL test
verifies a COMPLETED autosave survives, but edits during debounce/in-flight work
are not guaranteed durable. Clearing or submitting a draft does not automatically
delete its prior saved recovery copy; inspect/discard snapshots explicitly.

Tests cover both actual native owners, exact image/text persistence and same-record
updates, disable behavior, external mutation refusal, debounce/explicit flush,
independent-process restart and native terminal on/off controls. This implements
opt-in CHILD autosave, not general parent-editor durability, physical power-loss/
native filesystem conformance or remote autosave APIs. Those remain open.


### Autosave retained-memory accounting

Completed autosaves retain only the ID/digest update receipt, not a second decoded
text/image copy outside the editable-draft retention budget. Tracking for removed
local slots is released without deleting their saved recovery snapshots. A later
reappearing slot creates a new snapshot rather than overwriting an old recovery
copy. Existing explicit disk discard and autosave opt-in semantics are unchanged.


### Deferred quit preserves live UI services

A quit request deferred by an admitted durable draft write now leaves argument
completion and custom presentation/input services intact. Claw also retains its
owned name read. Teardown begins only when actual exit is admitted after write
settlement; repeated exit requests do not rerun cleanup. This keeps the still-live
UI usable instead of partially closing it while waiting for storage.


### Saved reload preserves editable-image ownership

Reload capacity now releases only the old catalog's exclusive image ownership.
Images still held by reviewed editable copies or an active child composer remain
counted when new catalog bytes are decoded. Reloads that would exceed local
retention refuse without replacing the existing catalog or losing editable copies;
exclusive old catalog bytes can still be replaced without false double-counting.


### Opt-in parent composer recovery

`/draft autosave on` enables session-local parent-composer snapshots; `off` stops
future saves. It is OFF by default and discloses PRIVATE PLAINTEXT local storage.
Debounced snapshots keep unsent text and image attachments separately from child
state, with the same owned off-thread writes, digest-bound updates, storage limits
and failure-stop behavior. Parent text supports up to 64 KiB; image limits remain
four per snapshot, 10 MiB each / 40 MiB total. The stored record capacity also
accounts for JSON-escaped Unicode text rather than truncating it.

`/draft saved [list|SNAPSHOT_ID]` explicitly discovers/loads parent snapshots.
Recovery has its own **Saved parent drafts** review: physical Enter after every
text/provenance/image page copies into an unchanged **empty** parent composer.
An existing or changed draft is preserved and recovery refused. Nothing sends the
prompt, invokes a model, changes history or imports former permissions. Parent
attachment ownership and all unrelated local image bytes remain counted; recovery
releases the temporary saved catalog after moving bytes into the composer.

**x** reviews deleting only the saved DISK snapshot; it does not clear current
composer text or attachments. A delayed read or changed review owner cannot adopt
old data into a replacement context. Graceful exit flushes pending snapshots and
holds while their actual workers settle. Snapshots remain available after submit/
clear until explicitly discarded; recovered text is never automatically replayed.

Both native owners are tested for text/image autosave, long Unicode saved recovery
with a NEW UI owner, changed-composer rejection, disk-only discard and zero model/
permission/history changes. Both private-tmux workflows perform full physical
parent-image recovery review and cursor/termios restoration. This is opt-in snapshot
recovery, not exact persistence of every keystroke: debounce/in-flight crash windows,
physical power-loss/native filesystem conformance, remote parent-draft APIs and
other host/platform interoperability requirements remain OPEN.


### Parent recovery view ownership

Parent snapshot reads are tied to the requesting auxiliary view. Closing that view
revokes delivery: a delayed disk result cannot reopen it or populate a replacement
view. Back/close and completed recovery release temporary read snapshots and saved
image catalogs without deleting disk files. Admitted disk operations still settle;
view cancellation is not a rollback or automatic retry.


### Parent recovery fences same-panel navigation

Saved-parent reads also bind to the requesting panel's view lease and child-view
identities. Switching to a child form, queue, preview or another draft browser
revokes late delivery even if the outer panel stays the same. Returning to the
roster does not revive the old read. Successful reads transfer their existing
composer/context receipt to the exact parent-recovery browser; stale callbacks
cannot copy or delete after navigation. Saved files and current drafts remain
unchanged, with no automatic read retry or focus takeover.


### Explicit parent draft save

Press **Ctrl+Alt+S** while the parent composer is focused to prepare a private
snapshot save without consuming its text or attachments. The native `/draft save`
control opens the same review for the current composer. The shortcut is preferable
when preserving an existing typed prompt: submitting a slash command normally
consumes that command's editor text. No autosave opt-in is required.

Every task/provenance/image digest page must be physically painted before Enter
admits the save. Changed composer text/images/cursor or a replaced view reject
old consent; pasted/repeated/unpainted Enter cannot confirm. Successful save
leaves the parent composer intact and creates one private snapshot with no model,
history or permission change. Confirmation is single-use; saved recovery still
uses `/draft saved` and the full reviewed empty-composer copy workflow. A failed
or uncertain write is not automatically retried. Existing autosave and native
private-storage limits remain unchanged.


### Parent save respects modal input ownership

The parent-save shortcut and explicit save review cannot take focus from an
active extension dialog, queued question, output review or pending output operation.
Coding also refuses while approval prompts/waiters own input. These guards apply
to stale review confirmation as well as initial shortcut admission; existing
composer text/images and permission decisions remain unchanged.


### Configurable parent-save shortcut

Parent save is now the `save_draft` action in `keybindings.json`, defaulting to
`ctrl+alt+s`. For example, `{"save_draft": "f8"}` rebinds it;
`{"save_draft": []}` disables the shortcut without a hard-coded fallback. `/keys`
shows the current binding. The action remains host-only, not editor submission,
and respects existing modal ownership and physical press/review guards.


### Read-only image preview in draft reviews

Press **p** in a saved/local draft list or full review to preview its images before
copying, saving or deleting. **Left/Right** selects attachments; **Escape** returns
to the same review. Preview performs no draft action, file deletion or agent call.
Returning resets review painting, so preview cannot count as consent or skip full
physical confirmation. A changed source invalidates preview/review rather than
applying an action against stale bytes. Both parent and child draft reviews use
this shared behavior. Linux/private-terminal evidence does not establish every
native desktop image protocol or provider/decoder interoperability.


### Parent recovery respects modal input ownership

Saved-parent read admission, delayed-result presentation and stale recovery copy/
delete callbacks now respect extension dialogs, queued questions, output reviews
and Coding approval owners. A read that completes under a new modal cannot take
focus or adopt a saved catalog. Reload explicitly after that owner settles;
composer text/images, permission decisions and disk snapshots remain unchanged.


### Save-and-quit completes after storage settlement

An explicit quit request deferred by draft storage now completes automatically
after all pending child/parent snapshots settle. The UI stays responsive and
input services remain live while waiting; edits made during that wait are flushed
before exit. A new storage failure cancels the deferred exit and keeps the UI
open for inspection instead of silently exiting with unsaved data. This fulfills
an explicit quit intent, not background submission, replay or a crash guarantee.

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
