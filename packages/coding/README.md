# xdog-coding

> Platform scope: **Linux only**. Non-Linux adapters and native-platform support
> are outside the current product scope; historical audit evidence is not a support promise.

**Interactive coding-agent CLI.**

A terminal coding agent with session management, built on
[`xdog-agent`](https://pypi.org/project/xdog-agent/) and the
[`xdog-tui`](https://pypi.org/project/xdog-tui/) rendering layer.

```bash
uv run xdog-coding
```

Resume a session from the current directory with `uv run xdog-coding -r`.
The selector lists all matching sessions, newest first. Use Up/Down, Page Up/Down,
Home/End and Enter to choose; Escape cancels. `--working-dir PATH` changes the
directory being listed. `--pick-session` opens the same selector.

`--resume-id ID` restores a session directly, including in non-interactive modes.
The selector shows current-directory sessions first, followed by older sessions
marked `[Unknown dir]` whose directory was never recorded. Choosing and resuming
an unknown session associates it with the current directory; canceling leaves it
unchanged. Sessions known to belong to other directories are excluded. Existing
saved model, messages and usage are preserved.

## Interactive terminal

The interface uses native scrollback, one status row, and a shared multiline
editor. Enter submits or queues; Ctrl+Enter inserts a newline. Ctrl+O opens
recent tool/reasoning details: Left/Right chooses an entry and Page Up/Page Down
scrolls. Escape closes autocomplete/details first, then cancels active work and
restores queued text ahead of the current draft. Ctrl+Z suspends on POSIX.
Ctrl+L repaints the visible UI without clearing scrollback, the draft, or focus.
The terminal cursor is visible in the focused editor and hidden while inspecting
details or approving a tool; returning to input restores it at the draft position.
Streaming updates are processed in bounded FIFO batches so input and rendering
continue to get a turn under sustained output.

Permission prompts take priority. Up/Down chooses an action, Enter confirms,
Escape denies that call, and Page Up/Page Down scrolls the command summary.
Confirmation requires visible action/context; tiny or clipped previews ask you
to resize. Space, modified Enter and reported key repeats never approve.
The editor draft stays intact when panels open or the terminal resizes.
Exit, EOF and terminal failures cancel owned agent/pre-turn work, discard queued
execution and close approvals; cleanup is not limited to keyboard callbacks.
Slash commands are serialized. Model/session/skill changes require an idle agent;
commands rejected while busy and messages submitted during compaction remain in
the draft. Escape cancels asynchronous command work. Standard console warnings
are rendered as sanitized UI messages rather than printing over terminal frames.
Transcript messages use shared TUI components: user text, Thinking, assistant
prose and tool previews. Thinking and prose remain separate message objects during
streaming; the transcript controls spacing and tools use a bounded branch preview.
User messages have a subtle background and one blank separator before replies.
The light theme uses a pale background; plain/NO_COLOR mode omits colors.
See [terminal verification](../../docs/tui-pi-parity.md); remote coding and
fullscreen application modes are excluded.

Details and permission prompts appear below the input. Coding shows the actual activity and
retains final tool durations. Queue/context metadata takes priority over long
model names; `/status` displays full model, session and working-directory data.
Context displays used tokens / prompt capacity, including cached input tokens,
even when the percentage would round to zero. Resume restores user text blocks
in both the transcript and input history, alongside saved model and usage.
Ctrl+Enter, Shift+Enter and Alt+Enter insert a newline when the terminal forwards
the modified key. If a desktop shortcut intercepts Ctrl+Enter, rebind that shortcut
or use Alt+Enter; the application cannot override desktop window management.

- `/details` or Ctrl+O opens a focused panel, including feedback when empty.
- Left/Right selects an entry; PgUp/PgDn reads pages; Home reads from the top.
- Details always open at the first line, including tool logs. Page Up/Page Down
  scrolls; End jumps to the latest entry/output and starts following new output.
- Thinking shows a two-row plain-text preview without opening details. Tool
  summaries keep multiline scripts on one header row; full commands are in details.
- Escape, Ctrl+C or Ctrl+O closes details and restores the draft/cursor.
- Permission panels show summary position and identify session approval as
  approval of the same call. Short terminals omit decorations and hints first.

Configure styling before launch:

```bash
XDOG_TUI_THEME=native uv run xdog-coding  # default: terminal-native body colors
XDOG_TUI_THEME=light uv run xdog-coding
XDOG_TUI_THEME=dark uv run xdog-coding
NO_COLOR=1 uv run xdog-coding             # unstyled content
XDOG_TUI_ASCII=1 uv run xdog-coding       # ASCII tool-state markers
XDOG_TUI_COLOR=16 uv run xdog-coding      # force basic ANSI colors
XDOG_TUI_COLOR=256 uv run xdog-coding
XDOG_TUI_COLOR=truecolor uv run xdog-coding
```

`XDOG_TUI_THEME=plain` also disables styling. `TERM=dumb` selects plain styling
and ASCII tool markers. Terminal-control sequences are still required for this
interactive mode; print mode is available for noninteractive output.

Color depth defaults to `auto`: `COLORTERM=truecolor/24bit` enables truecolor,
otherwise `TERM=*256color*` enables 256 colors, with a basic 16-color fallback.
The light theme supplies contrast-checked semantic colors for white backgrounds;
user-customized terminal palettes still need a visual check.

Compact tools use two text rows: name/state/duration/command, then an output
preview. Streaming previews retain the latest text; complete results remain
accessible in details. Rich image content may add rows. Permission prompts gain
a box when space permits, and shed it on short/narrow terminals.

The immutable UI run state tracks waiting, reasoning, responding, running,
permission and cancellation. Permission resolution restores the current work
phase; cancellation cannot be revived by progress events; completion clears
the timer. Worker locks and cancellation signals remain separate from UI state.

## Tool permissions

Potentially mutating tool calls are gated immediately before execution. By
default, read-only filesystem operations and `current_time` run automatically;
`bash`, filesystem writes/edits/deletes, and unknown extension tools ask first.

```bash
xdog-coding --permission-mode ask       # default: ask for dangerous calls
xdog-coding --permission-mode ask-all   # ask before every tool
xdog-coding --permission-mode allow-all # trusted unattended automation
xdog-coding --permission-mode deny      # allow read-only calls only
```

Interactive approvals offer **Allow once**, **Allow for this session** (the exact
same call only), and **Deny**. Non-interactive runs fail closed when no approver
is available unless `allow-all` was explicitly selected.

The same setting can be stored in `~/.config/xdog/coding/settings.json`:

```json
{"permission_mode": "ask"}
```

Permission prompts are an execution gate, not an OS sandbox: an approved shell
command still has the permissions of the `xdog-coding` process.

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

### Interactive file references

Type `@name` to fuzzy-search names under the session working directory, or use
`@src/`, `./`, `../`, `~/`, or an absolute path to browse a directory. Up/Down
selects a suggestion; Tab or Enter inserts it **without sending your draft**.
Directory selection continues browsing. Quote paths containing spaces, for
example `@"src/my file.py"`. Escape dismisses completion; Tab reopens it.
References remain prompt text, not eagerly read attachments or tool approval.

Name discovery and matching are asynchronous, resumable batches—not a total
20,000-file cutoff. A private SQLite index keeps candidate storage off the UI
thread; progress appears within the editor's row budget. Git supplies nested
`.gitignore`, repository-local and global exclude rules, even outside a Git
repository using private temporary metadata. Tracked names remain eligible when
ignored, but deleted names are removed on refresh. Untracked generated/hidden
paths and directory symlink traversal are omitted. Explicit directory paths
bypass ignore rules without bypassing tool permissions.

Results refresh after three seconds. Without Git (or if enumeration fails), the
UI explicitly reports a resumable basic-exclusion fallback; Git ignore semantics
are then unavailable. No repository hooks/fsmonitor commands are enabled and no
workspace/index metadata is modified. See `docs/agent-ui-gap-plan.md` at the
repository root for verification and remaining parity work.

### Keybindings and external editor

The live UI loads `$XDG_CONFIG_HOME/xdog/coding/keybindings.json` (or
`$CODING_DIR/keybindings.json`) on startup. It is a JSON object mapping action
names to one key string, a list of strings, or `[]` to disable the action:

```json
{
  "send": "ctrl+s",
  "newline": "alt+n",
  "details": "ctrl+t",
  "external_editor": "f4"
}
```

Overrides replace each action's default bindings. Conflicting, unknown, duplicate,
or malformed configuration is rejected as a whole with a UI diagnostic; defaults
remain active. Bare printable characters cannot be shortcuts. Escape and Ctrl+L
are reserved for contextual cancel/deny and recovery repaint; approval and dialog
navigation keys are not remapped. Modified keys require terminal reporting support.
Use **F1** or **`/keys`** to see the effective map, including editing, history,
undo/redo, kill/yank, interrupt, exit, suspend, details and editor actions. Restart
for configuration changes. Prompt hints show current bindings, not stale defaults.

**Ctrl+G** (by default) or **`/editor`** opens the composer in `$VISUAL`, then
`$EDITOR`, falling back to `vi`. Commands may contain quoted arguments; they are
not interpreted by a shell, and the temporary draft filename is appended. GUI
editors must be configured to wait for the file (for example their `--wait` flag).
Project settings and agent output cannot choose the editor command.

The external editor owns a cooked terminal while it runs. Its UTF-8 draft uses a
private directory and mode-0600 file, limited to 1 MiB and removed after exit.
Success updates the composer with undo support; **nothing is submitted**. Nonzero
exit, Ctrl+C interruption, invalid output or a missing program preserves the old
draft and cursor. Symlink/FIFO replacements are rejected. The TUI restores modes
even if the editor itself leaves raw input enabled, then re-anchors without
replaying native history or clearing scrollback. Editing requires prompt focus,
not a permission/details/overlay dialog.

Agent work can continue while the external editor owns the terminal; UI events
wait for the handoff to finish. Interrupting the editor does not undo tool effects
or cancel the agent: return to the prompt and use Escape for agent cancellation.
Linux/tmux behavior is automated; desktop editor/IME checks remain
manual and unverified.

### Steering, native follow-ups, and editable queues

While a turn is active, there are three explicit delivery choices:

- **Enter** keeps the existing local FIFO: a new full turn after the active SDK
  continuation settles, including normal prompt rebuild/compaction checks.
- **Alt+S** (default) or **`/steer TEXT`** queues text at the next SDK checkpoint.
  It does not interrupt an already running model/tool call or undo tool effects.
- **Alt+F** (default) or **`/follow-up TEXT`** queues text after the active tool/
  steering continuation, within the same SDK run.

These shortcuts are configurable as `steer`, `follow_up`, and `queue` actions.
**Ctrl+Q** or **`/queue`** inspects stable IDs, delivery lane and state. Local
next-turn IDs look like `q1`; runtime IDs look like `i1`. Use:

```text
/queue edit q1 replacement text
/queue remove i2
/queue move i3 up
/queue move q2 down
```

Moves operate within that delivery lane. A local edit applies under the queue
lock; runtime changes require an acknowledgement from the owning event loop.
Checkpoint-selected/consumed entries cannot be edited or removed. Replacement
text from a rejected edit stays in the draft. Queue inspection never resets the
active turn's timer. Edited queued text beginning with `/` remains literal agent
input, not an automatic slash command.

Acceptance means **queued**, not consumed. Consumption is reported only when the
input enters agent history. Cancellation returns only unconsumed runtime inputs;
these and local backlog are restored ahead of the live draft without automatic
resubmission. Idle/closing owners reject explicit delivery and preserve the text.
Local and identified runtime queues each permit 50 pending messages; runtime text
is limited to 64 KiB. Overflow rejects the new submission visibly, never drops old
messages. Tool approvals and Escape denial are unchanged.

These controls concern the primary composer/current parent run. The child-input
queue viewer is read-only; child queue mutation/recovery stays in the main-Agent/
SDK path described below.


### In-session browsing and conversation trees

**Alt+R / `/sessions`** opens a searchable, paginated browser while retaining the
composer draft. `/tree` opens the active session's branches and stable history
checkpoints. In either panel: `/` searches (atomic paste supported), Up/Down and
PgUp/PgDn navigate, Right enters a session/branch tree, Left goes back, Enter
activates, `r` refreshes, and `n` creates a new session. Escape first exits search,
then closes the panel. `sessions`/`tree` are configurable keymap actions; tree is
unbound by default to avoid stealing existing Alt+T custom shortcuts.

`/name` inspects the current session name; `/name NAME` sets a durable plain-text
name and `/name -` clears it. Names take precedence over automatic summaries in
session-browser titles/search; they never replace conversation content or model
instructions. Unicode is retained (256 UTF-8 bytes maximum); line/terminal controls
and bidi overrides are rejected. Naming requires an idle current owner, makes no
model/tool calls, and does not activate a session or rewrite its history.

Coding publishes a name only after its atomic session save succeeds and retains
the same access/extension persistence owner. Existing unnamed sessions remain valid;
model summaries, branches and subsequent metadata saves retain explicit names.

`/resume ID`, `/new`, `/fork [INDEX]`, `/branch [SESSION] ID`, and
`/goto [SESSION [BRANCH|-]] INDEX` expose the same operations. Message indexes are
one-based. Browsing is read-only and can coexist with an active turn; activation,
new sessions and branch/checkpoint changes require an idle agent and use the
owned command lifecycle. A rejected activation is not silently queued for later.

Switching retains saved context/model/reasoning/instructions, rebinds built-in
workspace tools and file completion, rebuilds project context, and clears old
session approvals/active skills/queues without relaxing the current permission
mode. Old session heads are persisted before switching. Branches record parent
lineage; navigation saves a return checkpoint before replacing the current head.
Tool-call/result batches cannot be split into invalid provider context. Session
writes are atomic private temporary-file replacements; traversal IDs/symlink
session files are refused. These are persistence guards, not OS isolation.

### Real images and explicit clipboard paste

`/attach PATH` reads a PNG/JPEG/WebP file into the editable draft (quote spaces).
`/attachments` lists stable IDs, names, dimensions and sizes;
`/attachments preview ID`, `/attachments remove ID`, and `/attachments clear`
inspect or change the draft without sending. The bounded preview uses terminal
image support when available and metadata fallback otherwise; Escape returns to
the draft. An active approval keeps priority over previews and clipboard results.

**Ctrl+V** (configurable `paste_clipboard`) or `/paste` explicitly reads the local
clipboard off-thread. Linux Wayland uses `wl-paste`; X11 uses `xclip`.
Missing helpers, unsupported formats and timeouts
are visible; ordinary terminal/bracketed text paste remains available. There is
no background clipboard polling. Late reads after submission remain in the new
editable draft, never auto-send; explicit `/attachments clear` discards pending
read results too.

Enter can submit an image-only draft. Up to **4 images**, **10 MiB each**, and
**40 MiB retained** across draft/queues are accepted, subject to stricter model
limits and an advertised image-input capability. Unknown/text-only capabilities
reject visibly and preserve the draft. Validation checks MIME/header/dimensions
(maximum 100 megapixels), not a full decoder or a security boundary. CLI `--file`
image inputs also become typed image content in print mode rather than path
placeholders. Image bytes are persisted in session history: treat those files as
sensitive.

Local queue edits/reorders and explicit steering/follow-ups retain their image
parts. `/queue` shows attached-image counts; removing a queue item releases its
attachments. Rejected/unconsumed inputs and failed turns preserve recoverable
bytes for explicit inspection/retry. Images from a completed turn are not returned
just because a later queued turn failed. Recovery never silently resends; a failed
provider input may already be in context, so inspect before retrying. Normal input
caps can be exceeded only by lossless recovery; remove excess before resubmission.
Linux/private-tmux and controlled clipboard helpers are tested; desktop clipboard,
Other Linux terminal preview behavior remains
unverified; controlled helper tests do not
prove native-platform compatibility.

### Transcript search, selection and optional fullscreen

**Alt+H / `/transcript [QUERY]`** opens the full retained transcript browser.
It reads source text, not Markdown's rendered layout or truncated tool previews:
user/assistant prose, reasoning, complete tool output/arguments and system notices
remain searchable. Tool arguments follow the existing presentation/redaction
policy; image bodies are not converted to visible base64. Search is literal and
Unicode-case-insensitive, with **every non-overlapping occurrence** navigable,
including repeated hits on a single long line. Selected matches pan horizontally
when needed.

- `/` edits the query; Enter finishes query editing without submitting a prompt.
  **n / Shift+N** selects next/previous match, wrapping across the retained history.
- Up/Down, PgUp/PgDn and Home scroll source lines; Left/Right selects an adjacent
  source entry. **Shift+Left/Right** pans horizontally.
- **v** begins/clears a whole-line range selection; navigation extends it, and
  Shift+Up/Down can start a range. **a** selects the complete current source entry,
  including lines outside the viewport. Escape clears a selection before closing.
- **r** refreshes an immutable source snapshot while retaining the source position
  where possible; refresh clears selection. **End** refreshes and goes to the
  latest retained source. Streaming continues normally; the browser does not
  silently change selected source text underneath you.
- **f / `/fullscreen`** toggles an alternate-screen transcript viewport using the
  full terminal height. `/transcript fullscreen`, `refresh`, and `close` are also
  available. `transcript` and `fullscreen` are configurable keymap actions; the
  fullscreen shortcut is unbound by default.

The default application still uses **native main-buffer scrollback**. Fullscreen
is opt-in and temporary, retains the original main-buffer painter, and re-anchors
its restored cursor after resize rather than replaying history. Draft/cursor,
queued images, active work and timers stay owned by the application. Escape or
Ctrl+C closes the viewer, not the agent. Approvals leave fullscreen, take focus,
and return to the inline browser after denial/resolution; fullscreen does not
resume automatically. Exit/EOF/failure restore terminal modes and screen ownership.
The viewer has no tool execution, session mutation, clipboard upload or network
operation. Copy/export/share remains separate work in the gap tracker.

Search covers retained client-visible context, including restored session history;
use `/sessions` to activate an archive. Hidden runtime instructions, other sessions
and binary image data are not silently exposed. Native terminal mouse/text
selection is unchanged; browser range selection is whole-line/entry based. Linux
private-tmux resize/screen-return/approval behavior is tested; desktop terminal,
Other Linux desktop/IME manual validation remains open.


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

### MCP stdio tools (first transport slice)

Global `settings.json` and project `.coding/settings.json` accept `mcp_servers`;
project entries override the same global server ID. Example:

```json
{"mcp_servers": {"local": {"command": "python", "args": ["/trusted/server.py"], "timeout": 30}}}
```

Configuration alone never executes a server. **`/mcp connect local`** explicitly
launches its configured argv in the session workspace—trust/review project commands
before doing this. `/mcp` opens the browser; `/mcp list` shows connection/protocol/tool status;
`/mcp refresh local` rediscovers schemas, and `/mcp disconnect local` releases the
owned process. Connection/schema changes require idle agent/command ownership and
run off the terminal loop. Workspace/session switching closes old connections,
removes old proxies and reloads layered project configuration. Exit requests owned
cleanup. Status does not print environment credentials.

Discovered namespaced tools enter the existing single SDK agent, not a second loop.
The normal permission hook still gates every external tool; untrusted MCP
`readOnlyHint` annotations cannot make it auto-approved. Unattended default/deny
modes do not execute these tools; `allow-all` remains the explicit exception.
Timeout/disconnect/late responses never automatically replay a call. See the Agent
README for current/legacy negotiation, schema validation, configured Roots and
limits. Explicit public-PKCE OAuth and inert context controls follow; removed
optional client input/task families are not pending supported features.

`mcp_servers` now also supports explicit current/legacy HTTP endpoints:

```json
{"mcp_servers": {"remote": {"transport": "http", "url": "https://host.example/mcp",
  "headers_env": {"Authorization": "MCP_AUTH_HEADER"}, "timeout": 30}}}
```

The selected environment value is the full header value (for example, a Bearer
header), not printed in status. `/mcp connect remote` reuses the same idle-gated
management, SDK proxies and approval policy as stdio. HTTP JSON/SSE decoding,
metadata/header mirrors, legacy session lifecycle and explicit auth/redirect/errors
are documented in the Agent README. No calls are automatically replayed or tokens
refreshed. Explicit public-PKCE OAuth, credential storage and inert resource/prompt
controls are documented below. Configured static/environment credentials remain
separate and cannot be mixed with the OAuth Authorization header.


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

Coding reads run off the UI thread and cancel when the browser closes or is
replaced. Connection/schema mutations retain the existing owned command lifecycle.

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

**Sampling, Elicitation and MCP Tasks are removed** from SDK/native/gateway
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

The builtin **`plan`** skill, supplied by `xdog-skills` (install the
`skills` extra), uses ordinary conversation or task documents
for objectives, dependencies, acceptance criteria and evidence-backed progress.
It does not create a separate plan database, inspector, execution mode or model
loop. The former `/plan`, `PlanPanel` and `update_plan` UI/tool APIs are removed.
List it with `/skills` and activate it with `/plan [request]`.

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

Access state survives session/branch restore. Historical saved planning state is
read only for compatibility: a retained restriction is not silently cleared by
navigation, and malformed state fails closed. These are host policy gates, not an
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

Coding project/session settings cannot enable extensions; global/user configuration
is the trust source. Its regular approval mode still gates plugin tools, even after
activation, and read-only read-only still blocks them under `allow-all`. Workspace/
session switching closes the prior owner and clears old status/registrations.


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

Coding saves entry snapshots with its session JSON and branch metadata. Resuming
invalid entries refuses the session instead of falling back to an unrestricted
replacement. Context contributions are not persisted as user chat messages.


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
Timeout, abort, navigation, disconnect and shutdown settle/cancel pending futures;
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

`/agents start TASK` starts host-managed read-only child work. `/agents start --inherit
TASK` inherits the current host policy; it does not copy approval grants or bypass
read-only. Inspect with `/agents list` or `/agents inspect ID`; use `steer ID TEXT`,
`follow-up ID TEXT`, `cancel ID`, `wait ID` and `close ID` under `/agents`.
Parents also receive the reserved unified `agent` tool; children cannot
recursively delegate through their parent's supervision tools.

Children share the workspace, so coordinate conflicting writes. Context is copied
at admission through a complete tool/result checkpoint. Model/options/tool execution
mode are inherited. There is no fixed concurrent-child or retained-record count
limit, elapsed-time deadline, model-call cap or cumulative reported-token stop
threshold. The main Agent/host manages lifecycle with cancel/wait/close. Usage
counters remain reported usage, not exact billable accounting; actual model
output/context limits, permissions and message/input/image/per-file capacities
still apply. Complete history uses atomic segmented persistence instead of a
cumulative 512 KiB / 2,000-message execution cutoff; tool/result pairs are not
silently truncated.

Coding and Claw child factories explicitly enable the Agent's existing built-in
compaction policy through `AgentConfig(compaction=selected.compaction(child_stream))`
and provide `archive_compaction=store.archive_compaction` to the supervisor.
This does **not** change the generic Agent default: automatic compaction is off
unless explicitly configured. The selected helper uses the executing adapter's
real context/prompt/output capacity; unknown metadata, context capacity or output
reserve returns `None` (auto-compaction remains off), not a guessed fallback.
A host requiring automatic compaction must reject that case or explicitly supply
trusted capacity/reserve through native `CompactionConfig`.

With the policy enabled, one long tool turn can compact repeatedly at safe paired
boundaries. The supervisor waits for a durable full pre-compaction snapshot before
the native summary request starts. Archive, summary or stream-cleanup failure does
not commit a history cut, and cancellation settles owned writes/cleanup. The native
summary's per-request timeout/capacity checks remain; there is no replacement child
lifetime cap. Inspect shows the current retained context, possibly with summaries;
`children.store.compaction_archives(child_id)` reads full earlier snapshots without
replay. These child archives are separate from the parent session's `/tree`
compaction checkpoints.

Each child has independent Coding approvals and extension workers/state. Parent
validation/context hooks and current read-only/permission gates remain authoritative;
rewritten effective arguments are checked before host approval. Approval panels
identify the child, queue concurrent questions, and preserve the editor draft.
Active children block navigation/policy/schema changes until canceled or settled.
Interrupt cancels children; exit joins ownership. Atomic session/branch-scoped child
sidecars retain final results and extension entries; interrupted work is never
restarted automatically. Closed results remain archived on disk. Corrupt catalogs
visibly disable delegation but do not disable the parent conversation. Shared parent pre-hooks remain serial; inherited validation
uses no-UI canceled dialog values rather than another run's interactive lease.

`/agents` opens a live roster; Enter or `/agents inspect ID` opens complete retained
child source with `/` search, navigation, selection, `f` optional fullscreen and
explicit copy/export/share review. Escape returns to the roster and parent draft.
`n` creates a task, Tab selects inherited access, `s`/`t` steer/follow-up, `c` cancels
and `x` archives, with visible specific confirmations. A pending wait does not block
cancel controls. `l` enables live snapshots without overwriting search/selection.
Source conversion runs on one coalescing worker; output review is child-scoped.
Complex extension forms are removed;
child extension dialogs use independent child/parent-session/UI leases, never another run's question owner.
Trusted Python callbacks remain unrestricted code; none of this is an OS sandbox.


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


### Child specialization and protocol limits

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
with child results and appear in complete child source. Runtime budget options
`--timeout`, `--calls`, `--tokens` and config/preset keys `timeout`, `max_model_calls`,
`max_tokens` are removed and rejected for new starts. Historical child records with
these budget fields remain readable; the fields are inert and never reapply a
budget or restart work. This removal does not remove the actual model's per-request
output token ceiling.
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
contexts use the same built-in text-token estimate as native compaction, rather
than comparing text bytes to token capacity. This is not exact tokenizer accounting.
Host-enabled compaction runs before forwarding the next model request; context that
cannot be safely compacted or fit still fails explicitly with original history
retained. Image/capability and actual output limits remain enforced. Parent
model/options/settings/grants never change. Named presets use explicit user configuration.


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
        "label": "Reviewer"
      }
    }
  }
}
```

Browse `/agents presets`, or use `/agents start --preset reviewer --label Audit --
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

