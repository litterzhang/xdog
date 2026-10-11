# xdog-tui

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

### Repository review

`RepositoryReview` supplies immutable `ReviewSnapshot` / `ReviewFile` objects
from one owned read-only Git worker. Patch rows are stored in a private SQLite
cache and fetched as display-cell-clipped pages; all hunk positions remain
available. Requests coalesce, stale results and partial document caches are
fenced, and close cancels/reaps the child and removes private storage. No agent
or application permission policy is imported into this standalone helper.

`ReviewPanel` composes a bounded file/filter/patch surface in the main-buffer
transient tail. Hosts poll snapshots, restore focus around higher-priority
permissions, and close the backend on exit/workspace change. The panel owns
keyboard/paste while focused so Enter cannot submit the underlying draft.
Library exports are available from `xdog.tui` and `xdog.tui.components`.

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
Wayland/X11 image helpers and the native macOS image/text adapter (text-only
`pbpaste` fallback only if `osascript` is absent) are explicit reads, never background
polling. The macOS adapter is contract-tested on Linux; native desktop validation
remains separate.

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

### Inert MCP completion forms

`McpCompletionPanel` composes an explicit prefix editor, advertised variable
navigation, ranked bounded suggestions and visible selection. Ctrl+N/P selects a
variable, Tab requests and Enter accepts into the parent's editable draft only;
Escape cancels. Typing invalidates/cancels pending suggestions and scope/prefix/
context checks reject late or mismatched replies. Layout/visibility changes fence
unpainted acceptance; control/newline text is sanitized for terminal output without
coercing the selected original value. No network client or application policy lives
in this component. `McpPanel` hosts own request/cancel callbacks and preserve JSON/
URI drafts on invalid/busy submissions.

`scalar_template.expand()` is a bounded inert RFC 6570 string-value expander for
all operators, prefix modifiers and scalar explode. It omits undefined variables,
percent-encodes values appropriately and accepts no list/map values. Expansion is
editable URI data only; neither helper resolves or fetches URLs/files.

### Opaque resource completion forms

`McpPanel` exposes **v** on server/resource rows for a JSON uri/argument/value/context
form. `mcp_resource.resource_request()` validates bounded named-string data without
interpreting/fetching URIs or guessing server schemas. Ctrl+Space opens the existing
picker with operator-specified names; acceptance updates the JSON draft only. A
separate Enter dispatches local resource_completion_preview, not resources/read.
Hosts label that selected/edited data and retain normal quoted preview insertion.
JSON argument forms use up to three editor rows within their existing budget;
soft-wrap does not truncate or silently discard the reference/value/context.
Existing resize, repeat-Enter, invalid/busy draft retention and stale-result fences
remain in force. Components still perform no network or application permission work.

### Typed extension form component

`ExtensionDialog` delegates kind form to `ExtensionForm`, with no dependency on
agent application policy. It edits named scalar fields, preserves invalid drafts,
shows title/help/constraints and implements whole-form paged visible confirmation.
Tab/Shift+Tab fields, Space/Left/Right choices, Enter/Ctrl+Enter review, PgUp/PgDn
help/review, Ctrl+E edit and Esc/Ctrl+C cancel are contextual. Multiline Enter adds
a line; pasted newlines/repeat Enter never submit. Budget/visibility invalidation
propagates through the wrapper, including zero rows. Hosts independently validate
declarations/answers and own deadlines/leases/cancellation; returning a dict is not
permission or execution. Flat typed forms do not claim arbitrary custom rendering
or nested-schema UI parity.

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

### Policy-free editor compare-and-set receipts

`EditorActions(snapshot, replace)` captures text/cursor/attachment identities and
issues a random single-use receipt tied to a host-provided owner key. Changed state,
wrong owner/revision or invalidated receipt refuses replacement; the host callback
validates replacement text before mutation. Snapshot and draft handling do not send
input, touch attachments or change application policy. Coding/Claw enforce command-
only authority, session/connection context and dialog precedence outside the library.

