# xdog-tui

> Platform scope: **Linux only**. Non-Linux adapters and native-platform support
> are outside the current product scope; historical audit evidence is not a support promise.

**Terminal UI library with differential rendering.**

A small terminal UI toolkit that redraws owned rows in the main terminal buffer
while preserving native scrollback. No curses dependency.

`InputPanel` supplies shared Unicode editing, undo/redo, history and atomic
paste. `InlineLayout` budgets the editor and prioritized auxiliary panels from
terminal height. The renderer uses cursor reports at startup, resize and resume.
Ctrl+L repaints visible owned rows without clearing native scrollback or focus.
Focused editor markers position and show the terminal cursor; panels without a
marker hide it. Overlays restore base focus and composite by display cells.
Clearing a scrolled transcript re-anchors the owned tail without erasing history.
Confirmation panels reject unshown/clipped actions and unpainted resize states.
`EventQueue.drain()` processes bounded FIFO batches so sustained producer output
does not monopolize a UI tick (defaults: 64 events or 8 ms between callbacks).

Standalone panels are available from `xdog.tui.components`:

| Component | Responsibility |
| --- | --- |
| `PermissionPanel` | Summary scrolling, choices, selection and cancellation callbacks |
| `DetailsPanel` | Full detail records, entry navigation, scrolling and explicit output following |
| `InputPanel` | Multiline editing, history, completion and submit/cancel callbacks |
| `StatusLine` | One bounded row of plain status or prioritized activity/context/model fields |

Panels accept data, theme functions and callbacks; they do not import Coding or
Claw policy. Applications own permission decisions and lifecycle state, while
`InlineLayout` only positions panels and allocates height. `PromptEditor` and
`BoundedDetails` remain compatible aliases at their original import paths.

See [terminal stability and verification](../../docs/tui-pi-parity.md) for scope,
test commands, and platform limitations. Fullscreen applications and remote
coding are outside this stability work.

## Part of xdog