SDK hosts use `manage_auth("remote", "refresh_prepare")` followed by
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

Bounded JSON tool details now survive save/reload/branches in both clients, preserving
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
SDK artifact_bytes/artifact_from_bytes/read_artifact expose the same validated codec.
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
policy. Print mode reports a refused input as a nonzero error. No synthetic assistant answer is fabricated.

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
not fabricated assistant prose or tool approval. Permission, host read-only, tool and model
capability gates are unchanged; returned command-looking feedback is never executed.

Print mode outputs explicit feedback and returns success for handled-only input;
JSON mode emits a valid system/input_handled envelope (optional real assistant output
is separate), not a counterfeit model message. Claw sends a content-free final
signal after actual handled feedback, releasing run ownership without synthesizing a response. Other
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

Active resource/catalog/combined notification watches are removed from the SDK,
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

SDK callers can opt in with `McpManager(..., credential_dir=...)` and prepare/confirm
`credentials_save`, `credentials_restore`, or `credentials_forget` through the
existing owned `manage_auth` API. A manager without a credential directory remains
memory-only. Actual client restart/consent and private-tmux tests cover the file
backend. File permissions are not an OS sandbox.

#### Opt-in Linux Secret Service storage

Set `"mcp_credential_backend": "secret-service"` in your global
`~/.config/xdog/coding/settings.json`. Project and session settings cannot override
this global storage choice. The default remains `"file"`.

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
retain existing child context/vision/reasoning/budget guards and parent tool
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

