# xdog-coding

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
Linux/tmux behavior is automated; desktop editor/IME and Windows checks remain
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

### Repository change review

**Alt+D** (configurable `review` action) or **`/diff`** opens a read-only change
browser rooted at the session's Git repository, including files outside a nested
working directory. `/diff refresh` refreshes an open browser; Escape/Alt+D or
`/diff close` closes it without submitting/clearing the draft or canceling the agent.

- Up/Down or Left/Right: changed file; **`/`**: filename filter (Enter accepts,
  first Escape exits filtering).
- **`s` / `w`**: selected file's staged / working patch; untracked files have
  working previews, not staged patches.
- **`n` / `p`**: next/previous hunk; PgUp/PgDn and Home/End: patch pages;
  Shift+Left/Right: horizontal movement.
- **`r`**: refresh snapshot; **`d` / `b`**: inspect an initialized nested
  repository/submodule and return to its parent scope.

Git discovery/diff runs off the UI thread. Patches are disk-paged rather than
truncated to a small fixed preview, with sanitized controls and four-cell tabs.
Binary/non-regular untracked entries get explicit summaries; symlink targets are
shown without following them. The browser has **no stage/apply/revert/commit
operations**. External diff, textconv, fsmonitor, hooks and pagers are disabled;
inspection does not modify Git index/worktree contents or call a model.

Review uses a snapshot: changes made during agent work need `r` to refresh. A
permission prompt takes priority, and denial/completion restores the open review
and draft. Private temporary patch storage is removed when the browser closes.
Git/OS failures and timeouts show feedback; Linux/tmux is verified, not Windows
or desktop terminal/IME behavior.

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
the same plan/extension persistence owner. Existing unnamed sessions remain valid;
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
clipboard off-thread. Linux Wayland uses `wl-paste`, X11 uses `xclip`, and macOS
uses the native `osascript`/AppKit image/text snapshot adapter described below.
`pbpaste` is a text-only fallback when that native helper is absent.
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
Kitty/iTerm preview, the actual macOS AppKit bridge/desktop and Windows remain
unverified as noted in the gap tracker; macOS image adapter contract tests do not
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
Windows and IME manual validation remains open.


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
README for current/legacy negotiation, schema validation and limits. OAuth,
richer schemas and further negotiated capabilities remain open;
this is not a declaration that the whole MCP gap row is complete.

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
refreshed. Interactive OAuth, richer management and resource/prompt UI remain open;
only configured static/environment credentials are currently supported.


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

Coding applies this restriction even with `allow-all`; entering execution does
not change the selected permission mode or grant new session approvals.

Metadata writes are serialized, committed before publication and settled during
cancellation before ownership is released; cancellation cannot undo an already
committed metadata update. Plan changes are idle-only, preserve drafts, and never
bypass approval/output-review focus. Claw-specific transport/compaction
behavior is documented in its README. Desktop/platform validation
remains limited to the Linux/private-tmux evidence in the goal tracker.


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

Coding project/session settings cannot enable extensions; global/user configuration
is the trust source. Its regular approval mode still gates plugin tools, even after
activation, and read-only planning still blocks them under `allow-all`. Workspace/
session switching closes the prior owner and clears old widgets/registrations.


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

`/agents start TASK` starts bounded read-only child work. `/agents start --inherit
TASK` inherits the current host policy; it does not copy approval grants or bypass
planning. Inspect with `/agents list` or `/agents inspect ID`; use `steer ID TEXT`,
`follow-up ID TEXT`, `cancel ID`, `wait ID` and `close ID` under `/agents`.
Parents also receive reserved `agent_spawn`/`agent_control` tools; children cannot
recursively delegate or update their parent's plan.

Children share the workspace, so coordinate conflicting writes. Context is copied
at admission through a complete tool/result checkpoint. Model/options/tool execution
mode are inherited. Defaults are three active children, 16 retained records,
120-second execution deadline, 16 model calls and a 32,000 reported-token stop
threshold—not exact billing. Output limits are clamped; input accounting can
overshoot on the final request. Cleanup/persistence may outlast the execution deadline.

Each child has independent Coding approvals and extension workers/state. Parent
validation/context hooks and current planning/permission gates remain authoritative;
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
Arbitrary richer child forms and platform validation remain pending;
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

### Prompt terminal-input hooks (Coding)

Trusted extensions can register a raw prompt-input hook:

```python
def setup(api):
    def terminal_input(data, metadata, context):
        if metadata["kind"] == "paste" and data == "shortcut":
            return {"data": "expanded draft text"}
        return None
    unsubscribe = api.on_terminal_input(terminal_input)
```

Callbacks may be async. `None`/`{}` continues, `{"consume": True}` consumes,
`{"data": "..."}` replaces input, and the returned unsubscribe is idempotent.
Hooks run serially in explicitly trusted workers after activation (for example,
`/extensions`); existing lazy startup activation is preserved. Metadata identifies `key` or
atomic `paste` and the `prompt` surface. Protocol negotiation, cursor reports and
clipboard replies are filtered **before** interception; paste boundaries cannot
be transformed into raw keys.

Only editable prompt input is hookable. Approval prompts, extension dialogs,
custom/child views, output viewers, overlays and other host surfaces retain their
input. Original submission controls keep FIFO behind held text without visiting a
hook. Escape, Ctrl+C/D/L/Z and host shortcuts bypass delayed hooks immediately.
Transformed keys are revalidated against the **current** keymap: they cannot
synthesize submit/steer/follow-up, approval, exit, external-editor or other host
controls. Callbacks cannot borrow dialogs, command actions or a model loop.

Input waits asynchronously with 32-item/128-KiB queue bounds, a 16,000-byte
UTF-8 per-item limit and a five-second host deadline. An oversized intercepted
paste is refused; pause hooks before pasting a larger payload through the normal
editor. Session/focus/physical-size changes, interrupts and failures cancel held
input rather than applying it to a successor surface or submitting a partial
draft. Retained originals are bounded to 32 items/128 KiB, and hooks pause on
rejection/interruption so recovery remains accessible.

**Alt+I** (`terminal_input` in `keybindings.json`) is a host-owned pause/resume and
recovery shortcut. `/terminal-input list`, `restore ID`, `discard ID`, `clear`,
`pause` and `resume` inspect/manage originals. Restore inserts original text into
the editable draft; it never replays raw controls, imports an attachment, calls a
hook, grants a permission or submits a model turn. Control-only records can be
inspected/discarded, not replayed. This supersedes the earlier Coding raw-key
exclusion above. Claw now wires its corresponding negotiated parent-prompt gateway
path. Independent child supervision-editor interception and native-platform
validation remain open; this is not a claim of full Pi/Codex parity.

Worker-selection generations also fence reload/unload/failure/close boundaries.
A held callback cannot dispatch into a newly selected worker or quarantine its
replacement. Retiring an old MCP/extension connection cannot remove successor
registrations or tools.


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
successful compaction. Plan and extension metadata owners retain the compacted
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

Checkpoint records retain model/settings, planning state, extension entries, and
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
target workspace, settings, planning state, and saved extension entries **before**
replacing the current conversation or its resource owners. A saved provider alias
must be declared by that prepared target runtime; the current runtime's catalog is
not used as a substitute. Declared effort/capability checks still apply.

Preparation has no UI/dialog/navigation lease and buffers host-managed entry writes.
A missing provider, failed activation, unsupported effort, canceled preparation, or
failed target save leaves the current session/Agent context, extension workers,
MCP manager, children, planning owner, permission mode, and draft intact. Buffered
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

### RPC lifecycle and shutdown ownership

`--rpc` owns stdin as a JSON-lines control stream. Startup emits `ready` without
first reading the pipe as a one-shot prompt. Each RPC prompt runs through the same
`AgentSession.send_message` lifecycle as other modes: extension turn/message/tool
hooks, selected adapter/compaction, persistence and skill retirement are retained.
The RPC adapter subscribes only to translate events; it does not implement another
model/tool loop or silently change permissions.