This package is one piece of [xdog](https://github.com/litterzhang/xdog), a
local-first toolkit for building, running and scheduling LLM workflows. The
centrepiece is [`xdog-flow`](https://pypi.org/project/xdog-flow/) — a typed
workflow format and compiler.

Documentation: **https://xdog.942295.xyz**

## Licence

Copyright (c) 2026 HugeMan <942295.xyz>

GNU Affero General Public License v3.0 or later — see
[LICENSE](https://github.com/litterzhang/xdog/blob/main/LICENSE). Output compiled
by `xdog-flow` is exempt; see the
[Generated Output Exception](https://github.com/litterzhang/xdog/blob/main/LICENSE-EXCEPTION.md).

### Non-blocking prompt path completion

`InputPanel.set_autocomplete_provider(provider)` now connects live prompt
completion. Call `poll_autocomplete()` from the host's UI tick and request a
repaint only when it returns `True`. Providers must return without blocking;
`WorkspaceFileCompletionProvider(root)` provides a coalescing filesystem worker
for fuzzy `@` references and explicit paths. Close owned providers during host
shutdown. Results never mutate the editor from a worker thread.

Selecting a file replaces only its cursor-local token and never submits the
draft; slash commands retain their existing Enter behavior. Focused details and
permissions suppress background suggestions. `max_entries`/`scan_seconds` bound
worker batches, not total discovery. Names are held in a private SQLite index;
root search and large directory scans resume until complete. Git supplies ignore
semantics for repositories and non-repositories through private metadata; no
workspace Git state or hooks are changed/executed. Missing/failing Git is a
visible basic-exclusion fallback. `CompletionStatusProvider` optionally supplies
non-blocking progress text; InputPanel clamps it to the existing menu budget and
never treats status as a selectable path. See `docs/agent-ui-gap-plan.md` for the
full application goal and verification.

### Prompt keymaps and foreground terminal handoff

`PromptKeymap` is the immutable, validated map used by the live Coding/Claw
editors and host action handlers. `InputPanel.set_keymap()` remaps actions without
falling back to replaced chords; dialog confirmation remains independent.
`help_text()` and `label()` expose effective bindings for help and hints.

`TUI.run_outside_terminal(callback)` schedules one foreground callback after
protocol cleanup and raw-mode restoration. The callback runs on the UI thread;
polling/painting pauses, while external workers may keep publishing events.
Modes are preserved around the callback in case its process leaves them changed.
Return re-enters negotiation and appends a new owned transient tail without
replaying durable history. Pending handoffs and stopped/suspending TUIs reject
new handoffs; coalesced input after a handoff request is not executed.

`edit_draft()` provides the standard private-file external-editor round trip.
Only user environment preferences select its executable; returned text must be
applied by the host, not submitted automatically. Helpers are exported from
`xdog.tui` as well as their modules. Approval policies remain application-owned.

### Queue control vocabulary

The shared `queue_controls` module supplies immutable queue views, stable local
ID bookkeeping and `/queue` command parsing; it does not route agent work or
choose application permissions. Hosts show bounded previews and own queue locks,
worker-loop acknowledgements, transport generations and draft recovery. The live
prompt keymap exposes `steer` (Alt+S), `follow_up` (Alt+F) and `queue` (Ctrl+Q).
This vocabulary serves the primary composer/run; `ChildQueuePanel` below is a
separate read-only child-input inspector.

### Session/branch navigation surfaces

`NavigationEntry`/`NavigationSnapshot`, `NavigationWorker` and `NavigationFeed`
provide immutable, paginated local-worker and remote-reply views. Search applies
to the full metadata set before pagination. Requests coalesce and stale/closed
replies are fenced; changed searches/scopes do not leave stale rows selectable.
`NavigationPanel` owns search, paste, navigation and focus in the bounded native
transient tail. Application callbacks own activation, idle gates, permission
isolation, storage and gateway context synchronization; the library does not
import agent/application policy or run model calls.

### Explicit image composer and clipboard reads

`Attachment` is immutable raw image data plus stable identity/metadata.
`attachment()`/`read_attachment()` validate PNG/JPEG/WebP headers and bounded
size/dimensions; file reads use nonblocking open/regular-file checks to avoid FIFO
hangs, not to establish a sandbox. `AttachmentReader` owns one off-thread read
and its bounded clipboard subprocess, publishing immutable events to the host.
Linux Wayland (`wl-paste`) and X11 (`xclip`) image/text helpers are explicit
reads, never background polling. No non-Linux clipboard adapter is shipped.

`AttachmentComposer` owns add/list/preview/remove/clear/take/restore on the UI
thread. Late reads after take stay editable; explicit clear fences prior reads.
Recovery retains bytes even above ordinary admission caps, leaving host validation
to block oversized sends. `InputPanel.set_attachment_summary()` reserves a bounded
summary row and enables empty-text submission only while attachments are present.
`AttachmentPanel` owns focused preview input, caps image height, and falls back to
metadata on unsupported/tiny terminals. Types/helpers are exported from `xdog.tui`,
the panel from `xdog.tui.components`. Provider capability, permissions, typed SDK
conversion, queue ownership, persistence and transport remain application policy.

### Full-source transcript browsing and temporary alternate screens

`Transcript.transcript_records()` projects immutable, stable-ID source strings
from retained message/tool components without rendering/truncation. User/assistant
source is retained before Markdown transformations. `TranscriptBrowser` owns one
coalescing worker: full source indexing, literal Unicode-casefold search over every
occurrence, source-position preservation on refresh, horizontal clipping and
bounded viewport snapshots. Per-line cumulative counts avoid allocating one
object for each repeated occurrence on a huge line. Old/closed results cannot
be published over newer requests. Source extraction for explicit copy/export is
separate and should run off the UI thread.

`TranscriptPanel` owns query paste/editing, occurrence and source-entry navigation,
vertical/horizontal scrolling, whole-line/range/entry selection and refresh/latest
controls. The host owns source/session boundaries, permissions and fullscreen
policy. Inline panels are capped at 12 rows; temporary fullscreen takes the
host-provided terminal-height budget. Searching/selection has no application
mutation, agent, provider or clipboard dependency.

`TUI.request_fullscreen(enabled)` schedules an owned terminal-loop boundary
transition rather than changing modes inside an input callback. A temporary
alternate painter retains the native main-buffer painter; return requests CPR to
handle hidden main-buffer reflow, then repaints only owned visible rows. Coalesced
keys cannot submit through a pending transition. `InlineLayout.fullscreen_view`
lets hosts provide a height-bounded alternate surface; no normal transcript/editor
is painted there. Hosts must leave fullscreen for approvals/other dialogs.
Stop/failure cleans temporary screen ownership; suspend retains it for re-entry.
The default remains native scrollback, with no main-buffer clear/replay. Helpers
are exported from `xdog.tui` and `TranscriptPanel` from `xdog.tui.components`.


Hosts with asynchronous remote sources can supply `TranscriptPanel(on_refresh=...)`,
set a visible loading/error status, and replace immutable records only after complete
validation. Selection is disabled during loading; pending query/End intent survives
old viewport and geometry receipts. Whole-entry selection is prioritized in the
header even when a source-status label is present. Large sanitized ASCII lines use
cell-equivalent direct slicing/panning instead of allocating a grapheme object for
every offscreen character; Unicode retains grapheme-safe clipping.


### Captured transcript output actions

`transcript_transfer` supplies immutable parsed options/prepared artifacts, source
scope extraction, HTML escaping/heuristic share redaction, exclusive private atomic
file creation and explicit OS clipboard writes with a visible private-file fallback.
`TransferWorker` owns one off-thread operation; it does not replay/coalesce writes.
`TranscriptActions` adapts immutable worker receipts into a context-fenced UI review
lifecycle. Hosts retain approval, source completeness, workspace/session and focus
policy. `OutputPanel` owns preview/path input, forbids confirmation until source and
full metadata/destination are rendered, and keeps paste/modified Enter/tiny resize
from saving. It imports no agent policy and performs no upload. `TranscriptPanel`
exposes optional output callbacks for y/e/s with stable selection/entry IDs.


### Child-work presentation

`ChildPanel` is a policy-free bounded roster/form/transcript component used by both
clients. Hosts provide validated summaries, immutable source records, operation
callbacks and ownership fences. It never executes agents or grants tool permission.
Child-specific start/steer/follow-up/cancel/close confirmations require a painted
visible action; paste/repeat/release/tiny-frame input cannot confirm. `ChildSourceWorker`
coalesces local conversion; child JSON conversion preserves complete retained text,
redacts sensitive fields heuristically and omits binary bodies/signatures. Search,
selection, fullscreen and explicit reviewed outputs reuse `TranscriptPanel` and
`TranscriptActions`. Child dialogs/configuration/tool-progress policy stays in hosts.

`ChildQueuePanel` is a read-only metadata inspector, not a queue editor. It shows
pending/selected inputs, attachment counts and stopped/uncertain recovery provenance.
Up/Down selects, Enter expands metadata, r refreshes and Escape returns. There are
no edit/reorder/remove/return, stopped-copy or binary-recovery controls, and pasted
input cannot become a command or draft. Hosts bind list-only requests to the exact
child/session/view. Main-Agent/SDK queue mutation and explicit durable recovery stay
in their own backend and normal permission policy; this component grants neither.
Ordinary child cancel/wait controls and transient child task editors are separate.


`OAuthPanel` is a policy-free bounded full-page consent/status surface composed by
`McpPanel`. Hosts validate endpoints/URIs and own network/view leases. Every field
must paint before confirmation; layout/content/visibility changes invalidate prior
review. Cancel outranks polling; URL copy is explicit after full review. Wrapper
budget/invalidation reaches the actual form, including zero rows. The component
never launches browsers, connects tools or interprets OAuth as execution permission.

Refresh reviews use the same full-page confirmation and resize invalidation as
initial consent. Their footer exposes paging until all fields paint, then Enter
confirmation; pending refresh shows **c** cancellation, which remains available
while an owner request is busy. Components dispatch `auth_refresh_prepare` and
`auth_refresh`; only application hosts manage credentials and token rotation.

Child task submission can prepare a local review while a roster request is pending,
without losing the input draft. Final confirmation remains blocked until that
request settles; review preparation itself never starts a child or grants access.

`clipboard_macos.read_macos_clipboard()` supplies a static JXA/AppKit program to the
reader's existing bounded subprocess runner: PNG/JPEG/WebP first, TIFF-to-PNG
conversion with pixel/byte limits, then Unicode text. A change-count guard refuses
mixed clipboard snapshots. Strict bounded JSON/base64 decoding rejects helper
failure without reflecting contents or retrying as text. No clipboard URL/file
reads, browser/app automation, clipboard polling or input submission are added.
Node-based contract tests exercise the exact shipped program with an independent
AppKit interface; they are not validation of Apple's real JXA bridge/pasteboard.
The script-contract tests explicitly skip if Node is unavailable; Node is not a
runtime dependency of the clipboard adapter or either client.

### Read-only extension command display

`ExtensionCommand` displays a host-validated artifact's text and bounded image
components, with complete detail_body and separately retained typed result data.
Its transcript_role is system, not tool/model input. Existing detail expansion and
transcript source ownership apply; narrow fallback text is clipped without altering
graphics payloads. Hosts alone decode/gate artifacts, preserve drafts/session scope
and prevent prompt submission. The component does not execute returned text/URIs,
write files, grant approval or pretend textual export contains the image bytes.

### Explicit typed snapshot output

`TranscriptActions.start_snapshot(options, root, prepare)`/`TransferWorker.start_snapshot`
run a host-supplied pure bounded snapshot recipe off-thread and ALWAYS show the
existing review, including explicit paths. Hosts supply PreparedTransfer data/text/
digest/context; the TUI adds no agent dependency or artifact-policy authority.
Artifact scope is JSON-only export, not binary copy/share or textual record coercion.
OutputPanel exposes no-redaction/sensitive-original-byte warnings; existing
visible-path/resize/context/cancel/private-no-overwrite rules remain. Command display
components carry a host-assigned owner token so clients can select only current
session/connection data for lossless export.

### Effective extension command bindings

`ExtensionShortcuts(rows, keymap)` resolves host-validated command contributions
against effective built-in/user actions, reports disabled collisions and dispatches
only PRESS events. A namespaced ext-ID-command keybindings override replaces that
registration's defaults or explicitly unbinds it; unknown/inactive registrations
cannot become callbacks from configuration alone. Conflicting extension keys are
all disabled, never arbitrary winner-takes-input. Context/activation/command authority
belongs to the hosts. The TUI performs no plugin loading or model/tool calls.

### Canonical pre-admission input display

Shared UserMessage.set_text/Transcript.transform_user_input updates only a matching
current user echo, preserving image-count labels and Markdown invalidation without
terminal command execution. Hosts carry input-transformed/refused ownership receipts,
update actual consumed queued text and restore owned original drafts/attachments on
pre-admission refusal. The TUI itself runs no extension filter, model or permission
policy. Native scrollback and existing source-role/selection semantics remain.

The live TUI now guards painting against geometry changing BETWEEN its input-loop
size check and tick callbacks. A raced frame is deferred while remaining dirty,
so the next loop still queries/re-anchors the cursor before repainting. Pure/offline
rendering remains unchanged. A deterministic lifecycle test and the physical output
review cases preserve existing native-scrollback and visible-confirmation assertions.

Handled input remains host-owned policy. Current clients show explicit system/extension
feedback and retain original composer attachments without pretending the request was
admitted as a model message. Child source inspection accepts separately bounded
input_feedback metadata and presents it as system data, never assistant prose.
No custom renderer/model/tool/permission authority is introduced by these displays.


### Paged output review on narrow terminals

Export/share/artifact review no longer requires an unusually wide terminal for a
long destination. When metadata, warnings and the full target do not fit together,
the output panel pages them with PgUp/PgDn/Home/End. Every displayed metadata/path
row must be painted under the active focus owner before Enter can save. At least
24 columns and four panel rows are required; narrower/hidden views cannot confirm.
Resize, focus loss and path editing revoke that proof and restart field review.

`v` opens the captured payload preview without enabling save, then returns to the
metadata/path review. Normal roomy layouts retain the combined preview. Tab edits
the path; repeated Tab/Enter or pasted confirmation text cannot approve an export.
A long Unicode path is reviewed without losing grapheme boundaries or replacing
its source value. Existing private atomic writes, no-overwrite, owner/context
fencing, no-upload and share-redaction policy remain authoritative.

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
and widget layouts are removed. Private native field-review components remain for
host-owned editor/navigation/queued-input confirmations; these are not a plugin
layout API or permission grant. Core TUI overlays, transcript fullscreen, Unicode,
clipping, cursor and tool-approval behavior remain.