### Explicit context compaction

Use `/compact [focus instructions]` to summarize older conversation context while
retaining a complete recent user turn, including its tool exchanges. The optional
focus is limited to 16 KiB. The summary uses the **selected session model adapter**
(including registered extension providers), not a primary-provider fallback, and
its request exposes no tools. Current model context/prompt limits determine the
retained history budget. Too little safely separable history is an explicit no-op.

Compaction requires an idle session and idle children. The editor draft and existing
permission mode remain unchanged. Empty, incomplete, canceled, or failed summaries
are not substituted into history, and a persistence failure is not reported as
successful compaction. Access and extension metadata owners retain the compacted
history for subsequent writes.

In the TUI, Escape or `/compact cancel` cancels an active compaction command. Once
an atomic history commit has been admitted, cancellation settles that disk write
and in-memory publication rather than pretending to roll it back. If cancellation
or disconnection loses a receipt, inspect `/transcript` before retrying; the command
is not automatically replayed. Native scrollback remains the record of what was
previously displayed, while the transcript viewer can read the current context.

Trusted extensions may observe, veto, or provide a summary through
`api.on("session_before_compact", callback)`, and observe committed context through
`api.on("session_compact", callback)`. This applies to manual and automatic
compaction; callbacks receive complete typed preparation rather than a UI preview.
Custom summary boundaries must preserve complete recent user/tool turns. Bounded
source/details metadata persists with the session and its branches. Hook callbacks
cannot borrow command/dialog/navigation authority or change permissions through
compaction results; see the Agent package's compaction extension contract.