EOF, quit and explicit abort cancel both pre-turn work and the actual streaming
producer, settle owned cleanup and deny pending approvals. An explicit abort may
be followed by status/a fresh prompt without borrowing the previous cancellation.
Invalid configuration requests report protocol errors and leave the reader usable.
A partial failed provider result retains content with `message_end.is_error=true`
and an `error` event, never a success-only completion. Headless handled-input receipts
remain separate from assistant output. Existing wire delta fields are unchanged.

The opt-in RPC extension control/dialog subset below is now implemented. Native
terminal rendering, navigation mutation and independent-child RPC UI are
separate remaining integration requirements; Claw gateway or interactive tests do
not imply those RPC APIs. OS sandboxing is not added by approval/cancellation.

### Opt-in RPC extension controls and dialog replies

`ready.capabilities` advertises supported protocol fields. Old clients remain
headless unless they explicitly negotiate exact integer capabilities:

```json
{"type":"rpc_hello","extension_dialogs":1,"extension_forms":1,"extension_multi_forms":1}
```

Use the session ID from `ready`; each control/negotiated prompt requires a fresh
request ID. Control IDs are consumed once (256 per connection), not replayed:

```json
{"type":"extension_control","request_id":"inspect-1","session_id":"SESSION","action":"command","name":"ext-example-ask","args":""}
```

Actions are `list`, `reload`, `unload`, `command`; management names must already
be globally trusted. Controls are idle-only and mutually exclusive with parent
prompts/policy changes/active children. The normal SDK/runtime owns activation,
validation, callback execution, persistence and tool permissions. No second loop.

A scoped `extension_dialog` event carries `session_id`, `request_id`, `scope`,
and `dialog.id/kind`. Supported kinds are select/input/confirm and separately
negotiated typed forms/multi-choice forms. Copy ALL owner fields back verbatim:

```json
{"type":"extension_dialog_response","session_id":"SESSION","request_id":"inspect-1","scope":"SCOPE","id":"DIALOG","value":"Blue"}
```

Replies are schema-validated, single-use and tied to the original pending broker;
wrong/late/foreign/duplicate replies are refused. Tool approval remains ONLY the
existing `permission_response` channel—answering a confirmation/form never grants
approval, changes permission mode, or submits a model turn. Unsupported/expired/
withdrawn questions return cancellation, never silently inferred defaults. Typed
false/zero/null and arrays remain distinct. Status remains readable during a dialog.

`extension_control_cancel` carries the original request/session IDs. EOF/quit/
explicit cancellation settle the actual pending worker (including pre-admission
cancellation) without replay. Normal RPC CLI shutdown also awaits extension process
cleanup before exit. `extension_result` reports success/cancel/error and current
validated catalog; rich command content is inert returned data, never executed.

With dialog negotiation enabled, parent prompts also require request/session IDs,
allowing tool/lifecycle questions to use the same scoped broker while the reader
continues accepting approval and dialog replies. With no UI negotiation, existing
prompt packets are unchanged. The host serves read-only session/model/usage/name
inspection for command callbacks from the actual owner; native browser/name
mutation and custom terminal components remain unavailable instead of borrowing
a remote client's unsupported surface. This is not Pi wire compatibility or full
RPC host-API parity; remaining original requirements stay open.

### Typed RPC image prompts

`ready.capabilities.image_prompts: 1` advertises protocol support, NOT that the
selected model accepts images. Send an inline image array on a prompt; image-only
prompts may omit/empty the message. No RPC file path or URI is fetched implicitly:

```json
{"type":"prompt","message":"Inspect the image","images":[{"type":"image","data":"BASE64","mime_type":"image/png"}]}
```

`type="image"` is optional; data/mime_type are required and all other fields are
refused. Inline attachments use the existing neutral `ImageContent`/SDK path and
selected model's advertised image capability/count/byte/media limits. PNG/JPEG/WebP
only, at most four attachments and 10 MiB decoded bytes per image (or lower model
limits). Invalid base64, MIME mismatch, unsupported models/limits and malformed
arrays produce explicit errors, never a silently text-only turn or provider
fallback. These are shared SDK/base64/MIME checks, not a full raster decoder or
OS sandbox. Binary contents remain typed in persisted history and are not echoed
as prose in JSONL replies.

Binary admission checks run off the RPC reader loop so status/abort remain usable.
After turn-start hooks refresh selected provider metadata, image admission is checked
again BEFORE compaction/history/model work; the same race guard applies to Coding
interactive and Claw sessions. A hook withdrawing vision cannot admit a formerly
accepted attachment. Negotiated RPC UI prompts use the same typed image path and
still require their request/session owner fields.

RPC retains inline JSONL images and now also supports the chunked transport below.
Native POSIX input is byte-framed before JSON parsing; alternate custom/unsupported-
platform input readers must honor sized reads and have no equivalent native proof.
Complete rendered-context checks use actual adapter metadata where available; absent
metadata/limits, decoder/provider and native desktop conformance remain open.

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

### Bounded RPC input and chunked image references

`ready` advertises `capabilities.image_uploads: 1` and `max_input_frame_bytes`.
Native POSIX stdio uses an owned asynchronous byte reader with a **64 MiB** JSONL
frame bound (preserving four legacy 10 MiB inline images plus JSON overhead).
Oversized records are drained/rejected as WHOLE records; suffix fragments never
become commands. Invalid UTF-8 is an explicit protocol error, and the next framed
record remains usable. An unterminated record still needs its delimiter/EOF; this
is framing/resource ownership, not an OS sandbox or a claim of instant parsing.

Upload base64 chunks with the original session ID and a fresh connection-local ID:

```json
{"type":"image_chunk","session_id":"SESSION","id":"image-1","index":0,"mime_type":"image/png","data":"BASE64_CHUNK","final":false}
```

Indices are exact nonnegative integers, ordered from zero. Each encoded chunk is
at most 100,000 characters; final is a real boolean. The final chunk includes
`sha256` over ALL decoded image bytes. `image_upload_result` reports partial/ready/
error and index. Limits: 16 active uploads, 10 MiB per decoded image, 40 MiB aggregate
decoded cache bytes. PNG/JPEG/WebP header/dimension admission precedes ready;
there is no URI/file loading. No disk storage, model work or permission changes
happen on upload. The shared assembly helper stays in `xdog.ai`; each application
retains its own ownership/header guards instead of Coding importing Claw policy.

```json
{"type":"prompt","session_id":"SESSION","image_refs":["image-1"]}
{"type":"image_discard","session_id":"SESSION","id":"unused-image"}
```

Choose inline images OR references, not both. References require completed uploads,
are single-use once a valid prompt lease is admitted, and are retired after use or
explicit discard (4,096 connection identities including active IDs). Invalid owner/
header/duplicate/incomplete references never silently resolve another image. Busy
or malformed prompt requests do not consume a valid cached reference. Session
replacement evicts cached images; EOF/quit releases all buffers. Lost outcomes are
not replayed; re-upload deliberately under a new ID for another attempt.

The caller should read acknowledgments between chunks and may interleave status or
control frames. Model capability/byte/count/media and complete-context admission
still happen through the existing SDK. Cache limits count decoded bytes, NOT total
process RSS/encoded-history memory. Alternate-platform/custom reader cancellation,
provider/raster conformance and goal-wide integration remain separately unproven.

### Client-owned RPC editor CAS requests

Negotiate `extension_editor: 1` independently of questions/forms:

```json
{"type":"rpc_hello","extension_editor":1}
```