### Exact-target extension navigation review

`ExtensionNavigation` composes ExtensionForm for default-deny, whole-page review of
current owner and exact action/session/branch/zero-based checkpoint data. It returns
only bool queued consent; it cannot mutate a runtime or grant tool permissions.
All target fields/warnings must paint after layout changes, paste/repeat confirmation
is inert and zero-row invalidation reaches the actual form. Hosts queue/validate/
dispatch the intent only after the original command settles successfully under
unchanged command/session/connection ownership and existing idle/navigation policy.

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


### Inline custom extension component

`components.extension_custom.ExtensionCustom` consumes bounded viewport-tagged data
and forwards keys/atomic paste through a host-supplied **enqueue-only** callback.
`render()` performs no worker/network I/O; `poll()` handles initial/resize requests.
The component invalidates painted revisions on blur, hidden rows, geometry changes
and view invalidation, tagging requests with a generation to reject delayed frames
from the same-size old view. It exposes busy/refusal feedback without replay.

Only vetted SGR styles/colors survive line sanitization. OSC/APC/DCS, clipboard,
links, graphics, cursor/erase/alternate-screen controls and private cursor markers
cannot pass through. Style resets isolate host components; optional carets are
placed at whole-grapheme boundaries. Host cancellation/redraw/suspend/exit keys are
reserved. `TUI.requires_current_viewport_input` fences EVERY forwarded key and paste
during a physical resize race. Reasserting the same focus owner is idempotent;
normal polling is not a blur/focus transition. Hosts still own permissions, leases,
original completion futures, modal precedence and headless/older-viewer negotiation.


`ExtensionCustomPresentation` manages a temporary fullscreen view using the host's
visibility/policy decision. `InlineLayout` supplies the actual terminal height to
that view; inline and fullscreen remain separate, epoch-fenced presentations.
The component supports sanitized metadata paging (F1 view/details, page/home/end
navigation) and explicitly requested repeat/release events, including release
subscription at the real TUI parser. Default press-only payloads keep the v1 wire
shape. Screen ownership, permission decisions and protocol capability negotiation
remain host concerns. Alternate-screen resize resets/re-homes only the active
alternate painter; it does not clear the saved main renderer or native scrollback.


`ExtensionDialogStack` preserves FIFO among unrelated roots while selecting the
active descendant of an owned custom parent. It caches parent components across
child input/form/custom questions and validates supplied parent linkage/scope;
hosts still enforce their actual runtime and transport leases. Reusing a parent
component restores its draft/state but invalidates old focus/viewport proof. No
worker or socket code runs in this dependency-light selection/cache layer.

### Owned custom-view overlay presentation

`ExtensionCustomPresentation` can attach a validated custom panel to a capturing
`TUI` overlay handle. The existing host overlay options supply percentage/absolute
width and height, anchors, offsets, positions and margins. The panel receives the
actual allocated viewport and uses the same sanitized frame/cursor/input protocol.
Only the owning handle is removed on nested questions, preemption or completion;
other host overlays keep their focus. `has_overlay(exclude=component)` lets a host
check competing visible overlays without treating its own panel as a blocker.

Main-buffer overlay composition anchors the visible tail after a physical resize,
including while an asynchronous custom frame has no cursor. The actual CPR row
still drives cursor moves. No alternate-screen or whole-screen/scrollback erase
is used, and historical rows above the visible viewport are never replayed.
Extension hosts retain approval policy, visibility and focus decisions; component
code does not gain a permission grant or direct terminal access.

### Worker-side custom-view composition

`xdog.tui.components.custom_layout` provides bounded custom-view composition:
`CustomText`, `CustomSpacer`, `CustomChild`, `CustomRow`, `CustomColumn`,
`CustomPadding`, `CustomBox`, `CustomViewport`, `CustomComponent`, and `custom_frame`.