### Recovering context before compaction

Every successful manual or automatic compaction now saves a named **Before
compaction #N** checkpoint containing the complete original typed history. The
checkpoint and shortened active context are stored in one atomic session write;
failed saves cannot publish a shortened context without its recovery data.
Compaction does not automatically activate the checkpoint or change permissions.

Open `/tree`, search for `Before compaction`, and select the checkpoint to restore
its conversation. Restoration retains a return checkpoint of the compacted head,
so it is reversible. The existing transcript viewer can then search/copy/export
that restored full history. This changes conversation context, not workspace files;
restoration still requires an idle owner and preserves the normal permission mode.
An incomplete tool-call tail is retained in recovery data but cannot be resumed
as a complete branch: drill into the tree and choose an earlier valid checkpoint.

Checkpoint records retain model/settings, read-only state, extension entries, and
prior compaction metadata. Committed compaction metadata and extension post-events
also carry an optional `recovery_branch_id` linking to that original preparation.
Repeated compactions keep separate checkpoints rather than replacing the old one.

### View saved context without activating it

In `/sessions` or `/tree`, select a saved session/branch and press **v** to open a
read-only source viewer. **Enter** remains the separate activation action. You can
also use:

```text
/transcript branch BRANCH_ID
/transcript saved SESSION_ID [BRANCH_ID]
```