Interactive extension COMMAND callbacks may use existing `get_editor()`/
`set_editor(text, revision)` APIs. The host emits the ordinary scoped
`extension_dialog` envelope with `dialog.kind="editor"`, operation read/replace
and (for replace) the original revision/text. Answer on `extension_dialog_response`
with ALL original session/request/scope/dialog identities. A read snapshot is:

```json
{"text":"client draft","cursor":12,"image_count":1,"revision":"64_HEX_REVISION"}
```

A replacement receipt is `{"applied":true,"reason":"..."}` or an explicit false
conflict. The remote client owns its ACTUAL editor, cursor and attachment identities.
Use `xdog.tui.editor_actions.EditorActions` or an equivalent implementation: capture
those values under the original RPC owner, generate a fresh opaque revision, then
apply only if that owner/revision/full state still matches. Invalidate on human edit,
caret/attachment/view change and owner retirement. Reply true only AFTER actual
CAS application; never apply returned text as a command or auto-submit a prompt.

The host validates copied snapshot/receipt fields and scopes, remembers only the
latest served revision per extension/command, consumes it once before forwarding
replace, and refuses fabricated/retired reads. It DOES NOT maintain a fake editor
mirror or claim it can prove a remote screen/client's correctness. Capability
withdrawal, EOF/quit/cancel invalidate pending receipts without replay.

Editor-only UI negotiation also requires request/session IDs on parent prompts,
but only COMMAND callbacks get editor authority; tools/hooks/custom/prompt callbacks
cannot borrow it. Questions/forms need their own separate bits. Native terminal
renderers, RPC navigation/name mutation and independent-child editor surfaces stay
open—not implicitly supported by this editor transport. Permissions, canonical
history/model and uploaded/composer image ownership are unchanged by text CAS.

### Native RPC session catalogs and navigation primitives

`ready.capabilities.session_controls: 1` and `ready.session_revision` describe the
native owner-backed controls. Every request needs a fresh ID and the actual current
session from ready/status/the last navigation receipt:

```json
{"type":"session_control","request_id":"list-1","expected_session":"CURRENT","action":"list","query":"name","offset":0}
{"type":"session_control","request_id":"resume-1","expected_session":"CURRENT","action":"resume","target_session":"TARGET"}
```

`list`/`tree` provide the same native Coding session/branch/checkpoint metadata as
the terminal browser, with bounded catalog pages/filter/membership revisions.
`catalog_revision` resumes a page; `session_revision` is the separate conversation
lease. Reads run off the reader loop, do not activate an Agent/model, and never
export full history/image data as metadata. The current tree is persisted native
source, not an invented live streaming checkpoint.

Idle-only primitives: `resume TARGET`, `new`, current-session `fork`, `rename`,
and combined `branch`/`goto` selections. Ready capabilities include
`session_checkpoint_targets: 1`: `branch` accepts `target_session` plus `branch_id`;
`goto` accepts `target_session`, optional `branch_id`, and `index`. Empty target
means the current session. RPC `index` is ZERO-BASED, as reported by catalog checkpoints (distinct
from terminal `/goto` numbering). Restore uses existing SDK staging/model validation,
retained return heads/tool boundaries/resource/permission gates. A failed fork save
does not publish a phantom branch; a failed new-session preflight removes only its
unchanged unadmitted candidate, never independently modified target data.

Successful mutations report the ACTUAL session/model/head and a fresh
`session_revision`. After using mutation controls, prompts, extension controls and
native model/thinking/compact controls must include `session_id` plus that current
revision. Stale/legacy owner-less packets cannot silently operate on the new head.
Status/settings acknowledgments expose current revision. Old clients not using
navigation retain their earlier prompt behavior.

`session_control_cancel` carries original request/expected-session IDs. Its
`session_cancel_requested` acknowledgment means requested, not rolled back. The
existing disk/preparation owner settles before another control can start. Admitted
commits report actual state with `receipt_canceled`; unadmitted stages retain the
head. Status stays responsive with a stable pre-publication snapshot while the
thread owns restore work; it does not claim a half-published new model/context.

Cross-session branch/checkpoint selections now use one staged SDK admission:
`AgentSession.navigate_checkpoint(session_id, branch_id=..., index=...)`. The selected
branch's history/model/settings/plan/extension state is preflighted before one target
save/publication; the target's unrelated live head is never activated first. Failed
preflight, invalid tool boundaries or failed target save retain the original owner
without saving phantom checkpoint branches. Native `/branch SESSION BRANCH` and
`/goto SESSION [BRANCH|-] INDEX` use the same operation; a saved return checkpoint
preserves the target's previous live head. Same-head `/goto` also stages its checkpoint
before persistence rather than saving a branch before attempting activation.

This is atomic application-level admission, not a multi-file/OS power-loss transaction.
Outgoing history is persisted separately and admitted cancellation settles actual
state, not invented rollback. Retained live/saved/timeline display-source replay and
scoped child supervision are available below; raw binary/canonical, richer custom/
terminal, unclean-crash queue recovery and transient UI artifact surfaces remain OPEN. This is not a claim of all API parity, an OS sandbox or
a complete goal. No returned extension command text is executed to navigate.

### Reviewed extension navigation over RPC

Negotiate `extension_navigation: 1` independently of questions/forms/editor controls.
A trusted extension command may use `api.navigate_session(...)`; the host sends a
scoped `extension_dialog` with kind session, operation navigate and the EXACT typed
target (action/session/branch/checkpoint). The remote client must visibly review that
target under the original owner and answer a strict boolean, with all original IDs.
This is not tool approval or arbitrary returned-text execution.

`True` means **queued after successful callback**, NOT activated. The host queues a
target only after its broker future ACTUALLY yields true, not merely because a late
transport answer was acknowledged. Callback failure/cancel, lost owner/revision or
withdrawn capability discards it. On callback success, an independently correlated
`extension_navigation_queued.navigation_request_id` maps to the native
`session_result`; existing staged restore/model/idle/child/permission/lease checks
still determine whether that later mutation commits.

The same actual-future rule now governs RPC editor-read revisions: an expired/
canceled read cannot create a usable host lease merely from a transport ACK. Remote
client rendering remains its responsibility; an opted-in capability is not proof of
an arbitrary screen's correctness or permission to change tools/models.

Cross-target combined branch/goto reviews use that same single staged admission,
not resume-then-restore. Missing branches and invalid checkpoint/metadata selections
are refused before review, and selection plus owner/capability are checked again after
actual acceptance. Final native model/extension/workspace preflight still controls
commit. `fork` remains a save of the current live head only, never a hidden cross-target
operation. Terminal custom views, richer independent-child RPC surfaces and fuller
reference/native-platform/atomic-root evidence remain OPEN; child supervision/source/
question transport is described below. Pass 143 separately covers the
native Coding/Claw late-answer navigation/rename grant race; RPC correctness alone
is not proof of other native/gateway races.


### Complete read-only RPC transcript sources

Ready advertises `transcript_sources: 1`. Explicitly negotiate it with
`{"type":"rpc_hello","transcript_sources":1}`; only the exact integer enables
content reads. This flag is independent of extension questions/editor/navigation:
a source-only hello does not withdraw an existing extension capability. The
`transcript_capabilities` response reports enabled state and transfer limits.

```json
{"type":"transcript_read","request_id":"source-1","session_id":"CURRENT","session_revision":"CURRENT_REVISION","scope":"saved","target_session":"SAVED","branch_id":"ARCHIVE"}
```

- `scope: "live"` (default): a captured tuple of current admitted messages plus the
  actual partial response, without reloading stale disk history. Target must be
  current and branch empty. Later admitted messages require a fresh read.
- `scope: "saved"`: the exact saved session head or named branch. No activation,
  provider/model/compaction call, workspace switch or current-head fallback.
