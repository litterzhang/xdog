> **历史归档，不是当前接口、待办或执行授权。**
> 原日期、结论和验证范围保留；当前入口见 [文档索引](../../README.md)。

# Coding and Claw: implemented UI and agent features

**Implementation reference:** XDOG `v2.3.0`, commit `7d7e40b`

**Documented:** October 11, 2026

This guide explains the features implemented and refined during the agent-UI goal:
what they do, where they are available, and the important limits. It is a feature
guide, not the chronological engineering log or a claim of complete compatibility
with every version of Codex, Pi, or Claude.

The goal closed **48 implementation requirements** and **seven verification
requirements**. Of the **64 unique requirements**, **55 are complete**, **five still
need external environments**, and **four native non-Linux verification requirements
are deferred**. The remaining requirements are described at the end; they have not
been silently counted as implemented-and-verified features.

**Authoritative status:** [requirement checklist](agent-ui-requirements.md), backed
by [the machine-readable ledger](agent-ui-requirements.json). Older package README
and audit sections describe intermediate stages; historical “OPEN” labels are not
additional current requirements.

## Contents

1. [Clients and quick reference](#1-clients-and-quick-reference)
2. [Editing, completion, and queued input](#2-editing-completion-and-queued-input)
3. [Sessions, branches, and context](#3-sessions-branches-and-context)
4. [Images and model capability checks](#4-images-and-model-capability-checks)
5. [Transcript inspection and output](#5-transcript-inspection-and-output)
6. [Repository review and planning](#6-repository-review-and-planning)
7. [Child-agent supervision](#7-child-agent-supervision)
8. [Saved drafts and crash recovery](#8-saved-drafts-and-crash-recovery)
9. [MCP, authorization, and credential storage](#9-mcp-authorization-and-credential-storage)
10. [Extensions and custom interfaces](#10-extensions-and-custom-interfaces)
11. [RPC and remote-client capabilities](#11-rpc-and-remote-client-capabilities)
12. [Terminal stability and safety](#12-terminal-stability-and-safety)
13. [Verification and remaining limitations](#13-verification-and-remaining-limitations)
14. [Complete implementation index](#14-complete-implementation-index)

## 1. Clients and quick reference

- **Coding:** a local interactive coding client. It also supports one-shot output
  and JSON-lines RPC; the RPC mode is an integration protocol, not another native UI.
- **Claw:** the terminal frontend connects to an existing gateway. The gateway owns
  group/session state and authorizes remote actions; the screen is not authoritative
  merely because it shows a previously received value.
- **Shared runtime and UI:** both clients use the common agent runtime and terminal
  components. This avoids separate tool loops or inconsistent permission policies.
- **Availability is explicit:** capabilities may require configuration, negotiated
  protocol support, an appropriate original caller, and user review. A feature
  available to a native parent session is not automatically available to a child,
  headless extension, or older remote client.

```sh
# From the workspace root, with the environment already installed/configured:
uv run xdog-coding
uv run xdog-coding -r                 # choose a saved session for this directory
uv run xdog-coding --resume-id ID     # resume an identified session
uv run xdog-claw tui                 # connect to an existing Claw gateway
```

These commands do not imply that a model provider, external MCP server, desktop
clipboard, or credential service has already been configured.

### Common entry points

Keys below are defaults for the native prompt; an open modal/child editor can own
the same key differently. Use `/keys` or F1 for the effective configured bindings.

| Workflow | Entry point | Important behavior |
| --- | --- | --- |
| Command help / key help | `/help [QUERY]`; `/keys` or F1 | Inspect commands and effective shortcuts. |
| Compose | Enter sends; modified Enter inserts a newline | Terminal forwarding of modified keys is required. |
| External editor | `/editor` or Ctrl+G | Uses `VISUAL`, then `EDITOR`, then `vi`; returning does not send. |
| Steering / follow-up | `/steer TEXT` / Alt+S; `/follow-up TEXT` / Alt+F | Uses the active run's supported input boundary, not tool rollback. |
| Queue | `/queue` or Ctrl+Q | `edit ID TEXT`, `remove ID`, `move ID up/down` act in the relevant queue lane. |
| Sessions / history tree | `/sessions` or Alt+R; `/tree` | Browse before activation. |
| Name / new / fork | `/name [NAME]`, `/name -`, `/new`, `/fork` | Inspect/set/clear a name or explicitly change session history. |
| Images | `/attach PATH`, `/paste` or Ctrl+V, `/attachments` | Preview/remove/clear through `/attachments`; clipboard reads are explicit. |
| Transcript | `/transcript [QUERY]` or Alt+H | `/transcript timeline [SESSION]` inspects the combined source without activating it. |
| Fullscreen inspection | `/fullscreen` or `/transcript fullscreen` | A temporary viewer, not a replacement for native-scrollback mode. |
| Copy | `/copy [response\|selection\|entry\|conversation\|all]` or Alt+C | Clipboard operation, not a model/tool call. |
| Export / share | `/export PATH [text\|md\|json] [scope]`; `/share PATH [scope]` | Reviewed new files; HTML sharing is local. |
| Repository diff | `/diff` or Alt+D | Read-only inspection; there is no `/git` command. |
| Plan | `/plan`; `/plan start OBJECTIVE` | Review via `/plan execute REVISION`; mode changes alone do not start work. |
| Child agents | `/agents`; `/agents start [OPTIONS] TASK` | Roster/inspect/steer/follow-up/cancel/wait/close and presets. |
| Parent drafts | `/draft save` or Ctrl+Alt+S; `/draft saved`; `/draft autosave on/off` | Explicit review and optional autosave. |
| Child drafts | `/agents drafts`; `/agents saved`; `/agents autosave on/off` | Separate storage/recovery from parent drafts. |
| Claw gateway drafts | `/draft remote`; `/draft remote-save` or Ctrl+Alt+Shift+S | Claw-specific, independently negotiated; not a Coding local save action. |
| MCP servers | `/mcp`; `/mcp connect NAME`; `/mcp disconnect NAME`; `/mcp refresh NAME` | Explicit connection/catalog management; tool permissions remain separate. |
| Extensions / provider auth | `/extensions`; `/extensions auth menu` | Trusted-extension management and reviewed provider authentication. |
| Model / reasoning / tools | `/model [NAME]`; `/thinking [LEVEL]`; `/tools [QUERY]` | Owner-specific inspection/selection; capability limits still apply. |
| Context compaction | `/compact [FOCUS]` | The current session's explicit compaction path; cancellation remains available. |
| Details / recovery | Ctrl+O; Escape; Ctrl+L | Contextual details/cancel and repaint without clearing scrollback. |

For direct indexed navigation, follow the specific client's command reference;
index conventions are not uniform across every command. The native tree browser
and bare `/fork` avoid assuming an index conversion.

Keymap files live under `$CODING_DIR/keybindings.json` / `$CLAW_DIR/keybindings.json`
when those overrides are used, otherwise the respective
`$XDG_CONFIG_HOME/xdog/coding/` or `xdog/claw/` config directory (with the normal
user-config fallback when XDG is unset). For example:

```json
{
  "external_editor": "f4",
  "details": "ctrl+t"
}
```

This replaces those actions' defaults; use `[]` to disable an action. Do not assign
protected approval or recovery keys. See each package reference for the complete
command syntax and configuration.


## 2. Editing, completion, and queued input

**Requirements: UI-001–004, UI-035.**

### File and path completion

Type `@name` to search workspace names; explicit directory forms include `@src/`,
`./`, `../`, `~/`, and absolute paths. Suggestions support fuzzy matching, directory
navigation, quoted paths, Unicode, and replacement around the current cursor.
Selecting a suggestion with Tab/Enter inserts text without submitting the prompt.

Workspace discovery respects Git ignore/exclude rules, keeps eligible tracked
names, avoids directory-symlink traversal, and performs resumable background work
for large workspaces. The UI reports degraded discovery when Git semantics are
unavailable rather than silently pretending the fallback is equivalent. Explicit
paths can bypass discovery filters, **not tool permissions**.

A completed `@path` remains a textual reference. Completion is not implicit file
reading, attachment upload, or permission to execute a command.

### Configurable shortcuts and an external editor

Actions can be rebound or disabled through each client's `keybindings.json`.
Invalid or conflicting configuration is rejected with a diagnostic rather than
partially applying an unpredictable keymap. Help reflects effective bindings.
Escape and recovery controls remain protected, as do approval/dialog controls.

An external editor can edit the current draft. The handoff belongs to the original
editor/session and preserves the draft on failure or cancellation. Terminal mode,
cursor, and rendering are restored when the editor returns. Editing text never
executes that text.

### Steering and follow-up queues

Input submitted during an active turn can be handled as steering or a follow-up.
The queue can be inspected, edited, removed, and reordered. Cancellation/rejection
recovery can restore parent input to the editor; explicit reviewed return is a
separate child-queue workflow.
Queue records have identities and revisions so a stale display cannot modify a
newer input accidentally.

Plain Enter during work queues another full turn in the local FIFO. Steering is
processed at the next supported SDK checkpoint; explicit follow-up is processed
after the active tool/steering continuation within the same run. Steering does not
interrupt or undo the tool currently running. Local and runtime queue items occupy
separate lanes and cannot be arbitrarily reordered across them.

Queued, accepted, consumed, and completed are different states. Losing a connection
or canceling work does not justify automatically replaying an input with uncertain
effects. Restored user text remains available for explicit review/resubmission.

### Extension command argument completion

Registered extension commands can offer argument suggestions using both prefix
and surrounding argument context. Replacement handles suffixes, cursor-middle
edits, escaped spaces, quotes, subsequent arguments, and Undo. Choosing an argument
never executes the command. Canceled, withdrawn, or outdated registrations cannot
publish suggestions into a different current editor.

## 3. Sessions, branches, and context

**Requirements: UI-005–007; related host controls in UI-032 and UI-043.**

### Search and resume

Browse and search saved sessions, create a new session, or activate an existing
one. Coding's startup picker is working-directory aware. Claw reads authoritative
session data through the gateway rather than switching based only on cached UI
state. Busy or invalid transitions are refused; a failed activation preserves the
previous session and draft.

### Branch and checkpoint navigation

The history view combines sessions, branches, checkpoints, and saved compaction
sources. Users can inspect history before choosing a transition. Navigation retains
a return point instead of treating a jump backward as permission to destroy the
current head. Restores respect complete tool-message boundaries.

Target sessions and their required extension/provider configuration are prepared
before activation. A failed target preflight does not leave the user halfway through
a restore. Viewing an archived source is not the same as admitting it as live model
history.

### Durable names and context controls

Session names persist and participate in search. Branch labels and checkpoint
metadata are separate; `/name` does not independently rename each branch. Native UI, extension APIs,
and applicable RPC requests refer to the actual owning session, not a stale name
from a previous screen. Renaming and navigation validate the relevant revision.

Context usage, model/tool inventory, and saved sources can be inspected. Explicit
compaction uses the existing session pipeline; extension-requested compaction and
other after-run controls wait for the actual original producer to settle before
changing session state. A completed callback alone is not proof that its run ended.

## 4. Images and model capability checks

**Requirements: UI-008–009, UI-015; related RPC support in UI-034/UI-038.**

Parent and child composers accept real image attachments, not placeholder strings.
Users can add supported files or clipboard images, inspect/remove attachments, and
use supported preview paths. Text and typed image bytes are preserved separately
through admission, history, saved drafts, and supported remote transport.

Image checks apply to the **complete rendered model input**, including parent/child
messages and tool/extension-provided images. The selected owning model's advertised
image support, formats, sizes, and counts are checked again after pre-turn hooks.
Unknown or withdrawn capability is refused rather than guessed from a model name.

A rejected submission preserves the user's draft and attachments. Extension-owned
messages cannot quietly consume the user's composer images. Existing historical
context is not incorrectly subjected to the new-composer attachment-count limit.

Clipboard and preview adapters are implemented, including non-Linux adapters where
required. That does **not** mean every desktop/terminal backend has been exercised
natively: real Linux desktop/Kitty validation and deferred non-Linux verification
remain separately listed below.

## 5. Transcript inspection and output

**Requirements: UI-010–011; related source transport in UI-038–039.**

### Read the whole conversation

Search complete transcript sources, move among matches, navigate entries, scroll
long content, and select text. Inspect saved/archived context without activating it.
Tool and reasoning summaries retain access to their full details; streaming thinking
and assistant prose are distinct presentations rather than mixed fragments.

Optional fullscreen inspection and custom views are temporary modes. The normal
client still uses the main terminal buffer and preserves native scrollback. Closing
a view restores the original input focus and cursor.

### Copy, export, and local sharing

Copy a response, entry, or selection. Create private text/Markdown/JSON exports and
explicit local HTML sharing output. Full content and destinations remain reviewable,
including long paths and narrow layouts.

Ordinary copy/export/share does not embed the original image bodies; authorized
canonical binary/image reads are a separate RPC source contract. Text redaction is
heuristic, not a guarantee that arbitrary conversation content contains no secrets.
Review before sharing.

Exports are create-only: they do not silently overwrite an existing file. Sharing
is **local file creation**, not automatic upload to a hosted service. Clipboard
failure does not discard the source content. Exported artifacts are inert data;
reloading a command artifact is not permission to rerun its command or tools.

## 6. Repository review and planning

**Requirements: UI-012–013.**

### Read-only repository changes

Inspect staged, working-tree, and untracked changes. Browse files and complete
hunks, filter results, refresh, and horizontally scroll long diff lines. Review
panels return focus correctly after errors, session changes, or approval prompts.

The review UI does **not** apply patches, revert changes, or approve later writes.
It is a way to inspect the repository, not an alternate mutation path.

### Plan before executing

Plans are structured, session-owned state rather than a heading the model happened
to print. Planning keeps the relevant execution gate read-only. Moving to execution
requires explicit confirmation against the current plan revision and idle state.

Changing planning mode does not itself start a turn; send an ordinary message to
begin the next work. Planning blocks shell/mutation, external MCP, and unknown tools,
even when Coding otherwise uses `allow-all`.

A plan never grants additional tools or bypasses the existing permission hook.
Executing a plan continues through the normal agent runtime instead of creating a
second independent tool loop.

## 7. Child-agent supervision

**Requirements: UI-014–018, UI-046.**

### Start and inspect bounded child work

Create and supervise depth-one child agents with visible status, source/history,
and cancellation. Child setup can select a permitted tool subset, model/reasoning
configuration, budget, and named preset. Children have their own tool approvals and
input ownership; choosing a preset does not raise parent permissions.

Children share the workspace; they are not filesystem-isolated copies. Default
access is read-only, and inherited access does not copy previous approvals.

Child work uses the shared agent runtime. The implementation is not an unbounded
recursive agent hierarchy and does not introduce an unrelated execution loop.

### Send and edit child inputs

Child start, steer, and follow-up composers support text and image attachments.
They are distinct from the parent's draft. Inspect/edit/remove/reorder live queued
inputs and return text to the relevant editor. Budgets, image limits, identity, and
actual acceptance receipts are checked before submission is represented as accepted.

### Recover stopped work without replay

Initial tasks and accepted queue changes are journaled. Stopped or interrupted
children retain recoverable text/image data and explicitly distinguish uncertain
admission from settled consumption. Recovery does not automatically restart a child
or re-execute an input.

Returning an image-bearing queued input uses a two-stage process: prepare and review
an exact copy, then separately review removal from the original queue. Original
revision checks and revocable, single-use grants prevent an old confirmation from
deleting a changed item. A durable return backup protects the recovery path.

Before a consumed/handled-input receipt is published, the corresponding typed
history or handler feedback is checkpointed. A crash between history and queue
writes is labeled uncertain and blocks automatic replay. This protects bookkeeping;
it is **not an exactly-once guarantee for external tool effects**.

## 8. Saved drafts and crash recovery

**Requirements: UI-019–024, UI-040, UI-047.**

### Independent parent and child draft stores

Save text and image-bearing drafts, browse their catalogs, read full content,
preview images, explicitly load/copy a selected record, or review a discard. Parent
and child stores remain separate. Recovery from old contexts is conservative and
can use read-only inspection instead of immediately adopting old state.

Storage uses bounded formats and private **plaintext** files with integrity and ownership checks.
Loading a draft does not restore an old permission policy or implicitly submit it.
Changed/nonempty target editors and stale review state are protected against blind
overwrite.

### Explicit saves and opt-in autosave

Explicit save is available from the draft workflow and a rebindable save shortcut.
Autosave is optional, local-session scoped, and off by default after restart. It is
debounced rather than a promise to synchronously save every keystroke.

The status line distinguishes:

| State | Meaning |
| --- | --- |
| `pending` | The latest observed edit is not acknowledged on disk yet. |
| `writing` | A particular snapshot is still owned by the write/receipt operation. |
| `committed` | The current observed snapshot matches an acknowledged saved snapshot. |
| `uncertain` | A storage error requires explicit inspection/re-enabling; do not assume rollback. |

Edits made during a write remain pending until their own snapshot is saved. Errors
stop automatic repeat writes rather than hiding a failure behind continuous retry.
An owned writer is awaited during cleanup. Quit can be deferred until the latest
required save settles; a new save failure cancels that deferred exit instead of
quietly losing the draft. Exiting the terminal does not shut down the Claw service.

### Remote draft access

Coding RPC and applicable Claw socket clients can negotiate parent-draft catalog,
text/binary reads, reviewed save/discard, and client-composer recovery. Coding RPC
also has independently negotiated saved-child-draft access. Transfers use chunking,
content digests, original ownership, and complete review before a one-use commit.
A protocol client must implement the review/receiver contract; receiving bytes is
not itself permission to overwrite an editor.

Owned Linux process-crash tests cover debounce, in-flight writes, publication, and
receipt boundaries. They demonstrate recovery of a committed snapshot or explicit
uncertainty, **not per-keystroke durability or physical power-loss safety**.

## 9. MCP, authorization, and credential storage

**Requirements: UI-025–029, UI-045, UI-048.**

### Connect, inspect, and manage

MCP integration includes stdio and supported current/legacy HTTP JSON/SSE transports,
server status, discovery, tool calls, timeouts, cancellation, and connection cleanup.
Users can inspect tools, prompts, resources, and resource templates through native
management views. Discovering a tool does not bypass tool approval.

Servers are declared in `mcp_servers`, using `transport: "stdio"` with command/args
and private environment configuration, or `transport: "http"` with URL and header
configuration. The contract also includes timeouts, explicit roots, optional HTTP
OAuth, and `allow_tools` (off by default). Merely declaring a server is not permission
to launch it or use all of its tools.

Schema handling validates declared dialects/references and structured results.
Content preserves supported typed text, images, audio, embedded resources, links,
and explicit errors rather than flattening everything to a string. Prompt and
resource/template argument completion supports review of the resulting request.
Opaque resource identifiers are not silently treated as filesystem paths or fetched
merely because they appear in a response.

### Explicit subscriptions and server-initiated requests

Resource and catalog watches are explicit, scoped subscriptions. Notifications
update status/invalidate the appropriate view; they are not authorization to read
new resource content or start a model turn. Combined watches preserve the limits of
legacy peers rather than claiming a multi-subscription feature they cannot express.

Negotiated client capabilities cover the audited roots, sampling/context,
elicitation, task, and related modern request/notification flows. The host checks
who requested the operation and which capability is still valid. Sensitive sampling,
context exposure, or elicitation steps use the appropriate separate user review;
server support alone is not a host permission grant.

The protocol-version boundary matters:

- The implemented/pinned modern `2026-07-28` path supports per-request
  `input_required` continuation for `tools/call`, `prompts/get`, and `resources/read`,
  with fresh request IDs and exact continuation state.
- Supported legacy versions are `2025-11-25`, `2025-06-18`, `2025-03-26`, and
  `2024-11-05`. Legacy reviewed roots/sampling/elicitation and explicitly enabled
  receiver-side task handling follow their negotiated contracts.
- Modern connections do **not** advertise legacy task handling or accept unsolicited
  modern reverse RPC merely because related legacy functionality exists.

These are implemented protocol targets, not a claim that this document checked
“the latest MCP.” Task/result/cancel and input-required continuation preserve
original ownership. Unsupported or withdrawn capabilities are explicitly unavailable.
Reviewed URL opening is separate from elicitation acknowledgment; neither proves
an external workflow completed.

### OAuth and saved credentials

OAuth support includes PKCE, issuer/resource-origin checks, metadata discovery,
pre-registration and supported dynamic registration/client metadata paths. Supported
client authentication includes basic/post and the implemented HS256/RS256 assertion
contracts. Refresh and credential persistence remain visible, reviewed operations.

The explicit `mcp_credential_backend` choices are `file`, `secret-service`,
`keychain`, and `windows-credential`. In Coding this trust selection is global;
project/session metadata cannot downgrade it. The default private-file credential
store is **plaintext**, explicitly disclosed in
the review. An opt-in Linux Secret Service backend uses an existing available,
unlocked service: it does not start/unlock a service or silently fall back to a
plaintext file. Stored credentials are namespace/resource/profile-bound. Restoring them requires
fresh explicit review; another session cannot reuse an earlier session's review
authority.

macOS Keychain and Windows native credential backends were also implemented with
explicit selection, review, cancellation, and failure handling. Their Linux-runnable
contract tests are not claimed as native macOS/Windows execution.

More: [MCP capability contract](agent-ui-mcp-client-capability-audit.md),
[task contract](agent-ui-mcp-task-contract.md), and
[native credential implementation](agent-ui-native-credential-audit.md).

## 10. Extensions and custom interfaces

**Requirements: UI-030–033, UI-035–037, UI-041, UI-043–044.**

### Runtime-loaded extensions

Trusted Python extension workers can register tools, commands, lifecycle/context/
input hooks, shortcuts, and UI contributions. Registration replacement and disposal
are live operations: withdrawn tools, schemas, approvals, or callbacks cannot retain
old authority. Rich tool/command results preserve typed content and errors, with
lossless artifact export and inert read-only reload where supported.

The public entry point is `xdog.agent.extensions.ExtensionAPI`. Named hooks and
contributions include `on`, `context`, `on_input`, `before_tool`, `after_tool`,
`tool`, `result`, `command`, `notify`, `status`, and `widget`. In Coding, the
`extensions` trust list and `extension_dir` are global settings; project metadata
cannot authorize arbitrary imports.

Extensions are **trusted code**, not sandboxed plugins. Host-mediated APIs preserve
application permissions and ownership; that is not a promise to confine arbitrary
Python code at the operating-system level.

### Forms, dialogs, and composable views

Extensions can use reviewed dialogs, typed forms and multi-selection, inline or
fullscreen custom views, nested views, rows/columns, scrolling, padding, layers, and
positioned overlays. Supported live rendering and focus controls can repaint or
hide/show a view without submitting model input.

Views have bounded frames, display-cell-aware geometry, explicit cursor placement,
current-frame acceptance, and restoration of the original editor. Nested dialogs
return to their own parent view. Hiding, switching, canceling, or withdrawing a view
revokes obsolete handles rather than leaving an invisible interactive owner.

### Editor, session, and inspection APIs

Extensions can inspect the owning model/context usage, session metadata, command
menu, and tool inventory. The inspection can be paged and revision-bound; read access
is not mutation authority.

The API distinguishes `get_editor` / `set_editor` (original interactive-command
composer access) from broader **write-only** `request_editor_text` proposals. The
latter require separately bound original ownership and review, cannot read the draft,
and do not submit a turn. Original-editor compare-and-swap operations protect against
replacing a changed draft. Reviewed editor writes, session naming/navigation, and applicable host
controls are available only in their named invocation scopes. A tool/hook/child or
headless caller cannot borrow the parent's editor or session merely because the
same extension also has an interactive command.

### Input hooks and argument completion

Prompt input interception preserves encoded key events, repeat semantics, atomic
paste, FIFO order, cancellation, and original parent/selected-child ownership.
The release-observation surface is separately negotiated from editable key handling.
Custom views have their own declared event contract.

Terminal protocol reports, arbitrary control bytes, approval input, and emergency
recovery controls are not an extension-owned terminal writer. Hooks cannot transform
an ordinary key into an approval or acquire another view's protected controls.
Command argument completion follows the quote/suffix/replacement contract described
in [editing](#2-editing-completion-and-queued-input).

### Reviewed messages, queues, and after-run controls

Extensions can propose explicit messages, including supported typed images, through
whole-content user review. A successful proposal means queued/admitted according to
its receipt—not that a model turn or tool execution completed. Only the supplied
message is used; user drafts and unrelated uploads are never borrowed.

Active steer/follow-up proposals use the original run's queue and permission path.
Canonical custom-context messages retain their own type, display preference, and
opaque details rather than being forged as user messages:

- `next_turn` waits for the next successfully admitted **user** prompt; a rejected,
  handled, extension-origin, or system input does not consume it.
- Active `steer` / `follow_up` delivery observes the existing agent-loop boundaries.
- Idle non-triggering `steer` / `follow_up` delivery appends context without calling the model.
- An explicitly supported and reviewed trigger continues canonical history through
  the existing continuation pipeline, without inventing a user message.
- Opaque details are not model-visible content. Hiding a message from display does
  **not** hide its content from the model.

The three message APIs intentionally remain distinct:

| API | Intended input path |
| --- | --- |
| `request_message(text, images=...)` | Reviewed explicit new-turn input after command success. |
| `request_queued_input(..., delivery="steer" or "follow_up")` | The original active run's queue. |
| `request_context_message(..., deliver_as=..., trigger_turn=False)` | Canonical custom context and its separately supported continuation modes. |

`has_pending_messages` exposes an owner-bound boolean, not another caller's queued
contents. `get_active_tools` / `set_active_tools(names, revision)` and
`request_compaction` also retain their own review/revision/producer rules. A returned
acceptance boolean is not a durable-completion receipt.

Host APIs also support the audited active-tool selection, queue/pending inspection,
session lifecycle, naming, and compaction controls. After-run mutations wait for the
actual producer and original callback/result-delivery obligations to settle. They
cannot race another producer, silently raise permissions, or use a stale approval.

### Session-owned providers and authentication

Extensions can register model providers with separate public metadata and private
authentication. Provider/model catalogs, capability limits, reasoning choices,
parent/child routing, live add/replace/remove, and saved model aliases are wired to
the owning session. Selection does not mutate a global provider registry or silently
fall back to a different model when the original provider disappears.

Private environment authentication and the implemented OAuth callback/store/refresh
workflow keep credential plumbing separate from public catalogs and use secret-free
authentication diagnostics. Trusted extension code can still disclose credentials
through its own output or metadata. Streaming preserves
partial output, typed failures, cancellation, and actual iterator/worker cleanup.
Live-provider interoperability is still a separate external verification requirement.

`register_provider` / `unregister_provider` expose the lifecycle. Private
`ProviderAuth` supports bearer/API-key credentials resolved from an environment
variable or resolver; the authenticated callback receives its private credential
argument separately from public catalog metadata. Mutually exclusive `ProviderOAuth`
supplies login, refresh, API-key projection, and optional model modification callbacks.
Login can request reviewed authorization/prompt interaction; refresh is explicit.

`provider_auth_control` supports the applicable login/refresh/logout/save/restore/
forget operations. Native storage needs an explicitly selected backend and namespace.
Coding RPC negotiates `extension_provider_auth` but refuses native credential-storage
actions; a remote auth capability does not implicitly gain a local desktop vault.

More: [extension host controls](agent-ui-extension-host-controls.md),
[canonical custom delivery](agent-ui-custom-delivery-contract.md),
[provider/auth contract](agent-ui-provider-auth-audit.md),
[input surfaces](agent-ui-input-surface-audit.md), and
[renderer contract](agent-ui-renderer-contract-audit.md).

## 11. RPC and remote-client capabilities

**Requirements: UI-034, UI-038–042; remote host scopes also UI-041/UI-043.**

Coding's JSON-lines RPC drives the real `AgentSession`: prompts, cancellation,
shutdown, navigation, extension interaction, and child supervision do not run through
an alternate fake session. Claw's socket protocol separately controls its gateway-
owned sessions. The two protocols are not interchangeable.

`rpc_hello` negotiates exact integer `0`/`1` capabilities. Important independent
families include `transcript_sources` versus `transcript_canonical`, `child_sources`
versus `child_canonical`, `transient_events` / `child_transient_events`,
`extension_editor` versus `extension_editor_write`, `extension_custom_views`,
`extension_overlay_controls`, and `terminal_input` / `child_terminal_input`.

Parent draft access independently negotiates `parent_saved_drafts`,
`parent_saved_images`, `parent_saved_save`, and `parent_saved_discard`; Claw also has
`parent_saved_uploads`. There is no single “all remote UI” permission switch.

Remote capabilities are independently negotiated and generally default off. A client
that supports dialogs has not thereby opted into editor mutation, canonical binary
export, child terminal events, draft commits, or custom continuation.

| Capability family | What it adds |
| --- | --- |
| Session and execution lifecycle | Real prompt/abort/quit handling and owner-bound session/navigation operations. |
| Typed image input | Bounded requests, chunked image uploads/references, capability and integrity checks. |
| Display and canonical sources | Read-only transcript views plus explicit canonical typed history and authorized original binary/image sources; these are distinct representations. |
| Transient events and progress | Live, sequenced tool/UI progress distinct from durable transcript records; history export is not a replay of every transient event. |
| Interactive extensions | Negotiated questions/forms, editor compare-and-swap, reviewed host controls and navigation/name operations. |
| Children and drafts | Scoped child approvals/queues/recovery and the reviewed parent/child draft-transfer protocols. |
| Custom and terminal views | Named typed frame/input/resize/focus contracts, including appropriate child views; the remote frontend owns rendering and must implement the contract. |

Chunked reads and commits are tied to source identity, revision, digest, and the
original connection/session. Stale handles, malformed payloads, withdrawn capability,
slow consumers, and disconnects are handled without adopting another owner or
replaying an uncertain action.

RPC EOF, explicit quit/abort, an unread output frame, or a closed/slow peer must
still settle the actual owned producer. Canceling only a wrapper task is not treated
as complete cleanup. A server does not gain permission to print raw terminal control
sequences into a client simply because the client enabled custom UI.

The remote custom-view contract is an XDOG interface, **not a claim of Pi RPC custom-
terminal compatibility or a shipped universal remote frontend**.

More: [Coding integration reference](../../../packages/coding/README.md),
[canonical/binary RPC sources](agent-ui-rpc-canonical-audit.md),
[remote parent drafts](agent-ui-remote-parent-drafts.md), and
[custom RPC contract](agent-ui-rpc-custom-contract-audit.md).

## 12. Terminal stability and safety

The work refined behavior across the feature set, not just added menus:

- **Responsive rendering:** bounded event batches and background work keep the
  input/render loop responsive during streaming, discovery, and long outputs.
- **Unicode correctness:** ANSI-aware wrapping/clipping preserves grapheme clusters,
  emoji/combining sequences, display-cell widths, cursor translation, and padding.
- **Resize correctness:** frame generations and acknowledgments prevent a late
  negative response from invalidating a newer viewport or approving a stale frame.
- **Protected approvals:** the relevant context/action must actually be visible;
  clipped/tiny layouts request a resize rather than treating hidden content as reviewed.
  Repeats, unrelated shortcut input, or extension hooks cannot manufacture approval.
- **Focus and terminal recovery:** view transitions, errors, EOF, external-editor
  return, cancellation, and exit restore the appropriate cursor, input owner, and
  terminal mode without clearing native scrollback.
- **Receipt-ordered lifecycle:** cancellation, child readiness, subscription cleanup,
  disk writers, and completion threads settle their real owners before a later step
  assumes resources are free. Tests observe actual receipts rather than guessed sleeps.
- **No silent replay:** reconnect/recovery exposes uncertain text/data for inspection;
  it does not automatically repeat potentially effectful work.

Ordinary read-only tool calls can run according to policy; mutating calls require
approval. Unattended mutation requires an explicitly selected `allow-all` mode.
Reviewing a plan, importing an extension, selecting a child preset, accepting an MCP
capability, or restoring a draft is not an implicit permission escalation.

**OS-enforced sandboxing was excluded from this goal.** Application permission,
path, ownership, and review guards are not a hostile-code sandbox.

## 13. Verification and remaining limitations

### What was actually verified

Completed verification requirements: **UI-049, UI-051, UI-053–056, UI-064**.

The final frozen current-Linux run completed on October 11, 2026:

- **8,200 tests passed**, zero failures/errors/skips, in a single complete root run.
- **1,427** scoped source/test/fixture/config files had unchanged membership/hashes
  before, during, and after the run; the release index retained these exact bytes.
- All **14 named terminal/safety coverage rows**, spanning **360 test modules**,
  were present and passing. Rows overlap and are not additional requirement counts.
- All **33 previously default-skipped available opt-in cases** ran and passed.
- Ruff, strict mypy across seven namespaces, eight wheel/sdist build pairs with
  metadata/RECORD/source/import checks, and 17 workflow examples passed.
- The installed independent MCP SDK matrix and available local CLI interoperability
  checks ran against owned test peers. Pi's saved public-source contract was reviewed;
  Pi native execution and blanket third-party conformance were not claimed.

Twine was not run locally; package metadata/RECORD checks were used instead. Local
evidence is not a statement that remote release CI or PyPI publication has finished.

See [final reconciliation](agent-ui-final-verification-reconciliation.md),
[named coverage](agent-ui-final-named-gates.md), and
[exact final results](agent-ui-teardown-final-skips-2026-10-11.json). Earlier failed
runs remain in the audit history; they were not relabeled as passing.

### Still required: five external environments

| ID | Missing evidence | Why local tests are not a substitute |
| --- | --- | --- |
| UI-050 | Another named real Linux Secret Service implementation beyond the tested GNOME service | One service does not establish another backend's namespace/lock/cancel/restart behavior. |
| UI-052 | Authorized real provider/model/decoder interoperability | Offline adapters cannot establish live capability limits, binary/error behavior, or provider cancellation. |
| UI-057 | An independently deployed OAuth/MCP service with authorized access | Owned peers do not prove third-party discovery, consent, registration, refresh, and call interoperability. |
| UI-058 | Controlled physical power-loss or an accepted equivalent storage-fault environment | Process-crash/SIGKILL tests do not prove hardware persistence. |
| UI-059 | A real Linux desktop clipboard/compositor and Kitty image-protocol session | Simulated clipboard helpers and tmux do not validate those desktop protocols. |

### Deferred: four native non-Linux checks

**UI-060–063** cover Windows terminal/desktop validation, macOS terminal/desktop
validation, native non-Linux credential-service validation, and other non-current-
Linux native platforms. These are deferred and not claimed passed. Implemented
Keychain/Windows credential adapters are not omitted merely because their native
verification is deferred.

The broader goal remains **blocked on external prerequisites**, not complete.
The release authorization did not waive these requirements. Separate Claw memory-
search/scheduler defects and user-service restarts were not added to this UI goal.

## 14. Complete implementation index

Every implementation ID is listed once here. Verification IDs and remaining external/
platform IDs are covered in the preceding section, not counted as extra features.

| ID | Implemented requirement | Guide section |
| --- | --- | --- |
| UI-001 | File/path completion | [Section 2](#2-editing-completion-and-queued-input) |
| UI-002 | Configurable shortcuts | [Section 2](#2-editing-completion-and-queued-input) |
| UI-003 | External-editor handoff | [Section 2](#2-editing-completion-and-queued-input) |
| UI-004 | Parent steering/follow-up queues | [Section 2](#2-editing-completion-and-queued-input) |
| UI-005 | Searchable session browsing and activation | [Section 3](#3-sessions-branches-and-context) |
| UI-006 | Branches/checkpoints/compaction-source navigation | [Section 3](#3-sessions-branches-and-context) |
| UI-007 | Persistent session names | [Section 3](#3-sessions-branches-and-context) |
| UI-008 | Parent image attachments and clipboard paths | [Section 4](#4-images-and-model-capability-checks) |
| UI-009 | Complete-context image admission | [Section 4](#4-images-and-model-capability-checks) |
| UI-010 | Transcript search/navigation/selection/fullscreen | [Section 5](#5-transcript-inspection-and-output) |
| UI-011 | Copy/export/local sharing | [Section 5](#5-transcript-inspection-and-output) |
| UI-012 | Read-only repository review | [Section 6](#6-repository-review-and-planning) |
| UI-013 | Planning and reviewed execution mode | [Section 6](#6-repository-review-and-planning) |
| UI-014 | Depth-one child supervision and configuration | [Section 7](#7-child-agent-supervision) |
| UI-015 | Child image editing/submission | [Section 7](#7-child-agent-supervision) |
| UI-016 | Editable child queues | [Section 7](#7-child-agent-supervision) |
| UI-017 | Child input journal and stopped/initial-task recovery | [Section 7](#7-child-agent-supervision) |
| UI-018 | Two-stage image-bearing queue return | [Section 7](#7-child-agent-supervision) |
| UI-019 | Local saved-child-draft browser | [Section 8](#8-saved-drafts-and-crash-recovery) |
| UI-020 | Coding RPC saved-child-draft protocol | [Section 8](#8-saved-drafts-and-crash-recovery) |
| UI-021 | Opt-in child draft autosave | [Section 8](#8-saved-drafts-and-crash-recovery) |
| UI-022 | Local parent draft save/recovery | [Section 8](#8-saved-drafts-and-crash-recovery) |
| UI-023 | Explicit draft save shortcut and image previews | [Section 8](#8-saved-drafts-and-crash-recovery) |
| UI-024 | Deferred exit after draft settlement | [Section 8](#8-saved-drafts-and-crash-recovery) |
| UI-025 | MCP transport/discovery/call lifecycle | [Section 9](#9-mcp-authorization-and-credential-storage) |
| UI-026 | MCP schema/typed content/argument completion | [Section 9](#9-mcp-authorization-and-credential-storage) |
| UI-027 | Explicit resource/catalog/combined watches | [Section 9](#9-mcp-authorization-and-credential-storage) |
| UI-028 | OAuth authorization/registration/refresh | [Section 9](#9-mcp-authorization-and-credential-storage) |
| UI-029 | Reviewed file and Linux Secret Service credentials | [Section 9](#9-mcp-authorization-and-credential-storage) |
| UI-030 | Extension workers/hooks/tools/commands lifecycle | [Section 10](#10-extensions-and-custom-interfaces) |
| UI-031 | Native forms/dialogs/custom views | [Section 10](#10-extensions-and-custom-interfaces) |
| UI-032 | Extension editor/session/inspection/arguments | [Section 10](#10-extensions-and-custom-interfaces) |
| UI-033 | Session-owned extension providers/model selection | [Section 10](#10-extensions-and-custom-interfaces) |
| UI-034 | Real-session Coding RPC control | [Section 11](#11-rpc-and-remote-client-capabilities) |
| UI-035 | Argument suffix/quote/replacement semantics | [Section 2](#2-editing-completion-and-queued-input) |
| UI-036 | Named terminal-input interception/observation surfaces | [Section 10](#10-extensions-and-custom-interfaces) |
| UI-037 | Composable renderer/container contract | [Section 10](#10-extensions-and-custom-interfaces) |
| UI-038 | Canonical/binary read-only RPC history sources | [Section 11](#11-rpc-and-remote-client-capabilities) |
| UI-039 | Transient RPC UI/tool-progress sources | [Section 11](#11-rpc-and-remote-client-capabilities) |
| UI-040 | Remote parent draft protocol and recovery | [Section 8](#8-saved-drafts-and-crash-recovery) |
| UI-041 | Extended RPC editor/host invocation scopes | [Section 11](#11-rpc-and-remote-client-capabilities) |
| UI-042 | RPC custom/child-terminal contracts | [Section 11](#11-rpc-and-remote-client-capabilities) |
| UI-043 | Reviewed message/queue/session/after-run host controls | [Section 10](#10-extensions-and-custom-interfaces) |
| UI-044 | Provider auth/registration/binary/error contracts | [Section 10](#10-extensions-and-custom-interfaces) |
| UI-045 | macOS Keychain/Windows credential backend implementation | [Section 9](#9-mcp-authorization-and-credential-storage) |
| UI-046 | History/input admission crash boundaries | [Section 7](#7-child-agent-supervision) |
| UI-047 | Draft debounce/in-flight crash-state visibility | [Section 8](#8-saved-drafts-and-crash-recovery) |
| UI-048 | Additional negotiated MCP client capabilities | [Section 9](#9-mcp-authorization-and-credential-storage) |

## Further reading

- [Coding reference](../../../packages/coding/README.md) — configuration, commands, and RPC details.
- [Claw reference](../../../packages/claw/README.md) — gateway-backed UI and per-group integration.
- [Agent reference](../../../packages/agent/README.md) — extension/runtime/MCP contracts.
- [TUI reference](../../../packages/tui/README.md) — reusable components and rendering contracts.
- [Authoritative requirement checklist](agent-ui-requirements.md) — exact status and acceptance evidence.
- [Historical implementation log](agent-ui-gap-plan.md) — chronological detail, including superseded checkpoints.