The viewer searches full saved text, reasoning, and tool input/output—not only
navigation previews. It does not resume the saved conversation, call a model/tool,
change the active branch, or modify the parent draft. Binary bodies/signatures are
not displayed, and normal source redaction applies. Current-session entries without
a branch open the ordinary live viewer; other saved sessions use persisted source.

Refresh explicitly with **r** or `/transcript refresh`; **f** toggles fullscreen.
Copy/export/share uses the displayed saved snapshot and the existing reviewed output
workflow, never the live conversation as a fallback. Loading/failed/retired reads
cannot be copied or exported. Closing returns to the existing draft; reopening
`/transcript` without a saved selector opens current source again.

### Model selection uses the owning adapter

`/model` lists canonical streaming-chat IDs from the same session adapter that
executes requests, including active registered extension providers. It does not
assemble a second catalog from the global AI runtime. Explicit selection resolves
through that adapter and persists the canonical ID before publishing model,
context/prompt limits, tool/vision metadata, and effective reasoning settings.

Unavailable, non-chat, non-streaming, or invalid cached profiles are refused without
switching the active model. Unsupported advertised reasoning efforts are not
silently substituted; select a supported effort or `off`. Selecting a model that
does not advertise reasoning clears the effective effort. Failed setting saves
leave model/options/settings/capabilities unchanged. Native saved-session/branch
profile and effort checks run before current context and owned resources are replaced.