- `scope: "timeline"`: complete recorded branch/head provenance for the target,
  including verified parent prefixes and compaction recovery links. For the
  current session the head is captured live; other heads are saved. No branch
  argument or content-only deduplication. The wire branch is `@timeline`.

Reads require a fresh bounded request ID (256 per connection), exact current
session and conversation revision, and no admitted navigation owner. One worker
settles preparation/spool cleanup before starting the latest replacement; one
pending request coalesces newer reads. Replaced, canceled or withdrawn requests
cannot silently replay. A successful mutation/revision change invalidates an
unfinished source. Status and tool-approval controls remain independently readable.

After `transcript_queued`, the server sends `source_begin` (byte count/SHA256),
ordered `source_chunk` records (base64 of up to 64 KiB bytes), then `source_end`
(chunk count). The payload is UTF-8 JSONL records with id/role/title/body, not terminal
screen lines or model/tool execution instructions. Every frame carries the original
request/session/conversation lease, target session, branch/scope and an integer
snapshot ordinal (`revision`, distinct from `session_revision`). Assemble and verify
all frames/checksum before publishing records or enabling search/selection/export.
`source_error` invalidates any partial assembly. No client may treat queue/transport
acknowledgments as a completed source or navigation/tool approval.

The shared `xdog.tui.source_receiver.SourceReceiver` supports this framing. Call
`expect(request_id, "", session_id, target_session_id=..., branch_id=...,
session_revision=...)` and construct it with the negotiated `max_bytes` bound.
Its completed records feed the existing TranscriptBrowser/TranscriptPanel and
captured-text output helpers; those components keep drafts and native scrollback
separate. Claw's existing import remains a compatibility export of the same receiver.

Display sources preserve full text, reasoning and joined tool-call/final results,
with terminal-control sanitation and heuristic credential masking. Binary image
bodies, reasoning signatures, system prompt, and opaque custom metadata are NOT
exported as prose; images receive explicit omission markers. This is complete
retained DISPLAY text, not raw canonical context/binary export or a reconstruction
of every transient notice/custom/child/live-tool-progress surface. Redaction is not
a confidentiality guarantee. Message/timeline limits remain those of native readers;
RPC transfer capacity is 128 MiB and oversize fails explicitly, never truncates.

`transcript_cancel` uses the original request/session/revision. The
`transcript_cancel_requested` receipt requests cancellation, not completion. EOF,
quit, capability withdrawal and replacement settle the existing preparation owner
and close its private spool; connection closure never invents a completed snapshot.

Native POSIX pipe/socket/terminal RPC output now has one ordered nonblocking writer.
Source frames await actual writes so a slow peer cannot queue an entire transcript
in memory or block the command reader. Canceled producers cannot abandon half a
JSON line while the connection remains live; output failure wakes the input owner.
Ready reports `rpc_async_output` and a 64 MiB output backlog bound (whole-frame
refusal, no truncation). Shutdown drains briefly then releases a stalled writer and
restores descriptor flags. Custom sinks/regular files/unsupported platforms retain
synchronous compatibility (`rpc_async_output:0`); that is NOT native Windows/macOS
cancellation or interoperability evidence. No OS-enforced sandbox is implied.


### Scoped child supervision over Coding RPC

Ready advertises `child_controls`, `child_sources`, `child_approvals`, `child_dialogs`,
`child_forms` and `child_multi_forms`. Independently negotiate exact integer 0/1
flags via `rpc_hello`; none borrows extension/editor/navigation or transcript consent.
Child-only hello leaves parent extension capabilities intact. `child_controls:1`
is required to use this surface. `child_capabilities` returns the actual current
`session_id`, `session_revision`, connection-scoped `child_owner`, and native
session/branch supervisor `child_epoch`. Copy those FOUR fields into every child
control, source or reply. Refresh after navigation; old epochs/owners are refused.

```json
{"type":"rpc_hello","child_controls":1,"child_sources":1,"child_approvals":1,"child_dialogs":1}
{"type":"child_control","request_id":"start-1","session_id":"CURRENT","session_revision":"CURRENT_REVISION","child_owner":"UI_LEASE","child_epoch":"SUPERVISOR_EPOCH","action":"start","task":"Inspect the tests","access":"read-only","config":{"label":"Reviewer","max_model_calls":4,"max_tokens":8000,"inherit_context":false,"tools":["filesystem"]}}
```

`child_control` actions: `list`, `inspect` (bounded redacted roster metadata, NOT
raw canonical history), `presets`, `configure` (resolve native preset/config without
starting), `start`, `steer`, `follow-up`, `cancel`, `wait`, and `close`. Existing
CodingChildren/ChildSupervisor owns actual agents, captured context, independent
extension workers/permissions, downward tool/model/budget limits and durable records.
There is no second tool loop, recursive spawn, provider fallback or fabricated model.
Configuration is the native ChildConfig schema; it cannot set access/permissions or
include reserved delegation/planning tools. `access` is separately read-only by
default; explicit `inherit` retains current parent gates, never copies its approvals.

Start requires an idle parent/extension/control/context owner. Parent prompt and
context-changing RPC controls cannot borrow a pending admission. Once admitted,
existing supervised children can run alongside the parent. Four control jobs and
256 fresh request IDs per connection are bounded; source IDs have their existing
separate bound. `child_status` notifications are marshaled from the child owner loop
onto the RPC reader loop and carry scope plus redacted roster metadata. Do not rely
on a status/question arriving after the start receipt: child startup is genuinely
concurrent. `child_result` correlates the original control and reports actual outcomes.

`child_control_cancel` matches the ORIGINAL request and all four scope fields.
`child_cancel_requested` is only a request acknowledgment. Canceling a `wait` job
does NOT cancel the child; use control action `cancel`. A canceled start settles
its exact admission and cancels that admitted child, never guessed catalog members.
Close archives the existing record rather than restarting it. Effects already
admitted are not rolled back/replayed. EOF, quit or output failure stops the parent
first, denies pending child approvals, settles control jobs and closes/joins all
connection-owned child supervisors/workers before disposing the session.

`child_sources:1` enables `child_source_read` with fresh request ID, child ID and the
four scope fields. It uses the same complete verified source protocol/byte bounds
as transcript reads, but scope `child` and the envelope adds `child_id`, `child_owner`,
`child_epoch`, and captured `child_revision`. It is independent of parent
`transcript_sources` consent. Shared SourceReceiver.expect also accepts the three
child fields to reject mixed-child frames. `transcript_cancel` must copy the
original parent/revision AND child ID/owner/epoch for these source requests;
a parent-only or foreign-child cancel is refused. Sources include actual canonical child
text, partials, bounded live tool progress with explicit capacity markers, effective
model/config and input-handler feedback as SYSTEM data (not a model answer). Final
canonical results win over older progress. Original native child retention/budget
limits remain; no raw binary/signature/private model header export is implied.

`child_approvals:1` enables `child_permission_request`, carrying child-specific
request ID, actual tool/arguments/summary, all four scope fields and
`approval_generation`. Reply with `child_permission_response`, those ORIGINAL
fields, child ID, ID and decision `allow_once`, `allow_session` or `deny`. Parent
permission responses cannot settle these child futures. No child borrows parent
session rules or another connection's requests. Approval withdrawal clears pending
requests/rules and changes generation: an already resolved approval held before
final tool admission cannot revive by toggling the UI back on. Current parent deny,
read-only child access and parent planning restrictions still veto mutations.
Unattended mutation remains possible only with explicitly selected allow-all policy.

`child_dialogs` enables child input/select/confirm questions; forms require
`child_forms` and multi-choice fields require `child_multi_forms`. Reply via
`child_dialog_response` with original scope, child ID, host dialog ID and typed value.
These are task data, NOT tool approval. Withdrawn/unsupported pending dialogs cancel
at the native broker even before UI publication was delivered. Repeated, expired or
foreign answers cannot reuse another future. Editor/session navigation/rename/custom/
terminal callbacks are refused, not forwarded as parent command authority.