```python
from xdog.tui.components.custom_layout import (
    CustomBox, CustomChild, CustomColumn, CustomRow, CustomText, custom_frame,
)

layout = CustomColumn((
    CustomChild(CustomText("Review"), size=1),
    CustomChild(CustomRow((
        CustomChild(CustomBox(CustomText("Main content", wrap=True), title="Main"), weight=2),
        CustomChild(CustomBox(CustomText("Status"), title="Side"), size=18),
    ), gap=1)),
), gap=1)

def render(width, height):
    return custom_frame(layout, width, height)
```

`size` reserves fixed cells along the parent axis; `weight` distributes remaining
space with deterministic integer largest-remainder allocation. Containers handle
narrow/zero-size viewports, clip at whole graphemes, and isolate SGR styles between
children. Padding uses explicit top/right/bottom/left cells. Box titles are safely
clipped. Text supports wrapping and left/center/right alignment.

`CustomViewport(content, width, height, left=0, top=0)` adds a scrollable virtual
canvas inside any box/row/column or another viewport. The extension owns scroll
state: rebuild the frozen viewport with new `left`/`top` offsets in its existing
input callback. Offsets are display cells/rows, not string bytes; cropping preserves
SGR and complete graphemes, including styled combining/ZWJ sequences. On resize,
offsets clamp to the last full visible canvas. A cursor is translated only while
visible; scrolling offscreen hides it without moving focus to another component.

```python
from xdog.tui.components.custom_layout import CustomViewport

# In render(width, height); update left/top in your api.custom input callback.
return custom_frame(
    CustomViewport(CustomText(document), width=120, height=320, left=left, top=top),
    width, height,
)
```

Virtual dimensions are explicit and bounded: at most 8,192 columns, 4,096 rows,
128 Ki display cells per canvas, 512 Ki cells across all viewport nodes, and
512 KiB retained virtual-frame/component text. This is bounded canvas rendering,
not an unbounded lazy document API; larger documents should use worker-side paging.
Only the cropped host-sized frame crosses the existing protocol. No extra socket
capability, host scrolling callback, direct terminal control or model call is added.

`CustomComponent(component)` adapts an existing **worker-owned** TUI `Component`.
The extension retains its state/focus and dispatches its own input; it should
provide the component's render budget (for example an InputPanel's
`set_render_budget`) when that component does not implement `set_height`.
The adapter extracts one cursor coordinate and strips the private marker before
serialization. Multiple focused cursors are rejected, not selected arbitrarily.

Limits: 256 rendered nodes, depth 12, 32 children per container, 128 KiB text/frame,
200 viewport rows, and 8,192 UTF-8 bytes per output line. Invalid/cyclic layouts or
excessive component output fail visibly through the existing custom-view lifecycle.
Zero-size viewports do not invoke components. Layouts are frozen; component state
remains owned by the extension worker.

Use these helpers **inside** an extension's existing `api.custom` render callback.
Only ordinary sanitized `lines`/`cursor` frame data reaches the client; no layout
object or callback runs on the UI thread or writes terminal commands. Agent does
not gain a TUI dependency. Coding/Claw already install TUI; standalone extension
environments using this optional helper must install `xdog-tui` themselves.
Existing broker revisions, viewport generations, nested dialog leases, fullscreen
ownership and permission gates remain authoritative.

### Explicit Windows clipboard snapshots

`AttachmentReader` now selects a dedicated helper on `win32`, invoked through the
current Python interpreter rather than a shell, PowerShell, clipboard history or
an application automation target. It reads the current Win32 clipboard only when
the composer requests paste. The existing owned process deadline, cancellation,
output bounds and composer epochs still apply; a read never submits a model turn.

The helper prefers PNG/JPEG/WebP data, then CF_DIBV5/CF_DIB (including Windows'
synthesized bitmap formats), then bounded CF_UNICODETEXT. Image errors do not fall
back to text. Clipboard handles remain clipboard-owned: they are locked/copied/
unlocked, never freed. The desktop clipboard is closed before raster conversion,
with sequence checks before and after conversion. Encoded image framing trims
native allocation padding rather than copying unused memory into an attachment.