Startup/resume no longer silently substitutes the first model for a missing requested
or saved native profile. With no configured model, default selection must find a real
streaming-chat profile; an empty catalog is an error, not a guessed `sonnet` request.

An explicitly supplied SDK stream function without model metadata remains an opaque,
caller-owned adapter. Its explicit IDs/options can be delegated to that function,
but selection does not borrow capability claims from the global runtime or the
previous model: model/vision metadata stays unavailable, tool support is unreported,
and context estimates use the documented host fallback rather than a claimed profile.
Extension aliases still require their active, explicitly trusted worker declaration.

### Prepare saved extension providers before navigation

Resume and branch restoration now prepare trusted extension workers against the
target workspace, settings, read-only state, and saved extension entries **before**
replacing the current conversation or its resource owners. A saved provider alias
must be declared by that prepared target runtime; the current runtime's catalog is
not used as a substitute. Declared effort/capability checks still apply.

Preparation has no UI/dialog/navigation lease and buffers host-managed entry writes.
A missing provider, failed activation, unsupported effort, canceled preparation, or
failed target save leaves the current session/Agent context, extension workers,
MCP manager, children, read-only owner, permission mode, and draft intact. Buffered
setup entries are not written into the saved target on failure. Successful activation
adopts the prepared workers once, binds their entries/inspection to the live owner,
and publishes model/history/settings together after the atomic target save. Branch
activation retains a return checkpoint in that same committed session snapshot.

Callbacks receive `context["staged"] == True` during preparation. This is an
informational lifecycle flag, not a permission grant. Extension code is explicitly
trusted code with the process user's permissions: arbitrary filesystem/network or
other external side effects cannot be rolled back by the host. Only host-managed
entries/session publication is buffered. The synchronous SDK uses one owned bootstrap
thread rather than nesting `asyncio.run`; the interactive main terminal loop stays
responsive. Cancellation before commit discards the stage; a commit already admitted
to atomic storage may complete, so inspect current state before retrying a lost receipt.

### Exact slash-command submission