This implements real supervision/source/question/approval transport, not an arbitrary
client's physical review/rendering, richer child terminal/custom APIs, unclean-crash
input recovery, deeper delegation, native-platform verification or whole-goal parity.
Live editable child queues are described below.


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


### Native Coding live image return

Coding's child input queue now supports **u** for live image inputs. It requires
**two independent full physical reviews**: first prepare and verify the byte copy
without dequeuing; then a visibly labelled **SECOND removal review** before
committing removal. Unpainted/repeated Enter or pasted newlines cannot skip either
review. Cancel during the second review releases its lease and leaves the original
input queued. Selected/edited/expired/stopped inputs still fail on the owning loop.

Before presenting removal consent, the verified image/text copy is stored as a
separate conservative read-only local backup, marked uncertain and available in
`/agents drafts`. A lost/mismatched commit receipt therefore cannot lose the only
local copy. This memory backup is NOT crash/power-loss persistence. After an exact
successful removal receipt, a separate child steering/follow-up draft owns the
same image bytes; no parent text/images are borrowed, no sibling draft overwritten,
and no child/model turn starts until explicit submission. The backup remains
available for inspection/discard because transport outcomes may be uncertain.

Coding's native worker binds return leases to the exact panel AND queue-view owner,
checks that owner at actual runtime admission and fences late results from a replaced
queue view. Auxiliary queue Escape does not detach a commit already settling;
forced closure still leaves its prior local backup accessible. Four images/10 MiB
per-image and shared local retention limits remain; a failed backup allocation
refuses removal rather than dropping bytes.

Tests cover actual native Coding 10 MiB commit/cancel, exact precommit backup,
unchanged parent/history/model-call count, mismatched receipts and a real private-
tmux image submit → queue → preparation review → second removal review → child
image draft → cursor/termios recovery path. Claw's equivalent native socket return is implemented in the following section,
with its own negotiated connection/view lease and verified transfer wiring.
RPC uses the previously documented explicit client-owned contract. Durable backups,
remaining native-platform/interoperability and whole-goal parity stay OPEN.


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

### Independently negotiated canonical RPC transcript export (UI-038, partial)

The RPC `ready` envelope advertises `transcript_canonical: 1`. A client must
explicitly negotiate the exact integer with `rpc_hello`; `transcript_sources: 1`
only authorizes the existing **display** source, not binary export. Send
`transcript_read` with `format: "canonical"`, the current `session_id` and
`session_revision`, a fresh `request_id`, and the existing live/saved/timeline
scope and target/branch fields. Default format remains display. Revoking one
format does not revoke the independently authorized other format.

Canonical `source_begin/chunk/end/error` frames carry `format: "canonical"` and
the same bounded chunks, byte count, SHA-256, order and owner/context fences as
display exports. The verified UTF-8 JSONL body starts with the
`xdog.canonical-transcript.v1` manifest, followed by source/provenance headers and
indexed typed message records (plus a separate live partial record). Timeline
export retains each recorded branch/head separately, not a text-deduplicated
conversation. Explicit typed image base64 and MIME values are retained; signatures,
opaque response IDs, redacted reasoning, common inline credentials and structured
credential fields are withheld. Numeric typed usage is preserved. Arbitrary
metadata cannot impersonate an image block to bypass redaction.

This is a **read-only export, not a session-resumption or replay format**. It
neither grants tool permission nor starts a model, activates history or resolves
resource links. Binary image contents are intentionally disclosed only under the
explicit canonical capability; clients must treat them as sensitive, untrusted
bytes, not terminal instructions. Redaction is bounded known-field/pattern
filtering, not a promise to detect secrets embedded in arbitrary prose or pixels.
`SourceReceiver.expect(..., format="canonical")` requires an explicit decoder;
format mismatches are refused before content is delivered to display decoders.

Actual offline CLI tests cover saved/branch/timeline 2 MiB image framing, long
Unicode text, redaction, unchanged permissions/history and slow/closed-output
cleanup. Owner/format/checksum/order/capacity/cancel/revision/withdrawal/preparation
settlement negatives are covered. **Child canonical export remains unimplemented**
(the existing child source is display-only); UI-038 remains open for that scope and
its complete acceptance evidence. No non-Linux native verification is claimed.

#### Child canonical source implementation (UI-038 follow-up)

`child_canonical: 1` is now independently advertised and negotiated. A current
child-control owner can issue `child_source_read` with `format: "canonical"`,
`child_id`, session/revision and child owner/epoch. Neither parent canonical nor
child display capability grants child binary export. Revoking child display or
parent canonical does not revoke an independently authorized child canonical read;
revoking child canonical/controls, changing owner/epoch or closing the child does.

The same canonical JSONL manifest/indexed messages/typed images/redaction format
includes a captured child metadata header and an explicit partial record when a
stream update has actually been admitted. It excludes transient tool-progress UI
artifacts (tracked separately under UI-039). The original approved child history
is not installed into the parent, and a read does not execute more child work.
Actual CLI checks verify 2 MiB image upload/export, digest/order/format, unchanged
parent permissions/history and owned child cleanup on quit/EOF/closed output with
an intentionally undrained binary stream. Full parent/child source matrix:
158 passed in `/tmp/ui038-child-verified.log`. UI-038 is pending verification
closeout, not declared complete while the earlier unbounded test process remains
live; see audit tracker for process ownership and the no-interruption constraint.

### Independently negotiated parent transient RPC events (UI-039, partial)

`ready.capabilities.transient_events` advertises `1`; opt in explicitly with
`rpc_hello` and exact integer `transient_events: 1` (or withdraw with `0`). This
capability does not grant transcript/canonical/child source access. Its receipt
has `event_owner`, session/revision, `max_event_bytes` and `replay: false`.
Parent `transient_event` envelopes include the same owner/session/revision,
monotonic sequence, `retained: false`, the original event name and projected data.
Supported events in this increment: tool execution start/update/end, assistant
message updates, handled input feedback, and agent end. Tool progress is text-only;
typed image bodies are omitted and counted. Known credential fields/inline forms
are redacted; signatures and redacted reasoning are never copied. The terminal
projection is not canonical/history resumption data.

At most 32 projected callbacks are pending; payload/frame sizes are bounded at
64 KiB. Overflow or oversized projection is explicitly marked `limited: true`,
not silently presented as a complete event or stored for later replay. Late
callbacks check their captured connection lease and actual session/agent/revision;
withdrawal and teardown unsubscribe. Re-enable rotates the event lease. Existing
legacy response/approval frames remain separate; this opt-in API does not change
tool permission or open another model loop.

The actual parent tool loop verifies start/progress/end in order and one tool
execution. Actual offline CLI verifies streaming partial delivery, withdrawal and
abort/producer settlement. This increment is **parent-only**: child transient tool
progress and broader custom artifact transport are not supplied by a retained child
source or this capability. UI-039 remains open until those applicable contracts
and complete safety/transport evidence are implemented and verified.

#### Child transient progress (UI-039 follow-up, partial)

Negotiate exact `child_transient_events: 1` with current child controls. This is
independent of parent `transient_events`, child display/canonical sources and
child approvals. Its capability receipt includes `event_generation`.
`child_transient_event` carries the actual parent session/revision, child owner/
epoch/id, event generation, monotonic sequence and `retained: false`. It projects
the same named lifecycle/progress events as the parent stream and does not export
binary progress bodies or execute another model/tool loop.

The original session child event hook is awaited, not replaced or skipped, and
is restored on detach. Event admission captures generation BEFORE that await;
withdraw/re-enable during an existing hook cannot resurrect old events under the
new grant. Queued delivery checks generation, child identity and owner again.
The queue is bounded to 32 projected callbacks with explicit overflow/oversize
markers. Actual child-tool start/update/end, original hook chaining, queue bounds,
redaction, stale owner/re-enable and real CLI child partial/quit settlement are
verified. Custom artifact/event contracts and complete slow-peer terminal/transport
acceptance remain UI-039 work; this increment alone is not feature completion.

