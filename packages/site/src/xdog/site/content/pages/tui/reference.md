---
title: Reference
---

Linux-only Python 3.12+ library with `wcwidth` as its direct runtime dependency.
There is no TUI console script. Import from `xdog.tui`, not `tui`.

## Top-level API

| Name | Import | Purpose |
|---|---|---|
| `TUI / Container / Component` | `xdog.tui` | Terminal-loop owner and string-row composition model. |
| `Terminal / ScreenBuffer / Cell` | `xdog.tui` | Lower-level terminal/grid primitives. |
| `Focusable / OverlayHandle / OverlayOptions` | `xdog.tui` | Focus participation and overlay control. |
| `parse_key_events / KeyEvent / KeyEventType` | `xdog.tui` | Key decoder, modifiers and press/repeat/release events. |
| `KeybindingManager / Keybinding / PromptKeymap` | `xdog.tui` | Scoped bindings and effective prompt actions. |
| `fuzzy_match / fuzzy_filter` | `xdog.tui` | Matching/filtering helpers. |
| `AutocompleteEngine / WorkspaceFileCompletionProvider` | `xdog.tui` | Completion interfaces and an owned filesystem worker. |
| `render_image / detect_capabilities` | `xdog.tui` | Terminal image rendering and capability detection. |
| `EditorComponent / UndoStack / KillRing` | `xdog.tui` | Editing protocol and primitives. |
| `visible_width / slice_by_column / truncate_to_width` | `xdog.tui` | Display-cell-aware text helpers. |
| `AttachmentComposer / AttachmentReader / read_attachment` | `xdog.tui` | Composer ownership and bounded off-thread/explicit image reads. |
| `TranscriptBrowser / TranscriptRecord / TranscriptView` | `xdog.tui` | Immutable full-source browsing, indexing/search and viewport data. |

## Component imports

Not every helper is re-exported from `xdog.tui.components`; use the exact module
for specialized components.

| Component | Import | Purpose |
|---|---|---|
| `Box / Text / Spacer` | `xdog.tui.components` | Layout and static rows. |
| `Input / Editor / InputPanel` | `xdog.tui.components` | Editing, history, undo and completion. |
| `Markdown` | `xdog.tui.components` | Rendered Markdown. |
| `Diff` | `xdog.tui.components.diff` | Unified diff rendering. |
| `Image` | `xdog.tui.components` | Kitty/iTerm2 inline images where supported. |
| `SelectList` | `xdog.tui.components.select_list` | Selection list; also exported as the `SelectListComponent` compatibility alias. |
| `SettingsList / Loader / CancellableLoader` | `xdog.tui.components` | Configuration and activity displays. |
| `PermissionPanel / DetailsPanel / StatusLine` | `xdog.tui.components` | Shared bounded display/callback panels; permission decisions stay in hosts. |
| `NavigationPanel / TranscriptPanel / AttachmentPanel` | `xdog.tui.components` | Focused navigation, full-source browsing and image preview. |
| `InlineLayout` | `xdog.tui.components.inline_layout` | Terminal-height allocation. |
| `Transcript` | `xdog.tui.components.messages` | Retained message/tool presentation and immutable source projection. |
| `ChildPanel / ChildQueuePanel` | `xdog.tui.components.child_panel` / `xdog.tui.components.child_queue_panel` | Host-driven child presentation; queue panel is read-only. |
| `McpPanel / OAuthPanel` | `xdog.tui.components.mcp_panel` / `xdog.tui.components.oauth_panel` | Reviewed UI, not server connection, token or tool authority. |

## Host integration

| API / module | Contract |
|---|---|
| `TUI.add_child / set_focus / start / stop` | Configure and own an interactive terminal loop on the UI thread. |
| `TUI.request_render / on_tick` | Schedule frames and nonblocking host callbacks. |
| `TUI.request_fullscreen` | Explicit owned temporary alternate-screen transition; default remains main-buffer scrollback. |
| `TUI.run_outside_terminal / edit_draft` | Foreground handoff and private-file external-editor round trip; host applies returned text. |
| `xdog.tui.event_queue.EventQueue` | Bounded UI-thread event delivery. |
| `InputPanel.set_autocomplete_provider / poll_autocomplete` | Nonblocking completion integration; repaint only on change and close owned providers. |
| `xdog.tui.transcript_transfer.TransferWorker` | Explicit prepared output/snapshot work off-thread, not automatic upload or replay. |
| `xdog.tui.transcript_actions.TranscriptActions` | Host-context-fenced review lifecycle for captured outputs. |

Pure rendering can be tested without a terminal; actual raw mode, cursor reports,
resize, suspend and foreground handoff require a suitable interactive terminal.
Library UI never activates skills/capabilities, restores Agent snapshots, compacts
history or owns a child runtime. Those remain [Agent/host](/packages/agent/design)
concerns. A reviewed export is not execution approval or a durable task receipt.

## Image and platform boundaries

| Surface | Notes |
|---|---|
| Kitty Graphics | Capability-dependent inline output on compatible terminals. |
| iTerm2 inline | Protocol compatibility only, not a non-Linux support promise. |
| Dimension parsers | Pure-Python PNG/JPEG/GIF/WebP; no Pillow. |
| Composer attachments | Validated bounded PNG/JPEG/WebP data; model vision and send permission remain host policy. |
| Linux clipboard | Explicit Wayland `wl-paste` / X11 `xclip` helpers; no background polling or non-Linux adapter. |

See the [complete component reference](https://github.com/litterzhang/xdog/blob/main/packages/tui/README.md)
and [terminal verification scope](https://github.com/litterzhang/xdog/blob/main/docs/tui-pi-parity.md).