Enter submits a completely typed registered slash command as written, even when
completion also offers a longer name—for example, `/session` does not become
`/sessions`, and `/ext-demo-show` does not become `/ext-demo-shows`. Matching is
case-insensitive, like command dispatch. Suggestions may still be browsed with
Up/Down and explicitly accepted with Enter, or inserted with Tab; incomplete-prefix
completion keeps its existing behavior.

Editing, history changes, and catalog changes clear prior neighbor-selection intent.
A command withdrawn while it is being typed is not silently reinterpreted as a
neighbor; the dispatcher reports its availability normally. A stale withdrawn
suggestion is not submitted. File/path completion remains separate: selecting a
file still inserts a reference without submitting a conversation.

### Search the session's combined history graph

Use `/transcript timeline [SESSION_ID]` to view the current context and every saved
branch of that session together, without activating a branch or sending a model turn.
The normal full-source search, fullscreen, selection, and reviewed output workflow
remain available. Current-session head data is captured from the admitted live Agent
history; inactive sessions use persisted data. Refresh explicitly to capture updates.

This is a provenance graph, not a guessed timestamp chronology. Records shared by a
recorded parent prefix or a verified compaction recovery link appear once and list
all contributing source labels/IDs. Different roots, repeated later turns, divergent
forks, changed protocol/model/content, and different tool results stay distinct even
when their visible text happens to match. Compaction-retained history must match its
original checkpoint before it can share identity; mismatched links fail the read,
never silently omit content. Binary/signature payloads are not displayed, but their
raw identity participates in lineage checks; normal source redaction applies.

Capacity failures report unavailable rather than exporting a clipped graph: at most
256 sources including the head, 20,000 messages per source, 200,000 raw references,
and 20,000 unique formatted records. Inspect individual branches when that capacity
is exceeded. The viewer leaves active context, permissions, branch and draft unchanged.

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

The ordinary main-Agent `agent` tool retains `action="queue"` with a typed
JSON `text` request. SDK `ChildSupervisor.control("queue", ...)` also remains.
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
An embedder owns later adoption/submission and its policy. Inspect prior history
and workspace effects before deciding whether to run a recovered copy.

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

Offline evidence includes both actual app/UI owners with file reads and 10 MiB
payloads, the real Claw socket upload path, 2 MiB chunk uploads, queue settlement/
reload, mismatched-receipt draft retention,
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
`agent` wrapper returns typed images plus receipt/count metadata. The
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
a new session/branch/connection context keeps only that current cache and drops
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
The main Agent can use its ordinary permissioned `agent` tool with
`action="queue"` and a JSON `text` request containing `return_prepare`,
`return_commit` or `return_cancel` plus the required input/revision/receipt fields.
Preparation returns typed image content and bounded token/receipt metadata; it
never dequeues and does not embed raw base64 into model-visible prose.

The tool supplies its private nonserialized supervisor owner; models cannot pick
one. Cancellation/abort/close revoke that owner. The existing token expiry,
selection/revision/receipt checks and retained-byte limits remain. Durable removal
and return-backup writes keep their existing owned settlement/no-automatic-replay
rules. A generic SDK embedder still owns typed-history retention, persistence and
ordinary tool permission hooks. A process-local token is not a promise of
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
The main-Agent wrapper emits typed images and bounded receipts; an SDK embedder
owns retention and adoption. Native child edits remain same-session transient;
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

## Supported Coding entry modes

Coding supports the native interactive TUI and non-interactive print mode. The
former `--rpc` JSON-lines frontend mode and its public `run_agent(rpc=...)` argument
are removed, including remote draft, history/image, progress, editor/session/host,
and custom-UI protocols. These removals do not remove internal Agent events, local
history/export/persistence, native drafts, or the ACP/session controller and durable
automatic inbox described below. Unknown `--rpc` input is rejected before startup.
Private extension-worker RPC, MCP JSON-RPC and ACP remain separate retained
transports; removing the frontend mode does not remove those protocols.

## ACP session routing, automatic inbox and batched turns

`--session ID --prompt TEXT` sends through ACP to the exact live owner without
constructing another Agent. It differs from `--resume-id ID` (explicit history
restore), rejects inactive/unknown/cwd mismatch, and cannot override target policy.
`--acp` is the standard stdio adapter; `--acp --session ID` fixes a bridge to an
existing owner. Ordinary native/print/navigation usage does not require optional ACP
activation; the pinned SDK currently supports Python 3.12–3.14 only.

Callbacks use an explicitly configured **ACP endpoint + session**, not a required
coding CLI subprocess or TUI event host. The independent lightweight ingress
(`python -m xdog.coding.acp.ingress_main --help`) receives into a durable per-session
inbox even when every TUI/owner is closed. It does not create an Agent or silently
restore history. Starting/stopping the ingress is explicit and independent.

`/inbox list [OFFSET]` and `/inbox show ID` inspect without scheduling work.
Every external ACP prompt/callback first enters the same durable Inbox. An
activated owner automatically consumes pending messages, whether they arrived
online or while all owners were offline. There is no message-approval dialog,
human wake-up budget, or approval-bearing ACP resume extension. `/inbox cancel ID`
targets pending intake; `/inbox retry ID` explicitly retries interrupted work while
retaining its old attempt facts. Actual tool calls still use the existing policy:
read-only calls may run automatically, restricted calls need approval, and no
approver means fail-closed unless the user explicitly selected `allow-all`.

`max_messages_per_turn` defaults to **10**, accepts only integer **1..128** (not
booleans), and follows explicit SDK override > saved session > project > global >
default precedence. Five inputs remain five independent history messages, with
separate IDs/sources/typed attachments, but one shared Agent turn and response/tool
stream. Setting 1 restores single-input selection. Selection is fixed; later
arrivals wait for the next turn, and byte/context/image limits can narrow a batch
without dropping messages. TUI live/replay shows shared output once.

