# Coding and Claw UI/capability gap closure

> **2026-10-16: historical audit, superseded where scope conflicts.**
> Current removals, simplifications and exact counts are in
> `agent-ui-simplification-plan.md` / `agent-ui-simplification-requirements.json`.
> Removed features and non-Linux adapters are not current OPEN requirements.

> **2026-10-10 用户范围更新 / 当前计数权威入口**
> 当前范围、完成条件、稳定需求编号及分类数量以
> `agent-ui-requirements.json` / `agent-ui-requirements.md` 为准。
> 本文以下内容是保留的历史审计/证据，不再把重复 OPEN 当当前独立需求。
> 仅非当前 Linux 的原生验证延期且非阻塞；不声称通过。
> Linux 功能、安全、生命周期、传输、终端与最终完整回归不缩减；
> 非 Linux 后端实现与原生验证分开记录。复算：`python tools/agent_ui_checklist.py --check`。


Active goal created **2026-10-05**. This is the implementation/evidence tracker
for the expanded goal, not a declaration of parity with an installed release of
Codex or Pi. The earlier terminal-stability scope in `tui-pi-parity.md` remains
useful regression evidence, but its fullscreen/search/extension exclusions do
**not** constrain this goal. OS-enforced sandboxing is excluded. Existing tool
permission gates remain required and must not be described as a sandbox.

## Completion requirements and status

Application behavior, not standalone helper classes or SDK capabilities, is the
unit of completion. Every row must work in both Coding and Claw where applicable;
gateway-backed operations must use the group/session owner rather than inventing
a client-only model of runtime state. Status remains open until tests exercise the
live wiring, ownership/cancellation/error paths, and terminal interactions.

| Gap | Required end state | Current status / next evidence |
| --- | --- | --- |
| File references and paths | Interactive `@` picker, fuzzy workspace search, quoted paths, directory drilldown, cursor-local replacement, ignore-aware/large-workspace behavior | Implemented in both clients: resumable private name index, full-path fuzzy search, Git ignores/negations, large directory discovery, stable selection and bounded progress. Git-less/failing enumeration is explicitly degraded; platform limitations and evidence below. |
| Shortcuts and external editor | User-configurable action bindings, conflict/error handling, discoverable current hints; external editor round-trip preserves draft and restores terminal on success/failure/cancel | Implemented in both live clients: shared validated keymap, effective hints/help, reserved approval/recovery keys, and owned external-editor round trip; Linux/tmux evidence below. Desktop/Windows manual validation remains open. |
| Steering and queues | Explicit steer versus follow-up delivery, runtime acknowledgement, inspect/edit/remove/reorder individual queued messages, safe cancellation and reconnect ownership | Implemented in both clients: explicit native steer/follow-up, identified editable/reorderable queues, owning-loop/gateway acknowledgements, consumption receipts and no-replay recovery; see pass 4. Images in queued input remain part of the separate attachment row. |
| Sessions and branches | Searchable in-session browser, switching/resume, branch/tree traversal with retained context, correct active-run exclusion and persistence | Implemented in both clients: searchable paginated live browser, branch/checkpoint trees, idle-gated activation/resume/new, retained return heads and safe tool boundaries. Pass 121 adds durable `/name` metadata, named-title search, failure/cancellation/crash recovery and native terminal evidence without history activation. Claw uses group-scoped durable catalog, gateway group lock, lease/correlation and history sync; pass 6 records evidence and limits. |
| Images and clipboard | Real typed image-content attachments, add/remove/preview in composer, supported clipboard paste, provider capability/size errors and persistence/replay | Implemented in both clients with typed SDK/persistence, explicit Linux clipboard adapters, bounded preview and lossless queue/error/no-replay recovery. Pass 7 records core evidence; pass 33 adds the shared explicit macOS image/text snapshot adapter with binary-type priority, TIFF-to-PNG conversion, change-count/bounds guards, owned helper and both-client physical protocol tests. Pass 66 adds explicit Windows native-helper/Unicode/encoded-image/DIB/RLE/profile transport through the shared reader and both real clients; Pass 75 adds self-contained calibrated RGB/ICC retention validated by independent LittleCMS; native execution remains open. The real Apple JXA bridge/macOS desktop, Kitty/iTerm and Windows validation remain OPEN. |
| Transcript interaction | Full-history search, match navigation, selection and scroll controls; optional finished fullscreen mode without losing the default native-scrollback workflow | Implemented in both clients: full-source search over every occurrence, scrolling/message navigation, whole-line/range/entry selection and an opt-in full-height alternate viewer preserving native scrollback. Claw now retrieves untruncated, redacted canonical/live source through verified bounded chunks with session/head/connection fencing; pass 9 closes the transport limitation found in pass 8. Cross-platform manual validation remains open. |
| Copy/export/share | Select/copy response and export transcript through UI, clipboard fallback/errors; intentional, explicit sharing with sensitive-content safeguards | Implemented in both clients: response/selection/entry copy, private text/Markdown/JSON exports and reviewed local HTML share artifacts. Pass 67 adds owned native Windows Unicode copy with explicit pre-commit checks, clipboard-owner/handle cleanup and both-client transport evidence; native desktop validation stays open. Full-source/immutable ownership, clipboard fallback/errors, no overwrite/upload and approval/context fencing are verified in pass 10. Pass 83 adds focus-owned paginated metadata/destination review and separate captured payload preview on narrow terminals, with full-path/grapheme proof, layout/edit/blur invalidation and actual both-client long-path saves. Platform/manual limits remain explicit. |
| Changes/review | Repository-wide diff/change browser, file/hunk navigation, safe review workflow; read-only inspection cannot accidentally apply/revert changes | Implemented in both clients: repository-wide status, staged/working/untracked inspection, file filter/navigation, disk-paged patches/all hunks, horizontal scroll, nested scopes, refresh and read-only focus/permission lifecycle; pass 5 records evidence and platform limits. |
| MCP | Configured client transports, discovery/tool calls, runtime lifecycle, management/status UI, permission enforcement, cancellation/timeouts, reconnect errors | Partial: explicit stdio and current/legacy HTTP JSON/SSE, discovery/schema-validated SDK proxies, ownership/permission gates and focused management/catalog/preview/draft/export workflows plus bounded legacy cursor recovery are wired into both clients (passes 11-14). Static/environment credentials and no-replay/auth/redirect errors remain supported. Pass 27 added the explicit pre-registered public-client PKCE foundation; pass 28 wires visible consent, status/cancel/local-forget, view/session/connection leases and actual callbacks in BOTH clients. Primary authorization docs are now retrievable and were checked. Pass 29 adds retained denial pointers/scopes, protected-resource root fallback and ordered OAuth/OIDC issuer discovery with origin pins and no replay. Pass 30 adds explicitly selected hosted client ID metadata and deprecated public-client DCR, both behind the same visible consent and lease gates. Pass 31 adds explicitly configured pre-registered client_secret_basic/client_secret_post using token-only environment-backed secrets and visible method/source review. Pass 76 adds explicitly configured pre-registered HS256 client_secret_jwt with fresh short-lived endpoint-bound assertions, method/algorithm/source consent, JWT/client/terminal evidence and no uncertain replay. Pass 32 adds explicitly reviewed resource-bound in-memory refresh, actual-grant scope ceilings, rotation and no uncertain-token replay with live-view cancellation. Pass 59 adds separately reviewed private plaintext POSIX credential save, explicit restart restore/reload and saved-file erase in both clients, with resource/profile/TLS/scope/expiry binding, original-view receipts, atomic owned I/O, refresh snapshot invalidation and actual client/terminal evidence. Pass 104 adds explicitly pre-registered private_key_jwt/RS256 with POSIX/OpenSSL, independently verified RSA signatures, bounded key-only stdin/minimal environment, owned async process cleanup and both-client reviewed exchange/refresh; broader confidential methods/algorithms, native keychain/Windows storage, further registration/refresh/external interoperability remain OPEN. Pass 34 adds explicit named prompt/template completion with actual current/legacy SDK transport, both live UI paths, bounded ranked results, context/draft/cancellation ownership and scalar URI-template insertion. Pass 35 adds explicitly named opaque/dynamic resource-reference completion, completion-only server negotiation, separate Claw feature gating, local selected-data review and both production UI paths without URI inference/read. Pass 36 adds dialect-aware schema checks, offline bundled refs, bounded owned validation, pre-/post-hook argument checks and finite typed current structured results (including explicit null) through models, both UIs and persistence. Pass 70 retains typed text/image/structured MCP failures with error status through both clients, permissions and persistence. Pass 79 unifies inert text/audio/resource/link/annotation validation and preserves text/image metadata in indexed details plus visible output, with preview/binary admission separation, both client persistence/policy checks and independent serving-SDK schema/transport evidence. Pass 82 preserves explicit preview/export content types including zero-byte binary payloads, negotiated Claw compatibility/no-downgrade and fixes actual destination-edit focus/submission bugs through both real clients. Pass 69 independently verifies prompt/resource/template/completion paths over both transports and adds lossless inert resource-link prompt previews in both clients. Pass 68 independently verifies serving-SDK 2.3.1 stdio/HTTP subscriptions, schema corpus and both real clients; other MCP conformance requirements remain open. Pass 58 wires explicit resource-watch notices and unwatch/view cleanup into both clients, with bounded SDK/stdio/HTTP current-dialect and legacy lifetimes, shared legacy URI ownership, no automatic reads/model admission, owned peer and terminal evidence. Pass 93 adds explicit tools/prompts/resources catalog-change watches with exact single-filter negotiation over current stdio/HTTP, declared legacy notice routing, both-client notice-only UI and independent serving-SDK evidence. Pass 96 adds exact combined catalog/URI subscription sets on one current stdio/HTTP stream, editable Alt+W forms in both clients, independent Claw capability withdrawal, complete filter validation and independent serving-SDK evidence; legacy combined streams are explicitly unavailable while existing singleton support is retained. Broader third-party/current protocol conformance beyond the independent SDK cases, schema/content/provider interoperability and negotiated client capabilities remain OPEN. |
| Extensions | Load/activate runner, events/tools/commands/UI contributions and disposal with documented boundaries; errors must not corrupt terminal ownership | Partial in BOTH clients: explicitly trusted owned workers, hooks/tools/namespaced commands, declarative UI and fail-closed effective-argument validation (pass 16); explicit kind/key-scoped status/widget removal, copied contributions, atomic batch validation and admitted/current-worker UI fencing (pass 89); durable branch-aware entries/full pre-model context (17), owned idempotent event/tool/context/input hook disposers with stable per-invocation dispatch and current-worker monotonic live declarations (88), live command/shortcut replacement/disposal and actual autocomplete/binding catalog refresh without restart (90), owned live tool schema/callback identity replacement/disposal, stale approval/executor denial and actual same-loop parent/child inventory projection (91), owned basic dialogs (18), parent/child typed forms (37) and typed array-valued multi-selection with independent old-viewer negotiation, focus-owned whole review and physical Space matching (87), rich typed tool content/metadata (38), rich read-only command artifacts (39) and reviewed lossless export/reload (40). Command editor CAS (41), session metadata/native browsing (42), reviewed direct navigation (43), bounded paginated host catalogs (44), registered/remappable conflict-safe shortcuts (45), canonical submitted-input transforms/refusal (46) and explicit handled feedback/receipts without model-history impersonation (47) use actual host lifecycles. Pass 48 adds the custom-view worker/broker foundation; pass 49 wires sanitized viewport-aware inline custom views in both clients and independent children with live ownership, revision/epoch/physical-resize fences and real parent/child terminal evidence. Pass 50 adds owned fullscreen, sanitized paginated metadata and opt-in enhanced input with actual parser/parent/child terminal evidence. Pass 51 wires bounded owned nested questions/custom stacks in both clients and child workers, with parent-state restoration, depth/cascade and worker-task cleanup. Pass 52 adds serial worker hooks and bounded arbitration foundations; pass 53 wires prompt keys/atomic paste, protected-control validation, host-only pause/recovery and cancellation into Coding with real worker/client/terminal evidence. Pass 54 wires Claw parent-prompt interception over negotiated connection/group/session/UI/worker-generation leases, bounded reader-responsive jobs, strict snapshots, monotonic no-replay sequences and real worker/socket/app/terminal evidence. Pass 55 batches bounded editable key spans rather than per-character RPCs and refreshes hook registrations through actual workers and both clients, with same-owner once-only drain after unsubscribe and command re-registration without reconnect. Pass 56 wires active selected-child steering/follow-up editors to their own live worker in both clients, with form generations, owned UI attachments, negotiated Claw child leases and real child/transport/terminal evidence. Start/settings/confirmation/approval stay host-owned. Broader raw surfaces remain OPEN. Pass 57 adds bounded worker-side fixed/weighted row/column, padding, titled box, safe text/spacer and existing-component composition, with grapheme-safe single-cursor frames and real both-client/child/nested/inline/fullscreen evidence. Pass 77 adds composable bounded virtual scroll containers, display-cell/row offsets, resize clamping, styled grapheme cropping and visible-cursor translation through real parent/child/nested/inline/fullscreen paths. Pass 78 wires explicit positioned capturing host overlays through the actual API/broker and both clients, independently negotiated Claw capability, original scoped handles/focus and native resize recovery. Pass 86 adds frozen composable in-view opaque layers for worker-owned menus/tooltips, grapheme/SGR-safe cell composition and cursor occlusion/translation through both parent/child and inline/fullscreen/host-overlay paths. Pass 95 adds invocation-owned background `request_render()` with coalesced/rate-bounded live frames, hidden-view deferral and queued/in-flight completion revocation through both clients and independent children. Pass 97 adds owned live hide/show/focus/unfocus controls and non-capturing host overlays, independent Claw negotiation/no downgrade, nearest-view and replacement-task fencing, cached nested-parent state, protected host focus and real ordinary keyboard/paste editing through both clients/children. Further arbitrary custom containers/rendering APIs remain open. Arbitrary richer renderers, broader raw-key interception, Pass 60 wires explicit session-owned callback provider declarations/catalogs/model selection/streams and independently routed child overrides into both clients, preserving the same tool loop and permissions without global AI registry mutation. Pass 61 restores configured/saved alias startup and in-session activation in Coding, configured-before-ready and saved-restart activation in Claw, cached limits/image metadata and clean no-fallback bootstrap teardown. Pass 64 adds typed public provider failures/cancellation, exact custom async-iterator cleanup, retained/persisted partial errors in both clients and correct independently owned child-abort status. Pass 65 adds session-owned live add/explicit replacement/removal, current catalogs/capabilities and per-registration stream fencing in both clients without restarting workers. Pass 71 projects real native model IDs and separate prompt/effort/vision limits through both clients and child routing, with actual admission and compaction gates. Pass 72 adds negotiated parent /thinking inspection/settings in Claw, scoped persistence and cancellation recovery without model submission. Pass 73 adds same-adapter configured native /model catalog/selection, canonical scoped resume/defaults and secret-free public summaries. Pass 80 adds owned read-only command get_model/get_context_usage snapshots in both clients, actual gateway owner reads instead of stale UI data, explicit missing-measurement/age semantics, current capability/vision fields and independently negotiated older-viewer denial; Pass 81 extends inspection to invocation-owned tools/hooks/context/custom/provider/child/headless callbacks, with actual child initialization before startup hooks and no borrowed parent usage; Pass 98 adds paged owning-Agent tool-inventory inspection through actual commands/tools/hooks/headless/parent/child invocation envelopes, exact copied public schemas, capability-gate separation, stable metadata revision and early child-profile filtering; Pass 100 surfaces the same owning configured definitions through native /tools browsers in both clients, full-schema/name search, explicit refresh/fullscreen, scoped output records and cold Claw-session negotiation with no model/tool turn; other host APIs remain open. Further native/provider/auth and other host APIs, binary/error interop and native-platform/older-viewer validation remain OPEN; no subset waives those requirements. |
| Explicit context compaction | Native idle-only `/compact` with optional focus, actual selected model routing, recent-turn/tool-boundary retention, cancellation, failure preservation and durable owner state | Implemented in both clients (pass 110): Claw negotiated owner-backed control/cancel/no-replay receipts; Coding no longer hides failures or bypasses its adapter/limits. Registered extension providers, disk failure, repeated cancellation through admitted commits, clean Claw summary restore and native terminal recovery are verified. Pass 111 adds real extension preparation/veto/custom-summary/committed-observer hooks with validated retained-user boundaries and branch metadata. Pass 112 adds atomic/indexed complete pre-compaction recovery checkpoints, actual tree restoration and create-only private same-second-safe archives. Pass 113 adds read-only full-source viewing/search/output of selected branches and other saved sessions without activation. Pass 119 adds all-branch session timeline search with conservative recorded-lineage merging and source provenance; no guessed total parallel chronology. Cross-platform/native validation remains open. |
| Planning | Explicit inspectable plan workflow and plan state; transition into execution must preserve permission policy and session ownership | Implemented in both live clients: frozen session-owned structured plans, revision-bound metadata tool, read-only host gates, inspect/scroll UI and visible idle-only execution/stop confirmation, retained permission policy, persistence/branches and no automatic turn submission. Pass 15 records Linux terminal evidence and refresh/checkpoint/platform limits. |
| Interactive child-agent supervision | Start/inspect/steer/cancel child work, status/results/errors, bounded resource ownership and inherited permission restrictions | Partial implementation in BOTH clients (passes 19–20): session/branch-owned factories, mandatory parent/child extension validation, read-only and inherited tool gates, independent Coding approval queues, persistent catalogs, bounded reader-responsive Claw controls and actual `/agents` commands. Pass 21 adds a focused live roster, child-source transcript/search/selection, visible start/steer/follow-up/cancel/close forms, optional fullscreen and scoped copy/export/share in both clients, including bounded checksummed Claw source transport. Pass 22 adds bounded running-tool progress to the same complete-source viewers. Pass 23 wires independently owned select/input/confirm child dialogs in both clients, including connection-scoped answers and detached-UI cancellation. Pass 24 adds per-child labels/instructions, fresh context, explicit inherited-tool subsets and downward request/token limits through tool schema, commands, gateway and reviewed UI settings. Pass 25 adds per-child model/reasoning selection using the actual adapter, capability checks, continuity cleanup and persisted effective choices. Pass 26 adds immutable named presets, explicit catalog browsing, CLI/tool/JSON selection, snapshot persistence and fully reviewed preset starts. Bounded depth-one supervision is implemented on the verified Linux path; arbitrary richer extension UI and broader platform validation remain on their original audit rows. |

No row is waived merely because another CLI deliberately does not provide it.
The goal is the union of identified applicable features, not reproducing every
vendor-specific hosted service or OS isolation mechanism.

## Pass 1 — file/path completion

Historical first-slice evidence; pass 3 below supersedes its total-entry cutoff
and lack of ignore handling.

Shared implementation: `xdog.tui.file_completion.WorkspaceFileCompletionProvider`
and `InputPanel.set_autocomplete_provider` / `poll_autocomplete`.

- `@name` fuzzy-searches workspace-relative names. `@src/`, `./`, `../`, `~/`,
  and absolute paths browse a directory; double quotes preserve paths with
  spaces and quotes. Up/Down selects; Tab/Enter accepts **without submission**.
  Directory selection opens the next level. A subsequent Enter sends the draft.
- Replacement affects only the token around the cursor, including any old token
  suffix. Surrounding prose, multiline text, undo and hardware cursor survive.
  Slash-command Enter retains its existing command submission behavior.
- Escape dismisses loading or visible completion before canceling active work;
  polling does not reopen dismissed suggestions. Tab explicitly reopens them.
  Focused permission/details surfaces keep ownership over background suggestions.
- Filesystem access and fuzzy filtering happen on one lazily started daemon
  worker. Requests coalesce, results are immutable, and superseded results cannot
  overwrite a newer prefix. Shutdown closes the worker without blocking terminal
  restoration on an uninterruptible filesystem call. It never touches UI state.
- Results refresh after three seconds. A scan examines at most 20,000 entries
  for 200 ms between filesystem operations, returning at most 20 suggestions.
  Common generated/internal directories and hidden names are omitted from fuzzy
  recursive search; explicit paths can still browse them. Symlink directories
  are not recursively traversed. These are responsiveness guards, **not** a
  security boundary or `.gitignore` support. Very large/slow workspaces can have
  incomplete results; ignore-aware, incremental indexing remains on the row's
  completion checklist rather than being claimed finished.
- Completion never reads contents, creates image attachments, invokes tools or
  grants permission. Inserted references remain prompt text for the agent's
  normal tool-mediated reads. Unsafe control-character names are omitted.
- Coding roots suggestions at its session working directory. Claw obtains
  `workspace_dir` through the group's ping/pong session information; it never
  guesses the group root from the TUI launch directory. Workspace changes close
  the old provider. Older gateways without that field disable file completion
  while retaining slash-command editing.
- Unchanged drafts cache their parsed token span: polling does not repeatedly
  scan large drafts. Shared fuzzy matching now maps Unicode lowercase expansion
  back to original offsets instead of crashing on names such as `İfile.py`.

### Evidence

- `packages/tui/tests/test_file_completion.py`: quoted/relative/home/absolute
  paths, recursive exclusions, symlink loops, scan/result limits, coalescing,
  stale results, close, refresh, Unicode, local replacement, undo, dismissal,
  async polling, provider replacement, tiny layouts and unchanged large drafts.
- `packages/tui/tests/test_fuzzy.py`: original Unicode highlight offsets.
- Coding `test_interactive_commands.py`: actual app polling, selection does not
  start a turn, worker closes on exit.
- Claw `test_inline_behavior.py`: actual app polling, gateway workspace selection,
  reconnect/provider fencing, legacy-peer behavior and exit.
- Claw `test_gateway.py`: real Unix-socket ping advertises an existing absolute
  group workspace.
- Coding `test_terminal_acceptance.py`: actual offline Coding CLI and production
  Claw socket client accept file suggestions without sending a turn, preserve
  cursor/draft, and restore terminal modes. Coding additionally tests quoted
  filenames, resize and Ctrl+L. No paid calls or live gateway restart.

Verification snapshot — **2026-10-05**:

- Full workspace: **2,313 passed, 7 skipped**, including **13** private-tmux
  terminal acceptance cases.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **330 source files**, no errors (unused-override notes only).
- Flow examples with `--allow-script-stub`: **17 passed**.
- `git diff --check`: clean. Saved AI-package/`uv.lock` fingerprints unchanged.
  No commit, paid model request, or live gateway restart.

Runtime platform evidence remains Linux/private tmux, not Windows or IME parity.
Future passes must retain all earlier stability regressions; see the historical
snapshot in `tui-pi-parity.md` rather than treating that snapshot as a fresh run.

## Pass 2 — configurable keys and external-editor ownership

Implemented in the shared `PromptKeymap`, `InputPanel`, `TUI`, `StdinBuffer`,
`MainBufferRenderer`, and `edit_draft`, then wired into both actual applications.
The older Coding `core/keybindings.py` is explicitly marked legacy compatibility;
it is not advertised as the live map.

- Each client loads its own user-config `keybindings.json` once at startup.
  Action overrides replace defaults; strings/lists and `[]` disable are supported.
  Unknown actions, duplicate JSON entries, duplicate normalized keys, conflicting
  defaults, unsupported descriptors and oversized files are rejected atomically.
  Defaults remain usable with a diagnostic, not a raw exception. No project
  configuration can silently rebind keys or select an executable.
- Prompt navigation/history/editing, newline, autocomplete, undo/redo, kill/yank,
  interrupt/exit, suspend, details and external-editor actions use the live map.
  Removed bindings do not silently invoke old hard-coded actions. Help (`F1` or
  `/keys`) and prompt hints derive from that same effective map. Modal navigation
  and permissions are not translated through prompt bindings. Escape and Ctrl+L
  remain fixed contextual-denial/recovery routes. Repeat/release events do not
  launch editors or submit/interrupt a second time.
- Default Ctrl+G or local `/editor` schedules a foreground callback **after** raw
  mode and terminal-protocol cleanup, not nested inside input dispatch. Coalesced
  pending keys cannot accidentally submit after handing off. Active agent/socket
  workers can continue producing immutable events, but the UI does not poll or
  paint until the external owner returns.
- Only the user's VISUAL/EDITOR environment (then `vi`) selects the command.
  Quoted argv is parsed without shell evaluation; the private draft path is
  appended. Coding uses its session cwd; Claw uses gateway workspace metadata
  when available. Files are private UTF-8 regular files capped at 1 MiB, and are
  removed after the child is stopped/reaped. Symlink/FIFO output is rejected.
- Successful edits update only the composer and retain undo. Nonzero status,
  interruption, cancellation, invalid/missing output or missing executable keep
  the original draft/cursor. There is no eager submit or agent abort/rollback.
  Edit requests while a permission/details/overlay has focus are refused.
- The handoff clears only owned transient rows. Resume negotiates input again,
  adopts the current cursor, and paints a fresh tail without replaying unchanged
  durable history over external output. Cooked terminal settings are preserved
  around the callback so even a child that leaves raw mode cannot redefine the
  shell's eventual defaults. Terminal failure/EOF/stop cleanup remains owned.

Evidence:

- `test_prompt_keymap.py`: validation, malformed/duplicate/oversized config,
  atomic fallback, disabled/replaced keys, custom motion/undo/completion,
  repeat/release suppression and effective help.
- `test_external_editor.py`: actual subprocess success/UTF-8 edits, file mode and
  cleanup, VISUAL/EDITOR precedence, literal argv/no shell, failure and bad output,
  private-file bounds, interruption and child termination.
- `test_terminal_lifecycle.py`: real PTY raw/cooked restoration before/after the
  callback, failing child mode changes, callback exception, stop, and coalesced
  input quarantine. `test_main_buffer_renderer.py` verifies transient clearing
  and no history replay at the external cursor.
- `test_interactive_keymap.py`: both real app adapters load their user maps,
  derive hints/help, dispatch custom shortcuts, apply edits once without model or
  gateway submission, preserve cursor/undo, and refuse inappropriate focus or
  handoff state. A custom send binding cannot confirm Coding permissions.
- `test_terminal_acceptance.py`: actual offline Coding and production Claw client
  under private tmux. Both use custom send/editor keys, a controlled real child
  reading cooked stdin, edited multiline drafts, exit status 7 while leaving raw
  mode, Ctrl+C interruption, resize/repaint, explicit later submission and final
  termios restoration. No live gateway restart or paid calls.

Verification snapshot — **2026-10-05**, pass 2:

- Full workspace: **2,369 passed, 7 skipped**, including **15** private-tmux
  terminal acceptance cases. Post-type-cleanup keymap/app checks: **35 passed**.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **332 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- Saved unrelated AI/`uv.lock` fingerprints remained unchanged. No commit,
  paid model request or live gateway restart.

Scope is still open across the remaining table rows. Platform evidence is the
Linux/private-tmux profile; desktop GUI wait behavior, IME integration and Windows
remain documented manual checks, not implied by a green suite.

## Pass 3 — ignore-aware, resumable workspace indexing

The file/path row now uses worker-owned `WorkspaceIndex` plus the existing live
provider/editor wiring. The first pass's 20,000-entry/time cutoffs are **batch
budgets**, not terminal truncation points. Source enumeration and query matching
retain their positions until complete; explicit directory scans also resume.

- Eligible names live in a private mode-0600 SQLite database. Filtering keeps at
  most the requested result count in memory instead of building/sorting an
  unbounded candidate list. Partial matches are usable during indexing, and
  selected path identity survives newly arriving higher-ranked results. Fuzzy
  queries such as `@sr/hp` match full relative paths; a trailing directory slash
  opens explicit directory browsing. Directory ancestry remains selectable.
- Git handles nested `.gitignore` patterns/negations, `.git/info/exclude`, and
  user global excludes. Cached/tracked ignored names remain eligible; deleted
  tracked names are skipped. Subdirectory roots are scoped while retaining
  applicable parent ignore rules. Initialized submodules and nested repositories
  are recursively enumerated under their own ignore rules with the same child
  ownership and root-relative names. Untracked hidden/generated paths are omitted
  in addition to Git ignores, while tracked hidden/generated names can appear.
- Non-repositories use a private temporary bare Git directory to apply the same
  ignore rules without creating or modifying `.git` in the user's workspace.
  No shell is used. `core.fsmonitor`/untracked caching and hooks are disabled,
  repository-redirection environment variables are cleared, and optional locks
  are disabled. Tests verify that fsmonitor hooks do not execute and the actual
  repository index bytes remain unchanged.
- A missing/failing Git executable or 30-second stalled enumeration is an
  explicitly labeled **basic-exclusion fallback**, with the reason available in
  status. That resumable walker omits common generated/hidden directories and
  does not follow directory symlink loops; it does not claim Git ignore semantics.
  Explicit paths intentionally bypass ignores, never tool permission gates.
- Git stdout is spooled through independent write/read file descriptions; split
  NUL-delimited records survive across batches without moving the child's write
  offset. Malformed oversized records are bounded. One owned child is stopped
  and reaped before fallback/close; index, spool, and Git metadata are cleaned
  when the worker exits. The hidden private-cache prefix prevents self-indexing
  even if the user's temporary directory is inside the workspace.
- Progress/fallback snapshots use the optional `CompletionStatusProvider` hook.
  The editor sanitizes and bounds status within its existing three-row menu;
  status is never selectable/insertable, and tiny terminals preserve edit space.
  Completed unchanged polls do not trigger repaints. Known exact slash commands
  no longer start accidental absolute-path lookups.
- Three-second refresh reconstructs the index so new files appear and deleted
  or newly ignored files disappear. Lowercase-expanding Unicode and joined emoji
  names are supported; unsafe terminal controls/bidi/surrogate names are omitted.

Evidence:

- `test_workspace_index.py`: real Git nested/negated/global/info rules; tracked
  ignored and hidden files; explicit ignored directories; non-repository ignores
  without workspace metadata; subdirectory/worktree scope; initialized submodule
  and nested-repository contents/ignores; environment isolation; no
  fsmonitor execution; unchanged Git index; missing Git with visible degradation;
  a **20,051-file directory** with late-file discovery in root and explicit scans;
  bounded results; live refresh; coalescing/close; actual child termination;
  split records and stalled enumeration; private-cache cleanup/self-exclusion;
  Unicode/emoji; sanitized progress in tiny layouts; stable selection; exact
  commands avoiding file lookup; and SQLite failure feedback without a thread
  traceback.
- Existing file/editor tests now assert eventual full discovery over small batch
  limits rather than treating truncation as success.
- Actual Coding/Claw private-tmux clients use the same provider, ignore one file,
  select its eligible sibling without starting a turn, show Git progress, preserve
  draft/cursor through resize/repaint, and never create workspace `.git` metadata.
  No paid calls or live gateway restart.

Verification snapshot — **2026-10-05**, pass 3:

- Full workspace: **2,391 passed, 7 skipped**, including **17** actual private-tmux
  client cases; **20** focused workspace-index cases include real Git/submodules
  and a 20,051-file directory.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **333 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- Saved unrelated AI/`uv.lock` fingerprints unchanged. No workspace commit,
  paid model call, or live gateway restart. Git commits/clones in index tests
  are isolated ephemeral local fixtures only.

Remaining limitations: missing Git cannot supply Git ignores; an uninterruptible
kernel filesystem operation can exceed a batch deadline (budgets are checked
between operations). Uninitialized nested repositories remain directory references
rather than invented contents; failed nested enumeration is reported in status.
Desktop terminal/IME and Windows checks remain unverified. These are documented behavior limits, not OS isolation
or a claim that the full agent UI/capability goal is complete.

## Pass 4 — identified steering/follow-ups and editable queues

Both applications now expose explicit delivery lanes without replacing the shared
agent loop. The new agent API is additive: legacy `steer()`/`follow_up()` behavior
and configured ONE_AT_A_TIME/ALL modes remain intact.

- Default Enter keeps the local full-turn FIFO. `/steer TEXT` / configurable Alt+S
  uses native SDK checkpoints; `/follow-up TEXT` / Alt+F uses the SDK's post-tool/
  steering continuation. No running tool/model call is preempted by steering and
  no effects are undone. Idle/closing/canceling owners reject delivery and preserve
  text instead of leaving a hidden message to contaminate a future run.
- `/queue` / Ctrl+Q lists stable IDs, lanes and state. Edit/remove/move commands
  operate on the actual queue; moves stay within the lane. Local qN entries are
  locked with the worker FIFO and their IDs remain stable through reorder/pop.
  Runtime iN entries use immutable SDK snapshots and owning-loop requests.
  Selected/consumed records refuse mutation. Rejected edit text remains editable.
  Slash-leading edited text is literal queued input, never a delayed UI command.
- `QueuedInput` and `InputConsumedEvent` distinguish queued acceptance, checkpoint
  selection and admission to agent history. Identity—not content equality—is used
  for edits/removal, so identical messages do not affect the wrong item. After
  settlement, scoped drains recover selected-but-unadmitted inputs and leave
  other owners/legacy queues untouched. Persistence is still session-owned.
- Coding routes control work with `call_soon_threadsafe`, active-generation and
  cancellation checks, then applies immutable acknowledgements on the UI thread.
  It renders consumed user inputs in sequence between assistant responses and
  restores only the undelivered remainder on cancel/error. No callback mutates
  editor/terminal state from an agent thread. Queue inspection/updates retain the
  active phase/timer and previews remain bounded.
- Claw advertises version-1 controls in ping/pong and refuses unsupported legacy
  delivery locally. Gateway requests are scoped to the connection's owned run
  and group, with namespaced backend IDs. SDK control methods never construct a
  new session or bypass runtime/tool hooks. Duplicate request IDs return cached
  receipts, while conflicting reuse and foreign runs are rejected. Snapshot ACKs
  contain bounded IDs/state, not megabytes of repeated text; consumed/returned
  bodies are individual bounded events. Unconsumed IDs are drained on settlement
  and disconnect, not carried into a successor.
- Claw input receipts are connection-fenced, run/ID checked and shape validated.
  Cancel defers runtime-text restoration until consumed/returned receipts; lost
  connections or missing final receipts preserve uncertain inputs/replacement
  variants ahead of the draft with an inspection warning. Nothing is automatically
  replayed, and acceptance is never presented as proof of consumption/rollback.
- Each local/runtime lane admits at most 50 pending messages (runtime text up to
  64 KiB). Overflow rejects the new item visibly rather than dropping older work.
  Tool approval choices, hidden-confirmation guards and Escape denial are not
  remapped or relaxed by queue commands/chords.
- Protocol testing exposed that the gateway's advertised 1 MiB frame limit was
  not set on its StreamReader, and oversized-line recovery could raise and close
  a live run. The reader now uses the real limit and discards only the rejected
  frame, retaining its successor. Active-run malformed/non-object/oversized
  frames report protocol feedback rather than falsely settling the model run.

Evidence:

- `agent/tests/test_identified_inputs.py`: identity-safe duplicate text, edit/
  remove/reorder, checkpoint mutation refusal, actual history-admission receipts,
  native steer-before-follow ordering, scoped drains and legacy/reset behavior.
- `coding/tests/test_queue_controls.py`: both applications' stable local IDs,
  edits/moves/removal, overflow/draft preservation, literal slash text, live Coding
  owner-loop/SDK delivery and edits, idle rejection, cancellation-only recovery,
  Claw accept/consume/return distinctions, orphaned/malformed receipt recovery,
  unknown/legacy/foreign run handling, and no permission bypass through shortcuts.
- `claw/tests/test_gateway_inputs.py`: real Unix socket + real SDK Agent delivery,
  edit/remove/reorder receipts, persisted user history, foreign connection
  rejection, duplicate/conflicting IDs, malformed/oversized text/direction,
  unconsumed abort return, no successor contamination, and oversized/non-object
  frames followed by a valid control while the owned run remains active.
- Private-tmux clients exercise queue inspection and local/runtime edits/removal,
  actual Coding native delivery and the production Claw socket UI, accepted vs
  consumed bodies, preserved drafts/hardware cursor across resize/repaint,
  subsequent local FIFO delivery, no duplicate visible consumed inputs and clean
  terminal restoration. The Claw terminal fixture is controlled; its real runtime
  integration is established separately by the Unix-socket/SDK tests above.

Verification snapshot — **2026-10-05**, pass 4:

- Full workspace: **2,415 passed, 7 skipped**, including **19** actual private-tmux
  client cases. The last combined Agent/TUI/Coding/Claw run passed **908** tests.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **335 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- Unrelated AI/`uv.lock` fingerprints unchanged. No workspace commit, paid model
  call, or live gateway restart; runtime/socket and terminal fixtures are isolated.

The feature is text-input control; attachment payloads still belong to the open
image/clipboard row. The general goal remains active across the other rows, with
Linux/private-tmux evidence only and Windows/desktop/IME checks unverified.

## Pass 5 — repository-wide read-only change review

Both clients wire `/diff [refresh|close]` and the configurable `review` action
(default Alt+D) into the shared backend/panel. This is not a filename-only
scaffold or reuse of one tool's edit preview.

- Read-only Git status discovers all changed paths at the repository root even
  when the session/group workspace is a subdirectory. Index/working statuses,
  renames/original names, deletions, conflicts and untracked names remain distinct.
  Names use NUL-delimited parsing, stable root/path identities and escaped display
  forms for unsafe filenames. Raw paths remain literal arguments, not pathspec
  magic or shell commands.
- Up/Down/Left/Right chooses files; `/` filters with typed/atomic-pasted text;
  s/w chooses the selected file's staged/working patch. Untracked files are not
  falsely labeled staged: text is a working preview, binaries/non-regular entries
  have summaries, and symlink targets are displayed without following them.
  d/b traverses initialized nested Git repositories/submodules and returns.
- Git output and all patch lines live in private disk storage, with SQLite row
  paging rather than a permanent small patch cutoff. n/p traverses all hunk
  positions; page/home/end navigation and horizontal panning reach long content.
  UI pages are clipped by display cells off-thread. Long physical lines retain
  continued chunks; UTF-8 decoding handles byte boundaries, joined graphemes are
  preserved, controls/bidi escapes are removed and tabs expand to four cells.
- One worker owns children/spools/SQLite. New requests coalesce and supersede
  stale Git work; interrupted partial documents cannot masquerade as cached data
  for another file. Scope changes roll back on errors/supersession. Refresh
  invalidates cached patches and explicitly updates the snapshot. Commands time
  out with UI errors, and close/exit cancels/reaps children and removes storage.
- Inspection has no apply/stage/revert/commit endpoints and never invokes a model.
  Git external diff, textconv, fsmonitor/untracked cache, hooks and pagers are
  disabled. Repository-redirection variables are cleared and optional locks
  disabled. Tests verify actual index/worktree bytes unchanged and hostile
  configured diff/textconv/fsmonitor scripts not executed. This is read-only
  application behavior, not an OS-enforced hostile-code sandbox.
- The focused panel owns Enter/typing/paste without editing or submitting the
  underlying composer. Its layout is a bounded transient native-buffer tail,
  not an alternate screen; resize/repaint preserves draft and native scrollback.
  Agent activity/timers continue independently. Permission panels take priority;
  denial/completion restores the open review instead of losing its focus.
- Coding uses the session cwd; Claw uses the group's gateway workspace metadata
  and fences/closes the view when that root changes. Legacy/missing workspace
  metadata produces feedback rather than guessing another directory. Review
  does not mutate session/model selection or replay gateway requests.

Evidence:

- `tui/tests/test_repository_review.py`: real staged/working/untracked status,
  repository-wide subdirectory root, unchanged index/worktree, renames/deletion,
  literal magic-looking paths and unsafe names, binary/symlink/FIFO handling,
  large patches/all 100 hunks, long Unicode/horizontal clipping/control removal,
  joined graphemes/tab expansion, disabled configured scripts, nested scopes,
  file filtering/paste/tiny layouts, live refresh, partial-cache supersession,
  timeout/cancellation/child reaping, private cache cleanup/self-exclusion,
  and no-repository feedback.
- `coding/tests/test_repository_review.py`: both actual app adapters preserve
  draft/active timer, never submit on panel Enter/paste, own transient rows/focus,
  switch details/filter correctly, restore Coding review after permission denial,
  and fence Claw workspace changes/no guessed root.
- Actual private-tmux Coding/Claw clients inspect real staged and unstaged diffs,
  filter files, navigate hunks, resize/repaint, restore hardware cursor/draft and
  leave index/worktree bytes unchanged. Coding additionally reaches a real
  fail-closed permission prompt while review is focused, denies it and restores
  review before returning to the draft. No paid calls or live gateway restart.

Verification snapshot — **2026-10-05**, pass 5:

- Full workspace: **2,441 passed, 7 skipped**, including **21** actual private-tmux
  client cases, **16** backend/panel review cases and **8** app ownership cases.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **337 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- Unrelated AI/`uv.lock` fingerprints unchanged. No workspace commit, paid model
  call or live gateway restart; Git commits in review tests are ephemeral local
  fixture setup only.

Review is an explicit snapshot, not an atomic live view of a concurrently edited
repository. Binary summaries are not binary editing/visual comparison; unsafe
controls are sanitized presentation, not byte-for-byte export. Kernel-blocked
filesystem calls can outlast a deadline; Windows/desktop terminal/IME validation
is still unverified. The other goal rows remain open.

## Pass 6 — searchable sessions and reversible branch/checkpoint navigation

The live clients now use a shared bounded `NavigationPanel` with immutable views,
not merely the startup picker or a printed list. Local read workers and remote
reply feeds coalesce requests, fence stale/closed results, paginate metadata and
apply search across all entries before paging. Changed queries/scopes no longer
leave older rows selectable while loading. Focused keyboard/atomic paste never
submits the underlying composer. Draft, cursor, timer, permission priority and
native scrollback ownership are retained.

- Alt+R / `/sessions` browses persisted sessions; `/tree` browses branch parentage
  and stable history checkpoints. Up/Down/pages choose entries, Right/Left enter
  and return, Enter activates, r refreshes and n creates a new session. Tree's
  configurable action is unbound by default to preserve existing Alt+T custom
  keymaps. Direct resume/new/fork/branch/goto commands share the runtime guards.
- Coding serializes mutations through its owned command worker and requires idle
  agent/session ownership. The prior head is saved before switching. Target
  workspace/default tools/FS context/model/thinking/settings are restored;
  prompt context and completion are rebound, active skills/SDK queues/session
  approvals cleared, and current permission mode remains unchanged. Same-session
  activation is a no-op rather than reloading stale disk data. Editor recall is
  rebuilt for navigation but historical `/clear` recall behavior is preserved.
- Branches store parent lineage, labels, model/settings and typed message prefixes.
  Navigating saves a return checkpoint before replacing the current head; older
  snapshot formats remain readable. Only complete tool-call/result boundaries are
  offered/accepted, including parallel batches; selected checkpoints do not invent
  or lose provider/tool context. Invalid target points are validated before
  switching to another session. Typed attachments in existing history remain
  serialized through the shared message conversion (new composer attachments are
  still in the open attachment row).
- Coding session files now use private atomic replacement; ID traversal and
  symbolic-link files are rejected. This also prevents metadata readers from
  observing a half-written session. The shared agent gains an idle-only tool-context
  rebinding API and stable checkpoint helper, not a second tool loop.
- Claw preserves the active-index compatibility format and adds a versioned
  durable catalog for archives and branch lineage. Reset no longer erases older
  metadata. Legacy orphan transcript IDs recover only in explicitly group-owned
  directories; shared stores never guess ownership. Explicit resume records a
  fresh activation timestamp, preserving original creation time without immediately
  daily-resetting away the resumed context. Corrupt/missing/foreign IDs are refused.
- Version-1 gateway list/tree replies are read-only, bounded and searchable.
  Mutations require a ready idle group, expected active-session identity and the
  group execution lock. Metadata work runs off-loop; cancellation retains ownership
  until uninterruptible durable work settles, rather than releasing into a race.
  Incoming channel messages without a ready checkpoint owner are collected instead
  of dropped into a disposed session. Namespace/session IDs cannot address another
  group's storage. Existing group workspace/model policy is not silently replaced.
- Correlated session results/changed metadata/history chunks reconstruct Claw's
  context and recall while preserving the draft; input remains blocked until sync
  completes. Stale browser providers/connection replies cannot activate a successor.
  Active-session leases reject ordinary sends against a changed conversation;
  refresh/resume makes that change explicit. Legacy gateway behavior remains
  supported without sending unknown controls. Uncertain mutations are not replayed.
- Cancellation testing also exposed that Coding's real session cancel method
  discarded identified inputs before the UI owner could return them (earlier mock
  cancellation bypassed it). Interactive cancel now explicitly preserves those
  inputs until owner settlement; default SDK cancel still clears legacy queues,
  and the worker clears leftovers after recovering undelivered records.

Evidence:

- Shared navigation tests cover global search/paging, worker coalescing, stale
  replies, tree/back/search/paste, tiny geometry and close.
- Coding session-navigation tests cover saved workspace/model/thinking/settings,
  unchanged permission mode, retained old head, same-session safety, safe tool
  boundaries, reversible branch heads and actual browser activation/replay/draft.
- Claw catalog tests cover archive reload/ownership, conservative legacy migration,
  explicit resume versus daily reset, reversible checkpoint heads and stale guards.
- Real Unix-socket gateway tests exercise new/list/search/tree/resume/goto/history
  sync with actual durable stores and zero model calls, busy/stale/foreign IDs.
  TUI adapter tests exercise read-only busy browsing, provider/connection fencing,
  correlated history synchronization, legacy refusal and no uncertain replay.
- Private-tmux Coding/Claw clients browse/search sessions, enter trees, restore a
  branch and its saved return head, preserve draft/cursor across resize/repaint and
  restore visible context without model calls. Coding uses real offline SDK/session
  persistence; Claw's terminal transport is controlled, with actual gateway/store
  ownership established separately by the Unix-socket integration tests.

Verification snapshot — **2026-10-05**, pass 6:

- Full workspace: **2,459 passed, 7 skipped**, including **23** actual private-tmux
  client cases. The combined Agent/TUI/Coding/Claw non-terminal run passed **929**
  tests; focused navigation/ownership groups were also rechecked after final changes.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **340 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- Unrelated AI/`uv.lock` fingerprints unchanged. No workspace commit, paid model
  call or live gateway restart; session/store and terminal fixtures are isolated.

Limits remain explicit: snapshots/metadata can evolve during browsing, activation
requires displayed/ready results and idle ownership; kernel-blocked filesystem work
cannot be forcibly interrupted. Windows/desktop terminal/IME remains unverified.
The full agent UI/capability goal is still open across the remaining rows.

## Pass 7 — typed image drafts, clipboard and queue/transport recovery

Both clients now send real image-content parts, not path placeholders. Shared
immutable attachments, one owned read worker, a bounded preview panel and an
image-aware input summary preserve the native-scrollback workflow. `/attach`,
`/attachments [preview ID|remove ID|clear]`, `/paste` and configurable Ctrl+V
are live-wired. Enter supports image-only drafts. Linux Wayland/X11 helpers
support explicit image/text reads; macOS `pbpaste` is text-only. No implicit
clipboard polling occurs. Reads after submission stay editable, never submit;
explicit clear fences pending reads. Regular-file/nonblocking reads avoid FIFOs;
clipboard children time out, terminate/escalate and are reaped on close.

- Admission requires advertised image capability and valid limits: 4 images per
  prompt, 10 MiB each, 40 MiB retained across composer/queues, stricter model
  limits respected. PNG/JPEG/WebP magic/header/dimension validation caps images
  at 100 megapixels. Unknown/text-only capability or malformed Claw limits fails
  visibly without detaching the draft. Header checks are not a full decoder or
  hostile-input sandbox. Recovery never discards bytes just to meet admission
  caps; excess must be removed before retrying.
- Coding converts bytes outside the terminal thread and validates again at the
  owned turn/identified-input boundary. CLI initial images also become typed
  print-mode input; image-only CLI files work and capability errors return failure
  without calling a provider. Typed history persists image bodies with existing
  serialization. Local and identified runtime queues retain image parts across
  text edits/reorders, release removed/consumed inputs, and show image counts.
- Coding worker failures now recover the CURRENT dequeued turn's tuple, not the
  first prompt's bytes. Stamped queued-start receipts update UI ownership; failed
  turns warn that input may already be in context. Cancellation/rejection restores
  unconsumed/local bytes; neither recovery nor late clipboard reads grants approval
  or resubmits. Preview focus yields to permissions and returns after denial.
- Claw transports immutable client-owned bytes through bounded checksum-verified
  chunks, avoiding the socket's single-frame limit. Connection-owned caches never
  write workspace files and reject foreign/incomplete/duplicate refs. Recognized
  refs are released on terminal rejections, including malformed lists, stale leases,
  unknown groups and invalid controls; invalid ref shapes cannot crash cleanup.
  Model validation runs off-loop, then rechecks active run ownership. Ordinary
  sends recheck the session lease AFTER validation while reserving the group, so
  another client's navigation cannot redirect a previously validated input.
- Claw queues, unavailable explicit owners, capability rejection of a dequeued
  input, orphan/disconnect settlement and abort receipts retain bytes for explicit
  retry. Transport generations/handshake still forbid replay on reconnect. Typed
  JSONL keeps image bodies; visible history carries counts without base64. Session
  changes refresh image capability/limits rather than reusing old metadata.
- The full-workspace run exposed a separate short-lived bash race: querying PGID
  after asyncio had reaped a fast shell returned ESRCH and lost successful output.
  Since subprocesses start a new session, the known shell PID is the group ID.
  A deterministic real-process test now simulates failed PGID lookup while checking
  repeated output and cwd tracking; timeout/cancel tests remain in the suite.

Evidence includes real-file/FIFO/header/limits, controlled Wayland image/text
helpers and child cleanup, clear versus late-send fencing, tiny image-only editor,
actual Coding SDK/session image persistence, CLI print-mode rejection, local image
queue identity, later-turn failure recovery and permission-focused late clipboard.
Real Unix-socket tests cover exact typed bytes/JSONL, foreign/incomplete/malformed
refs, repeated rejection beyond cache capacity, >1 MiB chunked images, session
changes during validation, and image-bearing identified edit/consume/abort-return.
Private-tmux cases attach a real file and use a controlled clipboard helper in
both clients, preview/close/resize/repaint, send two images through offline vision
fixtures, preserve the new draft and restore terminal modes without visible base64.

Verification snapshot — **2026-10-05**, pass 7:

- Full workspace: **2,506 passed, 7 skipped**, including **25** private-tmux client
  cases. Final focused image/permission, real gateway image-control and shell
  regression groups also passed independently.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **343 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- Existing unrelated work was not reset/stashed/committed. This pass intentionally
  extends shared AI image-reference validation. The prior temporary fingerprint
  manifest is unavailable in this environment; no fresh hash comparison is claimed.

Limits: actual desktop clipboards, Kitty/iTerm image display, macOS image paste,
Windows/IME and cross-platform manual validation remain unverified (macOS image
clipboard is not implemented). Image-containing session/transcript files are
sensitive. No paid model calls, live gateway restart or workspace commit are part
of this pass. The broader goal remains active: transcript interaction/fullscreen,
copy/export/share, MCP, extensions, planning and child supervision are still open.

## Pass 8 — full-source transcript browsing and optional fullscreen

Both live clients now open the transcript browser with Alt+H or
`/transcript [QUERY]`. This is a distinct application surface, not the limited
reasoning/tool details panel or an unused alternate-screen flag. Native main-buffer
scrollback remains the default. `/fullscreen` or f in the browser explicitly
opts into the full-height alternate viewport; the keymap exposes `transcript` and
an unbound-by-default `fullscreen` action. Completion/help/README controls are wired.

- Shared retained messages expose immutable, stable-ID source records before
  Markdown transformations. Coding's user/assistant prose, full reasoning, tool
  arguments and complete retained tool output, plus notices, are searchable without
  collapsed preview cutoffs. Claw searches all text retained by its UI, but the
  existing gateway projection can already have truncated it (see remaining gap
  below); this pass does NOT claim recovered source beyond those wire caps. Existing tool-argument presentation/redaction policy is retained. Binary
  image bodies are not shown as base64. Current visible retained context includes
  session/gateway history replay; foreign sessions/hidden runtime instructions are
  not silently exposed. Archives are activated through the existing session browser.
- One coalescing worker indexes full source, applies literal Unicode-casefold search
  to EVERY non-overlapping occurrence, and returns only a horizontally clipped,
  height-bounded viewport. Repeated hits on a long line are individually navigable;
  per-line cumulative counts avoid allocating a record for each occurrence. A
  chosen hit pans into view, including casefold expansions such as ß. Old/closed
  work cannot publish over a newer request; owned index/source caches are released
  at worker shutdown. Search/reflow does not run in input/render callbacks.
- The panel owns atomic paste and query editing, n/Shift+N wrap-around matches,
  vertical/page/Home/latest scrolling, adjacent source-entry navigation and
  horizontal pan. v/Shift+Up/Down marks whole-line ranges; a selects a complete
  source entry across pages. Selection is visual/inspectable and has source-text
  extraction for the next explicit copy/export pass, not a silent clipboard write.
  Entry selection can be requested while a search receipt is pending. Query editing
  uses grapheme deletion and a bounded visible cursor field; Enter never submits.
- Browser content is an immutable snapshot so active streaming cannot silently
  replace selected text. r refreshes, preserves the stable source-entry/line where
  possible and clears selection. End refreshes and goes to latest retained source.
  Session replay/replacement/clear closes old browser ownership. Draft text/cursor,
  queued attachments, active run/worker and elapsed timer remain application-owned.
  Closing with Escape/Ctrl+C never implies agent cancellation or mutation.
- Fullscreen is a finished, height-bounded transcript view. The terminal engine
  applies transitions at loop boundaries, retains the original main-buffer painter
  and uses a separate alternate painter. Return queries the restored cursor after
  hidden main-buffer reflow/resize, then repaints owned visible rows, never native
  historical scrollback. Coalesced input cannot submit through a screen transition.
  Clear-screen controls occur ONLY inside the alternate screen. Main-buffer mode
  retains its previous no-clear/no-alternate-screen regression tests.
- Coding approvals explicitly leave fullscreen, take focus and suppress browser
  shortcuts/paste; denial/resolution restores the inline browser, not automatic
  fullscreen. Suspend retains temporary mode for re-entry; EOF/stop/failure restore
  terminal modes and temporary screen ownership. External-editor/dialog gates are
  unchanged. Claw's viewer is local-only and sends no gateway/provider controls;
  session switches cannot retain old source selection.

Evidence:

- Shared browser tests search beyond 25,000 source lines/2,000 display columns,
  navigate repeated Unicode-casefold hits, pan original graphemes, fence coalesced
  requests/close, preserve source position across preceding growth, select/extract
  full local entries, sanitize controls and keep tiny/fullscreen row budgets bounded.
  End refreshes appended source, and query editing preserves grapheme boundaries.
- Both actual client adapters search hidden full tool output/reasoning, retain
  draft/cursor, refuse Enter submission, select a complete entry and restore focus.
  Approval tests exercise safe priority and denial. Existing partial-constructor
  tests now explicitly initialize the added optional panel field.
- Terminal-engine tests preserve main-painter identity/content across alternate
  view/resize, request restore CPR, and inject stop/failure with actual PTYs to
  prove raw-mode, screen and protocol cleanup.
- Private-tmux Coding/Claw clients search a real 100-line offline response, navigate
  matches, select all source lines, enter fullscreen, resize/repaint, return to
  native mode, retain shell/response scrollback and draft hardware cursor, and
  restore termios. A separate actual Coding case receives a tool approval while
  fullscreen, returns to native mode, denies safely and recovers browser/draft.
  Raw output is audited so every clear-screen control is between balanced
  alternate-screen enter/leave controls; no scrollback erase is permitted.

Historical full-source transport finding from pass 8 — superseded by pass 9 below:
`display_events.display_result` caps gateway tool/display text at 16 KiB,
`display_arguments` caps strings at 1,024 characters/arrays at 50 entries, and
`GroupRuntime._display_history_entry` applies those helpers plus a 16-part limit.
The new local browser cannot recover bytes omitted before receipt. Closing this
row requires correlated, redacted but untruncated gateway source retrieval in
bounded chunks, session/connection fencing, and real Unix-socket tests locating
markers beyond those caps in live tools and restored history. The existing caps
must not simply be removed into oversized frames. This is the next critical-path
work, before claiming full Claw transcript parity or proceeding to copying it.

Verification snapshot — **2026-10-05 UTC**, pass 8:

- Full workspace: **2,521 passed, 7 skipped**, including **28** actual private-tmux
  client cases. Focused retained-source/selection/permission/lifecycle tests also
  passed independently after final changes.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **345 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- No paid model calls, live gateway restart or workspace commit. Existing unrelated
  work was not reset/stashed. The full-source Claw gateway limitation above remains
  OPEN despite these green UI/renderer tests; they do not prove that omitted remote
  bytes were recovered.

Limits: application selection is whole-line/range/entry based; ordinary native
terminal mouse/text selection is unchanged. The viewer searches retained visible
source, not other sessions or binary data. Snapshots refresh explicitly rather
than replacing selected content during streaming. Desktop/Kitty/iTerm, Windows
and IME manual validation remain open. No paid model calls, live service restart,
workspace commit or unrelated reset/stash. Copy/export/share, MCP, runtime
extensions, planning, child supervision and remaining platform gaps keep the
expanded goal active.

## Pass 9 — Claw full-source transport, live capture and cap closure

This closes the specific remote truncation gap discovered in pass 8; it does not
claim the remaining copy/export/MCP/extension/planning/child-agent goal rows are done.
Normal native display projections remain bounded. Explicit transcript inspection
uses a separate version-1 read-only source path with no text, array or part-count
cutoffs inherited from those projections.

- Source is projected from the current persisted transcript or a captured immutable
  SDK message tuple when the active session owns the head. Partial assistant
  content and ID-scoped full live tool arguments/progress are included; complete
  canonical results take precedence, with distinct last-progress text retained
  when it differs. Reused provider tool IDs are scoped to their actual message/
  content-part identity. This reuses the one SDK runtime, not another tool loop.
- Existing redaction helpers now accept an explicit unbounded mode; their default
  bounded behavior is unchanged. All supported content parts, long argument strings
  and array entries remain inspectable after redaction. Channel-hidden/internal
  user inputs, redacted reasoning and binary image bodies are not exposed; images
  have textual counts. Error-path persistence now retains captured channel tags
  instead of potentially turning a hidden system input into untagged user text.
  Heuristic redaction is not a universal sensitive-content guarantee.
- One connection-owned sender coalesces requests. A worker prepares a private
  anonymous spool; 64 KiB raw chunks become small base64 JSON frames with declared
  byte count, ordered indexes and SHA-256. No oversized history frame is substituted
  for the old caps. Writes have backpressure/timeouts, and preparation/serialization
  and disk reads run off the gateway event loop. Cancel/close settles the one spool
  owner before a successor can prepare another. Unknown/malformed requests fail
  visibly; a non-string message type cannot abort an owned run via a type exception.
- Active session identity AND a context revision fence preparation, every frame and
  final completion. Read-only retrieval is allowed during model/tool work, but
  navigation in progress and same-session checkpoint replacement cannot commit an
  old source into the successor view. Group lookup cannot address a foreign session
  or filesystem path. Checks are repeated under the connection write lock so a
  frame waiting behind another response cannot bypass a changed head.
- The production client transport assembles chunks into a private spool off-loop,
  verifies size/order/revision/checksum, validates records, and publishes only a
  complete immutable snapshot. No chunk/base64 body reaches terminal rendering.
  Close, failure, newer nonce, generation change and session replacement fence
  old replies; checksum/parse/disk failures never install partial source.
- The browser keeps query/draft/cursor/timer ownership while loading. Query and End
  intent survive provisional old-dataset/geometry receipts. Selection is disabled
  during loading and reset on replacement; source status/progress/errors are visible.
  Explicit r/End refresh is read-only, not model execution. Client notices form a
  prelude; latest targets canonical source rather than a stale bootstrap notice.
  Legacy gateways get no unknown controls and show a limited-source upgrade label.
- Disconnect cancels owned SDK work BEFORE potentially uninterruptible source
  cleanup. Viewer cancellation cancels only source, never the agent. Source reads
  are not replayed on reconnect; idle timeouts require explicit retry. A reset ack
  now refreshes the active lease and closes the old browser. Source caches/progress
  follow session/compaction lifecycle; temporary files do not enter the workspace.
- Million-character ASCII output exposed unnecessary all-grapheme allocations for
  a small horizontal viewport. Sanitized ASCII now uses display-cell-equivalent
  direct slicing/offsets; Unicode keeps the existing grapheme-safe path. Native
  scrollback and optional fullscreen renderer invariants remain unchanged.

Evidence:

- Real Unix-socket tests inspect escaped/wide-UTF8 source records larger than 1 MiB,
  markers beyond 16 KiB results and 1,024-character arguments, array item 59 and
  content part 19, full reasoning, credential redaction and image/body omission.
  No model calls occur for persisted-source reads.
- An actual SDK tool emits >50 KiB live progress and >200 KiB final output; reads
  during active work preserve full arguments/progress and after completion return
  full canonical results without invoking an extra provider call.
- Real gateway tests reject foreign/unknown sessions/groups, and replace a checkpoint
  while source preparation is held: the same session ID with a changed head is
  correctly refused. Coalescing held reads still allows ping/control processing.
- The real client `_run_connection` performs its own handshake, receives/assembles
  full source through the production adapter and preserves the query/draft. Receiver
  tests cover corrupt checksum, revision drift, missing/duplicate/out-of-order chunks,
  stale/unsolicited replies and file cleanup; no partial ready snapshot is published.
- Private-tmux Claw first receives the bounded legacy projection, then searches a
  marker beyond 1.2 million characters via full source, selects all 3,005 tool-source
  lines, enters fullscreen, resizes/repaints and returns with draft/termios/native
  ownership intact. It sends no user/model message. Visible output contains neither
  the seeded credential nor source base64. Separate Unix-socket tests establish
  actual runtime projection and group/head ownership, not just the controlled peer.

Verification snapshot — **2026-10-05 UTC**, pass 9:

- Full workspace: **2,536 passed, 7 skipped**, including **29** actual private-tmux
  client cases. Full-source Unix-socket/SDK/client/receiver tests and pending-source
  query/latest tests also passed independently.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **348 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- The completed source path was verified beyond the old caps, not merely against
  short local snapshots. No paid model call, live gateway restart or workspace
  commit. Existing unrelated changes were not reset or stashed.

Limits: source is the accepted current head plus captured live output, not arbitrary
foreign archives or the cached system prompt; activate an archive first. Snapshot
content refreshes explicitly. Disk exhaustion/parse errors are visible, not silent
truncation. Kernel-blocked filesystem work cannot be forcibly interrupted. Native
source selection remains whole-line/range/entry based; platform/manual limitations
from pass 8 still apply. No OS sandbox, paid model call, live daemon restart, workspace
commit or unrelated reset/stash. The broader goal remains active.

## Pass 10 — captured copy/export and reviewed local sharing

Both clients now wire `/copy`, `/export`, `/share`, configurable Alt+C and browser
y/e/s into explicit user output actions. Library helpers alone are not the evidence:
the live controllers preserve draft/selection/focus, and terminal cases exercise
helper input, actual files and a resize-safe confirmation round trip.

- Output captures immutable source, not a rendered/collapsed viewport. Response,
  selected source lines, complete current entry, conversation and all-source scopes
  are supported. Copy defaults to latest assistant text; browser y uses marked
  selection/current entry. Source extraction/preparation runs off the UI thread.
- Claw fetches complete verified source for prompt-level actions, refusing legacy
  clipped fallbacks. Pending output is cleared on source failure, timeout, stale
  session/generation, close and disconnect; old cached source is never substituted
  after a failed refresh. Selection/entry requires a settled full-source browser.
  These requests neither invoke a provider nor execute/steer/cancel agent tools.
- Explicit clipboard writes use Wayland `wl-copy`, X11 `xclip`, or macOS `pbcopy`.
  A missing helper produces a reported private persistent text fallback. Failures/
  cancellation/timeouts are visible and not automatically retried; helper groups
  are terminated/escalated/reaped on failure. Successful platform clipboard owners
  may persist as required by the desktop. No implicit OSC52 or clipboard polling.
- Text/Markdown/JSON exports create private files via same-directory temporary
  writes, fsync and exclusive atomic publication. Existing files/symlinks/directories
  cannot be overwritten. Pre-commit failure/cancel removes the temporary and leaves
  no partial target. Paths are explicit user destinations, not an OS sandbox.
- Sharing is a portable local HTML artifact, never a silent hosted upload. Default
  conversation scope omits tools, reasoning and notices; explicit wider scopes are
  disclosed. Common credential patterns are redacted, source is HTML-escaped and
  scripts/remote resources/forms/base URIs are blocked by CSP. Review remains
  necessary: arbitrary secrets/PII are not reliably detected by heuristics.
- A bounded output panel shows scope, roles, bytes, digest, preview and destination;
  the worker writes exactly that immutable prepared artifact. Path editing and review
  are distinct phases. Plain Enter requires complete rendered metadata, a settled
  preview and visible confirmation; modified Enter/paste/loading/tiny or newly
  resized geometry cannot commit. A terminal test caught a misleading ready action
  label while preview indexing was pending; it now explicitly disables confirmation.
- Tool approvals preempt review and preserve denial semantics. Output chords cannot
  approve or submit. Share review leaves temporary fullscreen and restores the
  source/draft afterward. Context changes invalidate uncommitted prepared review;
  exit closes owned work. Already committed file/clipboard effects are not rolled
  back, redirected to new context or replayed. One output job is admitted at a time.

Evidence includes exact >30 KiB response/selection/JSON source, default omitted
private roles, explicit wider-role disclosure, escaped script content/CSP/redaction,
real UTF-8 stdin to a controlled clipboard helper, missing-helper private fallback,
helper cancellation/reaping, atomic modes/no overwrite/symlink refusal, fsync failure
cleanup, no share write before confirmation, and context-change cancellation.
Both actual adapters export >50 KiB source, preserve draft/cursor and require review;
Claw rejects output after failed refresh, and Coding approval denial safely restores
review. Private-tmux clients copy via a real helper, export JSON, review/create local
HTML across tiny/large resize, retain draft hardware cursor and restore termios with
no native buffer erase/alternate-screen entry or model request for output.

Verification snapshot — **2026-10-05 UTC**, pass 10:

- Full workspace: **2,551 passed, 7 skipped**, including **31** actual private-tmux
  client cases. Output scope/artifact/clipboard/cancel/atomic-write and live adapter
  approval/source-failure groups also passed independently.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **351 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- No paid model call, automatic upload, live daemon restart or workspace commit;
  unrelated work was not reset or stashed.

Limits: clipboard privacy/retention is platform-owned; fallback files persist until
removed. Source is the captured visible/verified snapshot, not binary attachments,
foreign sessions or hidden system prompts. Share redaction is heuristic; distribution
is manual/explicit. Windows clipboard and real desktop/macOS/IME validation remain
open. Kernel-blocked filesystem work cannot be forcibly interrupted. No paid model
call, live service restart, workspace commit or unrelated reset/stash. The expanded
goal is still active for MCP, runtime extensions, planning, child supervision and
remaining platform gaps.

## Pass 11 — MCP stdio foundation and both-client runtime wiring

This is a transport/runtime increment, NOT closure of the full MCP row. Current
primary specs were checked before implementation; the applicable release now
includes 2026-07-28 stateless `server/discover` and per-request metadata, not only
the older initialize/session flow. Sources consulted: MCP versioning, lifecycle,
stdio transport and tools/schema specifications on modelcontextprotocol.io.

- A reusable Agent-layer manager owns one background asyncio loop. It preserves
  connections across Coding's per-turn loops, bridges existing AgentTool calls,
  and creates no second model/tool loop. Configuration is explicit argv/cwd/env,
  never a shell string; construction starts neither process nor model request.
- Current discovery uses request metadata and supported-version checks. Only an
  unrecognized discovery method falls back to supported legacy initialize/
  initialized; recognized negotiation rejection does not downgrade. Current
  transports reject unsolicited server requests/notifications and do not send
  forbidden cancel notifications. Legacy tool-list-change status is recorded but
  never changes schemas silently during a turn. Unsupported client capabilities
  are refused, not advertised or automatically executed.
- Correlated bounded JSONL requests, separate bounded stderr draining, timeout/
  cancellation, late-response fences and EOF/malformed-response handling preserve
  process/request ownership. Connections/calls are never automatically replayed or
  reconnected. Disconnect/shutdown closes stdin, terminates/escalates/reaps the
  owned process/group and settles reader tasks. Cancellation remains best effort;
  a remote effect may already have happened.
- Paginated discovery validates unique names/cursors and stable namespaced aliases,
  object input/output schemas and structured results. Draft 2020-12 validation uses
  the declared jsonschema dependency; external schema references are rejected, never
  fetched. Server errors/unsupported data are not silently accepted or dropped.
  Resource data is inert, and linked resources are not fetched. Limits reject
  excessive frames/pages/tools/content explicitly rather than clipping them.
- Coding layers global/project server definitions. `/mcp list|connect|disconnect|
  refresh` is wired through its owned command lifecycle; schema/process mutations
  require idle state. Workspace switch drops old proxies/connections, reloads
  configuration and retains the existing permission mode. All external calls still
  pass its fail-closed approval hook; server readOnlyHint cannot bypass it.
- Claw creates a group-owned manager and correlated version-1 gateway controls,
  reserving the idle group while connect/discover/disconnect settles. Cached SDK
  sessions receive updated proxies; future sessions reuse them. Tool use defaults
  blocked unless trusted configuration explicitly selects allow_tools. User command
  trust and unattended tool-use authorization are distinct. Management outcomes are
  not replayed after reconnect; old generation/request replies are fenced.
- No automatic server startup, sampling, elicitation, roots exposure, hidden prompt
  sharing or OS-enforced sandboxing was added. Status excludes configured env values.
  Portable Flow's vendored-agent dependency list now declares jsonschema as well;
  LICENSE-EXCEPTION.md was reviewed and no Runtime Kernel/licensing boundary changed.

Evidence:

- Independent real stdio peers exercise current/legacy negotiation, initialized
  notification, per-request metadata, multi-page tools, true Draft-2020-12 argument
  validation, structured output, server errors, unsupported negotiation/refusal,
  external-ref rejection without fetch, timeout/cancel/late responses, malformed
  stdout/EOF and process/loop cleanup. Multiple separate asyncio.run calls reuse the
  same owned connection, matching the real Coding execution architecture.
- A real Coding AgentSession/SDK loop registers and executes an MCP proxy only with
  explicit allow-all; default unattended ask blocks a peer claiming read-only before
  tools/call is emitted. Workspace switch removes external proxies/project config.
- Real Unix-socket Claw management connects/discovers/registers tools, enforces its
  explicit allow_tools policy, permits read-only status while reserved, and refuses
  schema/process mutation of a busy group. Disconnect removes cached SDK proxies.
- A private-tmux Coding client loads configuration without launching, explicitly
  connects, requests an external tool, shows the real approval panel, emits no
  tools/call until confirmation, receives the actual remote result, disconnects
  and retains draft/cursor/termios without native erase or model network access.

Verification snapshot — **2026-10-05 UTC**, pass 11:

- Full workspace: **2,565 passed, 7 skipped**, including **32** private-tmux client
  cases. Controlled stdio/current/legacy/cancel/failure and both-host runtime/policy
  groups also passed independently.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **355 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. Portable-bundle declared-dependency guard passes
  with the new validator included. `git diff --check`: clean.
- No paid model calls, live gateway restart or workspace commit; unrelated changes
  were not reset/stashed. This pass adds jsonschema and its development typing
  stubs, updating workspace manifests/lock without dropping existing changes.

Remaining: Streamable HTTP/current stateless HTTP, authorization/OAuth, richer
interactive MCP management, prompt/resource browsing and additional capability
negotiation/interactive-input handling. These remain on the ORIGINAL MCP row, not
waived because stdio passes. Actual third-party servers/desktop/Windows remain
unverified. Configured processes/tools are trusted external code, not a sandbox;
remote cancellation cannot roll back effects. No paid model calls, live gateway
restart, commit or unrelated reset/stash. The broad goal stays active for remaining
MCP, extensions, planning, child supervision and platform work.

## Pass 12 — current/legacy MCP HTTP, bounded streaming and header/auth gates

Primary current/legacy lifecycle/transport/discovery specifications were re-read.
This caught a REAL compatibility flaw in pass 11: modern discovery returns
supportedVersions plus metadata identity, not a legacy protocolVersion field.
The implementation AND independent stdio peer fixture were corrected; passing a
self-invented response shape was not accepted as protocol evidence.

- HTTP configurations connect only on explicit host commands. The same manager,
  existing AgentTool proxies, idle host/group mutation guards and permission hooks
  are reused. Current stateless requests carry matching protocol/client metadata,
  method/name HTTP mirrors and validated annotated argument headers. No new model
  loop, auto-connection or read-only-annotation authorization is introduced.
- Supported versions/metadata are validated. Modern auth/version/unknown-method
  errors are distinguished from legacy transport rejection; recognized modern
  failures do not silently downgrade or trigger another potentially mutating POST.
  Legacy Streamable HTTP initialize/initialized, session/protocol headers, optional
  GET notifications, cancellation and owned DELETE are supported. Expired sessions
  fail without replaying tools/call or autonomously reconnecting.
- JSON bodies and incremental byte-based SSE parsing have explicit limits, correlate
  identities and reject invalid/incomplete/oversized/unsolicited current messages.
  UTF-8 split across byte chunks, multiline data and CR/LF separators work. Remote
  cancellation closes the owned HTTP stream; modern mode sends no forbidden cancel
  notification. Heartbeats do not extend the absolute per-request timeout.
- Header annotations must be valid primitive properties-only paths with unique
  case-insensitive tokens. Unsafe/ambiguous strings use the specified base64 sentinel;
  body arguments are retained. Invalid header tools are rejected with status, not
  silently exposed. Remote schema refs still are not fetched. HTTP transport is a
  direct Agent dependency and remains covered by portable Flow dependency checks.
- Endpoint/userinfo/fragment/reserved-header validation, TLS-by-default and explicit
  non-loopback plaintext opt-in protect destination intent. Static and environment
  credential headers are supported, but credential values/body/URL detail is not
  printed in status/errors. Redirects, ambient proxy inheritance, arbitrary resource
  fetches, automatic token refresh and OAuth/browser launches are disabled. Auth
  and redirect failure require explicit user action; no automatic retries/replay.
- HTTP active requests/background notification streams are connection-owned and
  canceled/closed at disconnect/shutdown. DELETE uses a bounded timeout/stream rather
  than eagerly reading an unbounded server response. Late or mismatched replies do
  not settle successor calls. Management/status reports transport/rejected tools/
  notification-stream error without revealing configured secrets.

Evidence:

- Independent real local HTTP peers exercise current JSON, chunked UTF-8 SSE and
  legacy negotiation/session lifecycle, nested primitive header mirrors/base64,
  invalid-tool rejection, credentials, auth/version/redirect refusal without extra
  POSTs, expiry, timeout, cancellation and identity mismatch. Parser tests reject
  incomplete/oversized events and handle multiline data. StdIO current/legacy peers
  were rechecked after the discovery-shape correction.
- The actual Claw group manager registers an HTTP proxy under unchanged default
  blocked policy and emits no remote call during discovery. Coding's private-tmux
  client starts no connection until /mcp connect, then shows the real approval panel
  before HTTP tools/call, receives a verified remote result, checks header/body
  mirroring, disconnects and restores terminal modes. Model HTTP remains prohibited
  by the fixture; only MCP's explicitly configured local HTTP factory is enabled.

Verification snapshot — **2026-10-05 UTC**, pass 12:

- Full workspace: **2,582 passed, 7 skipped**, including **33** private-tmux client
  cases. Current/legacy HTTP, SSE/header/auth/cancel/identity and corrected stdio
  groups plus actual Claw HTTP policy registration passed independently.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **357 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- No paid model calls, live gateway restart or workspace commit; unrelated work
  was not reset/stashed. HTTPX is now explicitly declared by Agent, already present
  in the workspace and portable-bundle dependency set.

Remaining: interactive authorization/OAuth, SSE resumption where applicable,
richer MCP schemas/completion/subscriptions, interactive authorization and further negotiated client
capabilities. Those stay on the original MCP row, not waived by these transport
passes. Real third-party endpoints, desktop/macOS/Windows remain unverified. Read
limits fail explicitly, not silently truncate; remote effects cannot be rolled back.
No paid model calls, live service restart, commit or unrelated reset/stash. Broad
goal remains active for MCP, extensions, planning, child supervision and platforms.

## Pass 13 — MCP catalogs, inert context previews and focused management

- Both actual clients now open a shared focused `/mcp` browser. Connections remain
  explicit; c/d/r reuse existing idle-gated host management. u/t/p list validated,
  paginated resources/templates/prompts; filtering/navigation operate locally.
  Enter retrieves previews only. Template URIs are manually resolved in an editable
  field, not expanded/fetched implicitly. Prompt arguments are named-string JSON,
  required/unknown names are checked, and repeated busy submissions are ignored.
- Frozen resource/prompt documents preserve text, quoted roles, binary base64/MIME
  and URI metadata. Prompt system roles/unknown content, invalid blobs, cyclic or
  excessive pagination and unsupported deferred input fail explicitly. Resource-
  and prompt-only peers connect/refresh without requesting nonexistent tools.
  Resource reads/prompt gets reuse the owned stdio/HTTP manager; linked URIs are
  never autonomously fetched. No new provider/tool loop was introduced.
- Preview text is inert until explicit i insertion into the existing editable
  draft with an untrusted-data label. It is NOT submitted. Binary insertion refuses
  rather than dropping data; e captures reviewed local JSON export with raw base64.
  Confirmation remains necessary; export roots at the selected workspace. Output
  cancellation restores the MCP browser and tool approval keeps focus priority.
  Replacing the one details slot closes/cancels the old MCP browser.
- Coding preview workers cancel on close/replacement, settle their owned async
  request and fence late results. Connection/schema mutation stays on the existing
  owned slash-command lifecycle. Claw sends checksummed 64 KiB IPC chunks instead
  of oversized frames, with request/group/session/revision fences and client
  spooling/decoding off-loop. Its connection-owned worker coalesces successors,
  cancels slow RPCs, and settles temporary-file creation/reads before closing the
  file or starting another operation. Slow MCP reads do not block ping/cancel.
- Actual private-tmux verification caught and fixed a resource-row rendering bug:
  catalog rows displayed the server instead of the resource URI. Large draft
  assertions now distinguish the viewport from native scrollback rather than
  incorrectly expecting the start of a 200 KiB draft in its tail viewport.

Evidence covers real stdio catalog pagination, >200 KiB text and binary/prompt data,
resource-only capability behavior; actual Coding session and Claw gateway/client
browser connect/read/preview/manual insertion with zero provider/tools/call; bounded
panel rendering; template/argument gestures; approval priority/output cancellation,
binary snapshot preservation/workspace rooting; late/closed/context-stale previews;
actual gateway ping/cancel during blocked reads; coalescing and cancellation during
file creation with all owned files closed. Private-tmux covers live Coding browser,
large preview/draft insertion, tiny resize, disconnect and terminal restoration.

Verification snapshot — **2026-10-05 UTC**, pass 13:

- Full workspace after UI/focus/cancellation fixes: **2,600 passed, 7 skipped**,
  including **34** private-tmux terminal cases. The final non-tool fixture split
  (resource-only and prompt-only peers separately) was then rechecked: **6** Agent
  context tests passed, with no production changes after the full run.
- Actual browser/gateway/ownership groups: **17 passed** independently. Focus and
  existing interactive regressions: **50 passed** after the final hint fix.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **361 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- Footer hints now follow actual focus: approval first, then output review, MCP
  browser and transcript. A regression caught a hint-state mismatch outside an
  active turn; presence of the focused approval now takes priority over run phase.

This pass does not complete the original MCP row: OAuth, applicable SSE resumption, completion/subscriptions,
richer server/tool schemas and further interactive negotiation remain open. Binary
resource insertion is deliberately unsupported (raw export works). Third-party
server and desktop/macOS/Windows validation remain open. No paid model calls,
live service restart, commit, unrelated reset/stash, or OS sandbox was introduced.
The full goal remains active for those gaps plus extensions, planning, child
supervision and remaining platform/clipboard work.

## Pass 14 — legacy SSE response recovery without tool-call replay

Primary 2025-11-25 transport, current 2026-07-28 HTTP and WHATWG EventSource
specifications were re-read. Legacy resumption uses GET with Last-Event-ID, whereas
current stateless HTTP forbids GET. Current request-scoped progress/log messages
are allowed, unlike global notifications/server requests. This caught another
compatibility mistake: current resource URI mirrors use Mcp-Name, not Mcp-Uri.
The implementation and independent HTTP evidence now reflect those distinctions.

- Legacy POST response streams, including initialization, own per-request cursors. After a clean
  close, truncated event or actual truncated HTTP body, recovery GETs target only
  the configured endpoint with the original session/version and last complete
  cursor. The correlated result must match the ORIGINAL request ID. Parallel
  calls never share cursor state. No POST tool call or initialization is replayed.
- Empty priming events commit cursors; incomplete successors cannot advance past
  unreceived data. Empty IDs reset recovery and NUL-containing IDs are ignored.
  Safe bounded UTF-8 IDs, split-byte delivery, CR/LF/CRLF and final CR delimiters
  work. JSON/identity/header-control/size errors remain explicit protocol failures,
  not transport-retry authorization. Current streams tolerate request-scoped log/
  progress telemetry without treating it as model/tool content, and never send GET.
- Recovery uses up to three GET attempts with server retry intervals clamped to
  50 ms–5 s. Response recovery retains the original absolute request timeout;
  heartbeats/new GETs cannot renew it. Optional legacy notification streams have
  separate cursor state and bounded recovery, with visible terminal status.
  No cursor/reset, refusal, expiry, redirect, auth failure or exhaustion triggers
  automatic initialization, new credentials, forwarding or POST replay.
- Cancellation during recovery/waits and explicit disconnect close owned streams.
  Remote cancellation remains best effort, not effect rollback. Existing Coding
  approval and group-owned Claw allow_tools gates are unchanged. No new model loop,
  security sandbox or hosted service was added.

Evidence: independent real HTTP peers verify Unicode cursor/session/version headers,
POST/initialize result recovery, incomplete events and real Content-Length truncation, parallel
correlation, reset/no ID, invalid JSON/identity, auth/redirect/expiry/refusal, exhausted
attempts, original timeout and cancel/disconnect. Separate notification-stream
recovery and current no-GET/log/context-mirror cases pass. Actual Claw gateway runs
its one SDK agent: default policy emits no remote tool call; explicit trust produces
one POST, one recovery GET and one persisted tool result. Private-tmux Coding shows
the real approval BEFORE the tool POST, then the recovered result, one POST/GET,
no second approval, explicit disconnect and restored terminal modes. Models remain
controlled offline fixtures; no paid provider request or live daemon restart.

Verification snapshot — **2026-10-05 UTC**, pass 14:

- Full workspace after initialization recovery was integrated: **2,625 passed,
  7 skipped**, including **35** private-tmux terminal cases.
- Independent HTTP resumption/parser/context cases: **22 passed** after the final
  initialization addition. Actual gateway policy/persistence variants both passed;
  the live Coding approval/recovery/disconnect case passed in the full suite.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **361 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.

This closes applicable legacy SSE resumption, not the whole MCP row. OAuth, completion/subscriptions, richer
schemas and further negotiated input remain open, along with extensions, planning,
child supervision and platform/clipboard validation. Third-party server retention
and replay behavior remain unverified. The original goal and sandbox exclusion
remain unchanged; no commit or unrelated reset/stash was performed.

## Pass 15 — session-owned planning, inspection and explicit execution

Planning was brought forward because its host permission/ownership guarantees
also underpin future extension and child-agent work. The remaining MCP/OAuth,
extension, child-supervision and platform rows were NOT removed or narrowed.

- Frozen plan/step state carries mode, revision, objective, explanation and bounded
  pending/in-progress/completed steps. update_plan is a RESERVED session-owned tool,
  not a global registry tool or second agent loop. It cannot set mode; updates must
  match the current revision, and only one step may be in progress. UTF-8 sizes,
  count/status/unknown fields, old owner calls and invalid stored state are checked.
  Competing updates only accept one revision; failed persistence retains old state.
- /plan start OBJECTIVE enters read-only planning. The actual host pre-tool hooks
  block shell, workspace mutations, external MCP and unknown calls—even Coding
  allow-all and prior approvals cannot bypass this phase. Metadata updates remain
  available without granting workspace tool permission. The shared TUI inspector
  exposes objective/steps/status, selected-step scrolling and revision-aware mode
  transitions. Prompt hints retain a visible read-only/executing badge when closed.
- e execution and s stop require a RENDERED, visible confirmation and idle ownership.
  Resize/insufficient rows, metadata revisions, busy refresh and pasted Enter cannot
  authorize transition. Direct user commands execute REVISION/revise/stop use the
  same owner guards. They never submit a model turn automatically or change the
  underlying Coding approval mode/session grants or Claw MCP allow_tools policy.
- Coding reuses its owned command worker. Claw uses correlated group/session/revision
  controls, idle reservations and authoritative snapshots; old gateways disable
  operations, late/foreign replies are ignored and uncertain commands are not
  replayed. Metadata I/O is serialized off-loop, published after commit and settled
  before cancellation releases tool/control/group ownership. Modeled plan content
  cannot switch the host into execution.
- Plans persist with sessions and branch snapshots; navigation closes old owners
  and restores the selected snapshot. Forking a selected Claw branch inherits THAT
  plan rather than the current head. Active Claw plans prevent automatic daily/idle
  reset from silently dropping restrictions. Session/branch plan sidecars are atomic,
  independently authoritative and cached at startup: recovering a corrupt transcript
  catalog cannot silently discard plan mode. Invalid/null/non-object plan sidecars
  fail closed; gateway health reports the error without reconnect churn. Invalid
  initial controls do not create a phantom session or lose the client's session lease.
  Compaction keeps the existing static
  prompt cache prefix, adding plan data in a mutable block. Its silent flush tools
  now also retain host permission gates: this fixes a pre-existing MCP-policy bypass,
  while managed durable-memory flush and transcript/archive metadata still work.
- Actual terminal verification caught a busy/refresh eligibility UX issue: Claw
  could display a cached plan while controls were pending. The inspector now shows
  waiting/inspect-only state and invalidates confirmation while busy. A long-objective
  regression also ensures arrows bring the selected step into view, not an invisible
  selection below the viewport.

Evidence: structured one-SDK-loop plan updates; filesystem writes blocked during
planning under both ask and allow-all; stale revision/closed owner/invalid state/
failed persistence/concurrent updates; cancellation settling; session/new/branch
isolation; bounded sanitized rendering, visible confirmation and busy/revision
invalidation; actual Claw gateway/client group/session/idle checks, persistence,
branch-source inheritance, automatic-reset protection, catalog recovery/corrupt
sidecars, actionable health errors and compaction tool guards.
Private-tmux runs BOTH live clients. Coding retains the real tool approval after
execution; Claw blocks the file during planning and writes it only after explicit
execution under its normal policy. Both show structured plans, preserve drafts,
perform no automatic model turn on mode changes and restore terminal modes.

Verification snapshot — **2026-10-05 UTC**, pass 15:

- Full workspace after final input/session/storage/health guards: **2,646 passed,
  7 skipped**, including **37** private-tmux terminal cases across both clients.
- The latest inspector/SDK/gateway guard group passed independently: **10 passed**.
  Owner validation/concurrency/persistence/cancellation and catalog/sidecar recovery
  cases also passed independently and are included in the full suite.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **363 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.

Plans are branch snapshots,
not reconstruction at every older historical message. Cross-client Claw changes
refresh on inspector open; backend revision checks remain authoritative. Managed
session/memory/archive writes are application metadata, not all-process read-only
isolation. Claw controls enqueue no new agent turn themselves, but its existing
queues/scheduler continue under the selected mode and normal host policy. Platform
evidence is Linux/private tmux, not macOS/Windows proof.
No paid model call, live daemon restart, commit or unrelated reset/stash. The broad
goal stays active for remaining MCP, extensions, child supervision and platforms;
OS-enforced sandboxing remains excluded.

## Pass 16 — runtime-wired trusted Python extensions

Pi's primary extension documentation was revisited: its broader API includes tools,
events, commands, session/context contributions, custom UI/input/shortcuts and
provider registration. This pass connects real runtime behavior for the first group,
not a declaration that discovery helpers or a restricted API close the entire row.
Those additional surfaces remain on the original extension row and broad goal.

- Selected global/user IDs load lazily in dedicated owned Python workers. No project
  or session setting authorizes Python imports. Relative roots resolve at the trusted
  configuration directory. Entry modules/settings/dependencies/identities and command/
  UI capacities are validated. Settings travel over the owned pipe, not argv. Safe-path
  bootstrap avoids importing a workspace xdog.py or shadowing stdlib modules; packaged
  relative imports have isolated identities. Legacy hook exports share the new types.
- Existing stdio process/manager ownership is reused; there is no second model loop,
  hosted service or OS sandbox. Python code is FULLY TRUSTED with the same user access.
  Import/hook/command effects are not prevented by agent tool gates. Captured prints
  and structured UI never write raw control codes to the parent's terminal. Callback
  error/timeout/cancel disables and closes the worker, without replay; failed selected
  validation makes tool calls fail closed until explicit unload/reload. Initialization
  is serialized so another caller cannot bypass hooks while registration is pending.
- Tools enter the actual one SDK loop with collision-safe ext_ aliases, and commands
  enter live client dispatch/completion as /ext-ID-COMMAND. /extensions list/reload/
  unload uses idle host/group ownership and explicit trusted IDs. Commands preserve
  the editor draft and do not auto-submit a model prompt. Events include session,
  turn, message and tool lifecycle; legacy pre/post/stop callbacks remain usable.
- Pre-tool transforms are schema-validated before the ORIGINAL host policy decides
  on the EFFECTIVE args. Tests actually turn a requested filesystem read into a write
  and verify ask/allow-all/planning behavior; no rewritten write bypasses approval or
  planning gates. Claw plugin tools remain blocked unless extension_allow_tools is
  explicitly configured. Post-hook failures retain actual results; denied/error/binary
  results are not silently rewritten into successful text.
- Notice/status/widget text is structured, sanitized, bounded and rendered through
  the existing transient work area without stealing approval/output focus. Session/
  run/connection ownership fences late output; malformed socket snapshots cannot
  corrupt completion/rendering. A real Claw terminal test found a cross-loop socket
  task bug: delivery now marshals to the gateway loop before write tasks are created.
  Terminal tests also distinguish early setup widgets from finished activation receipts.
- Session/workspace navigation closes prior process owners and clears UI/commands.
  Stop hooks are bounded and cleanup reaps normal process groups; intentional escaped
  child processes/arbitrary trusted code are not an isolation guarantee. Existing
  planning and compaction gates remain in force; shutdown includes retired owners.

Evidence includes real import markers, process RPC tools/commands, hook veto and text
transforms, effective-parameter mutation/policy/planning, project activation refusal,
relative-root trust, concurrent initialization, timeout/process reap, required-hook
failure/unload/reload, stdlib/project shadow refusal, legacy hooks once plus stop,
malformed UI snapshots, actual Coding session/UI and Claw gateway/client wiring,
widgets/completion/notices and draft/session fencing. Private-tmux exercises BOTH
live clients with explicit loading, user command, widget, one SDK tool call (Coding's
real approval), unload/stop marker and restored terminal modes. Model calls remain
controlled local fixtures; no paid provider request or live service restart.

Verification snapshot — **2026-10-05 UTC**, pass 16:

- Full workspace with the final initialization, delivery and trust fixes:
  **2,660 passed, 7 skipped**, including **39** private-tmux terminal cases.
- Real extension-owner/session/gateway integration group: **12 passed** separately,
  covering legacy hooks, startup serialization, failure cleanup and trust boundaries.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **369 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- No paid model calls, live service restart, commit or unrelated reset/stash.

Rich custom dialogs/renderers,
shortcuts/input interception, persistent custom session/context contributions,
provider registration, richer extension command context and binary returns remain
OPEN. Third-party plugins, macOS/Windows and deliberately escaping trusted code are
unverified. No goal scope was removed, commit made or unrelated reset/stash performed.

## Pass 17 — durable extension entries and full pre-model context hooks

Pi's primary extension documentation was rechecked for appendEntry/context semantics:
custom entries are session/tree data, and context is a pre-model view, not a rewrite
of canonical conversation history. This pass supplies that actual behavior to both
Coding and Claw; it does not claim JavaScript API compatibility or close other rows.

- Active callbacks can append bounded, JSON-finite extension entries. Frozen host
  snapshots, per-extension namespaces, commit-before-receipt and serial persistence
  make entries durable independent of plugin process memory. Callback context gets
  only that extension's entries. Unload/reload, session activation, explicit new and
  selected branch restore have real persistence/ownership behavior. Old owners are
  closed on branch activation as well as session navigation.
- Coding stores entries in session/branch JSON. Claw stores independently authoritative
  atomic session/branch sidecars cached at startup: catalog recovery cannot clear
  entry state. Branch-source inheritance is explicit. Invalid/corrupt data fails rather
  than silently becoming empty state; failed writes leave the old snapshot published.
  Close invalidates writes, including attempted late shutdown contributions.
- The new context callback is installed on the ACTUAL Agent transform path before
  every model invocation. Coding composes the original SDK transform; Claw retains
  its system-prompt/static-cache/planning boundaries. Injected/filterable message
  views remain ephemeral: transcript search/persistence sees canonical messages,
  not injected context. Host tool/permission/planning checks remain authoritative.
- Serialization validates known roles and typed parts and COMPLETE tool/result
  batches. Unsupported/system roles, invalid images/signatures, orphan/incomplete
  results and silent content coercion are rejected. No-op hooks preserve original
  typed objects. Thinking/signature/image and tool metadata survive a real hook.
- Full contexts use checksummed owned 64 KiB transfers and off-loop spooling rather
  than oversized single JSON-RPC frames. A REAL >8 MiB image-bearing round trip is
  tested; media is not replaced with fake text to pass easier context tests. Identity,
  order, checksums, explicit 128 MiB context and absolute deadline bounds fail visibly.
  Parent file work is settled before disposal; workers own their result spools.
- Cancellation exposed an ownership weakness in the generic cross-loop bridge:
  Future.cancel can acknowledge before a metadata thread settles. The EXTENSION owner
  now waits for its remote task's actual settlement before releasing Agent/session/
  group ownership. A blocked real entry write test proves cancellation waits; a
  committed metadata update is not falsely described as rolled back. MCP's generic
  transport behavior was not changed to introduce call replay or a second agent loop.

Evidence covers real worker append/restore/context callbacks, finite/size limits,
failed-write/closed-owner behavior, invalid contexts before provider invocation,
original SDK-transform composition, host planning prompt/policy retention, canonical
history isolation, session/new/resume/branch/cache/catalog recovery, large typed image/
thinking/tool contexts, cross-loop cancellation settlement and actual gateway/client
paths. Private-tmux runs BOTH live clients through entry command, actual transformed
model input, reload/restored entry count, another turn and terminal restoration.
Models are controlled offline fixtures; no paid provider or live service restart.

Verification snapshot — **2026-10-05 UTC**, pass 17:

- Full workspace after the final cross-loop cancellation/transfer changes:
  **2,675 passed, 7 skipped**, including **41** private-tmux terminal cases.
- Entry/context/SDK/gateway group: **13 passed** independently; both live terminal
  entry/context/reload cases passed separately and are included in the full suite.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **372 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- No paid model calls, live service restart, commit or unrelated reset/stash.

Entry/context limits are explicit,
not silent truncation. Branches capture metadata at snapshot creation; arbitrary
older-message metadata reconstruction is not implied. Additional extension provider/
UI/input/command/binary capabilities, MCP authorization/management, child supervision
and platforms remain on the original goal. No commit or unrelated reset/stash.

## Pass 18 — extension select/input/confirm with suspended callback ownership

Pi's primary extension UI documentation was rechecked for interactive select/input/
confirm semantics. This pass wires actual user responses to waiting extension code
in both clients; it is not a fake notice widget, an approval bypass, or arbitrary
custom-component/provider compatibility.

- Async extension methods select/input/confirm validate bounded descriptors and
  wait on process-local futures. The worker responds with a pending callback/query
  receipt, keeps THAT callback suspended, accepts its matching host reply and returns
  its result. Sequential dialog chains work without re-executing tools or commands.
  No-UI callers get canceled values immediately; deadlines are bounded and expired
  questions do not fail/replay the whole plugin. Context-hook callbacks can also
  suspend, within the existing context transfer's absolute budget.
- A dependency-light shared form renders bounded sanitized questions/details,
  navigable choices, editable input and visible confirmations. Input caret ownership
  follows focus. Main drafts survive; tool approvals/output review remain above
  extension dialogs. Confirmation cannot be sent before a new selection/question
  has been painted, through pasted select/confirm text, on tiny/resize frames or
  repeat/release keys. Escape/Ctrl+C answers canceled; confirmation is NOT permission.
- Host-generated broker IDs, validated answers, thread-safe resolution, session/run/
  command/connection fences and bounded pending queues protect stale/foreign replies.
  Abort/navigation/exit/timeouts settle questions; cancellation reaps the waiting
  worker rather than replaying its effects. Closed owners cannot prompt a successor.
- Claw extension controls now use connection-owned async jobs with retained group
  reservations, not a reader-blocking await while a callback waits for that SAME
  socket's answer. Its original client can answer; another connected client cannot
  answer a known dialog ID. Control/turn UI delivery uses the gateway loop/write lock.
  Initial session creation is pinned in the dialog receipt so an intervening pong
  does not falsely invalidate the command's legitimate session lease.
- Tests distinguish real visible selection from Down+Enter arriving in one batch;
  an unseen new action is not silently confirmed. Four physical form rows are
  required to show the question/action/hint, and a tiny-resize test proves Enter
  cannot answer a hidden confirmation. No tool mode/grant/model turn is introduced.

Evidence includes real owned-worker sequential callback suspension/results, no-UI
canceled values, deadline expiry without worker quarantine, broker close/foreign ID/
invalid answer, callback cancellation/process reap, bounded/control-safe rendering,
actual Coding command/UI draft/approval priority, actual Claw gateway/client answers
and socket/other-client/pong ownership. Private-tmux exercises BOTH live clients
through select, visible Blue choice, editable input/caret, confirm and callback result
with restored terminal modes; Coding additionally verifies tiny resize refusal.

Verification snapshot — **2026-10-06 UTC**, pass 18:

- Full workspace with final quarantine/dialog cancellation wiring: **2,686 passed,
  7 skipped**, including **43** private-tmux terminal cases across both clients.
- Broker/worker/form/Coding/Claw dialog group: **9 passed** independently. Both live
  terminal select/input/caret/confirm cases passed separately and in the full suite.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **374 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- No paid model, live service restart, commit or unrelated reset/stash.

API cancel is a canceled VALUE,
not revocation/rollback of trusted callback effects. Dialogs never replace host tool
approval or planning restrictions. Third-party plugins, arbitrary custom UI, richer
forms/providers/input APIs, MCP authorization, child supervision and platforms remain
open on the original goal. No paid model, live service restart, commit or unrelated
reset/stash. OS-enforced sandboxing stays excluded.

## Pass 19 — child-supervision runtime foundation (not client parity)

The initial unintegrated supervisor draft is now hardened and regression-tested.
This pass does **not** expose delegation in Coding/Claw or mark the child-supervision
row complete. No parent permission gate has been relaxed and no child model request
was made to a live/paid provider.

- A dedicated owned loop supervises actual generic `Agent` instances: start,
  status/result/transcript inspection, identified steering/follow-ups, wait, cancel
  and close. Defaults are read-only access, inherited-context intent, 120-second
  deadline, 16 requests and a 32,000 reported-token stop threshold. Host factories
  MUST enforce access and snapshot stable context; these labels alone authorize
  nothing. There are no built-in Coding/Claw factories yet.
- Three concurrent children and 16 retained records are enforced atomically.
  A record keeps its concurrency slot until producer and final persistence settle,
  including cancel-during-write and repeated cancellation. Persistence runs exactly
  once per started child. Snapshot reads are locked; broken/disconnected UI callbacks
  do not strand execution. Bridge admission and close share a scheduling lock so
  concurrent start/disposal cannot enqueue work onto a stopped loop. Closing rejects
  new starts before draining; async close
  waits for the owned thread and preserves caller cancellation after settlement.
- The supervisor wraps the actual request adapter itself rather than trusting a
  factory to count requests. `max_tokens` is capped to the remaining reported-token
  budget before each request. Completed-message usage is observed synchronously
  before another request can start. Input/billable accounting can still overshoot
  the threshold on the final request, and providers may ignore output limits. This
  is deliberately NOT represented as an exact provider billing guarantee.
- Canonical transcript retention is bounded to 2,000 messages / 512 KiB aggregate
  serialized data; oversized completions/tool results stop the child and preserve
  the last size-safe transcript. Records include a stable checkpoint index for
  inspection/recovery without replaying a partial tool batch; factories providing
  inherited history with incomplete batches fail before the first request. Partial
  streaming messages are also checked. List
  snapshots limit result previews; transport chunking/full-source browsing still
  belong to the upcoming client integration.
- Reserved `agent_spawn`, `agent_control` and `update_plan` tools are rejected in
  children. Depth-one children cannot recursively spawn or borrow a parent's plan
  metadata owner. Namespaced extensions, memory and MCP require deliberate host
  ownership/hook integration; a generic factory is not permission inheritance.
- Generic `Agent` now retains its producer task and offers owner-loop-only
  `cancel_and_wait()`, alongside the existing cooperative cross-thread `abort()`.
  Producer admission before task cancellation guarantees idle/stream cleanup even
  for immediate cancellation. It refuses to abort a captured run's successor.
  Parallel batches now shield source-order awaits and cancel/join ALL started
  sibling tools on producer cancellation or finalizer failure. Otherwise an idle
  producer could leave later siblings mutating their workspace. Tests hold all
  three sibling cleanups and prove persistence remains blocked until they settle.
  Async cancellation stops a provider that ignores the abort event; it cannot
  terminate blocking/native code, cancellation-suppressing code or undo effects.

New regression evidence covers completed/failed/timed-out/canceled real mock Agents,
limits without factory cooperation, aggregate transcript bounds, owner-only tools,
reserved tools, input queue bounds, persistence errors, callback failure, repeated
cancel-during-persist, close-during-persist, immediate producer cancellation,
abort-ignoring providers, stable checkpoint checks, full parallel-tool settlement,
synchronous disposal, canceled asynchronous disposal and concurrent start/disposal.

Client integration remains REQUIRED: isolated Coding permission grants and approvals;
dynamic parent planning gates; Claw allow-tools/MCP/extension hook enforcement;
connection/group/session ownership and reader-responsive controls; stable checkpoint
context inheritance; durable per-child catalogs/recovery; and both `/agents` UIs.
Neither terminal parity nor inherited permission safety is claimed from core tests.
OS-enforced sandboxing remains excluded; the original overall goal stays active.

Verification snapshot — **2026-10-06**, pass 19:

- Final full workspace: **2,717 passed, 7 skipped** (186.10 s), retaining the
  existing **43** private-tmux terminal acceptance cases across both clients.
- New supervisor/producer/batch-ownership regressions: **31 passed** independently.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **375 source files**, no errors (unused-override notes only).
- Flow examples: **17 passed**. `git diff --check`: clean.
- No paid model, live service restart, commit, unrelated reset/stash or OS sandbox.
- Child supervision remains OPEN at the client/UI/permission-integration level.

## Pass 20 — actual Coding/Claw child integration

Both clients now expose real session-owned child agents, rather than only generic
supervision scaffolding. `/agents start TASK` defaults to read-only; `/agents start
--inherit TASK` requests inherited access, not extra authority. `/agents list`,
`inspect ID`, `steer ID TEXT`, `follow-up ID TEXT`, `cancel ID`, `wait ID` and
`close ID` inspect/control the same retained child. Reserved `agent_spawn` and
`agent_control` tools are registered on parents, never children. Status, result,
errors, canonical messages and stable checkpoints are inspectable as native JSON.
A dedicated focused live browser/search surface is still REQUIRED on this row.

- Host factories capture immutable parent state at admission, before asynchronous
  startup/persistence; inherited messages are deep-copied only through the latest
  complete tool/result boundary. Children retain model/options/native-tool capability
  and sequential/parallel tool mode, use the correct workspace/tool context, and have
  explicit shared-workspace/child/no-descendants instructions. Shared workspaces are
  deliberate; no worktree or OS sandbox is implied.
- Coding children have fresh approval managers/grants. Effective transformed tool
  arguments pass schema validation, child access restrictions, current parent planning
  gates and current parent permission mode. A planning/deny transition while approval
  waits is rechecked before execution. A parent's exact-call session rule cannot leak
  into a child; another child cannot use the first child's approval ID or grants.
- Real Coding permission panels identify their child and queue concurrent questions.
  Parent turn settlement no longer clears a waiting child approval; stale child-owner
  tokens/session switches cannot authorize successor work. Normal parent approval
  replacement retains its existing stale-callback identity guard. Native draft/caret,
  visible-choice/tiny-terminal confirmation rules and tool-approval priority remain.
- Claw children use the parent's selected tool set, current read-only planning gate,
  MCP allow-tools and extension allow-tools policies. No new Claw permission layer
  or implicit broader allowlist is introduced. User starts reserve idle group ownership;
  inspect/steer/cancel/wait remain available while a parent is busy waiting for children.
- Children have independent extension workers, entry namespaces and tool executors.
  Context-enabled children copy durable extension entries without rewriting the parent.
  Parent validation/context hooks still retain authority—including in-memory vetoes
  set by a parent command—before child hooks and host gates. Shared parent pre-hooks
  are serialized across callers, preserving async quota/veto semantics. Inherited
  validation/context requests explicitly suppress interactive UI instead of borrowing
  a parent's dialog lease; documented canceled values apply. Parent quarantine fails
  closed. Child turn/message/tool/end events run in its own extension environment;
  worker cleanup and final entry persistence settle before releasing the child slot.
  Child extension dialogs currently use documented no-UI canceled values; the richer
  scoped child dialog/browser workflow remains OPEN rather than borrowing a parent
  dialog/command UI lease and presenting it as complete parity.
- Session/branch/model/skill/plan/MCP/extension changes refuse active child work.
  Coding idle-only mutations freeze new admission across their complete operation,
  including replacement owners during navigation. Claw group queue/reservation and
  expected-session fences remain authoritative. Automatic Claw daily/idle resets are
  deferred while children are active. Interrupt cancels children without destroying
  the supervisor; Coding CLI/interactive exit and Claw daemon shutdown settle owners.
- Atomic per-child records live under validated session/branch sidecar scopes. Final
  transcripts/results and child extension entries survive reload; startup writes an
  interruption marker, never an automatic replay receipt. Closing releases the live
  catalog slot but archives the file. Corrupt catalogs visibly disable delegation
  while leaving the parent's UI/conversation usable, preserving files for repair.
- Claw socket jobs are connection-owned and bounded (eight pending). The reader can
  process a ping or cancel while another child wait is pending. Start request IDs
  cannot replay work on the same connection; changed reuse is rejected. Metadata
  reads do not accumulate large replay caches, and full receipt capacity cannot
  disable cancellation/inspection. Session/group changes reject stale controls; an
  initial empty session may be created only by an explicit start, not a list/ping.
- Claw group/channel/memory/goal/skill service tools execute on their original host
  loop, with child-loop update delivery and actual remote task settlement. No
  child-loop cancellation Event is passed into a host-loop waiter. A real group
  message service test asserts loop identity through child tool execution.
- Owner-loop aborts now take effect immediately at the next tool checkpoint. A
  regression proves an oversized assistant cannot start its attached mutation.
  Settled records release their Agent reference, avoiding retention of oversized
  canonical provider/tool history after saving the last bounded transcript.
- Shared MCP bridges now wait for actual remote coroutine cancellation settlement
  before a child can release its tool/session lease. This is ownership, not rollback:
  remote HTTP/server effects and already-started trusted callback effects may remain.

Evidence includes actual parent/child Agents with mock providers, real independent
extension subprocesses, a parent in-memory veto that a fresh child worker cannot
bypass, child-only durable entries, effective read-only/inherit/planning gates,
isolated approval IDs/grants and visible concurrent panels, stable inherited context,
active-child navigation refusal, archived/interrupted/corrupt catalogs, real Claw
socket/TUI commands, replay-safe starts and ping/cancel during child waits. New
private-tmux cases exercise both live clients: Coding's child approval/denial/list,
Claw's child start/results/list, subsequent draft/caret and restored terminal modes.
No paid model, live service restart, unrelated reset/stash or OS sandbox was used.

The original goal remains ACTIVE. Focused child thread/browser/search, richer child
UI/dialog ownership and applicable per-child specialization/configuration still
require implementation and verification alongside the open MCP, extension and
platform rows. This pass does not turn a JSON status command into a parity claim.

Verification snapshot — **2026-10-06**, pass 20:

- Final full workspace: **2,750 passed, 7 skipped** (186.91 s), including **45**
  private-tmux terminal acceptance cases across both clients.
- Supervisor/store/Coding/Claw child group: **62 passed** independently, including
  real MCP subprocess allow-tools gates and real extension subprocess inheritance,
  in-memory veto, serial validation and no-borrowed-dialog ownership tests.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **379 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- An existing Claw planning terminal test exposed a test observation race (file
  exists before its content write finishes); it now waits for the actual expected
  content and retains the final exact-content assertion. No product assertion was
  relaxed to hide the failure.
- No paid model, live service restart, commit, unrelated reset/stash or OS sandbox.
- The overall goal and richer child UI/configuration work remain OPEN.

## Pass 21 — focused child roster, transcript and bounded source transport

`/agents` and `/agents list` now open a focused bounded browser in BOTH clients;
`/agents inspect ID` opens that child's source viewer rather than printing large JSON
into native scrollback. Existing explicit slash controls and parent delegation tools
still address the same session-owned supervisor. Opening/closing/searching a view
never submits a model turn, grants tool access or cancels unrelated child work.

- Live roster metadata identifies child IDs, state/access and model-call/reported-token
  counters. Filter with `/`, select by stable ID and Enter to inspect; removal/filtering
  cannot confirm an unseen foreign/stale child. Summaries bound task/result previews,
  while complete retained text comes from separate immutable source captures.
- `n` edits a new read-only task; Tab explicitly selects inherited access. `s`/`t`
  edit steering/follow-ups (Ctrl+S/Ctrl+T in source view). Start/steer/follow-up/cancel/
  close require a separately rendered specific confirmation, preserve the parent's
  draft, and cannot be confirmed by a pasted newline, repeat/release key or tiny
  terminal frame. These confirmations are NOT tool approval; host gates and fresh
  child permission panels still decide actual tool use. A pending wait cannot lock
  the UI out of cancellation/inspection.
- The embedded source viewer reuses full-history background search, line/entry
  navigation/selection, horizontal scrolling and explicit output review. `f` provides
  opt-in fullscreen with paired alternate-screen restoration. Escape returns first
  to the roster, then the unchanged parent draft/caret. Live snapshot following is
  explicit (`l`) and does not overwrite an active search/selection.
- Immutable reference captures separate live assistant partials from canonical
  completed messages. Conversion is off the UI/daemon loop, includes full retained
  reasoning/prose/tool arguments/results and error markers, omits binary image bodies
  and thinking signatures, sanitizes terminal controls and heuristically redacts
  credentials. It is not a confidentiality guarantee. Final persisted transcripts
  do not acquire transient partials. Live tool-progress capture remains open.
- Coding uses one coalescing source-conversion worker; old/closed requests cannot
  publish into a successor view. Idle start commands retain the existing command
  lease, and source/control replies are fenced by child ID, request ID, session/view
  context and child-owner token. Approval/dialog/output focus outranks the browser.
  Switching the child changes output context; an old review cannot silently save
  another child's content. Explicit copy/export/share still means local, reviewed
  snapshots, never upload.
- Claw extends the existing connection-owned source sender/receiver with a separate
  child stream: 64 KiB blocks, ordered indices, byte count and SHA-256 validation,
  a 2 MiB display-source limit, coalescing and cleanup. Envelopes bind group and
  parent-session/child scope; session/branch revision and child membership fence
  transfer. A ping/cancel still runs while source spooling or child waits are active.
  An older gateway without the new capability reports an unavailable complete
  source, not a clipped fallback disguised as searchable full history.
- Archiving a child now owns its session metadata until the actual filesystem write
  settles, including cancellation. Navigation cannot release that owner during a
  canceled close. Existing source sender late-binding and coalescing semantics remain
  intact for main transcripts; child support is an additional owned source channel.

Evidence covers shared form visibility/tiny/repeat/paste guards; filtered/removed
IDs; full reasoning/tool-error/image-marker/credential-redacted source; coalesced
conversion disposal; actual Coding source/search/output-context/focus; actual Claw
TUI source/search and stale reply fencing; multi-block untruncated child text with
checksum verification and concurrent pong; canceled archive/navigation ownership;
and both live private-tmux clients through child browse/search/fullscreen/back,
parent draft/caret and balanced terminal mode restoration.

Physical terminal checks wait for source paint and the restored parent cursor before
sending subsequent keys. Otherwise a new bare ESC followed immediately by a letter
is legitimately an Alt-key sequence, not an acknowledged view-close event. The old
JSON-list assertions were replaced by stronger visible roster/source/search assertions,
not by relaxing permission, exact-content or terminal-restoration requirements.

The original goal remains ACTIVE: richer child dialogs, specialization/configuration,
full live tool-progress source, MCP authorization, remaining extension APIs and platform
validation remain required. OS-enforced sandboxing remains excluded. No paid model,
live daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — **2026-10-06**, pass 21:

- Final workspace: **2,763 passed, 7 skipped** (187.68 s), including **45**
  private-tmux terminal cases across both clients.
- Both upgraded live child browse/search/fullscreen/back/caret cases: **2 passed**
  independently (3.79 s), then passed again in the full suite.
- Shared child panel/source-conversion group: **9 passed** independently. Actual
  Coding/Claw viewer/search/context tests and multi-block child source with concurrent
  pong passed independently; canceled archive ownership passed independently.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **382 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- No paid model, live service restart, commit, unrelated reset/stash or OS sandbox.
- Richer child dialogs/configuration/live tool-progress and the broader original
  completion audit remain OPEN; the goal stays active.

## Pass 22 — live child tool-progress source

Running child tools now contribute immutable live-progress snapshots to both clients'
existing child source/search viewers. This closes the missing progress surface, not
the remaining dialog/configuration requirements or the overall parity goal.

- Progress is associated with the canonical assistant-message/part source ID, not
  merely a reused provider call ID or the most recent displayed tool. Reference
  snapshots copy frozen progress records, so a held source cannot be mutated by
  later updates. Source revisions advance only when reported progress changes.
- Both Coding's coalescing local conversion and Claw's checksum/chunk channel use
  the same presentation codec. Running output is visibly labeled as live progress
  with a pending final result. Tool errors, reasoning, arguments and binary-body
  omission retain their existing source behavior; credential redaction remains
  heuristic, not a confidentiality guarantee.
- Latest reported progress snapshots have a separate 512 KiB escaped-JSON retention
  budget and a 128-source limit. Capacity omission and byte clipping are explicit
  markers, never silent claims of complete output. Unicode/metadata/list overhead
  are budgeted. A display limit is NOT an execution veto or an extra permission;
  a valid small final result may still finish after oversized transient progress.
- Completion removes transient progress and replaces it with canonical tool results.
  Delayed source metadata cannot overwrite an already completed result. Progress is
  not injected into model history, persisted as fabricated tool results, or replayed
  after reload. Existing execution/canonical-history budgets remain authoritative.
- Capture, conversion, inspection and search make no model call, mutate no tool
  arguments and do not weaken parent/child planning, approval or allow-tools gates.

Evidence: real concurrent Agent/tool updates before final completion; immutable held
snapshots; separately bounded/clipped Unicode progress without aborting execution;
redacted/searchable running source with canonical-final precedence and orphan-ID
rejection; actual Coding child viewer/search while the tool waits; and actual Claw
source transfer showing running progress before the same child's final result.

Verification snapshot — **2026-10-06**, pass 22:

- Final workspace: **2,768 passed, 7 skipped** (188.90 s), including the existing
  **45** private-tmux terminal cases across both clients.
- Supervisor/shared-view group: **44 passed** independently. Actual Coding live
  viewer/search and Claw running/final transfer pair: **2 passed** independently.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **382 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- No paid model, live service restart, commit, unrelated reset/stash or OS sandbox.

The original goal stays ACTIVE. Richer child dialogs/specialization/configuration,
MCP authorization, remaining extension APIs and platform validation are still open.
OS-enforced sandboxing remains excluded.

## Pass 23 — independently owned interactive child extension dialogs

Child extension callbacks can now use the same supported select/input/confirm APIs
with actual user answers in BOTH live clients. Each child owns its own broker and
worker continuation. This replaces the former always-no-UI child path when an
interactive origin is available, without borrowing a parent's dialog reservation.

- At child admission the host captures an origin UI lease. Broker/question IDs,
  child IDs, session IDs and owner tokens fence answers. Questions display their
  child identity and use the existing sanitized bounded visible-confirmation forms.
  Their selection/input/confirm chains resume the SAME suspended callback, not a
  replay of the child task/tool/command. State remains independent of parent entries.
- Coding integrates child questions into the existing dialog queue, with real tool
  approvals and explicit output review retaining priority. Parent drafts survive.
  Parent dialog IDs cannot answer a child broker; a child/session/lease mismatch is
  rejected. A question leaves fullscreen before taking focus. A yes in an extension
  confirmation does NOT grant workspace/tool permission: an actual fresh child
  permission panel still waits for approval afterward, with its existing gates.
- Claw binds a child to the connection that admitted it, whether an explicit user
  start or the parent model's `agent_spawn` tool. Dialog traffic is marshaled onto
  the gateway loop and uses its write lock. The socket remains reader-responsive,
  including pong, child cancel and the originating connection's answer while a
  child callback/parent wait is suspended. A second connected client cannot answer
  even when it knows the entire dialog envelope and origin token.
- The lease survives normal parent-turn settlement: a model-spawned child may ask
  after its parent has already returned. It does NOT transfer to a successor
  connection. Origin request/run fences may pin an initially empty session only
  for its matching accepted start/run, never from an unrelated question. Pending
  startup receipts accept that matching pinned session rather than invalidating
  legitimate child work.
- Detach/disconnect closes pending brokers and removes future UI bindings; input/
  select return None and confirm returns False. A later question in the same
  trusted callback uses documented no-UI canceled values. No work is replayed or
  adopted automatically after reconnect. Child cancellation/deadline/disposal also
  settles questions; finalization disables new questions before closing workers
  and committing entries. UI-binding mutation/answer/detach is lock-protected.
- Parent inherited validation/context remains authoritative and deliberately uses
  silent canceled-dialog values rather than a child borrowing the parent's UI.
  Child's own context/pre/post/event/tool callbacks may use its independent lease.
  Callback cancel is an answer value, not rollback of trusted Python effects.
  None of these questions are OS sandboxing, execution approval or policy escalation.

Evidence includes a real owned-worker three-question child callback with user
Blue/user/True results; parent/foreign-child/foreign-token answer rejection; visible
Coding confirmation followed by a still-required real tool approval/denial; canceled
values on detach with no callback/model replay; actual Claw socket/TUI child input,
foreign-connection refusal and concurrent pong; model-spawned dialog answering after
parent settlement; disconnect/reconnect rejection without adoption; and two new live
private-tmux cases across both clients through child questions, separately owned
Coding permission, source inspection, parent draft/caret and terminal restoration.

This closes the supported child select/input/confirm ownership gap, not arbitrary
custom renderers/rich forms or per-child specialization/configuration. Those and
MCP authorization, remaining extension APIs and platform validation remain open on
the original ACTIVE goal. No paid model, live daemon restart, commit or unrelated
reset/stash occurred. OS-enforced sandboxing remains excluded.

Verification snapshot — **2026-10-06**, pass 23:

- Final workspace: **2,776 passed, 7 skipped** (202.22 s), including **47**
  private-tmux terminal cases across both clients.
- Coding/Claw child/dialog integration group: **34 passed** independently. Both
  new physical terminal child-dialog/approval/cursor cases: **2 passed** (6.45 s)
  independently, then again in the final full suite.
- Model-spawned child dialog after parent settlement and actual socket disconnect/
  reconnect refusal passed independently and in the full suite.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **382 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- No paid model, live service restart, commit, unrelated reset/stash or OS sandbox.
- Child specialization/configuration, arbitrary richer extension APIs/forms,
  MCP authorization and platform validation remain OPEN; the goal stays active.

## Pass 24 — per-child specialization and bounded configuration

Both clients now support task-specific child labels/instructions, fresh canonical
context, narrower tool sets and explicit resource controls. These are applied to
actual Agents, not only displayed settings. This does NOT add permission authority,
OS isolation or model/provider routing overrides.

- Frozen validated child configuration carries label, instructions, tool names,
  inherited-context choice, deadline, model-call limit and reported-token stop
  threshold. Defaults remain 120 s / 16 requests / 32,000 reported tokens. Explicit
  request/token limits may only decrease those caps; execution deadline remains
  1–600 s. Numeric booleans, NaN/infinity, unknown fields, malformed/duplicate or
  reserved tools and oversized text are rejected before admission/model work.
- Instructions append to the existing parent restrictions instead of replacing
  them. Tool selection is an actual allowlist intersection with the host factory's
  inherited tools (including independently owned external proxies). Unknown names
  fail before a model request; `tools: []` means NO executable tools. The parent
  tool set/prompt/history/settings/grants remain unchanged. A specialist instruction
  to ignore restrictions cannot bypass read-only/planning/approval/MCP gates.
- `--fresh` removes inherited canonical message history and child extension entry
  inheritance; mandatory parent policy/context transforms still apply. It is fresh
  conversation context, not a blank-policy or filesystem-isolation promise. Model,
  reasoning settings, workspace and host tool-execution mode remain inherited in
  this pass; per-child routing/reasoning and reusable presets remain OPEN.
- `/agents start` flags before TASK: `--inherit`, `--label NAME`, `--instructions
  TEXT`, `--tools NAME,NAME` (or `none`), `--fresh`, `--timeout SECONDS`, `--calls N`
  and `--tokens N`. `--` explicitly ends options for tasks beginning with flags.
  Quoted flag values use lexical parsing only—no shell/code/file evaluation—and the
  task suffix retains its original quotes/newlines. Duplicate/unknown flags fail
  visibly; empty starts cannot accidentally invoke an unbounded model turn.
- The reserved `agent_spawn` schema exposes the same bounded `config` object. Legacy
  outer deadline/context controls merge with nested settings rather than being
  silently ignored. Effective specialization/limits are inspectable in source and
  persisted in final/interrupted sidecars. Reload preserves configuration without
  restarting child work; old records still load with validated defaults.
- In the focused new-child form, Ctrl+G edits settings JSON without starting work;
  Enter validates/applies only the draft and restores the task. Final start review
  presents the full task and normalized settings in bounded pages. EVERY page must
  be painted before Enter can confirm, and width/row changes invalidate previous
  review. Repeat/release/paste/tiny-frame input cannot approve unseen settings.
  Tool approval remains independent even after this user start confirmation.
- Claw transports settings through expected-session/connection-owned start jobs and
  advertises `child_config`. A specialized command/form refuses old gateways instead
  of allowing them to silently ignore settings. The existing live roster identifies
  specialist labels; complete configuration is a separate source entry AFTER actual
  tool/prose results, so default metadata does not hide the useful output on entry.
- Claw explicit-start UI receipts are now concise child-ID/access notices, not a
  whole roster JSON dump whose expanding metadata could push the ID off the visible
  screen. Protocol start results still carry the validated child catalog. Search,
  status counters, transient progress and persistence ownership remain unchanged.

Evidence covers strict config/flag rejection; literal task preservation; actual
selected tool sets and clamped request limits; unavailable-tool zero-model failure;
specialist prompt composition; fresh context; exact persisted/restored settings;
legacy/nested merging; actual Coding CLI and browser start; actual Claw CLI/gateway
and browser start; old-gateway refusal; read-only/planning denial despite adversarial
specialist prose; and full-page/tiny/resize review guards. Two new private-tmux cases
exercise BOTH actual clients through settings/task preservation, no-start-before-
review, applied specialization/tool exclusion and restored parent draft/caret.

The original goal stays ACTIVE. Per-child model/reasoning selection and reusable
specialist presets, richer extension APIs/forms, MCP authorization and platform
validation remain required. OS-enforced sandboxing remains excluded. No paid model,
live daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — **2026-10-06**, pass 24:

- Final workspace: **2,812 passed, 7 skipped** (206.30 s), including **49**
  private-tmux terminal cases across both clients.
- Configuration/panel/Coding/Claw focused group: **76 passed** independently.
- Both new physical settings/task/review/application/cursor cases: **2 passed**
  independently (4.65 s). All six live child browser/dialog/config terminal cases
  then passed together independently (13.39 s) and in the final full suite.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **383 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- New configuration source initially displaced results in small source views;
  configuration now follows real output. Claw start receipt expansion also exposed
  a visible-terminal metadata-flood issue; the actual UI now displays a concise
  validated start receipt while retaining the full protocol catalog. Physical
  tests wait for the captured/restored task caret before changing form modes.
- No paid model, live service restart, commit, unrelated reset/stash or OS sandbox.
- Per-child model/reasoning selection and reusable presets, remaining extension
  APIs/forms, MCP authorization and platform validation remain OPEN. Goal active.

## Pass 25 — actual per-child model and reasoning routing

Both clients now support `--model MODEL` / `--thinking LEVEL` and equivalent
`model`/`thinking` fields in fully reviewed child settings. These route real child
requests independently without changing parent model/options/settings/permissions.
No authentication, provider activation, catalog sync, fallback or second tool loop
is introduced.

- `stream_fn_from_provider()` carries an optional cached-model resolver tied to the
  SAME provider/runtime that sends requests. Existing Runtime adapters can route
  across providers already active there. Plain custom StreamFns without this metadata
  contract reject explicit model/positive-reasoning overrides rather than consulting
  ambient global providers. Metadata resolution is off-loop; default inheritance
  requires no resolver I/O. Explicit `off` also works without a resolver.
- Selected models require canonical catalog identities, streaming chat capability
  and advertised context limits. Inactive/unknown models, embeddings/nonstreaming
  routes, malformed IDs and prefix mismatches fail BEFORE a model request, without
  hidden fallback to another provider's same-named model.
- Null reasoning inherits, `off` disables, and minimal/low/medium/high/xhigh require
  advertised reasoning and compatibility with reported effort lists. Unsupported
  explicit or newly inherited effort choices fail rather than silently downgrade.
  A switched nonreasoning model disables inherited reasoning, visible as effective off.
- Actual child context/prompt/output/native-tool capabilities come from selected
  metadata. Output is clamped; reported-token/request/timeout limits and ALL host
  planning/approval/external-tool gates remain authoritative. Same-model aliases
  cannot undo an explicit host native-tool override. Non-tool-capable models are
  described as such rather than promised automatic tool execution. Parent model,
  cache hints, options, history, grants, tool set and persisted settings stay unchanged.
- A model switch strips old thinking/response/text/tool continuity signatures from
  the child copy, omits reasoning-only messages and retains ordinary prose and
  complete tool/result pairs with truthful provenance. This also applies AFTER
  transient context transforms. Only the child's provider cache-routing key clears;
  parent context/history is not rewritten.
- Before forwarding, the guard checks transformed user/tool-result images against
  chosen capability/type/count/size limits. Switched models get a conservative
  byte-based context preflight covering messages, system and tool descriptions/
  schemas plus overhead and output reservation. This is NOT exact tokenizer billing
  or automatic compaction; oversized context fails with fresh/lower-output/larger-
  model guidance. Provider accounting/context rejection may still differ.
- Requested config and constructed effective model/reasoning/context/prompt/output/
  native-tool choices are separately persisted and inspectable. Early selection
  errors do not invent a successful effective choice. Old sidecars retain validated
  defaults. Independent child extension callbacks now receive their own effective
  model/reasoning/config, not the parent's model presented as their active choice.
- Tool schema, CLI flags, reviewed JSON, Coding command workers and connection-owned
  Claw starts all share the same configuration. `child_config: 2` negotiates routing/
  reasoning. Config-v1 gateways remain usable for earlier fields but refuse these
  overrides instead of silently dropping them. Full-page settings confirmation and
  tiny/resize/paste/repeat guards still apply to the two new fields.

Evidence covers actual-adapter/canonical routing and clamped options; unavailable,
malformed/unsupported catalog/effort zero-call failure; explicit host tool override;
continuity cleanup with untouched parent history; transformed image/context rejection;
actual Coding CLI/settings and Claw CLI/gateway/settings to a DISTINCT model/effort;
persisted requested/effective choices; unchanged parent model/options/settings and
read-only mutation denial. Two new private-tmux cases across BOTH live clients verify
distinct child-response markers, parent model/caret and terminal restoration.

Reusable specialist presets remain OPEN alongside MCP authorization, remaining
extension APIs/forms and platform validation. The original goal stays ACTIVE.
OS-enforced sandboxing stays excluded. No paid model, live daemon restart, commit
or unrelated reset/stash occurred.

Verification snapshot — **October 6, 2026**, pass 25:

- Final workspace: **2,839 passed, 7 skipped** (210.41 s), including **51**
  private-tmux terminal cases across both clients.
- Model routing/reasoning/capability/continuity/preflight group: **21 passed**
  independently. Model/Coding/Claw integration group: **99 passed** independently
  before the final same-model override regression, which also passed in the full suite.
- Both new distinct-model physical-terminal cases: **2 passed** independently
  (3.65 s). Those plus both reviewed-config terminal cases: **4 passed** together
  (7.68 s), then again in the final full suite.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **384 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- No paid model, live service restart, commit, unrelated reset/stash or OS sandbox.
- Reusable specialist presets, MCP authorization, remaining extension APIs/forms
  and platform validation remain OPEN. The original goal stays active.

## Pass 26 — reusable named child presets

Both clients now support explicit data-only specialist templates under user
configuration, `/agents presets`, `--preset NAME`, reviewed JSON and parent tools.
Templates reuse label/instructions/tools/context/budgets/model/reasoning, not extra
execution, permission or provider-activation authority.

- Frozen catalogs validate at most 32 IDs and 256 KiB RAW and EXPANDED data. Existing
  ChildConfig rules apply. Unknown fields, executable commands/imports, access/
  permission overrides, reserved tools, chaining and over-budget values fail. Snapshots
  are owned copies; external config mutation cannot rewrite group/session templates.
- Coding accepts global/user templates only; project/session data cannot add/replace
  them. Invalid preset namespaces show a diagnostic and disable template selection,
  not the parent's model/permission settings/conversation. Claw invalid operator
  templates raise its typed config error, not a fallback to default tools/settings.
- Sparse `--preset`/tool/JSON settings expand before admission; explicit flags override
  their selected fields. Default controls do not erase saved deadlines/fresh context.
  Access remains read-only unless inherited access is explicitly chosen SEPARATELY;
  templates cannot approve tools, change planning or copy parent approval grants.
- `/agents presets` reads descriptions with no model turn or empty Claw session.
  Ctrl+P in a new-child form applies a selected template ONLY to a draft; Ctrl+G
  adjusts settings. Starting still requires every page of the FULL expanded task/
  config to paint. Parent/task drafts and caret survive; selection isn't delegation.
- Expanded settings/provenance persist with results/interruption markers. Old children
  remain inspectable after a new catalog removes/changes a preset; reload never
  resumes or reinterprets work. NEW unavailable-preset starts fail before model work.
- Claw advertises `child_config: 3` for catalog/selection; older gateways cannot silently
  drop preset choices. Old config schemas still receive their supported inherited
  fields: newly introduced null/default keys are omitted, never chosen nondefault
  settings. Failed/malformed catalog replies clear stale templates and show errors.
  Unsupported catalogs don't masquerade as configured empty catalogs. Reads remain
  connection/session-owned, bounded and reader-responsive.

Evidence: immutable catalog/snapshots, explicit overrides and strict privilege/code/
chaining/tool/resource rejection; delegated/direct expansion and zero-call reads;
archived config after catalog removal; actual Coding global/project isolation, CLI/
picker and invalid-template deny-policy retention; actual Claw catalog/CLI/picker,
no session from browsing and invalid operator config without fallback; old-schema
field compatibility; full-page picker review; and BOTH live private-tmux clients
through preset selection/task/review, no work before confirmation, applied settings
and parent draft/caret/terminal restoration.

This closes the named-template gap of bounded depth-one supervision, not the overall
goal. MCP authorization, remaining extension APIs/forms and platform validation stay
OPEN. OS sandboxing remains excluded. No paid model, live daemon restart, commit or
unrelated reset/stash occurred.

Verification snapshot — **October 6, 2026**, pass 26:

- Final workspace: **2,859 passed, 7 skipped** (214.95 s), including **53**
  private-tmux terminal cases across both clients.
- Core/catalog/Coding/Claw/shared-picker preset and old-schema group: **18 passed**
  independently before final immutable-catalog/diagnostic hardening; all passed in
  the final full suite as well.
- Both new physical preset/task/full-review/application/caret cases: **2 passed**
  independently (4.65 s), then again in the final full suite.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **384 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- No paid model, live service restart, commit, unrelated reset/stash or OS sandbox.
- MCP authorization, remaining extension APIs/forms and broader platform validation
  remain OPEN. The original overall goal stays active.

## Pass 27 — explicit SDK OAuth/PKCE foundation (NOT finished client UI parity)

The existing HTTP MCP owner now supports an explicit pre-registered public-client
profile and actual authorization-code/PKCE exchange through its SDK. This pass does
NOT implement `/mcp` authorization controls in Coding/Claw, browser launch/review UI,
connection-scoped consent, token persistence/refresh or automatic registration. The
MCP authorization row therefore remains OPEN on the original goal.

Primary-source retrieval was attempted through web tools and the upstream raw
specification URL, but failed in this environment (connection reset/no returned
source). No current/latest MCP authorization-revision compliance is claimed. This
is an intentionally explicit profile, exercised against independent offline peers;
primary-spec verification and broader protocol interoperability remain required.

- Operator-configured HTTP profiles pin an HTTPS issuer, pre-registered public client
  ID and 1–16 scopes. HTTPS issuer endpoints must remain on that pinned origin, URLs
  must not contain credentials/query/fragment, and advertised resource/issuer/scopes,
  code/S256/public-client capabilities must validate before code/browser/token effects.
  HTTPS verification stays enabled, with optional explicit absolute CA files. OAuth
  cannot mix with static/environment Authorization headers or remote plaintext URLs.
- Current discovery profile reads the resource-specific protected-resource well-known
  path and issuer-specific OAuth-authorization-server well-known path. It does NOT
  yet process WWW-Authenticate pointers, root/OIDC fallback discovery, cross-origin
  endpoint consent, confidential clients, CIMD or dynamic registration. These limits
  are recorded rather than disguised as general OAuth compatibility.
- `McpManager.manage_auth(server, action, review_id)` exposes prepare/start/status/
  cancel/forget. Preparation returns a safe five-minute single-use review receipt;
  start explicitly consumes it, creates fresh state/S256 verifier/challenge and binds
  an owned 127.0.0.1 callback. No browser opens automatically. The resulting URL is
  intended for the operator's browser, not chat/token/code paste.
- Callback state and optional/required advertised response issuer are validated;
  malformed/foreign/duplicate/Unicode/early-before-admission callbacks cannot acquire
  or consume the legitimate code transaction. Header/query sizes, eight concurrent
  callback handlers, read deadlines and a five-minute absolute authorization deadline
  bound ownership. Immediate cancellation releases a producer even before its first
  step; duplicate starts cannot allocate duplicate listeners/code transactions.
- Code exchange includes the exact resource, client, redirect and verifier. Token
  scope cannot exceed reviewed scopes, token fields must validate, and responses
  are bounded uncompressed JSON. Discovery cookies are cleared between requests;
  configured MCP headers/bearers never go to issuer discovery/token endpoints.
  Redirects, ambient proxies, network/code replay and token refresh are not automatic.
- Tokens remain memory-only on the exact resource/profile owner, with local lifetime
  capped to one hour and expired credentials cleared/refused. Public snapshots/errors
  exclude bearer/refresh tokens, verifier and codes; completed/canceled flows release
  their transient verifier/URL/code state. Profile/URL changes cannot reuse another
  resource's bearer. Forget disconnects proxies and erases local credentials; it is
  NOT remote grant revocation/rollback.
- Authorization does NOT connect/register tools or change host permissions. Only an
  explicit subsequent connection obtains proxies. The existing Coding approvals,
  Claw allow_tools/planning gates and transport no-replay policy remain unchanged.
  Both POST and owned SSE/cleanup headers use the same resource-bound supplier.

Evidence includes real owned-loopback HTTP callbacks and independent mocked HTTPS
issuer/resource peers: valid S256 exchange, wrong state/issuer, stale/single-use review,
concurrent starts, callback bursts and pre-admission races, scope/resource/endpoint
mismatch, unsupported/downgraded metadata, expired/unsafe/overprivileged token rejection,
secret-free output, cancel/listener settlement, discovery-cookie isolation, redirect
refusal, changed-resource bearer prevention, and actual MCP connect after authorization
with allow_tools STILL false. No live identity-provider login or paid model occurred.

Required next work: standards/primary-reference verification; consent and connection/
session-owned auth controls in BOTH clients and real terminal flows; challenge/fallback
metadata handling and registration alternatives; credential persistence/refresh and
explicit logout/revocation semantics; further MCP and extension/platform audit rows.
OS sandboxing remains excluded. Goal ACTIVE; no commit, live daemon restart or
unrelated reset/stash occurred.

Verification snapshot — **October 6, 2026**, pass 27:

- Final workspace: **2,887 passed, 7 skipped** (212.87 s), including the existing
  **53** private-tmux terminal cases. No new OAuth terminal/UI coverage is claimed.
- New independent-peer/owned-loopback SDK OAuth regressions: **28 passed** (0.44 s).
  OAuth plus HTTP/stdio/resume group: **69 passed** independently before final
  changed-profile/Unicode/admission/burst guards; those guards also passed in full.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **385 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Primary MCP spec fetches failed in this environment; current-revision compliance
  remains unverified. No live issuer login, paid model, daemon restart, commit,
  unrelated reset/stash or OS sandbox.
- OAuth client UI/connection ownership, wider discovery/registration, persistence/
  refresh and the remaining MCP/extension/platform audit stay OPEN. Goal active.

## Pass 28 — real Coding/Claw authorization consent and ownership

The pre-registered public-client profile now has actual UI controls in BOTH clients:
`/mcp authorize SERVER`, or `a` on a configured server in `/mcp`. This is real owned
PKCE/callback/credential wiring, not a mock notice or automatic permission grant.
The existing HTTP authorization profile's interoperability limits remain OPEN.

Primary MCP authorization documentation retrieval succeeded this turn (the
2026-07-28 reference). HTTP authorization separation, PKCE/resource binding and
explicit client registration alternatives were checked; no claim of full supported
registration/discovery/refresh interoperability or live identity-provider validation
is made from that reference or offline peer tests.

- A dependency-light consent component pages EVERY issuer/resource/client/scope/
  endpoint/certificate-trust field before Enter can confirm the receipt. Paste,
  repeat/release keys, hidden/tiny frames, changed content/layout and resize cannot
  confirm unseen fields. Wrapper budget/invalidation propagates to the actual form;
  zero-height parent rendering cannot retain an old visible-confirmation flag.
- Start returns a validated URI matching the exact reviewed endpoint/client/resource/
  scopes/receipt plus S256/state and a loopback callback. The form directs the operator
  to their browser, with explicit full-URI review/copy; no browser or model auto-launch,
  code/token paste, draft insertion, sharing/export or credential output occurs.
  Status polls are read-only; cancel outranks an in-flight status poll. Authorized
  state explicitly says connection remains separate.
- Only `c` on the server list explicitly connects/registers tools afterward. Existing
  Coding approvals, Claw allow_tools/planning, model and child restrictions stay
  authoritative. `s` inspects authorization state; `f` reviews local forget/disconnect
  before erasing credentials/removing proxies. It is NOT remote grant revocation or
  rollback. Parent draft/caret, normal approval/output focus and scrollback survive.
- Each Coding view owns an immutable lease token and immediate revocation Event.
  Session/manager/context identity plus idle parent/children gate review delivery and
  credential commitment. Closing/leaving/canceling rejects late grants even before
  worker settlement; schema-changing operations exclude navigation/new child work.
  A plain finished resource credential may survive closing its success view, but a
  pending callback/transaction cannot authorize a successor view.
- Claw uses connection-owned bounded background jobs and per-view group/session/head
  scope records. Prepare/start/forget reserve idle group ownership only for their
  operation; the socket remains responsive during metadata and browser waits.
  Review/start/cancel cannot be stolen by another connection, even with its known
  receipt/lease ID. Cancellation/deallocation outranks a saturated eight-job quota;
  repeated closing requests coalesce to one bounded cleanup job per view. Source changes, child/model work and view/socket detach invalidate
  commitment. Disconnect settles jobs/listeners; a new connection cannot adopt old
  consent. Local credentials already committed remain resource/group-bound.
- Manager leases fence foreign prepare/start/cancel/forget; metadata-task cancellation
  settles before reservations release. Callback guards recheck the host's live scope
  before token commit. Complete safe status includes consumed review details for
  local forget; waiting status never reveals a live browser URI/receipt for adoption.
  No token is persisted, refreshed, automatically connected or made a tool grant.
- The existing restricted profile stays pinned-origin/pre-registered/public-client,
  query-free and memory-only, without challenge pointers/OIDC/root discovery/CIMD/DCR/
  confidential-client support. Auth-capability negotiation prevents old gateways from
  silently accepting missing controls. Wider authorization/MCP and platform work
  stays on the original goal rather than being waived by successful local UI flows.

Evidence includes shared full-page/tiny/resize/repeat/paste/copy guards, status-poll
cancel priority, actual Coding review/callback/explicit connect/local forget/draft and
pending-view revocation, actual Claw review/callback/connect/forget, a foreign socket's
refused receipt with concurrent pong, disconnect/listener settlement and reconnect
non-adoption, actual lease guard zero late-credential commit, secret-free display,
unchanged permissions/no model turn, and BOTH live private-tmux flows through visible
consent, real owned callback, explicit connection and parent caret/terminal restoration.
Coding's physical flow also exercises tiny resize denial. Offline issuer/resource
HTTP peers are independent of the implementation; no live login/paid model occurred.

The original goal remains ACTIVE: broader authorization discovery/registration,
credential persistence/refresh, richer MCP/extension APIs/forms and platform validation
still require implementation/verification. OS sandboxing stays excluded. No commit,
live daemon restart or unrelated reset/stash occurred.

Verification snapshot — **October 6, 2026**, pass 28:

- Final workspace: **2,898 passed, 7 skipped** (220.58 s), including **55**
  private-tmux terminal cases across both clients.
- Core/consent/Coding/Claw OAuth group: **36 passed** independently (2.54 s).
  Saturated eight-job detach/cancellation: **1 passed** independently (0.81 s).
  All passed in the final full suite.
- Both new physical consent/callback/connect/caret/terminal cases: **2 passed**
  independently (6.98 s), then in the full suite. No live identity-provider login
  is inferred from independent offline HTTPS peers and real local callbacks.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **386 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Primary MCP authorization reference retrieved and checked this turn; broader
  external interoperability/registration/persistence/refresh remains unproven/open.
- No paid model, live daemon restart, commit, unrelated reset/stash or OS sandbox.
- Remaining MCP/extension/platform requirements stay OPEN. Overall goal active.

## Pass 29 — challenge-aware resource and OAuth/OIDC discovery

The existing explicit authorization flow now discovers more actual server layouts
without guessing issuers, importing credentials or replaying failed tool calls.
The primary 2026-07-28 authorization/discovery reference was checked for challenge
priority, resource paths and ordered OAuth/OIDC well-known candidates. Registration,
persistence/refresh and full external-service interoperability remain OPEN.

- A bounded HTTP authentication codec handles quoted commas/escapes, optional header
  whitespace, multiple schemes and other schemes' opaque tickets. It selects ONE
  unambiguous Bearer challenge, with distinct metadata-pointer/scope fields; duplicate
  case-insensitive parameters, malformed quotes/control bytes, ambiguous Bearer
  choices and unsafe/relative URI pointers fail visibly instead of being ignored.
- Actual resource 401/403 POST and SSE denial hints are retained against the exact
  resource/config profile. Initial discovery and tool failures expose those hints
  to the manager; no automatic auth or POST/tool replay occurs. Malformed actual
  challenges block later fallback rather than silently replacing failed evidence.
  Old-profile hints do not migrate to a changed resource/issuer configuration.
- An advertised resource_metadata pointer takes priority and can carry an inert query.
  It is fetched credential/cookie-free, without redirects, only on the configured
  resource/issuer origins or an explicitly configured `metadata_origins` HTTPS
  allowlist (at most eight distinct origin-only entries). Metadata-origin trust does
  NOT change authorization/token endpoint trust or the pinned issuer/resource checks.
- Without a pointer, path-specific protected-resource metadata precedes root metadata,
  with duplicate/root paths eliminated. Only a missing GET route (404/405) permits
  fallback; invalid 200 metadata, wrong identity, redirects, bad JSON, network failures
  and token POST errors do not become hidden alternate-route/code retries.
- Issuers with paths use the ordered OAuth path-inserted, OIDC path-inserted and OIDC
  path-appended well-known candidates; root issuers use the two distinct root forms.
  Metadata must still match the operator-selected issuer EXACTLY before code effects;
  no default issuer/new provider/registration is chosen from arbitrary returned lists.
- Operation-specific challenge scopes outrank broad scopes_supported metadata; they
  need not be a subset of that listing. Otherwise metadata/operator fallback is
  explicit. Requested scopes remain within the configured operator ceiling; a required
  scope outside it fails with an instruction to inspect/update the profile. Empty
  scope listings are represented/reviewed as no scopes requested, not hidden broader
  defaults. Unsafe/overprivileged token responses remain refused by the existing flow.
- Prepared review metadata records actual discovery URLs and scope-source provenance.
  Existing Coding/Claw view/session/connection consent, full-page/resize confirmation,
  token lifetime, permission separation, lease cancellation and no-auto-connect remain
  intact. This pass adds discovery semantics, not approval or generic server trust.

Evidence includes independent multi-scheme/quoted/token68 headers, duplicate/control/
relative/ambiguous rejection; precise candidate order and deduplication; actual
path/root/OIDC fallback with no cookies/credentials; successful wrong-issuer metadata
that MUST NOT fall through; real denied MCP discovery followed by its retained custom
pointer/operation scope with exactly ONE POST; malformed denial preventing fallback;
operator-ceiling rejection with no code/browser/token; scope-less review; and explicit
metadata-origin support without token-endpoint trust transfer. Existing live auth UI
and transport regression cases remain in the full workspace verification.

Remaining original-goal work: registration/CIMD/DCR/confidential clients, token
persistence/refresh, scope-update/remote-revocation interoperability, richer MCP schemas/
completion/subscriptions/client capabilities, remaining extension APIs/forms and
platform validation. Goal ACTIVE. OS sandboxing remains excluded. No paid model,
live identity-provider login/daemon restart, commit or unrelated reset/stash.

Verification snapshot — **October 6, 2026**, pass 29:

- Final workspace: **2,919 passed, 7 skipped** (221.11 s), retaining **55**
  private-tmux terminal cases across both clients.
- Discovery/core-auth/Coding/Claw authorization group: **55 passed** independently
  (2.68 s), then in the final full suite. No new external identity-provider or
  discovery-specific physical-terminal claim is inferred from these offline peers.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **387 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Primary authorization/discovery reference checked for candidate/scoping order.
  Full service interoperability, registration and persistence/refresh remain OPEN.
- No paid model, live login/daemon restart, commit, unrelated reset/stash or OS sandbox.
- Remaining MCP/extension/platform requirements stay OPEN. Overall goal active.

## Pass 30 — explicit hosted client metadata and public DCR alternatives

The existing OAuth profile/UI now supports three explicitly chosen registration
strategies: pre-registered (unchanged default), hosted client ID metadata, and legacy
public-client dynamic registration. There is no silent fallback between strategies,
implicit hosted-document creation, client-secret escalation or automatic connection.
This closes public registration alternatives, not every OAuth/platform audit item.

- `registration: metadata` uses an operator-configured HTTPS client_id document URL
  with a path and an exact nonzero callback_port. The issuer must advertise client ID
  metadata support. Credential/cookie-free bounded retrieval validates document
  client_id EXACTLY matches its URL, client_name, public token method, code/grant types
  and the exact configured 127.0.0.1 callback. Logo/client_uri/jwks/other linked assets
  aren't fetched; local document hosting, redirects and dynamic fallback aren't used.
  Fixed callback registration is explicit, not claimed general loopback-port variance.
- `registration: dynamic` has no supplied client ID. The issuer's HTTPS registration
  endpoint must remain on its pinned origin. Preparation only reads/reviews metadata;
  it never registers. Full visible consent shows strategy/name/client assignment,
  callback semantics, scopes/endpoints and the remote-record side effect before start.
- After that single-use confirmation, an owned callback port is allocated and exactly
  ONE JSON registration POST requests a native public client, that callback, code
  response/grant, none authentication and reviewed scopes. Only a validated 201
  response's issued ID is adopted; wrong method/callback/ID fails. Unexpected secrets/
  management tokens are neither retained, displayed nor sent to token exchange.
- Registration creates a REMOTE record. Cancel/stale consent/network uncertainty is
  not rollback: listeners/temporary state settle, no local ID/code flow is adopted,
  and POST is never replayed. Retrying requires fresh review. A late registration
  response cannot escape the existing host lease/live guard. Persistence, reuse across
  restarts and remote registration-management deletion remain separate future work.
- Actual issued IDs replace only the child auth transaction's unassigned client,
  without inventing placeholder credentials. Authorization URI validation binds the
  issued ID/strategy to its original receipt and all resource/scope/endpoints/S256
  fields. Token exchange uses that public ID/verifier, never a returned secret.
  Completed safe review metadata supports local forget; it is not reusable consent.
- Coding/Claw propagate the same strategy through existing configured-server consent,
  status/cancel/forget and connection/session guards. Claw advertises mcp_registration
  so missing capability cannot silently omit registration effects. Authentication
  still registers no MCP tools or approvals; connection remains explicit and host
  allow_tools/planning/permission gates remain unchanged.
- Longer registration reviews exposed resize anchor behavior: layout/content changes
  now restart full review at the first field while clearing old visibility. Every page
  must repaint before confirmation, including after tiny resize. Tests retain exact
  no-effect-before-review and secret-free output assertions.

Evidence: independent hosted-document identity/public-method/callback rejection;
unsupported metadata without hidden DCR; actual public registration/code/S256/resource
exchange in both modes; wrong DCR ID/method/redirect rejection; receipt non-replay;
revoked consent after remote POST without local adoption; strict no supplied/coerced
credentials; actual Coding/Claw DCR UI and issued-client use with unchanged permissions;
plus BOTH physical terminal flows parameterized over existing and dynamic strategies,
no registration POST before review, real local callback, explicit connect and restored
parent caret/terminal modes. No live identity-provider interoperability is inferred.

Remaining goal scope: persistence/refresh, confidential authentication methods,
remote revocation/registration management and wider issuer interoperability; richer
MCP schemas/completion/subscriptions/client capabilities, extension APIs/forms and
platform validation. Goal ACTIVE. OS sandboxing stays excluded. No paid model, live
login/daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — **October 6, 2026**, pass 30:

- Final workspace: **2,938 passed, 7 skipped** (228.42 s), including **57**
  private-tmux terminal cases across both clients.
- Registration/Coding/Claw/shared-consent group: **25 passed** independently (3.78 s).
- Existing and dynamic physical consent/callback/connect flows across both clients:
  **4 passed** independently (13.18 s), then again in the final suite. Hosted-document
  identity/method/callback semantics are independently verified; no live issuer
  compatibility claim is inferred from offline HTTPS peers.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **388 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- No paid model, live login/daemon restart, commit, unrelated reset/stash or OS sandbox.
- Persistence/refresh, confidential methods, wider MCP/extension APIs and platform
  verification stay OPEN. Overall goal active.

## Pass 31 — explicit environment-backed confidential token authentication

Pre-registered OAuth profiles now support `token_auth_method` none (unchanged public
default), client_secret_basic or client_secret_post. Secrets come ONLY from an
explicit `client_secret_env` name; no literal secret/client assertion/config file
secret, silent method fallback or public-client secret escalation is accepted.

- Configuration validates method/source and restricts these confidential methods to
  pre-registered clients. Metadata/DCR profiles remain public none; unexpected DCR
  secrets continue to be ignored, not promoted to authentication. Secret environment
  names are bounded/validated. Missing/empty/control/non-ASCII/oversized values fail
  before discovery/start; exchange resolves the source anew without retaining it on
  an owner, review, status or browser URL. Removed late sources cause safe failure.
- Issuer metadata must support the explicit method; missing method metadata uses the
  Basic default, never implicit secret-post/public downgrade. PKCE, resource binding,
  scope ceilings, endpoint/issuer validation and all host lease/permission rules remain.
- Basic form-encodes client ID and secret individually BEFORE colon/base64 joining;
  secret_post uses its form body. No request uses both methods. Payloads are copied,
  not mutated. A credential-bearing header/body can go ONLY to the exact live review's
  pinned HTTPS token operation, never metadata, resource or registration endpoints.
  Redirects/proxies/cookies/replay remain refused; no secret is placed in auth URIs.
- Consent/status safely show the method and variable NAME, not value. Coding/Claw use
  existing full-page/resize confirmation. Claw advertises mcp_client_auth and requires
  explicit reviewed method/source fields for confidential starts, so older/missing
  controls cannot silently send credentials. Authorization still connects no tools
  or approvals; model/draft/allow_tools/planning remain unchanged.
- Unexpected background transport/codec failures now fail the authorization owner
  with a safe error, rather than leaving it waiting forever or publishing a traceback
  containing request credentials. Core output contains no client/bearer secret;
  existing local forget/cancel/expiry and no persistence behavior remain intact.

Evidence: independent actual Basic and POST exchanges with delimiter/space/percent
encoding checks; exact token-only routing; no multiple methods/payload mutation;
unsupported/inline/coerced/public/method mismatch rejection; missing/unsafe/removed
sources and metadata-default behavior; Coding UI method/source review without draft/
status leakage; Claw refused unreviewed confidential starts and a valid reviewed Basic
exchange; and BOTH live physical terminal flows parameterized over public existing,
public dynamic and confidential methods, successful local callbacks/explicit connects
and no private secrets in terminal capture. No live identity-provider login inferred.

Remaining original scope: credential/registration persistence and refresh, further
confidential methods (e.g. private-key assertion/mTLS), remote revocation/management,
richer MCP schemas/completion/subscriptions/client capabilities, extension APIs/forms
and platform validation. Goal ACTIVE. OS sandboxing stays excluded. No paid model,
live daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — **October 6, 2026**, pass 31:

- Final workspace: **2,960 passed, 7 skipped** (234.27 s), including **59**
  private-tmux terminal cases across both clients.
- Confidential/core/Coding/Claw group: **27 passed** independently (4.78 s).
- Public existing/dynamic plus confidential physical consent/callback/connect/caret
  flows: **6 passed** independently (18.39 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **389 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. git diff --check: clean.
- No paid model/live login/daemon restart/commit/unrelated reset or OS sandbox.
- Remaining persistence/refresh/MCP/extension/platform requirements stay OPEN.

## Pass 32 — reviewed resource-bound in-memory refresh

Both clients now expose **r** on authorized/expired OAuth status to prepare a fresh
five-minute, single-use refresh receipt. Every review field must paint before Enter;
refresh neither launches a browser nor registers a client. Claw advertises and
requires `mcp_refresh: 1`, carries existing connection/session/view leases and uses
its idle-group reservation. Coding preserves its draft, approvals and focus.

- The actual successfully granted review is retained separately from subsequent
  browser transactions. Code and refresh responses use one bounded secret-safe token
  codec. Requested/granted scopes remain within the actual prior grant; refresh cannot
  widen scopes to the original configured ceiling. Token fields have no dataclass
  diagnostic representation and public status exposes availability/state only.
- One explicit POST uses the exact resource, actual client ID, scopes and existing
  public/Basic/POST authentication helper. Credential-bearing data is restricted to
  the current operation's pinned token endpoint. No MCP request/tool replay or
  automatic reconnect is introduced. No refresh is attempted before consent.
- A valid response rotates refresh tokens, or retains the previous token only after
  successful validation if rotation is omitted. The old capability is removed before
  POST: failed/lost/invalid/canceled/late responses cannot retry an uncertain rotating
  token. A still-valid old access token may survive; this is not remote rollback.
- Owner cancel/close first cancel and join an active refresh before acquiring its
  operation lock, avoiding lock deadlock. Manager detach owns and settles the actual
  POST; revoked guards refuse late commits. Unavailable preparation releases newly
  acquired leases instead of blocking another view. Local forget clears both tokens.
- Physical testing caught a refresh-specific consent footer defect: paging and
  confirmation were enforced but hidden behind the status hint. The footer now
  exposes the same full-page confirmation as initial consent and explicit cancellation
  while refresh is pending. A focused regression prevents this from returning.
- A full-suite rerun also exposed a separate child-settings race: a pending roster
  poll could make local task submission return after the input component consumed
  its draft. Preparing a local child review no longer depends on roster settlement;
  final execution still requires a settled owner request, visible confirmation and
  existing permissions. A deterministic pending-reply regression preserves the
  exact task and checks that no child starts until the reply settles. Both physical
  child-settings forms are included in the focused rerun.

Evidence: independent owned loopback code exchanges followed by reviewed refresh
POSTs, actual narrowed grant scopes, rotation/no-rotation, overprivileged/unsafe/type/
lifetime rejection, stale single-use receipts, lost response without old-token retry,
revoked late commits and cancel settlement; actual Coding and Claw UI/gateway flows
with unchanged tools/approvals; and both private-tmux physical refresh flows with no
POST before review, explicit connect only and restored parent caret/terminal modes.
No live identity-provider interoperability is inferred.

Remaining original scope: durable credential/registration storage and reload, further
refresh interoperability, private-key/mTLS authentication, remote revocation/client
management, richer MCP schemas/completion/subscriptions/client capabilities,
extension APIs/forms and macOS/desktop/Windows validation. Goal ACTIVE; OS-enforced
sandboxing stays excluded. No paid model calls, live login/daemon restart, commit or
unrelated reset/stash occurred.

Verification snapshot — pass 32 (final local run):

- Workspace: **2,976 passed, 7 skipped** (244.26 s), including **61**
  private-tmux terminal cases across both clients.
- Refresh/core/Coding/Claw/shared consent/child-review group: **38 passed**
  independently (6.22 s).
- Physical public/dynamic/confidential OAuth consent/callback/connect/refresh and
  Coding/Claw child-settings forms: **10 passed** independently (29.53 s), then
  again in the final suite. Initial refresh-footer failures and the later
  child-roster race were fixed, not treated as successful verification.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **390 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Credential/registration persistence, further MCP/extension capabilities and
  platform validation remain OPEN. Overall goal stays active; OS sandboxing excluded.

## Pass 33 — explicit macOS image/text clipboard adapter

The shared reader now has a macOS-native image path instead of always treating
`pbpaste` as text. Both existing Ctrl+V and `/paste` hosts reach the same adapter;
no client-specific clipboard store or new submission path is introduced.

- One static `osascript -l JavaScript` program imports AppKit/Foundation and reads
  PNG/JPEG/WebP binary types before text. TIFF-only pasteboard data uses
  NSBitmapImageRep to produce PNG. It does not open clipboard file URLs, launch a
  browser, target application automation or poll the clipboard in the background.
- Input/output image bytes are capped at 10 MiB; TIFF bitmap dimensions at 100
  megapixels; text at 1 MiB. The existing runner bounds encoded stdout, times out
  and reaps the helper; the codec validates JSON/type/base64/decoded size without
  reflecting pasteboard contents in errors. Existing image headers, model limits
  and composer admission/epoch/permission guards still apply.
- A pasteboard change-count guard refuses inconsistent image/text snapshots. A
  selected but unavailable/invalid/oversized image fails rather than quietly
  changing to text. Native-helper failure never retries another clipboard backend.
  Only absence of osascript permits the existing text-only pbpaste fallback.
- Immutable results are published off-thread. Close cancels and reaps the owned
  helper and suppresses late publication; paste never submits a turn, changes
  permissions or replaces the runtime-owned image upload/queue recovery paths.

Evidence: exact native program executed with an independent AppKit contract in
Node (direct type priority, TIFF conversion, nil/conversion/pixel/byte failures,
Unicode/empty text, changed snapshot and no base64 output on refusal); actual
owned helper subprocess/image/text publication, malformed protocol without content
leakage, missing-helper fallback and cancel/reap; both physical production UIs now
parameterize image attach/Ctrl+V/preview/send/draft recovery across Wayland and
macOS helper protocols. These are offline contract/Linux terminal tests, NOT real
Apple JXA/NSPasteboard/desktop validation. Apple primary NSPasteboard and
NSBitmapImageRep API descriptions were inspected when designing the helper; they
do not replace native execution evidence.

Remaining original scope is retained: the real Apple bridge and Mac desktop/access
denial behavior, Kitty/iTerm/Windows validation; durable MCP credential/registration
storage and reload, further client authentication/management and refresh
interoperability; richer MCP schemas/completion/subscriptions/client capabilities;
extension APIs/forms. Goal ACTIVE; OS-enforced sandboxing stays excluded. No paid
model call, live identity-provider login/daemon restart, commit or unrelated
reset/stash occurred.

Verification snapshot — pass 33 (final local run):

- Workspace: **3,007 passed, 7 skipped** (248.45 s), including **63**
  private-tmux terminal cases across both clients.
- Native-program contract/codec/owned-helper plus existing attachment group:
  **35 passed** independently (1.58 s). Node is available here; script-contract
  tests explicitly skip where Node is absent (not a runtime dependency).
- Physical Wayland and macOS-helper-protocol image workflows across both clients:
  **4 passed** independently (7.39 s), then again in the final suite.
- Coding composer and Claw gateway/upload image regression group:
  **38 passed** independently (3.28 s).
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **391 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Real Apple JXA/AppKit/pasteboard/desktop execution remains unverified, not inferred
  from Linux/scripted interfaces. All original remaining requirements stay OPEN;
  overall goal active, OS sandboxing excluded.

## Pass 34 — explicit MCP prompt/template argument completion

Both live clients now use the manager's actual completion API, not local invented
suggestions. Select a prompt/template, Ctrl+Space to choose a named variable,
Ctrl+N/P to change variable, Tab to request, Up/Down to select and Enter to insert
into the editable argument/URI draft. Another explicit Enter retrieves the preview;
main-prompt insertion remains separately explicit. Ordinary typing never requests
completion or submits model input.

- Connected servers must advertise completions; fresh catalogs validate the exact
  prompt/template and argument names. The request carries ref/prompt/ref/resource,
  current prefix and other edited named strings as context.arguments. Metadata
  fields never collide with user-named arguments. Frozen responses retain ranking,
  at most 100 bounded strings, total and hasMore. Oversized/invalid/deferred responses
  fail, not silently truncate/coerce; hasMore invites explicit narrower requests.
- Core traffic allows one active and one queued explicit intent per server, with a
  cancellable 200 ms interval. Cancellation settles the actual owner; disconnect
  fails active/queued work without replay. Queued work rechecks the exact connection
  before any catalog/POST, and a late canceled reply cannot deliver to its successor.
  The configured timeout bounds queue wait, catalog and POST as one operation,
  rather than resetting for every page. This interval is fulfillment of an explicit
  intent, not automatic POST retry.
- Both hosts carry existing view/session/context ownership. Claw advertises and
  requires mcp_completion, uses the same coalesced/checksummed/chunked source sender
  and validates the bounded completion envelope on receipt. Old gateways fail
  visibly before sending, preserving the prefix. No oversized control response,
  tool call, approval, authorization, auto-connect or subscription is introduced.
- Editing/cycling variables cancels pending suggestions and invalidates selection;
  returned resource/ref/argument/prefix/context must match the request. Acceptance
  requires a painted selection and layout invalidation propagates through the
  wrapper even at zero rows. Suggestion/metadata controls and newlines cannot become
  terminal commands or extra rows; original strings remain data in escaped JSON.
  Repeat Enter is refused even after acceptance changes back to the parent form,
  so a held key cannot implicitly retrieve the newly completed draft.
- Variable edits used as completion context are retained on acceptance, avoiding
  mismatched final argument context. URI insertion uses bounded RFC 6570 scalar
  string expansion across all operators and prefix/scalar-explode modifiers;
  undefined variables are omitted, non-scalar values are refused. The resolved URI
  remains editable and is read only through the connected MCP server after a new
  explicit gesture. Invalid JSON/URI and busy Enter no longer discard the draft.

Evidence: independent current/legacy owned stdio and loopback HTTP completion peers;
reference/context, metadata, capability and count/type/size/deferred rejection;
bounded concurrency/debounce, cancellation, queued disconnect and no call replay;
strict IPC envelope and actual checksummed round-trip; scalar RFC examples and
Unicode/percent/modifier/error bounds; actual Coding and Claw UI/gateway selection
without tool/model activity, missing gateway feature gating and preserved drafts;
both private-tmux prompt/template completion, tiny/unpainted selection refusal,
preview only after a separate gesture and parent caret/terminal restoration.

Primary MCP completion specification and RFC 6570 were inspected. This is not a
claim of every third-party server or platform's interoperability. All original
remaining scope is retained: opaque/plain-resource completion references beyond
advertised named templates, resource subscriptions, richer MCP schemas/negotiated
client capabilities, credential/registration persistence, further auth/management,
extension APIs/forms and native Mac/desktop/Windows validation. Goal ACTIVE;
OS-enforced sandboxing stays excluded. No paid model call, live login/daemon restart,
commit or unrelated reset/stash occurred.

Verification snapshot — pass 34 (final local run):

- Workspace: **3,079 passed, 7 skipped** (254.78 s), including **65**
  private-tmux terminal cases across both clients.
- Completion transport/codec/timeout/cancellation, scalar-template, shared UI and
  actual Coding/Claw/source-transfer group: **78 passed** independently (6.41 s).
- Physical prompt/template completion and preview/parent restoration workflows:
  **2 passed** independently (5.34 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **394 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Opaque/plain-resource completion, subscriptions, richer MCP/schema/client
  capabilities, credential persistence, extension APIs and native-platform
  requirements stay OPEN. Overall goal active; OS sandboxing excluded.

## Pass 35 — opaque resource references and completion-only servers

The remaining resource-reference branch of completion is now implemented in the
actual manager, both live UIs and gateway data path. v on a server/resource row
opens a bounded JSON URI/argument/value/context form; names are explicitly supplied
from server documentation, never inferred from descriptions, paths or fictitious
catalog schemas. Ctrl+Space/Tab uses the existing owned value picker.

- SDK kind resource sends ref/resource for the exact opaque/dynamic/unlisted URI.
  It does not list catalogs, require invented variables, read resources, follow a
  URL/path or expand a URI template. The server defines the explicit reference/name/
  context semantics. Current-argument/context conflicts are rejected. Named prompt/
  template catalog checks and all byte/count/timeout/concurrency/connection guards
  remain intact; returned frozen results and strict IPC codecs carry kind resource.
- Tests exposed a real transport gap: both clients still rejected completion-only
  servers even though completion was implemented. Current/legacy stdio/HTTP now
  accept object-typed completions capability without pretending to expose tools,
  prompt/resource catalogs; malformed completion-only metadata fails closed.
- Both UIs expose the workflow from server rows (including completion-only services)
  and selected resource rows. Explicit argument/context JSON is preserved on invalid
  input. Selection updates this JSON draft, not a guessed URI. A new Enter requests
  LOCAL review labelled Operator-selected/edited and NOT a resource read/resolved URI.
  Preview i inserts quoted untrusted data into the retained main draft without a
  model turn. No remote read/tool call/approval/authorization/connection is introduced.
- Claw independently advertises/requires mcp_resource_completion: 1, so earlier
  named-completion gateways cannot silently receive the new action. Resource values
  use the existing checksummed/chunked session-fenced sender/decoder. Local review
  stays in the host and is not another gateway operation. Cancellation action routing
  includes the new request and owner/context/prefix guards still fence late replies.
- Physical testing exposed poor inspectability of long JSON reference/value/context
  fields in the inherited one-row editor. Argument forms now allow up to three
  bounded editor rows. Terminal verification matches displayed soft-wrap continuations
  rather than mistaking a wrapped value for lost data. Existing native scrollback,
  main draft, repeat-Enter gating, budget caps and parent caret restoration remain.

Evidence: independent current/legacy owned stdio and loopback HTTP requests for
unlisted opaque references and completion-only profiles, no list/read/URI following,
correct context/current-argument rejection and strict capability metadata; actual
frozen-result checksum transport; bounded/manual JSON/error/cancel/scope/soft-wrap
UI checks; both Coding and Claw app paths (catalog and completion-only server entry),
missing Claw feature refusal with retained prefix, local edited-data review/main-draft
insertion and zero model/tool/resource calls; named and opaque physical terminal
forms across both clients, local preview only after an independent gesture and
restored parent caret/terminal modes.

Primary completion spec confirms ref/resource covers URI or URI-template references;
no server schema is inferred from that. Offline evidence is not a claim of live
third-party/native-platform interoperability. Remaining original scope is retained:
subscriptions, richer MCP schemas/client capabilities, credential/registration
persistence and further auth/management, extension APIs/forms and native Mac/desktop/
Windows validation. Goal ACTIVE; OS-enforced sandboxing stays excluded. No paid
model call, live login/daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — pass 35 (final local run):

- Workspace: **3,113 passed, 7 skipped** (261.56 s), including **67**
  private-tmux terminal cases across both clients.
- Resource/named completion SDK/codec, manual/bounded/wrapped forms, actual
  Coding/Claw workflows and checksum transfer group: **76 passed** independently
  (10.45 s), then again in the final suite.
- Physical named and opaque argument completion across both clients:
  **4 passed** independently (8.00 s), then again in the final suite.
- Existing sender/stdio/HTTP transport regressions: **27 passed** independently
  (6.60 s); final suite also includes the new opaque checksum case.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **395 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Subscriptions, richer MCP schemas/client capabilities, credential persistence,
  further auth/management, extension APIs/forms and real native-platform
  verification stay OPEN. Overall goal active; OS sandboxing excluded.

## Pass 36 — typed structured JSON and owned dialect-aware validation

Modern MCP structuredContent is no longer incorrectly restricted to objects. Arrays,
scalars, booleans and explicit null are retained without coercion; presence is distinct
from omission and advertised schemas are validated. Legacy results stay object-only.
Canonical structured JSON is part of the actual tool content, including when a text
summary exists, so models, full-source viewers and saved histories see the real result.
Compact previews still bound visible rows; Ctrl+O/transcript opens complete data.

- Recognized dialect declarations select draft-04/06/07/2019-09/2020-12 semantics;
  default is 2020-12. Unknown dialects/required vocabularies and invalid references
  fail rather than fetch or silently downgrade. An explicit no-retrieval registry
  resolves local pointers, anchors/dynamic refs and bundled IDs only. ID/base changes
  cannot initiate HTTP/file reads. Inert defaults/examples are not mistaken for refs;
  targets explicitly referenced as schemas are traversed/checked. Input calls remain
  objects; provider-facing type normalization intersects that invariant, while raw
  refs/composition/nullable/boolean/conditional constraints remain authoritative.
- An optional sync/async AgentTool argument check runs before permission hooks. MCP
  uses it rather than the generic lightweight structural validator, which could not
  handle nullable type unions/boolean properties. Effective args are checked again
  before RPC; both hosts' extension pre-hooks use the same validator after rewrites.
  Default tool behavior is unchanged. Permission/planning/allow_tools gates remain;
  no server annotation/schema can approve a call. Cancellation covers pre-permission
  checks and revalidation, not just the later network request.
- Validation runs in one owned local helper, not the terminal/agent event loop. This
  closes regex CPU-stall and cancellation gaps without an OS sandbox: three-second
  queue/validation timeout, kill/reap on timeout/cancel, coalesced close and retention
  of an in-flight creation owner before releasing its child. `-I` prevents project/
  PYTHONPATH shadow imports. No provider/model/tool loop or URL retrieval is added.
  Later explicit pure checks may restart the helper; remote calls are never replayed.
  Worker caching is 32 entries/4 MiB SERIALIZED schemas (not process memory quota).
- JSON shape/UTF-8/finite-number/key/type/depth/node/byte checks refuse unsafe values,
  tuples/key coercion and nonfinite results. Compiled document hashes detect post-
  compilation mutations; model parameter copies cannot weaken validation. Generic
  validation errors do not echo private instances or schema URLs. Missing output,
  false-schema failures and deferred/unsupported results fail without call replay.
- Full-workspace packaging checks caught a missing direct referencing dependency.
  It is now declared by agent and portable SDK bundles, with only two corresponding
  lockfile metadata entries changed (no unrelated re-resolution/version upgrades).
  The isolated helper explicitly adds its own trusted library root: it works when
  vendored, while project/PYTHONPATH shadows remain excluded. An actual copied-library
  subprocess test proves the helper came from the bundle. LICENSE-EXCEPTION.md was
  reviewed; dependency metadata does not alter generated/kernel/vendored terms.
- Live testing exposed Coding's parameter formatter assuming dictionary properties;
  boolean/union properties now render without crashing. It also exposed the shared
  serializer dropping tool details. Bounded JSON metadata now round-trips without
  scalar/null coercion across Coding and Claw persistence/branches; live/non-JSON
  handles remain transient. Canonical text and typed details are both retained.

Evidence: independent current/legacy owned stdio and loopback HTTP with nullable
inputs and array/null/scalar/boolean/schema/missing/nonfinite outputs; pre-permission
actual agent validation and mutated effective args refused without a remote call;
dialect semantics, bundled IDs/dynamic refs, no network/default-data confusion,
unknown target dialect/ref/vocabulary/depth limits, changed-doc identity; actual
regex helper timeout/clean restart, cancellation/close/creation reaping and project
shadow refusal and actual vendored-package startup; both live UIs' permission denial and successful full-source/typed
persistence; physical production tool approval/structured inspector/parent restoration
on both clients. No live third-party model/identity-provider compatibility is inferred.

Remaining original scope: subscriptions; further schema/content/provider and mixed-
dialect interoperability, format-assertion/custom required vocabularies and ambiguous
HTTP header mappings through refs/composition; credential/registration persistence
and further auth/management; extension APIs/forms and native Mac/desktop/Windows
validation. No item is waived. Goal ACTIVE; OS-enforced sandboxing excluded. No paid
model call, live login/daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — pass 36 (final local run):

- Workspace: **3,171 passed, 7 skipped** (312.57 s), including **69**
  private-tmux terminal cases across both clients.
- Dialect/owned-helper/timeouts/cancellation/typed JSON persistence and actual
  stdio/HTTP/Coding/Claw structured-result group: **55 passed** independently
  (17.47 s), then again in the final suite.
- Physical nullable tool arguments, Coding approval, structured inspector and
  parent restoration across both clients: **2 passed** independently (4.62 s),
  then again in the final suite.
- Packaging/vendored isolated-helper regression group: **16 passed** independently
  (7.24 s); full suite verifies all distribution/vendored dependency declarations.
  Initial undeclared-dependency failures were fixed, not ignored.
- Existing tool/stdio/HTTP/live-extension regressions: **37 passed** independently
  (16.83 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **398 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Only the necessary referencing dependency declarations were added to the existing
  manifests/lock; no unrelated versions were updated or worktree changes reset.
- Remaining subscriptions/schema/provider/content interoperability, credentials,
  extension APIs and native-platform rows stay OPEN. Overall goal active; OS
  sandboxing excluded.

## Pass 37 — reviewed typed extension forms in parent/child UIs

The trusted extension API now has async form(title, fields, message, timeout), using
its real pending callback/UI-reply path. Both applications render typed fields and
return a named object only after explicit complete review; headless/timeout/cancel/
unsupported viewers return None, not automatically accepted defaults.

- Backend declarations support 1–32 text/integer/finite-number/boolean/typed-choice
  fields, named labels/help, required/default, text length and numeric bounds.
  Unknown constraints, duplicate names/typed choices, invalid defaults and type/
  boolean-number coercions are refused. Fields/declarations and returned answers
  are snapshotted/bounded (128 KiB/64 KiB). False, zero, empty text, explicit null
  choices and unset/omitted optional fields are distinct; required is presence,
  min_length expresses nonempty. No regex/code evaluation or nested schema is inferred.
- The standalone TUI form edits fields with Tab/Shift+Tab; choices cycle explicitly;
  multiline Enter adds data rather than submission. Enter/Ctrl+Enter starts complete
  form review. Help/constraints and required/omitted values are inspectable; every
  review page must paint before Enter submits. Ctrl+E editing, invalid input, resize,
  width/budget changes and hidden zero rows invalidate confirmation. Esc/Ctrl+C and
  pasted/repeated newline handling do not grant permissions or send model turns.
- ExtensionDialog wrapper forwards focus/budget/invalidation to the actual form.
  Coding's approval dialog still outranks extension input. Both parent and child
  callbacks retain their actual host-generated IDs/session/connection/UI leases,
  queue/deadline/cancel/disconnect behavior and independent permission policy.
  Invalid typed replies and foreign/guessed leases cannot complete a pending form.
- Claw negotiates extension_forms: 1 per connection (client hello and server pong).
  Unsupported viewers are canceled before being sent an unknown form; newer
  clients also refuse unnegotiated form semantics. Basic dialog behavior remains.
  Plugin fixture tool ordering was preserved, not used to change existing callbacks.

Evidence: independent field/answer/default/size/type codec and real extension worker
suspension, no-headless-default/deadline behavior, broker invalid-answer retention;
complete review/edit/multiline/paste/repeat/resize/zero-budget/control-character TUI
cases; real Coding command approval-priority/draft/focus/no-model paths and Claw
socket forms with malformed/foreign answers rejected and older-viewer cancellation;
both live child-agent callback form paths with owner isolation and typed results;
both physical command forms with required input, integer editing, paged/resize
review, default false/null/zero retention, cancel and parent caret/terminal recovery.

All original scope remains: arbitrary custom renderers/nested forms, input/shortcut
interception, provider registration and richer command/binary contexts; MCP
subscriptions/further schema/content/provider interoperability and credentials;
remaining native Mac/desktop/Windows validation. No typed-form subset redefines
completion. Goal ACTIVE; OS-enforced sandboxing excluded. No paid model call, live
login/daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — pass 37 (final local run):

- Workspace: **3,208 passed, 7 skipped** (326.68 s), including **71**
  private-tmux terminal cases across both clients.
- Form codecs/components/real worker and parent/child dialog ownership group:
  **43 passed** independently (18.29 s; 40 unrelated cases deselected), then
  again in the final suite.
- Physical typed-form edit/review/resize/cancel/default retention/parent restoration:
  **2 passed** independently (5.58 s), then again in the final suite.
- Existing dialog/child-agent/gateway regressions: **68 passed** independently
  (23.02 s), then again in the final suite.
- Ruff across packages: clean (fixture-only string wrapping corrected with identical
  runtime content). Strict mypy across seven namespaces: **400 source files**,
  no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Arbitrary renderers/nested layouts/interception/provider registration, further
  MCP/credential capabilities and native-platform rows remain OPEN. Full goal
  stays active; OS sandboxing excluded.

## Pass 38 — rich extension tool content and live image delivery

The real trusted extension worker now accepts tool strings (unchanged), typed
AgentToolResult objects and validated text/image/structuredContent/details results.
api.result builds the latter without file/URI reads or a second tool/model loop.
Commands remain text-only; this pass does not disguise that remaining API gap.

- The result codec supports text and validated PNG/JPEG/WebP base64 image parts,
  typed finite JSON structured data (including explicit null) and separate bounded
  JSON extension details. Snapshots preserve false/zero/null/omission semantics and
  reject unknown content/fields, coercions, invalid MIME/base64, nonfinite/non-JSON
  data and byte/count limits instead of silently dropping binary data. Limits are
  64 blocks/four images, 2 MiB decoded per image, 1 MiB rich text block, 256 KiB each
  structured/details and a 3 MiB encoded rich envelope. Legacy strings stay unchanged.
- An explicit rich-result protocol marker triggers independent host revalidation.
  The extension manager alone attaches extension_details(_present) metadata;
  generic MCP servers do not gain an extension-metadata namespace. Structured
  content uses the existing canonical JSON model/UI/persistence path. Extension
  details are actual typed metadata, not injected prose, approvals or hidden policy.
- Both live UIs preserve image content through the actual agent loop, component,
  canonical result and saved transcript. Existing approvals/planning/allow-tools
  restrictions still decide invocation. Text-only post-hooks leave non-text results
  intact; they do not replace images with text. Child factories keep their own
  extension/tool/context/permission ownership and typed result data.
- Integration exposed Claw dropping tool images from live ToolFinished events even
  though model/persistence content retained them. Final display events now forward
  bounded image parts to the existing component. Generic larger images get an
  explicit live-preview omission/transcript-retention notice, not an oversized IPC
  line or silently lost canonical data. Native scrollback/caret behavior is preserved.

Evidence: result builder/typed object/schema/metadata snapshot and malformed/unknown/
count/byte/MIME/nonfinite rejection; actual extension worker rich/typed/explicit-null
results and failure quarantine without replay; both UI denied/allowed execution,
model images, post-hook non-loss, canonical saved typed metadata; inherited child
result isolation; actual Claw wire image component and larger-image notice with
original bytes retained; physical production image/JSON inspection, Coding explicit
approval and parent caret/terminal recovery without base64 in the terminal.

Remaining original scope is intact: richer command contexts/returns, arbitrary
custom renderers/nested layouts, input/shortcut interception, provider registration;
additional audio/resource/error-image content interoperability; MCP subscriptions,
further schemas/capabilities/credentials and native Mac/desktop/Windows validation.
No result subset redefines completion. Goal ACTIVE; OS-enforced sandboxing excluded.
No paid model call, live login/daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — pass 38 (final local run):

- Workspace: **3,242 passed, 7 skipped** (341.38 s), including **73**
  private-tmux terminal cases across both clients.
- Rich-result codec/real worker/denied-allowed live UI/model/persistence/child and
  bounded-image notice group: **32 passed** independently (9.27 s), then again
  in the final suite.
- Physical extension image/JSON inspection, Coding approval and parent restoration:
  **2 passed** independently (5.28 s), then again in the final suite.
- Existing extension/live-UI/gateway image regressions: **27 passed** independently
  (12.21 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **401 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Commands/custom renderers/interception/provider registration, further binary/
  MCP/credential interop and native-platform rows remain OPEN. Full goal stays
  active; OS sandboxing excluded.

## Pass 39 — read-only rich extension command return artifacts

The remaining text-only command return path now uses the real extension worker:
strings remain compatible, while typed content/structured JSON/details returns
become a validated frozen ExtensionCommandResult. The rich tool builder is reused;
no new program/path/URI is interpreted from command output.

- SDK result identity/result snapshots are validated separately from text. Worker
  and runtime reject malformed/unknown result envelopes rather than str()-coerce
  them, preserving false/zero/explicit null and original image data. Canceled
  callbacks settle/quarantine the actual worker; they never replay or publish late.
- Coding carries a distinct rich_output in CommandResult through the actual owned
  command worker. Session fencing rejects stale artifacts; rich output cannot use
  the builtin skill prompt path. Builtin/legacy text/skill semantics stay unchanged.
  Claw returns a separate validated command_content payload with safe text summary,
  while retaining command/request/group/session fences and idle runtime ownership.
- Both UIs append a shared read-only command component with images, compact preview
  and complete Ctrl+O/transcript text/JSON/details. Its system-source role prevents
  tool impersonation and lets existing source inspection retain it. Original binary/
  typed data belongs to the artifact; textual source/export is not falsely called
  binary export. Narrow image fallbacks are clipped; native graphics data is intact.
- Neither command-looking prose, returned URI nor metadata becomes draft changes,
  tool approval, permission/planning changes, model input or automatic file output.
  Existing draft/focus and approval priority remain. Artifact display adds no
  canonical agent messages. Local UI artifacts are not claimed durable output files.
- Claw negotiates extension_command_results: 1 independently in client hello/server
  pong. An older viewer gets an explicit update-required summary without raw data;
  callbacks are not replayed and binary objects are not stringified into terminals.
  New clients refuse malformed/unnegotiated data. Existing rich-result frame limits
  keep the separate artifact within the bounded receiving transport.

Evidence: real worker rich/legacy command return, typed codec/namespace/snapshot,
malformed-return failure and cancellation settlement; shared component system-source/
image/Unicode/control/narrow/expanded detail checks; real Coding command draft,
permission-priority, no-model/history mutation and stale-owner refusal; actual Claw
socket negotiation, rich artifact/model-history isolation, source inspection and
older-viewer summary, plus stale/unnegotiated rejection; physical production command
image/JSON inspection, resize and parent caret/terminal restoration in both clients.

Original scope remains: richer command host-action/session/editor/navigation
contexts, arbitrary renderers/nested layouts, input/shortcut interception, provider
registration, binary artifact export/persistence and additional content interop;
MCP subscriptions/capabilities/credentials and native Mac/desktop/Windows validation.
No return-artifact subset redefines completion. Goal ACTIVE; OS-enforced sandboxing
excluded. No paid model call, live login/daemon restart, commit or unrelated
reset/stash occurred.

Verification snapshot — pass 39 (final local run):

- Workspace: **3,254 passed, 7 skipped** (348.20 s), including **75**
  private-tmux terminal cases across both clients.
- Command artifact codec/real worker/legacy/cancel/failure, component/system-source,
  live Coding/Claw/compatibility and stale-owner group: **10 passed** independently
  (5.98 s), then again in the final suite.
- Physical command image/JSON/inspector/resize/parent recovery:
  **2 passed** independently (5.35 s), then again in the final suite.
- Existing extension/live-UI/interactive-command/dialog regressions:
  **35 passed** independently (12.72 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **403 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Command host-action contexts, arbitrary rendering/interception/providers, binary
  persistence/export, remaining MCP/credential and native-platform rows stay OPEN.
  Full goal remains active; OS sandboxing excluded.

## Pass 40 — reviewed lossless binary command artifacts and read-only reload

Binary artifacts are no longer only ephemeral UI data. /export PATH artifact captures
the latest owned command component's full identity/text/image/JSON/details into a
versioned private JSON file. /artifact PATH reloads that file as read-only display,
not extension activation or execution of stored command names/prose/URIs.

- The SDK file codec includes xdog.extension-command/v1, validated normalized payload
  and SHA-256 canonical integrity. Original image bytes and typed false/zero/null/
  omission survive round-trip. Unknown versions/fields, corruption, invalid identity/
  MIME/base64/type/nonfinite data and 4 MiB limits fail. Integrity is not authenticity;
  no returned filename/path/script is followed. Explicit regular/nonblocking file
  reads reject FIFOs/nonregular sources and retain bounded data.
- A typed snapshot output entry point reuses the actual existing output lifecycle.
  Even an explicit target requires visible review; it cannot take the ordinary
  text-export direct-write path. Preview contains image MIME/byte/checksum and
  typed data but no base64; warnings explicitly say original sensitive binary and
  NO redaction. Binary artifact scope refuses implicit copy/share and lossy formats.
- The existing private 0600 temporary/fsync/no-overwrite commit writes only the
  captured reviewed bytes. No upload, file replacement, model/tool call or automatic
  save occurs. Context/epoch changes and cancellation invalidate preparation/review;
  committed changes are not falsely claimed rolled back. Text transcript output
  remains compatible and does not pretend to preserve binary data.
- Command displays get a current owner token. Both clients select only current
  session/connection/history-epoch artifacts, not stale/foreign content. Loading
  happens in one owned off-thread read, emits a typed result under the captured
  context and suppresses close/stale publication. Imported data is not model input
  or permission/planning state. OS-blocked filesystem work cannot be force-killed;
  these are responsiveness/audit guards, not OS sandbox or confinement claims.

Evidence: exact binary/typed codec and declared version/checksum/identity/type/image
rejection; regular/FIFO/oversize and owned loader/cancel/late-result checks; public
snapshot lifecycle mandatory explicit-path review, repeat/hidden/context refusal,
private no-overwrite files; actual Coding and Claw rich commands, original-byte
export/read-only reload, main draft/model/history non-mutation and stale owner
refusal without a gateway source-fetch fallback; both physical production JSON/image
export and reload workflows with tiny-review refusal, private files, checksummed
content and parent caret/terminal restoration without base64 in the terminal.

Original scope remains: command host-action contexts, arbitrary renderers/nested
layouts, input/shortcut interception/provider registration, further binary/error/
content interoperability; MCP subscriptions/capabilities/credentials and native
Mac/desktop/Windows validation. No new artifact subset redefines completion.
Goal ACTIVE; OS-enforced sandboxing excluded. No paid model call, live login/daemon
restart, commit or unrelated reset/stash occurred.

Verification snapshot — pass 40 (final local run):

- Workspace: **3,272 passed, 7 skipped** (354.56 s), including **77**
  private-tmux terminal cases across both clients.
- Lossless codec/loader/cancel, mandatory reviewed private snapshot and actual
  Coding/Claw export/import/owner/no-model group: **16 passed** independently
  (2.73 s), then again in the final suite.
- Physical lossless image/JSON export, tiny-review refusal, read-only reload and
  parent caret/terminal recovery: **2 passed** independently (5.57 s), then again
  in the final suite.
- Existing output/command source/compatibility regressions:
  **18 passed** independently (3.94 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **404 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Command host actions/custom UI/interception/providers, remaining content/MCP/
  credentials and native-platform rows remain OPEN. Full goal stays active;
  OS sandboxing excluded.

## Pass 41 — command-only editor host context and non-clobbering draft CAS

The extension API has actual get_editor/set_editor host requests, not merely a
serialized context dictionary or plugin-side guessed editor state. Interactive
commands receive captured text/cursor/image count plus a random revision; replacing
text succeeds only while the same snapshot/command/extension/UI owner remains current.

- The existing pending callback/UI-reply protocol carries a validated editor request
  and typed reply. Only original xdog/command requests get the runtime capability;
  API headless/tool/hooks/context/child paths return unavailable, and a forged worker
  flag cannot authorize another original RPC action. Hosts independently validate
  active command/extension, idle model, session/connection/history-epoch authority.
- Policy-free TUI CAS receipts bind original text, cursor and attachment IDs to a
  host-provided owner. They are random, single-use even on rejection, and explicit
  owner/state/attachment/cursor changes refuse replacement. Text/receipt/answer types
  and 64 KiB UTF-8 limits fail closed. Unsafe controls are rejected before mutation,
  not silently sanitized. Attachments/normal undo remain intact; no returned text/
  URI becomes a model turn, permission/planning change or host command execution.
- Editor operations are host services, not an unrenderable editor dialog. Coding
  defers them while approval/output/overlay has precedence. Existing broker IDs,
  deadlines/cancel/exit/disconnect/owner cleanup remain authoritative. A rejected
  CAS returns false without callback replay or clobbering the new user draft.
- Claw negotiates extension_editor: 1 per connection, independently of form/rich
  result capabilities. Unsupported viewers are canceled before receiving unknown
  editor semantics. Parent commands retain request/session/extension ownership;
  child/model work cannot adopt the editor service. Default basic dialogs stay intact.

Evidence: typed request/snapshot/receipt codecs; standalone successful CAS and
single-use/wrong-owner/text/cursor/attachment/invalidate refusal; real worker read/
replace requests, headless and tool callback refusal; live Coding and Claw command
success/conflict with no model/history/permission mutation and retained Coding image
attachments; invalid terminal-control replacement; actual older socket viewer
cancellation; both production terminal command transformations with editable returned
draft, zero provider calls and restored parent caret/terminal modes.

All original requirements remain: session/navigation/model/other command host-action
contexts, arbitrary renderers/nested layouts, input/shortcut interception/provider
registration; further content/MCP/credentials and native Mac/desktop/Windows validation.
This editor subset does not redefine completion. Goal ACTIVE; OS-enforced sandboxing
excluded. No paid model call, live login/daemon restart, commit or unrelated reset/stash.

Verification snapshot — pass 41 (final local run):

- Workspace: **3,285 passed, 7 skipped** (364.08 s), including **79**
  private-tmux terminal cases across both clients.
- Editor request/answer codec, policy-free CAS and real worker/Coding/Claw success,
  conflict/attachment/headless/tool/control/older-viewer group:
  **11 passed** independently (6.21 s), then again in the final suite.
- Physical command editor transformation/no-model/parent recovery:
  **2 passed** independently (4.69 s), then again in the final suite.
- Existing dialogs/forms/parent/child ownership regressions:
  **84 passed** independently (27.35 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **406 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Session/navigation/model/broader host contexts, custom UI/interception/providers,
  remaining content/MCP/credentials and native-platform rows stay OPEN. Full goal
  remains active; OS sandboxing excluded.

## Pass 42 — command session inspection and deferred native navigation

Extensions now have actual command get_session/open_sessions host requests, not
plugin-side guessed state. get_session returns bounded current session/workspace/
group metadata only. open_sessions(tree=...) queues the existing native session or
current-tree browser AFTER the callback successfully settles. It does not directly
activate/create/navigate a session from inside the extension callback.

- Typed requests/answers reject unknown operation/scope and malformed metadata or
  boolean receipt coercion. Commands receive no hidden transcript/system/foreign
  session content. API flags and original RPC action restrict access to interactive
  commands; tools/hooks/context/children/headless callers return unavailable.
  Host command/extension/session/connection checks remain independently authoritative.
- Deferred browser intent is bound to the same command ID and current context epoch.
  Queued receipt means queued, not activated/completed. Failed/canceled callbacks,
  stale IDs or changed owners/epochs discard it. Browsing occurs after runtime
  command/group ownership is released, not by changing the session underneath a
  waiting callback or replaying an extension on a new context.
- Both clients use the actual native NavigationPanel/provider and existing runtime
  catalog/group/session protocol. User Enter/new/tree gestures still own any mutation;
  existing idle/child/planning/permission/safe-checkpoint/persistence policies stay.
  No automatic model turn, draft replacement or attachment change occurs, and
  closing the browser restores parent editor focus. No second navigation runtime
  or client-only model of Claw state is introduced.
- Claw negotiates extension_session: 1 independently in client hello/server pong.
  Older viewers cancel before unknown host requests reach renderers. Basic dialogs,
  editor CAS, forms and rich command outputs remain compatible.

Evidence: session request/typed bounded answer codec; real worker snapshot/browser
suspension and correct tree scope with tool/headless refusal; live successful current
session and tree browser wiring, failed callback queue discard and retained draft/
model/history/permission state in both applications; stale command/epoch refusal;
actual older socket viewer cancellation; existing session/tree/editor regressions;
both production terminal native browser/current-tree entry/return, zero provider
calls and parent caret/terminal restoration.

Original scope remains: DIRECT session/navigation actions and paginated extension
catalog APIs, model/other command host contexts, arbitrary custom rendering/nested
layouts, input/shortcut interception/provider registration; further content/MCP/
credential capabilities and native Mac/desktop/Windows validation. Native browsing
is a concrete increment, not a substitute for those requested host actions or a
claim of completion. Goal ACTIVE; OS-enforced sandboxing excluded. No paid model
call, live login/daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — pass 42 (final local run):

- Workspace: **3,297 passed, 7 skipped** (376.45 s), including **81**
  private-tmux terminal cases across both clients.
- Session metadata/request/real worker and actual Coding/Claw success/tree/failure/
  stale/tool/headless/older-viewer group: **10 passed** independently (7.83 s),
  then again in the final suite.
- Physical native session/current-tree command browser and parent restoration:
  **2 passed** independently (5.59 s), then again in the final suite.
- Existing session/tree/gateway/editor host-context regressions:
  **17 passed** independently (6.35 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **407 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Direct session/navigation actions/catalog APIs, broader host contexts/custom UI/
  interception/providers, remaining MCP/credentials/native-platform rows stay OPEN.
  Full goal active; OS sandboxing excluded.

## Pass 43 — reviewed direct command navigation through existing host controls

The previously open direct-navigation branch is now a real command API:
navigate_session(action, session_id, branch_id, index). Five actions (new/resume/
branch/goto/fork) have strict typed/ID/action-field combinations. Empty applicable
session means the captured current owner; fork cannot substitute a foreign head.
One queued intent per command, not an arbitrary chain of silently applied mutations.

- Request validation rejects guessed operations, path-like IDs, boolean checkpoint
  indices and incompatible field combinations. Targets are snapshotted separately
  from callback dictionaries. Reply true means queued, never activated/committed.
  Headless/tool/hooks/context/child callbacks cannot use command authority; runtime
  original RPC and host active extension/command checks remain independent.
- A policy-free ExtensionNavigation wrapper uses the typed form for default denial
  and whole-page exact-target review: current owner, action/session/branch/index,
  callback-success/queued-not-activated and unchanged permission warnings. Every
  page must paint after width/budget changes. Hidden/tiny/repeat/pasted confirmation
  cannot approve; default false or cancel returns refusal. Coding approvals still
  preempt the dialog rather than being bypassed by a navigation request.
- Only successful callback settlement with unchanged command/extension/current
  context allows deferred dispatch. Stale/failed/canceled intents are discarded.
  Both clients reuse their actual native serialized commands/session controls,
  not a plugin-side or duplicated runtime. Target existence, group ownership, idle/
  child exclusion, checkpoint tool-call boundaries and durable plan/extension state
  are validated by the existing host path after review. No unknown target substitutes
  the current session. Native fork saves a checkpoint; branch/goto activation retains
  the normal application semantics.
- Integration caught a Claw fork routing gap: an empty API current-head reference
  must be resolved to the captured actual session before its native gateway control.
  That is now explicit; it does not invent a catalog or follow an arbitrary URI.
  Draft/attachments/permission policy and zero-provider navigation behavior remain.
- Claw negotiates extension_navigation: 1 independently. Older viewers that already
  support metadata/browser requests cancel reviewed direct operations instead of
  receiving unknown rendering/mutation semantics. Basic dialogs, editor, forms,
  rich outputs and browser entry are unchanged.

Evidence: request target/type/field and receipt codecs; real worker direct queued
request plus headless refusal; default-deny/exact-target/full-page/paste/repeat/zero/
resize TUI gates; BOTH live hosts' new/resume/branch/goto/fork, denial, failed callback
and missing targets with preserved draft/no model/permission mutation; stale command/
epoch refusal; older Claw viewer cancellation; existing native session/tree/editor
regressions; physical production reviewed new-session target/tiny-review refusal,
actual session synchronization and parent caret/terminal restoration in both clients.

Remaining original scope: paginated extension catalog/session/tree APIs and other
model/host-action contexts, arbitrary custom rendering/nested layouts, input/shortcut
interception/provider registration; further content/MCP/credential capabilities and
native Mac/desktop/Windows validation. Direct navigation no longer stays falsely
listed as absent; these other requirements are not waived. Goal ACTIVE; OS-enforced
sandboxing excluded. No paid model call, live login/daemon restart, commit or
unrelated reset/stash occurred.

Verification snapshot — pass 43 (final local run):

- Workspace: **3,331 passed, 7 skipped** (396.70 s), including **83**
  private-tmux terminal cases across both clients.
- Reviewed target/receipt/worker/component and real Coding/Claw all-action/deny/
  failure/missing/stale/older-viewer group: **32 passed** independently (16.67 s),
  then again in the final suite.
- Physical reviewed new-session target, tiny-review refusal, synchronization and
  parent restoration: **2 passed** independently (5.37 s), then again in the suite.
- Existing native session/tree/gateway/command-metadata regressions:
  **19 passed** independently (8.17 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **408 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Extension catalog/model/other host APIs, custom UI/interception/providers,
  remaining content/MCP/credentials/native-platform rows stay OPEN. Full goal
  active; OS sandboxing excluded.

## Pass 44 — read-only paginated extension catalogs from actual hosts

Extension commands now have list_sessions/get_session_tree with query, offset,
branch/target and optional membership revision. This uses the actual Coding session
manager/tree sources and owning Claw GroupRuntime, not plugin-side state or a client
cache. Catalog reads do not activate context, open a question or submit model input.

- Bounded typed response metadata includes current owner/resolved target, query,
  offset/total, at most 50 inert entries and explicit continuation. Entries preserve
  session/branch/checkpoint identity, depth/active/title/detail previews; no hidden
  conversation/system prompt is returned. Strings/IDs/nonboolean counts/scope/frame
  and duplicate/continuation validation refuse malformed input/output without
  coercion. Query/page/traversal limits are explicit (1000 bytes/256 KiB/one million).
- Real paging tests exposed command entry persistence changing Coding's recent-
  session ordering between pages. Command catalogs now use stable ID order (native
  browser recency unchanged) and SHA-256 membership/owner/query/target revision.
  Continuation can require that revision; changed membership yields unavailable
  instead of silently mixing catalogs. This is not a frozen metadata/full-history
  version: checkpoint previews/active flags may be current on each page.
- Original xdog/command and explicit handler authority restrict access independently
  of worker flags. Coding captures command/session/epoch before off-thread source
  work and refuses stale delivery; Claw holds the actual group command/session owner,
  reads group-private catalogs and rejects foreign-group tree targets. Existing
  state/permission/branch/checkpoint sources are reused without a second runtime.
- Owned disk operations are joined before cancellation releases the command/group,
  suppressing late extension state/catalog adoption. OS-blocked threads cannot be
  forcibly interrupted and are not a sandbox. Missing/foreign/unreadable/revision-
  changed reads return unavailable without quarantining a healthy callback or retry.
  Malformed declarations/replies still fail closed. No dialogs, model turns, draft/
  attachment changes, approval bypass, planning mutation or navigation are added.

Evidence: page/search/typed scope/entry/count/continuation/snapshot codecs; stable
identity ordering and required revision refusal; actual worker command catalog read,
no-terminal-question routing, tool/headless refusal and benign read-error liveness;
both real hosts' 61-entry multi-page reads (complete membership/no duplicates),
search/current tree/branch metadata and preserved draft/model/permission/session
state; Claw foreign-group rejection; Coding cancellation retaining and settling its
blocked filesystem owner without late entry commit; production terminal session/
current-tree catalog commands before normal browser entry/return in both clients.

Catalog APIs no longer stay falsely listed absent. Original remaining requirements
are intact: other model/host-action context, arbitrary custom rendering/nested UI,
input/shortcut interception/provider registration; further content/MCP/credential
capabilities and native Mac/desktop/Windows validation. No catalog subset redefines
completion. Goal ACTIVE; OS-enforced sandboxing excluded. No paid model call, live
login/daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — pass 44 (final local run):

- Workspace: **3,356 passed, 7 skipped** (399.76 s), including **83**
  private-tmux terminal cases across both clients (existing browser cases extended
  with real catalog commands rather than artificially increasing terminal count).
- Catalog codec/revision/worker and actual Coding/Claw pages/tree/group/cancellation
  group: **25 passed** independently (6.35 s), then again in the final suite.
- Physical command catalogs/native browser/current tree/parent return:
  **2 passed** independently (6.03 s), then again in the final suite.
- Existing command session/navigation/gateway host-action regressions:
  **41 passed** independently (23.62 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **409 source files**, no errors (unused-override notes only; initial missing local
  variable annotation corrected without runtime behavior changes).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Remaining model/host context/custom UI/interception/providers, content/MCP/
  credentials and native-platform rows remain OPEN. Full goal active;
  OS sandboxing excluded.

## Pass 45 — trusted runtime shortcuts and configurable effective keys

api.shortcut(name, key, description, callback) now registers an actual namespaced
command callback and declarative key in the owned worker. Both UIs invoke that same
existing command path from the ready idle prompt. This is not arbitrary interception
or another tool/model loop; global activation trust and all host command policy stay.

- Registrations validate command/name/key/description/count, canonical modifiers and
  actual command presence. Typing/unmodified editor/dialog keys, Escape/Ctrl+C/D/L/Z
  and cancellation/recovery routes are refused. Worker info/runtime snapshots carry
  bounded namespaced active registrations, excluding failed/unloaded callbacks.
- Built-in/effective USER bindings win over plugin defaults. Different extensions
  claiming one effective key are ALL disabled, not arbitrarily reordered/adopted.
  /keys reports active commands and exact disabled conflict/unavailable reasons.
  Existing keybindings.json remaps/unbinds using full ext-ID-command names, supports
  explicit multiple keys and rejects reserved/builtin/duplicate overrides. Config
  cannot activate an unloaded/unregistered callback or silently capture ordinary text.
- Dispatch requires a PRESS at the ready idle prompt. Approval/dialog/viewer/overlay/
  completion/child/plan/MCP/output ownership and active model/command work suppress
  extension dispatch. Release/repeat cannot duplicate invocation. The existing
  namespaced command lifecycle, session/connection/child/idle gates, editor CAS,
  permission/planning rules and cancellation are reused, not bypassed.
- Tests exposed Coding only refreshing extension snapshots from widget/notices.
  Commands now refresh their actual captured registration snapshot after settlement,
  including plugins with no widget. Owner checks reject stale snapshots. Claw
  separately advertises extension_shortcuts: 1; missing gateway support disables
  dispatch and is shown accurately in effective help. Unload/failure/reload updates
  callbacks and no key event auto-loads an extension.

Evidence: registration/command/canonical-key/capacity/snapshot invalidity; real
worker default command/reload/unload/legacy snapshots; effective built-in/user and
all-disabled cross-extension conflicts, remap/unbind/unregistered overrides and
reserved-route refusal even after user action unbinding; live Coding/Claw shortcut
command/editor CAS without provider/history/permission mutation, approval/viewer/
active-command suppression, repeat refusal and missing capability help; physical
production default Alt+K and configured Alt+X through both clients, /keys conflict
inspection, actual retained editable draft, unload/no late invocation and parent
caret/terminal restoration. Ambiguous legacy terminal chords are not claimed
independently distinguishable; the tested default/remapped keys are unambiguous.

Original scope remains: arbitrary input/shortcut interception, custom rendering/
nested UI, provider registration and other host APIs; further content/MCP/credential
capabilities and native Mac/desktop/Windows validation. Runtime shortcut registration
is now implemented but does not waive those distinct requirements. Goal ACTIVE;
OS-enforced sandboxing excluded. No paid model call, live login/daemon restart,
commit or unrelated reset/stash occurred.

Verification snapshot — pass 45 (final local run):

- Workspace: **3,394 passed, 7 skipped** (413.13 s), including **87**
  private-tmux terminal cases across both clients.
- Shortcut registration/worker/effective keymap/conflict/override and real Coding/
  Claw command/context/unload group: **34 passed** independently (4.28 s), then
  again in the final suite. Case-insensitive duplicate command overrides now fail
  rather than silently overwriting one another; the final suite includes this case.
- Physical default Alt+K and user-configured Alt+X, conflict help, actual command
  draft transformation and unload/parent recovery across both clients:
  **4 passed** independently (8.09 s), then again in the final suite.
- Existing keymap/editor/extension/live-command regressions:
  **53 passed** independently (14.46 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **411 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Input interception/custom renderers/providers/other host APIs, remaining content/
  MCP/credentials/native-platform rows remain OPEN. Full goal active;
  OS sandboxing excluded.

## Pass 46 — canonical submitted-input interception and no-loss refusal

api.on_input now registers an actual worker callback for submitted text before model
or canonical user-message admission. None/continue, explicit transformed text and
explicit rejection are typed/bounded; unsupported handled/image/command payloads
are refused rather than silently interpreted. This is not raw terminal-key interception.

- The optional generic Agent input_transform path covers initial and dynamically
  queued user messages, including explicit plain continuation. It transforms an
  atomic selected batch before admission, rebinds identified message IDs without
  changing original recovery identity and emits actual transformed/refused events.
  Consumption is emitted only when history receives the transformed message;
  failed selected identified inputs stay recoverable and plain inputs enter an
  explicit-recovery quarantine (drain_rejected_messages), not a next-run replay queue. Cancel settles the actual hook before
  releasing idle ownership. Refusals are not fabricated provider/assistant messages.
- Real extension workers receive text/source/active-channel/image MIME+encoded-size
  metadata, never original image base64. Transforms reconstruct user text with the
  original image objects/timestamps intact; decisions cannot silently drop/replace
  images or invoke host commands. Runtime chains follow deterministic registration/
  activation order. Input text/reason/callback count limits are 1 MiB/2000 bytes/32.
- Missing/malformed/exception/timeout decisions fail admission, not fallback to raw
  input. Failed registered filters remain required until explicit unload/reload;
  normal explicit rejection leaves a healthy extension active. Failed reload of a
  previously registered filter remains REQUIRED/failed until successful reload or
  explicit unload; reload failure is not implicit bypass. Hosted callback
  side effects are not falsely rolled back or replayed. Command editor/session/
  navigation authority remains unavailable in the input-hook original RPC context.
- Both actual host Agents bind the extension input hook; Coding composes any existing
  SDK hook. Child factories bind independently owned workers while retaining parent
  validation/policy. Claw initial scheduler/goal/system work bypasses HUMAN input
  interception, while queued human steering/follow-up uses the actual hook. No second
  tool/model loop, approval bypass or planning/permission/provider capability change.
- UI transforms update a matching original user echo and actual identified consumed
  input, not arbitrary older user history. Initial refusals restore original text/
  typed attachments to the draft, without admission/provider calls/auto replay.
  Live tests caught Coding recovery reading a lagging UI active-image batch rather
  than the current worker tuple: dispatch now uses its actual owned images. They
  also caught Claw transform receipts missing per-connection/run prefix normalization:
  all transformed/refused/consumed input receipts now share the same ID fencing.

- Headless tests caught refused pre-admission input appearing as empty success:
  print now returns a visible nonzero error and RPC an error rather than successful
  message_end. RPC initializes actual extension registrations before prompting.
- Full workspace terminal testing caught a generic output-review resize race:
  geometry changed after the loop check but before tick painting, allowing the
  previous-size fields to advance without a fresh cursor anchor. Raw terminal
  evidence proved this was a real missed query, not a missing marker. The raced
  paint now defers while remaining dirty; the next input loop queries/re-anchors.
  A deterministic lifecycle regression and unchanged physical resize assertions
  verify this; native scrollback and unrelated rendering policy remain intact.

Evidence: generic initial/steer/follow-up/continuation/abort and original receipt/
image/history semantics; actual worker typed decision/metadata/no-base64/authority,
missing/failed/malformed required-filter refusal and explicit unload; both live UIs'
canonical model/history text, typed image/persistence, original-refusal recovery and
normal permission preservation; actual identified follow-up image delivery/canonical
consumption across both clients; worker-image race regression; independent child task
hooks and unchanged parent history; Claw system-input exclusion; both production
terminal transformed provider input, actual user display/refusal draft recovery,
resize and parent caret/terminal restoration.

Original remaining scope is intact: arbitrary raw-key interception, handled-input
execution semantics, custom renderers/nested UI, providers/other host APIs; further
content/MCP/credential capabilities and native Mac/desktop/Windows validation.
Submitted-input interception is now implemented but does not redefine all extension
parity or waive those separate rows. Goal ACTIVE; OS-enforced sandboxing excluded.
No paid model call, live login/daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — pass 46 (final local run):

- Workspace: **3,430 passed, 7 skipped** (431.17 s), including **89**
  private-tmux terminal cases across both clients.
- Canonical input/queue/abort/typed decision/worker and actual Coding/Claw initial/
  queued-image/refusal/child/system/headless/reload group:
  **33 passed** independently (13.96 s), then again in the final suite.
- Physical submitted-input flows plus existing copy/export/share resize regression:
  **4 passed** independently (8.40 s), then again in the final suite. The initial
  missed-reanchor failure was fixed with raw-terminal and deterministic evidence,
  not hidden by weakening the resize assertion.
- Existing identified-input/queue/gateway/child regressions:
  **70 passed** independently (22.50 s), then again in the suite.
- Renderer/lifecycle/geometry race group: **53 passed** independently (0.30 s).
  Extension reload compatibility: **5 passed** independently (4.00 s).
  Existing RPC reader/approval regressions: **2 passed** independently (0.47 s);
  new headless refusal cases are part of the 33-case group and final workspace.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **412 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Raw-key/handled-input/custom-renderer/provider/other host API gaps, remaining
  content/MCP/credentials and native-platform rows stay OPEN. Full goal active;
  OS sandboxing excluded.

## Pass 47 — explicit handled input without counterfeit model/history admission

The previously open handled decision is now an actual API/worker/runtime/Agent and
UI outcome. It requires typed nonempty feedback (64 KiB maximum), not an implicit
None drop, image mutation, permission grant or host command. Remaining input hooks
stop for that request; logical handler consumption is distinct from model admission.

- Generic HandledInput/InputHandledEvent carry original request/source/ID and explicit
  feedback. Identified handled inputs are removed once without InputConsumedEvent or
  automatic replay. A handled request is not rolled back if a later batch item fails;
  only other original requests are refused/recovered. SDK handled receipts are
  inspectable/drainable, including original typed attachments. No fabricated user/
  assistant message enters canonical history. Empty input batches emit no stale
  old-assistant error/answer as a new result.
- All newly supplied requests being handled cannot invoke the model with old history.
  Genuine unhandled queued work may proceed, with existing ONE_AT_A_TIME boundaries.
  Explicit prompt([]) continuation remains unchanged rather than being accidentally
  redefined by the new empty-prepared logic. Original image metadata-only exposure
  and later tool/model/permission/planning gates are retained.
- Both live UIs emit system/extension feedback and restore original images to the
  composer, not consumed text back into a replay queue. Handled initial Claw text is
  no longer considered uncertain input during a later abort. Run/connection/prefix
  fences and identified ownership cleanup include the distinct handled receipt.
  Claw content-free final completion closes the actual run without fake prose.
- Print text mode exposes feedback; JSON mode emits one valid system/input_handled
  envelope with any REAL assistant result separately optional. RPC emits explicit
  handled/completion metadata rather than hiding it in empty success. Model/history
  scope and original caller image ownership stay distinct. Non-TUI channel feedback
  is channel output, not claimed model generation.
- Child supervision stores feedback separately (64 entries/64 KiB) with explicit
  extension-result labels, zero model-call accounting when fully handled, child record
  reload validation and system-role source inspection. It does not merge child input/
  result into parent history or impersonate an assistant in the child transcript.

Evidence: generic initial image ownership/no-model/history, identified steer/follow-up
receipts, all-handled old-history refusal, explicit empty-list continuation and queue
boundary cases; feedback/type/field/count bounds; both actual shipped clients' handled
initial/queued images, feedback/ownership cleanup and no raw fallback/duplicate call;
independent child task feedback/result/source/counter behavior; handled vs rejected
text/JSON/RPC completion; existing queue/supervision/store/source regressions; physical
production transform/handled/reject flow with explicit provider-call counters and
parent draft/caret/terminal recovery. Trusted callback effects are not falsely claimed
rolled back. Older-viewer handled-outcome interoperability is NOT inferred merely
from a parser accepting new event names.

Original scope remains: raw-key interception, arbitrary custom rendering/nested UI,
providers/other host API, further content/MCP/credential capabilities and native Mac/
desktop/Windows validation. No handled-text subset redefines completion. Goal ACTIVE;
OS-enforced sandboxing excluded. No paid model call, live login/daemon restart,
commit or unrelated reset/stash occurred.

Verification snapshot — pass 47 (final local run):

- Workspace: **3,450 passed, 7 skipped** (440.80 s), including **89**
  private-tmux terminal cases across both clients (existing submitted-input cases
  extended with handled outcomes/provider counters, not artificially recounted).
- Generic initial/queued/old-history/image/queue-boundary and actual worker/parent/
  child/print-JSON-RPC handled/refused input group: **53 passed** independently
  (19.87 s), then again in the final suite.
- Physical production transform/handled/reject and original draft/caret/terminal
  recovery with explicit response-model counters: **2 passed** independently
  (5.67 s), then again in the final suite.
- Existing identified-queue/supervision/store/source/gateway/RPC regressions:
  **81 passed** independently (6.41 s), then again in the final suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **412 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Raw-key/custom-renderer/provider/other host API, further binary/content/MCP/
  credentials and native-platform/older-viewer rows remain OPEN. Full goal active;
  OS sandboxing excluded.

## Pass 48 — custom-view worker/broker foundation (UI integration still OPEN)

This pass hardens the unfinished custom-view protocol rather than claiming the
custom-renderer parity row is closed. Neither shipped interactive client enables
`custom_handler` yet. Existing dialogs, permission prompts and terminal ownership
remain unchanged; an extension asking for a custom view gets `None` on hosts that
do not explicitly advertise this capability. There is no raw-terminal rendering
fallback, client-only dialog owner, extra model loop or OS sandbox claim.

Implemented and exercised with an owned real extension worker:

- `api.custom(title, render, on_input, ...)` keeps Python callbacks inside its
  original suspended worker callback. Rendering takes viewport width/height and
  returns lines plus an optional cursor. Only input callbacks may propose an
  explicit bounded JSON done/value result. Frame requests never replay the original
  command or resolve its worker future; the original validated UI reply does that.
- Separate custom capability negotiation avoids creating phantom questions on
  headless, dialog-only or not-yet-wired hosts. A custom-only host works without
  borrowing ordinary dialog handlers. Nested registrations cannot overwrite an
  existing custom callback, and interleaved commands cannot lend their callback ID
  to frame callbacks.
- Host-generated dialog IDs, worker view identity, strict event fields/geometry,
  typed modifiers, reserved host keys, bounded paste/lines/cursor/JSON snapshots,
  and a 32-request FIFO quota include in-flight and cross-thread scheduled work.
  Closed-loop checks and scheduling-failure rollback do not leak that quota.
- A completion proposal is accepted only against the preceding live frame revision
  and unchanged nonempty viewport. Stale revisions, changed geometry and hidden
  zero-height requests publish another review-required frame instead. Ordinary
  dialog answer submission cannot bypass this path with a fabricated custom value;
  cancellation remains available through the original broker.
- Deadlines, renderer failures, close and outer callback cancellation settle/join
  frame tasks and release the dialog lease. Renderer errors expose content-free
  diagnostics, not private exception text. A real hung worker frame is reaped and
  quarantined after the view/transport deadlines; its command is not replayed.
  Worker protocol contribution envelopes
  are drained separately from the validated custom frame.

Important remaining work: a terminal-safe policy-free custom component, actual
Coding/Claw parent and independent child wiring, connection/group/session/child
leases, older-viewer capability refusal, focus/permission precedence, visible
busy/quota feedback, sanitizer/caret policy and physical resize/input/paste tests.
The custom input fence must cover EVERY forwarded key and paste on a physical
geometry race, not just the existing Enter/Space confirmation guard: arbitrary
custom callbacks can propose completion on any accepted input.
Frame lines are currently **untrusted wire data**, not permission to emit ANSI;
no live UI should enable this handler before those protections land. Arbitrary
nested UI, raw-key interception, provider/other host APIs, remaining MCP/content/
credential capabilities and native-platform validation retain their original rows.
The full goal remains ACTIVE; OS-enforced sandboxing remains excluded.

Verification snapshot — pass 48 (October 7, 2026):

- Workspace: **3,518 passed, 7 skipped** (446.64 s), retaining the existing
  **89** private-tmux terminal cases. These are regressions for existing client
  functionality, **not custom-view physical terminal validation**.
- Custom codecs/broker/ownership/capability negotiation and actual worker group:
  **70 passed** independently (5.82 s). The full suite collected the first 68;
  the loop-closing scheduling race and hung-worker deadline/reaping cases were
  added after collection and verified independently in the final 70-case run.
  The final fixture uses a worker-owned invocation counter, not a constant
  stand-in for detecting callback replay.
- Custom foundation plus existing dialog/form/editor/navigation/runtime group:
  **119 passed** independently (11.94 s).
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **414 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Custom terminal component/client/child/transport wiring and physical tests remain
  OPEN, as do all other original outstanding capability/platform rows. Goal ACTIVE;
  OS sandboxing excluded. No paid model calls, live logins/daemon restarts, commits,
  unrelated resets or stashes were performed.

## Pass 49 — live sanitized custom views in both clients and independent children

The pass-48 protocol is now wired into the shipped Coding and Claw clients, not
just a broker test harness. It stays a bounded inline custom view, not a claim
that every nested/fullscreen/raw-key/provider/host extension surface is complete.

- Shared dependency-light `ExtensionCustom` paints bounded worker-produced lines,
  optional host-owned carets and visible pending/refusal feedback. Render is pure;
  poll sends initial/resize requests through enqueue-only host callbacks. Python
  render/input callbacks remain in their original suspended owned worker, and the
  original completion future resumes the command/tool once, without replay.
- Vetted SGR styles, indexed/true colors and per-line resets are supported. OSC
  links/clipboard, APC/DCS graphics, cursor/erase/private/alternate-screen controls
  and supplied host markers cannot reach raw terminal output. Caret insertion
  clamps to a whole grapheme boundary and actual viewport; renderer/input failure
  diagnostics never echo private exception text. Malformed Kitty/control/Unicode
  keys and host enqueue errors do not crash the UI or fall back to raw input.
- Generation-tagged requests/frames fence old same-size views after blur, hide,
  budget, geometry or invalidation changes. The broker tracks the newest requested
  epoch even when quota refuses its resize, preventing old queued completions from
  committing. Acceptance requires both live revision and generation. Stale epochs
  cannot submit custom data through generic dialog answers.
- TUI's physical resize guard now covers EVERY forwarded custom key/paste because
  arbitrary callbacks can complete on arbitrary input. Escape/Ctrl+C remain
  unconditional view cancellation; redraw, suspend and exit remain host-owned.
  Same-owner focus reassertion is idempotent, not a repeated blur/focus that would
  invalidate every frame on each tick. Approvals, output panels and overlays keep
  their precedence, with a fresh frame required after restoration.
- Coding parent/child event and request routing uses existing session/dialog/child
  UI ownership. Claw negotiates `extension_custom:1` and reader-responsive queued
  requests, checks original connection/group/session/child leases, and stamps
  frames under the live dialog lease/write lock. Unknown/foreign/stale requests
  are refused. Older viewers, capability withdrawal and disconnect cancel owned
  unsupported questions without transferring them to a new connection.
- Real command and tool callbacks return finite JSON data (including false/zero/
  null), never host permission grants or fabricated assistant/model turns. Child
  workers retain independent original callback/state, not parent metadata proxies.
  Close/disposal detaches custom handlers and settles frame tasks under the same
  original ownership and deadline path.

Evidence includes independent codecs/broker/component tests; both actual clients'
parent/child completion and cancellation with original draft/caret/model counters;
foreign connection/session/child lease refusals; older-viewer refusal, capability
withdrawal, disconnect/no-adoption and actual Claw parent model/tool lifecycle;
physical production parent AND child custom frames in both clients, ANSI stripping,
atomic multiline paste, hidden keys/paste/Enter refusal during resize, fresh-frame
restoration, explicit independent Coding child tool approval, cancellation, child
source inspection and termios/native-scrollback/caret recovery. The first physical
child-source assertion selected the prior child: the final test targets the newly
started concrete ID through the existing inspect command, rather than weakening
result or resize assertions.

Remaining original scope: nested/richer custom containers and extension-requested
fullscreen, raw-key interception outside a custom view, provider/other host APIs,
remaining MCP/content/subscription/credential interoperability and native Mac/
desktop/Windows checks. These stay OPEN. The full goal remains ACTIVE;
OS-enforced sandboxing remains excluded. No paid model calls, live identity login/
daemon restarts, commits or unrelated reset/stash occurred.

Verification snapshot — pass 49 (October 7, 2026, final source):

- Workspace: **3,600 passed, 7 skipped** (483.13 s), including **93** private-tmux
  terminal cases: the original 89 plus FOUR actual new custom parent/child paths.
- Custom codec/broker/generation/component and actual Coding/Claw parent/child/
  legacy/disconnect/withdrawal/model-tool group: **146 passed** independently
  (26.77 s), then again in the final workspace suite.
- Shared component: **57 passed** independently (0.29 s), including malformed
  Kitty keys and SGR-interleaved emoji/accent grapheme caret AND clipping cases.
- Production custom terminal, native-scrollback/input/paste/resize/approval/model
  counter/cancellation/source/caret/termios group: **4 passed** independently
  (17.41 s), then again against the final source in the workspace run.
- Existing renderer/lifecycle/dialog/form/parent/child regression group:
  **95 passed** independently (24.98 s), then again in the workspace suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **415 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- A first full regression run also passed (3,597 cases); the final full run was
  collected after the final caret/clipping hardening and new regression additions,
  not inferred from that earlier snapshot. Counts are not summed across runs.
- Inline custom views are supported on the verified Linux path. Nested/richer
  containers, extension-requested fullscreen, raw-key interception outside custom
  views, provider/other host APIs, remaining MCP/content/subscription/credential
  and native-platform rows remain OPEN. Goal ACTIVE; OS sandboxing excluded.

Known inline rough edges retained on the full audit: host chrome shows the title;
explanatory body text belongs in the render callback (separate message metadata is
not independently paginated). Kitty repeat/release delivery is not yet exposed to
custom callbacks; normal repeated PRESS events still work. These are not silently
counted as complete UI-API parity.

## Pass 50 — owned fullscreen, metadata paging and enhanced custom input

Custom views now close the previously named fullscreen and metadata/input-delivery
rough edges in the actual clients, while nested dialog ownership and richer
containers remain on their original audit row. Inline/press-only stays the default;
no OS-sandbox or complete extension parity claim is introduced.

- `api.custom(..., fullscreen=True, message="...", key_events=("press", "repeat",
  "release"))` declares bounded typed options. Both broker and worker enforce
  requested key kinds, reject malformed/duplicate declarations, and preserve
  snapshots. The original worker callback/future remains authoritative and executes
  once. Basic press-only packets omit new fields so a v1 server's exact event schema
  still works; current workers normalize missing press types for callback authors.
- Shared `ExtensionCustomPresentation` owns temporary alternate presentation in
  Coding AND Claw, including independent child views. Actual terminal height is
  passed to the bounded renderer. Approval/output/overlay preemption invalidates
  the view and releases its screen; fresh frames are required after restoration.
  Hidden zero-height resize fences still reach the broker while presentation is
  preempted. Exit, close/connection change and suspend/resume release/reconcile
  presentation without transferring the original dialog or inventing input.
- Sanitized message metadata opens in a separate paginated host introduction.
  F1 switches details/view; page/home/end navigate. Enter/paste/metadata navigation
  cannot invoke or complete the custom callback. View transitions change epochs,
  including same-size restoration. No network/worker I/O runs during painting.
- Explicit Kitty repeat/release opt-in reaches the actual TUI parser and callback
  with typed event metadata, not just a direct handle_input unit fake. Default
  press-only behavior remains. Cancellation/redraw/suspend/exit never reach the
  extension, and their repeats/releases cannot repeat host exit or cancellation.
- Claw negotiates `extension_custom_rich:1` separately from basic custom v1 support.
  Fullscreen, metadata and enhanced-key declarations are canceled for unsupported
  viewers, not silently downgraded. Capability withdrawal preserves original
  connection/session/child ownership and resolves canceled without model calls or
  replay. Existing parent-model/tool, stale/foreign lease and disconnect cases remain.
- Physical fullscreen resizing exposed an existing main-buffer reflow assumption:
  after shrinking, the alternate painter's negative origin could permanently clip
  the restored header. The TUI now clears/re-homes ONLY the alternate painter on
  resize. A deterministic regression proves saved main state is unchanged. Terminal
  tests now require the complete header after restoration, not merely a body line.
  Native child inspection also waits for the restored transcript's actual paint,
  rather than reading an ID during the cursor re-anchor interval.

Evidence includes typed declaration/schema and opt-in enforcement, metadata
sanitization/paging/non-admission, default-v1 compatibility, actual Kitty parsing,
presentation identity/preemption/resume and alternate-only repaint units; real
Coding/Claw parent AND child richer/default completion/cancellation with original
parent draft/caret/permissions and model counters; older/basic-only viewer and
withdrawal refusal; physical production inline AND fullscreen parent/child views,
metadata intro/tail, real repeat/release CSI sequences, independent Coding child
approval, hidden-input/paste/Enter refusal, complete resize/header restoration,
suspend/native shell/fg resume, cancellation, exact child-source selection, native
scrollback, balanced alternate transitions and termios/caret recovery.

Nested dialog ownership/richer containers, raw-key interception OUTSIDE custom
views, providers/other host APIs, remaining MCP/content/subscriptions/credential
interop and native Mac/desktop/Windows validation remain OPEN. Full goal ACTIVE;
OS-enforced sandboxing excluded. No paid model call, live login/daemon restart,
commit or unrelated reset/stash occurred.

Verification snapshot — pass 50 (October 7, 2026, final source):

- Workspace: **3,631 passed, 7 skipped** (513.80 s), including **97** private-tmux
  terminal cases: the original 89, four pass-49 inline cases, and four new richer
  fullscreen parent/child cases. Existing cases were extended, not double-counted.
- Custom option/event codecs, broker opt-in/generations, component/presentation,
  v1 compatibility/actual parser, and real Coding/Claw parent/child/legacy/capability
  group: **172 passed** independently (38.71 s), then again in the workspace suite.
- Production custom default/richer inline/fullscreen, metadata/repeat/release/
  paste/resize/full-header/independent approval/model counter/close/source/
  suspend/fg/native-scrollback/caret/termios group: **8 passed** independently
  (35.72 s), then again in the workspace suite. Both clients and parent/child paths
  use actual shipped code with offline providers, not component-only UI fakes.
- Existing renderer/lifecycle/dialog/parent/child group plus alternate-resize saved
  main-state regression: **90 passed** independently (24.41 s), then again in suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **415 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Nested dialog ownership/richer containers, raw-key interception outside custom
  views, provider/other host APIs, remaining MCP/content/subscriptions/credentials
  and native-platform rows remain OPEN. Goal ACTIVE; OS sandboxing excluded.

## Pass 51 — owned nested dialog/custom stacks without callback replay

The previous explicit nested-UI gap now has a real worker/host/both-client path,
including independent child workers. This closes bounded nested questions/custom
views, not every richer-container/raw-input/provider/other host API requirement.

- Custom input callbacks may await select/input/confirm/form/custom questions using
  a separate worker-owned frame callback ID, not overwrite their parent's suspended
  callback or borrow a concurrently serviced command. The original worker task is
  suspended exactly once and returns its frame/result once. Render callbacks cannot
  open dialogs; nested input has no command editor/session/catalog/navigation flags.
- Only an owned host renderer context establishes the parent relationship. The
  same broker, extension and live custom future are checked before linking a child;
  plugin-supplied parent IDs are stripped. Four displayed levels and the existing
  eight-question quota bound the graph. Parent cancellation/disposal cascades to
  descendants; aborts settle tasks and reap uncertain worker chains without replay.
- Parent request/completion proof is invalidated across child suspension. Hidden
  parent keys are refused. Upon child completion/cancellation, update the parent
  state and render it; only fresh parent input can commit. An old done proposal
  cannot quietly submit changed data after a nested question.
- Shared dependency-light `ExtensionDialogStack` selects owned descendants while
  preserving FIFO among unrelated roots and caches the original parent component.
  Both Coding and Claw restore that SAME state/draft/caret object after nested input,
  sequential confirmation, custom child and grandchild questions. Existing approval,
  output and overlay precedence remains; fullscreen exits/re-enters under its host
  lease, rather than creating a second terminal owner or an independent model loop.
- Claw negotiates `extension_custom_nested:1`, retains the originating connection/
  group/session/child UI lease, refuses stolen IDs/tokens and cancels unsupported or
  withdrawn child questions without adopting them as unrelated roots or canceling
  their parent. Parent/child event mapping keeps actual ownership, not client-only
  runtime state.
- Nested timeouts exposed a real serial-question race: an expired question can be
  followed by another under the same callback before the host's late reply arrives.
  Worker replies now recognize bounded retired IDs, ignore that stale answer and
  return the REAL new pending question. Foreign IDs still fail. Retired records are
  dropped when the callback finishes. Existing simple render failures preserve
  their prior cancellation contract; uncertain aborted nested chains are reaped.

Evidence: real owned workers including custom child/grandchild and timeout/next-
question cases, strict parent authority/depth/cascade/outer cancellation tests,
stack FIFO/cache/scope rejection, both actual parent AND child clients with inline/
fullscreen/sequential/custom nesting, original parent draft/caret/model counters,
foreign socket refusal, unsupported/withdrawn nested-capability no-adoption, and
physical production fullscreen parents/nested editable input/confirm/resize/fresh
return/model counts/exact child source/native-buffer/termios recovery in both clients.
Physical source inspection checks the invocation count in the actual bounded preview
and waits for the source/roster close transition before typing a parent draft, not
coalescing two Escape bytes into an unintended Alt+Escape.

Richer custom containers, raw-key interception outside custom views, providers/
other host APIs, remaining MCP/content/subscriptions/credentials and native Mac/
desktop/Windows rows remain OPEN. Full goal ACTIVE; OS sandboxing excluded. No paid
model calls, live login/daemon restart, commit or unrelated reset/stash occurred.

Verification snapshot — pass 51 (October 7, 2026, final source):

- Workspace: **3,657 passed, 7 skipped** (552.50 s), including **101** private-tmux
  terminal cases: the previous 97 plus FOUR actual nested parent/child paths.
- New worker/broker parent authority, timeout/next-question, depth/cascade/outer
  cancellation, stack FIFO/cache/scope and real both-client parent/child/custom/
  fullscreen/capability group: **22 passed** independently (23.39 s), then again
  in the final workspace suite.
- Physical production fullscreen parents/nested editable input/confirmation/
  resize/fresh return, explicit Coding child tool approval, once-only invocation/
  model-call counters, exact source lookup, source/roster close transition and
  parent draft/caret/native-buffer/termios recovery: **4 passed** independently
  (13.25 s), then again in the final workspace suite.
- Existing custom worker/dialog regression plus new worker authority/nesting group:
  **95 passed** independently (12.80 s); existing actual custom clients/shared
  component/dialog group: **91 passed** independently (34.56 s), then in suite.
- Ruff across packages: clean. Strict mypy across seven namespaces:
  **416 source files**, no errors (unused-override notes only).
- Workflow examples: **17 passed**. `git diff --check`: clean.
- Bounded owned nesting is supported on the verified Linux path. Richer custom
  containers, raw-key interception outside custom views, providers/other host APIs,
  remaining MCP/content/subscriptions/credentials and native-platform rows remain
  OPEN. Goal ACTIVE; OS sandboxing excluded.

## Pass 52 — bounded terminal-input extension foundations

Observed the already-started verification process to terminal completion instead
of restarting it. Authoritative logs: **3,690 passed, 7 skipped** (560.59 s), Ruff
clean, strict mypy **418 source files**, **17** workflow examples, and clean diff
check. These numbers describe the pass-52 source, not later pass-53 edits.

`api.on_terminal_input` supports ordered continue/consume/transform results,
idempotent unsubscribe, strict UTF-8/metadata limits and owned worker dispatch
without dialog/command/model authority. Runtime failures quarantine/reap uncertain
workers without replay. The shared queue is bounded, deadline-aware and rejects
stale IDs/owners while retaining originals. Neither client invoked these hooks at
that baseline; foundation tests alone did not establish live parity.

## Pass 53 — real Coding prompt interception and recovery

- TUI offers a host frame interceptor only after protocol filtering, with a
  once-only validated delivery path. Focus round trips and terminal handoffs have
  monotonic input leases; live physical geometry is part of ownership.
- Coding now wires serial trusted-worker callbacks through a single nonblocking
  callback worker and its UI event queue. Key frames retain original CSI/Kitty
  encoding; atomic paste never becomes raw key/protocol input.
- Host policy protects modal/approval/custom/child/output/overlay surfaces and
  configurable shortcuts. Original submits remain FIFO behind text, without being
  sent to hooks. Transformed keys cannot synthesize model submission, approval,
  exit or host handoff. Hook rejection cancels pending original submits too, rather
  than submitting an incomplete draft.
- Reserved interrupts and ownership/session/physical-resize changes cancel owned
  work immediately. Late results cannot target subsequent input. There is no raw
  fallback, callback replay, second model loop or permission grant.
- Alt+I is a configurable host-owned pause/resume/recovery shortcut. Failures and
  interruption pause hooks; `/terminal-input list|restore ID|discard ID|clear|pause|resume`
  manages bounded original records. Explicit restore inserts text into the draft,
  never raw controls/attachments/model input. Recovery preserves FIFO original
  order; refused insertion keeps the record. Oversized intercepted paste is
  refused explicitly; users can pause before using the normal larger-paste path.
- Claw does not advertise or dispatch terminal hooks yet. Its shared shortcut
  reports the missing gateway path instead of claiming support. Negotiated
  connection/group/session/child leases and real Claw interception evidence are
  still required. Broader custom containers, providers/other host APIs, remaining
  MCP/content/subscription/credential interoperability and native platforms retain
  their original OPEN scope. Full goal ACTIVE; OS-enforced sandboxing excluded.

Verification is targeted rather than another ten-minute workspace-suite repeat;
final command results are recorded below. No paid model calls, live identity
login, live-daemon restart, commit/reset/stash or unrelated change replacement.

Verification snapshot — pass 53 (October 7, 2026, final source):

- Final focused/affected shared-frame, arbitration, keymap, lifecycle/rendering,
  actual Coding worker/client, original-runtime-capture and TWO new production
  private-tmux terminal cases: **149 passed** (9.02 s). Native scrollback and
  termios restoration, atomic paste/FIFO, host pause, callback counts, once-only
  model admission and pending-hook physical resize/draft-only recovery are covered.
- Claw existing app/shortcut/custom/nested UI regression group: **33 passed**
  (35.44 s). This is regression evidence, not proof of Claw terminal interception.
- Targeted original Coding approval/resize and both-client parent/child custom
  terminal regressions, plus the two new cases: **11 passed** (40.94 s). The TWO
  new cases were rerun in the final 149-test group after lease refinements.
- The larger nonphysical TUI/Coding regression run produced **932 passed**, with
  ONE outdated `object.__new__` poll fixture missing the new interceptor dependency.
  Updated that fixture to provide a no-change poll result; the entire affected
  module and its poll/no-redundant-render test pass in the final 149-test group.
  No second broad/full-suite run is claimed for this pass.
- Ruff across packages: clean. Strict mypy across all seven namespaces: **420
  source files**, no errors (unused-override notes only). Diff check: clean.
- Startup retains explicitly trusted workers' existing lazy activation; raw hooks
  become available after activation, e.g. `/extensions`. Original runtime is
  captured on the UI thread before callback scheduling, so even a session handoff
  before thread startup cannot dispatch old text to a new extension worker.
- Full goal remains ACTIVE. Claw gateway/child terminal hooks and the original
  remaining renderer/provider/MCP/interoperability/native-platform rows are OPEN.

## Pass 54 — Claw prompt terminal interception and transport ownership

- Claw now uses the shared real frame interceptor and protected-key policy, rather
  than just advertising a worker API. Key/paste callbacks use its actual socket,
  active group/session extension runtime and UI event queue. Host-only Alt+I and
  `/terminal-input` pause/recovery work in both clients; modal/custom/child/approval/
  output/overlay ownership is preserved.
- `terminal_input:1` is explicit and strictly typed. A rotated connection-owned
  token fences capability withdrawal/regrant and foreign sockets. The original
  group/session/UI input lease, runtime generation and monotonic input sequence
  bind requests, cancellations and results. Wire UI leases are bounded hashes;
  long Unicode host identities retain their full LOCAL ownership proof.
- One in-flight job per connection and a four-second gateway deadline keep the
  reader responsive inside the five-second host deadline. Finite receipt storage
  plus a monotonic high-water sequence reject old inputs even after receipt
  eviction; no callback replay/retry or automatic raw/model fallback is introduced.
- Original extension runtime and worker selection are captured before scheduling.
  Reload/unload/failure/close changes fence Coding and Claw. Failure cleanup checks
  actual client identity, not just its name. MCP/extension disconnect now checks
  identity after close too: an old closer cannot remove a new client or its tools.
- Registration snapshots include exact boolean hook availability and bounded
  integer generation. Malformed pong/command/run snapshots disable hooks and cancel
  held input rather than retaining an active cached hook. Session/reset transitions
  clear stale hook availability. Cancellation tests wait for REAL callback entry
  markers, not just a scheduled task, which could legitimately be canceled before
  any worker invocation.
- Actual workers, live clients and owned Unix sockets cover typing/atomic paste,
  consume/transform, once-only model admission, synthetic submit refusal, draft-only
  restoration, original/foreign cancellation, capability withdrawal/regrant, worker
  reload, deadline, session handoff and reconnect, plus late-result refusal. Parent
  hooks do not inspect child forms or borrow child dialog/permission authority.
- The remaining scope is unchanged: independent child supervision-editor terminal
  hooks, broader raw/custom containers and provider/other host APIs, remaining
  MCP/content/subscription/credential interoperability and native platform rows
  remain OPEN. Full goal ACTIVE. OS-enforced sandboxing stays excluded.

Verification is targeted to the changed worker/IPC/client/terminal paths, not a
repeated full-workspace run. Final command results follow below. No paid model
calls, live logins, live-daemon restart, commit/reset/stash or unrelated worktree
replacement occurred.

Verification snapshot — pass 54 (October 7, 2026, final source):

- Final worker/owned-protocol/real Coding and Claw client group, including callback
  entry proofs, synthetic-submit rejection, cancellation, stale-worker reload,
  high-water/receipt eviction, timeout/session/reconnect, invalid snapshots and
  bounded Unicode wire ownership: **55 passed** (23.30 s).
- Earlier larger changed-runtime/stdio-manager/actual-client group plus selected
  existing and NEW terminal paths: **75 passed**, **98 deselected** (47.54 s).
  The final 55-test group rechecked the client paths after the bounded wire-owner
  refinement; retiring-client cleanup also has a dedicated successor/tool test.
- Existing app/transport/shortcut/custom/nested/input/dialog/navigation/session,
  Coding prompt and shared interception regressions with selected terminal paths:
  **164 passed**, **99 deselected** (75.99 s). Subsequent malformed-snapshot and
  long-owner refinements were exercised by the final worker/client group.
- FOUR final-source production/private-tmux cases, two Coding plus two Claw, cover
  FIFO typing/atomic paste/consume, callback/model counts, host pause, pending-hook
  physical resize and explicit draft-only recovery: **4 passed**, **101 deselected**
  (8.34 s). Native scrollback/main-buffer and termios restoration were checked.
- Ruff across packages: clean. Strict mypy across all seven namespaces: **421
  source files**, no errors (unused-override notes only). Diff check: clean.
- These are targeted pass-54 gates, not a new full-workspace suite claim. The
  original in-scope audit remains open; no OS sandbox has been introduced or
  implied by input/permission/connection ownership checks.

## Pass 55 — buffered key spans and live terminal registry

- Fixed a real parent-prompt stability issue: a buffered sentence previously
  created one RPC/queue item per character and could overflow the 32-item bound.
  Adjacent editable tokens in a filtered frame now use one bounded span. UTF-8/
  Kitty tokens are never cut; 16,000-byte limits, protected controls, FIFO original
  submission and separate atomic paste boundaries remain. Callback data/counts
  reflect framed spans, not a promised one-character unit.
- Worker envelopes report exact boolean live registration after callbacks, not
  just initial `xdog/info`. Runtime metadata updates only for the actual selected
  client; late/foreign metadata cannot rewrite a replacement. The field is removed
  before tool/custom/command content codecs, including nested renderer replies.
- Last-hook unsubscribe now updates availability immediately. Same-owner queued
  input drains once locally without replaying a callback; subsequent input is
  host-owned. Re-registration from a command updates the SAME worker without
  reload/reconnect. Coding reads its actual runtime; Claw receives strictly typed
  live availability in its matching owned terminal result. Registration mutation
  does not pretend to be worker replacement or borrow command/model authority.
- Failures still reject pending original submits; capability/session/focus/geometry/
  worker changes retain their fences. Synchronous runtime close now advances its
  lifecycle generation too, so closure cannot be mistaken for benign unsubscribe.
- Actual fixtures expand characters independently of terminal framing and record
  complete callback inputs in owned JSONL. Physical checks compare original key/
  paste streams for loss/duplication and counter agreement, not brittle hard-coded
  per-character call counts. Both clients exercise atomic unsubscribe followed by
  normal text, explicit command re-enable, fresh hooked input and once-only model
  admission/native terminal restoration.

Verification snapshot — pass 55 (October 7, 2026):

- Final worker/owned-protocol/real both-client/shared-arbitration group, including
  burst, UTF-8/Kitty boundaries, registry/command reactivation and synchronous close:
  **100 passed** (30.07 s). Oversized-token refusal and the final shared span group
  then passed independently: **31 passed** (2.47 s).
- Existing worker custom/nested/input/runtime and real both-client custom-view
  regressions, including compatibility with new envelope metadata: **134 passed**
  (49.19 s).
- FOUR production/private-tmux Coding/Claw FIFO/atomic-paste/pending-resize paths,
  now including atomic unsubscribe, unhooked text, command re-register and complete
  once-only callback stream/counter proof: **4 passed**, **101 deselected** (9.34 s).
- Ruff across packages: clean. Strict mypy across seven namespaces: **421 source
  files**, no errors (unused-override notes only). Diff check: clean. These are
  targeted gates, not another full-workspace suite claim.
- Old senders without additive live-registry metadata do not prove refresh parity.
  Independent child supervision-editor interception, broader raw/custom containers,
  provider/other host APIs, remaining MCP/content/subscription/credential interop and
  native platform validation remain OPEN. Full goal ACTIVE; OS sandboxing excluded.
  No paid models, live login/daemon restart, commit/reset/stash or unrelated reset.

## Pass 56 — independently owned child steering/follow-up terminal editors

- Both clients now select the existing child's extension runtime for editable
  steering/follow-up forms (`child-prompt`), not the parent's or a sibling's worker.
  Startup/settings/presets/confirmation and permission/custom/dialog/output views
  remain host-owned. Opening a view starts no worker or model loop.
- Child forms expose an explicit edit target and monotonic form lease. Repeated
  form entry, action/child selection, focus/physical geometry and actual runtime
  generation/attachment fence held input. Original Enter prepares the existing
  visible review; synthesized controls cannot review/execute/approve anything.
- Found/fixed real review-cancel draft loss: Escape/Ctrl+C now restores the reviewed
  child draft and advances its edit lease, rather than returning an empty form.
- Shared child extensions provide bounded metadata and captured original-runtime
  liveness under their UI-binding lock. Parent validation remains authoritative;
  callbacks still cannot borrow parent dialog/command/permission grants. Foreign,
  headless/unattached or retired workers cannot be advertised/adopted as owned.
- Claw separately negotiates `terminal_child_input:1` and returns its actual child UI
  token. Child ID/token/surface join the request/result/cancellation signature, with
  original group/session/worker/input leases and monotonic replay fences intact.
  Socket reader remains responsive. Withdrawn child support cancels only its
  captured callback and leaves the parent hook capability available.
- Owned registry notifications update activated/changed/closing child workers, even
  outside a last model-run subscription. They preserve connection/session/child
  ownership, exact boolean/integer metadata and finite catalog bounds. Completion
  retires cached child entries rather than accumulating ghost registrations.
- Same-worker unsubscribe/re-register works in child editors too. Switching siblings,
  back/reopen and physical resize cancel the original pending worker callback, keep
  bounded originals and refuse late responses; no callback/model replay is added.

Verification snapshot — pass 56 (October 7, 2026):

- Shared supervisor/child-panel/prompt/owned-protocol plus real parent AND child
  Coding/Claw group: **118 passed** (38.02 s), including separately verified
  **8** live selected-child/cancellation/foreign-peer paths, followed by child
  capability-withdrawal and review-restoration tests in the group.
- Existing Coding/Claw child agents and both clients' custom/nested parent/child UI
  regressions: **86 passed** (70.50 s). Existing app/transport/arbitration group:
  **46 passed** (1.00 s).
- SIX production/private-tmux cases: FOUR parent prompt/atomic-paste/registry/resize
  paths plus TWO NEW real child steering/edit/review/cancel/native-return paths:
  **6 passed**, **101 deselected** (13.79 s). Callback JSONL proves only one selected
  child sees complete key/paste input, with no parent/second model admission. Review
  cancellation restores the actual child draft; main-buffer/termios recovery holds.
- Ruff across packages: clean. Strict mypy across seven namespaces: **421 source
  files**, no errors (unused-override notes only). Diff check: clean. Targeted gates,
  not a new full-workspace suite claim.
- Full original goal remains ACTIVE: richer custom containers, broader raw/host and
  provider APIs, remaining MCP/content/subscription/credential interop, older-sender
  interoperability and native platform validation remain OPEN. No subset waives
  those rows or introduces OS sandboxing. No paid models, live login/daemon restart,
  commit/reset/stash or unrelated worktree reset occurred.

## Pass 57 — bounded structured custom-view composition

- Added standalone TUI worker-side immutable composition: text/spacer, fixed or
  weighted row/column slots, padding, titled boxes and adapters for existing TUI
  components. Integer largest-remainder allocation is deterministic; whole UTF-8
  graphemes and validated SGR styles are preserved/clipped without control leakage.
- A Component adapter retains extension-owned state/focus, receives its viewport
  hint, extracts ONE cursor coordinate and strips private host markers. Multiple
  visible/hidden markers and multiple focused component cursors are rejected.
  Layout objects/code never enter an application's UI process or terminal writer.
- Bounds: 256 nodes, depth 12, 32 children/container, 128 KiB text/frame, 200 rows,
  8,192 bytes/output line. Invalid dimensions, coercions, cycles, overflows and
  unsupported nodes fail, not silently render. Zero-sized viewports invoke no
  component. Existing component render-budget contracts remain explicit.
- Helpers return the existing custom `lines/cursor` wire frame. Agent acquires no
  TUI dependency; Coding/Claw workers already have TUI, and other extension hosts
  must explicitly install that optional library. Frame revision/generation,
  geometry/nesting/connection/child leases and permission safety are unchanged.
- Added a REAL editable two-column worker fixture, with Unicode styled caption,
  InputPanel component, sidebar, nested question and input-only completion. Both
  actual clients and independent child workers preserve the original draft/caret,
  nested state and resized ownership. Tests maintain the new geometry while
  awaiting completion, rather than racing another resize into the done proposal.

Verification snapshot — pass 57 (October 7, 2026):

- Initial helper/control/Unicode/fixed/flex/viewport/cycle/cursor group: **17 passed**
  (0.28 s); subsequently added total-node, hidden-marker and exact allocation tests
  were included in the final regression group.
- Four real Coding/Claw parent/child layout/nested/resize/model-counter cases plus
  the initial helper group: **21 passed**, **24 deselected** (7.81 s).
- Final layout/shared custom-frame codecs/brokers, real worker/custom/nested and
  BOTH clients' existing/new parent/child custom/nested regression group:
  **215 passed** (68.82 s).
- FOUR production/private-tmux cases, both clients in inline/fullscreen, verify
  editable Unicode component state, nested question return, resize/fresh completion,
  once-only command result, zero model calls, no unsafe OSC and exact native-buffer/
  termios recovery: **4 passed**, **107 deselected** (12.19 s).
- Ruff across packages: clean. Strict mypy across seven namespaces: **422 source
  files**, no errors (unused-override notes only). Diff check: clean. These are
  targeted gates, not a new full-workspace suite claim.
- Full goal ACTIVE. Further arbitrary render/raw/host/provider APIs, remaining
  MCP/content/subscription/credential interop, older-sender interop and native
  platforms remain OPEN. No row is waived; OS-enforced sandboxing remains excluded.
  No paid models, live login/daemon restart, commit/reset/stash or unrelated reset.

## Pass 58 — explicit MCP resource-watch lifecycle and UI notices

- Added bounded resource subscriptions, not another resource reader or model loop.
  Exact advertised subscribe capability and operator-selected URIs are required.
  SDK ownership ties original client, URI and view lease to a single handle; a
  manager monitor closes lost owners even without another server notification.
- Current-dialect stdio separates monotonic subscription IDs from ordinary RPCs;
  acknowledged filters and update/terminal frames are correlated and retired IDs
  cannot adopt a new watch. HTTP holds the original POST SSE lifetime, with no GET
  resumption or POST replay. Legacy subscribe/unsubscribe uses the existing channel
  and shared-URI local owner accounting, with last-owner remote unsubscribe.
- Subscription notices contain only URI/state/change counts, not resource contents.
  They coalesce at 20 Hz/watch and respect transport/manager/view/job/URI bounds.
  HTTP pool capacity leaves room for ordinary requests alongside long-lived watches.
- Both actual clients expose **w** in resource catalogs. Notices stay visible over
  an unchanged source preview. Explicit stop, panel/session/connection close and
  owner loss fence callback delivery and tear down the original remote lifetime.
  No resource read, composer insertion, model submission or permission grant occurs.
- Claw uses separately advertised watch support, connection-scoped view bindings,
  original group/session/MCP-manager references and bounded reader-responsive jobs.
  Closing an old view operates on its stored owner, not a newly active session.
  Unsupported/error/auth/redirect/stream conditions are visible, not implicit retry.

Verification snapshot — pass 58 (October 7, 2026):

- Final router/real stdio/owned HTTP/current/legacy/SDK/actual both-client/panel group:
  **15 passed** (2.55 s). Coverage includes shared URI accounting, exact cleanup,
  unsupported ACK, response identity/order, canceled IDs, idle owner loss, resource
  preview retention and no automatic read/model/draft admission.
- Existing stdio/HTTP/resumption/panel and both clients' structured MCP result/
  permission/persistence regression group: **55 passed** (33.44 s).
- FOUR production/private-tmux watch/update/stop paths, both clients × both
  dialects, verify one explicit subscription, no resources/read, zero model calls,
  parent editing and native-buffer/termios restoration: **4 passed**, **111
  deselected** (7.41 s).
- Ruff across packages: clean. Strict mypy across all seven namespaces: **423
  source files**, no errors (unused-override notes only). Diff check: clean.
  Targeted gates, not a new full-workspace suite claim.
- Protocol-source retrieval was attempted with the web tool and the official raw
  repository endpoint; no usable document was retrieved and the direct fetch
  failed with a connection reset. Current-dialect behavior is therefore tested
  against owned peers, NOT marked as externally conformant or a completed spec
  audit. Third-party/authorization/additional subscription capability interop
  remains explicitly OPEN.
- Full original goal ACTIVE. Remaining provider/host/render APIs, MCP content/
  credentials/interop and native platforms are not waived. OS sandboxing excluded;
  no paid models, live login/daemon restart, commit/reset/stash or unrelated reset.

## Pass 59 — reviewed private OAuth credential persistence and explicit reload

- Added opt-in SDK private credential storage and product-private Coding/group Claw
  data paths. Memory-only SDK managers remain unchanged. No startup load, auto-save,
  token POST, discovery/connect, model admission or permission grant occurs.
- Both actual UIs expose reviewed **p** save, **l** restore and **x** saved-file erase,
  with explicit plaintext/non-keychain disclosure and destination/pins/expiry. Full
  field visibility and fresh owned receipt are required; Claw separately advertises
  credential support through its existing bounded auth jobs/session/connection leases.
- Private plaintext records use hashed resource filenames, exact bounded format,
  user ownership/0700 root/0600 regular single-link files, no-follow directory-relative
  I/O, bounded reads, fsync/atomic replacement and directory-identity revalidation.
  Unsafe paths/modes/link/type/size/metadata fail without login/network fallback.
  POSIX-only protection is explicit; no unverified Windows ACL equivalence is claimed.
- Secret snapshots have no token repr/public fields. Reviews deep-copy only public
  grant metadata; unknown secret-bearing review fields are rejected on read. Restore
  binds exact resource/profile/client strategy/issuer/auth method/env source/scopes,
  CA file contents and lifetime; client-secret env VALUES are never persisted.
- Local I/O runs off the owned async loop but settles before releasing transaction
  ownership. Cancellation revokes atomic admission; actual grant mutation happens on
  the owner loop. Foreign/stale/detached or changed-grant/file receipts cannot commit.
  A restored grant clears previous flow/refresh receipts instead of reusing them.
- Saved refresh snapshots are invalidated BEFORE a reviewed potentially rotating
  token POST, not on a wrong receipt, and refreshed tokens are never auto-saved.
  File-only erase leaves live credentials intact; existing explicit forget clears
  memory/file and disconnects, without claiming remote revocation.

Verification snapshot — pass 59 (October 7, 2026):

- Final file-format/ownership/link/binding/expiry/secret/cancel/refresh-invalidation,
  public PKCE/client-auth/registration/refresh and actual both-client restart/UI/
  pagination-consent regression group: **95 passed** (8.15 s).
- Store-only final binding/bounds group before the writer-cancel test was added:
  **5 passed** (0.32 s); writer cancellation and the expanded store group were then
  included in the final 95-test gate. Real SDK save/restart/load/erase requires no
  network, and both real product clients exercise reviewed save and explicit reload.
- TWO production/private-tmux Coding/Claw authorization→separate-save→restore→erase
  paths prove no pre-review file write, actual 0700/0600 protection, no secret in
  pane/raw bytes, zero model calls, parent editing and native-buffer/termios recovery:
  **2 passed**, **115 deselected** (7.20 s).
- Ruff across packages: clean. Strict mypy across all seven namespaces: **424
  source files**, no errors (unused-override notes only). Diff check: clean.
  Targeted gates, not a new full-workspace suite claim.
- Full original goal ACTIVE. Further host/provider/render APIs, MCP content/client/
  authorization/registration/interop and native platform/keychain/ACL validation
  remain OPEN; no subset waives those rows. OS sandboxing excluded. No paid models,
  live identity login/daemon restart, commit/reset/stash or unrelated worktree reset.

## Pass 60 — session-owned extension provider callbacks and model routing

- Trusted setup can register bounded explicit model metadata and an async typed
  provider stream callback. Namespaced session aliases cannot shadow built-ins;
  no global AI/provider registry or unrelated provider/auth worktree changes.
- Both actual clients inspect/select activated aliases via `/model`. Coding refreshes
  cached limits/tool capability and persists the explicit model name; Claw negotiates
  an owned model-control capability and handles bounded async jobs without blocking
  its socket reader. Catalog inspection invokes no provider/model call.
- Complete typed context/options cross an independent checksummed upload, not a
  truncated RPC. Provider generators own opaque tokens, one active stream/worker,
  finite event/output budgets and explicit completion. Text/thinking deltas coalesce
  with continuity signatures; tool-call IDs/arguments/signatures, usage and response
  ID enter the actual existing agent loop, not another tool runner.
- Host-offered tools and existing schema/pre-hook/permission/planning gates remain
  authoritative. Providers cannot borrow UI/command authority. No explicit alias
  falls back to a built-in after failure/unload/close; canceled uncertain worker RPCs
  settle/reap rather than replay. Model output options obey declared maxima.
- Child overrides resolve cached metadata but execute in their independently created
  child worker. Existing child context/signature/vision/reasoning/tool/budget guards
  wrap that actual route; parent model/worker/count remains unchanged.

Verification snapshot — pass 60 (October 7, 2026):

- Final metadata/wire codec/real worker/checksummed >2-MiB context/signature/usage/
  cancellation/malformed-event/no-fallback and actual both-client parent/tool-approval/
  independent-child model group: **9 passed** (7.40 s).
- Existing Coding/Claw children, child model resolution, extension runtime/context,
  Coding prompt and Claw app regression group: **135 passed** (27.21 s).
- TWO production/private-tmux explicit activation→catalog→model selection→stream→
  invocation-count→parent editing paths, Coding and Claw: **2 passed**, **117
  deselected** (5.21 s). Exactly one extension provider invocation, zero built-in/
  paid calls, native-buffer/termios recovery.
- Ruff across packages: clean. Strict mypy across seven namespaces: **426 source
  files**, no errors (unused-override notes only). Diff check: clean. Targeted gates,
  not a new full-workspace suite claim.
- Full goal ACTIVE. Initial startup/persisted-alias activation, broader dynamic/native/
  provider-auth/embedding/search and host/render APIs, remaining MCP interop/content/
  credentials/client capabilities and native platforms retain OPEN scope. No subset
  waives them, creates a hosted dependency or introduces OS sandboxing. No live login/
  daemon restart, commit/reset/stash or unrelated worktree replacement occurred.

## Pass 61 — configured/saved extension alias startup and resume

- Coding SDK now honors explicit model_name precedence and recognizes namespaced
  aliases before ordinary catalog fallback. It constructs the original session's
  runtime, activates trusted metadata ONCE and applies actual limits/capabilities;
  it does not create/dispose a temporary worker and repeat setup effects.
- A synchronous SDK call inside an already running event loop uses one owned
  metadata bootstrap loop, not nested asyncio.run or a second agent loop. Saved
  session and in-session switching rebind their newly constructed worker and restore
  the alias/model info. Missing/untrusted aliases clean up owners and fail without
  rewriting a saved choice to a primary provider. CLI reports these errors cleanly.
- Claw activates configured/saved aliases before starting its gateway. Selection is
  persisted in a bounded session-owned atomic JSON model sidecar, before live model
  mutation; session identity/registration/idle guards remain. Restart restores the
  worker and cached limits without any provider/model invocation or global registry
  change. Active extension image metadata is used rather than an AI catalog guess.
- Existing extension management can still unload the selected provider: that makes
  subsequent use unavailable/no-fallback, not misreport the completed unload as a
  bootstrap failure. Model readiness validation is explicit during startup.
- Terminal harness checks distinguish actual new process readiness from alias text
  in the shell command/old scrollback. Resume exercises --resume-id on the saved
  choice; no new alias override masks whether persisted restoration works.

Verification snapshot — pass 61 (October 7, 2026):

- Configured/saved sync factory (also inside async caller), actual Claw configured/
  saved socket restart, bad-alias teardown and in-session saved activation group:
  **7 passed** (6.44 s).
- Final SDK/state/startup/provider/actual-client/child/app regression group:
  **69 passed** (30.68 s). No metadata activation calls a provider; first actual
  use invokes exactly its declared callback and never the base fixture.
- TWO production/private-tmux configured-alias startup paths, Coding/Claw, with an
  additional actual Coding saved-process resume: **2 passed**, **119 deselected**
  (6.03 s). Provider response, metadata-derived 32k window, parent editing, no base/
  paid calls and native-buffer/termios recovery are verified.
- Ruff across packages: clean. Strict mypy across all seven namespaces: **426
  source files**, no errors (unused-override notes only). Diff check: clean. Targeted
  gates, not a new full-workspace suite claim.
- Full original goal ACTIVE. Further dynamic/native/provider-auth/host/render APIs,
  MCP content/client/credential/interop and native platforms retain OPEN scope.
  OS-enforced sandboxing excluded; no paid model, live login/daemon restart,
  commit/reset/stash or unrelated worktree replacement occurred.

## Pass 62 — integration sweep and Claw model-boundary regression

- Observed the existing full-workspace run to completion; did not restart it.
  It reported **3,842 passed, 7 skipped, 4 failed, 2 errors** in **661.11 s**.
  All six unsuccessful cases were Claw orchestrator routing tests with the same
  cause: SDK callers supplied a Model object as the group fallback, and the new
  extension-alias limit check expected a string ID.
- Normalize str/Model/None once at the Orchestrator boundary, before initial or
  dynamically registered groups are constructed. Explicit extension alias IDs
  remain unchanged; downstream model/tool loops receive strings, with no hidden
  fallback or permission-policy change. Narrow the constructor type accordingly.
- Regression coverage checks metadata objects, plain IDs, explicit extension IDs
  and absent models across initial and dynamic registration. Existing response
  tests also assert that the actual stream receives a string model ID.

Verification snapshot — pass 62 (October 7, 2026):

- Focused Claw routing/tool-configuration/provider UI/startup plus Coding provider
  startup regression group: **27 passed** (12.62 s), including all six failures
  found by the integration sweep.
- Changed Python files: Ruff clean. Strict Claw mypy: **76 source files**, no
  errors (unused-override notes only). Diff check clean.
- The full sweep preceded this fix: it is NOT a clean final-source full-suite
  claim. Use focused checks for subsequent bounded fixes rather than repeating
  eleven-minute integration runs after every change.
- Goal remains ACTIVE; remaining interoperability/native-platform requirements
  above are not waived. OS-enforced sandboxing remains excluded. No paid calls,
  live daemon restart, commit/reset/stash or unrelated worktree replacement.

## Pass 63 — owned MCP watch completion and retained status

- Fix response correlation in the resource-watch router and owned HTTP stream:
  notifications retain subscription metadata, but terminal responses use their
  JSON-RPC request ID. Response metadata is optional; a duplicated subscription
  ID cannot contradict the request ID. Plain completion/error responses now
  terminate the correct watch rather than being ignored or marked interrupted.
- Validate exclusive result/error shape, typed error code/message and optional
  response metadata before changing active state. Retired IDs remain inert;
  sibling watches are unaffected and private peer errors are not surfaced.
- Reap finished owned HTTP watch tasks immediately. Completion is visible without
  closing the MCP connection, reading a resource, inserting a prompt or silently
  subscribing again. A subsequent watch requires the existing explicit UI action.
- Resource catalog rows retain per-resource state/count even when another notice
  or a catalog refresh replaces the panel's message. Both actual clients observe
  terminal responses and can explicitly restart the ended watch.
- Fix a related transcript-preview race found by these tests: a finished worker
  snapshot does not make a panel settled until its live view adopts the result.
  Loading views cannot expose selection/output or briefly claim an empty transcript.
- External conformance remains OPEN: official web retrieval returned no usable
  page and fetching the official raw schema failed with a connection reset.
  These changes prove concrete JSON-RPC correlation and ownership behavior against
  offline peers, NOT general conformance to an independently fetched MCP schema.

Verification snapshot — pass 63 (October 7, 2026):

- Final SDK stdio/HTTP watch, malformed response/no-replay/sibling isolation,
  explicit restart, MCP context/HTTP regressions, actual both-client UI and shared
  panel/transcript group: **59 passed** (9.16 s).
- SIX production/private-tmux paths, Coding/Claw × modern/legacy/server-completion:
  **6 passed**, **117 deselected** (11.20 s). Plain server completion remains
  visible during a catalog refresh; only an explicit key restarts the watch;
  no model/resource-read calls, parent editing and termios/native-buffer recovery.
- Changed Python files: Ruff clean. Strict agent/TUI mypy: **159 source files**,
  no errors (unused-override notes only). Diff check clean. No new full-suite claim.
- Goal remains ACTIVE; external/current-dialect conformance and remaining original
  requirements retain OPEN scope. OS-enforced sandboxing excluded; no paid call,
  live daemon restart, commit/reset/stash or unrelated worktree replacement.

## Pass 64 — extension provider failure/cancellation and iterator ownership

- Add bounded typed public provider error events with an explicit diagnostic,
  error/aborted stop reason and optional response ID. Prior partial content and
  usage remain in the actual AssistantMessage; exceptions/invalid JSON/cleanup
  failures still yield the generic private no-fallback failure.
- Support optional asynchronous aclose on custom async iterators, not only native
  async generators. Worker token removal makes close idempotent; the adapter closes
  its owned runtime stream/transfer and releases admission BEFORE terminal delivery.
  Healthy typed failures keep the worker/catalog available; uncertain interrupted
  RPCs continue to quarantine/reap rather than replay. Cleanup failure cannot report
  successful completion. Forced process termination cannot promise plugin cleanup ran.
- Coding retains partial terminal text/reasoning, distinguishes provider cancellation
  and suppresses duplicate MessageEnd/AgentEnd diagnostics. Claw sends owned retention/
  cancellation metadata to its TUI, retires the stream owner without erasing partial
  text and persists failed turns with their original error/stop reason/response ID/
  usage. Restored failed drafts remain editable and are never retried automatically.
- Child supervision honors provider-aborted terminal messages as canceled rather
  than failed/completed. Both clients exercise independent successful/error/aborted
  child workers, with parent model/call count untouched and retained partial results.
- Failed messages proposing a tool never enter host approval/pre-tool hooks or tool
  execution. Explicit user-selected aliases retain their original tool loop and
  permissions; no built-in provider fallback, secondary model loop or implicit retry.
- Document the public diagnostic and iterator-cleanup contract in the agent README.
  A public diagnostic is intentionally persisted/displayed and must not contain
  credentials; it is distinct from private callback exception details.

Verification snapshot — pass 64:

- Final actual worker/context/wire/private failure/exact custom close/cleanup failure/
  non-executed tool proposal, both-client parent/independent-child/provider startup,
  supervision and Coding/Claw routing/session/transport regressions: **164 passed**
  (28.81 s).
- SIX production/private-tmux paths, Coding/Claw × success/public failure/provider
  abort: **6 passed**, **121 deselected** (13.43 s). Actual partial retention,
  public diagnostics, one provider invocation, no built-in/paid calls, editable parent
  recovery and native-buffer/termios restoration. The Claw harness clears its
  intentionally restored failed prompt before typing a command; it does not replay it.
- Changed Python files: Ruff clean. Strict agent/Coding/Claw mypy: **213 source
  files**, no errors (unused-override notes only). Diff check clean. Focused gates,
  not a clean final-source full-workspace or native-platform conformance claim.
- Goal remains ACTIVE. Further dynamic/native/provider-auth/host APIs, remaining
  MCP interoperability/content/credentials and native-platform requirements above
  retain OPEN scope. OS-enforced sandboxing excluded; no live login/daemon restart,
  paid call, commit/reset/stash or unrelated worktree replacement.

## Pass 65 — live provider catalogs, route identity and current UI limits

- register_provider now returns an owned disposer and supports explicit replace=True;
  unregister_provider removes a named declaration. Duplicate implicit replacement
  remains invalid; an old disposer cannot remove a successor registration.
- Worker catalogs carry bounded monotonic revisions and per-registration identities.
  Changed metadata is emitted only when its revision changes, including pending UI
  replies. The host validates all declarations and the session-wide model limit
  before adoption. Initial-info races use the current client's latest validated
  snapshot; retired/stale/conflicting/malformed receipts cannot rewrite the catalog.
  Invalid metadata quarantines its worker even on an otherwise nonfatal custom frame.
- Streams pin their selected registration, not the entire catalog: unrelated additions
  do not cancel a valid stream. Replacement/removal refuses further output and closes
  the original iterator once, without invoking another callback or the base provider.
  Provider-only metadata changes do not enqueue unchanged terminal/UI snapshots or
  invalidate unrelated terminal-input generations.
- Both actual /model controls immediately see command-declared aliases. Coding
  refreshes selected cached limits/tool/image metadata after commands; both clients
  apply turn-start declarations before prompt preparation and compaction. Actual
  routes recheck current tools/reasoning/output/image capabilities against stale
  caller snapshots. Removal keeps the explicit model choice; subsequent use fails
  without rewriting the choice or automatically retrying/reloading another provider.
- Claw command/catalog responses now supply owned current display limits (zero for
  an unavailable alias). Its TUI fences model/session ownership, ignores malformed/
  legacy/foreign-model values and refreshes the footer without switching models.
  Context/usage precede long model/group/session labels, so changed limits remain
  visible at the normal 80-column width rather than only in wide terminals.
- Document process/session ownership: saved choices are not serialized callbacks or
  credentials; extensions redeclare needed callbacks during trusted startup. Child
  workers do not borrow mutable parent registries or silently replay parent commands.
  Broader native/provider-auth/other host APIs retain their original OPEN scope.

Verification snapshot — pass 65:

- Final real-worker dynamic add/replace/remove/current-client metadata/revision/
  per-registration identity/capacity/custom-frame quarantine/stale-capability,
  both-client command and turn-start lifecycle, static provider/startup/runtime,
  terminal hooks and Claw transport/usage regression group: **121 passed** (44.01 s).
- TWO production/private-tmux dynamic registration→catalog→selection→one stream→
  replacement→16k footer at 80 columns→removal→count→parent editing paths:
  **2 passed**, **127 deselected** (5.98 s). No restart, base/paid model invocation,
  callback replay or alternate-buffer entry; exact one invocation/one close and
  native-buffer/termios restoration. The harness waits for a visible completed
  registration snapshot, not an early JSON line already above the viewport.
- Changed Python files: Ruff clean. Strict agent/Coding/Claw mypy: **213 source
  files**, no errors (unused-override notes only). Diff check clean. Focused gates,
  not a new full-workspace/native-platform or external MCP conformance claim.
- Goal remains ACTIVE. Remaining native/provider-auth/embedding/search/host/render,
  MCP interoperability/content/credentials and native-platform requirements above
  are not waived. OS-enforced sandboxing excluded; no live login/daemon restart,
  paid call, commit/reset/stash or unrelated worktree replacement.

## Pass 66 — explicit Windows clipboard adapter and bitmap transport

- Add a win32 branch to the shared composer reader, using one owned current-Python
  helper and direct User32/Kernel32 clipboard APIs. No shell/PowerShell, polling,
  clipboard history, file-drop/HTML/OLE object reads or automatic model submission.
  Microsoft primary clipboard/bitmap documentation was fetched for API signatures,
  clipboard-owned handles, synthesized bitmap formats, DIB layout and RLE grammar.
- Native ABI bindings use pointer-sized handles/GlobalSize, bounded lock/copy/unlock
  and clipboard close in failure paths. Clipboard sequence checks cover capture and
  conversion; the clipboard is released BEFORE potentially expensive conversion.
- Prefer encoded PNG/JPEG/WebP, then DIBV5/DIB, then terminated aligned UTF-16LE text.
  Do not retry a failed image as text. Canonical image framing excludes native
  allocation padding; malformed helper output/text/limits never echo clipboard data.
- Add a dependency-free bounded packed-DIB codec: CORE/INFO/V4/V5, 1/4/8-bit palettes,
  16/24/32-bit RGB/bitfields/declared alpha, top-down/bottom-up/padding, RLE4/RLE8
  encoded/absolute/delta runs, embedded PNG/JPEG and inline ICC retention. Separate
  native raster/pixel bounds from encoded-image bounds, so ordinary uncompressed
  bitmap input is not incorrectly limited to a 10-MiB PNG's byte count.
- Existing composer epochs, cancellation, preview, provider capability gates,
  typed image submission and draft recovery remain authoritative in both clients.
  Owned helper cancellation/oversize/failure is reaped without late image insertion.
- Native Windows desktop/ABI/terminal execution remains UNVERIFIED. Linked profile
  reads are deliberately refused, and nontrivial calibrated color conversion remains
  OPEN rather than silently discarding its color metadata. Other desktop/Windows
  client transport, native credential and platform requirements are not waived.

Verification snapshot — pass 66:

- DIB raster/alpha/palette/bitfield/orientation/RLE/profile/bounds/PNG checksum,
  actual Python worker/native ABI contract/current-helper/cancellation/privacy,
  Mac/Linux clipboard regressions and Coding/Claw composer/upload/permission group:
  **141 passed** (4.90 s).
- TWO production/private-tmux Windows-helper-protocol paths, Coding/Claw, on Linux:
  **2 passed**, **129 deselected** (4.27 s). Explicit paste→two typed attachments→
  preview→tiny resize→one image turn→parent draft/termios/native-buffer recovery.
  These are actual client/owned helper transport tests, NOT Windows desktop evidence.
- Changed Python files: Ruff clean. Strict TUI mypy: **86 source files**, no errors
  (unused-override notes only). Diff check clean. No full-workspace/platform parity
  claim, live login/daemon restart, paid call, commit/reset/stash or unrelated
  worktree replacement. OS-enforced sandboxing remains excluded; goal ACTIVE.

## Pass 67 — Windows reviewed Unicode clipboard copy

- Wire win32 clipboard copy into the existing shared TransferWorker/output flows,
  ahead of Unix helper discovery. Use one owned current-Python helper with reviewed
  UTF-8 text on stdin; no shell, secret argv, polling, history reads or model call.
- Publish exact UTF-16LE CF_UNICODETEXT using an invisible message-only owner HWND.
  Prepare all movable-memory allocations and privacy-format IDs before EmptyClipboard;
  transferred handles become OS-owned and are never freed by the helper. Free only
  untransferred allocations; close the clipboard and destroy the owner on failures.
- Publish monitor/history/cloud exclusion markers before text. The OS/other-app
  exposure warning remains: these requested markers are NOT a confidentiality or
  native-desktop guarantee. No delayed rendering or automatic clipboard listeners.
- Explicitly refuse NUL/truncating text and copies over 32 MiB before launching any
  native operation; direct canceled calls now also stop before launching helpers.
  Cancellation/failure after an attempt reports an uncertain outcome and reaps the
  helper, without replay or automatic file fallback. Existing unavailable-helper
  fallback, permission/review/session/context ownership and export/share stay intact.
- Both actual clients exercise their existing response copy, export and reviewed
  local-share UI using the Windows helper protocol, with exact captured source and
  retained parent drafts. Native Windows desktop/HWND execution, privacy-flag behavior
  and overall Windows client/platform validation remain OPEN, not waived.

Verification snapshot — pass 67:

- Native ABI/pointer/memory/HWND/privacy ordering/pre-commit and partial-commit cleanup,
  real writer main/UTF-8→UTF-16/no-secret-output, actual owned-helper copy/cancel/no-replay,
  shared transfer/review and Coding/Claw full-source/output/read-clipboard regressions:
  **61 passed** (3.05 s).
- TWO production/private-tmux Windows-copy-protocol paths, Coding/Claw on Linux:
  **2 passed**, **131 deselected** (4.78 s). Exact response copy→local JSON export→
  explicit share review/tiny resize→parent draft/native-buffer/termios recovery.
  These are real-client/owned transport tests, NOT native Windows desktop evidence.
- Changed Python files: Ruff clean. Strict TUI mypy: **88 source files**, no errors
  (unused-override notes only). Diff check clean. No full-workspace/platform parity
  claim, live login/daemon restart, paid call, commit/reset/stash or unrelated
  worktree replacement. OS-enforced sandboxing remains excluded; goal ACTIVE.

## Pass 68 — independently verified MCP resource-watch interoperability

- Install the independently published MCP TypeScript serving SDK/server/core 2.3.1
  into an isolated owned /tmp directory, with lifecycle scripts/audit/funding disabled.
  No workspace manifest/lockfile or production dependency is changed. The fixture
  verifies the pinned package version and imports real serveStdio/createMcpHandler
  and public schemas rather than wrapping XDOG's own protocol as a peer.
- Verify real independent discovery, resource catalogs, explicit dual watches,
  notices, owner-specific unwatch, no resource reads/tool grants and metadata-bearing
  graceful teardown over BOTH stdio and stateless HTTP/SSE. Raw test control requests
  supply the required HTTP name mirrors; production manager calls already supply them.
- Generate additional acknowledgement/result cases through the SDK's public validators.
  Accept explicitly false optional list-change filters without opting into them;
  unrequested true, integer/null or unknown filters still fail closed. Recognize
  schema-empty/meta-only completion payloads by the owned RPC ID. Bare-body tolerance
  is compatibility behavior, NOT a broad invalid-server conformance claim.
- A terminal response before acknowledgement now refuses ONLY that watch and reports
  the reason without disconnecting a valid sibling, losing ordinary catalogs,
  automatically resubscribing or reading/submitting resource content.
- Both real client UI/gateway paths and both production/private-tmux clients connect
  to the independent SDK peer. Document explicit opt-in setup; missing dependency
  skips are not evidence. Other current-MCP content/capability/schema/OAuth/native
  platform requirements retain their original OPEN scope.

Verification snapshot — pass 68:

- Final real serving-SDK stdio/HTTP/lifecycle and public-schema corpus, defensive
  router/current/legacy transport/MCP context, both-client SDK/custom peers and
  shared panel regression group: **65 passed** (12.82 s), SDK root explicitly set.
- TWO production/private-tmux independent SDK watch→real notification→explicit unwatch→
  parent editing/native-buffer/termios paths: **2 passed**, **133 deselected** (5.67 s),
  SDK root explicitly set. No resource read, model turn, tool grant or base/paid call.
- Published pinned SDK/core 2.3.1 were loaded from npm's Apache-2.0 packages in /tmp;
  their own serving implementations and validators supplied the wire behavior.
  The test fixture is authored locally; SDK implementation is not copied/vendored.
- Changed Python files: Ruff clean. Strict agent mypy: **76 source files**, no errors
  (unused-override notes only). Diff check clean. Focused interoperability evidence,
  not full-workspace/all-MCP or native-desktop parity. Goal ACTIVE; OS-enforced
  sandboxing excluded. No live login/daemon restart, commit/reset/stash or unrelated
  worktree replacement.

## Pass 69 — independently verified MCP context/completion and resource-link prompts

- Extend the pinned independent SDK 2.3.1 peer with real prompt/URI-template
  completion and explicit resource/template/prompt callbacks. Verify over BOTH
  stdio and HTTP that catalogs and suggestions do not retrieve content, while
  separate requested reads/prompts use the existing transport/name mirrors.
- The independent peer exposed a genuine missing content type: standards-valid
  resource_link prompt messages were rejected. Accept bounded inert link metadata
  without pretending it is fetched resource content; preserve the ENTIRE JSON block,
  including annotations/vendor fields, for source preview/search/export and explicit
  editable-draft insertion. Do not dereference its URI or promote it to instructions.
- Validate URI/name/title/description/MIME/size and finite bounded JSON; malformed,
  nonfinite, oversized or system-role content still fails closed. Existing binary
  insertion refusals and permission/session ownership remain unchanged. Tool discovery
  is not a grant: the independent peer's proxies remain unapproved.
- Both actual terminal clients now perform independent SDK named completion →
  argument draft → distinct prompt retrieval → inert link preview/full-source search →
  explicit unsubmitted composer insertion. The 80-column preview's compact JSON may
  be horizontally clipped; source search finds metadata outside that viewport without
  changing, truncating or fetching the data. No widened-terminal-only proof is used.

Verification snapshot — pass 69:

- Final independent SDK subscriptions/schema/context/link/prompt/URI-template
  completion, defensive link metadata, current/legacy context/completion and actual
  Coding browser/Claw gateway context group: **85 passed** (17.04 s), SDK root set.
- TWO production/private-tmux independent SDK watch PLUS prompt completion/link-preview/
  full-source search/unsubmitted insertion/native parent recovery paths:
  **2 passed**, **133 deselected** (6.38 s), SDK root set. Explicit one prompt retrieval,
  no linked/resource/template read, model/base/paid call, tool grant or automatic
  composer submission; native-buffer/termios recovery remains verified.
- Changed Python files: Ruff clean. Strict agent mypy: **76 source files**, no errors
  (unused-override notes only). Diff check clean. Focused wire/application evidence,
  not full-workspace/all-MCP or native-desktop parity. Broader MCP/schema/OAuth/client
  capabilities and original native/provider/host/render/platform requirements remain
  OPEN. Goal ACTIVE; OS-enforced sandboxing excluded. No live login/daemon restart,
  commit/reset/stash, production dependency change or unrelated worktree replacement.

## Pass 70 — lossless rich MCP/extension tool execution failures

- AgentToolResult gains a backward-defaulted explicit boolean is_error field.
  Sequential/parallel execution preserves returned typed failure content/details;
  finalization hooks receive failure status and end events/ToolResultMessage flags
  remain aligned. Cancellation/exception/invalid flags remain error outcomes.
- MCP isError no longer raises a text-only exception that discards images and
  structured diagnostics. Validate every content block and finite JSON, retain the
  canonical structured data in visible source/details and mark the result failed.
  A deliberate error need not match the tool's advertised SUCCESS output schema;
  success output validation and invalid-result failures remain unchanged.
- Extension api.result(..., is_error=True) and typed results cross both validated
  worker/host boundaries without flattening. Existing Coding/Claw failed/denied
  post-hook guards remain; command artifacts reject execution-error flags rather
  than treating them as successful callback/navigation receipts.
- Both clients retain marked rich failures in live source and persisted transcripts;
  images reach the next typed context rather than becoming a success or disappearing.
  Denied calls still never reach the peer. No auto retry, additional model/tool loop,
  hidden image/file read, permission grant or policy rewrite is introduced.
- Independent serving SDK 2.3.1 stdio/HTTP tools return standards-valid text/image/
  structured errors that do not match their success schema, proving the real wire
  path rather than only an authored XDOG peer. Direct SDK proxy callers now inspect
  result.is_error; protocol/transport validation errors continue to raise.

Verification snapshot — pass 70:

- Final typed failure/sequential/parallel/hooks/flag validation, MCP independent SDK/
  custom current/legacy transport/schema, extension worker/command artifact, both-client
  permission/error/persistence and Flow external-agent regression group:
  **137 passed** (40.37 s), independent SDK root explicitly set.
- FOUR production/private-tmux rich execution-failure paths, Coding/Claw × MCP/extension:
  **4 passed**, **135 deselected** (9.54 s). Actual approval/one execution, text/image/
  structured error inspection, no post-hook laundering, typed subsequent context,
  retained parent editing and native-buffer/termios recovery. No base/paid model call.
- Changed Python files: Ruff clean. Strict agent/Coding/Claw mypy: **213 source files**,
  no errors (unused-override notes only). Diff check clean. Targeted source/application
  evidence, not a clean full-workspace/all-MCP/native-platform parity claim. Remaining
  native/provider/auth/host/render/content/capability/platform requirements stay OPEN.
  Goal ACTIVE; OS-enforced sandboxing excluded. No live login/daemon restart,
  commit/reset/stash, production dependency change or unrelated worktree replacement.

## Pass 71 — full extension provider prompt/effort/vision metadata

- Accept validated optional max_prompt_tokens, supported_efforts and vision_limits;
  project them into actual frozen Model metadata through startup, live catalogs,
  selected-model refresh and independently owned child factories. Model IDs now
  support normal native dots/slashes/colons (bounded, control/space-free) rather
  than requiring command-like short IDs. They remain opaque namespaced identities,
  never paths/URLs to follow or global AI registry mutations.
- Independent prompt ceilings cannot exceed total context limits. Explicit efforts,
  image count/size/MIME and JSON types are validated atomically before declarations
  enter the catalog. Existing callers omitting these fields retain prior semantics.
- Actual provider routes reject unsupported effort/image contexts before invoking a
  callback, even for stale callers. Output limits remain clamped to the current model.
  Coding rejects incompatible explicit preference changes without options/settings
  mutation; configured unsupported startup preferences fail and settle owners.
- Both clients use actual selected metadata; Claw now updates its prompt-limit cache
  and compaction uses the stricter advertised prompt ceiling, not a larger total
  context window. Existing child model resolution checks the same capabilities and
  preserves independent worker ownership, parent options/model/count and permissions.

Verification snapshot — pass 71:

- Final declaration/native-ID/prompt/vision/effort/actual-worker/stale-input admission,
  both-client configured/catalog/independent-child, dynamic/static provider/startup,
  SDK state and Claw session/compaction regression group: **111 passed** (33.93 s).
- TWO production/private-tmux native compound-ID catalog→selection→effective context/
  prompt footer→supported Coding reasoning→one actual callback→count→parent editing
  paths: **2 passed**, **139 deselected** (5.47 s). Exact one provider invocation,
  no base/paid call or permission change; native-buffer/termios recovery verified.
- Changed Python files: Ruff clean. Strict agent/Coding/Claw mypy: **213 source files**,
  no errors (unused-override notes only). Diff check clean. Targeted source/application
  evidence, not a clean full-workspace/all-provider/native-platform parity claim.
  Broader native/provider-auth/host/render/MCP/capability/platform requirements stay
  OPEN. Goal ACTIVE; OS-enforced sandboxing excluded. No live login/daemon restart,
  commit/reset/stash, production dependency change or unrelated worktree replacement.

## Pass 72 — Claw parent reasoning inspection/settings parity

- Add discoverable /thinking inspect/change controls to the actual Claw TUI, with
  explicit reasoning_controls negotiation and an owned bounded reader-responsive
  gateway job. Legacy/malformed/unnegotiated/foreign/stale/busy controls do not become
  conversation input or an implicit setting change. One model/settings request is
  outstanding per UI; response metadata is group/session/model/correlation fenced.
- Inspect cached metadata on the existing stream adapter (including session-owned
  extension models), validate non-off requested efforts, exclude active parent/child
  mutations and retain tool/planning/approval restrictions. No provider activation,
  authentication, model request or automatic turn submission. Status displays reasoning.
- Atomically persist bounded session/model-bound thinking preferences with pre-commit
  owner/cancellation admission; resume only on the same model. Changes to another
  model do not borrow the previous model's persisted preference. Native/extension
  startup validates restored metadata rather than silently changing providers.
- Owned disk work settles under cancellation. A revoked pre-commit operation does not
  write/apply later; a lost post-commit acknowledgement keeps live/persisted state
  aligned on the same owner without replay. Setting queries are read-only; callers
  inspect after uncertain outcomes. Model IDs now preserve the full bounded native
  namespace length in Claw model persistence/control rather than a 128-char cutoff.

Verification snapshot — pass 72:

- Final actual socket/TUI inspect/mutate/reject/no-turn/restart restore, negotiated/
  foreign/busy/legacy/stale/malformed controls, pre-/post-commit cancellation/atomic
  ownership and existing provider/startup/metadata/dynamic/child/settings/session/
  transport/navigation/routing regression group: **75 passed** (24.06 s).
- TWO production/private-tmux compound-model/provider paths, with the new Claw
  /thinking inspect→high→status→explicit provider turn and Coding equivalent:
  **2 passed**, **139 deselected** (5.70 s). No setting operation invokes a model;
  subsequent explicit turn calls the selected provider exactly once, preserves
  parent editing and restores native-buffer/termios.
- Changed Python files: Ruff clean. Strict Claw mypy: **76 source files**, no errors
  (unused-override notes only). Diff check clean. Targeted source/application evidence,
  not a clean full-workspace/all-provider/native-platform parity claim. Remaining
  native/provider-auth/host/render/MCP/capability/platform requirements stay OPEN.
  Goal ACTIVE; OS-enforced sandboxing excluded. No live login/daemon restart,
  commit/reset/stash, production dependency change or unrelated worktree replacement.

## Pass 73 — Claw configured native model catalogs/selection and scoped defaults

- Add optional cached CatalogStreamFn enumeration on the SAME actual adapter;
  the provider helper lists active cached metadata and extension adapters compose
  independently owned declarations. No global AI registry mutation, ambient runtime,
  sync/login/provider activation or secondary model/tool loop is introduced.
- Claw /model now selects configured native models as well as extension aliases,
  behind negotiated native_models support and existing session/group/idle/child gates.
  Resolve/persist canonical IDs, apply actual context/prompt/tool/reasoning metadata,
  reject missing/non-chat/nonstreaming models and preserve no-fallback behavior.
- Persist generic session model choices with atomic owner/cancellation admission and
  compatibility accessor names. Startup validates saved native choices on the actual
  adapter before readiness. Cancellation settles owned disk work; an admitted lost
  acknowledgement keeps live/persisted selections aligned without replay.
- Fix an actual cross-session leak: new conversations restore the configured model/
  limits instead of retaining the prior conversation's mutable selection. Matching
  saved session choices/preferences still restore. Existing extension saved-alias
  resume checks now assert that the selection actually succeeded before restart.
- Fix first-control bootstrap ownership: an initially empty session owner captures
  the newly explicitly created session ID before persistence admission. Existing
  nonempty owner leases remain strict; an empty request cannot adopt another session.
- Public catalog summaries omit native Model headers/base URLs/protocol internals,
  preventing credentials/endpoint leakage. Bound cached catalogs and native reserved
  namespaces; legacy clients fail unsupported native controls visibly, never as input.

Verification snapshot — pass 73:

- Final configured cached catalog/canonical selection/public secret exclusion,
  saved native unavailable/restart/new-session scope, pre-/post-commit cancellation,
  actual stream/limits/tool/reasoning, legacy controls and existing provider/startup/
  extension/child/session/navigation/transport/routing/tool-config regression group:
  **128 passed** (28.75 s).
- ONE production/private-tmux native catalog→canonical short-name selection→64k
  footer→parent thinking high→explicit one native callback→parent editing recovery:
  **1 passed**, **141 deselected** (2.36 s). No metadata operation calls a model,
  activates providers or leaks an Authorization header; native-buffer/termios restored.
- Changed Python files: Ruff clean. Strict agent/Claw mypy: **152 source files**,
  no errors (unused-override notes only). Diff check clean. Targeted source/application
  evidence, not full-workspace/all-provider/native-platform parity. Remaining native/
  provider-auth/host/render/MCP/capability/platform requirements stay OPEN. Goal ACTIVE;
  OS-enforced sandboxing excluded. No live login/daemon restart, commit/reset/stash,
  production dependency change or unrelated worktree replacement.

## Pass 74 — integrated verification sweep complete

- Revalidate the shared-runtime/client changes together rather than relying only on
  package-level checks. Started and observed ONE root `uv run pytest -q` integration
  sweep, tool session **76648**, to authoritative exit **0**; no restart occurred.
  Final current-runtime baseline: **4,085 passed, 18 skipped** in **738.89 s**
  (12 min 18 s), `/tmp/pass74-workspace.log`. No integration failures required edits.
- Repository-wide Ruff: clean (`/tmp/pass74-ruff.log`). Strict mypy across all seven
  namespaces: **431 source files**, no errors (unused-override notes only;
  `/tmp/pass74-types.log`). Diff check: clean.
- Run the shipped workflow example test command after verifying its test runner
  stubs every agent node and cannot fall back to providers/CLI: **17 passed**
  (`/tmp/pass74-examples.log`). No production service restart.
- Run the independent serving-SDK opt-in group explicitly with the owned pinned SDK
  root: **13 passed** (9.68 s), not default-environment skips
  (`/tmp/pass74-independent-sdk.log`). The baseline full suite leaves explicitly
  optional/native/dependency checks as skips; those never prove missing requirements.
- The temporary in-progress observation was superseded by the completed run above.
  Current runtime-source integration is green, but that does NOT establish full
  Codex/Pi/API/native-platform parity. Original native/provider-auth/host/render/MCP/
  platform requirements remain OPEN and the goal stays ACTIVE. OS-enforced sandboxing
  excluded; no commit/reset/stash, dependency/lockfile replacement or unrelated
  worktree overwrite. Future bounded changes use targeted gates until another
  justified integration checkpoint; do not repeat this sweep after every small edit.

## Pass 75 — calibrated Windows DIB color profiles without a runtime color dependency

- Replace the explicit nontrivial-calibration refusal with bounded self-contained
  calibrated RGB→ICC matrix/TRC representation for V4/V5 DIBs. Parse signed 2.30 XYZ
  endpoints and unsigned 16.16 per-channel gamma, normalize the reference white and
  represent D50 PCS/chromatic adaptation and channel curves rather than assuming sRGB.
- Preserve original RGBA pixel values/declared alpha. Inline PNG iCCP carries the
  calibration; no external profile/file/URL/native color-library read is introduced.
  Invalid/singular/non-adaptable data fails, rather than discarding or guessing it.
  Gamma above ICC signed-parametric range uses a bounded curve including every output
  8-bit sample instead of clipping the Windows unsigned value.
- Profiles are deterministic, bounded below the existing 1-MiB profile budget and
  share the encoded-image size limit. Existing row/palette/RLE/dimension/alpha/input
  constraints remain. Capture metadata/user/timestamps are not added; clipboard is
  released before conversion and changed revisions still invalidate the result.
- Validate with the REAL independent system LittleCMS engine: open generated profiles,
  separately construct reference RGB profiles from the parsed source primaries/white/
  gamma, compare multiple color transforms to ≤1 output byte (including unequal
  channel gamma). This library exists here; the tests executed, not skipped. It is
  not a new runtime dependency. Native Windows desktop/ABI/terminal checks stay OPEN.

Verification snapshot — pass 75:

- Final calibrated ICC/raster/alpha/large gamma/invalid calibration/independent color
  transforms/clipboard release+revision and existing DIB/Windows/Mac/composer/upload/
  provider image constraints: **151 passed** (5.15 s); LittleCMS comparisons executed.
- TWO production/private-tmux calibrated-RGB/alpha/profile Windows-helper-protocol
  paths in actual Coding/Claw on Linux: **2 passed**, **140 deselected** (4.34 s).
  Exact typed attachments/profile-preserving submission/preview/tiny resize/parent
  recovery/native-buffer/termios, NOT native Windows desktop evidence.
- Changed Python files: Ruff clean. Strict TUI mypy: **89 source files**, no errors
  (unused-override notes only). Diff check clean. Targeted source/application/color
  evidence, not a new full-workspace/native-platform parity claim. Original native/
  provider-auth/host/render/MCP/capability/platform requirements remain OPEN. Goal
  ACTIVE; OS-enforced sandboxing excluded. No live login/daemon restart, paid call,
  commit/reset/stash, production dependency change or unrelated worktree replacement.

## Pass 76 — explicit HS256 client-secret JWT authentication

- Add pre-registered `client_secret_jwt` with an explicit environment-backed secret
  (minimum 32 bytes); reject inline secrets, reserved caller assertion fields and
  implicit registration/method/algorithm fallback. Preflight before discovery/browser
  work; require issuer advertisement of this method and HS256 signing support.
- Generate a fresh 60-second assertion for each code exchange/explicit refresh:
  client ID issuer/subject, exact reviewed token-endpoint audience, random JTI and
  HMAC-SHA256 signature. Never post the secret itself or retain assertions on public
  reviews/status or saved credential snapshots. Existing exact token-target pinning,
  resource/scope/PKCE/lease and default tool-permission gates remain authoritative.
- Both clients use the shared consent panel, which visibly pages method, HS256,
  lifetime, audience and environment-source name before confirmation. Extend the
  owned offline peer to independently verify signature/claims on real UI exchanges.
- Test endpoint refusal, redirects/uncertain refresh outcomes and consumed receipts:
  no assertion/refresh-token replay. Saved credentials round-trip with method/source
  binding, and changed profiles cannot restore them. Fix the Claw physical fixture's
  short secret so it executes JWT exchange rather than only setup rejection.

Verification snapshot — pass 76:

- Targeted OAuth discovery/authentication/registration/refresh/store/MCP and both
  client/panel regression group: **131 passed** (9.30 s). Additional panel paging/
  algorithm/audience/source/confirmation regression: **5 passed** (0.18 s), including
  four already covered panel cases. Initial combined JWT/client UI check: **21 passed**.
- TWO actual Coding/Claw production/private-tmux JWT consent→callback→exchange paths:
  **2 passed**, **142 deselected** (7.09 s), no paid model/live identity login or
  user-daemon restart. Offline interoperability, not live-issuer/native-platform proof.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **302 source
  files**, no errors (unused-override notes only). Diff check clean. Logs:
  `/tmp/pass76-regressions.log`, `/tmp/pass76-terminal.log`, `/tmp/pass76-types.log`.
- Goal remains ACTIVE; private-key JWT/mTLS, encrypted/native credential stores,
  broader provider/host/render/MCP and native platform requirements remain OPEN.
  OS-enforced sandboxing excluded. No new runtime dependency, commit/reset/stash,
  lockfile replacement or unrelated worktree overwrite. Use bounded targeted gates,
  not another full-workspace sweep after this small change.

## Pass 77 — composable custom-view scroll canvases

- Add frozen `CustomViewport` to the existing worker-side custom-layout API. Explicit
  virtual dimensions and display-cell/row offsets compose with text/components,
  boxes, fixed/weighted rows/columns, padding and nested viewports. The worker retains
  scroll/input state; only ordinary cropped frame data crosses the existing broker.
- Clamp offsets on resize, translate the one visible cursor and hide it offscreen.
  Preserve SGR and whole graphemes even when styles occur inside combining/ZWJ
  sequences; partial edge glyphs become their overlapping blank cells, never broken
  glyphs or terminal controls. Zero-size views never invoke component renderers.
- Bound virtual dimensions (8192 columns/4096 rows), each canvas (128 Ki cells),
  aggregate canvas nodes (512 Ki cells), virtual-frame/component retention (512 KiB
  each), line bytes and existing node/depth/text/host-frame limits. Allow components
  to render the reviewed virtual height without relaxing the host's 200-row frame.
  This is a bounded canvas API, not unlimited lazy document paging.
- Extend the real worker fixture with a 320-row two-axis scroll view. Both clients'
  actual parent AND child workers exercise scroll keys, unchanged editor draft/cursor,
  nested-question recovery, physical viewport revisions, retained scroll position,
  reset and original-owner completion. No provider/model turn is used by parent UI
  commands; child paths retain the existing runtime/tool/permission loop.

Verification snapshot — pass 77:

- Final custom layout/panel/stack/broker/nested-runtime and both live client group:
  **220 passed** (62.09 s), `/tmp/pass77-final.log`. Additional final zero-size
  component regression included in **32 passed** (0.46 s); no component invocation
  on a hidden viewport. Tests cover 300-row offsets, resize clamps, styled Unicode,
  cursor visibility and invalid/excessive/aggregate/invisible retention budgets.
- EIGHT actual production/private-tmux Coding/Claw structured views (static/scroll ×
  inline/fullscreen): **8 passed**, **140 deselected** (29.89 s),
  `/tmp/pass77-terminal-final.log`. Scroll→horizontal pan→nested dialog→resize→scroll
  back/reset→submit→parent editing and terminal restoration execute with no paid
  model/live identity login or user-daemon restart. Earlier marker failures identified
  an overlong test label clipped by the deliberately narrow sidebar; shorten the
  fixture label rather than weakening cropping or accepting a partial row marker.
- Changed Python files: Ruff clean. Strict TUI mypy: **89 source files**, no errors
  (unused-override notes only), `/tmp/pass77-types.log`. Diff check clean. No full
  workspace rerun after this bounded pass; pass 74 remains the earlier baseline.
- Goal ACTIVE, not full Codex/Pi/render API or cross-platform parity. Further arbitrary
  rendering/host/provider/MCP/native-platform requirements remain OPEN. OS-enforced
  sandboxing excluded. No new runtime dependency, commit/reset/stash, lockfile
  replacement or unrelated worktree overwrite. Previous goal turn: concrete progress.

## Pass 78 — positioned host overlays and verified resize/focus recovery

- Wire the TUI's existing positioned overlay capability into `api.custom(overlay=...)`
  and both live clients, rather than simulating a popup inside an inline frame.
  Validate data-only absolute/whole-percent sizes/positions, nine anchors, signed
  offsets and copied margins; reject unsafe/unknown callbacks, focus-policy flags,
  invalid ranges/types and simultaneous fullscreen+overlay declarations.
- Capturing overlays allocate the actual host viewport through the existing
  sanitized lines/cursor protocol. Keep the owning worker, panel, draft/state and
  permission policy; only the owned overlay handle is acquired/released. Host
  approval/output/foreign overlays still preempt it. Nested questions release it
  and restore the same parent with fresh viewport evidence, never stale replay.
- Add independently negotiated `extension_custom_overlay:1` in Claw in addition to
  rich/custom support. Gate publication/frames/requests and original parent/child
  connection/group/session leases. Unsupported rich-only viewers and capability
  withdrawal cancel the question instead of silently downgrading it to inline.
- Real terminal acceptance exposed a resize defect: async overlay frames temporarily
  lack a cursor, so CPR-based reflow could anchor the whole modal above the visible
  screen. Explicit screen-relative overlay composition now anchors only the visible
  tail; the actual CPR still drives moves and historical rows are never replayed.
  No whole-screen/scrollback erase or alternate-screen mode is introduced.
- The same terminal paths exposed a Claw focus defect after nested overlay closure:
  an old nested input could retain base focus when no inline detail panel remained.
  Explicit restoration now selects the live parent/host editor before handle release.
  Both clients' application tests assert actual restored focus, not only draft text.
- Document capturing placement, lifecycle/limits and negotiated compatibility in
  agent/TUI READMEs. Noncapturing overlays, live worker visibility/focus controls,
  other render/host/provider/MCP and native-platform requirements remain OPEN.

Verification snapshot — pass 78:

- Final custom protocol/broker/nested/runtime, TUI compositor/renderer/lifecycle,
  both clients' actual parent+child overlays and nested-owner regression group:
  **291 passed** (101.75 s), `/tmp/pass78-final.log`. Includes invalid placement,
  copied margins, exclusive modes, viewport epochs, original handle-only preemption,
  resize no-cursor/long-history preservation, capability denial/withdrawal and actual
  parent focus recovery. Initial protocol/panel group: **171 passed**; isolated
  renderer/compositor group: **103 passed** (overlapping evidence, not additive).
- TWELVE actual production/private-tmux Coding/Claw structured views (static/scroll ×
  inline/fullscreen/overlay): **12 passed**, **140 deselected** (43.48 s),
  `/tmp/pass78-terminal-final.log`. Actual scroll/pan→nested edit→shrink/grow→reset→
  completion→restored editable parent and terminal modes. Four focused overlay paths
  additionally pass after both discovered defects were corrected (14.89 s). No paid
  model/live identity login or user-daemon restart; not native desktop platform proof.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **302 source
  files**, no errors (unused-override notes only), `/tmp/pass78-types.log`. Diff check
  clean. No full workspace rerun; pass 74 is the earlier integrated baseline.
- Previous goal turn: concrete progress. Goal ACTIVE; every original open scope remains
  tracked. OS-enforced sandboxing excluded. No new runtime dependency, commit/reset/
  stash, lockfile replacement or unrelated worktree overwrite.

## Pass 79 — shared MCP content validation and lossless annotation projection

- Replace raw unvalidated tool audio/embedded-resource/link JSON with shared bounded
  content validation also used by prompt/resource previews. Preserve opaque finite
  metadata while checking base64 input/decoded bounds, nonempty/control-safe URIs,
  exactly-one text/blob resource payload, MIME field bounds, annotation audiences,
  numeric priority and offset-aware timestamp forms. Invalid data fails explicitly
  after one call, without retry, URI retrieval or silent coercion.
- Retain text/image annotations and vendor fields as indexed
  `mcp_content_metadata` details and visible `[MCP content metadata]` output. Image
  payload bytes are NOT duplicated into details. Both actual clients exercise this
  through the normal tool loop, effective permissions, source viewers and persisted
  sessions. Valid metadata does not impersonate message roles or grant tool access.
- Preserve inner AND outer prompt resource metadata and audio/image annotations in
  the existing document text fields. Binary summaries still show MIME/byte count
  alongside metadata and keep the original data for explicit export. No new Claw
  document fields/capability downgrade is introduced.
- Keep transport validation separate from native model image admission: actual tool
  ImageContent projection retains PNG/JPEG/WebP validation; inert binary prompt
  previews still retain other encoded image formats for inspection/export, and cannot
  silently draft them as text. A final compatibility test prevents a shared-validator
  refactor from regressing the previously supported inert GIF preview path.
- Add independent serving-SDK mixed-content tool and public ContentBlockSchema corpus
  (text/audio/embedded text/blob/link, annotations/opaque metadata and invalid members).
  Real owned stdio/HTTP calls preserve all markers and record no automatic resource,
  template or prompt read. Pinned optional SDK remains outside production dependencies.
- Document boundaries: retained audio/blobs/links are inert data, not audio playback,
  provider-native audio support or general binary artifact API parity. Those original
  content/provider/platform requirements remain OPEN rather than being waived.

Verification snapshot — pass 79:

- Content/context/links/structured/stdin/HTTP/tool errors, both clients' real policy/
  persistence/source and catalog/preview/gateway/IPC regression group: **139 passed**
  (36.09 s), `/tmp/pass79-final.log`. Final inert-image compatibility guard plus
  current content/context/link and both live client group: **77 passed** (13.13 s),
  `/tmp/pass79-compatibility.log`; overlapping evidence, not additive totals. Earlier
  two fixture assertions wrongly assumed every result part was text; fix them to
  check typed images and exclude image bytes from metadata, not flatten the result.
- Explicit independent SDK opt-in: **10 passed** (7.50 s),
  `/tmp/pass79-independent-sdk.log`, including schema corpus and BOTH real serving
  transports. These executed rather than skipped; no dependency download/live login.
- TWO actual production/private-tmux Coding/Claw rich-content tool paths: **2 passed**,
  **152 deselected** (5.11 s), `/tmp/pass79-terminal.log`. Permission/once-only actual
  call, image/native tool content, complete structured output, resize-safe details
  navigation and restored editable parent/terminal modes. No paid model call,
  external content retrieval or user-daemon restart; not native desktop proof.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **303 source
  files**, no errors (unused-override notes only), `/tmp/pass79-types.log`. Diff check
  clean. No full workspace rerun; pass 74 remains the earlier integrated baseline.
- Previous goal turn: concrete progress. Goal ACTIVE; further rendering/host/provider/
  MCP/native-platform requirements remain tracked. OS-enforced sandboxing excluded.
  No new runtime dependency, commit/reset/stash, lockfile replacement or unrelated
  worktree overwrite.

## Pass 80 — owned command model/context inspection without provider work

- Add `api.get_model()` and `api.get_context_usage()` to the existing validated
  command host-request path. The same original command/session ownership and idle
  gates apply. Tools/hooks/context/child/headless paths remain unavailable rather
  than borrowing command/browsing authority; extending read-only inspection to their
  independently owned contexts remains an explicit wider-host-API requirement.
- Project actual selected-model public identity, effective context/prompt ceilings,
  output limit, thinking and reasoning/tool/input/effort/vision capabilities. Unknown
  capabilities remain None; stale metadata for another model is not used. Return
  fresh finite bounded JSON, not mutable Model/provider records. Credentials,
  headers/base URLs/endpoints/protocol internals and raw history are never included.
- Context snapshots report the last matching assistant response's input/output and
  cache-read/write counters, effective prompt ceiling and percentage, with explicit
  `last-response` source and message age. Missing or another model's usage produces
  `unavailable`/None fields. Never present a character estimate, later history or
  changed prompt prefix as measured current usage; do not clip values above 100%.
- Coding reads its captured actual idle Agent session. Claw's gateway resolves against
  the original active broker/runtime under the existing reserved command group;
  deliberately corrupted/stale client footer/model/usage values cannot substitute
  for the owning runtime. Read-only calls neither invoke model streams nor change
  model/options, draft, session, planning or tool-permission policy.
- Negotiate `extension_session_inspection:1` separately from basic session support.
  Legacy session-capable viewers (including omitted/zero/boolean flags) receive
  unavailable results, not unknown dialogs or fabricated measurements. Explicitly
  supported reads can settle without asking the viewer to invent runtime data.
- Extend the real navigator worker with an inspection command and branch-owned
  persistence of snapshots; existing illegal tool fixture asserts both APIs stay
  unavailable. Document return schemas, real measurement limitations and remaining
  scope in the agent README.

Verification snapshot — pass 80:

- Broad inspection/session/catalog/navigation/native-model/reasoning/same-adapter
  and both real client group: **109 passed** (40.93 s), `/tmp/pass80-final.log`.
  Final vision-field/privacy/range/unavailable/usage-age/scope and both actual client/
  old-viewer group: **39 passed** (14.28 s), `/tmp/pass80-inspection-final.log`;
  overlapping evidence, not additive totals. Actual persisted metadata and restored
  parent focus are checked. Typed invalid replies never become capability grants.
- TWO actual production/private-tmux Coding/Claw inspection→native session catalog→
  browser/tree→parent edit paths: **2 passed**, **152 deselected** (6.70 s),
  `/tmp/pass80-terminal.log`. Actual read-only command, unavailable numeric reporting,
  default native scrollback and terminal/focus recovery; no model/provider/login
  call or user-daemon restart. A Claw test marker wrapped between a JSON key and
  value; assert the exact null value across displayed whitespace rather than
  flattening/changing the result or accepting a fake zero measurement.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **304 source
  files**, no errors (unused-override notes only), `/tmp/pass80-types.log`. Diff check
  clean. No full workspace sweep; pass 74 remains the earlier integrated baseline.
- Previous goal turn: concrete progress. Goal ACTIVE; model/auth/other host/render/
  MCP/content/native-platform requirements remain OPEN. OS-enforced sandboxing
  excluded. No paid model call, new production dependency, commit/reset/stash,
  lockfile replacement or unrelated worktree overwrite.

## Pass 81 — callback/headless/child inspection and literal-search focus stability

- Extend the read-only inspection APIs beyond the command-only slice. Every actual
  parent runtime projects an independently validated invocation envelope into its
  existing worker RPC context. Generic SDK runtimes may supply an explicit producer;
  absent/invalid/closed producers yield unavailable, never fabricated state.
- Protect session/child/inspection fields against caller-context overrides. Worker
  ContextVars capture a per-invocation immutable observation; getters return fresh
  copies without dialogs, provider/model calls or session/editor/navigation authority.
  Interactive commands retain their original negotiated fresh host bridge, including
  older-viewer denial rather than silently switching to callback snapshots.
- Bind the actual independent child Agent before initialization/session/turn hooks.
  Preserve mandatory parent validation, selected model/options/history, same tool loop,
  inherited restrictions and independently loaded workers. Use the child's current
  provider metadata, not the parent's alias/producer. Closing/changing the original
  parent invalidates inspection rather than adopting a different session.
- Exclude inherited parent response measurements from child counters with an explicit
  ownership boundary; fresh children report unavailable until their OWN response.
  Inherited durable entries remain truthful historical records with original provenance,
  not re-labeled as new child observations. Actual client tests distinguish them.
- Real terminal paths caught a UI defect: the child viewer's plain `l` live-mode hotkey
  swallowed an `l` while editing a literal transcript search. Add a public query-editing
  state and defer the hotkey to the search editor while active. Live toggling still
  works outside query editing; typing `last-response` remains exact.
- Test the actual user interaction: default Coding parent/child approval, explicit
  queued-child live refresh, source search/horizontal navigation, exact visibly
  measured counters, shrink/grow and settled Escape→parent focus recovery. Do not
  bypass approvals, fake zero measurements, or coalesce a bare Escape with a draft
  into an unintended legacy Alt shortcut merely to make the fixture pass.

Verification snapshot — pass 81:

- Broad owned inspection/worker/context/session/provider and both client child/runtime/
  native routing regression group: **146 passed** (70.42 s), `/tmp/pass81-final.log`.
  Final both-client UI AND headless parent/independently-selected-child, explicit
  allow/deny, retained history/counter ownership/persistence/focus group: **16 passed**
  (22.65 s), `/tmp/pass81-clients-final.log`; overlapping evidence, not additive totals.
  Owned producer/callback scope/copy/model-change/spoofing/measurement-boundary group:
  **28 passed** (1.23 s), `/tmp/pass81-unit.log`. Literal search/live-key regression and
  existing child panel group: **15 passed** (0.35 s), `/tmp/pass81-search.log`.
- FOUR actual production/private-tmux Coding/Claw parent/independent-child inspection
  tool→source viewer→search/counts→resize→parent editing paths: **4 passed**,
  **154 deselected** (13.04 s), `/tmp/pass81-terminal-final.log`. Two legitimate offline
  model callbacks per turn, no extra inspection model/provider/auth calls. Existing
  native-scrollback/termios restoration assertions execute. No live service/user-daemon
  restart or native desktop parity claim. A queued static child view required explicit
  live refresh; wrapped JSON/counts are verified across visible cells, not partial labels.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **304 source
  files**, no errors (unused-override notes only), `/tmp/pass81-types.log`. Diff check
  clean. No full workspace sweep; pass 74 remains the earlier integrated baseline.
- Previous goal turn: concrete progress. Goal ACTIVE; broader host/model/auth/render/
  MCP/content/native-platform requirements remain OPEN. OS-enforced sandboxing excluded.
  No paid model call, production dependency/lockfile change, commit/reset/stash or
  unrelated worktree overwrite.

## Pass 82 — typed empty binary content and actual export-path editing

- Preserve explicit text/image/audio/embedded-text/blob/link content types in frozen
  MCP document blocks. Binary identity is no longer inferred solely from nonempty
  bytes. Zero-byte binary data stays visibly binary (0 bytes), retains metadata and
  cannot silently draft as text; genuine empty text remains supported. Export keeps
  the exact empty data string and type rather than discarding the block.
- Extend the existing Claw checksummed/session-fenced document transport without
  silently sending unknown fields to older viewers. Negotiate `mcp_content_types:1`
  with exact integer checks; legacy shape remains available for safe ordinary/nonempty
  content. Empty binary downgrade is explicitly refused after one reviewed read, with
  no automatic fetch replay. Format changes during read/spool/chunks fence commitment.
- Cover the opposite compatibility direction too: an upgraded viewer receiving an
  untyped older-gateway document cannot infer its type from MIME or metadata and
  insert it. Preview/private export remain available with a visible update requirement.
- Real terminal export tests exposed two existing UI bugs. Output destination editing
  accepted text with no focused hardware cursor; propagate host focus to the nested
  editor across blur/edit/review. Enter then read a cleared InputPanel draft instead
  of the submitted argument; retain the actual submitted path and require a new
  visible review before private save. Do not weaken confirmation/resize/context or
  no-overwrite policy to compensate for the fixture failure.
- Validate independent public serving-SDK content schemas including empty audio/image/
  embedded blob cases as inert wire data, separately from actual model image admission.
  Native raster decoding/playback/provider audio and broader content/platform APIs
  remain their original OPEN requirements, not waived by typed transport.

Verification snapshot — pass 82:

- Content/context/link, both client live previews/draft refusal/export, gateway/IPC,
  share/export/artifact and persistence regression group: **110 passed** (8.96 s),
  `/tmp/pass82-final.log`. Final both-direction old-viewer/capability/format-fencing
  and output-focus/path-submission group: **28 passed** (4.56 s),
  `/tmp/pass82-compat.log`; overlapping evidence, not additive totals. Core initial
  typed empty binary/legacy refusal/withdrawal group: **50 passed** (2.02 s).
- Independent pinned serving-SDK opt-in: **10 passed** (7.56 s), `/tmp/pass82-sdk.log`;
  public content schema cases actually execute, not optional-dependency skips.
- TWO actual production/private-tmux Coding/Claw empty binary preview→draft refusal→
  typed PRIVATE JSON export→fresh path review→0600 exact saved artifact→parent editing
  paths: **2 passed**, **158 deselected** (5.29 s), `/tmp/pass82-terminal.log`. One
  explicit resource read, no tool/model/provider/auth call, upload or user-daemon
  restart; native main-buffer/termios recovery executes. Target-path submit waits for
  the actual cursor/painted path before requesting review; no coalesced resize bypass.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **304 source
  files**, no errors (unused-override notes only), `/tmp/pass82-types.log`. Diff check
  clean. No full workspace sweep; pass 74 remains the earlier integrated baseline.
- Previous goal turn: concrete progress. Goal ACTIVE; every original broader host/
  provider/auth/render/MCP/native-platform requirement remains tracked. OS-enforced
  sandboxing excluded. No paid model call, production dependency/lockfile change,
  commit/reset/stash or unrelated worktree overwrite.

## Pass 83 — paged output review for long destinations and small viewports

- Replace the unconfirmable resize-only output layout with paged review when the
  metadata/warnings/hash/full destination cannot fit. PgUp/PgDn/Home/End navigate
  actual grapheme-safe rows; every metadata/path row must be visibly painted under
  active focus before save. Do not clip the long target into a misleading receipt.
- Keep normal roomy combined preview unchanged. `v` opens the immutable captured
  payload separately in paged layouts; Enter cannot save there. Returning to a roomy
  geometry exits that mode rather than trapping confirmation behind a hidden toggle.
- Revoke proof on resize, path editing and focus loss; hidden renders cannot count as
  operator review. Enforce minimum 24-column/four-row geometry, reject repeated Tab/
  Enter as approval and retain explicit fresh review after destination submission.
  All no-overwrite/private write/context-fencing/share-redaction policies remain.
- Standalone review tests now establish real active focus, not unseen rendering as
  approval authority. Model/provider/tool permissions and native main-buffer/termios
  ownership are unchanged. A long path's editor may show its cursor-local tail, but
  review pages show EVERY source path cell before confirming the exact destination.

Verification snapshot — pass 83:

- Output/export/share/artifact, both client UI/preview/ownership regression group:
  **41 passed** (8.80 s), `/tmp/pass83-final.log`. Final focused paginated Unicode
  path, payload mode, metadata completeness, resize/blur/reset, repeat-key and
  existing output/artifact/UI group: **20 passed** (1.25 s), `/tmp/pass83-unit.log`;
  overlapping evidence, not additive totals.
- FOUR actual production/private-tmux Coding/Claw wide AND paged empty-binary private
  export paths: **4 passed**, **158 deselected** (11.12 s),
  `/tmp/pass83-terminal-final.log`. Paged cases review a long Unicode destination at
  36 columns/eight terminal rows, cannot save on the first page or in payload mode,
  commit while still narrow to the exact reviewed filename with 0600 mode and intact
  typed empty data, then restore parent editing. Success notices may scroll into
  native history; the owned artifact and preserved history verify actual commitment,
  not a partial label. No model/live service/daemon restart or upload.
- Changed Python files: Ruff clean. Strict TUI mypy: **89 source files**, no errors
  (unused-override notes only), `/tmp/pass83-types.log`. Diff check clean.
- Integration checkpoint started after the recent shared-runtime passes: ONE root
  `uv run pytest -q`, live tool session **15183**, `/tmp/pass83-workspace.log`.
  It is IN PROGRESS, not a green baseline or a completion claim. Observe this same
  handle/log to terminal exit; never restart solely because an observation expires.
  Pass 74 remains the earlier completed workspace baseline until this finishes.
- Previous goal turn: concrete progress. Goal ACTIVE; original native/provider/auth/
  host/render/MCP requirements remain tracked. OS-enforced sandboxing excluded. No
  production dependency/lockfile change, commit/reset/stash or unrelated overwrite.

## Pass 84 — integrated checkpoint gates, same full-suite owner still active

- Previous goal turn: concrete paged-review implementation and verified terminal
  progress. Continue the ONE root pytest sweep from pass 83, tool session **15183**,
  to its eventual authoritative exit; no duplicate/restart occurs. Current tool/OS
  process observations confirm the original pytest process is live and progressing
  through the suite. `/tmp/pass83-workspace.log` is still IN PROGRESS, not a new
  green workspace baseline or proof of completion.
- Complete repository-wide Ruff: clean, `/tmp/pass84-ruff.log`. Strict mypy across
  all seven namespaces: **434 source files**, no errors (unused-override notes only),
  `/tmp/pass84-types.log`. Diff check clean. No source edits during this sweep.
- Inspect the current flow testing runner/executor: every agent backend is dispatched
  through CaseStubs/StubRunner, with missing stubs failing rather than invoking a
  provider/CLI. Shipped examples: **17 passed**, `/tmp/pass84-examples.log`.
- Run the independent pinned serving-SDK opt-in plus both real resource-watch UI
  groups explicitly: **16 passed** (11.25 s), `/tmp/pass84-sdk.log`. Those dependency
  checks actually execute, rather than claiming default skips are conformance.
- TWO additional production/private-tmux SDK-backed watch→completion/prompt metadata→
  unsubmitted draft/parent terminal-recovery paths: **2 passed**, **160 deselected**
  (5.91 s), `/tmp/pass84-sdk-terminal.log`. Independent owned SDK peer, no paid model,
  live identity service, provider activation, dependency download or daemon restart.
- Goal ACTIVE. These completed gates strengthen integration evidence but do not
  waive any original native/provider/auth/host/render/MCP/platform requirement or
  establish full Codex/Pi parity. OS-enforced sandboxing excluded. Preserve the live
  sweep handle and all unrelated changes; no commit/reset/stash/lockfile replacement.

## Pass 85 — integrated verification checkpoint complete

- Observe the SAME root pytest sweep from pass 83, tool session **15183**, to
  authoritative exit **0**; no restart/duplicate sweep occurred. Current integrated
  source/runtime baseline: **4,294 passed, 21 skipped** in **875.86 s** (14 min 35 s),
  `/tmp/pass83-workspace.log`. No integration failures required source changes.
- This supersedes the temporary in-progress observations in passes 83/84. Those
  observations were verified waits on the actual live process, not a lock-file guess
  or a reason to start another run. Root execution covered the recent shared TUI,
  worker/callback/child/session/model/content/typed-export/paging changes together.
- Completed companion gates remain repository-wide Ruff clean,
  `/tmp/pass84-ruff.log`; strict all-seven-namespace mypy **434 source files**,
  no errors (unused-override notes only), `/tmp/pass84-types.log`; diff check clean.
  No source edits occurred during this integrated sweep.
- Fully stubbed shipped workflow examples: **17 passed**,
  `/tmp/pass84-examples.log`. Independent pinned serving-SDK opt-in plus both real
  watch UI groups: **16 passed** (11.25 s), `/tmp/pass84-sdk.log`. TWO production/
  private-tmux independent SDK-backed watch/completion/prompt/draft/terminal recovery
  paths: **2 passed**, **160 deselected** (5.91 s), `/tmp/pass84-sdk-terminal.log`.
  All run against owned offline peers/private servers; no paid model/live identity
  service/provider activation/dependency download or user-daemon restart.
- Default-environment optional/native checks account for the **21 skips** and do NOT
  prove those requirements. Separate SDK opt-in evidence above is explicit execution;
  no skip is promoted to interoperability or native-platform conformance.
- Current integration is green, not full goal completion or Codex/Pi/API/native
  platform parity. Original provider/auth/host/render/MCP/content/credential and native
  desktop/platform requirements remain OPEN. Goal ACTIVE; OS-enforced sandboxing
  excluded. No commit/reset/stash, production dependency/lockfile replacement or
  unrelated worktree overwrite. Future bounded changes return to targeted gates
  until another justified integration milestone, not this sweep after every edit.

## Pass 86 — composable worker-owned in-view layers

- Add frozen `CustomLayer` and `CustomStack` to the existing custom-layout API.
  Extensions compose positioned opaque rectangles over their own background without
  acquiring another host overlay, callback or policy/focus handle. Explicit cell/row
  coordinates, optional sizes and viewport clipping remain worker-owned.
- Blank each entire footprint; preserve SGR and whole combining/ZWJ glyphs, including
  styles embedded inside a grapheme. Translate a foreground cursor, hide an occluded
  background cursor and reject multiple still-visible focused cursors. Invisible/
  zero-size layers never invoke component renderers. Existing node/depth/cycle/text/
  frame/virtual-canvas bounds and 32-layer capacity remain authoritative.
- Exercise a real worker popup over its draft: defocus/cover the editor, retain text,
  ignore popup paste, close and restore the same editor/cursor, then nested question
  and physical resize recovery. Actual parent AND independent child paths use both
  clients' existing workers/brokers/permission/transport/screen ownership, not a new
  UI-only state model or model loop.
- Physical inline tests initially matched an old same-width popup after shrink/grow
  before forwarding F5. Make the owned fixture expose monotonic rendered-frame identity
  and wait for the actual new viewport frame. Do not weaken current-generation/input
  admission or make stale replay acceptable to pass a screenshot assertion. Improve
  hidden-marker failure diagnostics to include actual visible terminal contents.

Verification snapshot — pass 86:

- Final custom layout/panel/stack/broker/nested runtime and both live parent/child
  client regression group: **263 passed** (87.24 s), `/tmp/pass86-final.log`.
  Layer/style/grapheme/footprint/cursor/zero-size/offscreen/resize/conflict/invalid
  rectangle/cycle/bounds group: **42 passed** (0.48 s), `/tmp/pass86-unit.log`;
  overlapping evidence, not additive totals. Both actual application structured-layer
  groups: **16 passed**, **26 deselected** (42.01 s), `/tmp/pass86-ui.log`.
- TWELVE actual production/private-tmux Coding/Claw structured views (static/scroll ×
  inline/fullscreen/host-overlay), each exercising in-view popup open/resize/close,
  retained editor/cursor, nested question and original parent/terminal recovery:
  **12 passed**, **150 deselected** (52.13 s), `/tmp/pass86-terminal-final.log`.
  No extra model/provider/auth call, live service, user-daemon restart, terminal
  escape injection or alternate-screen command from extension code.
- Changed Python files: Ruff clean. Strict TUI mypy: **89 source files**, no errors
  (unused-override notes only), `/tmp/pass86-types.log`. Diff check clean. Targeted
  verification after the completed pass 85 integration baseline, not another full
  workspace/native-platform or arbitrary-rendering parity claim.
- Previous goal turn: concrete completed integration evidence. Goal ACTIVE; original
  broader render/host/provider/auth/MCP/credential/native-platform requirements remain
  OPEN. OS-enforced sandboxing excluded. No production dependency/lockfile change,
  commit/reset/stash or unrelated worktree overwrite.

## Pass 87 — reviewed typed multi-selection forms and real Space input

- Add `multi_choice` form fields with 1–32 offered typed scalar choices, explicit
  array answers, min/max item bounds, required/default validation and exact type-
  sensitive uniqueness. False/zero/null are distinct; optional omission differs
  from empty []. Reject unknown/duplicate/out-of-range/coerced values at both
  worker and host boundaries without silently dropping a selected item.
- Use focused Up/Down/Space editing, explicit Delete-empty/Backspace-unset and the
  existing whole-form review. UI arrays use declaration order; valid SDK arrays
  retain their exact order. Paste/repeat/hidden/unpainted review never submits.
  Focus loss now invalidates form proof; underlay renders cannot mark unseen rows.
- Independently negotiate `extension_multi_forms:1` in Claw. Basic form support
  does not imply multi-selection; unsupported/boolean/withdrawn capabilities cancel
  original parent/child questions, never flatten data or borrow another view's lease.
- Actual terminal tests found a parser mismatch: printable Space arrives as `" "`,
  while descriptor matching expected `"space"`. Normalize the descriptor comparison
  only, preserving actual editor characters and strict ctrl/alt/shift matching.
  This fixes physical boolean/single-choice toggles as well as new multi-selection.
- Exercise the real worker and both clients' parent/independent-child submission,
  cancellation, current ownership, exact typed arrays and restored parent focus.
  A stale test-created Claw session was corrected to observe the runtime actually
  created by the original command/start rather than inventing UI-side ownership.

Verification snapshot — pass 87:

- Final typed form/protocol/worker, keys/keybindings, parent/child actual clients,
  legacy-capability/withdrawal and nested UI regression group: **97 passed**
  (40.57 s), `/tmp/pass87-final.log`. Both actual client submission/cancel groups:
  **8 passed** (11.28 s), `/tmp/pass87-ui-final.log`; overlapping evidence, not
  additive totals. Final actual Space/scalar/multi-field proof group: **21 passed**
  (0.48 s), `/tmp/pass87-keys-final.log`.
- FOUR actual production/private-tmux Coding/Claw scalar AND multi-selection form
  review/resize/default/type/submission/cancellation→editable parent recovery paths:
  **4 passed**, **160 deselected** (10.87 s), `/tmp/pass87-terminal-final.log`.
  Whole metadata review executes with informative typed-selection constraints; no
  paid model/provider/login call or user-daemon restart, automatic tool approval,
  session mutation, paste confirmation or native terminal mode loss.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **304 source
  files**, no errors (unused-override notes only), `/tmp/pass87-types.log`. Diff check
  clean. Targeted gates after pass 85 baseline, not another full-workspace/platform
  or arbitrary-form/render/provider/auth parity claim.
- Previous goal turn: concrete source/terminal progress. Goal ACTIVE; original broader
  UI/capability/native-platform requirements remain OPEN. OS-enforced sandboxing
  excluded. No production dependency/lockfile change, commit/reset/stash or unrelated
  worktree overwrite.

## Pass 88 — owned hook disposal and coherent live declarations

- Add idempotent per-registration disposers for lifecycle, pre/post-tool, context and
  submitted-input hooks. Ignore-return callers remain compatible. Bound callable
  registrations to 32 per kind/event; stale disposers never remove later registrations
  of the same callable. Revision advancement is bounded and atomic with changes.
- Snapshot admitted callback lists in actual worker dispatch. A self-disposing hook
  cannot skip the next listener, and a newly added listener does not unpredictably
  run in the current invocation. Preserve the same serial runtime hooks/tool loop.
- Carry bounded monotonic hook declarations through the existing worker protocol,
  including pending/returned callback paths. Adopt only the current worker's metadata;
  ignore stale receipts, reject conflicts, update event/context/tool/input host caches
  without restart and retain bootstrap/reload compatibility with original info fields.
- Explicit current-owner input disposal removes that owned required listener. Failed,
  unloaded/closed or retired workers cannot clear mandatory input validation or update
  a replacement's registry. No permission/session/editor/model authority is added.
- Real current-client tests wait for the original user turn to settle before issuing
  another lifecycle command; a worker call/cursor paint alone is not idle ownership.
  Do not bypass that existing gate or replay a command after an uncertain callback.

Verification snapshot — pass 88:

- Lifecycle/disposal/worker/context/input/dynamic-provider/nested and both actual UI
  regression group: **63 passed** (24.77 s), `/tmp/pass88-final.log`. Direct owned
  callback/list/field/identity/metadata/fail-closed group initially **13 passed**
  (0.85 s); current-owner conflict and retired-worker reload checks are included in
  the final group. Both actual client enable→user transform→dispose→ordinary input→
  re-enable/replacement/retired-disposer paths: **2 passed** (3.90 s),
  `/tmp/pass88-ui.log`; overlapping evidence, not additive totals. Worker process
  identity remains unchanged and only explicitly submitted turns call the offline model.
- TWO production/private-tmux Coding/Claw hook enable→canonical input→dispose→ordinary
  input→counter inspection→resize→editable native parent recovery paths: **2 passed**,
  **164 deselected** (6.57 s), `/tmp/pass88-terminal-final.log`. Explicit idle/final
  response proof, no metadata model/provider/login call or user-daemon restart.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **305 source
  files**, no errors (unused-override notes only), `/tmp/pass88-types.log`. Diff check
  clean. Targeted gates after the completed pass 85 integration baseline; no new full
  workspace/native-platform or all-extension-API parity claim.
- Previous goal turn: concrete source/terminal progress. Goal ACTIVE; original broader
  native/provider/auth/host/render/MCP/credential requirements remain OPEN. OS-enforced
  sandboxing excluded. No production dependency/lockfile change, commit/reset/stash or
  unrelated worktree overwrite.

## Pass 89 — explicit UI contribution removal and admitted worker ownership

- Add status/widget None removal and named clear methods. Preserve existing setters
  and explicit empty widget lists; copy input lines rather than retain caller-owned
  mutable lists. Removal frees the existing bounded namespace slot and is idempotent.
- Validate and prepare each UI batch before commit/publication. A bad later row cannot
  partially remove prior UI. Clear only the original extension's matching kind/key;
  status clear cannot remove a widget that replaced the shared legacy key slot.
- Fence every normal/pending/nested/custom UI result to the actual current worker.
  Buffer setup contributions until source/settings/registration admission succeeds,
  then publish them; do not lose startup UI merely because the connecting process
  has not yet entered the host's active-client map. Retired, failed/unloaded/closed
  workers cannot re-arm or delete a replacement's UI. No snapshot/capability downgrade
  is sent to old viewers: the existing complete UI dictionary simply lacks cleared rows.
- Both live clients render show→clear→repeat-clear→show/collision under the same
  session and worker, retain parent draft/focus and do not invoke models or change
  permission/planning policy. No second UI-only runtime or implicit tool dispatch.

Verification snapshot — pass 89:

- UI removal/worker admission/lifecycle/owned hook/custom and both application
  regression group: **144 passed** (21.66 s), `/tmp/pass89-final.log`. Core bootstrap/
  collision/identity/reload/closed-state/atomic-malformed-batch/copy/empty-widget cases:
  **8 passed** (1.28 s), `/tmp/pass89-unit.log`; final combined worker/runtime group
  **13 passed** (5.09 s), `/tmp/pass89-unit-final.log`. Both live clients:
  **2 passed** (2.14 s), `/tmp/pass89-ui.log`; overlapping evidence, not additive totals.
- TWO actual production/private-tmux Coding/Claw show status/widget→explicit clear→
  visible removal→resize→editable parent/native-buffer/termios recovery paths:
  **2 passed**, **166 deselected** (4.99 s), `/tmp/pass89-terminal.log`. No model/
  provider/auth call, live service, daemon restart, permissions change or upload.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **305 source
  files**, no errors (unused-override notes only), `/tmp/pass89-types.log`. Diff check
  clean. Targeted gates after pass 85 baseline, not another full-workspace/platform
  or all-extension/UI API parity claim.
- Previous goal turn: concrete source/terminal progress. Goal ACTIVE; original broader
  host/render/provider/auth/MCP/credential/native-platform requirements remain OPEN.
  OS-enforced sandboxing excluded. No production dependency/lockfile change, commit/
  reset/stash or unrelated worktree overwrite.

## Pass 90 — live command/shortcut replacement, disposal and actual catalog refresh

- Return idempotent owned command/shortcut disposers and add explicit same-name
  command replacement. Validate callables, descriptions, exact identifiers and
  bounded counts before mutation; reject ambiguous case-only aliases. Old disposers
  never remove later command registrations. Remove dangling bindings only when their
  actual command disappears, preserving independent replacement ownership.
- Report bounded monotonic data-only catalogs from the actual worker and adopt them
  only under the original current client. Bootstrap/reload/legacy info fields remain
  compatible; stale or retired receipts cannot re-arm removed aliases and conflicting
  same-revision metadata fails. Keep aggregate host command/shortcut limits; reload
  capacity no longer double-counts the original namespace being replaced.
- Publish the existing complete host snapshot so BOTH clients refresh autocomplete
  and prompt shortcut dispatch without another UI-only command registry or reconnect.
  An already captured callback keeps its original execution; removal governs future
  admission. A disposed command is visibly unknown, not a request to a missing worker
  callback that would quarantine the healthy extension. Failed/unloaded commands are
  not dispatched through stale cached metadata.
- Preserve reserved host controls, conflict/remapping/contextual key priority,
  command/session/worker/child ownership, permissions and no replay. Catalog changes
  do not start models, activate providers, change the parent draft or acquire approval.

Verification snapshot — pass 90:

- Command declaration/lifecycle/shortcut/runtime/hook and both live command/binding
  client regression group: **52 passed** (12.01 s), `/tmp/pass90-final.log`. Owned
  stale-disposer/case/capacity/metadata/refusal/reload/callback replacement group:
  **7 passed** (1.26 s), `/tmp/pass90-unit.log`. Both actual clients' autocomplete
  visibility, F6 callback replacement/disposal/re-enable/original-worker and parent
  focus group: **2 passed** (2.33 s), `/tmp/pass90-ui.log`; overlapping evidence,
  not additive totals. No metadata operation invokes a model.
- TWO production/private-tmux Coding/Claw dynamic enable→F6 original callback→explicit
  replacement→F6 new callback→disposal→F6 no-op→resize/editable native parent recovery
  paths: **2 passed**, **168 deselected** (5.47 s), `/tmp/pass90-terminal.log`.
  No worker restart, paid model/provider/login call, daemon restart, tool approval,
  source/view namespace substitution or native terminal mode loss.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **306 source
  files**, no errors (unused-override notes only), `/tmp/pass90-types.log`. Diff check
  clean. Targeted gates after pass 85 integration, not another full-workspace/native-
  platform or all-runtime/API parity claim.
- Previous goal turn: concrete source/terminal progress. Goal ACTIVE; original broader
  render/host/provider/auth/MCP/credential/native-platform requirements remain OPEN.
  OS-enforced sandboxing excluded. No production dependency/lockfile change, commit/
  reset/stash or unrelated worktree overwrite.

## Pass 91 — owned live tools, schema identity and same-loop inventory

- Add tool disposers and explicit replacement with new per-registration identity.
  Validate callable/name/capacity/schema before atomic mutation; stale disposers do
  not remove future replacements. Carry bounded monotonic identity-only metadata,
  rediscover actual schemas on the same owned MCP manager and preserve generic MCP
  behavior with optional adapter provenance unset outside trusted extensions.
- Fence old argument validators before/during approval admission and old executors
  before network/callback admission. A stale approval cannot authorize a new schema
  or callback behind the same alias. The actual worker checks the captured identity
  too; a definitive mismatch reports an execution error without callback invocation,
  replay or unnecessary replacement adoption. Original admitted executions retain
  their own callback/result rather than being re-routed mid-await.
- Project inventories on the actual owning Agent loop after context hooks, including
  independent children. Each model request/tool batch shares that snapshot; changes
  inside a callback appear in the next request on the SAME existing loop. Generic
  loop callers without a tools getter retain static-context semantics. No worker
  reader mutates Agent state and no second tool/model loop is created.
- Preserve native tools, parent/child permission/planning/allowlist restrictions,
  fail-closed Coding approval and cached model routing. Bound retained old alias
  provenance for session inventory cleanup. Registering metadata never starts a turn,
  authenticates/activates providers or promotes registration IDs to tool permission.

Verification snapshot — pass 91:

- Agent/loop/tool/runtime/rich results, both real clients and child permission/routing
  regression group: **116 passed** (41.56 s), `/tmp/pass91-final.log`. Final real SDK
  string→integer callback/schema replacement/disposal and same-worker paths in both
  clients, Coding pending-approval replacement denial, actual parent AND child
  callback-created inventory in the next request: **5 passed** (7.33 s),
  `/tmp/pass91-ui-final.log`; overlapping evidence, not additive totals. Direct old
  validator/executor/process/metadata/worker-admission and same-generic-loop cases:
  **8 passed** (1.66 s), `/tmp/pass91-unit.log`.
- MCP stdio/HTTP and external CLI runner regression group: **54 passed** (10.91 s),
  `/tmp/pass91-adapters.log`. Generic transports retain their original behavior.
- TWO production/private-tmux Coding/Claw enable→actual tool call→explicit schema/
  callback replacement→actual integer call→disposal→resize/editable native parent
  recovery paths: **2 passed**, **170 deselected** (7.42 s),
  `/tmp/pass91-terminal.log`. Original Coding approvals execute for both registrations;
  four explicit offline model callbacks per test, no registration/metadata model call,
  worker/daemon restart, implicit approval or terminal mode loss.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **307 source
  files**, no errors (unused-override notes only), `/tmp/pass91-types.log`. Diff check
  clean. Targeted gates after pass 85 baseline, not a new full-workspace/provider/
  platform or every SDK/MCP API parity claim.
- Previous goal turn: concrete source/terminal progress. Goal ACTIVE; original broader
  host/render/provider/auth/MCP/credential/native-platform requirements remain OPEN.
  OS-enforced sandboxing excluded. No production dependency/lockfile change, commit/
  reset/stash or unrelated worktree overwrite.

## Pass 92 — live inventory refresh retains model tool-call capability gates

- Audit the new actual inventory getter against the original native capability
  invariant. Its unconditional callback could reintroduce API tool definitions after
  `supports_tool_calls=False` had deliberately withheld them from the initial context.
  Fix the live getter, not the registered/local inventory or tool descriptions.
- Recheck actual current capability when each inventory is projected. Unsupported
  models receive no API definitions and cannot execute fabricated native tool calls,
  including after a context hook adds/updates tools. Supported/unknown capability
  models still receive fresh tools from an initially empty inventory; do not capture
  an initially false `_send_tools` flag and accidentally disable later registration.
- Preserve registered local tools, the same request/execution-batch snapshot and the
  original permission policy. Dynamic metadata/provider replacement does not imply
  native tool support or approval. No schema/executor/registration proof is weakened.
- Extend real worker provider replacement scenarios to submit a SECOND actual turn
  using the replaced text-only model. The owned callback verifies tools are None,
  emits a distinct success marker and never falls back to the base adapter. Current
  inventories remain present locally while API submission remains capability-gated.

Verification snapshot — pass 92:

- Agent/loop/tools/path guards/live registration, supervision/model capability,
  dynamic providers, both actual clients and child routing/policy regression group:
  **145 passed** (42.26 s), `/tmp/pass92-final.log`. Focused live-empty-inventory/
  unsupported-fabrication/path/callback identity group: **34 passed** (1.76 s),
  `/tmp/pass92-unit.log`. Both actual replaced-provider/lifecycle/unknown metadata
  groups: **6 passed** (7.05 s), `/tmp/pass92-ui.log`; overlapping evidence, not
  additive totals. No fabricated native call executes and stale capability does
  not become permission.
- TWO production/private-tmux Coding/Claw provider catalog→selected stream→live
  replacement→actual text-only no-API-tools turn→removal/counter/parent editing
  paths: **2 passed**, **170 deselected** (6.81 s), `/tmp/pass92-terminal.log`.
  Two explicit owned extension callbacks, no base provider/model/login fallback or
  daemon restart. Native terminal ownership/restoration assertions still execute.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **307 source
  files**, no errors (unused-override notes only), `/tmp/pass92-types.log`. Diff check
  clean. Targeted regression correction after pass 91, not a new full-workspace/
  native-provider/platform or all-capability parity claim.
- Previous goal turn: concrete source/terminal progress. Goal ACTIVE; original broader
  host/render/provider/auth/MCP/credential/native-platform requirements remain OPEN.
  OS-enforced sandboxing excluded. No production dependency/lockfile change, commit/
  reset/stash or unrelated worktree overwrite.

## Pass 93 — catalog-change watch filters and real notice-only management UI

- Extend current subscription routing beyond singleton resource URI watches to
  separately owned tools/prompts/resources catalog-change filters. Reuse the same
  negative identity namespace and lifetime bounds, verify exact acknowledgements and
  reject unrequested flags/notifications. Legacy list-change watches require explicit
  capability declaration and the existing live notification channel, not a resource
  subscribe request or a fabricated URI.
- Current stdio and HTTP own the original long-lived request/SSE response. Cleanup
  settles/cancels failed streams, retired IDs cannot reach successors, and detach/
  timeout/disconnect never imply read/list/completion/model admission or request replay.
  Separate resource/catalog keys preserve collision-free view ownership.
- Wire BOTH clients' focused MCP server list: Alt+T tools, Alt+P prompts, Alt+U resource
  catalog notices; same key explicitly stops the watch. Existing ordinary catalog
  browsing and URI w-watch semantics remain. Show actual filter/state/change count
  without replacing previews, auto-registering tools or submitting agent input.
- Claw negotiates `mcp_catalog_watch:1` separately, preserves original connection/
  group/session/view bindings and cancels in-flight catalog jobs on withdrawal.
  Detach only catalog watches in that case, retaining unrelated URI resource watches.
- Check primary current subscription documentation and the independently installed
  SDK's public notification schemas/serving methods. No runtime SDK dependency is
  introduced; source peer records distinguish explicit notice cause from reads/lists.

Verification snapshot — pass 93:

- Exact router/filter/cancellation, stdio/HTTP/current/legacy, both actual clients'
  catalog/resource UI and shared panel regression group: **77 passed, 2 skipped**
  (14.21 s), `/tmp/pass93-final.log`. Default optional SDK skips do not establish
  interoperability. Exact catalog kinds/unrequested filter/no-read/cleanup group:
  **9 passed** (0.88 s), `/tmp/pass93-unit.log`. Both live client current/legacy plus
  original-view capability-withdrawal/unrelated-resource preservation group:
  **5 passed** (1.91 s), `/tmp/pass93-ui-final.log`; overlapping evidence, not additive.
- Explicit independent serving-SDK catalog opt-in for ALL THREE filters over BOTH
  transports: **6 passed**, **10 deselected** (5.58 s), `/tmp/pass93-sdk.log`.
  These execute rather than skip and record no implicit list/resource/prompt retrieval.
- TWO production/private-tmux Coding/Claw connect→Alt+T→actual asynchronous change
  notice→explicit unwatch→view close→resize/editable native parent recovery paths:
  **2 passed**, **172 deselected** (5.20 s), `/tmp/pass93-terminal.log`. One owned
  subscriptions/listen, no post-watch tools/resources/prompts list/read, tool/model/
  provider/login call, upload or user-daemon restart. Native terminal modes preserved.
- Changed Python files: Ruff clean. Strict agent/TUI/Coding/Claw mypy: **307 source
  files**, no errors (unused-override notes only), `/tmp/pass93-types.log`. Diff check
  clean. Targeted gates after pass 85 baseline, not a new full-workspace/protocol/
  combined-filter/provider/platform parity claim. Remaining original requirements
  stay OPEN; goal ACTIVE and OS-enforced sandboxing excluded. No production dependency/
  lockfile change, commit/reset/stash or unrelated worktree overwrite.

## Work order

Continue richer custom containers and broader raw-input ownership/older-sender
interoperability,
provider/other host APIs, remaining MCP/content/
subscription/credential management and native clipboard/platform validation. Reorder when
runtime dependencies or verified risk demand it, but retain every row in the
completion audit. Each pass records concrete implementation and test evidence;
the goal stays active until all rows are implemented and verified.

## Pass 94 — stable MCP browsing and exact management key matching

- Fix shared panel navigation: server-list filters no longer leak into resource,
  template or prompt catalogs and make real entries appear missing. Entering a
  preview clears list-filter state so preview actions remain reachable.
- Same-view refresh preserves the selected server/catalog entry by identity, not
  its old row index, while retaining the user's filter. Removed/empty entries fall
  back to a valid selection. Filter deletion resets selection consistently.
- Match management/navigation keys with exact modifiers. Ctrl/Alt combinations
  no longer accidentally invoke ordinary connection, catalog or resource-form
  actions; explicitly bound Alt+T/P/U catalog-watch actions remain available.
- Shared panel + actual Coding browser and Coding/Claw catalog/resource-watch
  regressions: **24 passed, 2 skipped** (5.12 s), `/tmp/pass94-tests.log`.
  Skipped optional cases are not interoperability evidence. Changed-file Ruff
  clean; strict TUI mypy **89 source files**, `/tmp/pass94-types.log`.
- This is a bounded shared UI stability fix, not additional protocol conformance
  or native-platform parity. Original remaining gaps and the goal stay ACTIVE;
  OS-enforced sandboxing remains excluded. No full-suite rerun, dependency change,
  model/login call, daemon restart or unrelated worktree reset.
- Existing actual Coding/Claw catalog-watch toggle and native-terminal recovery
  paths: **2 passed**, **172 deselected** (4.53 s), `/tmp/pass94-terminal.log`.
  These verify the retained Alt-key path and parent recovery, not physical-terminal
  coverage of every new filter/selection case. Final diff whitespace check clean.

## Pass 95 — owned background custom-view repaint API

- Check the primary Pi custom-TUI reference's `tui.requestRender()` behavior, then
  add `api.request_render()` to XDOG's actual trusted-worker API. Background tasks
  from the owning custom invocation can repaint after updating state without
  synthetic key/paste/resize gestures from the user or any model/tool submission.
  Render callbacks cannot recursively request rendering. Unavailable/closed owners
  return False; custom UI alone does not promise background repaint support.
- Coalesce worker requests within an event-loop tick and host requests through
  callback admission, bound the original FIFO and cap background admission at
  30 FPS. Retain only dirty state while hidden/zero-height/nested-suspended, then
  repaint the original view on visibility recovery. No new second model/tool loop.
- Fence extension namespace, admitted current worker, live callback/dialog/view
  identities and original broker future. Independent children use their own broker
  and detach cleanup. The private extension notification is not admitted by ordinary
  current MCP clients; it is not an advertised generic MCP client capability.
- Revoke queued AND in-flight completion proof as soon as background state changes.
  A repaint only invokes render, never input or completion. Require a newly painted
  frame before confirming changed state. Existing scoped custom frames suffice for
  the Coding/Claw viewers; no new viewer wire fields or capability downgrade.
- Add owned liveview worker fixtures and actual parent/child Coding/Claw tests:
  two background state transitions, 100 coalesced requests each, zero input callbacks
  until one explicit confirmation, original draft/cursor/focus recovery and no
  parent model calls. Include missing capability, pre-viewport/hidden/nested deferral,
  retired/reloaded workers, queue/in-flight proof races and generic MCP rejection.
- An intermediate broad run overlapped the final capability-negotiation edit and
  reported 170 passed / four live-view timeouts (`/tmp/pass95-final.log`). This is
  NOT final-source evidence. A fresh-process focused run after the edit passed
  **9 tests** (`/tmp/pass95-current.log`, 8.64 s); final gates recorded below.

Verification snapshot — pass 95 final source:

- Worker/broker/ownership/proof/custom/nested/stdio plus actual Coding/Claw
  custom parent/child regression group: **159 passed**, **16 deselected**
  (57.02 s), `/tmp/pass95-final-current.log`. Deselected cases are the existing
  structured-layout matrices, not evidence of native-platform/full parity.
- TWO production/private-tmux Coding/Claw background repaint→explicit confirmation→
  resize/native parent editor recovery paths: **2 passed**, **174 deselected**
  (5.24 s), `/tmp/pass95-terminal-final.log`. Zero model calls, no synthetic input
  for background updates, no user-daemon restart or terminal mode loss.
- Changed-file Ruff clean; strict agent/TUI/Coding/Claw mypy **307 source files**,
  `/tmp/pass95-types-final.log`. Final diff whitespace check clean. No dependency/
  lockfile changes, paid calls, commit/reset/stash or unrelated worktree overwrite.
- Previous goal turn: verified progress. This turn implements a real additional
  rendering API and proof-race safeguard. Goal remains ACTIVE; broader original
  host/provider/MCP/credential/native-platform requirements remain OPEN.
  OS-enforced sandboxing is excluded. No full-suite or full parity claim.

## Pass 96 — exact combined MCP subscription sets through both live clients

- Add current-protocol `watch_notifications` / `unwatch_notifications` to stdio,
  HTTP and the existing manager, with independent combined-watch detach. One stream
  carries any selected subset of three catalog flags and explicit URI arrays.
  Normalize false/empty fields and URI order for stable owned-set identity; copied
  filters/snapshots, exact booleans, duplicate/unknown/empty rejection and existing
  watch quotas remain bounded. URIs remain opaque explicit data, not patterns.
- Check complete ACK filters without silent narrowing. Partial flags/URI subsets
  settle unsupported; extra flags/URIs, malformed values, duplicates/reordering and
  unrelated notices are refused. Route only the selected catalog/URI notices and
  preserve original stream identities, cancellation/close and no replay. Explicitly
  refuse legacy combined streams rather than substitute multiple singleton requests;
  existing legacy singleton catalog/resource behavior is retained.
- Wire focused MCP server-list Alt+W to an editable exact JSON filter form in BOTH
  actual clients. Enter explicitly starts/stops the selected set, draft persists,
  errors remain editable, view close detaches all owned watches. Combined snapshot
  fields do not fabricate a URI/catalog. No auto read/list/register/model admission.
- Claw independently negotiates `mcp_combined_watch:1`, fences original connection/
  group/session/view and bound job filters. Withdrawal settles in-flight combined
  jobs and combined watches only; unrelated singleton URI/catalog ownership remains.
  Missing capability and null-filter downgrade attempts are refused.
- Inspect the locally installed independent SDK's actual public serving behavior;
  extend the owned SDK fixture with explicit arbitrary-URI notices. No runtime SDK
  dependency/download or live service/model/login is introduced.

Verification snapshot — pass 96:

- Exact filters/ACK/router, all catalog-flag subsets × zero/one/multiple URI shapes,
  generic modern/legacy/stdio/HTTP, both actual catalog/resource/combined clients
  and shared panel regressions: **128 passed, 2 skipped** (15.11 s),
  `/tmp/pass96-final.log`. Optional default skips are not interoperability evidence.
- Explicit independent serving-SDK **18 passed** (14.27 s),
  `/tmp/pass96-sdk-final.log`, including combined three catalogs + multiple URI
  streams, unrequested URI suppression and exact unwatch over BOTH stdio/HTTP;
  not blanket external MCP/client-capability conformance.
- TWO production/private-tmux Coding/Claw Alt+W editable set→one owned stream→three
  asynchronous notices→explicit unwatch→view close→resize/native parent recovery:
  **2 passed**, **176 deselected** (5.36 s), `/tmp/pass96-terminal.log`.
  Zero automatic tools/resources/prompts list/read, tool/model call or daemon restart.
- Repository Ruff clean (`/tmp/pass96-ruff-final.log`); it found one existing
  121-column line in the pass-92 capability test, wrapped without semantic change
  and rechecked: **4 passed** (0.14 s), `/tmp/pass96-capabilities.log`.
  Strict affected namespaces **308 source files**, `/tmp/pass96-types-final.log`.
  Final diff whitespace check clean. No dependencies/lockfiles, commit/reset/stash
  or unrelated user worktree changes overwritten.
- Previous turn: verified progress. This turn closes the applicable combined-filter
  UI/API gap while retaining original broader host/provider/auth/credential/MCP/
  native-platform requirements. Goal ACTIVE, OS-enforced sandboxing excluded.

## Pass 97 — live non-capturing overlays with protected input ownership

- Add boolean `non_capturing` placement and explicit `live_overlay=True` opt-in for
  otherwise capturing custom overlays. Separate visible-frame ownership from input
  focus: visible non-capturing overlays render/repaint without a custom cursor or
  intercepting parent typing/paste. Hidden overlays send zero-height ownership
  updates; capturing/inline/fullscreen behavior remains separately supported.
- Add bounded invocation-owned `api.request_overlay(hide/show/focus/unfocus)`.
  True is request admission, NOT an applied-focus receipt. Monotonic controls carry
  full host-owned hidden/capture state; callback output cannot manufacture it.
  Initial controls wait for actual host frame admission, with 32 queued requests
  per view, instead of being lost before dialog creation. Closed/retired/reloaded
  worker/callback/view identities and duplicate sequences cannot target successors.
- Bind background repaint/control tasks weakly to their first requested view. Old
  tasks cannot adopt sequential replacements; nested callbacks resolve the nearest
  custom view, not the root. Explicitly starting a new custom invocation resets
  only that invoking task's binding. Render callbacks cannot control presentation.
- Preserve cached parent state through nested questions; selected-view presentation
  respects approvals/output reviews/foreign host overlays and cannot promote a
  later unrelated dialog. Focus/hide/unfocus revokes queued AND in-flight completion
  until a new painted viewport generation. Returning from a nested unfocused overlay
  restores the actual base editor, not the retired nested input.
- Claw separately negotiates `extension_custom_live_overlay:1`, including parent
  and child view tracking. Static-overlay-only viewers cannot downgrade this mode;
  withdrawal/disconnect cancels original live futures, not unrelated static views.
  Both clients retain scoped Escape/Ctrl+C cancellation from the background editor,
  native suspend/exit and existing busy/submission/permission policy.
- Physical terminal tests found that old global key handlers swallowed ordinary
  typing even with correct non-capturing focus (paste-only tests had missed it).
  Fix actual key routing in BOTH clients and add real key/paste dispatch regression
  assertions. Do not relax viewport/approval or terminal ownership checks to pass.
- Validate malformed initial/control snapshots without poisoning later owner updates.
  Add the dependency-light OverlayHandle.is_capturing inspection rather than access
  its private entry from the shared custom-presentation component.

Verification snapshot — pass 97:

- Broad custom/refresh/live/nested/stdio, TUI rendering and BOTH actual client
  parent/child/structured-layout/fullscreen/overlay regression group: **278 passed**
  (119.14 s), `/tmp/pass97-final.log`. This broad gate precedes the final ordinary-key
  routing and invalid-initial-state fixes; not a final-source full-suite claim.
- Final live owner/API/task replacement/nearest nested, complete/cancel, exact ordinary
  key + paste editing, protected approval/foreign-overlay focus, nested restoration,
  old-viewer/capability/disconnect and malformed-state group: **23 passed**, **110
  deselected** (20.86 s), `/tmp/pass97-final-current.log`. Final prompt keymap/Claw
  inline/TUI-render/refresh/current transport and selected real-client routing group:
  **86 passed**, **17 deselected** (28.69 s), `/tmp/pass97-routing.log`.
  These overlap and must not be added as a unique total.
- Repository Ruff clean (`/tmp/pass97-ruff-last.log`); strict affected namespaces
  **308 source files**, `/tmp/pass97-types-last.log`, no errors. Diff check clean.
  No production dependencies/lockfile changes, paid model/login calls, user-daemon
  restarts, commits/resets/stashes or unrelated worktree overwrite.
- Previous goal turn: verified progress. This turn adds actual live render/focus
  capabilities and fixes terminal-discovered routing/proof defects. Original broader
  rendering/raw-input/provider/auth/host/MCP/credential/native-platform requirements
  remain OPEN; goal ACTIVE and OS-enforced sandboxing excluded.
- Final TWO production/private-tmux Coding/Claw initial background overlay→real
  ordinary-key parent editing→worker hide/show/focus→explicit completion→resize→
  editable original parent/native cleanup paths: **2 passed**, **178 deselected**
  (12.12 s), `/tmp/pass97-terminal-final.log`. Initial run exposed the key-swallowing
  defect (`/tmp/pass97-terminal.log`, two failures); the final run verifies its fix,
  not a relaxed assertion. Zero model calls/synthetic overlay-input events or user
  daemon restarts. Native macOS/Windows/Kitty/iTerm validation is still not established.

## Pass 98 — invocation-owned tool inventory and early child-profile projection

- Add `api.get_tool_inventory(query, offset, revision)` backed by the actual owning
  Agent's configured definitions, not a registry or borrowed parent footer. Return
  copied public names/descriptions/full finite JSON schemas, actual model/owner,
  separate API capability gate and immutable-at-invocation source semantics.
  Literal search, 32-row continuation pages, revision-stale receipts and bounded
  2,048-tool/512-KiB snapshots avoid implicit truncation or unbounded metadata.
- Preserve False/0/null, unavailable versus empty inventories, and metadata versus
  executor/approval identity. No tool execution, schema-reference retrieval, global
  registry mutation, automatic input or model/provider/auth call. Invalid/foreign/
  unstable optional snapshots are unavailable, not fabricated permissions.
- Wire actual Coding/Claw parents and independent children, including commands,
  tools/hooks/context and headless paths. Protect invocation fields from caller
  context forgery; worker admission verifies actual session/child owner. Later
  callbacks see newly projected definitions; awaited callbacks retain their snapshot.
- Apply explicit child tool subsets BEFORE startup hooks and after final extension
  startup projection in BOTH factories. Previously startup introspection could show
  inherited definitions later excluded by the supervisor. Existing supervisor
  unavailable-name checks, permission/planning/capability and live-registration
  policy still apply; no new tool loop or upward tool grant is introduced.

Verification snapshot — pass 98:

- Final core public schema/copy/query/page/capability/owner-race/forgery/unavailable
  and existing model/context inspection tests: **49 passed** (2.16 s),
  `/tmp/pass98-core-final.log`.
- BOTH actual clients × parent/child × allowed/denied × UI/headless callbacks:
  **16 passed** (22.99 s), `/tmp/pass98-ui-current.log`. Four actual read-only command
  paths additionally inspect exact schemas, unsupported/supported API gates,
  replacement metadata revisions, unchanged permissions/drafts/cursors and zero
  model/tool calls: **4 passed** (4.60 s), `/tmp/pass98-command-final.log`.
- Wider tool lifecycle/supervision/child profile/both-client callback regression
  gate: **202 passed, 1 failed** (73.57 s), `/tmp/pass98-final.log`. The failure found
  a pass-81 forged-context test using a raw tool RPC without the executor registration
  required since pass 91. Update that test to assert raw denial, then supply the
  legitimately captured registration while KEEPING forged owner/context/registration
  fields. Do not weaken production registration guards. The final 49-test core gate
  verifies correct owner projection; the earlier broad gate is not a green/full-
  workspace claim and overlapping counts are not additive unique totals.
- TWO production/private-tmux Coding/Claw explicit inventory command→actual cached
  definitions→no permission/model submission→resize/editable native parent recovery:
  **2 passed**, **180 deselected** (5.71 s), `/tmp/pass98-terminal.log`.
- Repository Ruff clean (`/tmp/pass98-ruff-last.log`); strict affected namespaces
  **309 source files**, `/tmp/pass98-types-last.log`. Diff whitespace check clean.
  No production dependency/lockfile changes, paid model/login calls, user-daemon
  restarts, commits/resets/stashes or unrelated user changes overwritten.
- Previous turn: verified progress. This turn adds a real host API and fixes child
  startup inventory consistency. Goal stays ACTIVE; broader original host/provider/
  auth/MCP/credential/render/native-platform requirements remain OPEN, with
  OS-enforced sandboxing excluded. No overall parity or full-suite claim.

## Pass 99 — bounded inspection IPC cache and frame-budget recovery

- Avoid repeating full tool schemas on each command/hook/custom/provider invocation.
  Negotiate private `tool_inventory_cache:1` through actual admitted worker info,
  transfer the first snapshot inline, then reference its owner/model/capability/
  definition fingerprint only after worker acknowledgement. Older workers use the
  original inline path; no public MCP/viewer capability is inferred or changed.
- Bound host identities and worker data to eight snapshots. Resolve with exact
  owner validation and fresh copies, preserving admitted callback snapshots through
  eviction. Changed schemas/models/owners transfer new data; reload owns new caches.
  Missing refs report unavailable and repair a LATER invocation's cache, without
  replaying the current command/tool/hook or borrowing a successor's definitions.
- Fix a new optional-inspection stability risk: large otherwise valid callback
  arguments plus inline schemas could exceed the existing stdio frame limit and
  fail the worker. Reserve normal envelope space and omit optional inventory for
  that invocation; preserve exact arguments, original registration/permission and
  frame bounds. Later small calls recover full inspection. No implicit data clipping,
  tool/model call, second tool loop or permission grant.

Verification snapshot — pass 99:

- Real worker schema-heavy repeated callback vs older-inline, definition replacement/
  reload, near-8-MiB arguments, no replay, cache miss recovery and copied owned FIFO
  eviction plus existing inventory/model/context inspection: **55 passed** (7.80 s),
  `/tmp/pass99-core-final.log`. Repeated negotiated callback frame is measured below
  **1/20** of its original inline fixture frame; not a blanket CPU/model latency claim.
- Wider live tool registration and BOTH actual clients × parent/child × permission
  × UI/headless callback/command regression group: **98 passed** (56.89 s),
  `/tmp/pass99-final.log`, before the final cache-miss repair; final callback/UI/
  custom refresh/live ownership group: **47 passed** (51.65 s),
  `/tmp/pass99-ui-final.log`. Overlapping evidence, not additive unique totals.
- TWO production/private-tmux Coding/Claw actual repeated inventory commands with
  separate completion counters→no model/tool permission→resize/editable native
  parent recovery: **2 passed**, **180 deselected** (5.92 s),
  `/tmp/pass99-terminal.log`. Existing full schemas/API gates remain visible without
  replaying command results or making paid/provider/login calls.
- Repository Ruff clean (`/tmp/pass99-ruff-last.log`); strict ALL SEVEN namespaces
  **439 source files**, `/tmp/pass99-types-last.log`, no errors. Shipped fully stubbed
  workflow examples **17 passed**, `/tmp/pass99-examples.log`. Diff whitespace clean.
  No dependency/lockfile changes, daemon restart, commit/reset/stash or unrelated
  user changes overwritten. These are targeted/static integration gates, not a new
  full-workspace/platform/protocol parity claim.
- Previous turn: verified progress. This turn changes actual shared worker/host wire
  behavior and removes a real capacity failure while retaining exact semantics and
  owned lifetimes. Goal ACTIVE; original broader host/provider/auth/MCP/credential/
  render/native-platform requirements remain OPEN. OS-enforced sandboxing excluded.

## Pass 100 — actual native configured-tool/schema browsers

- Expose `/tools [QUERY]` in BOTH clients with real owning Agent metadata, full
  names/descriptions/compact lossless schemas, model API gate and explicit NOT
  approval banner. Reuse native transcript search/selection/scroll/fullscreen and
  output review infrastructure, but never substitute canonical conversation data.
  Schema/name search covers all definitions, not only 32-row metadata pages.
- Coding reads on one bounded background worker with view/session/branch request
  fencing, unchanged editor and no model/tool submission. Its headless command
  prints complete query-filtered JSON via all inspection pages. Claw reads the
  actual gateway group/session Actor, separately negotiated `tool_inventory:1`,
  with reader-responsive bounded jobs, cancel/withdrawal/disconnect cleanup,
  exact request/session correlation and no local footer approximation.
- Cold Claw inspection initializes the actual local session cache when needed,
  returning and binding its real owner ID without a model/tool turn or daily/idle
  reset. Owner changes clear cached display/output admission; explicit r refresh
  reads the current owner. Invalid/unavailable snapshots do not retain old tools
  as a silent successful fallback. Copy/export/share captures the settled tools
  records, not canonical model history or unrelated transcript selections.
- Physical tests exposed that source search omitted tool names (titles alone were
  not searched) and that a cold gateway had no cached Agent yet. Add names to the
  searchable source body and the actual-session bootstrap path. Keep owned namespace,
  permission/argument/tool-registration and native terminal proof checks unchanged.
- Shared source-browser chrome now uses subclass captions/labels instead of textual
  replacements that could rewrite user schema strings or exceed narrow line widths.
  Standard transcript captions remain unchanged.

Verification snapshot — pass 100:

- Final actual both-client API-gate/schema/name/query/refresh/no-model/editable parent,
  scoped output capture/fullscreen, raw gateway capability/foreign owner/cancellation/
  reader responsiveness/quota recovery, headless completeness and shared formatting
  plus existing commands/full-source/output/inline regressions: **80 passed** (3.65 s),
  `/tmp/pass100-final-current.log`. Focused browser group **8 passed** (1.47 s),
  `/tmp/pass100-focused-final.log`; overlapping evidence, not additive unique totals.
- TWO production/private-tmux Coding/Claw native /tools→full name search→explicit
  refresh→alternate fullscreen→native return→resize/editable parent cleanup paths:
  **2 passed**, **182 deselected** (3.95 s), `/tmp/pass100-terminal-final.log`.
  Initial terminal failures (`/tmp/pass100-terminal.log` and intermediate current
  log) identified the actual name-search/cold-session defects rather than prompting
  relaxed assertions. No model/tool/permission grant or user-daemon restart.
- Repository Ruff clean (`/tmp/pass100-ruff-final-last.log`); final seven-namespace
  strict mypy result recorded below. Diff whitespace clean. No dependency/lockfile
  changes, paid model/login calls, commit/reset/stash or unrelated user worktree
  overwrite. No full-workspace/platform/protocol parity claim.
- Previous turn: verified progress. This turn connects an actual UI capability, not
  only an SDK helper. Goal ACTIVE; original broader host/provider/auth/MCP/credential/
  render/native-platform requirements remain OPEN; OS-enforced sandboxing excluded.
- Final ALL SEVEN namespaces: **440 source files**, no errors,
  `/tmp/pass100-types-all.log` (unused-override notes only).

## Pass 101 — inventory provenance, reconnect and output admission

- Give the tools source viewer explicit valid-inventory readiness separate from
  the generic transcript index being settled. Valid empty inventories still show
  their real zero count; loading/failed/withdrawn/disconnected snapshots cannot be
  treated as current configured definitions or copied/exported through any scope.
- Clear tool request/capability/display admission on connection loss/open, reject
  late old replies and require explicit refresh after a successful new handshake.
  Preserve the parent editor and do not automatically resubmit reads or model input.
- Fence canonical transcript read requests, source events and deferred conversation
  outputs away from the tool-definition surface. The tool browser cannot receive
  conversation records merely because it shares transcript rendering infrastructure.
  Check current owner again at output initiation, not only on the next polling tick.
- Exercise actual Unix-socket loss/reconnection: retain the same viewer/draft, remove
  old inventory, change the owning Agent definitions while disconnected, then explicitly
  refresh the real new connection. No local metadata fallback or model/tool execution.

Verification snapshot — pass 101:

- Both actual clients, full schema browser/output/fullscreen/headless/owner/empty/error
  distinctions, real gateway cancellation and reconnect plus existing transport,
  transcript/output/commands/inline regressions: **111 passed** (4.14 s),
  `/tmp/pass101-final.log`. Focused explicit reconnect/canonical-source isolation:
  **2 passed**, **8 deselected** (1.12 s), `/tmp/pass101-reconnect.log` (overlapping).
- TWO production/private-tmux tools-browser search/refresh/fullscreen/native-parent
  paths retained: **2 passed**, **182 deselected** (3.93 s), `/tmp/pass101-terminal.log`.
  These terminal paths verify retained interaction, while socket reconnect/failure
  admission is proved by the dedicated live client tests, not blanket platform parity.
- Repository Ruff clean (`/tmp/pass101-ruff-last.log`); all seven namespace type gate
  recorded below. Diff whitespace clean. No dependency/lockfile change, paid model/
  login call, user-daemon restart, commit/reset/stash or unrelated worktree overwrite.
- Previous turn: verified progress. This turn fixes actual provenance/admission paths
  introduced by the native schema browser. Goal ACTIVE; original broader host/provider/
  auth/MCP/credential/render/native-platform requirements remain OPEN and OS-enforced
  sandboxing remains excluded.
- Final strict ALL SEVEN namespaces: **440 source files**, no errors,
  `/tmp/pass101-types-all.log` (unused-override notes only).

## Pass 102 — workspace integration checkpoint (fail-fast run complete)

- Previous turn: verified implementation progress. Start ONE full workspace fail-fast
  sweep after the shared rendering/runtime/MCP/inspection changes since pass 85:
  `uv run pytest -q -x`, `/tmp/pass102-workspace.log`, tool session **27097**.
  Do not restart or duplicate this sweep based on an observation timeout.
- Authoritative final exit: session **27097**, code **1**, **4,163 passed, 29 skipped,
  1 failed** (1043.55 s / 17m23.55s). The first failure was the navigation review test
  described and resolved in pass 103 below. The original process stopped through
  fail-fast, not an observation timeout. No duplicate full sweep was started.
  This is NOT a green full-suite or goal-completion claim.
- Verify all eight CI namespace/entry-point imports successfully. Build agent, TUI,
  Coding and Claw source distributions AND wheels offline using the existing cached
  build dependencies, `/tmp/pass102-wheels`, `/tmp/pass102-build.log`.
- Inspect all four wheel contents for the new inventory/browser/runtime modules,
  unpack together into a private temporary directory, and assert those four imports
  resolve from the BUILT artifact paths rather than editable source. All pass.
  Dependency imports still use the workspace environment; this is artifact-content/
  import evidence, not a hermetic/native-platform installation claim. Temporary
  unpacked files are cleaned, wheel/sdist artifacts remain under /tmp only.
- No production code edit while the integration sweep runs; no dependency download,
  lockfile change, paid model/login call, user-daemon restart, commit/reset/stash or
  unrelated worktree overwrite. Diff whitespace clean. Goal remains ACTIVE and all
  original remaining requirements remain intact; OS-enforced sandboxing excluded.


## Pass 103 — focused navigation review fixture and remainder verification

- Previous goal turn: verified wait plus completed artifact/import evidence.
  Revalidate the same workspace process and observe its authoritative failure/exit;
  investigate the actual failing component and its review proof before editing.
- The navigation test fixture never assigned focus. Since pass 87, shared forms
  correctly refuse to collect whole-review proof while unfocused. The real Coding/
  Claw clients already assign focus to the wrapper and delegate it to the form.
  Fix the stale TEST fixture to emulate that ownership; do NOT auto-focus production
  dialogs or remove the focused/whole-target/narrow/hidden/repeat confirmation gates.
- Add explicit negative regression: an unfocused navigation view cannot accumulate
  any seen target rows, and blur discards prior proof. Returning to focus requires a
  complete newly painted target before an affirmative answer. Default denial and
  no navigation/model/tool/permission mutation semantics are retained.
- Core navigation/form plus BOTH actual client navigation paths: **27 passed**
  (16.83 s), `/tmp/pass103-navigation.log`. Entire TUI package: **699 passed**
  (15.08 s), `/tmp/pass103-tui.log`, including the fixed and new focus proof cases.
- Inspect actual current pytest collection: **4,599 collected** after adding the
  regression; **407 cases from the original failure onward**, all under TUI. Thus
  the separate TUI sweep covers the entire previously unexecuted remainder. This
  verifies coverage across runs, NOT a fresh single-process all-workspace green run
  or conformance evidence for the 29 optional/platform skips. Collection evidence:
  `/tmp/pass103-collection.log` and the asserted package/source-path audit output.
- TWO production/private-tmux Coding/Claw extension navigation/parent recovery
  paths retained: **2 passed**, **182 deselected** (5.34 s),
  `/tmp/pass103-terminal.log`. No real navigation approval proof was relaxed.
- Repository Ruff clean (`/tmp/pass103-ruff.log`); strict ALL SEVEN namespaces
  **440 source files**, `/tmp/pass103-types.log`, no errors. Diff whitespace clean.
  Only the owned navigation test/goal tracker changed this turn; no production code,
  dependencies/lockfiles, paid calls, user-daemon restart, commit/reset/stash or
  unrelated worktree overwrite. Goal ACTIVE; original broader host/provider/auth/
  MCP/credential/render/native-platform requirements remain OPEN. OS sandbox excluded.

## Pass 104 — explicit private-key JWT client authentication

- Add pre-registered `private_key_jwt` with explicit `client_private_key_env`, exact
  issuer method/RS256 advertisement, no shared-secret/dynamic/algorithm fallback.
  Reuse existing normalized authentication-source review/profile persistence fields;
  do not store private key bytes or assertions in public/session records.
- Implement owned asynchronous POSIX/OpenSSL RS256 using unencrypted 2,048–8,192-bit
  RSA PEM, 64-KiB source cap and key bytes only on stdin. Minimal helper environment
  excludes inherited key variables, no PEM argv/key file, and stdout/stderr failures
  are not published. A private unnamed temporary descriptor holds unsigned PUBLIC
  claims only, never key/code/verifier/token, and is cleaned after owned process
  kill/reap/timeouts/cancellation. No Python crypto dependency/download or hand-rolled
  production RSA signing. Windows/other native signing backends remain open.
- Fresh 60-second exact-token-endpoint-bound assertions for code exchange and reviewed
  refresh. Preserve issuer/resource/TLS/scope/cookie/redirect/no-replay policy and
  recheck the original consent owner's liveness before a confidential POST after
  async signing. Direct malformed SDK profiles cannot bypass pre-registration checks.
- Show method, RS256, source variable name and lifetime/audience in BOTH actual whole-
  view consent panels. Claw separately negotiates `mcp_private_key_jwt:1`; missing
  support rejects private auth operations and withdrawal revokes only corresponding
  private auth-view leases before later signer/token admission. Native tool permission
  and independent child/control behavior remain separate.

Verification snapshot — pass 104:

- Real signer independent public RSA/PKCS#1 verification, fresh claims/nonce, invalid/
  weak/encrypted/source/config/method/algorithm/retired-owner and signer cancellation
  plus existing confidential/public/refresh/MCP/both actual client consent regressions:
  **109 passed** (11.42 s), `/tmp/pass104-final-last.log`. Final signer minimal-env/
  process tests **17 passed** (1.18 s), `/tmp/pass104-signer-final.log` (overlap).
- BOTH actual client RSA consent→explicit code exchange→reviewed refresh→fresh
  independently verified assertions→no public PEM/assertion/model/tool/draft mutation
  paths pass; Claw also verifies private capability withdrawal and no later POST.
- TWO production/private-tmux Coding/Claw RSA source/method/whole review→real owned
  callback→authorized→reviewed refresh→explicit MCP connection→native parent cleanup:
  **2 passed**, **184 deselected** (8.94 s), `/tmp/pass104-terminal.log`.
  No real issuer/login/provider/model call or user-daemon restart. Terminal test key
  files are generated disposable fixtures, not production credential storage.
- Repository Ruff clean (`/tmp/pass104-ruff-final-last.log`); strict ALL SEVEN
  namespaces **441 source files**, `/tmp/pass104-types-last.log`. Diff whitespace
  clean. No runtime dependency/lockfile change, commit/reset/stash or unrelated user
  worktree overwrite. No whole-protocol/native-platform/full-workspace parity claim.
- Previous turn: verified integration/test progress. This turn adds an actual remaining
  client-auth capability with real host/UI wiring. Goal ACTIVE; original broader
  host/provider/auth/MCP/credential/render/native-platform requirements remain OPEN,
  including further algorithms/backends. OS-enforced sandboxing is excluded.

## Pass 105 — repeated signer cancellation retains resource ownership

- Previous turn: verified implementation progress. Inspect the actual new async
  signer and find a second cancel could interrupt its shielded cleanup while process
  creation or claim-descriptor creation remained pending. This could abandon an
  owned helper/descriptor on repeated view/session close despite passing single-
  cancel tests.
- Add a cleanup-only settlement wait. Repeated cancellation cannot abandon process
  startup, communication draining, wait/reap or threaded unsigned-claim descriptor
  creation. Preserve the original canceled operation result; this is NOT a callback/
  token retry, suppressed user cancellation or changed consent/permission policy.
- Deterministic race fixtures cancel twice while a spawned helper is withheld before
  returning its owner and while temporary descriptor creation is held in its worker
  thread. Both original tasks remain canceled, wait for the exact existing owner,
  then kill/reap or close it; no second process/file is created and no key is published.

Verification snapshot — pass 105:

- Actual RSA signatures/weak/malformed/config/algorithm/retired-owner and single PLUS
  repeated cancellation races: **19 passed** (0.99 s), `/tmp/pass105-signer.log`.
  Same underlying auth/refresh/MCP/both real-client UI/whole-consent regression
  group: **111 passed** (11.55 s), `/tmp/pass105-final.log` (overlap).
- TWO production/private-tmux RSA whole-review/callback/refreshed-assertion/native
  parent recovery paths retained: **2 passed**, **184 deselected** (8.99 s),
  `/tmp/pass105-terminal.log`. These verify retained terminal behavior, while the
  deterministic repeated-cancel races independently prove owned cleanup.
- Repository Ruff clean (`/tmp/pass105-ruff-final.log`); strict ALL SEVEN namespaces
  result recorded below. Diff whitespace clean. Only the owned signer/helper tests
  and goal documentation changed; no dependencies/lockfile, paid calls, daemon
  restart, commits/resets/stashes or unrelated user changes overwritten.
- Goal ACTIVE; original host/provider/auth/MCP/credential/render/native-platform
  requirements remain OPEN. POSIX/OpenSSL limitations are not waived and OS-enforced
  sandboxing remains excluded. No full-workspace or native-platform parity claim.
- Final all seven namespace strict type gate: **441 source files**, no errors,
  `/tmp/pass105-types-final.log` (unused-override notes only).

## Pass 106 — negotiated signing withdrawal and visible retired consent

- Previous turn: verified implementation progress. Inspect actual private-key auth
  UI/protocol paths: withdrawing the viewer's signing capability revoked backend
  leases, but unconditional server pong advertising could leave the client displaying
  an actionable old consent/authorization view.
- Return the connection's effective negotiated private-JWT capability through the
  actual dispatch/pong path (not only the event writer). Disable only a corresponding
  private-key OAuth panel: clear URL, pending review/receipt admission and busy state,
  stop polling, show signing unavailable, and preserve only the close route.
  Late launch/status receipts cannot revive it and local signing requests are refused.
- Existing completed resource credentials are NOT remotely revoked by withdrawing
  a signing/UI lease. Prove that credential retention and unrelated public-client
  consent remain usable. Re-enabling support does not silently re-authorize an old
  disabled panel; the user must reopen a fresh consent flow.
- Recheck original owner liveness after asynchronous source/key validation and before
  callback listener creation. A capability/view withdrawal during that await cannot
  open a callback or create a token/code operation afterward. Preserve signature/
  scope/resource/permission/no-replay and existing callback ownership checks.

Verification snapshot — pass 106:

- Actual private/public/confidential/refresh/MCP/gateway + both real clients and shared
  panel disabled-URL/no-poll/no-actions/late-receipt, unchanged credential/public
  consent and async-key-validation withdrawal regressions: **128 passed** (13.29 s),
  `/tmp/pass106-final-last.log`. Actual negotiated socket/visible-view focused group:
  **15 passed** (3.09 s), `/tmp/pass106-live-current.log` (overlapping evidence).
- TWO production/private-tmux Coding/Claw RSA consent/code/refresh/native parent paths
  retained: **2 passed**, **184 deselected** (8.27 s), `/tmp/pass106-terminal.log`.
  No real provider/login/model calls, tool approval or user-daemon restart. Dedicated
  socket race tests prove withdrawal; terminal paths verify retained normal use.
- Repository Ruff clean (`/tmp/pass106-ruff-final-last.log`); seven-namespace type gate
  recorded below. Diff whitespace clean. No runtime dependencies/lockfile changes,
  commits/resets/stashes or unrelated user changes overwritten.
- Goal remains ACTIVE; original host/provider/auth/MCP/credential/render/native-platform
  requirements are OPEN. Existing POSIX/OpenSSL limitations remain explicit, and
  OS-enforced sandboxing remains excluded. No full-workspace/native parity claim.
- Final strict all seven namespaces: **441 source files**, no errors,
  `/tmp/pass106-types-last.log` (unused-override notes only).

## Pass 107 — OAuth whole-review proof follows actual focus ownership

- Previous turn: verified UI/admission progress. Inspect consent rendering and find
  the OAuth component's seen-row proof depended on geometry but not the nested MCP
  surface actually owning focus. A painted background consent could accumulate proof
  while another host review held input, contrary to the established form policy.
- Add explicit focused consent state, invalidate complete/seen/scroll proof on blur
  or focus transition, and collect rows/confirmability only while focused. Keep
  default unfocused state; do NOT implicitly focus production consent or simplify
  endpoint/source/algorithm/scope review.
- Propagate the actual MCP view's focus into newly prepared, refreshed, restored and
  existing OAuth panels. This follows the existing TUI's real focus transitions,
  including protected overlay preemption, without a second input/focus owner.
- Both live Coding and Claw RSA paths now fully review, lose focus to a separate
  host-owned overlay, paint/page while hidden, reject Enter and collect no rows,
  then regain focus and review ALL fields freshly before original code/refresh
  operations. Standalone consent fixtures explicitly emulate normal host focus.

Verification snapshot — pass 107:

- Focus/default-hidden/blur plus private/public/confidential/refresh/MCP/both actual
  client/source/copy/preview/schema browser regressions: **140 passed** (14.80 s),
  `/tmp/pass107-final.log`. Direct both-client protected preemption and shared OAuth
  group **24 passed** (10.22 s), `/tmp/pass107-live.log` (overlapping evidence).
- FOUR production/private-tmux Coding/Claw HMAC AND RSA whole-review/callback/native
  parent paths retained: **4 passed**, **182 deselected** (14.29 s),
  `/tmp/pass107-terminal.log`. No actual confirmation proof, width/budget/repeat gate,
  credential/source review, token no-replay or native terminal ownership was relaxed.
- Repository Ruff clean (`/tmp/pass107-ruff-last.log`); strict ALL SEVEN namespaces
  **441 source files**, `/tmp/pass107-types.log`, no errors. Diff whitespace clean.
  No production dependencies/lockfile, paid/model/login calls, user-daemon restart,
  commit/reset/stash or unrelated user changes overwritten.
- Goal remains ACTIVE; original broad host/provider/auth/MCP/credential/render/native-
  platform requirements remain OPEN. POSIX/OpenSSL limitations are retained and
  OS-enforced sandboxing remains excluded. No full-workspace/native parity claim.

## Pass 108 — nested MCP completion focus and fresh insertion proof

- Previous turn: verified consent-focus progress. Inspect the other nested MCP input
  surface and find completion's editor was always focused and insertion proof was
  geometry-only, independent of whether the actual parent MCP view owned input.
- Add explicit default-unfocused completion state, delegate the parent's real focus
  to new/existing completion panels and their editor, and invalidate insertion proof
  on focus transitions. Closing completion restores argument focus only when the
  parent is focused, not unconditionally while another host surface is active.
- Blurred views cannot request fresh suggestions, edit/consume another owner's paste,
  or insert a value. Original scoped responses may remain inert data; returning focus
  requires a newly painted current viewport before insertion. Preserve draft/scope
  receipts, parent compare-and-swap, resize/budget/repeat guards and the separately
  explicit prompt/resource retrieval gesture. No cancel/replay or auto read/model/tool.
- BOTH actual clients lose completion focus to a separate protected host overlay,
  paint/page/attempt input while blurred, retain the exact original argument draft,
  then recover focus and repaint before inserting the original scoped suggestion.

Verification snapshot — pass 108:

- Shared prompt/template/opaque-resource completion/draft/hidden/narrow/focus plus
  real Coding/Claw gateway/model/consent regressions: **111 passed** (23.06 s),
  `/tmp/pass108-final.log`. Direct shared/both actual completion browser group:
  **49 passed** (6.57 s), `/tmp/pass108-live.log` (overlapping evidence).
- FOUR production/private-tmux current/legacy Coding/Claw explicit argument-completion,
  editable draft, separate retrieval and native-parent paths retained: **4 passed**,
  **182 deselected** (9.56 s), `/tmp/pass108-terminal.log`. No model/tool/provider/
  login call, permission grant or user-daemon restart. Protected preemption is proved
  by dedicated live-client cases, not inferred from these normal terminal paths.
- Repository Ruff clean (`/tmp/pass108-ruff-last.log`); strict ALL SEVEN namespaces
  **441 source files**, `/tmp/pass108-types.log`, no errors. Diff whitespace clean.
  No dependencies/lockfile, commits/resets/stashes or unrelated user worktree overwrite.
- Goal ACTIVE; original broader host/provider/auth/MCP/credential/render/native-platform
  requirements remain OPEN. OS-enforced sandboxing remains excluded. No new full-
  workspace/protocol/native-platform or overall CLI parity claim.

## Pass 109 — native skill selection and correct request placement

- Finish the in-progress skill-control pass rather than repeat the full-workspace
  comparison or suite. Claw lacked user-owned native selection despite exposing a
  model skill tool; add `/skills`, `/skills load SLUG`, `/skills unload SLUG`, and
  `/skills unload all`, negotiated against the actual owning gateway/session.
- Persist validated slug-only selection in atomic session sidecars, rejecting
  malformed/duplicate/path-like/oversized metadata and symlink destinations. Keep
  session-wide selection separate from transcript/branch instruction bodies.
- Fix a shared Agent bug: structured system prompt blocks dropped the skill index
  and selected fixed bodies during composition/rebuild. Prepend one cacheable skill
  block while retaining the original static/mutable blocks and their cache flags.
- Both actual clients now inject selected turn-scoped skill bodies only into request
  context, not canonical history or the cacheable prefix; remove Coding's transient
  active-name prefix note. Expire selections on attempted-turn failure as well as
  completion. Session skill bodies remain removable without model submission.
- Recheck idle parent/children and current owner after asynchronous discovery. Settle
  admitted Claw disk writes AND owning-loop selection/prompt state through repeated
  cancellation before releasing group reservations, including canceled-before-start
  job cleanup. A lost receipt is not permission to replay a mutation.
- Validate catalog receipts and actual current session; reject late/unsolicited,
  malformed, disconnected and capability-withdrawn results. Add explicit request
  expiry and reconnection reset; pong reports connection-effective skill negotiation.
- Document session-wide (not branch-local) selection and uncertain receipt recovery.
  Preserve editor drafts, permissions, native scrollback and existing model/tool loop.

Verification snapshot — pass 109:

- Skill discovery/placement/metadata, both actual apps, real Unix gateway malformed
  action and negotiation, durable restore, idle/foreign-owner rejection, failed-turn
  expiry, repeated-cancellation settlement, stale receipts, command/queue/session/
  child regressions: **179 passed** (12.59 s), `/tmp/pass109-final.log`.
- TWO production/private-tmux Coding/Claw skill load/unload/list, resize and native
  parent/termios restoration paths: **2 passed**, **186 deselected** (4.28 s),
  `/tmp/pass109-terminal.log`. No model/tool/login call or user-daemon restart.
- Repository Ruff clean, `/tmp/pass109-ruff-final.log`; strict ALL SEVEN namespaces
  **442 source files**, `/tmp/pass109-types-all.log`; diff whitespace clean.
  No full-workspace rerun, dependency/lockfile changes, commits/resets/stashes or
  unrelated user changes overwritten in this pass.
- Goal remains ACTIVE. Broader host/provider/auth/MCP/credential/native-platform
  requirements remain OPEN; OS-enforced sandboxing remains explicitly excluded.
  This is a bounded verified improvement, not an overall parity/completion claim.

## Pass 110 — native manual compaction and selected-adapter correctness

- Previous turn made verified progress (native skill selection). Inspect the current
  clients rather than repeat the broad comparison: Claw had automatic compaction
  but no native manual control; Coding's manual command swallowed errors, used a
  fixed context target and bypassed the selected adapter through global completion.
- Add Claw `/compact [focus instructions]` and `/compact cancel`, negotiated against
  the actual idle group/session. Requests have bounded instruction text, fresh IDs,
  connection-effective capability receipts, group reservations, explicit pre-start
  cancellation receipts, stale-owner validation, timeout/reconnect recovery and no
  automatic replay. Drafts remain editable/retained; other submissions remain drafts
  while compaction owns the session. Escape respects foreground surfaces first.
- Both clients use a single tool-free summary request through their actual selected
  adapter, including registered extension providers. Preserve selected effort/output
  options, disable web search, reject tool/empty/error/aborted/truncated/oversized
  summaries, bound request time and sanitize request/cleanup errors. This is not a
  second conversation/tool loop and does not grant permissions.
- Coding now honors current context/prompt limits, supports explicit focus, preserves
  complete recent user/tool turns and includes old tool-call/result text in summaries.
  Manual failures reach the command UI instead of reporting fake success. Automatic
  Coding failure retains history and remains best-effort.
- Claw manual compaction works below the automatic threshold, avoids needless small-
  history summary calls, retains its existing guarded durable-memory flush/archive
  path, and routes built-in flush/summarizer helpers through the current session model
  rather than the initial model. Missing/unavailable summary placeholders no longer
  replace unsummarized history. Archive filesystem writes are owned and off-loop.
- Settle admitted disk commits AND owning-loop history publication through repeated
  cancellation before releasing compaction ownership. Cancellation may lose a receipt
  after an admitted commit; explicitly direct users to inspect current transcript,
  never claim rollback or replay the request. Reject concurrent context/policy changes.
- Keep Coding's existing session data identity so plan/extension persistence closures
  cannot overwrite compacted history with stale pre-compaction data. Restore Claw's
  tagged summaries as clean user-context text, not repr of the metadata dictionary.
  Preserve source revision, permission/planning policy and native scrollback.

Verification snapshot — pass 110:

- Shared summary rejection/options/async/cancel, BOTH actual clients and real Unix
  gateway, current selected native and registered extension providers (success and
  failure, exact worker cleanup/no primary fallback), no-op/disk failure/retained
  draft, plan/extension follow-on persistence, repeated cancellation against observed
  live commit owners, coalesced pre-start cancellation/reservation release, duplicate
  request and capability denial, skill/session/navigation/provider/queue/full-source
  regressions: **151 passed** (26.66 s), `/tmp/pass110-final.log`.
- TWO production/private-tmux Coding/Claw `/compact` with focus, actual summary/model
  route, persisted shortened context, resize and native parent/termios restoration:
  **2 passed**, **188 deselected** (3.86 s), `/tmp/pass110-terminal.log`. Offline peers
  only; no paid model/login calls or user-daemon restart. Aiosqlite emitted an existing
  event-loop deprecation warning during gateway cleanup; tests passed.
- Repository Ruff clean (`/tmp/pass110-ruff-final.log`); strict ALL SEVEN namespaces
  **443 source files**, `/tmp/pass110-types-all.log`; diff whitespace clean. No full-
  workspace rerun, dependency/lockfile edits, commit/reset/stash or unrelated changes
  overwritten in this pass.
- Goal ACTIVE. Original broader host/provider/auth/MCP/credential/native-platform
  requirements remain OPEN. OS-enforced sandboxing remains excluded; no goal-wide,
  fresh full-suite or cross-platform parity/completion claim.

## Pass 111 — extension-owned compaction preparation, veto and summary overrides

- Previous goal turn made verified progress. Inspect current hooks/runtime and find
  manual/automatic compaction had no real extension preparation/result contract.
  Implement before/after lifecycle callbacks rather than a client-only notice shim.
- Add `session_before_compact` and committed `session_compact` registration using
  existing owned/live/idempotent hook declarations and dispatch snapshots. Complete
  typed preparations use existing checksummed owned transfer, including >8 MiB input;
  private worker operation capability is required independently of UI availability.
- Each before callback gets an independent original preparation copy, with current
  model/owner inspection and no borrowed UI/editor/session browser/navigation lease.
  Add invocation-scoped `api.has_ui` as a hint only, never a permission/approval gate.
- Typed decisions support continue, explicit public veto, or a bounded custom summary
  with optional finite JSON details and retained-user-turn index. Validate source
  boundaries against actual canonical messages, preserve the latest user/tool turn,
  reject mixed/invalid results and conflicting custom providers, and never silently
  fall back to a model when a configured hook fails. Retire/reap canceled silent
  hook workers before releasing the host operation; explicit reload/unload remains.
- Route BOTH actual clients' manual and automatic pre-turn paths through the same
  contract. Custom summaries bypass stateless summary requests; Claw retains its
  existing guarded durable-memory flush/archive stage. Automatic veto preserves
  history and lets the normal turn follow its ordinary context/provider gates;
  manual veto reports its bounded public reason without fake success.
- Persist source/retention/original-count/details metadata in Coding's session and
  branch records and Claw's tagged transcript/branch summary entries. Preserve
  legacy summary tags and recover clean previous summary text from typed entries.
  Post observers run only after disk AND memory commit, inside the cancellation-
  settled commit owner, including when caller cancellation loses its receipt.
- Actual automatic-turn tests exposed another mismatch: Coding triggered at the
  session's prompt ceiling but prepared retention using the larger Agent ceiling,
  yielding a no-op instead of compaction. Bound preparation by both ceilings so
  triggering and retention agree; retain selected-adapter and no-replay behavior.

Verification snapshot — pass 111:

- Actual worker oversized/signed/tool/image preparation, immutable siblings, version
  denial/disposal; typed result/boundary/record validation; BOTH actual applications,
  real Unix gateway, manual AND normal automatic user turns, continue/veto/custom/
  malformed/conflicting hooks, observed live-worker cancellation/reaping, repeated
  admitted-commit cancellation with committed observers, durable branch restore;
  core hooks/full-context/provider/summary/skills/session/navigation/queue/source
  regressions: **217 passed** (45.10 s), `/tmp/pass111-final.log`.
- FOUR production/private-tmux Coding/Claw ordinary AND extension-provided `/compact`
  routes, real persisted context/metadata, zero summary-model calls for custom
  providers, resize and native parent/termios recovery: **4 passed**, **188 deselected**
  (7.93 s), `/tmp/pass111-terminal.log`. Offline peers only; no paid model/login or
  user-daemon restart. Existing aiosqlite loop-deprecation warning remains visible.
- Repository Ruff clean, `/tmp/pass111-ruff-final.log`; strict ALL SEVEN namespaces
  **444 source files**, `/tmp/pass111-types-all.log`; diff whitespace clean. No full-
  workspace rerun, dependency/lockfile edits, commit/reset/stash or unrelated work
  overwritten. This extends real host capability, not overall/native parity proof.
- Follow-up audit found concrete history-retention questions: Coding still replaces
  its old context without an automatic pre-compaction return checkpoint, and Claw's
  Markdown archive filenames are only second-resolution. Durable lossless pre-
  compaction recovery/browsing and same-second archive overwrite prevention need
  dedicated work; do not treat current-context viewer tests as proof of those.
- Goal ACTIVE; original broader host/provider/auth/MCP/credential/native-platform
  requirements remain OPEN. OS-enforced sandboxing remains excluded.

## Pass 112 — durable pre-compaction recovery and non-overwriting archives

- Previous turn made verified progress. Inspect the actual persistence/navigation
  paths and implement the concrete follow-up, not just an archive directory listing.
- Coding atomically writes the complete original typed history as a named **Before
  compaction #N (manual/automatic)** branch together with the shortened active head.
  Copy prior model/settings, planning, extension entries and compaction metadata.
  Publish branches and active context together on the owning loop; failed saves
  publish neither. Keep existing data identity and post-commit cancellation ownership.
- Claw writes/indexes a create-only lossless recovery branch before replacing current
  JSONL context. A checkpoint failure cannot discard original history. If replacement
  later fails, an extra durable checkpoint is harmless, not a fake success receipt.
  Capture the complete actual Agent history, including unsaved live tail, while
  preserving unchanged disk-only channel/timestamp/fallback-usage/summary metadata.
- Link committed compaction metadata/post observers to the typed source through an
  optional validated `recovery_branch_id`. Distinct checkpoints survive repeated
  compaction; neither app automatically activates the old context. Both report the
  native `/tree` recovery workflow instead of requiring users to find raw files.
- Both actual clients now browse/select their displayed recovery branch, restore full
  history without a model turn, retain parent drafts and a reversible compacted return
  head, and keep normal permission/planning/idle/group/tool-boundary checks. Retain
  incomplete tool-call tails as data, but refuse unsafe full-branch activation; valid
  earlier tree checkpoints remain available. Workspace files are not rolled back.
- Fix Claw Markdown archive naming with random suffixes, exclusive creation, flush/
  fsync, failed-write cleanup and private POSIX mode. Same-second/concurrent archives
  cannot overwrite each other or follow an existing symlink. Lossless recovery uses
  typed branch data, not Markdown reconstruction.

Verification snapshot — pass 112:

- Full/context/hook/summary/provider/session/planning/queue/source regressions,
  actual Coding and real Claw gateway recovery-tree activation, manual/automatic
  checkpoints, complete >8 MiB image/signed/tool snapshots, unsaved live tail,
  repeated compaction links/return heads, checkpoint/save failure, observed repeated
  admitted-commit cancellation, unsafe tail refusal, concurrent/same-second private
  archive/collision/symlink/fsync cleanup: **232 passed** (48.91 s),
  `/tmp/pass112-final.log`.
- FOUR production/private-tmux Coding/Claw ordinary AND extension-provided compaction
  → visible **Before compaction #1** selection → persisted full-history restoration
  → resize/native parent/termios recovery, with no extra model turn: **4 passed**,
  **188 deselected** (9.23 s), `/tmp/pass112-terminal-next.log`. Offline peers only;
  no paid model/login or user-daemon restart. Existing aiosqlite deprecation warning
  remains visible. Terminal tests wait for actual committed restoration rather than
  matching an old scrollback line as proof of success.
- Repository Ruff clean, `/tmp/pass112-ruff-final.log`; strict ALL SEVEN namespaces
  **444 source files**, `/tmp/pass112-types-all.log`; diff whitespace clean. No full-
  workspace rerun, dependency/lockfile edits, commits/resets/stashes or unrelated
  work overwritten in this pass.
- This closes the concrete checkpoint and same-second overwrite defects from pass
  111. Recovery/search uses explicit tree activation and the existing full-source
  viewer; an aggregate/read-only archive timeline across all branches is not proved
  by these tests and remains a separate capability audit item. Broader host/provider/
  auth/MCP/credential/native-platform requirements remain OPEN. Goal ACTIVE;
  OS-enforced sandboxing remains explicitly excluded.

## Pass 113 — read-only saved session/branch source browsing

- Previous turn made verified progress. Inspect current source/navigation paths and
  close the explicit follow-up without requiring archive activation or model work.
- Add focused physical **v** to both native session/tree browsers; keep **Enter** as
  the separate activation gesture. Add `/transcript branch ID` and `/transcript saved
  SESSION [BRANCH]`. View both saved branches and other saved sessions; current live
  session entries without a branch still use the ordinary canonical/live source.
- Coding uses one coalescing background saved-read owner and shared complete typed-
  message formatting, not temporary Agent/session activation or UI-thread disk I/O.
  Preserve child preview formatting/partial/progress behavior and its original limits.
  Parent saved views have an explicit 20,000-message presentation limit, full text,
  redaction, image counts and no exposed binary/signature bodies.
- Claw reads the actual group-owned target through an independently negotiated saved-
  source operation. Reuse the existing bounded checksummed source sender/receiver with
  explicit target-session/branch envelopes. Preserve the active session/revision lease,
  independently fence every transfer frame's saved scope and refuse cross-group reads.
  Capability withdrawal cancels saved reads rather than live turns; no client-only
  guessed history or legacy/live fallback. Reconnect/owner change requires refresh.
- Saved panels are visibly read-only and isolated from live messages/client notices.
  Search traverses the complete saved text beyond previews and chunk boundaries;
  refresh/fullscreen/selection use existing native components. Source/output epochs
  and request/context correlation reject stale, closed and owner-changed work.
  Loading/failed/unsupported views remain non-exportable, and conversation/response/
  selection export/copy/share uses displayed archived records, not active conversation.
- Preserve active session/branch/Agent identity, permissions and parent drafts. Do not
  create/resume an Agent, mutate history, invoke tools, upload/share automatically, or
  implicitly activate the old source. Closing returns to current input. Read-only
  individual saved-source browsing is implemented; aggregate/deduplicated all-branch
  timeline semantics are not silently inferred from it.

Verification snapshot — pass 113:

- Saved workers coalesce/invalidate/close real blocked reads; source scope/checksum
  fencing; focused-press-only navigation gesture; BOTH actual apps and real Unix
  gateway branch AND other-session views, full 80-KiB tail search, redaction, archived
  output source, untouched active context/draft/permission/branch, foreign-group and
  capability denial, stale/closed/loading receipts; navigation/child/progress/source/
  compaction/session/selection/transfer regressions: **220 passed** (43.34 s),
  `/tmp/pass113-final.log`. Direct new feature/ownership group **14 passed** (1.83 s),
  `/tmp/pass113-live-final.log` (overlapping evidence).
- FOUR production/private-tmux Coding/Claw ordinary AND extension-provided compaction
  → **v** view/search original saved context while persisted active context stays
  shortened → close without activation → explicit tree restoration → native parent/
  termios recovery: **4 passed**, **188 deselected** (9.58 s),
  `/tmp/pass113-terminal-third.log`. Physical selection keys distinguish a fresh
  Enter from a repeated submit key; Ctrl+C closes query/selection regardless of state.
  Offline peers only, no paid model/login or user-daemon restart; existing aiosqlite
  event-loop deprecation warning remains visible.
- Repository Ruff clean (`/tmp/pass113-ruff-final.log`); strict ALL SEVEN namespaces
  **445 source files**, `/tmp/pass113-types-all.log`; diff whitespace clean. No full-
  workspace rerun, dependency/lockfile edits, commits/resets/stashes or unrelated work
  overwritten. No goal-wide/cross-platform parity claim.
- Goal ACTIVE. Original broader host/provider/auth/MCP/credential/native-platform
  requirements remain OPEN; merged all-branch timeline semantics remain separately
  unproven. OS-enforced sandboxing stays explicitly excluded.

## Pass 114 — owning-adapter native model catalog and failure-preserving selection

- Previous turn made verified progress. Inspect live model selection and find Coding
  still listed/refreshed native profiles from global `ai.load()` while its Agent
  executed through another adapter; it mutated the model before metadata validation,
  swallowed lookup failures and retained stale capability limits. Startup also fell
  back to a different native model or guessed `sonnet` when profiles were missing.
- Introduce bounded same-adapter catalog/profile validation. `/model` lists canonical
  IDs from the actual adapter (plus its active extension providers), avoids ambiguous
  suffix-only labels, rejects invalid/duplicate/unavailable/non-chat/non-streaming
  profiles, and routes/persists the canonical resolved identity.
- Resolve/validate native profile and current effort before changes, atomically save
  the selected model/settings/history snapshot before owning-session publication, and
  update context/prompt/tool/vision/reasoning fields together. Clear effort for models
  without reasoning; refuse unsupported declared efforts instead of substituting one.
  Failed saves preserve the prior model/options/settings/capabilities and data owner.
- Use actual Agent tool capability state in prompt construction instead of a second
  global lookup. Explicit opaque SDK adapters retain caller-owned IDs/options but have
  no borrowed global/previous-model capability claims; clear stale metadata/ceilings
  and mark tools unreported. Do not pretend unknown adapter metadata is a real catalog.
- Native session/branch restores preflight saved model and effort before disposing
  current context/workers/rules. Keep extension aliases behind their existing explicit
  trusted-worker validation, not a native or primary-provider fallback. Comprehensive
  staged cross-session extension-provider restoration remains on the broader lifecycle
  audit; this pass does not claim a transactional staged-runtime solution for it.
- Startup uses the actual provider adapter's cached resolver and valid chat profiles.
  Missing requested/saved native IDs, invalid stream/type/window profiles and empty
  default catalogs fail clearly; no alternate-model/guessed-name fallback or native
  invalid-profile session creation. Keep public result compatibility without inventing
  a fallback warning. Existing Claw selection already used actual adapter metadata;
  verify both client paths against the same owned test adapter rather than duplicate
  another gateway policy loop.

Verification snapshot — pass 114:

- BOTH actual UI clients + real Claw gateway catalog/canonical selection/effort reset/
  all capability fields/no global fallback, native unavailable denial, save failure,
  preflight unavailable-session/branch/effort rejection with unchanged owners, startup
  invalid/empty profile refusal/no new session, unchanged invalid-resume bytes,
  opaque adapter no-borrow behavior; extension startup/capability/live/UI, session/
  compaction/recovery/saved-source/child regressions: **141 passed** (57.48 s),
  `/tmp/pass114-final.log`. Direct new profile group **11 passed** (1.38 s),
  `/tmp/pass114-profile-next.log` (overlapping evidence).
- TWO production/private-tmux Coding AND Claw native-model catalog/selection/current
  effort/unsupported-model and effort refusal, persisted canonical fields, zero model
  calls, resize and native parent/termios recovery: **2 passed**, **191 deselected**
  (4.96 s), `/tmp/pass114-terminal.log`. Offline peers only; no paid model/login or
  user-daemon restart. No confirmation/permission/transport gates relaxed.
- Repository Ruff clean (`/tmp/pass114-ruff-final.log`); strict ALL SEVEN namespaces
  **446 source files**, `/tmp/pass114-types-all.log`; diff whitespace clean. No full-
  workspace rerun, dependency/lockfile edits, commit/reset/stash or unrelated work
  overwritten. Not a full-provider/goal-wide/native-platform parity claim.
- Goal ACTIVE. Original broader host/provider/auth/MCP/credential/native-platform
  requirements remain OPEN; staged cross-session extension lifecycles and merged
  all-branch timeline semantics remain unproven. OS-enforced sandboxing excluded.

## Pass 115 — Coding staged saved-provider navigation and buffered host state

- Previous turn made verified progress. Inspect restoration and find an unavailable
  saved extension provider could be discovered only after Coding had retired current
  workers/MCP/children/planning and replaced conversation data. Fix the actual session
  API and native command path, not a UI-only pre-check against the current catalog.
- Prepare a separate target extension runtime against target directory/settings,
  saved entries/planning/model and current permission mode. Validate the declared
  target alias/effort/streaming profile before publication; never borrow a provider
  from the current runtime or primary/native fallback. Stage has no interactive
  authority/inspection borrowed from the old owner and publishes no target UI.
- Buffer host-managed startup entry contributions instead of writing the target while
  merely validating it. On failure/cancellation, settle and close the prepared owner
  and leave saved target bytes plus active resources/context unchanged. Source code
  remains explicitly trusted; arbitrary external side effects are not rollbackable
  and the lifecycle flag `staged=True` is informational, not an OS sandbox.
- Successful activation commits target model/settings/entries/history before switching
  resources, adopts the same prepared workers (one setup/start, no cold reactivation),
  rebinds entry persistence/context/inspection/tool inventory to the live session, and
  publishes capability/effort fields without a second fallible global model lookup.
- Branch restore commits its target state and retained compacted/current return head
  in one session snapshot. Failures preserve old branch/planning/extension owners.
  Keep data identity where required by existing persistence closures and actual Agent
  hooks/child factories bound to the owning session. Native preflight behavior remains.
- Bind preparation/commit admission to the originating interactive command and SDK
  cancellation signal. UI remains responsive while the synchronous SDK's one owned
  bootstrap thread prepares/settles workers; canceling an unadmitted stage cannot
  publish it. Admitted atomic commits retain the existing no-rollback/no-replay rule.

Verification snapshot — pass 115:

- BOTH Coding resume and branch commands through the actual app, target-entry-based
  provider declaration, missing/failing/setup/save/canceled stages, unchanged target
  bytes/active Agent/workspace/draft/permission/MCP/plan/child/worker owners on failure,
  adopted provider streaming and follow-on durable entry writes with one startup;
  native/extension/session/compaction/recovery/saved-source/command/context/hook
  regressions: **136 passed** (50.09 s), `/tmp/pass115-final.log`. Direct stage matrix
  **10 passed** (16.47 s), `/tmp/pass115-ui.log` (overlapping evidence).
- TWO production/private-tmux Coding valid/missing saved-provider resume, buffered
  target-state persistence/no failed rewrite, original-session verification, one
  adopted startup, zero model calls during navigation, resize and native parent/
  termios recovery: **2 passed**, **193 deselected** (5.15 s),
  `/tmp/pass115-terminal-third.log`. Explicit command token avoids conflating fuzzy
  `/sessions` completion with `/session` metadata output. Offline peers only; no paid
  model/login or user-daemon restart, no permission/consent gates relaxed.
- Repository Ruff clean, `/tmp/pass115-ruff-final.log`; strict ALL SEVEN namespaces
  **447 source files**, `/tmp/pass115-types-all.log`; diff whitespace clean. No full-
  workspace rerun, dependency/lockfile edits, commit/reset/stash or unrelated work
  overwritten. This is verified Coding lifecycle progress, NOT a both-client/global
  staged-navigation completion claim.
- Goal ACTIVE. Claw's equivalent staged target navigation remains OPEN, along with
  original broader host/provider/auth/MCP/credential/native-platform requirements
  and merged all-branch timeline semantics. OS-enforced sandboxing excluded.

## Pass 116 — Claw staged navigation, buffered persistence and responsive cancellation

- Previous turn made verified Coding progress. Inspect Claw's actual group/session
  path and implement its counterpart, preserving dependency direction (Claw does
  not import Coding policy/staging helpers).
- Build a separate target AgentSession on a shadow group runtime with copied store
  metadata and buffered target transcript/entries/planning writes. Restore target
  history/child branch scope and initialize/validate actual target extension model/
  effort before activation. Do not publish target UI, borrow the old provider, run
  a model/tool turn, or rewrite target host state merely to validate it.
- Missing/failing/canceled providers leave the original cached Agent/extension/plan/
  child/MCP resources, pointer, context, saved files and parent draft intact. Successful
  admission adopts the same validated session and worker once and binds captured plan/
  entry writers, inspection/model context and child host services to actual group
  storage and the owning gateway loop. Preserve source-revision and permission policy.
- Add managed-file/cache navigation error transactions with private backup copies,
  atomic individual writes, active-pointer publication last, and exact ordinary-error
  restoration/temporary-file cleanup. Return/goto branch records are included in the
  managed transaction. Explicitly retain the boundary: NOT power-loss/crash atomic
  across multiple files, not a hostile-code sandbox, and not rollback of arbitrary
  trusted extension external side effects or unavailable rollback storage.
- Move mutating session controls into bounded connection-owned jobs so setup does not
  block the socket reader. Add independently discoverable navigation-cancel capability
  and Escape cancellation; correlate current owner/request, acknowledge pre-start
  cancellation, release reservations even for canceled-before-start tasks, and settle
  observed live prepare/commit owners through repeated cancellation/disconnect.
- If an admitted commit completed while its caller canceled, detect the actual changed
  session/revision and send real committed-state history synchronization, never fake
  rollback or replay. During disconnect, abort model/tool owners before waiting for
  potentially blocked navigation disk cleanup. Legacy viewers/peers retain explicit
  capability refusal, not a guessed cancellation receipt.

Verification snapshot — pass 116:

- Actual Claw app + real Unix gateway resume/branch target-entry-dependent provider
  activation, no borrowed/default fallback, missing/failing/canceled stages, untouched
  original owners/target files, late active-pointer-write failure rollback, reader
  responsiveness while setup blocked, one adopted startup and follow-on entry/plan/
  actual provider streaming; observed live commit owner through repeated cancel with
  real synchronized new state; Coding staging/native/source/compaction/recovery and
  Claw session/provider/navigation/child/full-source regressions: **161 passed**
  (93.78 s), `/tmp/pass116-final.log`. Direct final controls/staging/navigation group
  **25 passed** (31.41 s), `/tmp/pass116-controls-last.log` (overlapping evidence).
- FOUR production/private-tmux Coding AND Claw valid/missing saved-provider resume,
  target no-failed-rewrite/original-owner verification, one prepared startup/no extra
  model calls, resize and native parent/termios restoration: **4 passed**,
  **193 deselected** (9.76 s), `/tmp/pass116-terminal.log`. Offline peers only; no paid
  model/login or user-daemon restart, permission/consent gates unchanged.
- Repository Ruff clean (`/tmp/pass116-ruff-final.log`); strict ALL SEVEN namespaces
  **448 source files**, `/tmp/pass116-types-all.log`; diff whitespace clean. No full-
  workspace rerun, dependency/lockfile edits, commit/reset/stash or unrelated changes
  overwritten. Both-client staging progress, not full-storage/native parity proof.
- Goal ACTIVE. Original broader host/provider/auth/MCP/credential/native-platform
  requirements and merged all-branch timeline semantics remain OPEN. Multi-file
  abrupt-termination/rollback-storage limits are explicit, not waved away. OS-enforced
  sandboxing remains excluded.

## Pass 117 — durable Claw navigation undo journal and startup recovery

- Previous turn made verified progress. Inspect actual managed-file error transaction
  and close its recorded abrupt-process-interruption gap instead of relabeling atomic
  individual writes as a complete multi-file commit.
- Add a private checksummed undo journal with an explicit prepared/committed state.
  Flush all original backups before durable prepared publication; sync managed
  replacement names before committed publication; clean up only after a durable
  completion/rollback decision. Avoid exposing half-applied catalog/entry/plan/head/
  active-pointer changes after interrupted navigation.
- Recover BEFORE the store loads indexes/catalogs/sidecars. Prepared state restores
  originals/removes newly created targets; committed state keeps new complete state
  and finishes cleanup. Validate every source checksum/identity/destination before
  any replay, keep backups throughout replay, and make interrupted recovery repeatable.
- Restrict journals to owned managed basenames/private transaction artifacts; reject
  symlinks, invalid types/path traversal, duplicate sources/destinations and malformed
  manifests. POSIX advisory ownership refuses recovery while another process owns a
  live transaction. This is not an OS sandbox or hostile-filesystem confinement claim.
- If ordinary rollback itself encounters unavailable storage, retain complete recovery
  files and block the live store's session access until repaired/reopened. Restore cache
  state even when recovery fails; never delete backups in an unconditional finally.
  A committed cleanup error remains committed/restart-cleanable, not fake rollback.
- Preserve staged worker/model/entry admission, cancellation ownership, normal file
  permissions, source leases and native-scrollback/permission behavior. Explicitly
  retain filesystem/hardware/POSIX versus Windows durability boundaries; do not claim
  arbitrary external extension effects or broken storage are recoverable by the host.

Verification snapshot — pass 117:

- Real offline subprocess `os._exit` after EACH of ELEVEN managed writes; before/after
  actual commit marker; interrupted recovery after one restored file; exact old bytes/
  new committed state, indexes/planning/entries/return branches and active pointers;
  corrupt backups/path traversal/foreign live-process lock refusal; live rollback
  storage failure/reopen and committed cleanup failure; Claw staging/session/provider/
  child/navigation/source and Coding staging/recovery/saved-source regressions:
  **174 passed** (100.58 s), `/tmp/pass117-final.log`. Direct recovery/crash group:
  **21 passed** (9.01 s), `/tmp/pass117-crash-next.log` (overlapping evidence).
- FOUR production/private-tmux Coding/Claw valid/missing saved-provider navigation,
  persisted owner checks, one prepared startup, no model calls during navigation,
  resize and native parent/termios recovery retained: **4 passed**, **193 deselected**
  (9.94 s), `/tmp/pass117-terminal.log`. Offline peers only; no user-daemon restart,
  paid model/login call or weakened permission/consent gate.
- Repository Ruff clean (`/tmp/pass117-ruff-current.log`); strict ALL SEVEN namespaces
  **449 source files**, `/tmp/pass117-types-all.log`; diff whitespace clean. No full-
  workspace rerun, dependency/lockfile edits, commit/reset/stash or unrelated changes
  overwritten. No native Windows/hardware-power-loss or goal-wide parity claim.
- Goal ACTIVE. The concrete multi-file process-interruption gap now has durable
  recovery evidence. Original broader host/provider/auth/MCP/credential/native-platform
  requirements and merged all-branch timeline semantics remain OPEN. OS-enforced
  sandboxing stays excluded; unavailable/corrupt recovery storage fails closed.

## Pass 118 — exact slash-command intent versus neighboring completion actions

- Previous turn made verified progress. Revalidate the concrete terminal ambiguity
  recorded during staging tests (`/session` needed a trailing-space workaround to
  avoid `/sessions`). Inspect shared editor and find it excluded the exact command
  from its list but kept longer matches, then blindly submitted the remaining selected
  match on Enter. Fix actual editor behavior for both apps, not only the test input.
- Enter preserves a complete registered typed command unless the user explicitly
  browsed a neighbor with Up/Down. Keep Tab insertion and incomplete-prefix selection
  behavior; preserve file/path replacement and its separate no-submission rule.
  Normalize case consistently with dispatch and retain command-provider slash handling.
- Scope explicit neighbor-selection intent to current text/cursor/catalog items.
  Reset it on editing/history/catalog changes. Preserve exact typed intent through
  dynamic withdrawal so the unavailable command is reported, not changed into another
  action. Revalidate selected commands against the actual current catalog before
  inserting/submitting stale metadata. No model/tool/permission policy changes.
- Test exact versus explicit neighboring commands through BOTH actual live extension
  runtimes/apps and real Claw gateway callbacks, not synthetic command text alone.
  Remove the trailing-space `/session` fixture workaround; actual terminal tests now
  exercise the exact singular command directly.

Verification snapshot — pass 118:

- Exact/mixed-case/narrow-hidden suggestions, deliberate arrow/Tab choice, edited/
  history/dynamic withdrawal and stale selection; prompt/keymap/terminal/paste/file/
  workspace behavior; BOTH actual registered command callback counters/no model turn/
  permissions, Code `/session` versus `/sessions`, command/staging/Claw queue/shortcut
  regressions: **189 passed** (50.52 s), `/tmp/pass118-final.log`. Shared editor group
  **64 passed** (6.36 s), `/tmp/pass118-editor.log`; new both-client callback group
  **4 passed** (3.27 s), `/tmp/pass118-ui.log` (overlapping evidence).
- SIX production/private-tmux paths: Coding/Claw exact command + explicitly chosen
  neighbor with real callback counts/native parent recovery, plus valid/missing saved-
  provider paths retaining direct exact `/session` behavior: **6 passed**,
  **193 deselected** (14.16 s), `/tmp/pass118-terminal.log`. Offline peers only, zero
  model calls for commands/navigation, no paid model/login or user-daemon restart,
  no confirmation/focus/permission policy relaxed.
- Repository Ruff clean, `/tmp/pass118-ruff-final.log`; strict ALL SEVEN namespaces
  **449 source files**, `/tmp/pass118-types-all.log`; diff whitespace clean. No full-
  workspace rerun, dependency/lockfile edits, commits/resets/stashes or unrelated work
  overwritten. No goal-wide/provider/native-platform parity claim.
- Goal ACTIVE. Original broader host/provider/auth/MCP/credential/native-platform
  requirements and merged all-branch timeline semantics remain OPEN. OS-enforced
  sandboxing stays excluded.

## Pass 119 — session-wide read-only provenance timeline search

- Previous turn made verified progress. Inspect saved source/compaction/branch records
  and implement the repeatedly recorded merged-history gap in BOTH actual apps.
- Add `/transcript timeline [SESSION]` using existing read-only source viewers and
  owning background/chunked readers. Include every saved branch and admitted current
  head without activation, model/tool work or editor/context/policy mutations.
  Current-session head reads use captured live Agent history; inactive sessions use
  persisted data. Expose the view as **Session timeline · read-only**, with explicit
  refresh/search/fullscreen/selection and the normal reviewed output workflow.
- Define conservative provenance semantics: only recorded parent prefixes and actual
  compaction `recovery_branch_id`/original-count/retained-index links can share message
  identity, after complete canonical content/protocol/model/signature/binary checks.
  Verify transitive compaction retention. Keep summary events, independent roots,
  repeated later turns, divergent forks and tool-result variants distinct. Never use
  redacted visible-text equality to merge unrelated events or assert timestamp order.
- List source labels AND IDs on merged records/index, preserve full untruncated text
  and normal redaction/binary-signature omission. Claw retains channel/goal/scheduler
  filtering and clean summary conversion while producing full timeline source.
  Invalid/cyclic/mismatched lineage fails the whole read, not a guessed partial merge.
- Explicit capacity guards (256 sources/head included, 20k messages/source, 200k raw
  references, 20k unique records) return unavailable/no partial export; individual
  saved-source reads remain separate. Claw negotiates timeline independently from
  saved source, preserving target-session/sentinel scope across verified chunks and
  active-owner/revision/connection fencing. Unsupported peers cannot fall back to live
  content disguised as a merged history. No provider/execution/permission identity change.

Verification snapshot — pass 119:

- Pure parent-prefix/conservative independent-root/repeated-turn merge, transitive
  compaction tail/provenance, divergent tool results, cycle/mismatch/capacity refusal
  and signature omission; BOTH actual app + Claw Unix gateway timeline search over
  pre-compaction history, untouched current context/draft and one correctly attributed
  retained record; browser/selection/output/worker/session/compaction/recovery/source/
  staging regressions: **121 passed** (44.88 s), `/tmp/pass119-final.log`. New final
  timeline/independent capability-denial group: **7 passed** (0.60 s),
  `/tmp/pass119-scope.log` (overlapping timeline evidence plus final capability case).
- SIX production/private-tmux paths: Coding/Claw timeline search finding divergent
  fork-only history while active head stays unchanged, plus ordinary/extension
  compaction → saved view → explicit full recovery → native parent/termios recovery:
  **6 passed**, **195 deselected** (13.40 s), `/tmp/pass119-terminal-last.log`. Explicit
  physical Enter through the production enhanced-key parser distinguishes selection
  from submitted-command repeat; no focus/confirmation gate relaxed. Offline peers
  only; no model/tool call for reads, paid model/login or user-daemon restart.
- Repository Ruff clean (`/tmp/pass119-ruff-final.log`); strict ALL SEVEN namespaces
  **450 source files**, `/tmp/pass119-types-all.log`; diff whitespace clean. No full-
  workspace rerun, dependency/lockfile edits, commit/reset/stash or unrelated changes
  overwritten. No total-parallel-chronology/native-platform/goal-wide parity claim.
- Goal ACTIVE. Recorded merged history now has explicit provenance-backed semantics
  and both-client/native evidence. Original broader host/provider/auth/MCP/credential/
  native-platform requirements remain OPEN. OS-enforced sandboxing excluded.

## Pass 120 — integration checkpoint and synchronous Claw staging loop ownership

- A root checkpoint (`uv run pytest -q -x`) stopped authoritatively at **1 failed,
  2,834 passed, 25 skipped** (261.96 s), `/tmp/pass120-workspace.log`. The actual
  extension-entry gateway test called synchronous `GroupRuntime.navigate_session`
  from an active async SDK loop. Staging nested `asyncio.run`, raising before target
  activation and leaking an unawaited coroutine. This is NOT a green workspace run;
  cases beyond that stop remain unverified by this checkpoint.
- Preserve the synchronous API and existing transactional navigation policy. Run
  target initialization AND unadmitted prepared-session cleanup on an owned bootstrap
  thread/loop, as Coding already does. Construct the coroutine inside that owner;
  join and close the executor, propagate initialization failure/cancellation, and
  retain the caller loop. Production gateway navigation still runs through its disk
  owner, not by blocking the reader loop. The synchronous SDK API itself remains
  blocking; this is compatibility, not a claim of asynchronous navigation.
- Retain the actual previously failing gateway/branch/context regression. Add direct
  sync-from-async runner success/error/cancellation tests, settled async cleanup,
  closed owner loop/thread and unaffected caller loop assertions, plus explicit
  prepared-session child/extension/disposal cleanup ordering.

Verification snapshot — pass 120, **2026-10-08**:

- Entry/context/staging/catalog/navigation/journal/TUI-navigation regressions:
  **48 passed** (31.06 s), `/tmp/pass120-regression.log`. Initial affected subset
  **18 passed** (22.17 s), `/tmp/pass120-fix.log` (overlapping evidence).
- TWO production/private-tmux Claw saved-provider available/unavailable restoration
  and native-parent recovery paths: **2 passed**, **199 deselected** (5.00 s),
  `/tmp/pass120-terminal.log`. Offline peers only; no paid model/login or user daemon
  restart; no focus/confirmation/permission gate relaxed.
- Repository Ruff clean, `/tmp/pass120-ruff.log`; strict all-seven mypy clean across
  **450 sources**, `/tmp/pass120-types.log`; diff whitespace clean.
- All four Agent/TUI/Coding/Claw wheel AND source distributions build with cached
  isolated `uv build --offline`, `/tmp/pass120-build-isolated.log`. Verify every
  **320 Python sources** is present in BOTH artifact types and seven extracted-wheel
  imports actually resolve from packaged files, including extensions/MCP/supervision,
  timeline/staging/navigation journal: `/tmp/pass120-package-inspect.log`.
  Earlier no-isolation attempt failed because the runtime environment lacks hatchling;
  cached isolated builds succeeded without installing dependencies or editing manifests.
- No new full-root run after the fix; the failed checkpoint does not certify the
  unexecuted remainder. No dependency/lockfile edits, commits/resets/stashes or unrelated
  changes overwritten in this pass. Goal remains ACTIVE; broader host/provider/auth/MCP/
  credential/native-platform completion requirements remain open. OS sandboxing excluded.

## Pass 121 — durable session names and named browser search in both clients

- Previous turn made verified progress. Complete a bounded Claw integration check
  beyond pass 120's stopping point: **565 passed, 1 skipped** (246.24 s),
  `/tmp/pass121-claw.log`. It started before naming edits and is evidence for the
  previous Claw integration state, not a fresh full-root/new-name verification.
- External primary-reference fetches were unavailable in this pass. Do not assert
  a current-release Codex/Pi comparison from unavailable sources. Inspect actual
  native session browser/store/commands and implement missing user-assigned names
  as a concrete session usability improvement. Broader extension-host APIs stay open.
- Both native clients support `/name` inspection, `/name NAME` save and `/name -`
  clear. Named titles/search take precedence over generated summaries; preserve
  names through reload, branches and ordinary plan/extension/turn metadata writes.
  Retain Unicode/emoji joiners, reject terminal/newline/bidi/invisible formatting
  controls, and cap names at 256 UTF-8 bytes. Legacy unnamed Coding data still loads;
  preserve positional dataclass compatibility by appending its optional name field.
- Coding writes a staged data copy atomically before publishing name/timestamp to
  the SAME persistence data owner. Failures leave bytes/live metadata unchanged;
  cancellation before admission refuses the save. No model/tool/policy/history change.
- Claw independently advertises `session_rename: 1`, requires an explicit actual
  current group/session owner and idle reservation, and refuses target/branch
  substitution or a mismatched cached Agent. Journal catalog/index together using
  existing private recovery storage; never rewrite transcript/plan/entry sidecars.
  Before-admission cancellation retains the old name. Repeated cancellation after
  admission settles commit/cache publication before releasing group ownership.
- Correlated owner-checked receipts update metadata without `session_changed` or
  history replay, output-epoch churn, draft/view/selection loss or worker restart.
  Old/withdrawn gateways cannot silently approximate naming. Disconnect/uncertain
  receipts are not replayed; reconnect inspection reflects actual saved metadata.
- A wider Coding/session regression subset revealed TWO older test assumptions:
  branching used an Agent with empty model identity despite a saved `test` model;
  empty-context compaction expected a fictional "Compacted" success. Make the branch
  fixture use its explicit owning model and assert it in branch metadata. Strengthen
  the empty-context test with a fail-on-model-call stream, exact no-op notice and
  unchanged Agent identity. Do not weaken production preflight/compaction gates.

Verification snapshot — pass 121, **2026-10-08**:

- Name validation/actual both-client command and gateway receipts, named browser
  search, reload, owner/policy/history retention, plan/extension metadata persistence,
  unadmitted and repeated admitted cancellation, index failure rollback, real process
  death after BOTH metadata-write boundaries, no replay/foreign receipt/legacy peer,
  plus session/command/branch/staging/navigation/journal regressions:
  **126 passed** (47.89 s), `/tmp/pass121-regression-final.log`.
- Final name-owner group additionally tests stale cached Agent refusal and named
  archive reactivation/active-index catalog recovery: **10 passed** (2.74 s),
  `/tmp/pass121-owner.log` (overlapping nine earlier cases plus cached-owner case).
- TWO production/private-tmux paths: physical naming → actual filtered named session
  browser → resize → exit search then browser → native editor focus → clear name →
  native parent/termios/scrollback recovery: **2 passed**, **201 deselected** (5.34 s),
  `/tmp/pass121-terminal-complete.log`. Wait for each Escape's actual rendered state
  before next input; do not reinterpret an empty trimmed capture row as a failed
  cursor, or accidentally join a pending Escape with `n` into an Alt+N shortcut.
  No parser/focus/confirmation gate relaxed. Offline peers only; no paid model/login
  calls or user-daemon restart.
- Repository Ruff clean and strict all-seven mypy **451 sources** (final logs below).
  No new full-root checkpoint or parity/desktop-platform claim. No dependency/lockfile
  edits, commits/resets/stashes or unrelated changes overwritten. Goal ACTIVE;
  original broader host/provider/auth/MCP/credential/native-platform requirements
  remain open. At that snapshot native names did not expose extension-worker name mutation APIs; pass 122 adds the owned reviewed API below.
  OS-enforced sandboxing remains excluded.
- Final admission/capability audit: recheck cancellation inside the storage owner
  immediately before journal creation, not merely before waiting for ownership.
  Verify no journal/name write when canceled at that boundary, and detach withdrawn
  name-capability receipts without replay. Final BOTH-client name/ownership group:
  **14 passed** (3.00 s), `/tmp/pass121-final-admission.log` (overlapping prior name
  evidence plus the last storage-admission and withdrawal cases).
- Final repository Ruff: `/tmp/pass121-ruff-done.log`; final strict all-seven mypy:
  **451 sources**, `/tmp/pass121-types-done.log`; diff whitespace clean. Final
  physical-terminal rerun on this storage-admission implementation is recorded in
  `/tmp/pass121-terminal-done.log`; retain the same two native paths, no relaxed gates.
  Rerun finished: **2 passed**, **201 deselected** (4.67 s).

## Pass 122 — actual extension session-name inspection and reviewed deferred CAS

- Previous turn made verified progress. Inspect the actual worker/API, host broker,
  native naming owner, both UI command lifecycles and Claw negotiated transport.
  Implement `api.get_session_name()` and `api.set_session_name(name)` in both clients,
  not a plugin-only helper or an automatically executed returned slash command.
- Interactive command reads use the owning host metadata; Claw serves reads from
  the reserved gateway/broker, ignoring stale/forged viewer caches. Other invocations
  receive copied owner-checked snapshots when provided by the app. Expose parent
  names to actual lifecycle/pre/post/context/tool/headless-command callbacks without
  exposing history/model secrets or mutation authority. Unavailable/staged/stale/
  independent-child contexts cannot borrow another owner's name.
- Runtime replaces caller-supplied `host_session_name` with its own validated data.
  Verify session AND invocation owner IDs, strict fields/canonical Unicode names,
  fresh snapshot/no stale fallback and optional frame-pressure omission. No new
  global provider registry, model/tool loop, terminal policy or permission grant.
- Setter validates plain text then requests an exact expected-old/new metadata
  review using the existing whole-form/viewport/focus confirmation component.
  Session operations cannot impersonate an unrelated confirmation/form dialog.
  `True` is queued, NOT saved. Callback failure/cancellation/owner change discards
  the action. After callback success dispatch an owned background native name job;
  CAS the actual old name before storage admission. Claw keeps its journaled storage
  and admitted cancellation settlement; Coding retains the same persistence data
  owner. Empty name clears, while API `"-"` remains literal (no slash-text inference).
- Independently negotiate `extension_session_name: 1` on Claw. Older or boolean/
  withdrawn capability advertisements cannot borrow ordinary session/model inspection
  or navigation authority. Receipts/control remain session/connection scoped and
  no operation is replayed. Names/history/model/plan/children/extensions/draft remain
  owned by the original session, including declined/failed/stale-target paths.
- Explicit limit found during inspection: native Claw `/name` without arguments
  still displays last confirmed client metadata, whereas the extension command
  getter now reads the gateway owner directly. Multi-client native inspection
  freshness remained OPEN at that snapshot; pass 123 closes the native read below. No current-state freshness claim
  is made for that native cached display. Broader host/provider/auth/MCP/credential/
  native-platform requirements remain open, not waived by this API slice.

Verification snapshot — pass 122:

- Worker/API/dialog name contracts, foreign/caller/child forgery refusal, lifecycle/
  pre/post/context/tool/headless snapshots, optional frame-bound omission and fresh
  next invocation; actual both-client read/review/save/clear/literal-dash/decline/
  callback failure/stale-CAS/cancel, preserved draft/model/history/policy/owners,
  independent old/boolean/withdrawn capability and prior session/inspection/navigation
  regressions: **109 passed** (46.78 s), `/tmp/pass122-final.log`.
- Earlier broader integration subset: **159 passed** (80.76 s),
  `/tmp/pass122-regressions.log`; started before the final metadata frame guard and
  fixture hook additions, so it is not a fresh all-source/full-workspace claim.
- EIGHT production/private-tmux paths: BOTH-client exact name review, tiny resize
  cannot approve hidden metadata, callback-success saves / callback-failure retains
  the old name, normal native naming/search, and existing reviewed session navigation;
  native editor/parent/termios/scrollback recovery: **8 passed**, **199 deselected**
  (20.54 s), `/tmp/pass122-terminal-final.log`. Failures intentionally show the
  existing sanitized callback-failure notice, NOT raw plugin exceptions. No parser,
  focus/confirmation/approval gate relaxed. Offline peers only, zero model calls for
  metadata controls; no paid model/login or user-daemon restart.
- Final repository Ruff / strict all-seven mypy and diff whitespace results recorded
  below. No new full-root run, dependency/lockfile edits, commits/resets/stashes or
  unrelated changes overwritten. Goal remains ACTIVE. OS-enforced sandboxing excluded.
- Final repository Ruff clean: `/tmp/pass122-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **451 source files**: `/tmp/pass122-types-done.log`;
  `git diff --check` clean.

## Pass 123 — authoritative reader-responsive native Claw name inspection

- Previous turn made verified progress. Inspect the known native `/name` cached
  display gap and replace it with an actual group/session-owned gateway read.
  Coding already reads its owning session metadata; preserve that behavior.
- Claw independently negotiates exact integer `session_name_read: 1`; ordinary
  rename/session/extension-inspection support cannot masquerade as native reads.
  Old/boolean/withdrawn capability yields unavailable, not a guessed footer value.
  Request an explicit current session only; reject target/branch/name substitution.
- Store reads acquire its metadata lock NONBLOCKING and copy only validated committed
  group-owned name/identity. Wait asynchronously while journal/navigation publication
  settles, with a ten-second bound. Do not expose an in-flight index value as a
  name-read receipt, or create a cached Agent/read history/start a model to inspect
  metadata. No filesystem/helper worker is spawned by the read itself.
- Reader-responsive connection-owned jobs are capped at eight, with duplicate ID
  exclusion across metadata/mutation owners, explicit scoped cancellation, capability
  revocation and disconnect cleanup. Read completion/cancellation NEVER releases a
  different operation's group reservation. No uncertain read is automatically replayed.
- Separate client read lease from mutation/run ownership. Validate request/action,
  original group/session/connection and canonical plain-text response; refuse stale/
  foreign/malformed/superseded receipts. Changed current session marks context stale
  until explicit browser refresh/resume, never silently activates another head.
  Starting a local rename cancels its older read lease; replies cannot overwrite a
  newer local name receipt. Read-only operation retains draft/views/output epoch and
  active run/timer; hints/Escape explicitly cancel the read first, not the model turn.
- The reply is a linearized committed metadata snapshot, NOT an ongoing subscription
  or an external-on-disk file watch. Other raw pong metadata is still a last-confirmed
  cache and must not be confused with the authoritative `/name` read.

Verification snapshot — pass 123:

- TWO actual UI peers observe one another's rename, both cold/no-Agent and cached
  Agent owners; held journal commit/rollback, responsive ping/escape/withdrawal/
  disconnect, stale-owner protection, forged/noncanonical/late receipts, old/boolean
  negotiation, eight-job capacity/cancellation, and an ACTUAL active offline turn
  retaining its reservation/timer through read cancellation; prior native/extension
  name/branch/navigation/command regressions: **52 passed** (21.38 s),
  `/tmp/pass123-final.log`.
- SEVEN production/private-tmux paths: NEW Claw current gateway name after a remote
  owner update without any UI push, then pending read → resize → Escape → retained
  name/native editor/parent/termios/scrollback recovery; plus BOTH-client ordinary
  naming/search and reviewed extension name success/failure paths: **7 passed**,
  **201 deselected** (16.82 s), `/tmp/pass123-terminal.log`. Offline peers only, no
  paid model/login or user-daemon restart; no parser/focus/approval gate relaxed.
- Diagnostic note: the first expanded offline-turn fixture yielded DoneEvent but
  failed to resolve its EventStream final-result future. It therefore failed turn
  settlement and held teardown. Authoritatively interrupt ONLY that owned pytest
  process, then fix the fixture to supply its actual final result; no production
  runtime/test timeout or permission policy weakened. An isolated wire check then
  **1 passed** (0.83 s), `/tmp/pass123-wire-debug.log`; the final group above verifies
  complete owned turn/read cleanup. Earlier interrupted run is NOT green evidence.
- Final repository Ruff / all-seven strict mypy / diff whitespace evidence follows.
  No new full-root or native-desktop/full-parity claim. No dependency/lockfile edits,
  commits/resets/stashes or unrelated changes overwritten. Goal ACTIVE; original
  broader host/provider/auth/MCP/credential/native-platform requirements remain open.
  OS-enforced sandboxing remains excluded.
- Final repository Ruff clean: `/tmp/pass123-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **451 source files**: `/tmp/pass123-types-done.log`;
  `git diff --check` clean.

## Pass 124 — failed extension withdrawal and owning-loop/UI cancellation consistency

- Previous turn made verified progress. Inspect a concrete runtime/UI gap from the
  name-error work: failed workers remained enumerable through `tools()` and parent
  command error paths skipped projection/catalog refresh. Retained executor guards
  denied those calls, but inspection/menus could still describe dead declarations.
- Enumerate only current connected, owner-matching live tool registrations. Preserve
  historical aliases and original validator/executor guards so withdrawal cannot
  grant an old approval, route to a replacement callback or waive mandatory hooks.
- Settle projection in owning parent command/input/context/pre/post/initialization/
  turn boundaries, including errors/cancellation. Never mutate Agent state on the
  worker reader thread, initialize a fallback provider, or auto-unload/restart a
  failed worker. Keep other workers/native definitions and policy/history ownership.
- Apply the same failure-boundary projection to child callbacks, with explicit
  `spec.tools` filtering: a refresh cannot re-add tools omitted by the child profile.
  Verify real admitted supervised-child context failure/cleanup while its parent
  worker stays healthy; no parent validation or permission mode is bypassed.
- Coding failure/cancel command receipts carry the current same-owner validated
  catalog. Claw sends an explicitly versioned, same-session descriptive failure
  snapshot for a known invoking owner; UI refuses malformed/foreign metadata and
  never uses it to adopt a new session or change provider limits. No replay.
- Add Claw's missing native Escape cancellation for owned extension controls with
  independently advertised `extension_command_cancel: 1`. Pin original request/
  group/session (including empty bootstrap leases). Settle actual worker cleanup
  and emit a cancellation/error receipt before clearing UI pending state. Handle
  even cancellation BEFORE task admission: return the receipt and release only its
  original group reservation. Most-specific dialog/overlay cancellation remains first.
- Strengthen the older name-error regression to expect failed-tool withdrawal,
  while retaining its unchanged name/model/history/plan/child/permission checks.
  This intentionally changes obsolete tool enumeration, not its safety guards.

Verification snapshot — pass 124:

- Actual worker/tool filtering, retained old guards, selected-subset refresh; BOTH
  parent clients' command/cancel/tool/pre/post/context/input/event failure paths,
  same-loop projection, healthy sibling preservation, post-error actual result,
  real supervised child failure/subset/parent isolation; native name/command,
  extension lifecycle/runtime/child and permission regressions: **103 passed**
  (71.78 s), `/tmp/pass124-final.log`.
- Final descriptive-failure scope/invalid/foreign metadata, original bootstrap
  cancel lease and coalesced wire cancellation BEFORE admission/receipt/reservation
  release: **7 passed** (0.75 s), `/tmp/pass124-cancel-scope.log` (six cases overlap
  the broader run; pre-admission wire case is additional).
- FOUR production/private-tmux paths: BOTH clients fail or cancel actual command
  workers → native `/tools` search finds zero retired registrations → native
  editor/parent/termios/scrollback recovery: **4 passed**, **208 deselected**
  (12.69 s), `/tmp/pass124-terminal.log`. No model calls for command/catalog work,
  paid model/login or user-daemon restart. No parser/focus/approval gate relaxed.
- Final repository Ruff/all-seven strict mypy/diff whitespace evidence below.
  No full-root/new desktop-platform/parity claim; no dependency/lockfile edits,
  commits/resets/stashes or unrelated changes overwritten. Goal remains ACTIVE;
  broader host/provider/auth/MCP/credential/native-platform requirements stay open.
  OS-enforced sandboxing remains excluded.
- Final repository Ruff clean: `/tmp/pass124-ruff-last.log`; strict ALL SEVEN
  namespaces clean across **451 source files**: `/tmp/pass124-types-last.log`;
  `git diff --check` clean.

## Pass 125 — Coding integration continuation and fresh offline distribution evidence

- Previous turn made verified progress. Run a broader Coding checkpoint excluding
  the already separately tested production terminal suite. It stops at **1 failed,
  342 passed** (196.49 s), `/tmp/pass125-coding.log`: a quiet-poll test bypassed
  `InteractiveMode.__init__` and omitted current saved-source/view ownership state.
- Replace that synthetic partial UI with an actual constructed SDK session/UI and
  fail-on-model-call adapter. Exercise the real polling, footer activity and render
  path: TWO unchanged busy frames cause no activity/reset/render, a real elapsed
  label change causes EXACTLY one activity update/render, and session/draft/deny
  policy remain unchanged. Do not add production `getattr` defaults to hide invalid
  objects, weaken polling, or merely populate another growing bag of fake fields.
- Exact stopped-node continuation under explicit root CI configuration then stops
  at a SECOND old fixture assumption: **1 failed, 215 passed, 1 skipped, 342
  deselected** (49.54 s), `/tmp/pass125-remainder-final.log`. The unresolvable-model
  test's handmade Agent had no owning `supports_tool_calls` capability property.
- Replace it with actual Agent/AgentSession objects and a NONE/FALSE/TRUE capability
  matrix, retaining unknown-provider resolution plus actual prompt-tool visibility:
  unknown/false describe tools; explicit true avoids duplication. Inspection cannot
  invoke a model. Preserve model routing/capability safety rather than restoring
  global-provider lookup or guessing a default capability in production.
- Correct pytest configuration/collection evidence: direct package-path pytest can
  select its package-local pyproject (node IDs start with `tests/`). Further runs
  explicitly use `-c pyproject.toml` from the repository root. Temporary in-process
  collection plugins validate unique exact stopped nodes and deselect ONLY the
  already-observed prefix; nothing is installed or written into the repository.

Verification snapshot — pass 125:

- Root CI configuration prefix through the fixed poll test: **343 passed**, **314
  deselected** (195.71 s), `/tmp/pass125-root-prefix.log`. Exact capability-node tail
  including new matrix: **99 passed**, **558 deselected** (47.14 s),
  `/tmp/pass125-last-segment.log`. Middle continuation above contributes **215
  passed, 1 skipped**, with ONE quiet-poll overlap with the reverified prefix.
  Combined Coding non-terminal collection evidence: **656 distinct passed, 1
  skipped** over **657 collected** cases. This is explicitly SEGMENTED evidence,
  not a single-process full-root/full-workspace green run.
- Both changed fixture files: **77 passed** (0.94 s),
  `/tmp/pass125-fixture-final.log`; actual interactive file alone **45 passed**
  (0.86 s), `/tmp/pass125-interactive-final.log` (overlapping evidence).
- Fresh cached offline wheel AND source builds for Agent/TUI/Coding/Claw:
  `/tmp/pass125-build.log`, artifacts `/tmp/pass125-wheels`. Verify ALL **321 Python
  source files** included in BOTH artifact types and SEVEN representative extracted-
  wheel imports resolve from those artifacts, including new name, extension failure,
  supervision, timeline and gateway code: `/tmp/pass125-packaging.log`. No dependency
  install/download or manifest/lock edit needed; no stale pass-120 artifact claim.
- Shipped workflow examples: **17 passed** with `--allow-script-stub`,
  `/tmp/pass125-examples.log`; offline stubs only, not hosted/live model evidence.
- External primary-reference refresh could not supply usable content; direct HTTPS
  fetch reset by peer and produced ZERO bytes, `/tmp/pass125-reference-fetch.log`.
  Do not infer current Codex/Pi API availability from an unopened/unavailable page.
  Broader comparison requirements are not waived or declared implemented from that
  failed fetch. Local command registries remain observable for future focused audit.
- Final Ruff/all-seven strict mypy/diff whitespace evidence below. No production
  runtime edits, native-terminal rerun or user-daemon restart in this verification
  pass. Preserve prior dirty changes in both fixture files; no commit/reset/stash,
  dependency/lockfile changes or unrelated work overwritten. Goal ACTIVE; full-root
  integration and broader host/provider/auth/MCP/credential/native-platform completion
  requirements remain open. OS-enforced sandboxing remains excluded.
- Final repository Ruff clean: `/tmp/pass125-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **451 source files**: `/tmp/pass125-types-done.log`;
  `git diff --check` clean.

## Pass 126 — native live-menu help and filtering in both actual clients

- Previous turn made verified progress. Inspect the next concrete discovery gap:
  Claw did not register/dispatch `/help`; Coding's generic help omitted UI-only
  controls and current extension registrations even though completion exposed them.
  Implement native `/help [QUERY]` in BOTH interactive clients from their actual
  current completion provider, not a second drifting list or a model request.
- Include current native/UI/skill/registered-extension entries where present in
  that app's menu; normalize optional leading slash and sort consistently. Search
  names/descriptions case-insensitively. Explicit unload/withdrawal affects the next
  help snapshot; do not load/activate/replay commands to discover them. Coding's
  headless help retains the separate Skills section, query and known extensions.
- Treat metadata as inert read-only text. Reject unsafe names/queries/conflicting
  entries; flatten sanitized descriptions, retain complete long rows/all matching
  entries, and fail an excessive menu explicitly instead of claiming a truncated
  view is complete (10k entries/1 MiB total/8 KiB description/256-byte query guards).
  No permission, model, draft/context or active-run timer changes from help.
- Scope Claw's help correctly: it is the current TUI completion-menu snapshot,
  including commands whose remote availability is checked at invocation. It is NOT
  an authoritative new gateway command API or an implied registration on old peers.
  External current Codex/Pi comparison remains unverified; no unavailable-source claim.

Verification snapshot — pass 126:

- Shared help normalization/full rows/filter/no-match/controls/conflicts/capacity;
  BOTH actual registered command runtimes/help/menu/explicit unload/callback counters,
  retained draft/permissions/Agent/model/history/run timers; exact-command, headless
  skills/help, editor/command/render and Claw inline regressions: **149 passed**
  (5.37 s), `/tmp/pass126-done.log` (explicit root CI pytest configuration).
- TWO production/private-tmux paths: native filtered help → registered callback
  counters STILL ZERO → UI-only `/copy` discovery → no-match → resize → native
  editor/parent/termios/scrollback restoration: **2 passed**, **212 deselected**
  (5.52 s), `/tmp/pass126-terminal.log`. No model call, paid model/login or user
  daemon restart; no focus/parser/approval gate relaxed.
- New shared helper ships in BOTH fresh cached offline TUI wheel and source build:
  `/tmp/pass126-build.log`, `/tmp/pass126-package-inspect.log`; no dependency install
  or manifest/lockfile change. This is not a fresh all-four-package build claim.
- Final Ruff/all-seven strict mypy/diff whitespace evidence below. No root-wide
  test rerun/desktop-platform/goal-wide parity claim. No commit/reset/stash or
  unrelated changes overwritten. Goal ACTIVE; broader host/provider/auth/MCP/
  credential/native-platform requirements remain open. OS sandboxing excluded.
- Final metadata hardening rejects bidi/format/surrogate controls in command names
  and queries and strips invisible formatting from inert descriptions. Final shared
  bounds + both-client help group: **17 passed** (2.48 s),
  `/tmp/pass126-bounds-final.log` (overlaps prior cases plus two format-control cases).
- Final repository Ruff clean: `/tmp/pass126-ruff-clean.log`; strict ALL SEVEN
  namespaces clean across **452 source files**: `/tmp/pass126-types-last.log`;
  `git diff --check` clean. Final TUI artifact rebuild after that hardening succeeded:
  `/tmp/pass126-build-last.log`; no stale pre-hardening artifact claim.

## Pass 127 — whole-workspace checkpoint, navigation loading/readiness and completion audit

- Previous turn made verified progress. Start ONE owned root CI run, keep the
  same handle through observation timeouts, and do not edit production source
  while it runs. Authoritative terminal result: **1 failed, 4,100 passed, 29 skipped**
  (1151.51 s), `/tmp/pass127-workspace.log`. Do NOT label that a green root run.
- Add `agent-ui-completion-audit.md`: map the original requirements to actual
  app/runtime/artifact evidence, distinguish implemented Linux paths from missing
  native-platform/current-comparison evidence, and retain concrete remaining MCP/
  native credential storage/host/rendering/input/provider requirements. Existing
  plaintext credential review is not a native keychain or a sandbox. No requirements
  are waived merely because recent UI slices/tests passed.
- Investigate the exact native recovery failure, not another full-root restart.
  Targeted reproduction: **1 failed, 3 passed** (19.68 s),
  `/tmp/pass127-recovery-repro.log`. Owned temporary trace proves physical PRESS
  Enter reached a new focused NavigationPanel with ZERO entries while an older
  native-scrollback tree still displayed six entries. No parser or approval issue
  is guessed: failed activation correctly did not apply unseen/empty history.
- Correct shared initial navigation state: request the read THEN capture its actual
  snapshot. A newly opened view immediately represents pending/loading, not a false
  empty-ready result; synchronous feeds retain their actual ready result. Do not
  weaken Enter's physical-press/focus/loaded-view requirements or activate a pending
  target to make the test pass.
- Terminal recovery now waits for actual source-viewer/editor/new-navigation cursor
  transitions and the LAST/current tree header, not any older identical scrollback
  line. Keep the explicit enhanced-key physical Enter and full recovery/policy checks.
  Restore all owned temporary tracing changes from pre-trace bytes that included
  the prior worktree; no unrelated fixture changes overwritten or trace hooks shipped.

Verification snapshot — pass 127:

- Native/source/journal/session/staging/compaction/navigation and new pending/ready
  constructor tests across both apps: **44 passed** (35.80 s),
  `/tmp/pass127-navigation-final.log`.
- FOUR production/private-tmux compaction → saved view/search → fresh recovery tree
  → actual branch restore → native parent/termios/scrollback paths (both clients,
  ordinary and extension compactor): **4 passed**, **210 deselected** (10.73 s),
  `/tmp/pass127-recovery-fixed.log`. Existing third-party aiosqlite deprecation
  warning is not hidden. Offline selected-adapter peers only; no paid model/login
  or user daemon restart; no focus/permission/confirmation/parser guard relaxed.
- Exact full-root stopped-node continuation (unique validated collection boundary,
  explicit root configuration): **857 passed**, **4,129 deselected** (119.97 s),
  `/tmp/pass127-remainder.log`, including the corrected case and the later packages.
  Original prefix + fixed-node remainder: **4,957 passed, 29 skipped** as SEGMENTED
  evidence (two new navigation cases account for the collection increase). The
  shared constructor change also has the affected prefix's targeted navigation/
  staging coverage above; this remains NOT an atomic fresh full-root green run.
- Final Ruff/all-seven strict mypy/diff whitespace evidence below. No dependency/
  lockfile edit, commits/resets/stashes or unrelated work overwritten. Goal ACTIVE;
  the new completion audit explicitly keeps every still-unproven requirement open.
  OS-enforced sandboxing remains excluded.
- Final repository Ruff clean: `/tmp/pass127-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **452 source files**: `/tmp/pass127-types-done.log`;
  `git diff --check` clean. Temporary terminal bootstrap trace bytes were verified
  restored exactly against their owned pre-diagnostic snapshot.

## Pass 128 — scoped public extension command-menu metadata in both hosts

- Previous turn made verified progress. Inspect a concrete local host API gap in
  the completion audit and implement `api.get_command_catalog(query, offset,
  revision)` through the actual worker/broker and BOTH current terminal menus.
  Native help alone is not a public extension accessor; no upstream API is guessed.
- Interactive parent COMMAND callbacks inspect sanitized normalized native/UI/
  currently registered extension/skill menu metadata where present. Get the SAME
  current menu normalizer used by `/help`, not a second executable registry.
  Explicit source `terminal-menu` is metadata, NOT approval or a promise of remote
  availability. No activation/discovery/model/tool work is started by enumeration.
- Bound menu/page/query bytes/counts, exact copied fields, normalized names, no
  controls/nonfinite/coerced pagination and 32-entry pages. Preserve ALL matches
  across pages. Full metadata + original command/context/session owner fingerprints
  revisions; stale owner/menu revision returns NO partial data/no next page. Caller
  may explicitly restart; another invocation cannot reuse the original revision.
- Validate original session in the actual worker API as well as frontend/broker
  ownership. Reject caller-selected session/branch/host-action fields. Strip ONLY
  independently added host dialog tags at the UI service boundary; do not let a
  trusted host's session tag masquerade as a caller-selected navigation target.
  Headless/tools/independent children cannot borrow interactive parent-menu authority.
- Claw independently negotiates `extension_command_catalog: 1`; absent/boolean/
  old viewers get unavailable and no guessed dialog, no fallback to session/tree/
  model/name metadata. Keep original callback/session/connection fencing and no
  replay. All other API/render/provider/auth/native-platform requirements stay open.

Verification snapshot — pass 128:

- Strict menu paging/filter/copy/stale/foreign session/coercion/action contracts;
  actual isolated worker read/headless/tool denial; BOTH current UI/Unix-gateway
  callbacks traverse >32 native/extension entries, filtered `/copy`, last registered
  sample entry, zero command callback execution and retained Agent/model/history/
  draft/policy; independent older/boolean viewer denial, existing session/tree/
  tool/command/help contracts: **105 passed** (29.46 s),
  `/tmp/pass128-regressions-done.log` (explicit root CI configuration).
- Final focused ownership/menu group: **24 passed** (6.64 s),
  `/tmp/pass128-scopes.log` (overlapping evidence).
- TWO production/private-tmux paths: registered command → all menu pages → zero
  execution counter → native/UI `/copy` metadata → filtered one-entry page → resize
  → native editor/parent/termios/scrollback restore: **2 passed**, **214 deselected**
  (6.77 s), `/tmp/pass128-terminal-final.log`. Offline peers only; no paid model/login
  or user-daemon restart; no parser/focus/approval/permission gate relaxed.
- Fresh cached offline Agent wheel/source build includes the new command-catalog
  module in BOTH artifact types, `/tmp/pass128-build.log`, `/tmp/pass128-wheels`.
  No new dependency/manifest/lockfile changes or full-workspace restart.
- Final Ruff/all-seven strict mypy/diff whitespace evidence below. Goal ACTIVE;
  the completion audit now distinguishes this implemented parent accessor from
  still-missing argument completion and other original gaps. No complete parity,
  native desktop, headless-command/menu mutation or execution API claim.
  OS-enforced sandboxing remains excluded; unrelated changes preserved.
- Final repository Ruff clean: `/tmp/pass128-ruff-last.log`; strict ALL SEVEN
  namespaces clean across **453 source files**: `/tmp/pass128-types-last.log`;
  `git diff --check` clean. Fresh Agent artifacts were additionally inspected for
  actual new-module membership rather than assuming a successful build includes it.

## Pass 129 — registration-bound argument callback/runtime foundation (UI remains OPEN)

- Previous turn made verified progress. Inspect the explicit remaining argument
  completion gap and implement its necessary worker/runtime layer, preserving the
  entire requested UI/host objective. This is an intermediate foundation, NOT a
  substitute for the actual editor/transport integration and NOT a gap-closure claim.
- `api.command(..., complete=callback)` registers an independent data callback with
  an opaque per-registration identity. Existing command declarations stay identical
  when no completer exists. Owned replacement/removal advances live metadata;
  stale/old disposers cannot withdraw replacement callbacks.
- Dispatch `xdog/command_complete` separately from command execution. Strict prefix/
  item/result bounds, plain Unicode/no controls/bidi, unique values and exact data
  fields prevent reinterpretation. Maximum 32 items/64 KiB output, bounded per-field
  and prefix sizes; callback metadata is never command/model execution authority.
- Runtime lookup does not initialize/activate workers or invoke command callbacks.
  Revalidate identity before/inside/after callback; in-flight replacement returns no
  stale data. No interactive dialog/editor/navigation/name mutation authority is
  granted to completers. Deadline/error cancellation settles and reaps uncertain
  workers rather than replaying or leaving an unknown callback running.
- Both Coding/Claw SDK session wrappers query registered completion data and settle
  the owning Agent's tool projection on error/cancellation. Verify these actual
  client sessions without changing Agent/model/history/draft/permission ownership.
- KEEP OPEN: cursor-local argument spans, coalesced background requests, stale editor
  draft/cursor/catalog fencing, focus/Tab/Enter no-submit behavior, Claw negotiated
  editor RPC/cancel and full physical argument-selection paths. Backend passing tests
  do not prove any of those missing UI requirements. No status row is waived.

Verification snapshot — pass 129:

- Actual process-owned callbacks, lifecycle identity/replacement/old disposer/
  withdrawal, strict input/output contracts, in-flight registration change/no replay,
  deadline/invalid-result process cleanup; BOTH actual client SDK completion reads,
  denied host UI/mutation access and zero command/model callback execution; prior
  command/shortcut/menu/failure lifecycle and owning-session regressions:
  **91 passed** (38.80 s), `/tmp/pass129-final.log` (explicit root CI configuration).
- Focused BOTH-client/backend group: **22 passed** (4.13 s),
  `/tmp/pass129-sessions.log`; command/shortcut foundation group **66 passed**
  (5.27 s), `/tmp/pass129-runtime-final.log` (overlapping evidence).
- FOUR existing production/private-tmux dynamic command/shortcut replacement/
  disposal and exact command-versus-neighbor/native parent recovery paths:
  **4 passed**, **212 deselected** (9.25 s), `/tmp/pass129-terminal-regression.log`.
  These are compatibility evidence ONLY, not new argument-selection acceptance.
  Offline peers only; no paid model/login/user-daemon restart or weakened guards.
- Fresh cached offline Agent wheel AND source build includes the new argument
  completion module in BOTH artifact types: `/tmp/pass129-build.log`,
  `/tmp/pass129-wheels`. No dependency/manifest/lockfile edits, full-root restart,
  commits/resets/stashes or unrelated changes overwritten.
- Final Ruff/all-seven strict mypy/diff whitespace evidence follows. Goal ACTIVE;
  UI integration and all original broader requirements remain open. OS-enforced
  sandboxing remains excluded.
- Final repository Ruff clean: `/tmp/pass129-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **454 source files**: `/tmp/pass129-types-done.log`;
  `git diff --check` clean. Both Agent artifacts were inspected for actual new-module
  membership; the absence of interactive editor integration remains explicit.

## Pass 130 — parent-editor argument selection/coalescing and actual Claw transport

- Previous turn made verified progress. Extend the pass-129 registration foundation
  into BOTH actual parent editors and owning Claw gateway; do not count backend-only
  tests as UI completion. Shared feed represents one in-flight query plus ONE latest
  draft, not per-keystroke threads/remote jobs. UI reads are nonblocking and data-only.
- Parse cursor-local argument tokens/right suffix/quoted whitespace without shell/
  filesystem expansion; preserve surrounding arguments. Callback-supplied replacement
  is literal. Tab/Enter inserts ONLY and keeps undo; pending Enter cannot accidentally
  submit a command. Escape dismisses late data, explicit Tab reopens. Native file/
  slash-name completion retains its existing paths and exact-command intent rules.
- Scope results and insertion to original session/context, exact registration ID,
  full draft/cursor and current focused idle parent editor. Changed/replaced/withdrawn
  registrations or foreign/reconnected/unavailable peers cannot apply stale data.
  Coding transports frozen request/data events, never mutable session objects across
  its UI event queue. Worker thread does NOT project Agent tools; receipt projection
  occurs only on the owning idle UI side. No model/history/permission mutation.
- Claw independently negotiates exact `extension_argument_completion: 1`; own bounded
  request jobs reserve only the actual idle group, avoid session activation, and use
  the current cached session's runtime callback. Existing session/command capabilities
  cannot substitute. Scope cancellation support to original connection/group/session;
  before-admission cancellation must settle its receipt and reservation separately.
  Reset client feed/receipts on disconnect so lost outcomes are not replayed.
- CURRENT LIMITS remain OPEN: Escape editor dismissal quarantines results but does
  not yet issue immediate worker cancellation; the one-second runtime deadline owns
  eventual callback cleanup. Richer label/suffix semantics, independent child prompt
  completion and broader argument API interoperability are unproven. Do NOT claim
  complete original argument-cancellation parity from basic selections or a parsed
  cancel control. These are explicit next integration requirements, not scope waivers.

Verification snapshot — pass 130:

- Shared quoted/cursor-local spans, no-submit pending/selected Enter/Tab/undo,
  coalesced latest requests and owner/cursor/catalog/reset/dismiss stale fencing;
  actual BOTH-client editor queries/selection with zero execution/model side effects,
  exact separate Claw legacy/boolean/disconnected denial; runtime registration/
  lifecycle and prior shared prompt/file/command/transport regressions:
  **146 passed** (11.13 s), `/tmp/pass130-regressions-done.log` (root CI configuration).
- Final focused feed/editor/Claw scope group: **12 passed** (2.21 s),
  `/tmp/pass130-scopes.log`; final quoted/editor group **8 passed** (2.09 s),
  `/tmp/pass130-spans.log` (overlapping evidence).
- TWO production/private-tmux paths: actual registered argument callback → Tab
  insertion → another prefix → physical Enter insertion ONLY → actual execution
  counter still ZERO → resize → native editor/parent/termios/scrollback recovery:
  **2 passed**, **216 deselected** (5.20 s), `/tmp/pass130-terminal-final.log`.
  Offline peers only; no paid model/login or user-daemon restart; no focus/parser/
  permission/confirmation gate relaxed. This proves basic parent argument selection,
  NOT immediate cancellation, arbitrary native platform or entire goal parity.
- Final repository Ruff clean: `/tmp/pass130-ruff-final.log`; all-seven strict mypy
  clean across **455 sources**, `/tmp/pass130-types-final.log`; diff whitespace clean.
  No full-root restart, package/dependency/lock edits, commits/resets/stashes or unrelated
  changes overwritten. Goal ACTIVE; original broader requirements remain open and
  OS-enforced sandboxing stays excluded.

## Pass 131 — immediate owned argument cancellation and settled Coding exit

- Previous turn made verified progress. Close the explicitly retained immediate
  cancellation gap from pass 130 in BOTH actual editors, not only by hiding late
  data or shortening a timeout. Shared feed accepts an owning cancel adapter,
  cancels once on dismissal/invalid context/blur/hidden view/close, and retains its
  in-flight slot until the real settlement receipt. Coalesced later work cannot
  race into a still-cleaning-up owner; repeat dismiss/close does not repeat cancel.
- Coding owns each asynchronous completion loop/task/thread with synchronized
  pre-admission cancellation intent. Cancel the actual running task thread-safely,
  settle its process bridge and publish ONLY frozen metadata. No UI event contains
  the mutable task or session. Preserve UI-side idle tool projection and draft/Agent/
  model/history/permission identity; no model/command callback runs on cancellation.
- A physical exit test exposed manager retirement racing the canceled completion
  thread. Defer session disposal until its completion cancellation settles; join owned
  threads outside the terminal after restoration rather than blocking the Escape/UI
  reader. This is actual cleanup ownership, not a relaxed terminal timeout/guard.
- Claw independently negotiates exact `extension_argument_cancel: 1`. Original
  request/group/session/connection scopes carry cancel, never whichever head happens
  to be current now. Duplicate cancellation is idempotent through cleanup. Handle
  before-task-admission receipt/reservation release without activating an Agent;
  settled cancellation returns validated descriptive failure metadata so a reaped
  worker is not still advertised in the editor. Older/boolean peers receive no
  guessed control and retain existing bounded callback deadlines.
- Validate opaque completion IDs at the shared editor boundary too: malformed/
  non-data catalog entries cannot dispatch new worker queries. This also prevents
  synthetic mocks lacking a real registration from spawning invalid awaitables.
  Actual current command registrations are still the authoritative contract.
- Keep broader labels/suffixes/argument interoperability, independent child completion
  and original host/provider/auth/MCP/credential/native-platform requirements OPEN.
  No invisible cancellation is presented as rollback of arbitrary trusted plugin
  side effects; uncertain workers are reaped, not replayed or automatically reloaded.

Verification snapshot — pass 131:

- Actual BOTH-client explicit Escape/repeat, focus-loss and exit cancellation with
  process reaping and withdrawn menus; test callbacks use a five-second deadline
  ONLY to distinguish explicit cancellation from timeout and assert actual task
  cancellation reached cleanup. Coalesced feed slot/once-only cancel/foreign/malformed
  identity, Claw legacy boolean denial/original-head cancellation/before-admission
  wire ownership, and prompt/command/runtime/transport regressions:
  **137 passed** (10.72 s), `/tmp/pass131-final-clean.log` (root CI configuration,
  no unhandled-thread warning). Final scope subset **21 passed** (6.44 s),
  `/tmp/pass131-scopes-final.log` (overlapping earlier cases).
- SIX production/private-tmux paths: BOTH-client normal Tab/Enter insertion-only;
  actual held callback Escape or exit reaches explicit owning-task cancellation,
  cleanup receipt/process withdrawal and retained/native parent/termios recovery:
  **6 passed**, **216 deselected** (13.06 s), `/tmp/pass131-terminal-final.log`.
  Opt-in offline fixture extends only the callback deadline and records explicit
  CancelledError AFTER actual bridge cleanup; no production cancellation/approval/
  focus/parser policy weakened. Zero model calls, no paid model/login/user daemon
  restart. The previous native Coding exit failure is not green evidence.
- Final repository Ruff clean: `/tmp/pass131-ruff-done.log`; all-seven strict mypy
  evidence and offline artifact checks below. Diff whitespace clean. No root-wide
  restart, dependencies/manifests/lock edits, commit/reset/stash or unrelated changes
  overwritten. Goal ACTIVE; OS-enforced sandboxing remains excluded.
- Final repository Ruff clean: `/tmp/pass131-ruff-clean.log`; strict ALL SEVEN
  namespaces clean across **455 sources**: `/tmp/pass131-types-last.log`;
  `git diff --check` clean. A loop-variable-only type correction preserves the
  verified shutdown behavior; no test or production ownership guard weakened.
- Fresh cached offline TUI/Coding/Claw wheels AND source builds succeeded:
  `/tmp/pass131-build.log`, `/tmp/pass131-build-coding-last.log`, `/tmp/pass131-wheels`.
  Cancellation owner module membership in BOTH artifact types was inspected; this
  does not claim an all-four-package rebuild or native desktop runtime evidence.

## Pass 132 — argument display labels versus insertion identity in both editors

- Previous turn made verified progress. Inspect the callback/item/editor pipeline:
  registered callbacks already return labels, but shared feed discarded them and
  the select-list rendered insertion strings only. Preserve supplied labels through
  BOTH actual editors/socket/worker paths rather than extending only the data helper.
- Keep insertion value and presentation label separate. Display distinct labels
  with bracketed insertion identity and descriptions; duplicate labels are allowed,
  while duplicate values remain invalid. Up/Down/Tab/Enter/undo and retained selection
  use actual values, never the label text. Unicode/narrow menus retain shared bounds
  and existing no-submit behavior. Label-only updates request repaint without moving
  the selected value; slash/file paths retain their default value presentation.
- Independently validate complete UI metadata including label/description types,
  controls/bidi/surrogates, byte/item bounds and insertion uniqueness. Invalid data
  refuses the WHOLE result, no partial guessed menu. This stricter check exposed
  Coding's completion event handler running before its usual frozen-event thaw:
  use the existing owned-copy conversion before feed validation. Never relax the
  immutable cross-thread event queue to pass a mutable callback dictionary.
- Apply the existing exit guard BEFORE completion events too: retired UI owners
  cannot project snapshots or request paints after application exit. Keep actual
  completion cancellation/process/registration cleanup and permission/run ownership.
- No argument schema/suffix mutation, arbitrary execution, upstream conformance or
  independent-child menu claim. Richer suffix semantics, broader argument/host/
  provider/auth/MCP/credential/native-platform requirements remain OPEN.

Verification snapshot — pass 132:

- Actual BOTH-client callbacks/menus display distinct Unicode and duplicate labels,
  select the second duplicate's true insertion value, and keep execution/model counters
  at zero; shared narrow/label-only repaint/selection/invalid-whole-result, frozen
  queue, cancellation/owner/command/help/slash/prompt regressions:
  **126 passed** (9.99 s), `/tmp/pass132-final.log` (root CI configuration).
- SIX production/private-tmux paths: BOTH clients' Tab/Enter normal insertion, duplicate
  Unicode labels → explicit second item → exact `release-2` editor value without
  execution; plus held callback Escape/exit cancellation, resize, native parent/
  termios/scrollback recovery: **6 passed**, **216 deselected** (14.23 s),
  `/tmp/pass132-terminal.log`. Offline peers only, zero completion execution/model
  calls, no paid model/login or user-daemon restart. No parser/focus/approval guard
  relaxed; strict metadata validation is strengthened, not weakened.
- Earlier label run correctly failed on Coding's frozen row type; focused diagnostic
  `/tmp/pass132-select-debug.log` identified unavailable metadata rather than guessing
  a timeout/focus problem. Fix the real owned-copy boundary; the final group above
  verifies the actual row pipeline. Do not count the failed intermediate run as green.
- Repository Ruff clean: `/tmp/pass132-ruff-third.log`; strict ALL SEVEN namespaces
  clean across **455 sources**: `/tmp/pass132-types-third.log`; diff whitespace clean.
  Fresh offline TUI/Coding artifact evidence below. No full-root restart, dependency/
  manifest/lock edits, commit/reset/stash or unrelated changes overwritten.
  Goal ACTIVE; OS-enforced sandboxing remains excluded.
- Final repository Ruff clean: `/tmp/pass132-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **455 sources**: `/tmp/pass132-types-done.log`;
  `git diff --check` clean. Fresh cached offline TUI/Coding wheel AND source builds:
  `/tmp/pass132-build.log`, `/tmp/pass132-wheels`. Three affected implementation
  modules were compared BYTE-FOR-BYTE with current source in BOTH artifact types:
  `/tmp/pass132-artifacts.log`. No broader artifact/platform claim.

## Pass 133 — context-dependent later-argument completion in both actual clients

- Previous turn made verified progress. Inspect a concrete limitation in existing
  callback wiring: only the token prefix reached the callback, so it could not use
  earlier arguments to distinguish a later one. Add copied bounded argument context
  through the same actual worker/runtime/SDK/editor and Claw gateway paths.
- `context['argument_context']` contains only captured argument body plus cursor/
  token-start/token-end Unicode code-point offsets RELATIVE to that body, excluding
  the slash-command/host namespace. Validate exact fields/ints/ranges/plain text/byte
  caps and prefix equality at runtime, worker and gateway boundaries. Mutation in
  callback stays process-local; never rewrite editor data or canonical Agent history.
- Host keeps authoritative original span/session/draft/cursor/registration ownership.
  The same `en` prefix under `production` versus `staging` now yields different
  results, including actual right-suffix replacement with surrounding arguments
  preserved. No new command execution/model/tool/permission/UI authority granted.
- Both SDK wrappers accept optional `argument_input`. Existing prefix-only queries
  still work and explicitly expose context `None`, NOT a fabricated full draft.
  Claw independently negotiates exact `extension_argument_context: 1`; ordinary
  completion/name/command capability does not substitute. Unsupported rich payloads
  are refused before callback admission; old prefix-only queries retain support.
- Withdrawn context support cancels captured contextual jobs and quarantines late
  results, without automatically replaying/downgrading the same request. Shared
  owned cancel helper now settles pre-admission receipts on capability revocation as
  well as explicit cancel. No phantom Agent activation or stolen group reservation.
  A fresh user edit/Tab may explicitly request remaining prefix-only functionality.
- Keep labels/values/no-submit/undo/coalescing/cancellation safety and ordinary editor
  text behavior. Richer suffix semantics and broader argument/host/provider/auth/MCP/
  credential/native-platform requirements remain OPEN; no upstream API parity claim.

Verification snapshot — pass 133:

- Exact copied metadata/scalar and coordinate/prefix/bounds/refused host-field tests;
  actual callback reads earlier argument and cannot mutate supplied input; BOTH live
  editors/Unix gateway later-token/right-suffix/Tab/Enter no-submit; legacy/boolean
  capability denial and no silent withdrawal downgrade; prior label/queue/editor/
  cancellation/runtime/slash/help/focus/policy tests: **133 passed** (12.18 s),
  `/tmp/pass133-final.log` (explicit root CI configuration).
- Final scoped context group including actual COALESCED context-capability withdrawal
  BEFORE job startup/no activation/owned reservation release: **45 passed** (5.06 s),
  `/tmp/pass133-scope-last.log` (overlapping earlier tests plus final wire case).
- SIX production/private-tmux paths: BOTH clients' normal/Unicode-label/cursor argument
  selections plus SAME `en` prefix under two preceding choices yields correct
  production/staging value without execution; held callback Escape/exit, resize,
  native parent/termios/scrollback recovery: **6 passed**, **216 deselected** (14.70 s),
  `/tmp/pass133-terminal.log`. Zero completion execution/model calls, no paid model/
  login or user-daemon restart; no parser/focus/approval guard relaxed. Offline fixture
  cancellation observer now forwards the optional input context instead of masking it.
- Final Ruff/all-seven strict mypy/diff whitespace and fresh offline distribution
  evidence below. No full-root restart or dependency/manifest/lock edits; no commit/
  reset/stash or unrelated work overwritten. Goal ACTIVE; OS sandboxing excluded.
- Final repository Ruff clean: `/tmp/pass133-ruff-final.log`; strict ALL SEVEN
  namespaces clean across **455 sources**: `/tmp/pass133-types-final.log`;
  `git diff --check` clean. Fresh cached offline Agent/TUI/Coding/Claw wheels AND
  source builds succeeded: `/tmp/pass133-build.log`, `/tmp/pass133-wheels`. Five
  affected implementation modules match current source BYTE-FOR-BYTE in BOTH
  artifact types: `/tmp/pass133-artifacts.log`. No native-platform parity claim.

## Pass 134 — refreshed primary reference baseline and canonical Coding RPC lifecycle

- Previous turn made verified progress. Primary references become retrievable:
  inspect official Codex CLI features and Pi's author-maintained extension docs,
  record them in `agent-ui-reference-baseline.md` and update the completion audit.
  Read-only installed versions: Codex CLI **0.160.0**, Claude Code **2.1.276**;
  no Pi executable found on PATH. These are NOT asserted latest upstream releases.
  No paid model/auth/session call or dependency install is used for comparison.
- Use the refreshed baseline to identify a concrete current client problem, not
  invent more presentation requirements: Coding RPC manually duplicated its turn
  setup and bypassed canonical extension lifecycle/skill retirement behavior.
  Route prompt execution through actual `AgentSession.send_message`, using an
  invocation-owned subscriber ONLY to adapt existing wire events. No second tool
  loop, provider fallback, permission bypass or extra model submission.
- Actual CLI subprocess validation finds a startup deadlock: `run_agent` ingested
  piped stdin as a one-shot prompt BEFORE entering RPC, swallowing/blocking its
  JSON-lines control stream. Route RPC before ordinary prompt/files/pipe loading.
  The real process can now emit ready and await subsequent commands while stdin
  stays open. Normal print/interactive startup paths remain separate and retained.
- Explicit abort now cancels pre-turn hooks/compaction as well as streaming. EOF/
  quit cancel and SETTLE the actual owned producer instead of waiting indefinitely
  or merely canceling a consumer. Unsubscribe adapters exactly once and deny pending
  approvals; explicit abort can be followed by status and a real fresh prompt with
  no borrowed cancellation token. Keep parent-owned child abort policy.
- Invalid model/setting/disk control requests report protocol errors without killing
  the reader. Partial provider failures keep their text, mark `message_end.is_error`
  and emit an error; never expose retained failed content as a success-only result.
  Handled-input/stream fields and permission response behavior stay compatible.
- KEEP OPEN, grounded in the reference/source audit: Coding RPC extension-dialog/
  command management/answers are missing even though Claw's owned Unix gateway
  supports corresponding interactive transports. This patch is lifecycle/startup/
  cancellation stability, NOT completion of that remote UI capability. Existing
  custom terminal-renderer/host/provider/auth/MCP/native-storage/platform requirements
  remain in scope. OS-enforced sandboxing stays excluded.

Verification snapshot — pass 134:

- Real canonical extension session/turn/message/tool-end lifecycle, compaction call,
  durable actual read-tool result, permission policy and subscriber cleanup; real
  pre-turn callback cancellation/process reaping; EOF/abort/quit streaming producer
  settlement; invalid-setting/status recovery; typed partial provider errors and
  prior print/headless handled-input/CLI/file/compaction/permission regressions:
  **95 passed** (34.75 s), `/tmp/pass134-regressions.log`.
- Final focused RPC/lifecycle group includes actual THREE offline CLI subprocess
  JSONL startup while stdin stays open, prompt → EOF/quit/abort → provider finally →
  process exit. Abort additionally admits a real successful successor prompt before
  quit: **12 passed** (5.04 s), `/tmp/pass134-rpc-last.log` (overlapping broader tests,
  with strengthened final successor assertion). No fake-only reader proof.
- TWO existing production/private-tmux Coding permission/paste/resize/exit and Claw
  isolated-socket/native-parent/termios paths: **2 passed**, **220 deselected**
  (3.40 s), `/tmp/pass134-terminal.log`. Offline peers only, no paid model/login/user
  daemon restart; no focus/parser/approval gate relaxed. JSONL subprocess evidence
  is separate from native terminal evidence, not mislabeled as desktop/platform proof.
- Earlier subprocess failure to emit ready correctly exposed the stdin-consumption
  bug; rerun only after fixing the actual CLI routing, not by sending an EOF before
  handshake or faking ready. Intermediate failed runs are not green evidence.
- Final Ruff/all-seven strict mypy and offline Coding artifact evidence below. No
  full-root restart, dependencies/manifests/lock edits, commits/resets/stashes or
  unrelated changes overwritten. Goal ACTIVE; reference baseline is not a complete
  interoperability/native-platform/goal-wide parity claim.
- Final repository Ruff clean: `/tmp/pass134-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **455 sources**: `/tmp/pass134-types-done.log`;
  `git diff --check` clean. Fresh cached offline Coding wheel/source build:
  `/tmp/pass134-build.log`, `/tmp/pass134-wheels`. Actual main/RPC modules match
  current source BYTE-FOR-BYTE in BOTH artifact types: `/tmp/pass134-artifacts.log`.

## Pass 135 — actual opt-in Coding RPC extension/dialog controls and worker shutdown

- Previous turn made verified progress. Implement the concrete Coding RPC dialog/
  command surface identified from the pass-134 reference/source audit, using actual
  existing SDK/workers/typed broker rather than a parallel runtime or fake terminal.
- Add reader-responsive `extension_control` list/reload/unload/command jobs, with
  exact current session, fresh bounded once-only request IDs and idle/child exclusion.
  Old prompt protocol remains unchanged without explicit UI negotiation. Active
  controls cannot overlap prompts/settings/session policy mutations; status and
  reply/control cancellation continue through the same reader while callbacks await.
- `rpc_hello` negotiates exact integer basic-dialog/form/multi-choice capabilities.
  Bind UI only for the owning command/negotiated parent prompt. Same process/host
  context serves read-only metadata, not stale viewer data. No arbitrary terminal
  custom/editor/navigation/name mutation or independent-child RPC surface is borrowed.
- Deliver copied select/input/confirm/form questions with original request/session/
  fresh scope/dialog IDs. Strict typed/schema/single-use replies refuse guessed,
  duplicate/late/foreign/expired answers. False/zero/null and arrays stay distinct.
  Capability withdrawal/unsupported surfaces resolve cancellation, not defaults.
  Answers are NEVER tool approvals; actual prompt tool test proves a dialog response
  cannot answer its pending permission request, which needs separate allow-once.
- Explicit owned cancellation, including BEFORE callback task admission, returns a
  settlement receipt and cannot replay a request. EOF/quit/cancel clear pending views,
  restore previous handlers and settle actual callbacks/worker ownership. Structured
  command results remain inert output; no implicit model prompt or tool permission.
- Real subprocess tests expose a normal RPC-exit worker leak: background `dispose`
  could end the CLI before child process cleanup. RPC CLI now awaits actual extension
  manager cleanup before final disposal/exit, even when child cleanup raises. Keep
  assertion that owned worker PID is gone; do NOT hide the leak with test teardown.
  Failure-path hygiene checks only owned fixture PID/command line before signaling;
  no unmatched/user daemon or process was signaled during the diagnostic cleanup.
- KEEP OPEN: fuller remote editor/navigation/custom/child APIs and reference protocol
  conformance; this supported subset is not a scope substitute or a complete parity
  declaration. Original host/provider/auth/MCP/native credential/platform/atomic
  integration requirements remain open. OS-enforced sandboxing remains excluded.

Verification snapshot — pass 135:

- Actual SDK/process command questions/forms/multi-choice/timeouts, old/boolean
  denial, owner/duplicate/invalid/late replies, metadata/no hidden browser authority,
  idle/cancel/reuse/early admission and capability withdrawal; parent prompt tool
  permission THEN distinct question reply; original RPC lifecycle/handled-input/
  CLI and both-client/agent dialog/form regressions: **109 passed** (43.33 s),
  `/tmp/pass135-final.log` (root CI configuration).
- Includes THREE ACTUAL Coding CLI JSONL subprocess paths: ready → negotiation →
  select/input/confirm or typed multi-form → exact structured result → quit, plus
  EOF with a pending question. Zero model calls for commands, worker PID reaped,
  process exit clean. Final RPC focused group **29 passed** (21.44 s),
  `/tmp/pass135-rpc-fixed.log` (overlapping before the final two metadata/validation
  tests in broader final group). Failed pre-fix PID checks are NOT green evidence.
- FIVE existing production/private-tmux typed ordinary/multi-form review/cancel and
  native Coding permission/paste/resize/parent/termios paths: **5 passed**, **217
  deselected** (12.58 s), `/tmp/pass135-terminal.log`. Remote typed-answer evidence
  is not misrepresented as a native remote renderer/focus test. Offline peers only;
  no paid model/login or user daemon restart; no native approval/confirmation/parser
  gate relaxed. Explicit CLI observer uses current owner/nonce/type fields.
- Final Ruff/all-seven strict mypy/diff whitespace and offline artifact evidence below.
  No full-root restart, dependencies/manifests/lock edits, commit/reset/stash or
  unrelated work overwritten. Goal ACTIVE; broader requirements remain intact.
- Final repository Ruff clean: `/tmp/pass135-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **456 sources**: `/tmp/pass135-types-done.log`;
  `git diff --check` clean. Fresh cached offline Coding wheel/source build:
  `/tmp/pass135-build.log`, `/tmp/pass135-wheels`. Main/RPC/new extension service
  modules match current source BYTE-FOR-BYTE in BOTH artifact types:
  `/tmp/pass135-artifacts.log`. PID checks establish reaping, not a claim about
  a leaked diagnostic process's transient running-versus-zombie state.

## Pass 136 — real typed RPC images and post-hook vision admission in all client paths

- Previous turn made verified progress. Inspect the RPC prompt path: it ignored an
  `images` field and required nonempty message text. Wire explicit neutral typed
  inline images through the actual reader/canonical SDK, including image-only and
  negotiated UI prompts. No URI/file fetch, base64 prose or text-only substitution.
- Strict array/count/field/data/MIME bounds; optional exact `type='image'`, required
  base64 data + neutral mime_type; unsupported/extra file/URI fields refuse before
  admission. Protocol `image_prompts:1` does not claim selected-model vision support.
  Existing advertised capability/count/byte/media/base64/MIME validation remains
  authoritative. Persist complete typed data, never echo raw binary in outcome text.
- Offload binary admission CPU checks from the asynchronous reader. Actual held
  validation test proves status/abort are handled while admission waits, with no
  model/history mutation. Invalid-image requests leave the reader usable for a
  later explicit valid prompt; no automatic fallback/replay or permission changes.
- Find/correct an applicable lifecycle race too: turn-start hooks may withdraw
  selected provider vision after initial frontend validation. Revalidate AFTER
  metadata refresh and BEFORE compaction/history/model work in Coding SDK/RPC,
  Coding interactive and Claw sessions. Actual registered worker withdraws its
  provider profile in turn_start; all THREE paths refuse, with no compaction, model
  callback, primary-provider fallback or image-history admission.
- Retain explicit limits: shared SDK validation is not a complete raster decoder;
  inline JSONL is not a chunked RPC image-upload/memory-bound-before-parsing claim;
  current/new-attachment checks are not proof of whole rendered history limits.
  Broader transport/provider/decoder/native-platform requirements stay OPEN, not
  waived because these attached-image tests passed. OS sandboxing remains excluded.

Verification snapshot — pass 136:

- Typed/schema/optional type, malformed/URI/file refusal, image-only/text/negotiated
  prompts, binary/MIME/count/size/media/model refusal and unchanged history; actual
  CLI vision/text-only subprocess behavior/persisted bytes/no binary echo; responsive
  status/abort during validation; actual worker vision withdrawal across THREE apps/
  mode paths; prior RPC/dialog/lifecycle/handled-input/Claw upload/attachment tests:
  **104 passed** (36.66 s), `/tmp/pass136-regressions-done.log` (root CI configuration).
- Includes TWO actual CLI subprocess paths: typed image-only RPC → model receives
  exactly one image → lossless saved bytes, versus text-only model rejects before
  any model callback/no fallback. Explicit zero model calls on invalid admission;
  valid calls use owned offline adapters only, not paid/hosted provider evidence.
- SIX existing production/private-tmux BOTH-client attachment/clipboard/preview/send/
  draft/native parent/termios recovery paths (Wayland and simulated macOS/Windows
  helper protocols): **6 passed**, **216 deselected** (10.72 s),
  `/tmp/pass136-terminal.log`. Simulation is NOT native macOS/Windows proof. No user
  daemon restart, paid model/login or native focus/parser/approval gate relaxed.
- Final Ruff/all-seven strict mypy/diff whitespace and fresh offline artifacts below.
  No full-root restart, dependency/manifest/lock edits, commits/resets/stashes or
  unrelated work overwritten. Goal ACTIVE; remaining original requirements intact.
- Final repository Ruff clean: `/tmp/pass136-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **456 sources**: `/tmp/pass136-types-done.log`;
  `git diff --check` clean. Fresh cached offline Coding/Claw wheel/source builds:
  `/tmp/pass136-build.log`, `/tmp/pass136-wheels`. Four affected RPC/SDK/native
  admission modules match current source BYTE-FOR-BYTE in BOTH artifact types:
  `/tmp/pass136-artifacts.log`. No decoder/native-platform/complete parity claim.

## Pass 137 — whole-rendered-context image limits on the actual stream adapter

- Previous turn made verified progress. Inspect the remaining contextual image gap:
  app frontends check newly attached images, while retained/tool/context-hook images
  can make the eventual model request exceed actual limits. Add admission in the
  shared Agent loop AFTER owned context transforms/conversion/inventory capture and
  BEFORE every stream request, not another client-specific tool/model loop.
- Count actual typed images across all rendered user/tool messages. Enforce the
  configured cached adapter's advertised image input/positive count/byte/media caps.
  Rejected contexts are NOT pruned or silently text-substituted. Original canonical
  images and actual tool results remain available; tool execution is never replayed
  to fabricate another result after a second-request image quota error.
- No global provider lookup, catalog sync, auth, new namespace activation or fallback.
  Opaque SDK/transparent wrappers returning no model metadata remain caller-owned;
  lack of metadata is NOT claimed validated. Profile malformed/disappeared/changed
  while off-loop byte checks run refuses the request, not guessed successor caps.
- Do not reuse composer/new-attachment four-image cap as a whole-context limit:
  actual model advertising six admits five rendered images. Undeclared byte limits
  use the existing neutral 32 MiB payload safety bound, labeled as safety—not a
  fabricated vendor cap. Typed MIME/base64 checks do not claim full raster decoding
  or validate arbitrary caller-defined serialization/wire formats.
- Preserve cancel ownership: after async image validation, recheck cooperative
  cancellation before calling the adapter. Real abort-during-validation test proves
  no model request begins when the check settles. Text-only requests do not resolve
  image metadata or change stream behavior.
- Actual Coding RPC/native and Claw sessions with SAME-adapter metadata retain one
  prior image plus one valid new attachment under a one-image model. All THREE reject
  the rendered context before adapter execution, preserving both images and errors.
  Known/registered paths are covered; missing-limit/opaque/provider/raster/native-
  platform requirements remain OPEN, not a scope waiver. OS sandboxing excluded.

Verification snapshot — pass 137:

- Shared actual Agent retained/context-hook/tool-result/unsupported/size/media/base64
  admission, no-pruning/no second tool execution, model-declared >composer limit,
  opaque/no global borrowing, metadata change/malformed/text-only/no lookup; THREE
  actual app-mode contexts and prior Agent/loop/provider/native/RPC/image lifecycle
  and gateway regressions: **99 passed** (8.07 s), `/tmp/pass137-final.log`.
- Final focused group adds a cooperative abort DURING held off-loop admission and
  verifies no later stream request: **15 passed** (1.13 s),
  `/tmp/pass137-admission-last.log` (overlaps original contexts plus final cancel case).
- SIX existing production/private-tmux BOTH-client attachment/clipboard/preview/send/
  retained draft/native parent/termios paths (Wayland and simulated macOS/Windows
  helpers): **6 passed**, **216 deselected** (10.79 s), `/tmp/pass137-terminal.log`.
  Simulated helpers are NOT native platform proof. Owned offline adapters only;
  no paid model/login/user daemon restart or relaxed focus/parser/approval guards.
- Final Ruff/all-seven strict mypy/diff whitespace and Agent artifact evidence below.
  No full-root restart, dependency/manifest/lock edits, commits/resets/stashes or
  unrelated changes overwritten. Goal ACTIVE; broader original requirements intact.
- Final repository Ruff clean: `/tmp/pass137-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **457 sources**: `/tmp/pass137-types-done.log`;
  `git diff --check` clean. Fresh cached offline Agent wheel/source build:
  `/tmp/pass137-build.log`, `/tmp/pass137-wheels`. Agent loop/new image-admission
  modules match current source BYTE-FOR-BYTE in BOTH artifact types:
  `/tmp/pass137-artifacts.log`. No opaque/provider/native-platform completeness claim.

## Pass 138 — bounded RPC framing, chunked references and actual input-pipe ownership

- Previous turn made verified progress. Inspect recorded unbounded-inline transport
  gap and Claw's existing uploader. Extract its bounded memory/hash/ordering assembler
  into standalone `xdog.ai.utils.image_uploads`, with optional caller validation;
  retain Claw's header/dimension guard/import compatibility. Coding does NOT depend
  on Claw or turn TUI into an application-policy layer.
- Add connection/session-scoped RPC `image_chunk`/`image_discard` and prompt
  `image_refs`, with exact nonce/ordered index/type/final/hash checks, no file/URI fetch,
  active cache/image byte caps and once-only retired reference identities. Valid
  lease admission consumes refs; busy/malformed/foreign-lease attempts only peek/
  refuse and cannot consume another ready image. Discard remains idempotent and
  available at identity capacity for existing uploads. EOF clears all buffers.
- Field bounds alone were AFTER JSON parsing. Add native POSIX async byte input
  with 64 MiB frames, preserving ALL four prior 10 MiB inline attachments plus
  overhead rather than narrowing the earlier capability to make tests easier.
  Oversize is drained/refused as a whole record, no valid-looking suffix execution;
  UTF-8 errors and the following record remain distinct. No partial image admitted.
- Own/close the async input transport at protocol exit. Actual SIGINT subprocess
  exits while the caller still holds stdin open—no blocked executor-read thread
  awaiting peer EOF. Sized fallback exists for custom/unsupported-platform readers;
  no equivalent native cancellation/memory guarantee is claimed for those paths.
- Chunks may interleave ordinary status; upload assembly does not run a model or
  alter permissions. Existing SDK/new/full-context validation and image preservation
  remain required before model work. Resource caps are decoded cache bytes/frames,
  NOT a total process RSS/history bound or hostile-code sandbox. Unterminated frames
  still require delimiter/EOF. Other platform/provider/decoder conformance stays OPEN.

Verification snapshot — pass 138:

- Shared/Claw ordering/hash/cache/decoder validation and compatible callers; scoped
  RPC peek/lease/consume/discard/session eviction/cache identity capacity/retired
  replay, invalid UTF-8/max boundary/oversize whole-record/suffix refusal and next-
  record recovery; real CLI chunks → interleaved status → final hash → typed
  image-only model input/persisted one attachment → reuse refused, plus deliberate
  discard/no model callback and POSIX SIGINT without peer EOF; prior RPC/dialog/
  approval/model/lifecycle/Claw gateway/attachment regressions:
  **109 passed** (31.54 s), `/tmp/pass138-final.log` (root CI configuration).
- Focused final transport group **9 passed** (3.99 s),
  `/tmp/pass138-transport-final.log` (overlapping evidence). Real subprocess stages
  explicitly verify no model callback before prompt and no base64 echo in outcomes;
  upload inspection is NOT counted as model/provider conformance.
- SIX existing production/private-tmux BOTH-client attachment/clipboard/preview/send/
  preserved draft/native parent/termios paths (Wayland and simulated macOS/Windows
  helper protocols): **6 passed**, **216 deselected** (11.28 s),
  `/tmp/pass138-terminal.log`. Native platform equivalence is not claimed. Offline
  peers only; no paid model/login/user daemon restart or relaxed focus/approval guards.
- Final Ruff/all-seven strict mypy/diff whitespace and offline distribution evidence
  below. No full-root restart, dependency/manifest/lock edits, commit/reset/stash or
  unrelated work overwritten. Goal ACTIVE; original broader requirements intact and
  OS-enforced sandboxing remains excluded.
- Final repository Ruff clean: `/tmp/pass138-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **460 sources**: `/tmp/pass138-types-done.log`;
  `git diff --check` clean. Fresh cached offline AI/Coding/Claw wheel/source builds:
  `/tmp/pass138-build.log`, `/tmp/pass138-wheels`. Five shared/framing/upload modules
  match current source BYTE-FOR-BYTE in BOTH artifact types:
  `/tmp/pass138-artifacts.log`. No alternate-platform/total-RSS/full decoder claim.

## Pass 139 — authoritative full-root checkpoint and typed JSON terminal assertion

- Previous turn made verified progress. Recent shared model/context and image/RPC/
  framing changes justify ONE whole-workspace CI checkpoint. Leave production code
  unchanged throughout. Keep its existing tool/process through confirmed live polls
  and long observation timeouts; never duplicate/restart it because observation ended.
- Authoritative root exit: **1 failed, 4,212 passed, 29 skipped** (1115.93 s),
  `/tmp/pass139-workspace.log`. Failure: dynamic Claw extension-provider terminal
  test expected the literal `"terminal_generation": 1` on one physical display line.
  Actual current JSON contained the correct integer field split colon/value across
  lines after adding command-completion snapshot metadata. No runtime bug is guessed.
- Replace physical one-line assumption with a typed visible JSON field matcher:
  tolerate whitespace/wrapping ONLY, preserve exact field/scalar token and delimiter.
  Refuse integer 10, quoted "1", boolean true, unrelated fields and escaped JSON
  description text. Existing provider activation/stream/counter/limit/permission/
  terminal recovery assertions remain; no terminal widening, focus/Enter/guard or
  provider fallback changes. Do not merely increase timeout or accept a substring.
- Runtime remains unchanged after root failure. Verify the entire actual ten-case
  both-client provider/error/aborted/dynamic/capability terminal matrix PLUS seven
  strict matcher cases, then continue from the UNIQUE exact stopping node under root
  CI configuration. No invented test boundary or broad new retry loop.

Verification snapshot — pass 139:

- Changed real provider terminal matrix and typed/wrap/scalar assertion:
  **17 passed**, **212 deselected** (24.69 s), `/tmp/pass139-terminal-fixed.log`.
  Ten production/private-tmux paths still exercise real registered providers and
  native parent/termios behavior; seven matcher cases are not miscounted as terminal
  paths. Offline peers only; no paid model/login/user daemon restart or weakened
  native focus/parser/permission/confirmation policy.
- Exact full-root stopped-node continuation: **920 passed**, **4,241 deselected**
  (234.92 s), `/tmp/pass139-remainder.log`. Initial prefix + corrected-node tail:
  **5,132 passed, 29 skipped** over the full **5,161-case** current collection.
  SEVEN additional matcher cases account for collection growth. This is explicitly
  SEGMENTED evidence, not an atomic green whole-workspace run. Production code stayed
  unchanged, and the focused provider matrix covers the altered earlier cases.
- Repository Ruff clean and strict ALL SEVEN namespaces **460 sources** passed
  both before and after fixture correction; final logs below. Diff whitespace clean.
  No runtime/dependency/manifest/lock edits, package rebuild, commits/resets/stashes
  or unrelated work overwritten. Do not mislabel the failed original root run as
  green, waive skipped/native-platform requirements or mark the goal complete.
- Completion audit now records the actual checkpoint rather than older pass-127
  state. Goal ACTIVE; atomic-root and original broader host/provider/auth/MCP/native
  storage/platform completion requirements remain OPEN. OS sandboxing excluded.
- Final repository Ruff clean: `/tmp/pass139-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **460 sources**: `/tmp/pass139-types-done.log`;
  `git diff --check` clean. Original full-root and exact continuation process/tool
  handles are authoritatively terminal; no verification task is silently pending.

## Pass 140 — command-only RPC editor transport to an actual client-owned CAS

- Previous turn made verified progress. Inspect the remaining RPC editor API gap:
  get/set-editor requests returned unavailable despite native host CAS support.
  Extend the actual worker/broker/RPC path with independently negotiated exact
  `extension_editor:1`, NOT a invented host mirror or a parallel model/tool loop.
- RPC emits existing typed editor read/replace dialogs with original session/request/
  scope/dialog IDs. Remote client owns actual text/cursor/attachment identities and
  performs the same `EditorActions` snapshot/nonce/full-state CAS as native hosts.
  Returned labels/text—including `/quit`—are draft data, never commands/prompts or
  tool approvals. Wrong/human-edited/caret/image/owner state returns false conflict.
- Host validates copied snapshot/receipt schema and owner, caches ONLY the latest
  served opaque revision per extension/command, and consumes it once before forward
  replacement. Refuse a fabricated uncaptured revision without even sending a remote
  replace request. No claim that a host can prove arbitrary remote screen/client
  correctness; the opted-in client must apply CAS before acknowledging true.
- General UI binding now respects editor-only capability independently of questions/
  forms. Command-only host and worker checks prevent tools/hooks/prompt/custom tasks
  borrowing editor authority. Withdrawal, EOF/quit/cancel drop read leases/pending
  views; stale/foreign/malformed replies cannot revive another question or prompt.
  Existing native/menu/navigation/name mutation refusal remains; policy/history/model
  and composer/uploaded image ownership stay unchanged.
- KEEP OPEN: native custom rendering, fuller RPC navigation/name/child surfaces,
  reference wire/API conformance and all original host/provider/auth/MCP/credential/
  native-platform/atomic-root requirements. This scoped editor slice is not a broader
  parity or remote renderer guarantee. OS-enforced sandboxing remains excluded.

Verification snapshot — pass 140:

- Real client-owned InputPanel + SAME native EditorActions over the actual RPC
  reader/process worker: successful read/replace, human text/cursor/image/owner
  conflicts, retained attachments/no submission/model/permission change; old/unrelated
  capability denial, revoked/foreign/bad-cursor receipt, fabricated revision refused
  before forwarding, tool authority denial; prior RPC/dialog/canonical/image and
  Agent/editor-helper tests: **73 passed** (37.86 s), `/tmp/pass140-final-first.log`.
- Includes TWO actual Coding CLI JSONL subprocesses: independent editor negotiation
  → client read → scoped replace → actual CAS true versus human edit conflict false
  → unchanged permission/model/history/status → clean exit. Starting draft `/quit`
  is never auto-executed or submitted. Offline workers, zero model callbacks.
- TWO existing production/private-tmux BOTH-client command editor/context/caret/
  image compare-swap/native parent/termios paths: **2 passed**, **227 deselected**
  (4.86 s), `/tmp/pass140-terminal.log`. These retain native focus/permission policy;
  remote client CAS evidence is not mislabeled as arbitrary remote screen proof.
  No paid model/login/user daemon restart or weakened approval/parser guards.
- Final Ruff/all-seven strict mypy/diff whitespace and fresh Coding distribution
  evidence below. No full-root restart, dependency/manifest/lock edits, commit/reset/
  stash or unrelated changes overwritten. Goal ACTIVE; original scope intact.
- Final repository Ruff clean: `/tmp/pass140-ruff-done.log`; strict ALL SEVEN
  namespaces clean across **460 sources**: `/tmp/pass140-types-done.log`;
  `git diff --check` clean. Fresh cached offline Coding wheel/source build:
  `/tmp/pass140-build.log`, `/tmp/pass140-wheels`. RPC/controller modules match
  current source BYTE-FOR-BYTE in BOTH artifact types: `/tmp/pass140-artifacts.log`.

## Pass 141 — native RPC navigation primitives, owner lease fencing and SDK save safety

- Previous turn made verified progress. Add native session list/tree/new/resume/
  current-head branch/goto/fork/rename controls to actual Coding RPC through existing
  SDK session managers/staging, not returned extension text or another tool loop.
  Share the ACTUAL terminal navigation-entry helper so two clients do not drift.
- Bounded paged metadata reads use existing catalog filtering/revision codecs off
  the reader loop; no Agent activation/model/history/image export to browse. Tree
  source remains persisted native data, not a guessed streaming checkpoint. Mutating
  primitives require original expected session/fresh bounded request ID, idle parent/
  children and no competing RPC control. Index is explicitly zero-based as catalog.
- Mutation receipts reflect actual session/model/head and fresh conversation revision.
  After admitted native mutations, stale/owner-less prompts, extension/model/thinking/
  compact controls cannot silently address a new head. Read/page membership revisions
  are separate from conversation leases; original clients avoiding navigation keep
  their prior protocol. Failed unexpected state changes are reported, not asserted
  rolled back. No primary-provider/model fallback or permission-mode change.
- Reader-responsive owned thread preparation/commit retains ownership until settled.
  Explicit cancellation signals unadmitted SDK guards but never cancels admitted
  disk publication midway. Stable pre-publication status and cancel-request ACK are
  distinct from final commit receipt; committed cancellation reports ACTUAL target
  plus `receipt_canceled`, not invented rollback. EOF/quit settle owned work.
- Source inspection exposes related SDK safety issues. Create-branch appended to
  live state BEFORE an atomic save: failed writes could later publish phantom branch.
  Save a staged data copy, then publish on the SAME data owner captured by plan/
  extension writers. Failed save preserves live fields/bytes/owners. New-session
  preparation also no longer unnecessarily persists the old head before preflight;
  SDK switch already preserves that head at admission. Remove ONLY unchanged owned
  unadmitted new candidates on failure, never adopted/independently changed data.
- KEEP OPEN: combined cross-target atomic branch/checkpoint operations (native RPC
  currently composes explicit resume then current-head action), reviewed extension-
  directed navigation, full source replay/richer custom/child RPC APIs and all original
  provider/auth/MCP/storage/native-platform/atomic-root requirements. This intermediate
  native primitive set is not a scope waiver or full parity claim. OS sandboxing excluded.

Verification snapshot — pass 141:

- Actual SDK list/tree/filter/fork/branch/goto/resume/name/new with no model work and
  retained deny policy; failed fork bytes/object-owner preservation; unadmitted versus
  admitted repeated cancellation; real RPC stale-head prompt rejection and new target
  prompt; status/cancel ACK during held actual disk commit; new-candidate failure with
  unchanged removal versus independently changed preservation; current RPC/editor/
  dialog/lifecycle/native/session/staging/manager/extension-navigation regressions:
  **78 passed** (61.35 s), `/tmp/pass141-final-done.log` (root CI configuration).
- Includes an ACTUAL Coding CLI JSONL subprocess: named target discovery → staged
  resume → stale prompt refused before any model callback → fork metadata persists
  under correct head → stable actual status/revision/policy → clean exit. Additional
  direct reader/SDK tests use real workers/staging/disk ownership, not fake success.
- Final focused save/candidate/staging group **25 passed** (17.79 s),
  `/tmp/pass141-safety-fixed.log` (overlapping cases). Acknowledged cancel is NOT
  reported as canceled state until final owner settles; committed output is verified.
- FOUR production/private-tmux BOTH-client session/tree/return-head and reviewed
  extension-new-session/native parent/termios paths: **4 passed**, **225 deselected**
  (9.66 s), `/tmp/pass141-terminal.log`. Offline peers only; no paid model/login/user
  daemon restart or relaxed focus/permission/approval/confirmation/Enter guard.
- Final Ruff/all-seven strict mypy/diff whitespace and fresh Coding artifact evidence
  below. No full-root restart, dependency/manifest/lock edits, commit/reset/stash or
  unrelated work overwritten. Goal ACTIVE; original broader scope remains intact.
- Final repository Ruff clean: `/tmp/pass141-ruff-last.log`; strict ALL SEVEN
  namespaces clean across **462 sources**: `/tmp/pass141-types-last.log`;
  `git diff --check` clean. Fresh cached offline Coding wheel/source build:
  `/tmp/pass141-build.log`, `/tmp/pass141-wheels`. Five native/shared/RPC/SDK modules
  match current source BYTE-FOR-BYTE in BOTH artifact types:
  `/tmp/pass141-artifacts.log`. No combined-operation/goal-wide parity claim.

## Pass 142 — actually accepted reviewed RPC extension navigation and deferred native commit

- Previous turn made verified progress. Close the separately recorded reviewed
  extension navigation RPC path using the actual existing worker/broker and native
  staged navigator, not returned command text or a second model/tool loop.
- Independently negotiate exact `extension_navigation:1`. Parent COMMAND callbacks
  may request exact typed target review. Questions/forms/editor capability does not
  imply navigation; tools/prompt/custom callbacks cannot borrow a command owner.
  Unsupported cross-target combined branch/goto refuses BEFORE review, never a
  hidden sequence of partially admitted session changes. Combined atomic API OPEN.
- Record queued target only after the broker future ACTUALLY completed true under
  the original request/session/scope/revision. Successful transport ACK is not that
  callback's value; a future already canceled/expired must not create a grant. Apply
  the same actual-read-future rule to RPC editor revisions, fixing adjacent false
  read-lease creation instead of relaxing typed validation or inventing a UI mirror.
- `True` is queued, NOT commit. Dispatch existing native primitive from completed
  successful callback's done hook, after extension ownership releases. Revalidate
  current session/revision/capability and native idle/children/target/model preflight.
  Correlate extension_navigation_queued's native request ID with actual session_result.
  Callback failure/cancel/owner/revision change or withdrawal discards/no replay.
- Generic extension outputs remain inert. Native restore owns persistence/resource/
  permissions; no model call, prompt submission, primary fallback or tool approval
  from the review. Original policy remains deny where selected. Remote renderer must
  perform actual target review; this transport cannot prove arbitrary client screens.
- KEEP OPEN: combined cross-target atomic operations, native/gateway actual-future
  late-answer grant audit (RPC correctness is not proof for those separate paths),
  fuller source/custom/child RPC APIs and original provider/auth/MCP/storage/native-
  platform/atomic-root requirements. This slice preserves the full goal scope;
  OS-enforced sandboxing remains excluded.

Verification snapshot — pass 142:

- Actual RPC new/resume/branch/goto/fork review → callback success → native staged
  commit, denial/callback error, held post-review capability/revision/cancel discard,
  unrelated-capability/cross-target pre-review refusal; real broker future already
  completed None BEFORE transport answer: no navigation OR editor read grant;
  prior native/session/editor/dialog/lifecycle/agent/both-client navigation tests:
  **97 passed** (77.39 s), `/tmp/pass142-final.log` (root CI configuration).
- Final focused actual-future/editor/nav group **27 passed** (29.46 s),
  `/tmp/pass142-grant-last.log` (overlapping before the broader final run).
- Includes TWO actual Coding CLI JSONL subprocesses: independent navigation
  negotiation → exact target question → strict true → callback success reaches an
  actual new session/native receipt, versus callback failure discards and retains
  old session. Zero model callbacks, clean worker/process exit, original deny policy.
- FOUR production/private-tmux BOTH-client reviewed extension new-session and
  command editor/context CAS/native parent/termios paths: **4 passed**, **225
  deselected** (10.11 s), `/tmp/pass142-terminal.log`. Existing guards retained;
  these ordinary paths are NOT proof of native late-answer race coverage. Offline
  peers only; no paid model/login/user daemon restart or weakened native review.
- Final Ruff/all-seven strict mypy/diff whitespace/offline artifact evidence below.
  No full-root restart, dependency/manifest/lock edits, commits/resets/stashes or
  unrelated changes overwritten. Goal ACTIVE; broader scope intact.

- Final pass142 checks completed: Ruff clean; all-seven strict mypy **462 source
  files** clean (`/tmp/pass142-types-done.log`); `git diff --check` clean. Offline
  Coding wheel/sdist current RPC files verified byte-for-byte. Goal stays ACTIVE.


## Pass 143 — native navigation/rename grants require actual broker receipts

- Finish pass142's pending checks rather than duplicate its implementation. All
  seven packages strict mypy: 462 sources clean; Ruff and whitespace clean.
- Address the specific native Coding/Claw late-answer race recorded OPEN in pass142.
  `DialogBroker.ask` emits an owned `extension_dialog_accepted` receipt only when
  its actual awaited result is `True` for session navigation/rename, before closure
  and callback completion. Queue membership and `resolve()`/transport ACK do not
  establish that value. Other dialog results and permission approvals are unchanged.
- Both native UIs stage review proposals separately from granted navigation. Promote
  only on the matching broker receipt under original dialog/extension/operation/
  command/session/history context; Claw also requires its original group/request/
  connection owner. Native command authority and capability are checked again.
  Callback success remains necessary for existing staged native dispatch. Closure,
  wrong owner, history changes and callback failure cannot manufacture a grant.
- Claw's existing gateway publication forwards the receipt through its owned command
  channel, before extension completion. No second agent loop/model/permission path.
  An older gateway lacking actual receipts fails closed for the new native client.
- Revoke Claw reviewed navigation/rename and command-editor authority immediately
  when local cancellation is sent; late successful callback cannot navigate.
  Navigation capability withdrawal after acceptance also prevents native dispatch.
  Clear review/grant/command-editor ownership on disconnect, without replay.
- Real broker tests cover accepted/denied/expired/canceled callbacks and receipt
  ordering for both operations. Real Coding worker and Claw socket tests deliberately
  complete the actual broker future as None before a True transport answer while
  retaining broker membership: ACK succeeds but native navigation/rename stays put,
  callback completes normally with false, draft/history/policy stay intact. Scoped
  receipt/ACK/owner/epoch tests and before/after cancel/withdrawal regression cases.

Verification snapshot — pass 143:

- Focused shared broker/native both-client navigation/name/RPC editor/navigation
  group **120 passed** (75.89 s), `/tmp/pass143-final.log`. Final Claw cancel/withdrawal
  addition separately **4 passed**, `/tmp/pass143-revoke.log` (then latest focused
  native rerun below). No root suite rerun or claim of an atomic workspace checkpoint.
- Initial production/private-tmux reviewed new-session and rename success/failure
  paths in BOTH clients **6 passed, 223 deselected** (16.05 s),
  `/tmp/pass143-terminal.log`; latest Claw adjustment rechecked below.
- Repository Ruff clean `/tmp/pass143-ruff.log`; all-seven strict mypy **462 sources**
  clean `/tmp/pass143-types.log`; whitespace clean. No dependency download, paid
  model, login, user-daemon restart, metadata/lock changes, commit/reset/stash, or
  unrelated work overwritten. No artifact rebuild required for this source-only slice.
- KEEP OPEN: combined cross-target atomic navigation, richer source/custom/child RPC,
  native platform/credential storage, wider MCP/provider/auth interoperability,
  later atomic workspace checkpoint and all broader tracked requirements. Goal
  stays ACTIVE; OS-enforced sandboxing remains explicitly excluded.

- Latest native state after final Claw cancellation/capability revocation edits:
  **34 passed** (31.48 s), `/tmp/pass143-last.log`; latest actual BOTH-client
  navigation/rename production terminal paths **6 passed, 223 deselected**
  (15.93 s), `/tmp/pass143-terminal-last.log`. All owned test/check handles settled.


## Pass 144 — one staged cross-session branch/checkpoint admission

- Previous goal turn was verified implementation progress (pass143), not a wait or
  scope reduction. Close the remaining combined navigation gap in actual Coding
  SDK/native slash/RPC/reviewed extension paths, rather than compose resume plus
  another mutation or declare current-head-only primitives complete.
- Extract existing staged session activation into a shared private admission path.
  Add `AgentSession.navigate_checkpoint(session_id, branch_id=..., index=...)`:
  build the selected history/model/settings/plan/extension/compaction candidate and
  retained target return head; validate metadata and complete tool-batch boundaries;
  stage selected runtime/model/workspace; only then save/publish the final target.
  No target live-head activation, extra provider/tool loop or saved phantom branch
  on failed preflight/save. Outgoing current history persists separately as before.
- RPC branch/goto accepts an exact saved target and optional branch checkpoint;
  ready advertises `session_checkpoint_targets:1`. Fork remains current-head-only.
  Original idle/child/expected-session/revision/cancellation/resource/permission
  gates remain authoritative. Native `/branch SESSION BRANCH` and `/goto SESSION
  [BRANCH|-] INDEX` use the same operation. Same-head goto now stages the checkpoint
  BEFORE saving it, fixing the former save-branch-then-try-restore partial mutation.
  SDK empty-history -1 sentinel retained; RPC still requires explicit nonnegative
  checkpoint indexes. Terminal numbering remains one-based.
- Independently reviewed extension RPC branch/goto requests prevalidate exact saved
  selection before review, check it/owner/capability again after actual broker True,
  then dispatch the same native staged operation after successful callback. Invalid
  selections cannot silently become resume/head fallbacks. Late-answer receipt
  protections from pass143/142 are unchanged; callback failure/denial never commits.
- Reject mismatched saved-file session IDs, malformed branch records/history/model/
  settings with an owned refusal, retaining reader recovery rather than an uncaught
  task with no receipt. Preserve selected durable compaction/plan/extension state.
- Actual trusted staged-provider worker tests now cover cross-session branch/goto
  success, missing provider, failed preparation/save and canceled preparation:
  selected target starts once; original workers/resources live until commit and
  remain intact on failure; adopted state persists subsequent writes correctly.
- This is atomic APPLICATION ADMISSION, not an OS/multi-file/power-loss transaction.
  Admitted cancellation settles actual committed state and reports receipt_canceled;
  unadmitted cancellation retains the source without target changes. No invented
  rollback, no OS sandbox, no full-goal/API/native-platform conformance claim.

Verification snapshot — pass144:

- Shared native/session/staged-worker/RPC/reviewed extension group **96 passed**
  (67.69 s), `/tmp/pass144-final.log`, before final defensive/CLI discovery additions.
- Latest native selection/staged-worker rerun **49 passed** (35.56 s),
  `/tmp/pass144-native-last.log` (overlap; includes identity guard and THREE actual
  Coding CLI subprocesses selecting resume, cross-session branch, cross-session
  branch checkpoint with discovery/typed receipts/revision fencing/no model work).
- Final native checkpoint/RPC module **36 passed** (4.23 s),
  `/tmp/pass144-checkpoints-done.log`: selected metadata/compaction/restorable return
  head, one target save, missing/invalid/saving/model-effort/workspace/identity failure,
  committed/unadmitted cancel, malformed saved-branch refusal then next control,
  partial parallel tool-batch refusal vs complete batch, native slash and CLI cases.
- RPC + reviewed extension group after actual CLI capability/combined-review tests
  **51 passed** (31.74 s), `/tmp/pass144-rpc-last.log` (overlap). Includes FOUR actual
  extension CLI subprocess cases: reviewed new-session/cross-session branch checkpoint
  success versus failed callback, exact target/correlated native receipt, no model
  callback/clean process exit. Latest reviewed-only rerun recorded below.
- Latest private-tmux production session/tree cross-target restore/return-head/draft
  and reviewed new-session paths in BOTH clients **4 passed, 225 deselected**
  (9.70 s), `/tmp/pass144-terminal-last.log`. Offline owned peers only; Claw uses
  unchanged group-owned navigation. These paths are not proof of native platforms.
- Final all-seven strict mypy **462 sources** clean, repository Ruff/whitespace clean;
  offline Coding wheel + sdist exact current admission/native/RPC modules and sdist
  README checked byte-for-byte (`/tmp/pass144-artifacts.log`). Final status below.
- No root suite restart, paid model/auth/login/dependency download/user-daemon
  restart, manifest/lock edit, reset/stash/commit or unrelated work overwritten.
- KEEP OPEN: full RPC source/custom/child surfaces, richer host APIs, native platform/
  credential storage, broader MCP/provider/auth interoperability and later atomic
  workspace checkpoint. Goal remains ACTIVE with original scope intact.

- Final reviewed extension-only rerun **23 passed** (see
  `/tmp/pass144-reviewed-done.log`); all real new/combined CLI callback cases and
  original actual-future/ownership/withdrawal regressions remain green. Final Ruff
  `/tmp/pass144-ruff-final.log`, strict mypy462 `/tmp/pass144-types-final.log`,
  whitespace and byte-for-byte current source artifacts are clean. All owned
  verification commands finished; no unfinished root run or duplicated wait.


## Pass 145 — complete retained display-source RPC and nonblocking native output

- Previous goal turn was verified implementation progress (pass144). Advance the
  broader transcript/transport goal, not another scope reduction to navigation.
- Add independently negotiated exact `transcript_sources:1` and owned transcript
  read/cancel controls to actual Coding RPC. Read complete live canonical messages
  plus actual partial response, exact saved head/branch, and recorded provenance
  timeline. Live snapshots capture admitted tuples rather than reconstruct deltas
  or reload stale disk. Existing native parent-prefix/verified-compaction-link
  timeline semantics remain authoritative; no content-only guessed merging.
- Extract Coding's existing read-only source loader into a shared core reader and
  use it from the native saved/timeline UI and RPC. Freeze current timeline head
  before the background load. Preserve source IDs, captions, query/draft/scrollback,
  masks and provenance. Reject missing/mismatched/symlink/corrupt targets with no
  current-head fallback, activation, model/compaction/permission/extension authority.
- Move source receiver and neutral private JSONL spooling to dependency-light TUI
  modules, keeping Claw import/class compatibility and its formatter/owned sender.
  Optional exact conversation lease and receiver byte cap protect RPC assembly;
  existing Claw context revisions/field validation/order/SHA256/receiver behavior
  remain intact. No coding→claw dependency or second provider/tool loop.
- One RPC worker plus one latest pending read. Fresh IDs/connection capacity,
  exact parent/revision/target scope, native-navigation admission fence. Supersede,
  cancel, withdrawal or context change cannot publish a completed obsolete read.
  Settle thread-owned preparation/private spool closure before next read/EOF/quit;
  no cancellation pretending to stop actual filesystem work or rollback admitted work.
- Transfer complete UTF-8 JSONL display records via bounded ordered base64 blocks
  (64KiB raw), manifest bytes/SHA256 and terminal source_end. Receiver enables
  search/selection/output only after complete verification. Source_error invalidates
  partial assembly. Explicit 128MiB transfer cap fails, never truncates. Binary image
  data/signatures/opaque custom metadata stay out of display; images carry omission
  markers. Reject unsupported binary/signature content parts rather than let generic
  persistence repr leak them. Heuristic redaction is not a secrecy guarantee.
- Large push transfers exposed the old synchronous stdout deadlock risk. Add one
  bounded ordered nonblocking native POSIX pipe/socket/terminal writer for ALL RPC
  frames. Source blocks await actual writes; canceled producers do not abandon a
  half-written line on a live connection. A closed output peer wakes the input owner
  even with stdin still open. Backlog whole-frame refusal is bounded64MiB; bounded
  shutdown releases stalled writers/restores fd flags. Status reports source owner.
  Advertise native rpc_async_output state; custom sinks/regular files/unsupported
  platforms retain legacy synchronous compatibility, NOT native cancellation proof.
- Source-only hello leaves negotiated extension navigation/questions/editor state
  intact. Existing prompt/tool approvals remain independently processable while
  source preparation is held; source frames never approve tools, submit drafts,
  execute command prose or install canonical/model context.

Verification snapshot — pass145:

- All Coding RPC modules plus native saved viewers, Claw full-source/scope and TUI
  saved/timeline helpers: **208 passed** (84.45s), `/tmp/pass145-final.log`, before
  final status/discovery and malformed-role guard additions. No full-root rerun.
- Latest source/control/native-output/actual-CLI/lifecycle/native saved-view group
  after those final changes: **61 passed** (17.29s), `/tmp/pass145-latest.log`
  (overlap). Includes explicit source-only hello independence; actual held source
  preparation while status and real permission_response settle; current-head goto
  revision fence; live append snapshot; coalescing/cancel/withdraw/close/spool cleanup;
  long Unicode/reasoning/tool joins/provenance, typed image/signature omission,
  malformed-role/saved/symlink/capacity refusal and next-read recovery.
- SIX actual owned offline Coding CLI processes: saved head/branch/timeline complete
  multiblock UTF-8/SHA256 replay and unchanged current session/deny/no model calls;
  >1MiB deliberately UNREAD source peer then quit/EOF exit, or stdout-close with stdin
  still open → actual output failure exit. No draining workaround or merely parser
  claim of native responsiveness. Real-pipe backpressure/whole-frame ordering,
  canceled send/shutdown restoration and bounded output admission also exercised.
- FIVE production private-tmux BOTH-client source search/selection/fullscreen/resize/
  native-return/timeline paths (including real Claw source beyond wire caps): **5
  passed, 224 deselected** (9.70s), `/tmp/pass145-terminal.log`. These are recorded
  Linux paths, not Windows/AppKit/Kitty/iTerm platform evidence.
- Final Ruff `/tmp/pass145-ruff-final.log` clean; all-seven strict mypy **467 sources**
  clean `/tmp/pass145-types-final.log`; whitespace clean. Offline Coding/Claw/TUI
  wheels AND sdists built; all NINE current affected source modules byte-for-byte
  verified in BOTH artifact types `/tmp/pass145-artifacts.log`.
- No paid model/auth/login/dependency download/user-daemon restart, manifest/lock
  edit, commit/reset/stash, unrelated work overwritten, or root suite re-start.
- KEEP OPEN: raw canonical/binary source export and richer transient/custom/child/
  live-tool-progress RPC surfaces, remaining host APIs, native credential/platform
  storage/cancellation evidence, broader MCP/provider/auth interoperability and a
  later atomic workspace checkpoint. This pass implements retained DISPLAY-source
  replay, not every source/view API or full goal. Original goal ACTIVE, OS sandbox
  still excluded. All owned verification processes finished.


## Pass 146 — real Coding RPC child supervision, independent approvals/questions/source

- Previous goal turn was verified implementation progress (pass145). Advance
  applicable interactive child supervision and extension/source transport rather
  than claim native-only supervision closes the broader capability requirement.
- Add explicitly negotiated `child_controls`, `child_sources`, `child_approvals`,
  `child_dialogs`, `child_forms`, `child_multi_forms` (exact0/1). Child-only hello
  cannot withdraw parent extension/editor/navigation/source consent. Discovery
  returns original parent/revision, fresh connection UI lease and native supervisor
  session/branch epoch; controls/answers require ALL FOUR exact scope fields.
- Wire native presets/configure/start/list/inspect/steer/follow-up/cancel/wait/close
  into CodingChildren/ChildSupervisor; no second agent loop, recursive spawn or
  parent grant/model/tool policy bypass. Start gates idle parent/extension/context/
  admission; parent prompt/context controls cannot borrow an in-flight start.
  Config is native specialization/downward budget/tool/preset schema, never permission
  or access. Default child access stays read-only; explicit inherit retains current
  parent policy. Four bounded control jobs and fresh bounded once-only request IDs.
- Marshal native child-owner notifications/questions/approvals to RPC reader loop.
  Metadata catalog is sanitized/redacted, not a raw signature/image/history dump.
  Actual child-specific permissions never resolve parent/other-child/foreign-UI
  futures or copy parent session rules. Post-approval parent deny/planning/access
  gates remain authoritative. Optional client-owned approval-alive guard preserves
  native TUI behavior and refuses a grant resolved before UI revocation. Generation
  changes prevent withdraw→re-enable revival; clear pending/cache child grants.
- Typed child input/select/confirm/forms/multi-choice use existing broker/lease.
  Task answers are NOT tool permission. Session/editor/name/navigation/custom/raw
  terminal requests cannot borrow parent command authority. Cancel unsupported
  native broker kinds even BEFORE queued UI publication reached the client; stale
  delivered questions cannot reappear after callback/owner settlement. Generic
  broker cancellation predicate/child binding helper is policy-neutral; app flags
  remain in Coding. Parent/child physical renderer parity still not inferred.
- Add child_source_read via the existing owned full-source spool/chunk/checksum
  worker, independently of parent transcript consent. Snapshot actual partials,
  canonical results, bounded live tool progress/capacity markers, model/config and
  input-handler feedback as SYSTEM data, not fabricated model prose. Extend frozen
  ChildCapture with already persisted feedback, benefiting existing native viewers.
  Receiver optionally checks child ID/UI lease/native epoch across every frame;
  child-source cancel also requires exact original child scope, not parent-only ID.
- Control-cancel ACK is requested, never rollback. Pending canceled start produces
  terminal no-admission receipt; admitted canceled start shields/settles its exact
  native child ID then cancels ONLY that child, not a guessed catalog delta. Canceling
  a wait does not cancel work; explicit child cancel/close settles native lifecycle.
  EOF/quit/output failure stops parent production BEFORE child cleanup, denies
  pending grants, joins all current/retired native supervisors, settles workers and
  retains interrupted/canceled durable state without replay.

Verification snapshot — pass146:

- All Coding RPC + native Coding/Claw child clients + generic supervision/config/
  model/store/broker + timeline regression group **364 passed** (121.98s),
  `/tmp/pass146-final.log`, before final child-source cancel scope addition.
- Latest scoped child controller/actual CLI/parent transcript compatibility group
  **58 passed** (17.47s), `/tmp/pass146-last.log` (overlap). Real native agents/loops,
  independent extension workers, filesystem policies/persistence, source receiver,
  held actually-resolved allow-session then revoke/re-enable: no mutation; parent
  planning after review veto; foreign/nonreusable replies; typed false/zero/null
  forms; not-yet-published broker revoke; no-model input-feedback source; live steer/
  follow-up queues; wait cancellation vs work cancel; pre/post-admission cancellation
  and exact native child settlement; source parent/child capability/scope isolation.
- FIVE actual owned offline Coding CLI processes: ask-mode child filesystem approval
  allowed/denied (parent permission response refuses the child nonce), verified
  child source and unchanged parent history/model policy; actual blocked child stream
  then explicit cancel, quit, EOF → iterator settlement marker, canceled persisted
  record, clean process/thread exit, no replay. Fake local adapters only; no paid
  model/auth/login/user daemon or dependency download.
- FOUR production/private-tmux native child independent approvals/questions/config
  and parent cursor/terminal restoration across BOTH clients **4 passed, 225
  deselected** (9.97s), `/tmp/pass146-terminal.log`. Original guards retained;
  recorded Linux behavior is not other native platform/physical remote review proof.
- Final repository Ruff clean `/tmp/pass146-ruff-shipped.log`; all-seven strict
  mypy **468 sources** clean `/tmp/pass146-types-shipped.log`; whitespace clean.
  Offline Agent/Coding/TUI wheel AND sdist artifacts: all EIGHT current affected
  modules byte-for-byte verified in BOTH types `/tmp/pass146-artifacts.log`.
- No full-root restart, manifest/lock edits, commit/reset/stash or unrelated changes
  overwritten. Completed owned jobs only; no restarted observation timeout.
- KEEP OPEN: richer child/custom/terminal/render APIs and editable child-input queues,
  raw canonical/binary export, original native credential/platform storage/cancellation
  requirements, wider current MCP/provider/auth interoperability, eventual atomic
  workspace checkpoint and every other tracked original requirement. Goal ACTIVE;
  OS-enforced sandboxing remains excluded. No complete/deeper delegation parity claim.


## Pass 147 — actual editable live child queues in Coding/Claw/RPC

- Previous turn was verified implementation progress (pass146). Implement the
  recorded live child-queue gap through the actual existing Agent APIs, not a
  presentation-only FIFO or a narrower source/status-only capability.
- Generic child owner-loop queue snapshots and revision-checked list/edit/remove/
  same-delivery-lane move/return use Agent.pending_inputs/change_input. Ephemeral
  SHA256 revision covers child/agent lifetime/status, ordered identity/content/
  delivery/checkpoint selection and opaque image identity without hashing large
  binary payload on the loop. Capacity32/text16KiB refusal, no truncation. Stale,
  stopped, selected/consumed or cross-lane controls cannot apply or replay.
- Shared reviewed ChildQueuePanel opens with q in both native child rosters (Ctrl+Q
  from child source); exact target/input/content/revision pages must all be painted
  before fresh physical Enter. Paste/repeat/narrow/unpainted frames refuse commit.
  Native Coding SDK and Claw socket controls publish actual owner receipts, not an
  inferred queue mirror. Disconnected/older/withdrawn Claw child_queues capability
  refuses controls. Coding RPC separately negotiates child_queues and accepts exact
  typed queue requests under original parent/revision/UI/native-child epoch.
- Return restores ONLY the child steer/follow-up draft after an exact matching
  removal receipt for that reviewed row; never submits/calls models, overwrites the
  parent draft or invents recovery from an ACK. Failed/stale edits retain local
  text; focus follows parent modal ownership. Capability withdrawal AFTER painting
  cannot dispatch a reviewed action or consume an unsubmitted edit. Image identity
  is preserved by edit; image-bearing return refuses rather than lose attachments.
- Existing native child lifecycle/permissions/model/budget/parent-planning gates
  stay unchanged. Queue data is exact opted-in draft text (not redacted source
  text), not permission approval, new model context, raw canonical export or OS
  confinement. Private component/API remains presentation vs policy separated.

Verification snapshot — pass147:

- Agent supervision/queue/config/model/store + real native Coding/Claw child/RPC +
  shared panel/control regression group **205 passed** (40.07s),
  `/tmp/pass147-final.log`, before final after-paint capability withdrawal guards.
- Latest direct owner/physical shared widget/BOTH real native app SDK/socket/actual
  Coding CLI group **28 passed** (7.87s), `/tmp/pass147-last.log` (overlap). Actual
  checkpoint selection refuses all mutations, stale edit refuses without replay,
  same-lane movement, exact return receipt, image edit identity/lossy return refusal,
  live native widget edit/return preserves parent draft and does not submit literals.
- Includes FOUR actual native client queue paths (Coding/Claw × edit/return) with
  production shared widget + owning SDK/socket supervisor and held offline model:
  one child call only, parent canonical history/draft unchanged. Actual CLI queued
  /quit remains literal child draft; revision-stale remove refuses, confirmed return
  receipt reconstructs text without parent admission, explicit child cancel settles.
- TWO latest actual private-tmux production paths **2 passed, 228 deselected**
  (4.90s), `/tmp/pass147-terminal-final.log`: full paged child-queue physical review
  → actual removed receipt → child-only draft restore → explicit owned cancel →
  parent termios/cursor/editor recovery, plus unchanged Claw specialization/native
  restoration. Original physical checks retained, no paid model/auth/login/daemon.
- Final Ruff `/tmp/pass147-ruff-final.log` and all-seven strict mypy **470 sources**
  `/tmp/pass147-types-final.log` clean; whitespace clean. Offline Agent/Coding/Claw/
  TUI wheel+sdist: EIGHT current affected production modules exact byte-for-byte in
  BOTH artifact types `/tmp/pass147-artifacts.log`. No root suite restart or claim.
- No dependency/manifest/lock edit, commit/reset/stash or unrelated work overwritten.
- KEEP OPEN: stopped-child undelivered-input recovery, image-capable child drafts,
  richer raw/custom/terminal/render source APIs, native credential/platform/storage
  evidence, wider MCP/provider/auth interoperability, later atomic workspace gate
  and all original unresolved rows. Goal ACTIVE; OS sandbox excluded. This pass
  closes live queue controls, not every queue/supervision/native-platform workflow.


## Pass 148 — settlement-persisted stopped-child input recovery, no restart/replay

- Previous turn was verified implementation progress (pass147). Close the graceful
  stopped-child unadmitted-input recovery gap through actual Agent/supervisor/store,
  shared native queue viewer and Coding RPC, not a speculative mirrored FIFO.
- After actual producer/agent cancellation/idle settlement, drain only still
  identified inputs. Already admitted IDs were consumed out of that queue; actual
  consumed follow-up is proven absent from recovery. Pending/checkpoint-selected
  BUT unadmitted inputs become stopped recovery, not live edits. Release the agent
  only after capture; persist typed undelivered inputs with the complete child record.
- Extend bounded ChildStore validation/loading and supervisor restore, retaining
  old records without the field. Duplicate/unknown/selected/oversized/invalid typed
  image records fail closed rather than drop/relaunch. No agent/model creation on
  load or recovery copy. Private image bodies retained in bounded saved data; public
  queue metadata exposes count only, not binary. Whole-record/save capacity failures
  remain explicit, never advertised as successful truncated durable recovery.
- Stopped queue API shows stopped:true. Reviewed recover is NON-DESTRUCTIVE copy
  with exact recovered row and original input/revision/parent/child scope: retained
  backing input cannot vanish on lost/canceled receipt. Shared viewer u restores a
  fresh read-only TASK draft only after matching receipt. No steering dead workers,
  parent draft overwrite, model call, permission/model-context restoration or
  automatic restart. Existing fresh start still requires full task/config review.
- Separate reviewed permanent remove stages the next stored record, waits for actual
  filesystem owner, then publishes removal. Save failure retains memory/disk. One
  queue_work/settling owner prevents overlapping close/discard and parent navigation.
  Cancellation and supervisor shutdown wait for admitted write/actual commit before
  releasing metadata/owner loop; no invented rollback. Stopped edit/reorder refuses;
  editing belongs in the new draft. Images refuse lossy copy to text-only drafts.

Verification snapshot — pass148:

- Generic supervision/config/model/store/recovery + native Coding/Claw child/RPC
  + shared source/queue widgets **221 passed** (41.51s), `/tmp/pass148-final.log`.
- Latest persistence/recovery/typed-image/native BOTH-client/actual CLI/widget cases
  **32 passed** (9.17s), `/tmp/pass148-done-tests.log` (overlap). Actual cancellation
  → saved reload without model factory → copy retains durable input → explicit
  discard; admitted follow-up not re-offered; failed save unchanged bytes/tuple;
  canceled write and owner shutdown settle before releasing slot/thread; corrupted
  saved collection refuses; image retained typed but text-only copy denied.
- Includes SIX actual native shared-widget SDK/socket flows (Coding/Claw × live
  edit/live return/stopped recover) preserving parent draft/canonical state and
  original one held child call. Stopped copy opens NEW read-only task form, not old
  child steering. Actual Coding CLI canceled child retains literal /quit input in
  its durable record, source-copy does not restart/erase/submit it, parent stays empty.
- THREE actual private-tmux production paths **3 passed, 228 deselected** (7.54s),
  `/tmp/pass148-terminal.log`: Coding live return and canceled-child saved recovery
  full physical target/content review → child-only steer vs fresh-task draft, no
  submission → parent termios/cursor/editor restoration; unchanged Claw specialization
  restoration. Original guards retained. No native Windows/AppKit/etc proof inferred.
- Repository Ruff `/tmp/pass148-ruff-done.log` clean; all-seven strict mypy **470
  sources** `/tmp/pass148-types-done.log` clean; whitespace clean. Offline all-four
  Agent/Coding/Claw/TUI wheel+sdist builds; FIVE current affected production modules
  exact byte-for-byte in BOTH types and current Coding/Claw README in sdists:
  `/tmp/pass148-artifacts.log`.
- No full-root restart, paid model/auth/login/dependency download/user-daemon restart,
  manifests/locks/commits/resets/stashes or unrelated work overwritten.
- KEEP OPEN: unclean SIGKILL/power-loss active-run queue durability, image-capable
  child task drafts, richer raw/custom/terminal/render source APIs, original native
  credential/platform/storage evidence, wider MCP/provider/auth interoperability,
  eventual atomic workspace verification and all other unresolved original rows.
  Goal ACTIVE; OS sandbox still excluded. Graceful recovery is implemented, not
  every crash/image/native-platform requirement or complete parity.


## Pass 149 — one frozen full-workspace integration checkpoint IN PROGRESS

- Previous goal turn was verified implementation progress (pass148). After ten
  cross-package UI/RPC/runtime passes since pass139, start ONE actual full-root
  checkpoint: `uv run pytest -c pyproject.toml -q --tb=short`, owned tool handle
  **75509**, pytest PID **3636509**, log `/tmp/pass149-workspace.log`.
- This process is authoritatively LIVE, not a failed/finished timeout. At the last
  observation it reached **64%**, 7m37s elapsed. Keep polling the SAME handle/PID;
  do not restart the run, report an atomic green result or mark goal complete while
  it remains pending. Root command records WORKSPACE_EXIT when actually terminal.
- Freeze production/test/manifest/lock sources for valid atomic evidence. Snapshot
  `/tmp/pass149-source-before.json`; mid-run byte/hash audit
  `/tmp/pass149-source-mid.json` reports ZERO changes. No implementation edit in
  this pass. Documentation tracking and /tmp owned probes are separate from sources.
- Use waiting time for a bounded next-gap feasibility check: installed native
  Secret Service tooling can run against a NEW private session bus and owned
  temporary HOME/data/runtime/control directories, with graphical display disabled.
  `/tmp/pass149-keyring-probe.py` actual gnome-keyring service + secret-tool save,
  lookup, clear, then absent lookup all passed with ONLY dummy credentials.
  No user keyring or inherited session bus contacted; owned service/bus exited.
  Literal dummy credential absent from owned disk files; this is not a complete
  crypto/storage/privacy/locked-store/backend/UI conformance claim or implemented
  XDOG native storage. `/tmp/pass149-keyring-probe.log`. Native vault routing,
  reviewed binding/locked-store/cancel behavior and platform evidence remain OPEN.
- Read primary libsecret/Secret Service documentation for that future backend; no
  hosted model/login/token POST, dependency install, paid call or user-daemon restart.
  An unrelated long-lived compaction pytest was observed separately; it is NOT this
  owned checkpoint and was neither killed nor treated as our progress/evidence.
- Goal remains ACTIVE with original full scope; OS sandbox excluded. Next action:
  poll 75509, inspect final root result when terminal, then compare source hashes.
  Fix any actual failure only after this owner settles; record exact coverage and
  retain all original remaining audit requirements. No completion claim yet.


## Pass150 — frozen full-workspace checkpoint actually GREEN; storage negative probe

- Previous turn was a verified wait/probe, not a failed run or completion. Poll the
  SAME owned handle75509 to authoritative terminal EXIT0, without restart: pass149
  full root **5355 passed,29 skipped** (1413.78s), `/tmp/pass149-workspace.log`,
  explicit `WORKSPACE_EXIT=0`. No implementation/test/manifest/lock edits during
  the run. Post-run audit `/tmp/pass149-source-after.json`: all924 snapshotted
  source/config files unchanged, no added source files. This IS an atomic green
  checkpoint, unlike historical pass139 segmented results. It is NOT full parity.
- Fresh repository Ruff clean `/tmp/pass150-ruff.log`; all-seven strict mypy **470
  sources** clean `/tmp/pass150-types.log`; whitespace clean. Explicit shipped
  workflow command `uv run xdog-flow test packages/flow/examples/ --allow-script-stub`
  **17 passed**, `/tmp/pass150-workflows.log`. Inspected runner first: unstubbed
  agents fail rather than invoke a real model. No paid/provider/auth/login calls.
- Continue bounded native-storage feasibility investigation OUTSIDE frozen source.
  Negative probe `/tmp/pass150-keyring-locked-probe.py` used a new private bus and
  dummy keyring, locked the collection then ran lookup. Real helper attempted
  unlock/prompt activation and required timeout/cancellation; later unlock/forget
  phase FAILED, so this is not a successful locked-store/whole-backend test.
  `/tmp/pass150-keyring-locked-probe.log`. It changes the next design action:
  plain CLI lookup must NOT be treated as passive non-prompting vault access or a
  fully implemented reviewed backend. Explicit lock/unlock/prompt/environment/
  bounded-cancel/resource/profile semantics need primary/native evidence.
- Important probe limitation: private bus was started BEFORE script-specific HOME/
  DISPLAY/XDG overrides, so bus-activated helpers inherited a different environment.
  Although credentials and the secret-service instance remained dummy/private, this
  is not proof of complete desktop/process confinement. Do not repeat that flawed
  environment pattern. Owned daemon/bus exited and known activated helper PIDs
  no longer exist; no user daemon was restarted or unrelated old pytest killed.
- Native storage remains UNIMPLEMENTED in XDOG and OPEN. Existing plaintext-store
  reviews/fail-closed platform guards unchanged. No repository implementation
  changes were made just to satisfy the isolated probe or hide its failed cleanup.
- Goal ACTIVE, full original scope preserved, OS sandbox excluded. Remaining
  original host/raw/custom/render/child-image/native-platform/credential/MCP/auth
  requirements need their own implementation/evidence. A green workspace gate is
  progress and integration evidence, not a waiver of those feature gaps.

## Pass151 — explicit Linux native credential storage; packaged helper verified

- Implement opt-in `mcp_credential_backend: secret-service` in Coding global
  settings and Claw top-level config; SDK `credential_backend` selection shares
  the same validation. Default remains the reviewed private plaintext `file`
  backend. Unknown choices refuse, never silently downgrade; project/session
  settings cannot override Coding's global selection. Claw groups have separate
  namespace hashes. Native directory paths are namespace inputs, not token files.
- `oauth_native_store.py` runs adjacent `native_store_worker.py` with fixed OS
  `/usr/bin/python3 -I`, bounded stdin/stdout, suppressed diagnostics and a 10s
  watchdog. Save/forget require second owner-checked admission after preflight;
  cancellation kills/reaps the owned process group. Already admitted mutations
  may have committed; no rollback, replay or automatic plaintext/login fallback.
- Worker pins an already-running Secret Service owner, requires an encrypted
  service session and existing unlocked default collection. Raw non-autostart
  D-Bus calls refuse locked/ambiguous/outside-collection items; mutation prompts
  are dismissed, never launched. No collection creation or automatic unlock.
  Shared strict credential codec preserves resource/profile/review/clock checks;
  existing explicit save/restore/forget UI reviews display accurate native notices.
- Actual owned GNOME keyring fixture starts under a NEW private session bus with
  HOME/XDG/display isolation set BEFORE bus startup. Dummy encrypted roundtrip,
  namespace isolation/forget, manager restart/consent, both actual Coding and
  Claw socket UI owners, locked-store refusal and absent prompt activation/token
  files pass. No real login/model/token POST, user vault or daemon restart.
- `/tmp/pass151-final.log`: **198 passed** (48.45s) across OAuth/storage/client
  auth/refresh/registration, both OAuth UIs, native config/service/helper and
  session/panel regressions. Additional **2 passed/229 deselected** private-tmux
  existing credential save/restore/erase/recovery tests (7.93s),
  `/tmp/pass151-terminal.log`: this terminal evidence is for the FILE backend,
  not native vault tmux interoperability. Targeted groups overlap; do not sum.
- Artifact verification found a real distribution gap: `.gitignore` `*secret*`
  excluded the initial `secret_service_worker.py` from wheels. Rename to
  `native_store_worker.py`, rerun native/config tests: **22 passed** (6.69s),
  `/tmp/pass151-native-renamed.log`. Rebuild Agent offline; all four Agent/Coding/
  Claw/TUI wheel+sdist builds succeed. Byte-verify **22 affected-module entries**
  in all eight artifacts, including the actual helper, against current source:
  `/tmp/pass151-artifacts.log`, verifier `/tmp/pass151-verify-artifacts.py`.
- Document backend selection, prerequisites, timeout/uncertain-effect semantics
  and platform limits in both READMEs; update completion audit. Fresh repository
  Ruff and all-seven strict mypy472 are GREEN after final rename
  (`/tmp/pass151-ruff-packaged.log`, `/tmp/pass151-types-packaged.log`); whitespace
  checks are clean.
  Pass149 frozen full-root checkpoint PRECEDES this source patch; no current
  native-patch full-root coverage or goal-wide parity claim is made.
- Goal remains ACTIVE with original scope; OS sandbox excluded. macOS/Windows
  credential backends, broader native service/MCP/auth interoperability, richer
  raw/canonical/binary/custom/render host/RPC APIs, image-capable child drafts and
  active-run unclean crash queue durability remain OPEN. One GNOME fixture and
  Linux tmux tests do not replace missing native platform evidence.

## Pass152 — child image admission prerequisite, actual default-route factories

- Previous goal turn was PROGRESS: pass151 implemented native storage and fixed
  a missing packaged helper. Begin the image-capable child draft gap by tracing
  the shared panel, runtime, both hosts and RPC/socket boundaries. Existing forms
  and return/recovery controls remain text-only; do NOT mark that gap closed.
- Trace found a real safety/capability prerequisite: `ChildModel.stream` only
  validated images when selection-time metadata existed (explicit model/reasoning
  selection). Default/same-model children could forward retained user/tool and
  context-hook images without that guard; supervisor stream wrapping hides the
  generic adapter's optional resolver. Fix the actual child guard, not a parallel
  tool loop or a global provider/catalog fallback.
- Resolve the actual original child adapter's live model only for rendered image
  contexts. Unknown/invalid/withdrawn/non-chat/nonstreaming/text-only profiles
  refuse BEFORE adapter calls; do not revive an earlier selection snapshot.
  Recheck the profile after validation to refuse capability changes. Text-only
  custom/default adapter requests retain their existing no-resolver-lookup path.
- Share `validate_model_images` with the existing complete-context validator.
  Enforce advertised count/byte/media limits and typed base64 safety, not
  `validate_references`' four-image per-composer cap on whole retained history.
  Tests accept six retained images when the model advertises six or no count;
  explicit smaller limits still refuse. No image pruning or implicit text fallback.
  Existing child transcript/persistence budgets are unchanged, not falsely raised
  to support full-sized draft attachments in this prerequisite patch.
- Actual Coding AND Claw child factories cover inherited user images, retained
  tool-result images and a NEW actual isolated `childimage` context-hook worker;
  vision advertised/text-only/metadata-unavailable cases assert forwarding or
  ZERO adapter calls, unchanged parent state and settled/persisted child records.
  No hosted login/model/paid calls, dependency installs or user-daemon restart.
- Broad child/model/image/supervisor/queue/Coding/Claw/RPC regression group:
  **193 passed** (45.35s), `/tmp/pass152-final.log`. After final defensive source
  guards, fresh focused current-source model/image/both-factory group:
  **71 passed** (7.05s), `/tmp/pass152-current-image.log`. Groups overlap; do not
  sum or call this an atomic full-root checkpoint. Real existing private-terminal
  Coding child approval and Claw command/recovery compatibility: **2 passed,
  229 deselected** (3.97s), `/tmp/pass152-terminal.log`, not image-editor evidence.
- Fresh repository Ruff clean and all-seven strict mypy **472 sources** clean:
  `/tmp/pass152-ruff-final.log`, `/tmp/pass152-types-final.log`; whitespace clean.
  Offline Agent wheel+sdist rebuilt; **6 module entries** byte-verified against
  current `supervision_model.py`, `image_context.py` and the native storage helper
  (preserving pass151's distribution regression). Both READMEs/audit updated.
- Goal ACTIVE, original scope unchanged and OS sandbox excluded. Next substantive
  work remains image-capable child editors plus typed transport, fully reviewed
  exact-byte queue return/stopped recovery and full-capacity persistence. Platform
  storage/native desktop, richer host/raw/custom/render/RPC APIs, active-run crash
  queue durability and broader MCP/auth/service interoperability remain OPEN.

## Pass153 — actual reviewed child image submission in both native clients and RPC

- Previous turn was PROGRESS: pass152 closed default-route rendered-image admission.
  Implement the next actual feature slice, without reducing the original goal:
  typed images on ChildSupervisor start/steer/follow-up and Coding/Claw SDK methods,
  real shared child file/clipboard/inspection/preview editors, native application
  owners, Claw checksummed upload socket path and independently negotiated Coding
  RPC child_images capability. No extra agent/tool loop or permission escalation.
- Child form Ctrl+O reads a literal workspace-relative/absolute path asynchronously;
  Ctrl+V reads clipboard and Ctrl+B lists/previews/removes/clears child images.
  Parent text/cursor/attachments remain separate. Image-only inputs work. Full
  physical review pages task/access/config/image metadata/SHA256 before Enter;
  late reads invalidate consent instead of silently changing reviewed bytes.
  Exact actual-owner accepted_input text/digest/MIME receipt is required to clear
  a draft. Mismatches/invalid receipts restore editing and retain exact bytes;
  uncertain receipts stay disclosed on same-context reopening, never auto-replay.
- Separate image payload storage from 512 KiB text/metadata transcript limits.
  Four images per input, 10 MiB each, 40 MiB retained distinct payload guard;
  also bound serialized canonical and pending/recovery collections, so repeated
  identical inputs cannot exhaust persistence by defeating deduplication.
  Initial images are saved before factory work and retained on setup failure;
  no duplicate initial field when canonical history already holds those bytes.
  Queued images survive actual settlement/reload without agent construction.
  UI retained-byte checks also account for other local parent/child draft caches.
- Switched-model conservative context preflight now excludes base64 transport
  from text size and uses a rough image allowance; it is NOT exact tokenizer/
  billing proof. Actual model count/byte/media validation, neutral payload limits,
  original read-only/inherited approval restrictions and budgets still apply.
- Claw advertises exact child_images:1 and forwards refs through the existing
  orchestrator/group/session path. Large image-bearing canonical records are NOT
  shoved into a socket line: control responses explicitly omit those bodies and
  point to the separate complete DISPLAY source reader. Successful refs released;
  exact request replay reads the cached receipt without reexecution/requiring refs.
  Coding RPC requires child_images:1 separately; inline images or owned image_refs,
  never both, on scoped start/steer/follow-up. Successful refs consumed. Capability
  revocation cancels pending image work, not already admitted effects/permissions.
- Actual BOTH application UI/factory owners exercise file read, full review,
  initial submission, queue acceptance/cancel/persistence, untouched parent draft,
  and 10 MiB bounded payloads. Real Claw socket uploads and an actual Coding RPC
  subprocess with 2 MiB chunks provide transport evidence. These use dummy PNG
  headers/bounded bodies and offline adapters, not hosted decoder/provider proof.
- Broad regression checkpoint **253 passed** (51.32s), `/tmp/pass153-final.log`.
  Final-source focused UI/model/factory/RPC plus terminal group **73 passed,
  231 deselected** (24.60s), `/tmp/pass153-current-ui.log`. Actual recovery/queue/
  subprocess CLI group **29 passed** (9.08s), `/tmp/pass153-recovery-cli-final.log`.
  Final malformed-success/mismatched-receipt/native-submission group **21 passed**
  (4.14s), `/tmp/pass153-final-receipts.log`. A final actual native/socket group
  **6 passed** (4.18s), `/tmp/pass153-final-native.log`, additionally covers a TEXT
  child whose ONLY images are undelivered queue rows: cancel receipt stays below
  the socket line bound, exact bytes stay durable, and released-ref receipt replay
  does not enqueue twice. Groups overlap; never sum as root evidence.
  Existing source/store/queue/form negatives stayed fail-closed, not relaxed.
- Both new private-tmux image file/preview/full-review-CANCEL/restored editor/native
  terminal paths **2 passed,231 deselected** (5.84s), `/tmp/pass153-terminal.log`.
  This is actual image editor preparation/recovery, not hosted image model execution.
  No login/token POST/paid model calls, installs or user-daemon restarts.
- Repository Ruff/all-seven strict mypy **474 sources** clean; whitespace clean.
  All-four Agent/Coding/Claw/TUI wheel+sdist builds offline, **34 affected module
  entries** byte-verified against final source, including native vault helper:
  `/tmp/pass153-artifacts-final.log`. Both READMEs and completion audit updated.
  Pass149 atomic full-root checkpoint predates these patches, not current coverage.
- Goal ACTIVE, full scope preserved; OS sandbox excluded. NEXT: actual binary
  receipt transport for image queue return/stopped copy, initial failed-task image
  recovery editor and cross-context/reconnect draft browsing/durability. They
  remain OPEN: current queue operations refuse loss rather than pretend image
  recovery exists. Further native platforms/storage/clipboard/decoder/provider
  interoperability, raw/canonical/custom/render/RPC host APIs and active-run crash
  queue durability retain their original requirements; no subset waives them.

## Pass154 — verified non-destructive stopped-image recovery, native/socket/RPC owners

- Previous goal turn was PROGRESS: pass153 implemented real reviewed child image
  submission and bounded image/text storage. Continue the original gap, not a
  redefinition of completion: add an explicit stopped-input `recover_images`
  action and actual binary copy path into a separate fresh read-only child draft.
  LIVE image return/removal remains OPEN and still refuses lossy text return.
- Agent preparation validates the immutable saved image row off the owner loop,
  hashes exact bytes/content/MIME types and rechecks stopped state/queue revision
  after preparation. It returns an owned receipt/payload only. Source JSON and
  undelivered inputs are not removed, rewritten or restarted. Legacy text recovery
  still refuses image rows. No new model/compaction/tool/approval invocation.
- New bounded sender and standalone TUI receiver: `child_recovery_begin`, ordered
  64 KiB raw chunks, end; manifest sizes/SHA/MIME/content tied to request/session/
  child/input/queue revision and RPC connection/parent/child leases. Four images,
  10 MiB each / 40 MiB spool quota; sender ten-second deadline. Scope/order/size/
  digest/MIME/partial data are rejected, owned private spools closed on fail/cancel.
  Binary bodies never appear in the final socket/RPC control JSON line.
- Actual Coding worker decodes off UI loop; actual Claw gateway streams through
  its existing group/session owner path and client I/O receiver. Post-control
  session/child-owner checks fence branch swaps. Claw exact capability
  child_image_recovery:1; independently negotiated Coding RPC recovery + queue
  capabilities, revocation cancellation and native ordered output send receipts.
  Sending new image prompts and reading stopped images are separate capabilities.
- Only a full verified byte transfer plus exact reviewed retained-row/owner receipt
  publishes the new child form. Separate unique local recovery slots preserve
  existing default child drafts and previously edited copies. Access read-only,
  inherit_context false; parent draft/images/cursor and persisted original remain
  unchanged. Hidden caches are NOT a complete draft browser or crash recovery.
- Larger actual copies caught two concrete integration problems: a successful
  periodic roster reply incorrectly cleared the queue's pending receipt, now
  fixed without relaxing row/owner checks; the isolated Claw test used asyncio's
  64 KiB default reader unlike production's 4 MiB reader. Use a still-smaller 1 MiB
  test reader for the existing 87 KiB encoded frames, not wider terminal/protocol
  production bounds. Tiny tests alone would have missed both cases.
- Actual both app/UI owners exercise stopped-image copy at 10 MiB, real Claw
  socket framing and unchanged durable original/model count/parent draft. Real
  Coding RPC subprocess exercises 2 MiB OUTBOUND binary copy and exact receipt;
  independent capability rejection and mid-copy revocation are tested too.
  Sender/receiver faults close spools and never manufacture a partial draft.
- Final broad targeted regression gate **202 passed** (49.20s),
  `/tmp/pass154-final.log`. Earlier interop/RPC group **44 passed** (19.88s),
  `/tmp/pass154-rpc.log`; native owner group **6 passed** (5.61s),
  `/tmp/pass154-native.log`, overlap with final gate, never sum as root evidence.
  New both-client private-tmux full recovery review / fresh form / image inspection
  and preview / ZERO execution / native cursor+termios restoration: **2 passed,
  233 deselected** (4.99s), `/tmp/pass154-terminal.log`. Coding terminal fixture
  waits for actual ready status, not a model name echoed in its shell command.
- Repository Ruff and all-seven strict mypy **476 sources** clean; whitespace
  clean. Agent/Coding/Claw/TUI wheel+sdist built offline and **24 affected module
  entries** byte-verified against current source, `/tmp/pass154-artifacts.log`.
  Both READMEs/audit updated. No hosted auth/model/token POST, paid call, dependency
  install or user-daemon restart. Pass149 root checkpoint still predates these
  patches; targeted evidence is NOT a current full-root or platform parity gate.
- Goal ACTIVE, full scope preserved, OS sandbox excluded. Remaining: verified
  live image-return/removal protocol, initial failed-task recovery editor, actual
  local/cross-context/reconnect draft browsing and crash durability. Further native
  credential/clipboard/decoder/provider/MCP interop, raw/canonical/render/custom/
  RPC host capabilities remain on their original open rows. No subset waives them.

## Pass155 — reviewed local child draft browser, current/old contexts and cache release

- Previous turn was PROGRESS: pass154 added actual stopped-image recovery. Make
  the retained local draft slots accessible instead of pretending hidden caches
  are usable recovery. Both native clients now wire **d** in the child roster and
  `/agents drafts` to a shared browse/search/full-review component.
- Catalog captures owned current/old context cache identities, actual workspace/
  session/group provenance, action/slot, text, images, source configuration/access,
  uncertainty and saved editor cursor. It contains ordinary start/steering/follow-up
  drafts and separate recovered-image copies. No file/URI/provider lookup needed.
- Enter prepares a NON-DESTRUCTIVE fresh read-only start draft with context
  inheritance false, preserving text/images but importing NONE of the old access,
  tools, instructions, model/reasoning or preset configuration. Original cache
  remains. Each copy has a distinct slot; uncertainty is kept in that slot, not
  merely flashed once. No queue input/tool/model/navigation/restart is performed.
- r reopens only CURRENT-context drafts after full review; start settings/cursor
  restore, existing-child drafts require an actually listed running child. Old
  contexts/stopped children cannot regain execution rights; explicit fresh copy
  is offered instead. x discards only the reviewed LOCAL draft, never active work,
  pending backend input, permissions or the durable stopped-input record.
- Full provenance/task/config/image digest review needs physically painted pages;
  pasted/repeated/unpainted Enter cannot commit. Catalog/source identity/value and
  current browser owner are rechecked before action. Mutable config replacement,
  source removal and callback owner changes do not reuse old review grants.
- Browser transition closes/releases its hidden composer after snapshotting, so
  explicitly discarding a cache really releases retention accounting instead of
  leaking a second hidden image owner. Existing retained image accounting remains
  bounded; immutable images in a separate copy remain owned there. Parent draft,
  images and cursor are never borrowed. Restore the settings/access/preset hints
  alongside image hints rather than hiding those existing controls.
- Actual native app owner tests cover old-context image/text copy, conservative
  settings/access, exact bytes, preserved source/uncertainty, local-only discard
  and untouched parent/no model calls. Component tests cover search/full review,
  no paste/repeat commit, replaced/mutated/removed source fencing, old-context
  reopen refusal and explicit current reopen. These are not a blanket proof of
  every live reconnect/gateway race or remote RPC draft browsing.
- Final targeted gate **103 passed** (27.20s), `/tmp/pass155-final.log`; initial
  component/native draft group **30 passed** (5.97s), `/tmp/pass155-drafts.log`,
  overlaps the final group. Extended both private-tmux stopped-image recovery
  tests physically browse the retained local draft, fully review fresh copy,
  inspect resulting read-only form/image/text, and verify zero execution plus
  native terminal/cursor/termios recovery: **2 passed,233 deselected** (5.49s),
  `/tmp/pass155-terminal.log`. No paid/auth/model calls or user daemon restart.
- Repository Ruff and all-seven strict mypy **477 sources** clean; whitespace
  clean. Coding/Claw/TUI wheels+sdists rebuilt offline, **10 affected module
  entries** byte-verified, `/tmp/pass155-artifacts.log`; Agent source unchanged
  from pass154. Both READMEs/audit updated. No new root-wide checkpoint; pass149
  full-root evidence still predates these implementation patches.
- Goal ACTIVE with original scope, OS sandbox excluded. This is a native local
  MEMORY catalog, not durable cross-process/unclean-crash recovery, current full
  CLI parity or remote RPC draft management. Verified live image return, failed-
  initial-task recovery, broader raw/canonical/render/custom/RPC host surfaces and
  native/platform/credential/clipboard/provider/MCP interoperability remain OPEN.

## Pass156 — explicit retained initial-task recovery after setup failure

- Previous turn was PROGRESS: pass155 exposed retained local child drafts. This
  increment makes setup-failed initial child tasks available in the stopped-input
  queue, including images saved before an Agent/prompt could be constructed.
  The source row is explicitly initial-task, separate from delivery-lane inputs.
- Track initial-state pending/admitted/handled/uncertain/discarded separately.
  Actual initial prompt message admission or initial extension handling settles
  that state; later queued messages do not rewrite it. A provider failure AFTER
  admitted history does not turn an executed task into an invented unsent draft.
  Legacy/interrupted records lacking admission evidence remain UNCERTAIN, not
  claimed effect-free. Initial bytes remain available even when history is empty.
- u uses recover_task to create a separate read-only fresh draft (context
  inheritance false); image data traverses the existing exact-receipt/checksummed
  binary copy path. The original record is unchanged, no model/agent/compaction
  request occurs and parent/sibling drafts are untouched. x uses remove_task:
  actual persistence must commit before publication/releasing initial image bytes.
  Failed writes preserve both owners; cancellation waits for the admitted writer
  to settle. Ordinary queue actions cannot reinterpret an initial-task source.
- Exact child_task_recovery:1 advertised by Claw and negotiated independently by
  Coding RPC alongside queues; image copies additionally need image-recovery
  capability. Revocation stops pending task copy jobs. Existing view/connection/
  child/revision/manifest guards apply; no old execution permission is imported.
- Actual native Coding AND Claw setup-failure recovery tests with/without images
  assert unchanged persisted record, fresh read-only draft, exact original content,
  zero model calls and untouched parent. Runtime tests cover legacy uncertainty,
  admitted-provider failure, failed discard and cancellation settlement. RPC tests
  verify independent capabilities and typed non-executing recovery. Terminal
  checks cover both ordinary stopped image and UNCERTAIN initial-task sources.
- Authoritatively polled previously running handles48894/5061 to EXIT0, no restart:
  broad targeted gate **208 passed** (49.34s), `/tmp/pass156-final.log`;
  both-client stopped/initial image recovery + preview + local draft browse +
  native cursor/termios checks **4 passed,233 deselected** (9.50s),
  `/tmp/pass156-terminal.log`. Final defensive admission/revocation changes and
  added canceled-discard regression: **56 passed** (17.67s),
  `/tmp/pass156-current.log`. Groups overlap; not a current full-root checkpoint.
- Initial terminal failure was an assertion looking for the full task in a
  deliberately truncated roster row. Require actual interrupted-state marker,
  then full task text in the owned recovery editor; do not widen terminal or
  truncate recovery data. Both final native terminal cases actually pass.
- Repository Ruff and all-seven strict mypy **477 sources** clean; whitespace
  clean. All-four Agent/Coding/Claw/TUI wheel+sdist builds offline, **18 affected
  module entries** byte-verified, `/tmp/pass156-artifacts.log`. Both READMEs/audit
  updated. No provider login, paid model, installs or user-daemon restart.
- Goal ACTIVE, original scope intact; OS sandbox excluded. Live image-return
  removal/admission protocol, durable local/active-run crash recovery, remote RPC
  draft management, richer raw/canonical/custom/render host APIs and remaining
  platform/native credential/clipboard/provider/MCP interoperability remain OPEN.
  Pass149's atomic full-root checkpoint still predates these implementation edits.

## Pass157 — two-phase live image return runtime foundation, client integration OPEN

- Previous goal turn was PROGRESS: pass156 verified initial-task recovery. Inspect
  actual queued-image return paths and add the required shared runtime contract,
  not a lossy text conversion or an unreviewed dequeue. Native/socket/RPC image
  return remains OPEN until its actual host ownership/transport/removal review is
  wired and exercised. No parity/goal completion declaration from this foundation.
- `supervision_return.py` prepares a bounded exact immutable input byte copy off
  the child owner loop, then rechecks the same unselected queue row/revision.
  Preparation returns random token + exact content/digest/MIME receipt and image
  payload but leaves the original queued. Separate explicit return_owner lease is
  REQUIRED; existing generic tool/slash calls cannot silently borrow host authority.
- `return_commit` requires that owner/token/input/revision/verified receipt and
  performs one serial owning-loop removal of only the surviving unselected row.
  A selected/consumed/edited/stopped/expired/foreign/unverified input refuses with
  no removal. `return_cancel` validates owner/input/revision/token and releases
  preparation without modifying input. Leases expire after30 seconds; four combined
  prepared/completed records per child. Exact same-owner/content commit retries
  return an independent copy of the original receipt, not another removal.
- Application admission invariant only: these process-local leases/cache are NOT
  OS transactions, permission grants or active-run/power-loss durability. The
  caller must actually verify/preserve bytes and separately obtain removal consent;
  neither transport nor that UI consent is inferred by the runtime. No new model,
  tool or execution loop. Legacy image return still refuses lossily as before.
- Actual held-Agent tests cover 10 MiB image preparation/commit, no model reentry,
  unchanged queue before commit, exact once-only retries, mutated client receipt
  isolation, concurrent queue edits during preparation, canceled preparation with
  no surviving lease, selected/edited/stopped/expired/foreign/mismatched receipt
  rejection, bounded slots and owner-scoped cancellation. Preparation race checks
  preserve the changed current row instead of removing a stale input.
- Final targeted runtime + Coding/Claw child regression group **132 passed**
  (24.64s), `/tmp/pass157-final.log`; earlier queue/recovery group **38 passed**
  (1.16s), `/tmp/pass157-runtime.log`, overlaps. Existing physical Coding child
  queue text-return/stopped-copy/native-terminal compatibility **2 passed,
  235 deselected** (3.97s), `/tmp/pass157-terminal.log`: NOT image-return UI proof.
  All owned handles polled to EXIT0, no restarted long-running gate.
- Repository Ruff clean; all-seven strict mypy **478 sources** clean; whitespace
  clean. Agent built offline; **6 affected module entries** byte-verified in wheel
  and sdist, `/tmp/pass157-artifacts.log`. Both client READMEs/audit explicitly
  disclose contract versus unimplemented client integration. No paid/provider/
  login/install/user-daemon actions. Root pass149 still predates these source edits.
- Goal ACTIVE, full original scope preserved; OS sandbox excluded. NEXT: actual
  native Coding/Claw and RPC binary preparation receipt, second painted removal
  review/commit and retained child draft owner integration with revocation/closed-
  peer evidence. Durable local/active-run crash recovery, remote draft management,
  richer host/raw/canonical/custom/render surfaces and remaining platform/native
  credential/clipboard/provider/MCP interoperability stay on original OPEN rows.

## Pass158 — scoped Coding RPC live image-return transport and commit

- Previous turn was PROGRESS: pass157 implemented the actual two-phase supervisor
  contract. Wire Coding RPC to that contract without pretending a transfer is
  removal consent. Independently negotiated child_image_return + controls/queues
  and a separate child_return_owner lease gate prepare/commit/cancel requests.
- Capability withdrawal/re-enable rotates the return lease and cancels its pending
  jobs; a token created under the old owner cannot be revived by updating the
  packet envelope. Existing child commands remain compatible without the new
  return-only field. No extension dialog/approval/model authority is borrowed.
- Prepare uses the actual supervisor owner and existing checksum-bound binary
  sender with bounded native output receipts. Async current checks run on the
  child owning loop rather than inspecting a mutable queue from the RPC loop.
  Complete bytes + final prepared metadata leave the input queued. A separate
  explicit commit carries exact verified receipt/token/input/revision and current
  lease; successful reply is small returned metadata, never image bodies.
- Add actual host revocation guard checked at the supervisor removal boundary,
  including recheck after asynchronous preparation. Canceled/failed stream settles
  prepared-token cleanup on the owner loop. Closed peer/revocation does not remove
  an input, restart work or manufacture a returned draft. Already admitted commits
  remain uncertain across transport failure, not automatically rolled back/replayed.
- Actual offline Coding CLI subprocess exercises 2 MiB uploaded queued image,
  outbound checksummed transfer into the verified receiver, explicit list proving
  still queued before commit, then separate commit/retry once-only metadata and
  ZERO parent history. Runtime/RPC tests cover live receiver exact bytes, closed
  peer, foreign-owner, withdrawal and disable/re-enable token rejection, cleanup,
  and owning-loop guard refusal. This is not native image-return button evidence.
- Final runtime/queue/recovery/receiver/RPC/subprocess gate **93 passed** (22.69s),
  `/tmp/pass158-current.log`. Earlier actual-return/RPC group **55 passed**
  (21.91s), `/tmp/pass158-final.log`; groups overlap, never sum as root coverage.
  Repository Ruff and all-seven strict mypy **478 sources** clean; whitespace clean.
  Agent/Coding rebuilt offline; **6 affected module entries** byte-verified in both
  wheels/sdists, `/tmp/pass158-artifacts.log`. Both READMEs/audit updated.
- No paid/auth/model network calls, installs or user-daemon restarts. Original goal
  ACTIVE, OS sandbox excluded. Native Coding/Claw second painted removal review,
  verified draft ownership/retention and Claw socket live return remain OPEN, as do
  durable active-run/local draft crash recovery, remote draft management, richer
  raw/canonical/custom/render host APIs and remaining platform/credential/clipboard/
  provider/MCP interoperability. No current atomic full-root checkpoint is claimed.

## Pass159 — native Coding two-reviewed live image return with accessible precommit backup

- Previous turn was PROGRESS: pass158 wired scoped Coding RPC live returns. Wire
  the shared queue component into native Coding's actual owner, while keeping
  Claw's native/socket image-return capability disabled until equivalent wiring.
- u on a live image input prepares an independently full-painted review without
  removal, then verifies exact byte/content/MIME/token receipt. Before a SECOND
  visible removal review, preserve the verified copy as a separate read-only
  uncertain local backup in the actual draft browser. Backup capacity failure
  refuses consent/removal. The memory copy is not cross-process/crash persistence.
- Second painted physical confirmation sends return_commit under the exact native
  panel+queue-view lease. After actual matched removal receipt, a separate child
  steer/follow-up draft owns exact image bytes without overwriting parent/sibling
  draft. No automatic model call/submission. Cancel releases preparation with
  original still queued; while commit settles, queue Escape does not detach it.
  Forced close/lost/malformed receipt retains the precommit browseable backup.
- Native worker checks owner at actual runtime admission, not just before a thread
  starts. Late queue results are fenced by both panel and queue-view token to
  avoid adopting an old transfer into a replacement view. Claw still refuses live
  image return instead of borrowing this local Coding ownership or text fallback.
- Actual native Coding 10 MiB commit/cancel tests assert two separate reviews,
  input retained before commit, exact read-only backup, returned child draft,
  unchanged parent/history/model count and accessible retained draft catalog.
  Shared component malformed commit receipt test refuses publication and keeps
  its verified backup. Existing Claw/stopped/task/draft paths retain compatibility.
- Final broad targeted gate **116 passed** (28.78s), `/tmp/pass159-final.log`;
  final-source receipt/native group **26 passed** (7.44s),
  `/tmp/pass159-current.log`, overlap, never sum as root evidence. Real private-
  tmux image queue preparation+SECOND review+returned image editor+cancel child+
  cursor/termios/native recovery **1 passed,237 deselected** (3.14s),
  `/tmp/pass159-terminal.log`. Initial assertion wanted a later JSON action on the
  first page; instead show a real SECOND-review caption and page physical fields,
  not widen/truncate or weaken review guards. Exact image/task stays in editor.
- Repository Ruff/all-seven strict mypy **478 sources** clean; whitespace clean.
  Coding/TUI rebuilt offline; **6 affected module entries** byte-verified in wheel
  and sdist, `/tmp/pass159-artifacts.log`. Both READMEs/audit updated. No paid/model/
  login network, installs, user daemon restarts or unrelated worktree reset.
- Goal ACTIVE, original scope preserved; OS sandbox excluded. NEXT Claw socket
  live-return verified binary transfer, separate connection lease/revocation and
  equivalent native two-reviewed draft/backup ownership. Durable local/active-run
  crash recovery, remote draft management, richer raw/canonical/custom/render host
  APIs and remaining native platform/credential/clipboard/provider/MCP interop stay
  OPEN. Pass149 atomic full-root checkpoint still predates current source changes.

## Pass160 — Claw socket/native two-reviewed live image return

- Previous turn was PROGRESS: pass159 verified native Coding's two-review return.
  Implement Claw's equivalent actual socket/client path, reusing the same shared
  queue reviews and accessible precommit read-only uncertain memory backup.
- Explicit socket ping child_image_return:1 negotiates a random connection owner;
  each return request carries the exact queue_view token. Combined connection/view
  lease passes through existing orchestrator/session into the actual supervisor
  admission guard. Disable/re-enable rotates and revokes its lease; teardown
  revokes it before cleanup. Foreign views/old tokens cannot remove queued input.
- Preparation uses bounded checksummed binary frames with connection/view scope,
  no large image bodies in final control JSON. Async queue-current checks execute
  on the actual child owner loop. Failed/canceled streams settle lease cleanup;
  preparation alone never removes or executes. Second physical review commits only
  exact verified bytes/token/input/revision. Local backup survives lost receipts.
- Client matches pending queue-view identities for binary ready and final control
  receipts; late replies cannot populate a replacement view. Native shared panel
  opens a distinct child-only image draft after successful removal, with parent/
  sibling drafts untouched. Claw support remains fail-closed on older gateways.
- Actual both-client native gate **8 passed,37 deselected** (4.20s),
  `/tmp/pass160-native-final.log`, including 10 MiB commit/cancel and precommit
  backup. Separate socket negative gate **3 passed,22 deselected** (0.96s),
  `/tmp/pass160-socket.log`, exercises foreign-view commit, disable/re-enable and
  disconnect; unremoved original persists after child cancellation. Final targeted
  integration gate **119 passed** (30.10s), `/tmp/pass160-final.log`. Groups overlap,
  not a summed/atomic full-root checkpoint.
- Extend the real live image-return private-tmux fixture to BOTH applications:
  prepare review, verified bytes, SECOND removal review, returned child image/task,
  cancel child, parent cursor/termios/native-scrollback recovery **2 passed,
  237 deselected** (5.61s), `/tmp/pass160-terminal.log`. No hosted decoder/model,
  login/token POST, dependency install, paid call or user-daemon restart.
- Repository Ruff/all-seven strict mypy **478 sources** clean; whitespace clean.
  Claw wheel+sdist rebuilt offline and **8 affected module entries** byte-verified,
  `/tmp/pass160-artifacts.log`. Both READMEs/audit updated to distinguish current
  implementation from historical unimplemented statements. Other package source
  modules unchanged this pass. Root pass149 checkpoint predates these patches.
- Goal ACTIVE with original scope; OS sandbox excluded. Verified native/RPC live
  image return is progress, not a waiver of durable local/active-run crash recovery,
  remote draft management, richer raw/canonical/custom/render host APIs or missing
  native platform/credential/clipboard/provider/MCP interoperability. Precommit
  backup remains MEMORY-only; actual power-loss/process-crash durability remains
  OPEN. Next integration checkpoint and remaining named gaps need their own work.

## Pass161 — real concurrent image-return budget and fail-closed queue parsing

- Previous turn was PROGRESS: pass160 completed Claw native/socket return. Inspect
  the shared runtime under simultaneous preparations before moving into durability.
  Found actual limit bypass: all requests could pass the four-lease check before
  asynchronous byte preparation admitted any lease. Reserve an in-flight slot
  BEFORE yielding and release in finally on success/failure/cancellation; combined
  preparing/prepared/completed count remains bounded. No input removal on prepare.
- Simultaneous held real-owner requests prove four admitted preparations, fifth
  refusal BEFORE binary work, canceled slot release, replacement admission, exactly
  four resulting leases and unchanged queued image/model-call count. This changes
  authoritative safety behavior, not just a restatement or easier test subset.
- Claw previously examined `.get` on unvalidated JSON queue shapes. Parse through
  the existing strict typed queue contract first: arrays/null/scalars/missing fields/
  invalid JSON produce failed child_result while the same owned socket still
  answers ping. No unintended disconnect, agent construction or queue mutation.
- Final runtime/native/RPC/socket targeted group **109 passed** (29.85s),
  `/tmp/pass161-final.log`; focused concurrent/runtime tests **15 passed** (0.68s),
  `/tmp/pass161-concurrency.log`, overlaps. Both private-tmux live-image two-review/
  returned-draft/native-terminal regressions **2 passed,237 deselected** (4.80s),
  `/tmp/pass161-terminal.log`. All owned handles authoritatively EXIT0, no restarts.
- Repository Ruff/all-seven strict mypy **478 sources** clean; whitespace clean.
  Agent/Claw wheel+sdist builds offline, **4 affected module entries** byte-verified,
  `/tmp/pass161-artifacts.log`. Both READMEs/audit updated. No hosted auth/model,
  paid calls, dependency install, user-daemon restarts or unrelated worktree reset.
- Goal ACTIVE, full original scope intact; OS sandbox excluded. Durable local and
  active-run crash recovery, remote draft management, richer raw/canonical/custom/
  render host APIs and missing native/platform/credential/clipboard/provider/MCP
  interoperability still OPEN. Pass149 root checkpoint predates these edits; this
  targeted guard is not current whole-root or platform parity evidence.

## Pass162 — cancellation settles actual image threads before releasing capacity

- Previous turn was PROGRESS: pass161 bounded simultaneous image preparations.
  Inspect the real to_thread boundary, not only cancellable async fixtures. Found
  underlying decode/hash work could continue after its asyncio waiter canceled,
  releasing a return slot while owned image work still occupied memory/CPU.
- Add settled_image_work for recovery preparation and sender decoding: shield the
  actual worker, wait through repeated cancellation until it exits, consume any
  late worker failure and preserve cancellation instead of delivering a receipt.
  The existing preparation finally releases capacity ONLY after actual settlement.
  No images/inputs are removed or restarted and cancelled sender emits no frames.
- Tests hold actual thread validation/decode with owned events. Repeated cancellation
  leaves request pending/capacity occupied until release, then no lease survives;
  original queue/model count unchanged. Worker success/error and sender cancellation
  verify no result/frame delivery, late exceptions consumed, all owned work settled.
- Python threads cannot be forcibly stopped: cleanup may outlast the transport
  deadline while bounded local image work finishes. Document this explicitly,
  not as an OS sandbox/process isolation or crash-durability guarantee.
- Final targeted runtime/recovery/native/RPC/socket gate **129 passed** (31.27s),
  `/tmp/pass162-final.log`; worker-focused group **30 passed** (1.48s),
  `/tmp/pass162-worker.log`, overlap. Both native live-image two-review/returned-draft/
  terminal recovery regressions **2 passed,237 deselected** (4.84s),
  `/tmp/pass162-terminal.log`. Owned handles polled to authoritative EXIT0.
- Repository Ruff/all-seven strict mypy **478 sources** clean; whitespace clean.
  Agent wheel/sdist built offline, **2 affected entries** byte-verified,
  `/tmp/pass162-artifacts.log`; both READMEs/audit updated. No paid/provider/login
  calls, dependency installation, user-daemon restart or unrelated worktree reset.
- Goal ACTIVE, full original scope preserved; OS sandbox excluded. Durable local/
  active-run crash recovery, remote draft management, richer raw/canonical/custom/
  render host APIs and missing native/platform/credential/clipboard/provider/MCP
  interoperability remain OPEN; this cancellation fix is not full parity or a new
  atomic whole-workspace checkpoint. Pass149 root evidence still predates patches.

## Pass163 — flush child-record replacement directory entries on POSIX

- Previous turn was PROGRESS: pass162 settled actual image worker cancellation.
  Trace active-crash recovery storage before implementing journaling. Found an
  existing durability prerequisite: fsynced temporary records were atomically
  renamed without flushing the destination directory entry. Add directory fsync
  after rename; newly created child storage directory entries flush their parents
  too. New directories use0700; existing unrelated permissions are not changed.
- Failure after replacement is propagated as an uncertain actual commit, without
  rollback/automatic replay. Owned directory fd closes on success/error. Existing
  platforms without POSIX directory-fd support retain previous behavior; no native
  Windows power-loss or filesystem-equivalence claim. Not an OS security boundary.
- Tests verify actual file-fsync → rename → directory-fsync order, newly created
  parent flushes/private mode, repeated writes without redundant creation, no temp
  artifacts and closed fd on failure. Injected post-rename flush failure proves
  actual replacement remains visible, not falsely claiming old data survived.
- Final runtime/storage/native targeted group **105 passed** (30.70s),
  `/tmp/pass163-final.log`; initial storage group **29 passed** (1.84s),
  `/tmp/pass163-storage.log`, overlaps. Both-client stopped/initial/live image
  recovery and native terminal regressions **6 passed,233 deselected** (12.80s),
  `/tmp/pass163-terminal.log`. All owned handles actually EXIT0, no gate restart.
- Repository Ruff/all-seven strict mypy **478 sources** clean; whitespace clean.
  Agent wheel/sdist built offline; **2 affected module entries** byte-verified,
  `/tmp/pass163-artifacts.log`; both READMEs/audit updated. No paid/auth/model calls,
  dependency installs, user daemon restart or unrelated workspace reset.
- Goal ACTIVE, full scope preserved; OS sandbox excluded. This is completed-write
  durability prerequisite, NOT active-run write-ahead queue persistence, physical
  power-loss testing, durable local drafts or full parity. Those and remaining
  remote draft/raw/canonical/custom/render host APIs/native platform/credential/
  clipboard/provider/MCP interoperability remain OPEN. Root pass149 checkpoint
  still predates these implementation edits.

## Pass164 — write-ahead accepted child inputs with explicit uncertain crash recovery

- Previous turn was PROGRESS: pass163 flushed directory entries. Both real child
  factories now supply ChildStore.save_inputs to the supervisor. Before live
  steering/follow-up acknowledgment, bounded text/image rows are atomically saved
  in a private sidecar with file/directory flush. Failed write refuses enqueue;
  cancellation settles the actual writer, and final child settlement waits for
  admitted queue work before publishing terminal state. Async loops do not perform
  blocking filesystem writes. Queue controls/new inputs refuse while writer owns work.
- Interrupted reload merges only validated sidecars, marking queue rows explicitly
  crash-snapshot/uncertain. No agent constructor/model/tool loop is restarted and
  recovered copies still require explicit fresh read-only review. Snapshots may
  include consumed inputs or predate edits/removals: NOT an exact consumption log
  or proof recovered work had no effects. Existing recovery UI propagates uncertainty.
- Terminal record is durable BEFORE sidecar cleanup; terminal records win over
  leftover sidecars after cleanup interruption. No old snapshot is automatically
  installed into canonical history or treated as executable queue state on reload.
- Actual owned subprocess acknowledges a 2 MiB image input, is SIGKILLed, then
  fresh load verifies exact saved bytes and uncertain non-destructive copy without
  factory invocation. Writer failure/cancellation tests assert no false live
  acceptance, actual thread settlement and no stale overwrite/stranded child.
  Both native app owners now assert acknowledgment wrote the actual image sidecar.
- Final targeted gate **239 passed** (56.24s), `/tmp/pass164-final.log`. Final
  uncertainty UI/journal group **15 passed** (1.67s), `/tmp/pass164-current.log`,
  overlaps. Both-client stopped/initial/live image terminal regressions **6 passed,
  233 deselected** (12.69s), `/tmp/pass164-terminal.log`. Focused owned SIGKILL/
  failure/cancel/terminal-priority tests **4 passed** (1.04s),
  `/tmp/pass164-journal.log`, overlap, not summed root evidence.
- Ruff/all-seven strict mypy **478 sources** clean; whitespace clean. All-four
  packages built offline; **14 affected module entries** byte-verified in wheels/
  sdists, `/tmp/pass164-artifacts.log`. Both READMEs/audit updated. No provider/
  auth/model network, paid calls, installs, user daemon restart or unrelated reset.
- Goal ACTIVE, original scope intact; OS sandbox excluded. Accepted-input snapshot
  preservation is progress toward crash recovery, not full exact consumption/edit/
  removal journaling, physical power-loss proof or durable local editor drafts.
  Those, broader remote draft/raw/canonical/custom/render host APIs and remaining
  native-platform/credential/clipboard/provider/MCP interoperability stay OPEN.
  Pass149 full-root integration checkpoint still predates these source changes.

## Pass165 — persist acknowledged live queue edits/removes/moves/text returns

- Previous turn was PROGRESS: pass164 retained accepted input snapshots after
  actual SIGKILL. Extend that same durability to acknowledged live edits, removes,
  moves and text-only returns. Compute/validate prospective immutable rows without
  mutating Agent; persist off owner loop, then recheck exact revision and apply
  only the still-valid live mutation. No delivery-lane widening or stale-row replay.
- Failed prospective journal writes preserve live queue and prior disk state.
  Cancellation settles actual writer, never falsely acknowledges a live change;
  final settlement publishes actual live/drained state before sidecar cleanup.
  Consumption during write rejects the stale mutation instead of editing an
  already selected input. Prospective snapshot before lost acknowledgment remains
  explicitly UNCERTAIN, not a transactional/exact-once consumption guarantee.
- Actual owned subprocess SIGKILL now parametrically covers ordinary accepted
  image and ACKNOWLEDGED edited image + separately removed input. Restart retains
  edited exact image bytes and excludes removed input, without Agent construction
  or automatic replay. Runtime tests cover image-preserving edit/order/remove/
  text-return disk snapshots, writer failures, repeated cancellation and checkpoint
  selection race. Both hosts share the real supervisor path, no second tool loop.
- Final targeted gate **139 passed** (33.94s), `/tmp/pass165-final.log`; focused
  crash/journal gate **12 passed** (1.97s), `/tmp/pass165-crash.log`, overlap. Actual
  Coding text-return/stopped-copy and both native image-return terminal checks
  **4 passed,235 deselected** (8.52s), `/tmp/pass165-terminal.log`. Owned handles
  authoritatively EXIT0, not restarted because of observation timeout.
- Ruff/all-seven strict mypy **478 sources** clean; whitespace clean. Agent
  wheel/sdist built offline; **4 affected module entries** byte-verified,
  `/tmp/pass165-artifacts.log`; READMEs/audit updated. No paid/model/auth network,
  installs, user daemon restart or unrelated workspace reset.
- Goal ACTIVE, full scope intact; OS sandbox excluded. Exact consumption/handled
  admission and two-phase IMAGE return COMMIT journaling remain OPEN, as do durable
  local drafts, physical power-loss/native conformance, remote draft/raw/canonical/
  custom/render host APIs and platform/credential/clipboard/provider/MCP interop.
  This is acknowledged-change snapshot evidence, not full crash/parity completion.

## Pass166 — journal live image-return commit before acknowledgment

- Previous turn was PROGRESS: pass165 journaled ordinary queue changes. Extend
  the two-phase image return removal boundary: validate exact owner/token/input/
  revision/receipt, compute prospective remaining rows, persist off owner loop,
  then revalidate live lease and owner before removal. Exact completed retries
  need no new journal write. No preparation/removal consent shortcuts introduced.
- Failed write/canceled writer/checkpoint selection/host revocation refuse live
  removal. Cancellation settles actual writer, child queue work remains owned,
  and terminal settlement cannot race a stale prospective write. Pre-ack disk
  replacement remains UNCERTAIN if process dies before actual mutation; not an
  exact-once transaction or automatic replay permission.
- Actual owned SIGKILL fixture now covers acknowledged IMAGE return COMMIT:
  restart snapshot excludes the returned input. Runtime tests inject write failure,
  repeated cancel/selection/revocation while writing and assert no false removal;
  successful exact retry preserves receipt without another durable write.
- Final native/runtime/RPC/socket gate **117 passed** (33.68s),
  `/tmp/pass166-final.log`; initial journal/return group **33 passed** (2.64s),
  `/tmp/pass166-journal.log`, overlaps. Both native live image-return terminal
  reviews/draft/cursor recovery **2 passed,237 deselected** (4.90s),
  `/tmp/pass166-terminal.log`. All owned handles actually EXIT0, no restarts.
- Ruff/all-seven strict mypy **478 sources** clean; whitespace clean. Agent built
  offline; **4 affected module entries** byte-verified in wheel/sdist,
  `/tmp/pass166-artifacts.log`; READMEs/audit updated. No paid/auth/model calls,
  dependency installs, user daemon restart or unrelated workspace reset.
- Goal ACTIVE, original scope preserved; OS sandbox excluded. Exact consumption/
  handled admission journaling and durable local editor/return backups remain
  OPEN, as do physical power-loss/native conformance, remote draft/raw/canonical/
  custom/render host APIs and platform/credential/clipboard/provider/MCP interop.
  Image-return queue removal persistence does NOT persist the memory-only returned
  draft; full goal completion and new root-wide checkpoint remain unproven.

## Pass167 — durable image-return handoff before queue removal

- Previous turn was PROGRESS: pass166 persisted image-return queue removal. Close
  its remaining data-loss handoff: both real factories now persist an exact bounded
  return backup before prospective queue removal and actual commit. Backup bytes
  stay available if the process dies before the native editor receives the result.
- Durable .returns sidecar uses private atomic/fsynced child storage. Interrupted
  reload validates/merges returned backups; settled terminal records include those
  bytes before sidecars are removed. Recovery rows are labelled durable-return /
  uncertain, explicitly copied into fresh read-only drafts or discarded after full
  review. No agent construction, live queue reinsertion or permission restoration.
- Backup/queue write failures refuse removal; actual or uncertain backup writes
  retain conservative in-memory copies for terminal persistence. Backup allocation
  validates bounds BEFORE taking queue-work ownership, avoiding stranded capacity
  when a collection exceeds limits. Returned rows have separate bounded identities
  and 32-row/40 MiB collection; control frames never contain large binary bodies.
- Owned SIGKILL after acknowledged image-return commit verifies queue remains empty
  AND exact 2 MiB returned data survives as explicit recovery, without factory
  invocation. Runtime failure tests cover backup/queue writes, normal settlement,
  non-destructive copy and committed discard. Both actual native return owners
  assert precommit durable image bytes, including 10 MiB payloads.
- Final broad targeted gate **151 passed** (45.15s), `/tmp/pass167-final.log`;
  final-source journal/native/UI group **52 passed** (14.06s),
  `/tmp/pass167-current.log`, overlap. Both-client private-terminal stopped/initial/
  durable-return recovery + live return + draft browsing/cursor/termios cases
  **8 passed,233 deselected** (17.39s), `/tmp/pass167-terminal.log`. Owned handles
  authoritatively terminal; no test restart from an observation timeout.
- Ruff/all-seven strict mypy **478 sources** clean; whitespace clean. All-four
  packages built offline; **14 affected module entries** byte-verified in wheels/
  sdists, `/tmp/pass167-artifacts.log`. Both READMEs/audit updated. No paid/model/
  auth calls, installs, user daemon restart or unrelated workspace reset.
- Goal ACTIVE, full scope preserved; OS sandbox excluded. This closes returned-
  image handoff retention, not general durable editor drafts, exact consumption/
  handled-input journal or physical power-loss/native storage proof. Those and
  remote draft/raw/canonical/custom/render host APIs plus platform/credential/
  clipboard/provider/MCP interoperability remain OPEN. Root pass149 still predates
  current edits; no current whole-workspace or goal-wide completion claim.

## Pass168 — exact backup reuse after partial return write failure

- Previous turn was PROGRESS: pass167 retained durable return backups. Check the
  actual retry path after partial handoff failure. Found prospective validation
  appended an already retained same-token backup, rejecting retry as duplicate.
  Reuse an identical existing backup instead; conflicting content/identity refuses.
  No automatic retry, extra removal or consent shortcut introduced.
- Tests fail after actual backup replacement or on queue write following backup,
  then explicitly retry the same lease. Exactly one in-memory/durable backup remains,
  actual input is removed once, and completed retry performs no further writes.
- Final runtime/native/RPC/socket gate **122 passed** (34.79s),
  `/tmp/pass168-final.log`; retry-focused group **39 passed** (3.49s),
  `/tmp/pass168-retry.log`, overlaps. Both native live-return terminal workflows
  **2 passed,239 deselected** (4.83s), `/tmp/pass168-terminal.log`. Owned handles
  authoritatively EXIT0; no observation-timeout restart.
- Ruff/all-seven strict mypy **478 sources** clean; whitespace clean. Agent built
  offline, **2 affected module entries** byte-verified, `/tmp/pass168-artifacts.log`;
  both READMEs/audit updated. No paid/auth/model network, installs, user daemon
  restart or unrelated workspace reset.
- Goal ACTIVE, original scope preserved; OS sandbox excluded. This fixes an actual
  durable-handoff retry bug, not general durable editor drafts, exact consumption/
  handled-input journal, physical power-loss proof or remote/raw/custom host API/
  native-platform/credential/clipboard/provider/MCP parity. Those remain OPEN; no
  current whole-workspace or full-goal completion claim.

## Pass169 — frozen full-workspace integration sweep IN PROGRESS

- Previous turn was PROGRESS: pass168 fixed durable-return partial-write retries.
  After twenty cross-package passes since pass149, start ONE current full-root
  checkpoint: `uv run pytest -c pyproject.toml -q --tb=short`, owned handle99712,
  pytest PID67627, `/tmp/pass169-workspace.log`. Do not restart on observation
  timeout or claim completion while it remains live.
- Source freeze `/tmp/pass169-source-before.json` includes1017 source/test/config
  files. Mid-run `/tmp/pass169-source-mid.json`: ZERO changed/deleted/added files.
  No production/test/config edits during this run. Documentation tracking is separate.
- Actual PID inspection at3m17s confirms process RUNNING, latest output52%. At least
  one failure was recorded around40%; pytest continues collecting final evidence.
  Diagnose exact terminal failure output only after this same process settles.
- Fresh repository Ruff and all-seven strict mypy478 clean:
  `/tmp/pass169-ruff.log`, `/tmp/pass169-types.log`. These do not override the
  pending/failing root checkpoint or imply current full parity.
- Goal ACTIVE, full original scope preserved; OS sandbox excluded. Next action:
  poll SAME handle99712/PID67627 to authoritative terminal result, compare final
  source hashes, inspect all actual failures, then fix/verify without substituting
  targeted green results for an atomic full-root checkpoint. Durable editor drafts,
  exact consumption journaling, broader host/RPC/native-platform/interoperability
  requirements remain open even if this eventual integration gate becomes green.

## Pass170 — pass169 actually terminal/red, seven integration failures corrected

- Previous goal work was verified wait/progress: pass169 started one frozen full
  workspace sweep. Continue polling SAME owned handle99712/PID67627 through real
  completion, not restart on tool observation timeout. Authoritative EXIT1:
  **7 failed,5537 passed,29 skipped** (1470.42s), `/tmp/pass169-workspace.log`,
  explicit WORKSPACE_EXIT=1. Post-run snapshot `/tmp/pass169-source-after.json`
  confirms all1017 frozen files unchanged, no added source/test/config files.
- Only after this owner terminated, inspect every actual failure. Two dependency
  audits identify OS GI import in the standalone system-Python vault helper; four
  child terminal-hook cases expect a cleared form although same-context restore
  now intentionally preserves its existing owned draft; one rich-tool child test
  expects image forwarding from an adapter with no advertised image metadata.
- Guard GI/Secret prerequisites within native_store_worker.py explicitly; missing
  import still gives redacted refusal/no fallback. The dependency audit accepts
  externally supplied GI ONLY in this exact guarded standalone /usr/bin/python3
  helper, not arbitrary application imports or an environment-leaked pip dependency.
  Portable bundle audit already recognizes the defensive import. Do NOT install
  GI into uv, add unconditional dependency or contact the user vault to hide failure.
- Rich-tool fixture now resolves advertised vision metadata on its actual offline
  stream adapter. Keep production child-image admission fail-closed. Terminal
  tests assert the preexisting after[x] draft survives invalid intercepted input;
  do NOT erase it in production merely to satisfy obsolete empty-form assertions.
- All affected groups **76 passed** (55.09s), `/tmp/pass170-fixes.log`. Added actual
  isolated missing-GI subprocess verifies redacted dependency refusal without
  daemon/network/file fallback. Native/dependency/bundle group **42 passed**
  (17.23s), `/tmp/pass170-dependency.log`, overlaps, never sum as root evidence.
- Repository Ruff and all-seven strict mypy **478 sources** clean; whitespace
  clean. Current Agent wheel+sdist built offline, actual native helper byte-verified,
  `/tmp/pass170-artifacts.log`; completion audit updated. No paid/auth/model call,
  dependency download, user daemon restart or unrelated workspace reset.
- Goal ACTIVE, full original scope preserved; OS sandbox excluded. A FRESH full
  root sweep after these fixes remains REQUIRED: targeted green cannot replace
  red frozen pass169. General durable editor drafts, exact consumption journaling,
  wider remote/raw/custom host APIs and platform/credential/clipboard/provider/MCP
  interoperability remain OPEN even if the next integration gate becomes green.

## Pass171 — current frozen full-workspace integration GREEN

- Pass170 corrected all seven observed pass169 failures; do not conflate that
  targeted gate with whole-root completion. Start a NEW root run only after prior
  owner actually EXIT1 and fixes settled: handle95694, pytestPID122512,
  `/tmp/pass171-workspace.log`. Keep production/test/config freeze for this gate.
- Poll SAME live owner through slower terminal fixtures, not restart on elapsed
  observation timeout. Authoritative EXIT0: **5545 passed,29 skipped** (1487.69s),
  explicit WORKSPACE_EXIT=0. Before/mid/after snapshots show ALL1017 source/test/
  config files unchanged, no added files: `/tmp/pass171-source-after.json`.
  This is a current atomic full-root green gate, unlike targeted/segmented results.
- Fresh repository Ruff and all-seven strict mypy **478 sources** clean,
  `/tmp/pass171-ruff.log`, `/tmp/pass171-types.log`; whitespace clean. No paid/auth/
  model calls, dependency install, user-daemon restart or unrelated worktree reset.
  No implementation changes during the sweep; documentation records its actual
  result. Pass169 red checkpoint remains historical, not hidden or relabelled.
- Goal ACTIVE, full original scope preserved; OS sandbox excluded. Green current
  integration does NOT prove all feature/native-platform/API gaps. General durable
  editor drafts, exact consumed/handled-input journal, broader raw/canonical/custom/
  render/RPC host surfaces and platform/credential/clipboard/provider/MCP interop
  remain OPEN, and29 skipped cases are not implementation/conformance evidence.
  Resume substantive remaining-gap work after this integration checkpoint.

## Pass172 — awaited identified-input settlement before child model continuation

- Previous turn was PROGRESS: pass171 established a frozen current full-root green
  checkpoint. Resume the remaining consumption-persistence gap. Generic Agent now
  offers opt-in awaited input_settled/set_input_settled, invoked after identified
  history admission or explicit handler consumption, before model continuation.
  Unconfigured generic clients retain their prior behavior; this is not tool authority.
- Actual child supervisor wires its journal writer into that hook, chains any
  existing host hook, waits for prior queue writes and captures the actual remaining
  inputs after that wait. Filesystem work runs off the owner loop; cancellation
  settles its actual thread. Persistence failure stops continuation without claiming
  admitted history/handler effects were rolled back. No automatic replay.
- Runtime tests hold actual writes and prove next model request waits, failed writes
  prevent continuation, handled input invokes settlement without fabricated model
  history, and remaining queue snapshot excludes consumed input. Owned SIGKILL
  AFTER the next model boundary verifies flushed consumed input is absent on reload.
  Admission BEFORE flush remains UNCERTAIN, not an atomic history/queue transaction
  or exact-once effect guarantee. General durable local editor drafts remain OPEN.
- Targeted runtime/native/RPC/socket gate **168 passed** (37.09s),
  `/tmp/pass172-final.log`; added actual SIGKILL and final input/handler group
  **41 passed** (3.49s), `/tmp/pass172-current.log`, overlaps. Coding text-return/
  stopped-copy and both live-image terminal workflows **4 passed,237 deselected**
  (8.52s), `/tmp/pass172-terminal.log`. All owned verification handles terminal EXIT0.
- Ruff/all-seven strict mypy **478 sources** clean; whitespace clean. Agent built
  offline, **4 affected module entries** byte-verified, `/tmp/pass172-artifacts.log`;
  READMEs/audit updated. No paid/auth/model network, dependency install, user daemon
  restart or unrelated workspace reset. Pass171 root checkpoint precedes this patch;
  these tests are not a fresh atomic full-workspace run or goal-wide parity claim.
- Goal ACTIVE, original scope intact; OS sandbox excluded. Durable general editor
  drafts, physical power-loss/native-platform conformance, atomic consumption/history
  boundaries and wider remote/raw/canonical/custom/render host APIs plus remaining
  credential/clipboard/provider/MCP interoperability still require evidence/work.

## Pass173 — private durable child-draft storage foundation; native UI wiring OPEN

- Previous turn was PROGRESS: pass172 awaited child input-consumption persistence.
  Begin remaining durable local-editor drafts with a dependency-light TUI codec/
  store, not falsely treating in-process cache snapshots as durable UI behavior.
- Exact task/image/provenance snapshots are create-only, POSIX pinned-directory
  owned0700/regular0600/single-link files, bounded four10MiB images/40MiB total and
  metadata/32-record catalog. File+directory flush, exclusive publication and
  cooperating directory lock avoid overwrite and simultaneous capacity bypass.
- Strict schema/duplicate JSON/byte receipt/MIME/header/bounds checks reject unsafe
  links/hardlinks/modes and malformed records without partial text recovery.
  Digest-matched discard cannot erase a changed reviewed snapshot. Missing catalog
  is empty; broken symlink is refusal, not silent fallback. Non-POSIX requires its
  own backend/evidence and refuses rather than claiming equivalent protection.
- Loads are conservative read-only context-fresh uncertain copies; former access/
  model/tools/preset/instructions do not become execution configuration. No agent
  constructor/login/automatic submission is performed. Host must perform I/O off
  event/UI loop and separately review save/load/discard. Post-publication failure
  may have committed; no rollback/automatic retry promise.
- Actual independent Python process saves, fresh reader verifies exact text/image
  and stripped permissions. Concurrent8-writer test with2-slot quota admits exactly2;
  unsafe paths, stale delete/duplicate schema cases and10MiB byte roundtrip pass.
  Final store/browser/dependency group **36 passed** (8.44s),
  `/tmp/pass173-final.log`. This is NOT an application terminal/restart save test.
- Ruff/all-seven strict mypy **479 sources** clean; whitespace clean. TUI wheel/
  sdist built offline and new module byte-verified, `/tmp/pass173-artifacts.log`;
  READMEs/audit disclose storage foundation versus pending UI integration. No paid/
  model/auth calls, installs, user daemon restart or unrelated workspace reset.
- Goal ACTIVE, full original scope preserved; OS sandbox excluded. NEXT actual
  native reviewed save/load/discard plus owned asynchronous I/O and restart recovery
  integration. Autosave/general durable editor drafts still OPEN, alongside atomic
  history/consumption boundaries, remote/raw/custom host APIs and native/platform/
  credential/clipboard/provider/MCP interop. Pass171 root precedes this source patch.

## Pass174 — explicit native durable child draft save/load/discard

- Previous turn was PROGRESS: pass173 implemented the private codec/store. Wire
  both actual native clients: s in the local draft browser prepares full physical
  save review; /agents saved performs an explicit off-thread catalog read; x on
  a saved row reviews digest-bound DISK snapshot discard. Sources and independent
  editable copies remain unchanged; no model/tool/permission adoption or auto-submit.
- Saved entries are always conservative old-context read-only fresh task copies,
  with exact text/images/provenance. A single owned disk job retains capacity until
  receipt delivery; repeated actions refuse, graceful quit is held until settlement,
  and delayed reads cannot populate a replacement panel. Failed saves preserve
  local source; diagnostics omit source exception text. Loaded images are bounded
  cumulatively; unsafe/overcapacity reads refuse without truncated recovery.
- Actual fresh UI owners verify save → restart owner → load → reviewed copy/discard
  at10MiB, parent preservation and stripped permissions. Added stale-read and failed-
  write cases assert no callback adoption/replay or source loss. Storage process
  roundtrip evidence remains distinct from native owner tests; autosave is NOT added.
- Physical terminal tests found a real presentation gap: saved catalog entries
  were indistinguishable from OLD CONTEXT memory drafts. Add visible SAVED PRIVATE
  row labels and saved count, then rerun the same workflow at existing geometry.
  Do not widen terminals or weaken physical review to hide the failure.
- Final targeted native/storage/browser regressions **99 passed** (24.09s),
  /tmp/pass174-final-current.log. Both clients' saved-image recovery/save/load/
  local-browse/native cursor+termios paths **6 passed,235 deselected** (16.80s),
  /tmp/pass174-terminal-final.log. Initial failed terminal run remains recorded in
  /tmp/pass174-terminal.log; its final process exited before the presentation fix.
- Repository Ruff/all-seven strict mypy **480 sources** clean; whitespace clean.
  Coding/Claw/TUI built offline, **10 affected module entries** byte-verified in
  wheels/sdists, /tmp/pass174-artifacts.log. READMEs/audit updated. No paid/auth/
  model network calls, dependencies installed, user-daemon restart or unrelated reset.
- Goal ACTIVE with full original scope; OS sandbox excluded. This implements
  explicit durable CHILD snapshots, not automatic persistence of every unreviewed
  edit or general parent-editor autosave, remote saved-draft APIs, power-loss/native
  filesystem conformance or full feature parity. Those and broader raw/canonical/
  custom/render host/platform/credential/clipboard/provider/MCP gaps remain OPEN.
  Pass171 frozen root checkpoint precedes this patch, not current full-root coverage.

## Pass175 — metadata-only saved catalog and ID-scoped recovery load

- Previous work was PROGRESS: pass174 wired native durable child snapshot controls.
  Close the capacity usability gap: /agents saved list validates one bounded
  record at a time and returns metadata only, not adopted image-bearing drafts.
  Over-capacity aggregate catalogs remain discoverable; /agents saved ID loads one
  complete snapshot for full reviewed read-only copy/discard. No silent truncation,
  URI/path interpretation, old permission import or automatic execution.
- Both actual native hosts wire list/ID selection through the existing owned disk
  worker, preserving parent/local drafts and source files. Invalid IDs refuse before
  disk work. Bare saved retains bounded all-record compatibility. Metadata catalog
  tests lower aggregate capacity to prove listing/one-ID recovery survives refusal
  of whole-catalog image loading; no cached binary bodies enter catalog rows.
- Final native/storage/browser regression gate **72 passed** (14.83s),
  /tmp/pass175-final.log; focused capacity/native group **30 passed** (4.51s),
  /tmp/pass175-capacity.log, overlaps. Both-client private-tmux explicit snapshot
  save → metadata listing → load/recovery/native cursor+termios cases **6 passed,
  235 deselected** (24.05s), /tmp/pass175-terminal.log. Owned handles terminal EXIT0.
- Ruff/all-seven strict mypy **480 sources** clean; whitespace clean. Coding/Claw/
  TUI rebuilt offline, **8 affected module entries** byte-verified in wheel/sdist,
  /tmp/pass175-artifacts.log; READMEs/audit updated. No paid/auth/model network,
  installs, user-daemon restart or unrelated workspace reset.
- Goal ACTIVE, original scope intact; OS sandbox excluded. Autosave/general durable
  editor persistence, remote saved-draft APIs, physical power-loss/native storage
  conformance and broader raw/canonical/custom/render/platform/credential/clipboard/
  provider/MCP interoperability remain OPEN. Pass171 root checkpoint precedes this
  source patch, not current atomic full-root or goal-wide completion evidence.

## Pass176 — nonblocking durable-draft receipt collection and failed-start ownership

- Previous turn was PROGRESS: pass175 enabled capacity-safe saved catalog reads.
  Inspect actual UI worker lifecycle: poll joined its live thread after a receipt
  was queued, potentially stalling frames; thread.start failure left a false busy
  owner. Remove live join, keep capacity until actual worker exit, then collect one
  receipt nonblocking. Failed startup releases owner and admits no disk operation.
- Held published-receipt worker test proves poll never joins/bypasses capacity;
  failed-start test admits a subsequent fresh operation, and delivery rejection
  releases worker without replaying its rejected receipt. Both actual native
  save/load/discard owners preserve existing explicit consent/quit semantics.
- Final store/worker/native regression gate **58 passed** (14.84s),
  /tmp/pass176-final.log; focused lifecycle/native tests **15 passed** (3.20s),
  /tmp/pass176-worker.log, overlap. Both-client private-terminal durable save/list/
  load/recovery/cursor/termios cases **6 passed,235 deselected** (23.32s),
  /tmp/pass176-terminal.log. Owned handles authoritatively EXIT0, no restarts.
- Ruff/all-seven strict mypy **480 sources** clean; whitespace clean. TUI built
  offline, **2 affected module entries** byte-verified, /tmp/pass176-artifacts.log;
  READMEs/audit updated. No paid/auth/model calls, installs, user daemon restart
  or unrelated workspace reset.
- Goal ACTIVE, full original scope intact; OS sandbox excluded. Lifecycle stability
  does not implement autosave/general parent-editor durability, remote saved-draft
  APIs, physical power-loss/native conformance or broader raw/custom/platform/
  credential/clipboard/provider/MCP parity. Those remain OPEN; pass171 full-root
  checkpoint predates this patch and current goal-wide completion is unproven.

## Pass177 — bounded saved-draft lock contention

- Previous turn was PROGRESS: pass176 fixed nonblocking receipt collection. Bound
  the remaining storage lock wait: nonblocking cooperating directory-lock attempts
  with3s deadline refuse instead of leaving owned jobs/graceful quit pending forever.
  Snapshot privacy/atomic publication and explicit retry semantics unchanged.
- Real separate process holds directory lock. Save/load/catalog/discard all hit a
  shortened test deadline, leave original bytes/no temp artifacts, then successfully
  load after peer unlock. Only owned peer/process/fds are terminated/closed.
- Final storage/lifecycle/native gate **37 passed** (4.98s),
  /tmp/pass177-final.log; both-client terminal save/list/load/recovery cases
  **6 passed,235 deselected** (23.21s), /tmp/pass177-terminal.log. Ruff/all-seven
  strict mypy **480 sources** clean; whitespace clean. TUI wheel/sdist built offline,
  **2 affected module entries** byte-verified, /tmp/pass177-artifacts.log.
- READMEs/audit updated. No paid/auth/model calls, installs, user daemon restart
  or unrelated reset. Goal ACTIVE, original scope intact; OS sandbox excluded.
  Lock deadline is not a watchdog for arbitrary filesystem stalls/power loss;
  autosave/general editor durability, remote/raw/custom APIs and remaining native
  platform/credential/clipboard/provider/MCP interoperability stay OPEN.

## Pass178 — independently negotiated Coding RPC saved snapshot discovery/read

- Previous turn was PROGRESS: pass177 bounded saved-storage lock contention. Add
  read-only saved_catalog/saved_read controls through the existing Coding RPC owner,
  independently negotiated child_saved_drafts; saved binary bodies additionally
  require child_saved_images. Exact catalog ID/digest required for snapshot read.
- Catalog exposes metadata only. Image reads stream bounded checksum-bound binary
  frames with session/revision/connection/snapshot scopes; final reply contains
  exact receipt and conservative read-only/context-fresh draft metadata, no large
  image bodies. No history installation, queued input, model call, deletion or old
  permission/model/tool adoption. Filesystem/codec work off loop; owned threads
  settle on cancellation. Revocation/re-enable generation fences saved read jobs.
- Actual CLI subprocess reads a 2 MiB saved image snapshot without model entry or
  parent messages. Tests cover text-only reads, missing independent capabilities,
  stale catalog digest, closed peer and mid-transfer withdrawal with unchanged
  stored originals. Native snapshot writes remain separately reviewed operations.
- Final RPC/subprocess/storage/receiver gate **80 passed** (27.08s),
  /tmp/pass178-final.log. Ruff/all-seven strict mypy **480 sources** clean;
  whitespace clean. Coding wheel/sdist built offline, **2 affected module entries**
  byte-verified, /tmp/pass178-artifacts.log. Both READMEs/audit updated.
- No paid/auth/model calls, installs, user daemon restart or unrelated reset.
  Goal ACTIVE, full original scope preserved; OS sandbox excluded. Remote saved
  save/discard, autosave/general editor persistence and wider raw/canonical/custom/
  render/platform/credential/clipboard/provider/MCP requirements stay OPEN.
  Pass171 full-root checkpoint predates these changes, not current root evidence.

## Pass179 — two-phase Coding RPC saved snapshot discard

- Previous turn was PROGRESS: pass178 exposed read-only saved snapshots. Add
  independently negotiated child_saved_discard with exact catalog ID/digest
  preparation, bounded30s review receipt, explicit True exact echoed confirmation
  and cancel. Preparation never removes; confirmation grants are single-use.
- Same connection/saved generation, metadata/digest, expiry and worker-bound alive
  guard fence deletion after lock acquisition. Capability disable/re-enable clears
  grants/cancels owned work; actual disk worker settles before cancellation reports.
  Failed/uncertain delete is not automatically retried. Only saved DISK snapshot
  changes, never editable local copies, queues, permissions or agent history.
- Tests cover confirmed/cancelled/changed/foreign/revoked/expired/truthy-not-True
  review cases, held worker revocation and actual CLI prepare→bad consent→exact
  confirmation→reused receipt refusal with zero model/history mutation.
- Final RPC/CLI/native/storage group **91 passed** (30.01s),
  /tmp/pass179-final.log. Native saved-image/terminal compatibility **6 passed,
  235 deselected** (24.78s), /tmp/pass179-terminal.log. Ruff/all-seven strict mypy
  **480 sources** clean; whitespace clean. Coding/TUI rebuilt offline, **4 affected
  module entries** byte-verified, /tmp/pass179-artifacts.log; READMEs/audit updated.
- No paid/auth/model calls, installs, user daemon restart or unrelated reset.
  Goal ACTIVE, full original scope intact; OS sandbox excluded. Remote save/autosave,
  general editor durability, physical client review/native conformance and wider
  raw/custom/platform/credential/clipboard/provider/MCP gaps remain OPEN.

## Pass180 — two-phase Coding RPC saved snapshot creation

- Previous turn was PROGRESS: pass179 added reviewed remote discard. Wire separate
  child_saved_save capability and prepare/confirm/cancel controls for exact text/
  image snapshots, with saved-access and binary capabilities independently gated.
  Preparation returns full private-plaintext metadata/digest review, publishes no
  file and owns its byte copy; successful upload preparation consumes its refs.
- Confirmation requires exact echoed review and actual True, same connection/
  generation and unexpired30s grant. Single-use publication calls existing exclusive
  store with alive check immediately before link creation. Failed/uncertain write
  is not an automatic retry; catalog inspection remains explicit. No caller path/
  URI/model/tools/access becomes execution authority and no agent input is submitted.
- Native snapshot APIs stay compatible. Real RPC subprocess saves a2MiB chunk-
  uploaded image only after confirmation, verifies exact digest/bytes and zero
  model/history mutations, then refuses repeated consent. Runtime tests cover
  text/image capability isolation, cancel/expiry/truthy consent/withdrawal and held
  writer revocation before publication. Preparation count/bytes are bounded.
- Final RPC/CLI/native/storage group **99 passed** (31.34s),
  /tmp/pass180-final.log. Both-client saved-image native terminal compatibility
  **6 passed,235 deselected** (23.64s), /tmp/pass180-terminal.log. Ruff/all-seven
  strict mypy **480 sources** clean; whitespace clean. Coding/TUI built offline,
  **4 affected module entries** byte-verified, /tmp/pass180-artifacts.log.
- READMEs/audit updated. No paid/auth/model calls, installs, user daemon restart
  or unrelated reset. Goal ACTIVE, full scope preserved; OS sandbox excluded.
  Explicit remote saved snapshot operations do not implement autosave/general
  editor durability, physical client-review/native-platform conformance or wider
  raw/custom/provider/credential/MCP parity. Those remain OPEN. Pass171 root gate
  precedes these patches; no current full-workspace/goal-wide claim.

## Pass181 — session-local opt-in native child autosave

- Previous turn was PROGRESS: pass180 implemented explicit two-phase RPC snapshot
  saves. Add native /agents autosave on/off/status in both clients, OFF by default,
  with private-plaintext disclosure and no project/remote execution-policy control.
- Debounced0.5s snapshots retain unsent local child drafts; later edits update ONLY
  the same autosave owner's exact digest-matched prior record. External changes
  refuse; a write failure disables scheduling instead of repeated uncertain retries.
  Saved catalog copies are excluded from automatic resaving. All I/O off UI loop.
- Recovery remains explicit/full-reviewed read-only/context-fresh through saved
  catalog. Graceful quit flushes pending child snapshots and holds while actual
  writer settles. Completed autosaves survive owned SIGKILL; debounce/in-flight
  crash windows and general parent-editor persistence remain OPEN, not overstated.
  Clear/submit does not automatically delete prior recovery snapshots.
- Both native owner tests verify text/image save/update to same record, off behavior,
  unchanged parent/no execution and external-change refusal. Debounce/flush tests
  and an actual independent process autosave→SIGKILL→conservative reload pass.
  Final targeted store/IO/autosave/native group **63 passed** (15.50s),
  /tmp/pass181-final.log; added process/native group **19 passed** (3.51s),
  /tmp/pass181-crash.log, overlaps. Both private-terminal recovery/save/catalog/
  autosave on-off/control/cursor cases **6 passed,235 deselected** (24.67s),
  /tmp/pass181-terminal.log. Owned verification handles actually EXIT0.
- Final repository Ruff/all-seven strict mypy **481 sources** clean; whitespace
  clean. Coding/Claw/TUI rebuilt offline, **10 affected module entries** byte-
  verified against final source in wheels/sdists, /tmp/pass181-artifacts.log.
  Both READMEs/audit updated. No paid/auth/model network calls, dependencies
  downloaded, user-daemon restart or unrelated workspace reset.
- Goal ACTIVE, full original scope intact; OS sandbox excluded. Opt-in child
  autosave is not general parent-editor durability, automatic replay, physical
  power-loss/native-platform proof or remote autosave API. Those and wider raw/
  canonical/custom/render/platform/credential/clipboard/provider/MCP gaps stay OPEN.
  Pass171 full-root checkpoint precedes current edits; targeted checks are not
  current root-wide or goal-wide completion evidence.

## Pass182 — autosave receipt-only memory and removed-slot tracking release

- Previous turn was PROGRESS: pass181 completed opt-in child autosave. Inspect
  actual retention: completed bookkeeping held decoded images/text a second time
  outside UI retention accounting. Retain ID/digest-only receipt for future owned
  updates and prune tracking for removed local slots without deleting disk recovery.
- Same present slot still updates its exact prior snapshot. Removed/reappearing
  slots create a NEW snapshot, preserving the earlier recovery record. Tests assert
  no decoded bodies retained after delivery, exact image recovery still works,
  updates remain same-record and pruned slots cannot overwrite prior saved data.
- Final autosave/native/IO regression gate **23 passed** (3.56s),
  /tmp/pass182-final.log; both-client private-terminal saved recovery/autosave
  controls **6 passed,235 deselected** (24.00s), /tmp/pass182-terminal.log.
  Ruff/all-seven strict mypy **481 sources** clean; whitespace clean. TUI built
  offline, **2 affected module entries** byte-verified, /tmp/pass182-artifacts.log.
- READMEs/audit updated. No paid/auth/model calls, installs, user daemon restart
  or unrelated reset. Goal ACTIVE, full scope preserved; OS sandbox excluded.
  This fixes actual autosave retention, not parent-editor persistence, debounce/
  in-flight crash guarantees, native storage/power-loss conformance or broader
  remote/raw/custom/platform/credential/provider/MCP parity. Those stay OPEN.

## Pass183 — deferred quit must not partially tear down a still-live UI

- Previous turn was PROGRESS: pass182 bounded autosave retained memory. Inspect
  actual graceful-exit path: both clients closed argument/custom services BEFORE
  checking an admitted draft write, leaving deferred-quit UIs partially dead.
  Move teardown after actual exit admission; Claw name-read cancellation moves too.
- Actual held disk-write tests in both native owners call quit repeatedly and
  verify no service closes until writer settlement, then exact once-only cleanup
  after admitted exit. No drafts/input/model effects are dropped or replayed.
- Native/input/worker regressions **57 passed** (37.98s), /tmp/pass183-final.log;
  both-client private-terminal saved recovery/autosave workflows **6 passed,
  235 deselected** (24.07s), /tmp/pass183-terminal.log. Ruff/all-seven strict mypy
  **481 sources** clean; whitespace clean. Coding/Claw built offline, **4 affected
  module entries** byte-verified, /tmp/pass183-artifacts.log; READMEs/audit updated.
- No paid/auth/model calls, installs, user daemon restart or unrelated reset.
  Goal ACTIVE, full scope intact; OS sandbox excluded. Parent-editor persistence,
  debounce/in-flight crash guarantees and wider remote/raw/custom/platform/
  credential/clipboard/provider/MCP requirements remain OPEN. This actual lifecycle
  fix is not a current root integration checkpoint or full goal completion.

## Pass184 — saved reload counts image bytes still owned by editable copies

- Previous turn was PROGRESS: pass183 fixed deferred quit. Inspect reload retained
  accounting: subtracting every prior catalog image incorrectly released bytes
  still referenced by independently recovered local drafts. Count non-catalog local
  draft/composer ownership explicitly; only exclusive old catalog bytes are released.
- Both actual native owner tests share a1MiB saved image with an editable copy and
  simulate39MiB other retention. Reload refuses instead of adopting another decoded
  image over capacity, preserving catalog/copy/parent. Exclusive-only reload succeeds
  at the limit without double counting. No disk rewrite or model execution occurs.
- Final native/store/autosave gate **68 passed** (16.01s), /tmp/pass184-final.log;
  both-client terminal recovery/save/load/autosave cases **6 passed,235 deselected**
  (23.91s), /tmp/pass184-terminal.log. Ruff/all-seven strict mypy **481 sources**
  clean; whitespace clean. Coding/Claw built offline, **4 affected module entries**
  byte-verified, /tmp/pass184-artifacts.log; READMEs/audit updated.
- No paid/auth/model calls, installs, user daemon restart or unrelated reset.
  Goal ACTIVE, full scope intact; OS sandbox excluded. Parent-editor persistence,
  debounce/in-flight crash guarantees and remaining remote/raw/custom/platform/
  credential/clipboard/provider/MCP interoperability remain OPEN. This fixes actual
  capacity ownership, not full goal completion or a fresh root-wide checkpoint.

## Pass185 — opt-in parent composer persistence and reviewed recovery

- Previous turn was PROGRESS: pass184 fixed saved-image ownership accounting.
  Add separate parent composer autosave/recovery, OFF by default, explicit
  /draft autosave on/off plus /draft saved list/ID. No child invocation or project
  permission/model configuration controls. Shared private storage supports64KiB
  parent text and bounded images; capacity accounts for escaped Unicode metadata.
- Saved parent review requires full physical pages and unchanged EMPTY composer
  before text/image copy. Changed/nonempty drafts refuse without loss. Disk-only
  reviewed discard preserves current editor. Parent catalog ownership counts into
  image retention and is released after successful move into composer. All I/O off
  UI loop; graceful exit flushes/settles owned writers, no automatic prompt replay.
- Both actual native owners test autosave text/image, new-owner long Unicode
  recovery, unchanged permissions/history/model calls, changed-composer refusal and
  disk-only discard. Existing child/input paths keep compatibility. Final broad
  surrounding gate **111 passed** (50.76s), /tmp/pass185-final-current.log; added
  final store/native/discard gate **61 passed** (6.31s), /tmp/pass185-verified.log,
  overlaps. Both-client private-terminal parent image full review/recovery/cursor
  cases **6 passed,235 deselected** (26.09s), /tmp/pass185-terminal.log.
- Final Ruff/all-seven strict mypy **482 sources** clean; whitespace clean. Coding/
  Claw/TUI built offline, **10 affected module entries** byte-verified in wheel/
  sdist, /tmp/pass185-artifacts.log; READMEs/audit and command help updated.
  No paid/auth/model calls, installs, user daemon restart or unrelated reset.
- Goal ACTIVE, full scope preserved; OS sandbox excluded. This is opt-in durable
  snapshot recovery, not every-keystroke persistence, debit-free replay, physical
  power-loss/native filesystem proof or remote parent-draft API. Debounce/in-flight
  windows and broader raw/canonical/custom/render/platform/credential/clipboard/
  provider/MCP interoperability remain OPEN. Pass171 root checkpoint predates patch.

## Pass186 — parent read delivery fenced to actual auxiliary view

- Previous turn was PROGRESS: pass185 implemented parent snapshot recovery. Fix
  lifecycle ownership: parent reads now bind to the requesting auxiliary panel;
  close/back revokes delivery and releases saved/read image references. A delayed
  disk result cannot reopen a closed view or adopt into a replacement panel.
  Admitted I/O still settles and saved files remain unchanged; no automatic retry.
- Held actual worker tests in both clients close/replace the view before load
  receipt and verify no catalog adoption or parent draft mutation. Back releases
  temporary saved image accounting/read snapshot without disk deletion. Existing
  recovery/full review/input/permission semantics remain intact.
- Final native/storage/input gate **93 passed** (40.49s), /tmp/pass186-final.log;
  both-client private-terminal parent/child recovery/save workflows **6 passed,
  235 deselected** (25.25s), /tmp/pass186-terminal.log. Ruff/all-seven strict mypy
  **482 sources** clean; whitespace clean. Coding/Claw/TUI built offline, **6 affected
  module entries** byte-verified, /tmp/pass186-artifacts.log; READMEs/audit updated.
- No paid/auth/model calls, installs, user daemon restart or unrelated reset.
  Goal ACTIVE, original scope preserved; OS sandbox excluded. Debounce/in-flight
  crash windows, remote parent APIs and broader raw/canonical/custom/render/native
  platform/credential/clipboard/provider/MCP interoperability remain OPEN. This
  lifecycle fix is not current root-wide or full-goal completion evidence.

## Pass187 — parent recovery cannot steal focus after same-panel navigation

- Previous turn was PROGRESS: pass186 fenced replaced parent recovery panels.
  Extend ownership to requesting view lease plus queue/draft/preview identities:
  a delayed read cannot take over a newly opened child form/browser within the
  same outer panel. Back to the roster does not revive the old read grant.
- On successful read presentation, retain captured composer/context checks while
  binding the receipt to the exact parent recovery browser. Stale browser callback
  after same-panel navigation refuses both copy/discard without file/draft mutation.
- Actual both-client held-reader tests cover form, back roundtrip and replacement
  draft-browser transitions, then stale copy/delete callbacks. Final native/input/
  draft regressions **83 passed** (39.05s), /tmp/pass187-final.log; both-client
  terminal recovery/save/autosave cases **6 passed,235 deselected** (25.19s),
  /tmp/pass187-terminal.log. Ruff/all-seven strict mypy **482 sources** clean;
  whitespace clean. Three packages built offline, **6 affected module entries**
  byte-verified, /tmp/pass187-artifacts.log; READMEs/audit updated.
- No paid/auth/model calls, installs, user daemon restart or unrelated reset.
  Goal ACTIVE, full scope intact; OS sandbox excluded. Snapshot crash windows,
  remote parent APIs and broader raw/custom/platform/credential/clipboard/provider/
  MCP interoperability stay OPEN. Current root/goal-wide parity remains unproven.

## Pass188 — explicit native parent snapshot save without consuming composer

- Previous turn was PROGRESS: pass187 fenced same-panel parent read transitions.
  Add /draft save plus focused-parent Ctrl+Alt+S to open exact task/image/private
  snapshot review without autosave opt-in or consuming existing composer text.
- Full painted pages required before physical Enter; changed text/images/cursor,
  stale view or repeated confirmation refuse. Save publishes one private record
  via owned off-thread IO, preserving composer/history/permissions and never
  invoking a model. Recovery remains separately reviewed into empty composer.
- Both native owners verify exact text/image save, unpainted/repeat/paste refusal,
  changed-draft rejection, autosave independence and single-use grant. Surrounding
  native/input/storage gate **105 passed** (40.60s), /tmp/pass188-final.log;
  private-terminal focused shortcut/save/recovery/native cursor cases **6 passed,
  235 deselected** (33.04s), /tmp/pass188-terminal.log. All owned handles EXIT0.
- Ruff/all-seven strict mypy **482 sources** clean; whitespace clean. Three
  packages built offline, **8 affected module entries** byte-verified,
  /tmp/pass188-artifacts.log; READMEs/audit updated. No paid/auth/model calls,
  installs, user daemon restart or unrelated reset.
- Goal ACTIVE, full original scope preserved; OS sandbox excluded. Snapshot
  crash windows, remote parent APIs, broader raw/custom/platform/credential/
  clipboard/provider/MCP conformance stay OPEN. Current whole-root and full-goal
  completion remain unproven; pass171 root checkpoint precedes this patch.

## Pass189 — native parent save cannot steal modal input ownership

- Previous turn was PROGRESS: pass188 added explicit parent save. Fence shortcut
  and stale full-review confirmation against actual modal policy: extension dialog/
  queued question, output review/pending output, overlay and Coding permission
  prompt/waiters. No write or focus takeover admitted; composer/approval unchanged.
- Both native test owners inject dialog/queued/output modal ownership after review
  preparation, then assert shortcut/stale callback cannot proceed. Coding approval
  case preserves owner even with stale editor-focus flags. Final native/input gate
  **85 passed** (39.04s), /tmp/pass189-final.log; both-terminal parent save/recovery
  workflows **6 passed,235 deselected** (33.26s), /tmp/pass189-terminal.log.
- Ruff/all-seven strict mypy **482 sources** clean; whitespace clean. Coding/Claw
  built offline, **4 affected module entries** byte-verified,
  /tmp/pass189-artifacts.log; READMEs/audit updated. No paid/auth/model calls,
  installs, user daemon restart or unrelated reset.
- Goal ACTIVE, full scope preserved; OS sandbox excluded. Snapshot crash windows,
  remote parent APIs and broader raw/custom/platform/credential/clipboard/provider/
  MCP conformance stay OPEN; focused safety tests do not prove goal-wide completion.

## Pass190 — configurable native parent-save binding

- Previous turn was PROGRESS: pass189 fenced modal parent-save ownership. Integrate
  save shortcut with existing PromptKeymap instead of hard-coded Ctrl+Alt+S:
  save_draft action defaults to that key, supports remap/disable and /keys discovery.
  Host-only action stays outside canonical editor/send mapping; modal/review guards
  preserved. Old default cannot trigger after rebinding or disabling.
- Both native owner tests remapF8/disable and assert repeat/release/old shortcut
  do not open review or mutate draft; actual F8 opens only save preparation, no
  file write/model submission. Keymap tests validate conflicts/reserved recovery.
- Final binding/native gate **78 passed** (4.99s), /tmp/pass190-bindings.log;
  both-terminal parent save/recovery workflows **6 passed,235 deselected**
  (32.92s), /tmp/pass190-terminal.log. Ruff/all-seven strict mypy **482 sources**
  clean; whitespace clean. Three packages built offline, **6 affected module
  entries** byte-verified, /tmp/pass190-artifacts.log; READMEs/audit updated.
- No paid/auth/model calls, installs, user daemon restart or unrelated reset.
  Goal ACTIVE, full original scope preserved; OS sandbox excluded. Snapshot
  crash windows, remote parent APIs and broader raw/custom/native-platform/
  credential/clipboard/provider/MCP conformance stay OPEN. Current root-wide/
  goal-wide parity remains unproven; this is a named shortcut integration fix.

## Pass191 — read-only image preview in parent/child draft reviews

- Previous turn was PROGRESS: pass190 made parent save configurable. Add p image
  preview to saved/local parent and child draft lists/reviews, with left/right
  exact attachment navigation and Escape back. No save/copy/delete/agent action
  occurs; preview never grants execution or receipt consent.
- Return clears review painting and starts again at the first page, requiring all
  metadata pages physically painted before confirmation. Changed source identity/
  content invalidates preview and review without applying action. Original draft
  and exact images remain untouched.
- Component/native gate **65 passed** (4.59s), /tmp/pass191-tests-final.log;
  both-client private-terminal parent preview→full recovery review→save/cursor/
  termios cases **6 passed,235 deselected** (35.15s), /tmp/pass191-terminal.log.
  Final Ruff/all-seven strict mypy **482 sources** clean; whitespace clean. TUI
  wheel/sdist built offline, **2 affected module entries** byte-verified,
  /tmp/pass191-artifacts.log. READMEs/audit updated.
- No paid/auth/model calls, installs, user daemon restart or unrelated reset.
  Goal ACTIVE, full original scope intact; OS sandbox excluded. Native desktop
  image/provider/decoder interoperability, snapshot crash windows, remote parent
  APIs and remaining broader raw/custom/platform/credential/MCP requirements
  stay OPEN; this UI preview increment is not full goal or current root parity.

## Pass192 — parent recovery read/presentation obey modal ownership

- Previous turn was PROGRESS: pass191 added read-only draft-image preview. Extend
  parent recovery admission/delayed result/stale callbacks to the same modal policy
  as explicit save: dialogs, queued questions, output and Coding approval owners.
  A completed read cannot steal focus or adopt snapshots while another modal owns
  input; explicitly reload after it settles, without automatic retry.
- Both native policy tests reject stale copy/discard and new reads under modal
  ownership. Actual held-reader tests keep a directly owned modal alive (no broker
  lifecycle involved) and assert late delivery cannot take focus or modify files.
- Final native/input gate **97 passed** (39.47s), /tmp/pass192-final.log;
  both-terminal recovery/preview/save/cursor workflows **6 passed,235 deselected**
  (34.69s), /tmp/pass192-terminal.log. Ruff/all-seven strict mypy **482 sources**
  clean; whitespace clean. Coding/Claw built offline, **4 affected module entries**
  byte-verified, /tmp/pass192-artifacts.log; READMEs/audit updated.
- No paid/auth/model calls, installs, user daemon restart or unrelated reset.
  Goal ACTIVE, full scope preserved; OS sandbox excluded. Snapshot crash windows,
  remote parent APIs and broader raw/custom/native-platform/credential/clipboard/
  provider/MCP conformance remain OPEN; this guard is not current root-wide parity.

## Pass193 — complete explicit save-and-quit after successful draft settlement

- Previous turn was PROGRESS: pass192 fenced parent recovery modal ownership.
  Preserve an explicit quit intent while admitted draft writes settle, then finish
  automatically after flushing latest pending snapshots. No second quit gesture
  needed, input services remain live during wait and cleanup still occurs once.
- Track worker failure counts at quit admission. A new storage failure cancels
  deferred exit, preserves live UI/drafts and requests inspection rather than
  silently quitting with unsaved data. Native tests cover success/failure and
  latest parent edits while first autosave is held. No prompt replay/submission.
- Final native/input/lifecycle gate **110 passed** (40.58s), /tmp/pass193-final.log;
  both-terminal recovery/save/cursor workflows **6 passed,235 deselected**
  (32.23s), /tmp/pass193-terminal.log. Ruff/all-seven strict mypy **482 sources**
  clean; whitespace clean. Three packages built offline, **6 affected module
  entries** byte-verified, /tmp/pass193-artifacts.log; READMEs/audit updated.
- No paid/auth/model calls, installs, user daemon restart or unrelated reset.
  Goal ACTIVE, full scope intact; OS sandbox excluded. Snapshot crash windows,
  remote parent APIs and broader raw/custom/native-platform/credential/clipboard/
  provider/MCP conformance remain OPEN. This save-and-quit integration is not
  current root-wide or goal-wide parity evidence.

## UI-035 — literal suffix/quote/escape completion closure (2026-10-10)

Fix shared cursor-token scanning: escaped whitespace no longer splits the token;
escaped backslashes, escaped quotes and literal backslashes within single quotes
have explicit boundary semantics. Interior whitespace gets an empty insertion
slot. Preserve literal callback prefix/full-context coordinates and full-token
replacement, no auto-unquoting/requoting/expansion or suffix duplication.

Actual both-client owned extension workers (Claw real socket) verify three quoted/
escaped suffix replacements, unchanged surrounding arguments, undo and zero command/
model calls. Component coverage includes unfinished quotes/backslash parity/empty
slots; existing owner/registration/blur/exit/revocation/negotiation tests retained.
Current named group: **58 passed**, `/tmp/ui035-tests-final.log`; Linux actual two
clients suffix/quote/escape/undo/parent recovery and owned worker cancellation:
**6 passed,235 deselected**, `/tmp/ui035-terminal-final.log`.

Initial added fixture asserted a suffix even on unrelated end-of-line completions;
restrict that assertion to its named literal cases. Terminal assertions now explicitly
move to End before checking end-cursor (replacement intentionally leaves cursor at
inserted token). Before the terminal's separate counter command, wait for actual
Claw group reservation settlement instead of racing completion admission; do not
weaken idle gate or repeat commands. Earlier red logs remain `/tmp/ui035-tests.log`,
`/tmp/ui035-terminal.log` and `/tmp/ui035-terminal-fixed.log`, not passing evidence.

Repository Ruff clean (`/tmp/ui035-ruff-final.log`), all-seven strict mypy482 clean
(`/tmp/ui035-types.log`); TUI built offline and current affected source bytes verified
in wheel+sdist (`/tmp/ui035-wheels`). No user services restarted, no test interrupted,
no paid/auth calls or dependency downloads. UI-035 is completed; authoritative count
now 64 total,35 complete,13 to implement,7 to verify,5 missing environment,4 deferred:
**25 non-deferred unfinished requirements**. Final complete root regression is still
UI-055, not supplied by these focused tests. Goal remains active.

## UI-051 — current independent MCP SDK matrix (2026-10-10)

Previous goal turn is PROGRESS: UI-035 implementation/actual host and terminal
verification completed. Revalidated authoritative ledger (25 unfinished) and
process state; no running pytest/mypy/Ruff was interrupted. Reuse existing
`/tmp/xdog-mcp-pass68.lw1gcs` SDK installation without downloads/model/auth calls.
Independently installed server/core package metadata both identifies **2.3.1**.
Source snapshot `/tmp/ui051-source-before.json` captures839 relevant files.

Initial existing SDK matrix **24 passed** (`/tmp/ui051-sdk.log`), actual Linux
both-client terminal SDK checks **2 passed,239 deselected** (`/tmp/ui051-terminal.log`).
Source inspection found both native SDK UI watch tests only used stdio. Add explicit
HTTP SDK peer cases to Coding and Claw, required HTTP request name mirrors, and
async fixture-owned peer cleanup even on app setup/assertion failure. Preserve
legacy/modern tests and existing permission/no-read/no-model/draft/view fences.

Final full named SDK matrix **26 passed,no skips** (20.04s),
`/tmp/ui051-sdk-final.log`; actual native-terminal SDK watch/unwatch, separate
prompt completion/retrieval/link search/unsubmitted insertion/parent cursor and
termios recovery **2 passed,239 deselected** (6.38s),
`/tmp/ui051-terminal-final.log`. Terminal SDK cases remain stdio; do not infer actual
HTTP terminal execution from native-owner HTTP tests. Both owned processes polled
to authoritative terminal exit. No live peer remains. Source snapshot diff confirms
only the two intentionally extended UI test files changed, production/peer files
unchanged (`/tmp/ui051-source-final.json`). Repository Ruff clean
(`/tmp/ui051-ruff.log`), all-seven strict mypy482 clean (`/tmp/ui051-types.log`),
whitespace clean. No production artifact changed; no installs/downloads/publish/
service restart/unrelated overwrite. This is bounded SDK2.3.1 evidence, not all
current protocol/provider/OAuth/third-party conformance or final root regression.

UI-051 completed. Current authoritative totals:64 requirements;36 completed,
13 to implement,6 to verify,5 missing external environment,4 deferred.
**24 non-deferred unfinished requirements**. UI-048/057 and final Linux safety/
terminal/root gates stay independently open. Goal remains active.

## UI-038 — parent canonical/binary RPC export implementation (partial, 2026-10-10)

Previous goal turn is PROGRESS (UI-051 closed with current independent SDK and
native-owner HTTP evidence). Revalidate ledger24 unfinished and no live test process.
Add independently negotiated transcript_canonical:1 and explicit format=canonical
RPC reads, without authorizing binary export from display capability. Reuse owned
bounded private spool/output/cancellation protocol. Format-specific withdrawal is
independent; every canonical frame is tagged. Parent live/saved/branch/timeline
exports retain typed image base64, indexed history and partial/provenance records.
Signatures, opaque response IDs, redacted reasoning and known credential fields/
inline patterns withheld; usage counters retained. Reject unsupported content before
permissive persistence readers can drop it, including saved head/branch content.
Custom metadata cannot opt into the typed-image redaction exception.

Shared SourceReceiver adds expected-format fencing and explicit custom-decoder
requirement for canonical content; default display clients cannot accidentally
render image JSONL. No model/history activation/tool/resource execution introduced.
Actual offline CLI saved/branch/timeline framing covers2MiB images, long Unicode,
redaction and status/permission invariants; canonical slow/closed-output/quit/EOF
cases exercise the real reader. Held preparation cancellation/withdrawal/revision/
close settles actual spool; format/order/checksum/owner/capacity negatives tested.

Final relevant source/RPC/child-display/shared saved-source suite: **125 passed**
(34.40s), `/tmp/ui038-receiver-final.log`; current Ruff and all-seven strict mypy483
clean (`/tmp/ui038-static-current.log`), whitespace clean. Offline Coding/TUI wheel+
sdist builds `/tmp/ui038-current-wheels`, four current affected module byte checks
passed. Initial malformed/nonfinite metadata refusal test exposed missing finite
validation and was fixed; red `/tmp/ui038-tests.log` retained as historical evidence.
An incorrect test-file path `/tmp/ui038-receiver.log` collected no tests and is not
passing evidence. No test interrupted/restarted on observation timeout; no installs,
paid/auth calls, user daemon restart or unrelated overwrite.

**UI-038 remains 待实现**: this is substantive parent export implementation, not
complete scope acceptance. Existing child canonical/binary sources and their
connection/epoch/withdrawal/actual child CLI evidence still need implementation.
Other original requirements/final full-root gates unchanged. Count remains64 total,
36 completed,13 to implement,6 to verify,5 missing environment,4 deferred:
**24 non-deferred unfinished**. Goal active; no broader conformance claim.

## UI-038 — child canonical implementation and verified matrix (2026-10-10)

Previous turn PROGRESS: parent canonical/binary export implemented. Add
child_canonical independently negotiated beside child_sources and parent capability;
canonical child spool uses actual ChildCapture messages/config/status/partial,
known-secret redaction, typed images and existing owner/epoch/generation fences.
Format-specific withdrawal cancels only matching child streams; parent withdrawal
cannot revoke independent child authorization. All malformed capability/foreign
cancel/receiver mixed-epoch and held-spool close/revision/withdrawal tests retained.
Shared bounded spool pre-manifest final cancellation check added.

Initial fixture waited for partial after StartEvent, but actual runtime only
publishes partial after MessageUpdateEvent. Correct tests to wait for actual admitted
history; opt-in CLI fixture emits TextDeltaEvent and writes .partial marker AFTER
its admitted update. Do not change production lifecycle to fit a test. Subsequent
partial receipt race exposed by full named suite is fixed with that exact marker.
Historical red logs `/tmp/ui038-child-tests.log`, `/tmp/ui038-child-current.log`,
`/tmp/ui038-child-complete.log` retained, not substituted for passing evidence.

Actual child CLI 2MiB image export and original parent live/saved/branch/timeline
source/canonical/child control and cleanup full named matrix **158 passed** (55.40s),
`/tmp/ui038-child-verified.log`. Three actual child binary slow/closed-peer quit/EOF/
closed-output cases also **3 passed,21 deselected** (6.57s),
`/tmp/ui038-child-bounded-peer.log`, with authoritative source_begin before stopping
reads and bounded selector reads matching the latest negotiated capability receipt.
Ruff/strict seven namespace mypy483 clean `/tmp/ui038-child-verified-static.log`;
Coding wheel/sdist three current changed modules byte-verified under
`/tmp/ui038-child-verified-wheels`. Whitespace clean. No downloads/model/auth calls,
user-service restart or unrelated overwrite. Final full-root gate is still separate.

**Outstanding owned process: do not lose track.** Earlier test invocation
`/tmp/ui038-child-backpressure.log`, handle73617, pytestPID340375 and owned CLI
peerPID340485 were authoritatively live at last inspection. The old test's unbounded
TextIO read consumed an earlier child_capabilities receipt with canonical disabled
and waits indefinitely for source_begin after refused admission. User explicitly
requires not interrupting running tests: neither process was signaled, terminated,
or restarted due to observation timeout. New bounded test implementation fixes that
problem for future invocations and supplies independent valid evidence, but does
not retroactively make the old live process terminal. Inspect these exact PIDs/handle
at the next safe checkpoint; do not claim cleanup of that invocation. No production
peer/daemon involved. Other completed test handles are terminal.

UI-038 implementation is now **待验证**, not completed: preserve final closeout and
owned-run lifecycle issue instead of premature completion.64 requirements:36 done,
12 to implement,7 to verify,5 missing external environment,4 deferred;
**24 non-deferred unfinished**. Goal active and full original scope retained.

## UI-039 — opt-in parent transient RPC event stream (partial, 2026-10-10)

Previous turn PROGRESS: UI-038 child canonical implemented and bounded actual CLI
source gates passed; older unbounded-reader test still live. Rechecked exact pytest
PID340375/CLI peer340485, still live, no signal or interruption. Do not replace
that unresolved ownership state with a new successful invocation's evidence.

Implement parent transient_events exact independent negotiation, lease/session/
revision/Agent identity fencing, monotonic sequences and non-retained/replay=false
receipts. Parent tool_start/update/end, assistant update, handled feedback/agent_end
are explicitly projected (no whole history), known-secret redacted, binary progress
omitted/count reported. At most32 scheduled projected callbacks; event bytes bounded
64KiB, overflow/oversize visible limited=true. Withdrawal/re-enable/close prevents
late delivery; no model/history/tool authority introduced. Bind both before reading
and after control read so an actual completed navigation cannot borrow the old Agent.

Tests use actual subscribed event dispatch, known-secret/signature/opaque binary
negatives, stale lease/revision/withdraw/close, exact capability coercion refusal,
queue/byte bounds. Actual owned parent tool loop emits start/update/end and executes
one tool; retained final history remains normal. Actual offline CLI uses streaming
TextDeltaEvent partial, negotiated lease, withdrawal and abort/producer settlement.
Initial CLI test expected an invented aborted packet; correct to actual ok/action=abort
rather than change production protocol. `/tmp/ui039-parent-current.log` retains red
assertion evidence. Final affected parent/RPC/source/child canonical gate **70 passed**
(11.96s), `/tmp/ui039-parent-verified.log`. Ruff/strict all-seven mypy484 clean
(`/tmp/ui039-parent-static-final.log`); offline Coding wheel/sdist current events.py
and rpc_mode.py byte-verified (`/tmp/ui039-parent-wheels`). Whitespace clean.
No user-service restart, paid/auth call, dependency download or unrelated overwrite.

UI-039 remains 待实现: child transient/tool-progress routing and applicable custom
artifact contracts plus complete real-client/transport/cleanup evidence still needed.
No equivalent transient capabilities are inferred from Claw/native or canonical
source tests. UI-038 remains 待验证 while earlier unbounded test is live.64 unique
requirements:36 complete,12 to implement,7 to verify,5 missing external environment,
4 deferred; **24 non-deferred unfinished**. Final Linux terminal/safety/full-root
and all other original goals intact. Goal active.

## UI-039 — independently negotiated child transient progress (partial, 2026-10-10)

Previous turn PROGRESS: parent transient RPC implemented. Revalidate ledger24
unfinished and exact old owned pytestPID340375/peer340485: still live, not interrupted.
Implement child_transient_events independent from parent/child source/approval
capabilities through the existing ChildSupervisor on_event hook; chain/await the
original extension event callback and restore it on detach. No extra Agent loop,
model call, tool grant or generic runtime protocol change.

Child transient event generation, child owner/epoch/session/revision and ID are
checked at queued delivery. Capture original generation BEFORE awaited extension
hook; stale events cannot resurrect after revoke/re-enable while that hook waits.
At most32 projected jobs; oversized projected data withheld with limited=true and
queue overflow visibly marked. Same redaction/binary-omission contract as parent.
Actual child tool loop runs one tool, emits start/update/end, and preserves original
hook invocation and parent empty history/current policy. Actual offline CLI negotiates
child-only transient capability, receives admitted streaming partial with child scope,
withdraws and quits with producer settled. Held original-hook re-enable race and
queued foreign revision/controls/withdrawal plus capacity negatives verified.

Current parent/child transient/RPC/child canonical/control/CLI gate **113 passed**
(40.62s), `/tmp/ui039-child-final.log`. Ruff/all-seven strict mypy484 clean
(`/tmp/ui039-child-final-static.log`); Coding built offline and current changed
children.py bytes verified in wheel+sdist (`/tmp/ui039-child-wheels`). Whitespace
clean. No user-service restart/download/model-auth call/unrelated overwrite.

UI-039 remains 待实现: applicable custom artifact contracts and complete transient
transport/slow-peer/cleanup evidence remain, not inferred from display/canonical
sources. UI-038 remains 待验证 while the earlier unbounded test invocation is live;
handle73617/PID340375/CLI340485 must be re-inspected, not silently killed/restarted.
Authoritative64 requirements:36 complete,12 implementation,7 verification,5 missing
external environment,4 deferred; **24 non-deferred unfinished**. Final Linux full-root/
terminal/permission/lifecycle gates and all original scope remain intact. Goal active.

## UI-039 — scoped declarative extension contributions (partial, 2026-10-10)

Previous goal turn PROGRESS: child transient progress implemented and verified.
Revalidate current ledger24 unfinished and exact old unbounded-testPID340375/peer340485,
confirmed still live; neither interrupted nor restarted. Inspect actual RPC extension
publish/bind/command lifecycle before editing: legacy notice/UI frames existed, but
lacked independent opt-in generation, bounded projection, redaction and post-worker
scope settlement. Preserve existing compatibility for never-negotiated clients.

Implement independent extension_contributions:1/0, separate capability receipt,
request/scope/session generation+sequence and retained=false notice/status/widget/
clear projections. Contribution-only hello preserves dialog/editor/navigation flags.
Bound32 pending callbacks and32KiB payload/64KiB envelope; overflow/oversize visible,
no silent partial result. Contribution delivery settles inside original invocation
before extension_result receipt. Once negotiated, withdrawal forbids a fallback to
legacy duplicate/unredacted notice/UI disclosure. Re-enable revokes queued old data.
No arbitrary custom terminal render/child artifact/history/model authority introduced.

Real sample/uiclear workers verify notice/status/widget/removal, original scopes,
pre-result ordering and zero model/history/policy changes. Actual offline CLI runs
sample command and receives scoped contributions without model call. Coercion,
withdraw/re-enable/session/close, payload/queue bounds and redaction negatives pass.
Current whole named extension/UI navigation/parent+child transient gate **72 passed**
(58.28s), `/tmp/ui039-contributions-verified.log`; focused final withdrawal/no-legacy
coverage **8 passed** (7.61s), `/tmp/ui039-contributions-withdraw.log`, overlapping.
Ruff/all-seven strict mypy484 clean `/tmp/ui039-contributions-current-static.log`;
Coding offline wheel/sdist current extensions.py byte-verified under
`/tmp/ui039-contributions-wheels`; whitespace clean. All new owned checks terminal.
No downloads/model-auth calls/service restart/unrelated overwrite.

UI-039 remains 待实现: child declarative contribution routing and applicable rich
artifact/custom-view protocol contracts plus final transient transport/safety
verification remain. Do not infer those from a successful native renderer or existing
retained source. Old unbounded test handle73617/PID340375 and CLIpeer340485 remain
live, not falsely claimed cleaned; current bounded test source prevents recurrence.
No requirement removed/closed:64 total,36 completed,12 implementation,7 verification,
5 missing external environment,4 deferred; **24 non-deferred unfinished**. Full-root
and current Linux terminal/lifecycle/transport/safety remain mandatory. Goal active.

## UI-039 — independent child declarative contributions (partial, 2026-10-10)

Previous goal turn PROGRESS: parent contributions implemented and verified. Recheck
ledger24 unfinished and old owned pytest340375/CLI340485: still live, not interrupted.
Inspect ChildExtensions actual owned_publish and supervisor publish routing; add
child_contributions exact independent negotiation to existing RPC child path.
Child generation/owner/epoch/session/revision/ID fencing, non-retained sequence,
known-secret redaction,32 pending callback/32KiB payload limits with explicit loss
markers. Controls/contribution withdrawal rotates generation; closed child rejects
late delivery. No parent contribution/source/approval grant is borrowed.

Actual independent sample child worker emits bootstrap status/widget and turn notice;
real CLI validates negotiated child-only data and empty parent history/current deny
policy. Tests cover disabled/default state, withdrawal/re-enable/revision/controls/
close, queue/byte overflow and secret removal. Current broader parent/child
contribution/transient/controls/CLI/extensions gate **123 passed** (64.43s),
`/tmp/ui039-cc-final.log`; focused owner+child gate71 passed in
`/tmp/ui039-child-contributions.log`, overlapping. Ruff/all-seven strict mypy484
clean (`/tmp/ui039-cc-static.log`); current Coding children.py byte-verified in
offline wheel+sdist (`/tmp/ui039-cc-wheels`), whitespace clean. No downloads,
paid/auth calls, user service restarts or unrelated overwrites. New test processes
polled to actual terminal exit. The old unbounded test handle73617/pytest340375/
peer340485 remains live; do not claim its cleanup or restart on timeout.

UI-039 stays 待实现: richer artifact/custom contracts and complete transient transport/
slow-peer acceptance not supplied by this declarative increment. No new duplicate
requirements.64 total:36 complete,12 implementation,7 verification,5 missing external
environment,4 deferred; **24 non-deferred unfinished**. UI-038 still pending verification
closeout; all final Linux safety/terminal/full-root gates retained. Goal active.

## UI-039 — transient output memory reserve and real flood lifecycle (2026-10-10)

Previous goal turn PROGRESS: child declarative contributions implemented/verified.
Inspect real RpcOutput: transient callbacks were bounded at scheduling but could
fill the entire64MiB output backlog over time, leaving no control output reserve.
Add1MiB transient presentation ceiling (also <=half total budget), whole-frame
withholding and per-family bounded lost-event counts; next same-family delivered
event gets limited/transport_dropped, status exposes outstanding counts. Keep
lossless source send() and control/approval ordinary output untouched. No partial
JSON drop, no model/tool permission changes or automatic input replay.

Real pipe tests cover all4 transient families, control/approval after flood, recovery
loss marker and no loss attribution across families. Actual offline CLI parent and
child each admit128 provider updates while the output pipe is deliberately undrained
(marker AFTER all actual yields proves producer admission), then quit/EOF/closed-output
settle actual owned producer. Bounded byte-at-a-time selector reads match capability
receipts instead of recreating the earlier unbounded fixture problem. Test-only
flood adapter is explicit opt-in; no production provider/model network used.

Broader source/parent+child transient/contribution/controls/pipe/CLI gate **140 passed**
(70.64s), `/tmp/ui039-flood-final.log`; after final status-counter addition current
flood/output/parent events/RPC gate **30 passed** (17.81s),
`/tmp/ui039-flood-status.log`, overlapping. Earlier focused flood CLI/pipe gate15
passed (`/tmp/ui039-flood-cli.log`). Current repository Ruff and all-seven strict
mypy484 clean `/tmp/ui039-flood-current-static.log`; current output.py/rpc_mode.py
byte-verified in offline Coding wheel+sdist `/tmp/ui039-flood-current-wheels`.
Whitespace clean; new owned test processes polled to authoritative terminal exits.

UI-039 still 待实现: richer artifact/custom contracts remain; passing flood lifecycle
is not all transient feature acceptance or final full-root regression. Old unbounded
test handle73617/pytestPID340375/CLIpeer340485 were confirmed live this turn and not
interrupted, signaled or restarted; do not claim old invocation cleanup. No downloads,
paid/auth calls/user-service restart/unrelated overwrite.64 total requirements:
36 complete,12 implementation,7 verification,5 missing environment,4 deferred;
**24 non-deferred unfinished**. Full original Linux safety/terminal/full-root goals
retained; goal active.

## UI-039 — full declared Agent event projection (2026-10-10, custom integration still open)

Previous turn PROGRESS (real output flood/control budget fix). Rechecked ledger24
unfinished and exact older unbounded test340375/CLI340485 still live, not interrupted.
Inspect AgentEvent public union: initial transient projection omitted agent/turn/message
boundaries, cumulative tool-call data and identified input consumption/transform/reject
receipts. Implement all14 named declared Agent event kinds in the shared parent/child
projection. Redacted typed message projection preserves tool-call args/numeric usage,
withholds image bodies/signatures/opaque response IDs/redacted reasoning. Tool-end
now includes final safe text/details and binary omission count. No agent-end full
history, rejected original payload or raw stream delta disclosure. Valid stream kind/
index describe cumulative updates without credential-fragment leakage.

Exhaustive union test checks every declared event kind and secret/binary/signed
message/tool-result/usage/stream coordinate behavior. Actual parent/child CLI fixtures
now consume lifecycle receipts until message_update rather than inventing an initial
message-update-only ordering. Child flood assertion isolates the named flood from
actual earlier lifecycle events (wait for admitted history, then clear test log), not
weaken queue bounds. Initial fixture failure retained `/tmp/ui039-lifecycle-tests.log`.

Full current lifecycle/transient/contribution/native-navigation/child source/control/
real CLI/slow-peer acceptance gate **188 passed** (115.51s),
`/tmp/ui039-acceptance.log`; related132-test gate `/tmp/ui039-lifecycle-final.log`,
overlap. Ruff/all-seven strict mypy484 clean `/tmp/ui039-lifecycle-static.log`;
current events.py byte-verified in offline Coding wheel/sdist
`/tmp/ui039-lifecycle-wheels`; whitespace clean. Owned new acceptance process polled
through actual terminal exit, not restarted on observations. No paid/auth/download/
service restart/unrelated overwrite.

Dedup clarification: custom artifact/client-owned view implementation remains the
single UI-042 requirement; UI-039 retains its event integration/transport acceptance
and is not another implementation count for the same custom API. That integration
is still unproven, so UI-039 remains 待实现, not prematurely complete. Old test
handle73617/pytest340375/CLI340485 still live, unmodified; UI-038 closeout pending.
64 unique requirements:36 complete,12 implementation,7 verification,5 missing external
environment,4 deferred; **24 non-deferred unfinished**. All original final Linux/
permission/terminal/complete-root requirements remain. Goal active.

## UI-042 — parent client-rendered RPC custom view protocol (partial, 2026-10-10)

Previous turn PROGRESS: full14 AgentEvent transient projection. Revalidate ledger24
unfinished and exact older test340375/peer340485: live, not interrupted. Inspect real
ExtensionRuntime custom request and DialogBroker/CustomFrames; wire independent
extension_custom_views negotiation, actual owned custom_handler/refresh, client input
and broker frame publication into Coding RPC rather than new render/callback loop.
Parent custom generation/request/scope/session, monotonic input sequence, delivered
frame revision/geometry checks and original broker painted completion gate preserved.
Non-null direct custom answers denied; null cancellation allowed. Strip unsafe terminal
controls from output lines; reserved host interrupts denied by existing codec.
Withdrawal cancels broker custom views; original-generation checks before/after actual
worker render prevent late frame regrant. Custom-only hello preserves prior UI flags.

Actual worker tests initial resize/frame/input completion, forged answer, stale/reused
sequence/foreign scope/generation, reserved keys, resized geometry, withdrawal and
held worker render cancellation/cleanup. Actual offline CLI sends custom frames/input,
receives safe frame and typed False/0/None result, no model/history/policy change.
Current related native custom codec/broker+RPC extension/navigation/contribution gate
**171 passed** (70.88s), `/tmp/ui042-custom-current.log`; earlier165-test group
`/tmp/ui042-custom-final.log` overlaps. Ruff clean, all-seven strict mypy484 clean
(`/tmp/ui042-custom-static-fixed.log`); current extensions.py/rpc_mode.py byte-verified
in offline Coding wheel/sdist `/tmp/ui042-parent-wheels`; whitespace clean.

UI-042 stays 待实现: child custom/terminal routing, nested dialog/focus, live-overlay
controls and complete client/transport acceptance remain. UI-039 custom event integration
also not automatically closed. External client's truthful painted receipt is required,
not server evidence of physical client rendering; no Pi wire/native parity claim.
Old unbounded test handle73617/PID340375/CLI340485 still live, not signaled/restarted.
No downloads/paid-auth calls/user-service restart/unrelated overwrite.64 requirements:
36 completed,12 implementation,7 verification,5 missing environment,4 deferred;
**24 non-deferred unfinished**. Final full-root/Linux safety/terminal/transport goals
remain intact. Goal active.

## UI-042 — nested RPC dialog/custom admission and fresh-frame recovery (2026-10-10)

Previous turn PROGRESS: parent custom protocol implemented/verified. Revalidate old
owned pytest340375/CLI340485 still live, not interrupted. Inspect actual broker
pending-order and CustomFrames suspend/proof gates. Add broker input_owner read-only
admission check; RPC input/nonnull dialog answers require latest live broker dialog,
not merely the last delivered frontend view. Null ancestor cancel still cascades.
A held transport-publication test proves nested admission blocks parent BEFORE
frontend publication. Nested admission invalidates cached parent frame; child
completion's actual returned render is required before subsequent parent input.
Live custom capability negotiation updates owning runtime handlers consistently.

Actual workers cover nested input/confirm and nested custom view, preserved original
parent IDs, no borrowed editor/session authority, parent blocking, new-frame recovery,
stale parent rejection and withdrawal cascading. Real offline CLI walks full nested
question/confirm/recovery and leaves parent history/policy/model unchanged. Existing
navigation/editor completed-future race test now expects explicit refusal from
input_owner (previously an ACK without host grant); retain no-grant assertions,
do not weaken production ownership just to preserve the old ACK expectation.
Initial broader red gate2 failed/174 passed retained `/tmp/ui042-nested-final.log`.

Current related RPC custom/extension/navigation/contribution plus existing native
custom codec/broker gate **176 passed** (78.18s), `/tmp/ui042-nested-verified.log`;
Ruff/all-seven strict mypy484 clean `/tmp/ui042-nested-verified-static.log`;
Coding/Agent current extensions.py/dialogs.py byte-verified in offline wheel/sdist
`/tmp/ui042-nested-wheels`; whitespace clean. New owned verification polled through
actual exit, no observation-timeout restarts/download/paid-auth/service restart/
unrelated overwrite. Old handle73617/PID340375/peer340485 remains live and untouched.

UI-042 remains 待实现: child custom routing, live overlay/focus and complete transport/
client acceptance still missing; UI-039 integration not implied complete.64 requirements:
36 completed,12 implementation,7 verification,5 missing environment,4 deferred;
**24 non-deferred unfinished**. Final current Linux full-root/terminal/lifecycle/
permission/transport requirements remain unchanged. Goal active.

## UI-042 — child client-rendered custom frames and nested input ownership (2026-10-10)

Previous turn PROGRESS: parent nested focus implemented and verified. Rechecked
ledger24 unfinished; old unbounded testpytest340375/CLI340485 still live, no signal
or interruption. Inspect ChildExtensions broker/publish/reserve and actual RPC
child handlers before edits. Add child_custom_views independently negotiated,
custom generation and original child owner/epoch/session/revision to existing
child dialog/frame/input paths; reuse actual broker/render loop, no second tool loop.
Frame geometry/revision/input sequence checked, reserved keys and arbitrary
nonnull custom answers refused; null cancellation retained. Changed capability/
controls cancels custom broker views; late frames carry captured generation.
Add ChildExtensions input_owner accessor for authoritative broker admission so
nested child questions block parent input and restored parent requires fresh frame.

Actual independent child worker tool tests complete/withdraw/cancel/foreign owners,
capability isolation, safe rendered lines and one actual child tool execution.
Actual CLI test answers a SEPARATE child tool permission request before custom UI;
frames/late receipt refusal/completion retain empty parent history and ask policy.
Actual child nested input/confirm returns fresh parent revision before completion.
Initial fixture assumed a stable tool alias: actual exports include registration
identity, so select the tool from current context.tools instead of guessing names.
Historical red logs /tmp/ui042-child-tests.log and /tmp/ui042-child-first.log retained.

Current child/parent custom/child control/real CLI and original broker test gate
**199 passed** (66.92s), /tmp/ui042-child-final.log; Ruff/all-seven strict mypy484
clean /tmp/ui042-child-final-static.log. Coding+Agent built offline; current
children.py/rpc_mode.py/supervision_extensions.py bytes verified in wheel+sdist
/tmp/ui042-child-wheels. Whitespace clean. New owned verification terminal; no
paid/auth calls/download/publish/user-service restart/unrelated overwrite.

UI-042 remains 待实现: live overlay controls, full custom transport/client acceptance
and applicable terminal contracts still open. UI-039 custom event integration not
implicitly closed. Older unbounded-testhandle73617/PID340375/CLI340485 remains live,
unmodified; UI-038 cleanup verification still outstanding.64 unique requirements:
36 completed,12 implementation,7 verification,5 missing environment,4 deferred;
**24 non-deferred unfinished**. Final Linux terminal/safety/complete-root regression
requirements unchanged. Goal active.

## UI-042 — parent live overlay controls (2026-10-10, partial)

Previous turn PROGRESS: child custom frames implemented and verified. Revalidated
ledger24 unfinished and old owned pytest340375/CLI340485, still live and untouched.
Implement independent extension_overlay_controls negotiation; wire original broker
control handler/publications, maintain original sequence and completion-proof
revocation. Add broker custom_input_owner check for hidden/non-capturing state
in addition to nested input owner. Control publications invalidate cached frame;
fresh geometry generation/delivered revision required for completion after focus.
Overlay withdrawal cancels owned live views, preserves existing unrelated UI grants.

Actual overlaymock worker and offline CLI cover non-capturing key refusal,
hide/show/focus, fresh geometry completion and no parent history/model/policy change.
Independent capability/coercion and withdrawal coverage retained. Current related
RPC overlay/custom/extensions/navigation/native broker gate **177 passed** (83.47s),
`/tmp/ui042-overlay-current.log`. Ruff/all-seven strict mypy484 clean
`/tmp/ui042-overlay-final-static.log`; current Coding extensions.py/Agent dialogs.py
bytes verified in offline wheels/sdists `/tmp/ui042-overlay-wheels`. Whitespace
clean. All new checks polled to terminal exit; no downloads/auth/model calls,
user-service restart, unrelated overwrite or interruption of old test processes.

UI-042 stays 待实现: child overlay controls and complete custom client/transport
acceptance still outstanding; UI-039 integration remains separately unproven.
64 requirements:36 completed,12 implementation,7 verification,5 missing environment,
4 deferred; **24 non-deferred unfinished**. Old handle73617/pytest340375/peer340485
still live, not claimed cleaned. Final Linux terminal/safety/full-root gates retained.

## UI-042 — independent child live overlay controls (2026-10-10, partial)

Previous turn PROGRESS: parent overlay controls implemented/verified. Revalidated
ledger24 unfinished and exact older test340375/CLI340485 still live, not interrupted.
Wire child_overlay_controls independently beside child_custom_views; original child
broker custom_control publication, child owner/epoch/session/revision/generation,
cached-frame revocation and hidden/non-capturing input refusal. Overlay/custom/controls
change rotates generation and cancels pending custom views. Add scoped broker
custom_input_owner accessor; no parent focus/tool authority or second tool loop.

Actual child worker/CLI exercise independent capability, hide/show/focus, input denial,
fresh geometry, withdrawal and original painted completion. Real CLI responds to
SEPARATE child tool permission request before viewing, leaves parent history empty
and ask permission policy unchanged. Test-only overlaymock tool accepts explicit mode
so actual owned background controls are exercised; original defaults unchanged.
Current parent/child custom/overlay/control/actual CLI gate **104 passed** (85.88s),
`/tmp/ui042-co-final.log`; focused20-case first gate `/tmp/ui042-child-overlay-tests.log`,
overlap. Ruff/all-seven strict mypy484 clean `/tmp/ui042-co-final-static.log`;
Coding/Agent current children.py/supervision_extensions.py byte-verified in offline
wheels/sdists `/tmp/ui042-co-wheels`; whitespace clean. New checks terminal; no
paid-auth/download/model network/user-service restart/unrelated overwrite.

UI-042 remains 待实现: complete custom transport/backpressure/client and applicable
terminal contract acceptance still needed; UI-039 custom event integration not
implicitly closed. Old unboundedtesthandle73617/PID340375/CLI340485 remains live,
unmodified, not claimed cleaned.64 total requirements:36 completed,12 implementation,
7 verification,5 missing environment,4 deferred; **24 non-deferred unfinished**.
Final Linux complete-root/terminal/safety/lifecycle/transport gates remain. Goal active.

## UI-042 — mixed capability preflight safety (2026-10-10)

Previous turn PROGRESS: child overlay controls implemented and verified. Inspect
actual extension negotiate: earlier custom/overlay/contribution fields changed
flags/generations and canceled views before later validation, so invalid mixed
requests could leave partial authority. Add complete extension capability validation
before any side effects; actual RPC hello preflights extension+child+transient fields
across controllers before dispatch. Transcript compatibility semantics unchanged.

All named extension fields tested with invalid Boolean in otherwise granting batch:
controller state/generations/receipts unchanged. Actual live custom view test rejects
invalid mixed withdrawal with frame/broker/authority preserved. Real offline CLI
checks later invalid child/transient field cannot enable parent custom views and
invalid withdrawal preserves exact existing view/current frame and final completion,
without model/history/permission change. Current broader custom/overlay/child/extension/
contribution/navigation gate **112 passed** (127.47s), `/tmp/ui042-atomic-final.log`;
focused48-case gate `/tmp/ui042-atomic-tests.log`, overlapping. Ruff/all-seven strict
mypy484 clean `/tmp/ui042-atomic-final-static.log`; current extensions.py/rpc_mode.py
byte-verified in offline Coding wheels/sdists `/tmp/ui042-atomic-wheels`; whitespace
clean. New checks polled to actual terminal exit, no timeout restarts.

Old unbounded invocationhandle73617/pytest340375/CLI340485 remained authoritatively
live at checkpoint and unmodified; no claimed cleanup/interruption. No paid/auth/
network model calls/download/service restart/unrelated overwrite. UI-042 still
待实现 pending full custom client/transport and remaining terminal/API contracts.
64 total:36 completed,12 implementation,7 verification,5 missing environment,
4 deferred; **24 non-deferred unfinished**. UI-038/039 and final complete Linux
safety/terminal/root gates not waived. Goal active.

## UI-042 — actual unread large-frame lifecycle evidence (2026-10-10)

Previous turn PROGRESS: mixed capability preflight safety fixed/verified. Revalidate
ledger24 unfinished and exact older unbounded pytest340375/peer340485 still live;
no interruption, signal or observation-timeout restart. Add a bounded actual CLI
custom-frame test for parent/child x quit/EOF/closed-output. Temporary owned copy
of the existing custom worker renders fifteen 8KiB lines; marker proves actual
render, a bounded prefix read verifies LARGE-CUSTOM-FRAME reached real output,
then leaves remaining roughly120KiB undrained. Child case requires an independent
actual tool approval first. CLI exits and /proc/kill(pid,0) confirms owned worker
reaped without tool mutation. No frontend painting inferred from partial output.

Initial assertion incorrectly expected module-style worker argv; actual worker is
an explicit extensions/worker.py path. Red initial6 failures retained in
/tmp/ui042-custom-backpressure.log, corrected to inspect actual worker path plus
exact temporary extension root. First corrected6-pass run
/tmp/ui042-custom-backpressure-fixed.log is superseded by stronger prefix-read
matrix. Current custom/child/overlay/mixed capability/pipe output group **68 passed**
(86.90s), /tmp/ui042-backpressure-final.log. Ruff and all-seven strict mypy484 clean,
/tmp/ui042-backpressure-current-static.log; whitespace clean. Only a test added,
no production or distribution content changed this increment. New verification
polled to actual terminal exit. Old unbounded invocation remains live, not declared
cleaned. No dependency downloads/model-auth calls/user-service restart/unrelated overwrite.

UI-042 remains 待实现: complete client/terminal contract acceptance still unproven;
this named large-frame lifecycle evidence is not full protocol or goal completion.
UI-039 custom integration and UI-038 closeout remain separate.64 requirements:
36 complete,12 implementation,7 verification,5 missing external environment,
4 deferred; **24 non-deferred unfinished**. Final current Linux terminal/security/
transport and frozen full-root regression remain required. Goal active.

## UI-042 — in-flight input/resize retires old frame at admission (2026-10-10)

Previous goal turn PROGRESS: real large-frame backpressure/worker teardown tests.
Inspect actual parent/child custom_input: after admitting resize/key the old delivered
frame remained available until the later renderer publication. Another input could
therefore enter extension callbacks using obsolete geometry/revision, even though
broker completion might eventually refuse it. Both handlers now retire that frame
synchronously after successful broker queue admission; failed admission keeps it.
Subsequent interactive input awaits a fresh delivered frame. Existing owner/sequence/
painted/overlay/nested gates retained, no tool/model authority added.

Add held actual worker-render test and child immediate(no-yield) resize/stale-key
negative; verify rejected input doesn't reach callbacks, only one final Enter is
recorded. Tests polling frame revision now treat absent in-flight frame as loading,
not a KeyError. Initial held fixture incorrectly used asyncio.Event across worker
and test loops; replace with threading.Event and bounded polling on the actual owner
loops. Its original invocation handle38150/PID401202 was left running per user
instruction and polled to TERMINAL red exit:4 failed/37 passed in363.48s,
/tmp/ui042-inflight-tests.log. No interruption or timeout restart. Corrected current
custom/child/overlay/backpressure/capability group **61 passed** (89.24s),
/tmp/ui042-inflight-final.log. Earlier corrected partial red2 failed/39 passed
/tmp/ui042-inflight-fixed.log retained as historical, not current passing evidence.

Ruff clean; all-seven strict mypy484 clean /tmp/ui042-inflight-final-types.log;
current extensions.py/children.py bytes verified in offline Coding wheel/sdist
/tmp/ui042-inflight-wheels; whitespace clean. No downloads/model-auth calls,
user-service restart or unrelated overwrite. Older original unbounded invocation
handle73617/pytestPID340375/CLI340485 still authoritatively live and untouched.

UI-042 remains 待实现: complete custom client/terminal/API acceptance not proven by
this ownership fix; UI-039 integration and UI-038 verification remain separately open.
64 requirements:36 completed,12 implementation,7 verification,5 missing environment,
4 deferred; **24 non-deferred unfinished**. Final full-root/Linux safety/terminal/
transport/lifecycle requirements unchanged. Goal active.

## UI-042 — superseded renderer results cannot restore RPC input authority (2026-10-10)

Previous turn PROGRESS: input/resize admission retires old delivered frame. Inspect
adjacent queued-render window: an older worker render could finish after a newer
geometry generation was admitted and publish an obsolete frame while its successor
was still pending. Broker now labels such results superseded=true; parent/child RPC
filters them before frame receipt caching or transport publication. Existing native
broker frames/completion-proof semantics retained; no callback rollback claimed.

Owned broker test verifies explicit superseded marker. Actual parent/child worker
render callbacks are held with cross-thread-safe events, newer resize queued, old
render released and successor still held: neither RPC path publishes/caches the old
frame or admits stale input. Fresh successor frame restores normal viewing. Related
native broker/custom/RPC child/overlay/large-frame lifecycle gate **158 passed**
(90.76s), /tmp/ui042-superseded-final.log; first138-test gate
/tmp/ui042-superseded-tests.log overlaps. Ruff/all-seven strict mypy484 clean
/tmp/ui042-superseded-final-static.log. Current Coding extensions.py/children.py and
Agent custom_broker.py byte-verified in offline wheels/sdists
/tmp/ui042-superseded-wheels; whitespace clean. New verification polled to terminal
exit; no downloads/model-auth calls/user-service restart/unrelated overwrite.

UI-042 remains open pending complete client/terminal acceptance, not declared
complete from these specific races. UI-039 event integration and UI-038 closeout
remain independent. Old unbounded-testhandle73617/pytest340375/CLI340485 confirmed
live and untouched.64 requirements:36 completed,12 implementation,7 verification,
5 missing external environment,4 deferred; **24 non-deferred unfinished**.
Final current Linux safety/transport/terminal/frozen root regression retained.

## UI-042 — actual custom key-phase and atomic paste contract evidence (2026-10-10)

Previous turn PROGRESS: superseded render filters fixed/verified. Revalidate current
ledger24 unfinished and older unbounded test340375/CLI340485 still live, no interruption.
Inspect actual custom worker declaration/on_input, native codec and parent/child RPC
handlers. Add named end-to-end parent/child worker tests: press-only denies repeat/
release without retiring valid frame, enhanced key_events admits those phases without
completion, explicit Enter press completes; Unicode/newline/tab/quotes paste arrives
as ONE callback, not executable newline/key events. Current frame/revision awaited
after each admission, no stale receipt or model/history/permission change.

Actual offline CLI covers both press-only/enhanced phase contracts and atomic paste;
worker returned inputs exactly match phases plus one complete paste and final press,
False/0/None result retained. Parent/child actual worker cases4 passed in
/tmp/ui042-keyboard-tests.log; final expanded custom/child/overlay/large-frame/
mixed capability gate **69 passed** (110.67s), /tmp/ui042-keyboard-final.log.
Ruff/all-seven strict mypy484 clean /tmp/ui042-keyboard-static.log; whitespace clean.
Only test file added, no production/distribution change. New verification polled to
actual terminal exit; no model-auth/download/service restart/unrelated overwrite.

UI-042 remains 待实现 pending complete client/terminal/API requirement audit; these
specific key/paste contracts do not close remaining custom scope by implication.
UI-039 event integration and UI-038 cleanup closeout remain separate. Old test
handle73617/pytest340375/peer340485 still live and untouched.64 requirements:
36 completed,12 implementation,7 verification,5 missing external environment,
4 deferred; **24 non-deferred unfinished**. Final Linux safety/terminal/transport/
frozen complete-root regression unchanged. Goal active.

## UI-042 — bounded cross-thread custom-frame publication (2026-10-10)

Previous turn PROGRESS: enhanced keyboard/paste contract evidence. Inspect actual
parent/child frame publishers: individual frame bounds did not bound queued
call_soon_threadsafe closures. Add shared CustomDelivery reservation accounting,
16 pending frames/2MiB serialized bytes per controller; count/byte/closed-loop
refusal leaves no reservation. Finally release on callback success/failure. Both
parent and child publishers cancel the owned view on overflow, returning null
instead of silently incomplete interactive content or unbounded queued memory.
No tool/model loop, permissions or custom completion proof changed.

Actual parent/child worker views plus forced publisher bursts verify capacity
refusal cancels the owned view and preserves empty parent history/current policy.
Queue count/byte/exceptions/closed scheduling tested. Current broader custom/child/
overlay/backpressure/keyboard gate **59 passed** (95.65s),
/tmp/ui042-delivery-final.log. Ruff/all-seven strict mypy485 clean
/tmp/ui042-delivery-static.log; three current Coding modules byte-verified in offline
wheel/sdist /tmp/ui042-delivery-wheels; whitespace clean. No downloads/model-auth/
user-service restart/unrelated overwrite. New checks polled to actual terminal exit.

UI-042 remains open pending complete client/terminal/API acceptance; this concrete
stability fix is not a complete protocol declaration. Old unboundedtesthandle73617/
pytest340375/CLI340485 remains live, not interrupted or claimed cleaned.64 unique
requirements:36 complete,12 implementation,7 verification,5 missing environment,
4 deferred; **24 non-deferred unfinished**. All final Linux/full-root gates retained.

## UI-042 — visible, bounded frame-overflow cancellation receipts (2026-10-10)

Previous turn PROGRESS: bounded cross-thread frame delivery. Inspect overflow path:
it canceled broker futures but exposed only null result, giving no named capacity
reason to the client. Add separate bounded/deduplicated error callbacks(max8) with
original scope/child owner/epoch/generation, capacity code and canceled=true. Frame
queue full does not prevent the small error callback; errors are checked on delivery
against captured authority. A stale error cannot clear replacement view receipts.
Count/byte frame accounting and actual original view cancellation retained.

Actual parent/child worker views forced-overflow tests now verify one explicit
capacity error with owned identity, canceled null result and unchanged parent policy.
Error dedup/count release and stale parent scope/child generation re-enable negatives
added. Current custom/delivery/child/overlay/keyboard/large-frame/capability gate
**76 passed** (104.30s), /tmp/ui042-overflow-final.log. Ruff/all-seven strict mypy485
clean /tmp/ui042-overflow-static.log; three current modules byte-verified in offline
Coding wheel/sdist /tmp/ui042-overflow-wheels; whitespace clean. New owned tests
polled to authoritative terminal exit; no downloads/model-auth/service restart/
unrelated overwrite. Old unboundedtesthandle73617/pytest340375/CLI340485 still live,
not interrupted or claimed cleaned.

UI-042 stays open pending complete client/terminal/API acceptance; specific visible
error improvement is not whole goal completion.64 total requirements:36 completed,
12 implementation,7 verification,5 missing environment,4 deferred;
**24 non-deferred unfinished**. All original final Linux/full-root gates retained.

## UI-042 — refused output cannot create frame authority (2026-10-10)

Previous turn PROGRESS: explicit bounded capacity error receipts. Inspect current
parent/child _deliver: frame cached before emit(), so output backlog refusal could
leave an interactive receipt for a frame not admitted to transport. Cache now
published only AFTER successful output enqueue; OSError clears receipt and cancels
original broker view. This is output admission, NOT physical delivery/painting proof.
Actual parent/child worker tests with selectively refusing sinks verify null view
cancellation, no frame cache/reservation leaks and unchanged parent history/policy.

Relevant custom/delivery/child/overlay/keyboard/backpressure gate64 passed
(98.62s), /tmp/ui042-output-final.log. Current Ruff/all-seven strict mypy485 clean,
/tmp/ui042-output-static-fixed.log; current parent/child modules byte-verified in
Coding offline wheel/sdist /tmp/ui042-output-wheels; whitespace clean. A type-only
narrowing assertion followed the test gate; no runtime behavior altered. No downloads,
model/auth calls, publishing, user-service restart or unrelated overwrites.

UI-042 remains open for full named-contract audit; final Linux/root gates unchanged.
64 requirements:36 completed,12 implementation,7 verification,5 missing external
environment,4 deferred;24 non-deferred unfinished. Old owned unbounded test
handle73617/pytest340375/CLI340485 remains live at last inspection, not interrupted
or claimed cleaned. Goal active.

## UI-042 — independent child terminal-data hook RPC (2026-10-10, partial)

Previous turn PROGRESS: frame cache now follows output admission/refusal cleanup.
Inspect existing child capture_terminal, terminal runtime and native editable-key
policy; implement child_terminal_input independent capability with exact current
registry metadata/worker generation, capability generation and child owner/epoch/
session/revision. Data-only editable key or atomic paste hook; no terminal write,
model/child prompt submission or second tool loop. One actual owned RPC job,5s bound,
withdrawal/cancel/close settles existing worker operation, unsafe transformation/error
refused without raw fallback or automatic replay. Protected keys stay host-owned.

Add authoritative PermissionManager.has_pending_requests to reject child terminal
hooks during parent/any-child approval admission before frontend publication. Existing
child broker pending-dialog gate retained. Test confirms unpainted parent approval
blocks hook with zero callback count and no tool mutation. Actual child worker tests
transform/consume/paste, stale/reused/foreign IDs, malformed callback, owned hang
cancel/withdraw/revision and worker reap. Real CLI negotiates child registry, transforms
keys/multiline paste/consume, keeps parent history empty and deny policy intact.
Initial tests observed pre-model user history instead of actual first model boundary;
wait for actual adapter entered marker, do not change production lifecycle. Red logs
/tmp/ui042-terminal-tests.log and /tmp/ui042-terminal-current.log retained.

Current child-terminal/RPC/child controls+CLI/custom/permissions gate **110 passed**
(57.42s), /tmp/ui042-terminal-final.log. Ruff/all-seven strict mypy486 clean
/tmp/ui042-terminal-final-static.log; four current Coding modules byte-verified in
offline wheel/sdist /tmp/ui042-terminal-wheels; whitespace clean. No downloads,
paid/auth calls, user service restart or unrelated overwrite. New owned checks terminal.
Old unboundedtesthandle73617/pytest340375/CLI340485 remains live, unmodified.

UI-042 stays 待实现 until full named contract acceptance audit; child terminal transport
is concrete remaining implementation, not full goal completion.64 requirements:
36 complete,12 implementation,7 verification,5 missing external environment,
4 deferred;24 non-deferred unfinished. Final current Linux/frozen full-root gates
retained. Goal active.

## UI-042 — terminal pre-admission cancellation and late approval (2026-10-10)

Previous turn PROGRESS: scoped child terminal RPC implementation. Revalidated
actual sources and existing original owned test PIDs340375/340485 (still live,
not interrupted). A terminal task canceled before its coroutine starts previously
had no result receipt. Track actual admission and emit one captured-owner canceled
child_terminal_result from task completion when cancellation precedes admission;
no callback invocation, transformed data, consume receipt or automatic replay.

Tests cover explicit early cancel and capability withdrawal, exactly one result,
zero actual worker hook calls and released control slot. Held actual hook result
followed by an unpainted parent approval is rejected by the post-hook owner gate:
no data/consume publication and no retry (actual callback count remains one).
Related child-terminal/control/CLI/custom/permissions/capability gate **127 passed**
(68.64s), /tmp/ui042-terminal-races-final.log. Ruff/all-seven strict mypy486 clean
/tmp/ui042-terminal-races-static.log; current children.py bytes verified in offline
Coding wheel+sdist /tmp/ui042-terminal-races-wheels; whitespace clean.
No model/auth calls, downloads, publishing, user-service restart or unrelated overwrite.
New verification polled to actual terminal exit; original unbounded test still live.

UI-042 remains open pending full contract acceptance; no requirement count change.
64 unique requirements:36 complete,12 to implement,7 to verify,5 missing external
 environment,4 deferred; **24 non-deferred unfinished**. Final Linux terminal,
permissions/lifecycle/transport and complete frozen root regression remain required.

## UI-042 finite contract audit and current verification checkpoint (2026-10-10)

Previous turn PROGRESS: terminal cancellation/late-approval receipts. Consolidate
remaining custom contracts into `agent-ui-rpc-custom-contract-audit.md`, subordinate
to UI-042, no duplicate requirements. Add actual parent/child worker background
repaint verification and actual CLI held-terminal quit/EOF/closed-output/deadline
worker reaping. Source audit found custom key admission lacked authoritative pending
approval gating; fix BOTH paths and verify actual unpainted parent approval refusal
without retiring frame/sequence. These checks do not assert remote physical painting.
Current complete named custom/child/overlay/keyboard/refresh/queue/output/terminal/
capability/broker matrix **221 passed,no skips** (147.55s),
`/tmp/ui042-current-contract.log`; static Ruff/all-seven strict mypy486 clean
`/tmp/ui042-current-contract-static.log`. Current approval admission modules verified
in offline Coding wheel/sdist `/tmp/ui042-contract-wheels`. Only owned offline peers,
no downloads/model-auth calls/service restart/unrelated overwrite/test interruption.

UI-042 moves to 待验证, NOT complete: three finite remaining actions are structured
renderer parent/child RPC integration, physical current-Linux RPC terminal client,
and final integrated contract closure; fix any missing implementation they expose.
Do not repeat undefined broader API OPEN or silently waive those actions. UI-039
integration and final Linux safety/terminal/frozen root gates stay independent.
Original old invocationhandle73617/pytest340375/CLI340485 still confirmed live,
unmodified, not claimed cleaned. **24 unfinished** =11待实现+8待验证+5缺外部环境;
36已完成,4延期,64 total unique requirements. Goal active, original scope retained.

## UI-042 finite action1 — structured RPC renderer integration (2026-10-10)

Previous goal turn PROGRESS: finite custom audit and child terminal lifecycle evidence.
Recheck ledger24 unfinished and old unbounded340375/340485 live, not interrupted.
Run actual layout worker through parent/child RPC in inline/fullscreen/positioned
overlay, verifying row/column/box/virtual viewport/layer, Unicode/narrow/cursor bounds,
vertical/horizontal scroll, layered editor focus, nested question and unchanged
parent history/policy. Initial integration exposed REAL RPC sanitizer deleting ZWJ:
width-calibrated emoji expanded, breaking frame geometry. Use vetted safe_custom_line
then strip styles, preserving ZWJ/combining graphemes while dropping unsafe terminal
commands. Both parent/child frame projections use the same contract. Not a global
loosening of text/credential sanitization. Direct sanitizer regression added.

Initial red /tmp/ui042-layout-tests.log records width defect; a transient indentation
edit error /tmp/ui042-layout-fixed.log collected no tests; test child packet alias
corrected after /tmp/ui042-layout-current.log. Current owner/presentation cases6 pass
/tmp/ui042-layout-final.log. Final current full layout/custom/delivery/child/overlay/
keyboard/backpressure group **73 passed,no skips** (120.39s),
/tmp/ui042-layout-integration.log. Ruff/all-seven strict mypy486 clean
/tmp/ui042-layout-static.log; three affected modules byte-verified in offline Coding
wheel/sdist /tmp/ui042-layout-wheels, whitespace clean. New owned check terminal;
no downloads/model-auth calls/user-service restart/unrelated overwrite.

Finite audit action1 is VERIFIED; actions2 physical Linux RPC terminal and3 final
integrated contract closure remain unproven. UI-042 stays 待验证, not complete.
64 requirements:36 complete,11待实现,8待验证,5缺外部环境,4延期;
**24 non-deferred unfinished**. UI-039 integration and final Linux safety/full-root
requirements remain separate; older owned unbounded invocation still live and untouched.

## UI-042 finite action2 — physical Linux RPC terminal client (2026-10-10)

Previous turn PROGRESS: structured RPC renderer integration/ZWJ geometry fixed.
Owned test frontend launches actual offline Coding RPC subprocess, receives real
parent/child worker layout frames, and paints them through shipped ExtensionCustom/
TUI components inside private tmux. Physical Unicode paste, layer focus/obscured
editor refusal, narrow resize, nested input/return/new frame, final explicit Enter,
parent editor/hardware cursor and exact termios/native main-buffer recovery verified.
Child path has separate actual tool approval before view admission; invalid approval
text cannot grant tool/view. Positive painted revisions and physical resize/paste
receipts logged. No model call on parent command path, no filesystem mutation.

First frontend attempted a second input before fresh frame and relied on obsolete
resize pending counters. Fix test frontend to retire its local frame after enqueue,
wait for matching returned frame, and settle latest geometry accounting; service
stale-frame checks remain unchanged. Host Ctrl+C clears parent draft for explicit
/quit. Historical red physical logs retained, not replaced by implied success.
Final current owned physical fixture **2 passed** (9.12s),
/tmp/ui042-physical-current-fixture.log; physical/structured/keyboard group **15 passed**
(38.01s), /tmp/ui042-physical-verified.log. Ruff/all-seven strict mypy486 clean
/tmp/ui042-physical-final-static.log; whitespace clean. Only tests/offline fixture
changed, no distribution implementation change. New checks terminal; no downloads,
model-auth/user-service restart/unrelated overwrite. Old original unbounded test
340375/340485 still live at inspection, not interrupted or claimed cleaned.

Finite UI-042 audit actions1–2 verified; action3 final integrated contract closure
remains. UI-042 stays 待验证. This is a test frontend, not a shipped remote client,
Pi wire conformance or physical arbitrary-client painting evidence.64 requirements:
36 complete,11待实现,8待验证,5缺外部环境,4延期; **24 non-deferred unfinished**.
Final Linux/full-root/permission/transport gates remain unchanged. Goal active.

## UI-042 COMPLETE — frozen complete RPC/custom integrated contract (2026-10-10)

Previous goal turn PROGRESS: actual Linux RPC terminal acceptance action2 verified.
Final audit inspects public capability registration, actual command reader routing,
parent/child handlers/invocation scope, binary/permission boundaries, broker frame
painted/geometry/generation/sequence and disposal contracts. All finite actions1–3
in agent-ui-rpc-custom-contract-audit.md now have authoritative scope-matched evidence.
One complete current RPC+custom broker run **579 passed,no skips** (334.55s),
/tmp/ui042-final-rpc.log; includes actual Linux physical terminal parent/child paths.
All981 source/test/config files unchanged before/mid/after snapshots
/tmp/ui042-final-source-{before,mid,after}.json. New verification polled to actual
terminal exit, never restarted due to observation timeout.

Ruff/all-seven strict mypy486 clean /tmp/ui042-final-static.log. Current six Coding
contract modules byte-verified in offline wheel/sdist /tmp/ui042-final-wheels;
whitespace clean. No downloads, model/auth calls, publish, user-service restart or
unrelated source overwrite. Source freeze preserved during verification.

UI-042 marked 已完成 for its explicit applicable named RPC contract; previous
repeated generic full-client/terminal/API OPEN is superseded by this finite audit,
not counted again. No arbitrary third-party frontend/Pi wire/native nonLinux or
final whole-root completion claim. UI-039 event integration, UI-038 old invocation
closeout and final Linux/root regression/lifecycle gates remain independent.
Old unboundedtesthandle73617/pytest340375/CLI340485 still confirmed live; not
interrupted or declared reaped. Its outcome is not part of the passing new run.

Current unique counts:64 total,37已完成,11待实现,7待验证,5缺外部环境,4延期;
**23 non-deferred unfinished requirements**. Goal remains active, scope unchanged.

## UI-039 COMPLETE — actual custom-callback transient integration (2026-10-10)

Previous turn PROGRESS: UI-042 finite protocol completed after frozen579-case audit.
Verify UI-039's remaining actual custom callback event integration, not duplicate
UI-042 implementation. Parent/child original custom fixture callbacks emit notice,
status and widget; tests verify original scopes/generations, redaction, ordered
contributions before returned frame, no persistent-history impersonation, and
independent contribution withdrawal without canceling custom view authority. Actual
owned workers complete with one explicit callback per input; no extra model or tool
loop, no parent history/policy changes. Focused integration2 passed
/tmp/ui039-custom-integration.log; no production source change required.

One full current RPC/custom broker matrix **581 passed,no skips** (337.62s),
/tmp/ui039-final-rpc.log, includes actual CLI child/parent transient lifecycle,
contributions, slow/closed-output/control-memory, custom physical Linux terminal,
queue/ownership/approval/withdrawal negatives and all14 public AgentEvent projections.
Source snapshots before/mid/after unchanged across982 files:
/tmp/ui039-final-source-{before,mid,after}.json. Ruff/all-seven strict mypy486 clean
/tmp/ui039-final-static.log; whitespace clean. New owned run polled to actual exit,
not restarted on timeout; no production artifact changed, no dependency download/
model-auth calls/user-service restart/unrelated overwrite.

UI-039 marked 已完成 for explicit named transient/tool-progress/custom integration;
UI-042 owns custom protocol implementation once. Historical generic richer/custom
OPEN clauses superseded by these current acceptance records, not additional counts.
No all transient history replay/native nonLinux/Pi wire/general-client claims. Original
unbounded-testhandle73617/pytest340375/CLI340485 still live and untouched; its own
closeout remains UI-038/final UI-064 lifecycle, not claimed passed by new evidence.
Final whole-root/Linux terminal/security requirements remain separate.

Current64 unique requirements:38已完成,10待实现,7待验证,5缺外部环境,4延期;
**22 non-deferred unfinished**. Goal remains active with original scope unchanged.

## UI-036 — independently negotiated parent RPC terminal hook data (2026-10-10)

Previous turn PROGRESS: UI-039 completed with actual custom callback integration.
Inspect actual parent/child prompt hook runtime and RPC reader: parent terminal hook
transport absent while child terminal_input implemented. Add RpcTerminal with exact
independent terminal_input negotiation, captured connection owner/session/revision/
worker generation, bounded request identities and one active owned job. Only shared
editable-key or atomic-paste data; no prompt/model/tool submission or terminal writer.
Before/after approval/dialog/context lifecycle checks,5s deadline, transformation
revalidation, cancellation/withdrawal/EOF cleanup and single early-cancel receipt.
Reader integrates actual terminal_input/terminal_cancel; prompt/navigation/extension
and child terminal admission cannot race the owning parent terminal operation.

Actual worker transform/paste/consume, early cancel/withdraw, foreign/reused owner,
hanging cancel/deadline/reap, unsafe transform and late approval negatives verified.
Actual offline CLI activates extension, negotiates current worker metadata and returns
editing data without model/history mutation. Related parent+child terminal/RPC/control/
navigation/capability gate **70 passed**(67.63s), /tmp/ui036-parent-final.log; final
current parent source check **11 passed**(17.99s), /tmp/ui036-parent-current.log.
Ruff/all-seven strict mypy487 clean /tmp/ui036-static-verified.log; current terminal.py/
rpc_mode.py bytes verified in offline Coding wheel/sdist /tmp/ui036-wheels;
whitespace clean. New test processes terminal; no downloads/model-auth/user-service
restart/unrelated overwrite. Old unbounded340375/340485 still live, not interrupted.

UI-036 stays 待实现: broader raw input surface contracts/final audit still required,
not automatically completed from this concrete missing parent transport. Existing
native parent/child input, protected keys and permissions preserved; no vendor wire
compatibility or arbitrary terminal interception claim.64 unique requirements:
38 complete,10 implementation,7 verification,5 missing environment,4 deferred;
**22 non-deferred unfinished**. Full original Linux/root gates unchanged. Goal active.

## UI-036 — cross-parent/child unpublished dialog input ownership (2026-10-10)

Previous turn PROGRESS: parent RPC terminal data implemented/verified. Source audit
found parent hook ignored child pending-dialog ownership; child hook ignored parent
and other-child dialogs. Add authoritative ChildExtensions.has_pending_dialogs
accessor under its own lock. Parent/child RPC terminal admission and post-hook receipt
checks reject any parent/child pending dialog, including unpublished broker admission.
No invocation callbacks or parent history/permission effects introduced.

Actual broker admission tests deny parent hook before unpublished child question;
child question admitted after actual parent hook result rejects data/consume with
one callback/no retry. Child hook likewise denied during unpublished parent question,
zero callback count. Related parent/child terminal lifecycle/custom/RPC/capability
gate **84 passed**(92.74s), /tmp/ui036-dialog-final.log; first34-test gate overlaps.
Ruff/all-seven strict mypy487 clean /tmp/ui036-dialog-static.log; three affected
Coding/Agent modules byte-verified in offline wheels/sdists /tmp/ui036-dialog-wheels;
whitespace clean. New tests polled to terminal exit, no observation-timeout restart,
download/model-auth/user-service restart or unrelated overwrite.

UI-036 stays 待实现 pending broader input surface finite audit/acceptance, not
automatically closed by these lifecycle gates. Old unboundedtesthandle73617/pytest
340375/CLI340485 remains authoritatively live, untouched.64 unique requirements:
38 complete,10 implementation,7 verification,5 missing external environment,
4 deferred; **22 non-deferred unfinished**. Final Linux/full-root gates unchanged.

## UI-036 — actual parent/child CLI terminal-hook lifecycle (2026-10-10)

Previous turn PROGRESS: cross-dialog admission guards. Extend the existing bounded
owned CLI lifecycle fixture to parent and child hooks, rather than duplicate a peer.
Actual worker entered marker and exact /proc worker/root identity prove hook admission;
quit/EOF/closed-output/deadline cause CLI exit and owned worker reaping. Closed-output
case explicitly requests status to exercise a failed write while stdin remains open;
parent editing never invokes model, child producer settles, no tool mutation.
Current parent/child terminal/RPC/capability suite58 passed (64.44s),
/tmp/ui036-lifecycle-final.log; focused lifecycle8 passed (21.60s),
/tmp/ui036-parent-lifecycle.log. Ruff/all-seven strict mypy487 clean
/tmp/ui036-lifecycle-static.log; whitespace clean. Only test changed, no production
artifact changes. New tests terminal, no downloads/auth/model calls/service restart/
unrelated overwrite. Old unbounded pytest340375/CLI340485 still live and untouched.
UI-036 remains open for named broader surface acceptance audit.64 requirements:
38 completed,10 implementation,7 verification,5 missing environment,4 deferred;
22 non-deferred unfinished. Final whole-root/current Linux safety gates retained.

## UI-036 — prompt release boundary and finite surface audit (2026-10-10)

Previous turn PROGRESS: actual parent/child CLI hook cleanup verified. Inspect input
phase contract: release could be sent to a text-transform hook and become inserted
press text. Shared native prompt interception now excludes release before hook
without invalidating held edits; transformed releases refused, no partial submission.
RPC editing data rejects release on input/result, preserves encoded repeat; atomic
paste stays inert. Custom release support remains its independently declared UI-042
contract. Actual Coding/Claw owners verify release triggers zero worker callbacks.

Current shared/key/RPC/native owner group127 passed(72.53s),
/tmp/ui036-release-final.log; actual Linux both-client prompt/child editor pause/
recovery/resize/native terminal6 passed(14.59s), /tmp/ui036-release-terminal.log.
Ruff/all-seven strict mypy487 clean /tmp/ui036-release-current-static.log;
Coding/TUI current affected source bytes verified in offline wheel/sdist
/tmp/ui036-release-wheels, whitespace clean. New owned checks reached terminal exit;
old unbounded340375/340485 remain live, not interrupted/restarted/claimed cleaned.

Named input surface contract audit docs/agent-ui-input-surface-audit.md now defines
three finite remaining actions: reconcile concrete historical/reference raw contracts,
physical RPC editing-data application, and integrated final matrix. Preserve missing
applicable implementation rather than silently narrowing to editable-only input.
UI-036 stays待实现;64 unique requirements38 completed,10 implementation,7 verification,
5 missing environment,4 deferred;22 non-deferred unfinished. Final Linux/root gates
unchanged; no downloads/model-auth/user-service restart/unrelated overwrite.

## UI-036 — effective configured RPC input keymap (2026-10-10)

Previous turn PROGRESS: release boundary and finite input-surface audit. Actual RPC
editable_data used only default bindings, unlike native hosts' configured keymap.
Load bounded keybindings.json at parent/child controller construction and pass the
immutable effective keymap through input and transformed-result validation. Remapped
send/external editor/save/extension command keys cannot become hook editing input.
Invalid configured keymap fails closed for key data instead of silently treating
fallback defaults as authority. Atomic paste remains data, not parsed shortcuts.

Actual parent/child worker tests configured and malformed keymap refuse before hook,
zero callbacks; transformed remapped host key refused after one actual hook without
result/replay. Existing default/RPC/keymap/CLI lifecycle group **90 passed**(71.13s),
/tmp/ui036-keymap-final.log; focused45-case gate /tmp/ui036-keymap-tests.log overlaps.
Ruff/all-seven strict mypy487 clean /tmp/ui036-keymap-static.log; three current Coding
modules byte-verified in offline wheel/sdist /tmp/ui036-keymap-wheels; whitespace
clean. New owned checks terminal; no downloads/model-auth/service restart/unrelated
overwrite. Original unboundedtest340375/CLI340485 still live and untouched.

UI-036 remains待实现 pending finite raw contract reconciliation, physical RPC editing
data application and final input matrix.64 total:38 complete,10 implementation,
7 verification,5 missing environment,4 deferred;22 non-deferred unfinished.
Original current Linux safety/terminal/full-root requirements unchanged. Goal active.

## UI-036 finite action2 — physical RPC hook data application (2026-10-10)

Previous turn PROGRESS: configured RPC keymap guards. Actual owned Linux private
terminal frontend now invokes parent/child original RPC hooks and applies returned
data through shipped PromptTerminalInterceptor/InputPanel, not a fabricated string
or pure JSON assertion. Physical Unicode paste/hardware cursor, original encoded
repeat, release no-hook dispatch, held hook Escape original-owner cancellation,
paused plain-text recovery and final exact termios/main-buffer restoration verified.
Worker log proves actual callbacks/input kind, no parent model call, child producer
settles on exit. New test frontend does not claim a shipped remote client.

Physical parent/child2 passed(5.33s), /tmp/ui036-physical-edit.log; current related
hook/CLI/keymap/editor group **88 passed,no skips**(69.96s),
/tmp/ui036-physical-edit-final.log. Ruff/all-seven strict mypy487 clean
/tmp/ui036-physical-edit-static.log; whitespace clean. Only owned test frontend/tests
added, no production/distribution change. New checks polled to actual terminal exit;
no downloads/auth/model network/user-service restart/unrelated overwrite.

Finite input audit action2 VERIFIED; action1 concrete raw contract reconciliation
and action3 integrated input matrix remain open. UI-036 still待实现, no blanket raw
parity claim.64 requirements:38 completed,10 implementation,7 verification,
5 missing environment,4 deferred; **22 non-deferred unfinished**. Old original
unbounded test340375/340485 still live, untouched; final Linux/root safety gates retained.

## UI-036 — actual runtime shortcut-aware RPC editing boundary (2026-10-10)

Previous turn PROGRESS: physical RPC editor data application. Inspect current
runtime shortcuts and native ExtensionShortcuts conflict/override/disable rules;
RPC editing previously checked configured actions but not owned active extension
shortcut inventory. Parent/child input and transformed output now use actual runtime
shortcuts via shared native effective resolver. A registered action cannot be routed
as editing data; conflicting disabled shortcut does not revoke native editor action.
Atomic paste stays data. Actual parent/child workers verify registered shortcut input
refusal before callbacks and transformed shortcut result refusal after one callback.
Initial temporary fixture replacement accidentally also matched its nested enable
statement; restrict replacement to first occurrence, no production change for fixture.
Red /tmp/ui036-shortcut-inventory.log retained; current related hook/keymap/physical
editor/shortcut gate **60 passed** (49.05s), /tmp/ui036-shortcuts-final.log.
Ruff/all-seven strict mypy487 clean /tmp/ui036-shortcuts-static.log; three current
Coding modules byte-verified in offline wheel/sdist /tmp/ui036-shortcuts-wheels;
whitespace clean. New verification terminal; old340375/340485 still live, untouched.
Primary Pi fetch yielded no usable webpage; bounded curl failed connection reset,
so no new upstream comparison or completeness assertion is made. UI-036 finite
reference/raw-contract reconciliation remains open; no guessed API or scope reduction.
64 unique requirements:38 completed,10 implementation,7 verification,5 missing
external environment,4 deferred;22 non-deferred unfinished. Final current Linux
safety/terminal/full-root gates retained; no downloads/model-auth/service restart.

## UI-036 — approval open/close admission revision fences (2026-10-10)

Previous turn PROGRESS: runtime shortcut inventory safety. Inspect pending-only
approval checks: a question could open and close during a hook then disappear before
result delivery, allowing old edit across input ownership change. PermissionManager
now exposes monotonic admission_revision, incremented under its lock on actual
approval admission. Parent/child RPC hooks snapshot parent/child manager identities
and revisions; both before/after callback checks refuse changed approval admission
even after all pending requests settle. Manager inventory replacement also refuses.
No policy escalation, retry, input submission or rollback claim.

Tests hold actual worker hook results, admit and deny actual parent/child approvals,
then release hook: all four source combinations reject data/consume with one callback.
Initial related gate1 failed/69 passed in /tmp/ui036-approval-revision-final.log due
to missing runtime shortcut registration in one existing parent fixture. Do not hide
that red result: exact isolated keymap13 passed /tmp/ui036-approval-keymap-recheck.log,
and a fresh current complete related gate **70 passed**(74.24s),
/tmp/ui036-approval-current.log. No production shortcut behavior changed to suppress
that fixture. Ruff/all-seven strict mypy487 clean /tmp/ui036-approval-revision-static.log;
three current modules byte-verified in offline Coding wheel/sdist
/tmp/ui036-approval-revision-wheels; whitespace clean. New checks terminal; no
model-auth/download/user-service restart/unrelated overwrite. Old unbounded pytest
340375/CLI340485 still live and untouched.

UI-036 stays待实现 pending finite original raw contract reconciliation and final input
matrix; action2 physical RPC editor evidence remains verified.64 requirements:
38 completed,10 implementation,7 verification,5 missing environment,4 deferred;
**22 non-deferred unfinished**. Final current Linux/full-root requirements retained.

## UI-038 safe-checkpoint owned hang cleanup / current acceptance (2026-10-10)

Current authoritative state: `agent-ui-requirements.json`, generated Markdown;
`agent-ui-rpc-canonical-audit.md` records all named canonical/binary checks and the
confirmed Linux hung invocation. Prior never-interrupt wording does not indefinitely
retain confirmed owned hung tests: user now explicitly permits identity-checked,
normal-exit-first targeted cleanup, never normally progressing tests/services/Codex.
Historical handle73617 exit143 is aborted/red evidence, not a pass. Current bounded
startup/capability/no-prefetch test regressions and real undrained binary source
matrix passed118/no skips; static487 clean. UI-038 completed without weakening tests.
64 unique IDs:39 complete,10 implement,6 verify,5 external,4 deferred;21 current
non-deferred unfinished. Goal remains active; final frozen root and Linux safety
remain required. No publishing/restarting services/unrelated cleanup.

## UI-046 admission/history crash boundary completion (2026-10-10)

Authoritative UI-046 completed by prospective typed history/handled checkpoint before
visible receipts, atomic sidecar/base digest, uncertain old queue at inter-file crash,
awaited queue settlement, no automatic replay. Both actual native host factories wired.
Owned Linux six-boundary SIGKILL×image transform/handled, terminal precedence, storage
failure/cancel and real host/RPC safety final229 pass/no skips70.23s; static487 clean,
five modules current wheel/sdist exact. Detailed protocol/red/cancel-race evidence:
`agent-ui-input-admission-audit.md`. Historical flush-before OPEN no longer separate
pending demand; not an exactly-once external effect or physical-loss claim. UI-047 and
final-root/safety gates remain open.64 unique IDs:40 complete,9 implement,6 verify,
5 external,4 deferred;20 non-deferred unfinished. Goal remains active.

## UI-047 draft crash-window requirement completion (2026-10-10)

Current unique ledger UI-047 complete: both native hosts expose pending/writing/
committed/uncertain states, latest-edit vs old receipt; actual Linux parent/child
SIGKILL window matrix, initial publication alias nonmutating read/reviewed delete,
fresh offline Claw private app dir creation and physical terminal recovery. Named
contract/red evidence in `agent-ui-draft-crash-audit.md`; current170 pass/no skips,
static487 clean, six source module wheel/sdist exact. Historical debounce/inflight
OPEN no longer counts independently; physical-loss UI-058 still required.64 total:
41 complete,8 implement,6 verify,5 external,4 deferred;19 non-deferred unfinished.
Goal active, final complete root/Linux safety/terminal gates intact; no service restart.

## UI-045 native backend implementation acceptance (2026-10-11)

Historical nonLinux credential UNIMPLEMENTED requirement now closed by explicit
Keychain/Windows Credential Manager selection and bounded isolated native calls,
review/lease/no-file-fallback, full WinCred record staging and noninteractive Keychain.
Linux actual helper/ABI/manager/two-host UI contract66 passed; static488 clean,
three source module wheel/sdist exact. `agent-ui-native-credential-audit.md` records
contracts and limitations; UI-062 native execution is still deferred/not claimed.
64 IDs:42 complete,7 implement,6 verify,5 external,4 deferred;18 unfinished current
non-deferred. Goal active; no OS sandbox, publication or user-service restart.