The dependency-free DIB codec supports CORE/INFO/V4/V5 headers, top-down/bottom-up
RGB, palette, 16/32-bit bitfields with declared alpha, RLE4/RLE8, embedded JPEG/PNG
and bounded embedded ICC profiles. Source raster/pixel and final image limits are
separate: an uncompressed desktop bitmap may be larger than its encoded PNG.
Linked profiles are not followed. Self-contained calibrated RGB now becomes a
bounded inline ICC matrix/TRC profile, retaining channel gamma, chromatic adaptation
and the original RGBA samples. Singular/invalid calibration fails instead of being
silently relabeled sRGB. This is not a declaration that every Windows bitmap,
external profile reference or native desktop is verified.

Linux tests execute the helper/ABI and image conversion contracts with owned
peers, and both real clients exercise the composer transport in private tmux.
Native Windows clipboard/desktop/terminal validation remains **unverified**;
this adapter is not a claim of complete Windows client or platform parity.

### Reviewed Windows clipboard copy

The existing response/selection/transcript copy flow now chooses an owned native
Windows helper before looking for Unix clipboard tools. Reviewed text is passed
as UTF-8 on stdin, never in a shell command or argv, and published as terminated
CF_UNICODETEXT. Unsupported embedded NUL and inputs above 32 MiB fail explicitly
before native work; they are not silently truncated. Export remains available.

The helper creates an invisible message-only owner window (not a NULL clipboard
owner), prepares movable memory and privacy-marker formats before emptying the
clipboard, then transfers handles to the OS. It frees only handles that were not
transferred and closes/destroys owned resources on failures. It requests exclusion
from clipboard monitoring/history/cloud processing; these markers are not a
confidentiality guarantee against other applications or platform behavior.

Pre-canceled operations do not launch a helper. Cancellation or failure after a
write attempt reports an uncertain outcome, reaps the helper and does not retry
or automatically substitute a file write. Existing unavailable-helper fallback,
review panels, session/context fencing and local export/share behavior remain.
Linux owned-peer/private-tmux checks cover both clients' production transport;
native Windows desktop, privacy-flag behavior and overall client parity are still
**unverified**.

Calibrated-color profile tests compare generated ICC transforms against an
independently generated LittleCMS profile when that system library is available.
It is an optional verification dependency, not a runtime package. A missing library
causes an explicit skip and cannot be counted as color-conformance evidence. The
fixed profile creation date/identity is deterministic and does not disclose the
clipboard capture time or user. Conversion happens after releasing the clipboard;
its sequence, pixel/profile/output budgets and owned helper cancellation still apply.

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

### In-view custom layers

`CustomStack(background, layers)` and frozen `CustomLayer(content, left=0, top=0,
width=None, height=None)` compose opaque rectangles inside an extension's own
custom frame. Use them for a popup/menu/tooltip without asking the host for another
overlay or focus handle. Coordinates and sizes are display cells/rows; omitted
sizes use the remaining viewport, and rectangles clip to actual available space.

Each rectangle blanks its entire footprint, including rows with no foreground
text. Clipping preserves SGR and full combining/ZWJ graphemes; cut background glyphs
become overlapping blank cells rather than partial glyphs. A covered background
cursor disappears, a layer cursor is translated, and multiple still-visible focused
cursors are rejected. The worker owns focus/input state and must defocus its old
editor when another visible editor should own the cursor.

Invisible/zero-size layers do not render components. A stack allows at most 32
layers and shares the existing node/depth/text/frame/virtual-canvas limits. Only
sanitized ordinary lines and one cursor coordinate cross the current protocol;
no layout object, callback or terminal-control command reaches a UI host. These
helpers are distinct from the separately negotiated positioned host-overlay API.