#### Scoped declarative extension contributions (UI-039, partial)

Negotiate exact `extension_contributions: 1` independently of dialog/editor/
navigation capabilities. The separate receipt supplies generation, session,
64 KiB event bound and `replay: false`. During an owned command/prompt invocation,
`extension_contribution` carries request/scope/session, generation, sequence,
`retained: false`, and redacted notice text or a declarative status/widget snapshot.
Cleared slots are absent from the latest snapshot; snapshots are presentation
metadata, not executable callbacks or model messages. No images/custom terminal
frame bytes are implicitly exported. Contributions settle before the command's
result receipt. A contribution-only hello preserves existing UI grants.

At most 32 projected callbacks are pending, with explicit overflow/oversize markers.
Withdrawal refuses queued old-generation delivery and does not downgrade to legacy
notice/UI disclosure. Clients which have never negotiated this capability retain
the existing compatibility path. Re-enable rotates generation. Real worker and
actual CLI tests cover notice/status/widget/clear, no model/history/policy change,
original scope ownership, redaction and withdrawal. Child declarative contributions,
rich artifact contracts and complete transient transport verification remain open;
this is not arbitrary custom terminal rendering over RPC.

#### Independent child declarative contributions (UI-039, partial)

Negotiate `child_contributions: 1` independently of parent contributions, child
transient events, sources and approvals. The capability receipt supplies
`contribution_generation`. `child_contribution` carries actual child id/owner/epoch,
parent session/revision, generation, sequence, `retained: false` and projected
notice/status/widget snapshot data. This includes actual child-worker bootstrap and
turn-start contributions; parent worker state is never substituted for child state.

Withdraw/re-enable, changed owner/revision, revoked controls or a closed child deny
queued old contributions. At most32 projected callbacks are pending,32KiB payloads,
with visible overflow/oversize and known-secret redaction. Actual worker+CLI tests
verify delivery without parent history/model/policy changes. Richer artifact/custom
protocol contracts and full slow-peer transport verification remain UI-039 work.

#### Transient output backpressure and visible transport loss (UI-039)

Native POSIX RPC output now reserves the main output memory budget for control,
approval and lossless source frames: parent/child transient events and declarative
contributions have a **1 MiB presentation backlog ceiling**, bounded further by
half the configured total budget. Once reached, whole new transient frames are
withheld (never half-written), without blocking the input reader or tool producer.
The next delivered event of the same family reports `limited: true` and
`transport_dropped`; `status.transient_transport_dropped` exposes outstanding
per-family loss even if no later event occurs. These are presentation-loss counts,
not lost tool inputs or proof that effects were rolled back. Sequence gaps remain
visible. Other event families cannot inherit a dropped family's loss count.

Actual offline CLI tests admit 128 provider progress events with parent/child
output deliberately undrained, then exercise quit/EOF/closed-output producer
settlement. Real pipe tests verify control/approval memory remains available and
loss is reported after draining resumes. This is not a way to deliver unread
terminal output: it bounds memory and keeps lifecycle control responsive. Custom
artifact contracts and remaining complete-scope requirements stay open.

#### Complete named Agent lifecycle projection (UI-039)

Parent and child transient streams now project all fourteen declared Agent event
kinds: agent/turn start/end; message start/update/end; tool execution start/update/
end; input consumed/transformed/rejected/handled. Message events carry a redacted
typed message (including cumulative tool-call arguments), never provider signature
handles. Input consumption carries its identified delivery receipt and omitted-image
count, not an execution grant. Tool-end includes final text/redacted details and
omitted-image count. Agent-end does not embed the whole retained history.

Stream update metadata includes the event kind and valid content index only;
raw deltas are withheld because fragmented credentials/signatures cannot be safely
redacted independently. Cumulative messages preserve typed usage and tool-call
structure; redacted reasoning and every image body remain withheld from transient
streams regardless of independently enabled canonical export. Oversized projections
retain the existing explicit limited marker and transport-loss contract.

The full named current lifecycle/transient/contribution/navigation/child/real-CLI/
backpressure gate passes188 cases (`/tmp/ui039-acceptance.log`). Custom artifact
protocol implementation is tracked once under UI-042; its integration into the
UI-039 event surface remains required, not claimed implemented by this event matrix.

### Client-rendered RPC custom views (UI-042, partial)

Negotiate exact `extension_custom_views: 1` independently. The separate receipt
includes capability generation and viewport/frame limits. The custom-only hello
preserves existing dialog/editor/navigation grants. Owned extension calls may
open a `kind: custom` dialog, and the client sends `extension_custom_input` with
request/session/scope/dialog identity, `custom_generation`, an increasing sequence
and a bounded event (`resize`, `key`, or `paste`). The existing worker and broker
render `extension_custom_frame` replies with geometry, revision, generation, lines
and cursor. Unsafe terminal controls are stripped from rendered lines.

Input needs the current delivered frame's revision/geometry/generation; resize
establishes new geometry. Completion uses the existing broker's painted-frame,
viewport and current-input checks, not an arbitrary dialog answer. Clients may
cancel with a null dialog answer; non-null custom answers are refused. Escape and
protected Ctrl+C/D/L/Z remain unavailable to extension custom input. Withdrawal
cancels owned custom dialogs and late/in-flight frames cannot regain authority.
The client owns rendering: the server never writes custom frame ANSI to the CLI
terminal or changes tool permission. Frame receipt is not proof of external-client
physical painting; clients must supply truthful painted revisions.

Actual owned-worker and real offline CLI tests validate initial frame, selected
input completion, stale/foreign/reused sequence refusal, reserved controls, resize,
cancel and withdrawal during rendering; no model/history/policy changes. This
increment does not yet complete child custom UI, nested dialog/focus interaction,
live-overlay controls or the full final client/transport matrix. UI-042 stays open;
Pi wire/native custom equivalence is not claimed.

#### RPC nested custom/dialog focus ownership (UI-042, partial)

The broker's latest admitted live dialog owns interactive input, independently of
whether its transport publication has arrived. A nested question/custom view blocks
parent custom input before the frontend sees it. Admission of a nested dialog
invalidates the cached parent frame; after child completion, only the freshly
rendered parent revision can be used. Null host cancellation may still cascade
from an ancestor; arbitrary non-null answers cannot borrow a sibling's focus or
resurrect a completed future. Nested parent IDs come from the existing broker's
owned render context, never client-supplied hierarchy.

Actual nested input/confirm/custom worker paths and a real CLI sequence exercise
parent blocking, child completion, fresh parent frame, stale receipt refusal and
cascade cancellation. Capability changes in an active invocation now update actual
runtime custom/dialog handlers before returning capability receipts. Child-agent
custom routing and live overlay ownership remain open; these parent nested tests
are not evidence of all RPC custom or non-Linux native behavior.

#### Independently negotiated child RPC custom views (UI-042, partial)

`child_custom_views: 1` enables client-rendered views from the actual child worker,
independently of parent custom views and child ordinary questions/source grants.
The capability receipt includes `custom_generation`. Child dialogs and custom
frames carry their actual child id, parent session/revision and child owner/epoch;
`child_custom_input` must include those identities, generation, increasing input
sequence and the current frame revision/viewport. Existing broker painted completion
checks remain authoritative. Arbitrary non-null custom dialog answers are refused;
null host cancellation remains available. Unsafe terminal controls in frames are
removed; reserved interruption keys remain host-owned.