A shared batch cannot withdraw an already admitted message from model context:
cancel/disconnect ends that peer's waiter, not the other members' execution.
Running crashes become interrupted and are not replayed automatically. SDK/print/ACP
report finalization or persistence failures as errors, not blank success.

Optional `--session ID --enqueue --message-id STABLE_ID --prompt TEXT` writes data
without ACP/model startup, with a durable JSON receipt, not an execution result.
It cannot combine with model/permission/resume overrides. Enqueue ACK loss permits
same-ID/content dedup retry; standard prompt outcome-unknown never does. There is
no automatic harness/observer startup or exactly-once side-effect guarantee.

The old event-host package/environment/capabilities are removed, while stored
`xdog.external_event.v1` history remains readable. New external model input is
`xdog.session_input.v1`; a nonsecret `XDOG_SESSION_ID` tool-environment hint follows
session navigation without granting permissions. See the [full protocol and
receiver setup](../../docs/acp-session-routing.md).

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

## Session ownership and reliable shutdown

Coding's TUI, print, SDK and ACP ingress share one session controller and Agent.
Persistent writer locks live under `<sessions>/.owners`; they do not depend on
`TMPDIR`. Short private Linux Unix socket/discovery files are separate, under
`/tmp/xdog-coding-<uid>/<store-hash>`. Stop older Coding processes before upgrading:
old binaries do not participate in this store-anchored writer-lock protocol. Do
not run mixed old/new writers against the same session store.

SDK applications should `await session.aclose()` in `finally`. Closure is
idempotent, waits for actual child/Goal/extension/MCP cleanup, and reports cleanup
errors only after attempting the other resources. Cancellation of a waiter does
not abandon the underlying close. `session.dispose()` requests the same close
and returns a concurrent Future; synchronous applications can wait on `.result()`.
Do not block an asynchronous caller loop with `.result()`; await `aclose()` instead.
Synchronous construction and controls can be called with `asyncio.to_thread` when
used from an async application. A receiver accepting a queued message does not
require message approval; existing approval applies to tool execution only.

Automatic project instructions come **only** from the working directory's
`AGENTS.md`, `.coding/INSTRUCTIONS.md` and `.coding/instructions.md`. There is no
parent walk. Other harness configuration files are not automatic instruction
sources; they remain ordinary files that users can explicitly read or attach under
normal file-access and permission rules. There is no filename denylist.
Each automatic source must be a regular UTF-8 file of at most 64 KiB; symlinks,
special files and unreadable/oversized sources cause an explicit preparation error. Missing files are optional. These
instructions are refreshed in owned off-loop preparation before a turn, reach the
actual model prompt, and never grant tool permissions. The Agent alone renders
skill indexes and session/turn-scoped bodies; loading a skill starts no observer,
model, receiver or daemon.

`CreateSessionOptions.thinking_level` is honored, including explicit `"off"`.
Initial `allowed_tools` configuration is a **tool selection**, not authorization:
absent/null falls through to a lower layer, `[]` selects no tools, and a nonempty
list selects those configured names. Caller options/overrides take precedence
over saved explicit choices, then project and global configuration. Choices are
not unioned. Unavailable saved names do not create tools or gain authority later;
normal permission checks still apply to the selected tools.

The separate input history ledger has a common **64 MiB** read/write bound.
Admission reserves final-state, batch-ID and bounded diagnostic space for every
unfinished input; if it cannot fit, it is refused before acknowledgement. Errors
retain up to 4096 characters with an explicit truncation marker; batch IDs are
limited to 256 UTF-8 bytes. Accepted execution facts are never silently deleted.
At capacity, explicitly start another session rather than automatically archiving
or replaying requests. Recovery marks unfinished execution interrupted and never
replays potentially executed tool effects automatically.

Ordinary turn history uses one immutable, original-owner disk commit, off the
model loop, before publishing saved state and completing producer receipts.
File replacement synchronizes the file and parent directory. A failed or
uncertain history commit retains the live executed tail and fences further turns;
close and explicitly reload/inspect the saved state before continuing. Concurrent
metadata revision conflicts fail closed instead of overwriting newer facts.
These are local filesystem/process guarantees, not hardware power-loss
exactly-once execution or an OS security sandbox. No existing user data is migrated
or discarded automatically.

Enabled extensions may declare a public slash name. The packaged `goal_ui`
display exposes `/goals [GOAL_ID | page OFFSET]`; default native `/goal` manages the current Goal.
This is generic extension routing, not a Coding-specific Goal command. Public
names appear in help/completion and cannot shadow builtins or other owners; native installed capabilities
may explicitly overlap only their own skill command entry, without preventing skill loading;
model tool names and permissions do not change. See
`docs/extension-public-commands.md` for the shared Coding/Claw contract.


### Installed capability code boundaries

Trusted installed native capabilities use generic owner, lifecycle event, ACP
routing and receipt ports. Coding/Claw default dependencies include xdog-skills:
`/goal` commands and the model tool are available without a display-worker setting
or Goal enable flag. The packaged skill remains discoverable/loadable. No Goal is
created merely by installation or ordinary chat.

Goal owns one current binding/history, task files and ACP reminders. The current
main Agent plans, executes itself or uses existing child tools, updates tasks and
judges completion according to the skill. No Goal-owned worker/reviewer scheduler
or independent acceptance protocol remains. Tasks/*.json are authoritative, not
projections of duplicate database task state. Pauses and user/host waits cannot be
released by an automatic message. Existing readonly/permissions/approval checks
remain in force. Eyes watches only the generated task directory configuration;
stopping Goal automation does not cancel unrelated observers or accepted work.

See `docs/goal-simple-design.md` and its requirement/verification record. Earlier
capability-boundary and Goal reviewer lifecycle evidence describes its old frozen
implementation, not the current Goal contract.