Nested child questions protect input at broker admission before frontend delivery.
After nested completion only a newly returned parent frame can authorize input.
Withdraw/re-enable or changed controls revoke old custom frames, and late delivery
checks original capability generation. Tool permission is still separate: actual
CLI tests approve the child tool explicitly before interacting with its custom view;
no view answer grants tool access or installs child history into the parent.

Current child/parent custom, original child controls/CLI and broker tests pass199
cases (`/tmp/ui042-child-final.log`). Live overlay controls, complete custom-view
transport/backpressure and full client acceptance remain outstanding. UI-042 is
not complete; neither these RPC fixtures nor a painted field claim non-Linux native
or Pi wire compatibility.

#### Parent RPC live overlay controls (UI-042, partial)

Negotiate `extension_overlay_controls: 1` separately from custom views. Owned
worker hide/show/focus/unfocus requests use the existing broker's monotonic control
sequence and emit `extension_custom_control` with invocation/session scope. Control
changes invalidate delivered frame receipts. Hidden and non-capturing overlays cannot
receive key/paste input; resize may establish geometry without granting focus. After
focus changes, a fresh geometry generation and delivered frame are needed for the
existing painted-frame completion proof. Nested dialog ownership remains authoritative.
Withdrawal cancels live overlay views rather than downgrading to uncontrolled frames.

Actual worker and offline CLI tests cover non-capturing refusal, hide/show/focus,
fresh geometry and completion, independent negotiation and withdrawal. Current
related custom/broker/navigation suite:177 passed (`/tmp/ui042-overlay-current.log`).
Child overlay routing, all custom transport/client acceptance and final full-root
regression remain unverified; no physical remote-client painting or Pi compatibility
is inferred from protocol receipts.

#### Child RPC live overlay controls (UI-042, partial)

Negotiate `child_overlay_controls: 1` independently of child custom frames.
Overlay control frames carry child/session/owner/epoch and capability generation;
hide/show/focus/unfocus use the original child's broker, never the parent UI.
A hidden/non-capturing child overlay cannot receive key/paste input, and control
changes invalidate old frame proofs. Changing overlay/custom/controls capabilities
revokes pending custom views and rotates the child custom generation. Fresh geometry
and current painted-frame receipts are required after control changes.

Actual child worker and real CLI tests verify hide/show/focus, stale input rejection,
withdrawal, current frame completion and a SEPARATE tool approval before custom
interaction. Parent history and permission policy remain unchanged. Current related
parent/child custom+overlay/control/CLI suite104 passed (`/tmp/ui042-co-final.log`).
Full custom transport/backpressure/client acceptance and applicable terminal contracts
remain unfinished; protocol receipt does not prove physical client painting.

#### Mixed RPC capability request safety (UI-042)

Extension capability batches are validated completely before any flag/generation,
view cancellation or capability receipt changes. The actual `rpc_hello` reader also
preflights extension, child and transient-event fields together: an invalid later
child field cannot partially enable or revoke an earlier extension custom/overlay
capability. Fields require exact integers0/1; booleans and coercible strings are
not grants. Invalid batches retain an already live custom view and its frame proof.
Actual CLI tests verify both no partial grant and no partial withdrawal. Existing
transcript capability compatibility retains its separately documented fail-closed
negotiation behavior; this change does not turn all operations into a transaction.

#### Large custom-frame pipe lifecycle verification (UI-042)

Actual Linux parent and child RPC tests leave roughly120KiB custom frames undrained
(after verifying only their real output prefix), then exercise quit, EOF and closed
output. The CLI exits and reaps the owning extension worker; child tool approval
remains separate and no parent history/tool mutation is introduced. This verifies
bounded shutdown with unread custom output, not physical client painting or complete
custom protocol conformance. Final client/terminal/goal-wide gates remain open.

#### In-flight custom input frame ownership (UI-042)

Admitting a custom input or resize immediately retires its delivered frame receipt
in both parent and child RPC handlers. Another key/paste cannot reuse that receipt
while the callback is queued or rendering; it must await a fresh returned frame.
This protects extension input state, not just the broker's eventual completion
value. Failed admission does not retire the current receipt. Held-render and
synchronous child stale-input tests verify that no extra key reaches the callback.

#### Superseded custom render results (UI-042)

When a newer geometry generation is admitted while rendering, the broker marks the
older result `superseded: true`. Parent and child RPC handlers withhold that obsolete
frame instead of restoring an interactive receipt while the new render is pending.
Native broker consumers retain the explicit marker and existing completion checks;
this does not cancel admitted callback effects or claim rollback. Held actual worker
render tests cover both parent and child paths, with no obsolete frame/input admission.

#### RPC custom enhanced keys and atomic paste evidence (UI-042)

Actual parent/child worker tests cover declared press-only and enhanced press/repeat/
release modes. Unnegotiated repeat/release is refused before frame receipt retirement;
enhanced Enter repeat/release cannot complete the view, while explicit Enter press
can. Unicode multiline/tab/quoted paste is delivered once as data, not interpreted as
commands or individual keystrokes. Actual parent CLI exercises the same protocol
contracts. Full custom client/terminal requirement audit and final regression remain
open; these tests do not claim native enhanced keyboard execution on other OSes.

#### Bounded custom-frame cross-thread delivery (UI-042)

Parent and child RPC frame publications now reserve capacity before scheduling
onto the receiving loop: at most16 queued frames and2MiB serialized data per
connection controller. Reservation is released after delivery, including callback
failure; closed-loop scheduling refusal releases it immediately. If capacity is
exceeded, the owning broker view is canceled with a null result rather than allowing
an unbounded queue or silently delivering a partial interactive view. This is not
rollback of extension callback effects, nor a tool permission change. Existing
scope/generation/painted-frame fences still apply. Full goal regression remains open.

#### Explicit custom-frame capacity errors (UI-042)

Frame delivery capacity refusal now emits an owned
`extension_custom_error` / `child_extension_custom_error` with
`code: "frame_delivery_capacity"`, `canceled: true`, original scope/child identity
and capability generation. The view still returns null cancellation; this is not
callback-effect rollback. Error delivery is deduplicated and bounded independently
(maximum8 pending notices), so repeated refused large frames cannot build another
unbounded queue. Late errors fail the original scope/generation checks and cannot
clear or cancel a replacement view after re-enable. Existing permissions remain intact.

#### Output admission and custom frame receipts

Parent/child custom frame receipts are cached only after the output sink accepts
the frame. Output refusal cancels the owning view and leaves no interactive cached
receipt. Enqueue acceptance is not physical remote-client delivery or painting;
clients still provide truthful painted revisions.

### Independently negotiated child terminal input data (UI-042, partial)

Negotiate exact `child_terminal_input: 1`. Child capability receipts expose current
`terminal_generation` and `child_terminal_registry` (active hook/worker generation).
`child_terminal_input` packets require the current child/session/revision/owner/epoch,
fresh request ID, terminal capability and worker generations, kind (`key` or atomic
`paste`) and bounded data. Only editable keys are accepted; submission, host actions,
approval/recovery controls stay client-owned. Paste remains inert data even with
newlines/escape-looking text. This invokes the child's existing registered terminal
hook and returns `child_terminal_result`; it never submits/queues child input or
writes terminal controls. The client owns safe application of returned editing data.

At most one terminal request owns this child RPC control slot. Original lifecycle,
hook/connection generations and pending dialog/approval admission are checked before
and after the owned callback. Transform output is checked again; invalid/error/
timeout/cancel never silently falls back or replays original data. Withdrawal cancels
and settles the actual worker operation. Pending approvals are checked at their
actual admission, including before frontend publication. Real worker and CLI verify
text/paste/consume, no extra model turn/queue/policy change and cancellation/reaping.
Full custom/terminal client contract acceptance remains open.

#### Terminal cancellation and late approval receipts

A child terminal request canceled before worker admission returns one scoped
canceled result with no transformed data and no hook execution. If an approval
is admitted while the hook is running or before its result is returned, the
post-hook owner check refuses data/consume delivery. Original input is not
retried or submitted automatically; callbacks already executed are not rolled back.

#### Custom RPC contract acceptance status

The finite current contract audit is in
`docs/agent-ui-rpc-custom-contract-audit.md`. Background refresh and child-terminal
CLI disconnect/deadline reaping are verified; parent/child interactive custom input
also rejects authoritative pending approvals before frontend publication. UI-042
remains pending verification of structured RPC renderer integration, physical Linux
RPC terminal interaction and final integrated closure. Current221-pass named matrix
is not final root regression or physical remote-client painting proof.

#### Grapheme-safe structured RPC frame projection

Parent/child RPC custom-frame control filtering now uses the vetted custom-line
sanitizer before removing allowed styles, preserving ZWJ emoji/combining grapheme
width. The old ordinary text sanitizer widened composed layout frames by deleting
ZWJ. Actual structured worker layouts are now verified through parent/child RPC in
inline/fullscreen/positioned-overlay modes with narrow resize/cursor, scroll/layers
and nested focus. Physical Linux RPC terminal interaction and final contract closure
remain open in `docs/agent-ui-rpc-custom-contract-audit.md`.

#### Physical Linux RPC terminal acceptance evidence

An owned private-tmux test frontend now renders received real parent/child RPC
layout frames using shipped TUI components. Physical Unicode input, layer focus,
narrow resize, nested questions, independent child tool approval, restored parent
cursor and exact termios/native scrollback recovery pass. This is test evidence,
not a shipped remote frontend or assurance about arbitrary clients. UI-042 final
integrated contract closure remains pending in the finite audit.

### RPC custom/child-terminal contract verification checkpoint

As of2026-10-10, all three finite acceptance actions in
`docs/agent-ui-rpc-custom-contract-audit.md` are verified; UI-042 is completed.
Earlier partial/Open notes above are historical implementation slices, not current
additional requirements. The complete current RPC/custom broker suite579 cases
passes without skips, including actual Linux parent/child physical terminal paths,
with source files frozen during verification. This proves the documented named
contract, not Pi wire compatibility, arbitrary external clients, a shipped remote
frontend, non-Linux native validation or final full-workspace regression. Separate
UI-039 event integration and final Linux safety/root gates remain pending.

### RPC transient/custom event integration verification checkpoint

As of2026-10-10, UI-039's documented named parent/child transient/tool-progress and
custom callback contribution contracts are verified. Actual custom callbacks emit
scoped redacted notice/status/widget contributions before returned frames; withdrawing
contribution capability does not cancel custom view capability, and no contributions
are masqueraded as retained agent history. Full frozen RPC/custom broker581 cases
pass with zero skips. Earlier partial OPEN notes are historical slices, not current
extra requirements. This does not imply history replay of every transient artifact,
Pi wire conformance, arbitrary client behavior or final whole-workspace regression.

### Parent RPC terminal hook editing data (UI-036, partial)

Negotiate `terminal_input: 1` and inspect `terminal_capabilities` for current
`terminal_owner`, session/revision, worker generation and hook availability.
`terminal_input` requests require those exact identities and a fresh request ID;
`terminal_result` returns transform/consume data only. It does not submit a prompt,
write terminal bytes or change tool policy. Protected/submission keys are refused,
atomic paste stays paste, and results are revalidated before publication.

One owned5s operation; stale owner, approvals/dialogs, context changes, callback
errors and cancellation do not replay original data. `terminal_cancel` uses the
original request envelope; withdrawal and RPC teardown settle the actual worker.
Normal prompt/navigation/control admission is excluded during the hook operation.
Actual worker/CLI evidence exists; full raw-surface contract audit remains UI-036.

#### Cross-parent/child dialog terminal ownership

Parent and child RPC terminal hook data are denied while any owned parent/child
extension dialog is pending. The check uses actual broker admission, not delayed
frontend publications, and is repeated after the hook returns. A newly admitted
question cannot receive old transformed data or cause automatic replay.

#### Parent/child RPC hook CLI lifecycle evidence

Actual Linux CLI tests now cover both parent and child hanging terminal hooks on
quit, EOF, closed output and deadline, including exact owned worker reaping. The
closed-output check performs a status write while stdin remains open. Parent editing
hooks do not invoke the model; child producer cleanup settles without tool mutation.
This is bounded named lifecycle evidence, not final full-workspace regression.

#### Prompt release-event boundary

Native Coding/Claw prompt hooks do not receive release events or allow transformed
releases to synthesize editing presses. RPC editable input/results likewise refuse
release; encoded repeat remains supported. Custom-view release events are a separate,
explicitly declared protocol. The named remaining input acceptance actions are in
`docs/agent-ui-input-surface-audit.md`; UI-036 remains open.

#### RPC hook configured key bindings

Parent/child RPC editing-key validation now uses the configured bounded
`keybindings.json` snapshot, not just defaults, on both incoming and transformed
key data. Keys remapped to submission, external editor, draft saving or extension
commands remain host-owned. Invalid keymap configuration refuses key-hook data
rather than guessing authority from fallback bindings. Atomic paste is still data;
remote clients must additionally enforce their own local keymap before applying
returned editing input. Full UI-036 acceptance remains open.

#### Physical RPC editing-data acceptance evidence

Owned Linux private-terminal frontend applies parent/child RPC hook data through
shipped editor/arbitration components. Physical Unicode/paste/cursor, repeat phase,
release exclusion, original-owner held-hook cancellation, paused recovery and exact
terminal restoration pass. Test worker logs verify actual data application; this is
not a shipped arbitrary remote client. UI-036 concrete raw-contract reconciliation
and final integrated input matrix remain open.

#### Runtime shortcut-aware RPC hook data

Incoming and transformed parent/child RPC keys are checked against their owning
extension runtime's current shortcut inventory as well as configured keybindings.
The shared native effective resolver preserves conflict/override/disable behavior:
active command shortcuts are host actions, not editing data, while a disabled
conflicting shortcut does not take away its native editor binding. Atomic paste
remains inert data. UI-036 reference/raw-contract reconciliation remains open.

#### Approval admission revision and terminal hook results

Parent/child RPC hook operations capture approval-manager identities and monotonic
approval admission revisions. Opening then closing an approval still invalidates an
earlier hook result, even when no request remains pending at delivery. Replacing the
child approval-manager inventory also refuses the old result. This prevents edits
crossing an input ownership change; it is not a tool policy change or callback rollback.

## ACP session routing and reviewed inbox

`--session ID --prompt TEXT` sends through ACP to the exact live owner without
constructing another Agent. It differs from `--resume-id ID` (explicit history
restore), rejects inactive/unknown/cwd mismatch, and cannot override target policy.
`--acp` is the standard stdio adapter; `--acp --session ID` fixes a bridge to an
existing owner. Ordinary native/RPC/navigation usage does not require optional ACP
activation; the pinned SDK currently supports Python 3.12–3.14 only.

Callbacks use an explicitly configured **ACP endpoint + session**, not a required
coding CLI subprocess or TUI event host. The independent lightweight ingress
(`python -m xdog.coding.acp.ingress_main --help`) receives into a durable per-session
inbox even when every TUI/owner is closed. It does not create an Agent or silently
restore history. Starting/stopping the ingress is explicit and independent.

`/inbox list [OFFSET]` and `/inbox show ID` inspect without running a model or
refilling credit. `/inbox approve ID [ID...]` selects exact messages through the
normal permission hook and sole controller. No implicit all or startup replay.
A batch starts at most one automatic turn; interrupted attempts need explicit
`/inbox retry ID`, retaining prior attempt evidence. TUI/RPC label external source,
evidence, status and full details, joining canonical attempts without duplicates.

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
