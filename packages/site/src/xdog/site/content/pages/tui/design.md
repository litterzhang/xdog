---
title: Design
---

A string-row rendering toolkit for Linux/Python 3.12+, not an owner of models,
approvals, sessions or child execution.

## Differential rendering and native scrollback

`Component.render(width)` returns `list[str]`. The default `TUI` renderer diffs
owned rows against the prior frame and emits bounded terminal updates. Startup,
resize and resume use cursor reports to re-anchor the transient tail; Ctrl+L
repaints visible owned rows without clearing native scrollback. Geometry changes
can defer a frame rather than painting under stale dimensions. Actual terminal
behavior still depends on the emulator and supported protocols; this is not an
unconditional no-flicker guarantee.

The default is the main terminal buffer. Hosts can explicitly request a temporary
fullscreen surface with `request_fullscreen(enabled)`; the owned transition uses
an alternate painter and restores main-buffer ownership on return. This does
not make all applications fullscreen or grant extension custom-layout authority.

## Components, focus and visible confirmation

A `Container` vertically concatenates child rows; `TUI` is itself a Container.
Components handle input and invalidate cached views. Overlays composite by display
cells, can capture focus and restore base focus on close. Focused editor markers
position the terminal cursor; a panel without a marker hides it.

Shared `InputPanel`, `PermissionPanel`, `DetailsPanel` and `StatusLine` accept
data, theme functions and callbacks. `InlineLayout` allocates height; it does not
choose permission or execution policy. Confirmation panels require the relevant
fields/actions to be painted, invalidate review on geometry/focus changes and
refuse clipped, pasted or otherwise unreviewed confirmation input.

## Input and workers stay on their owners

Key parsing distinguishes press/repeat/release and modifiers. `PromptKeymap`
resolves effective host actions; editor paste is atomic rather than interpreted
as terminal commands. Unicode-aware width/clipping preserves display-cell and
grapheme boundaries where required.

`TUI.start()` owns a synchronous raw-terminal loop on the UI thread. Host tick
callbacks must not block on files, networks or model calls. Workers publish
immutable events; the host applies them and requests repaint. `EventQueue.drain()`
uses bounded FIFO batches (64 events or 8 ms between callbacks by default) to
avoid monopolizing a tick. Hosts close owned completion, attachment, navigation,
transcript and output workers during shutdown.

`run_outside_terminal(callback)` hands off only at an owned boundary, restores
terminal modes around foreground work and re-enters negotiation afterward.
External-editor results remain drafts until the host explicitly applies/submits
them. Neither handoff nor a UI confirmation is tool approval.

## Images and clipboard are different capabilities

Inline Kitty/iTerm2 image output depends on terminal detection, not the operating
system name in a protocol. Dimension parsing for PNG/JPEG/GIF/WebP is pure Python;
there is no Pillow dependency. Validated composer attachments separately support
PNG/JPEG/WebP and bounded reads.

Linux clipboard reads use explicit Wayland `wl-paste` or X11 `xclip` operations,
not polling. No non-Linux clipboard adapter is shipped. Terminal image compatibility
does not imply macOS/native-platform support or model vision capability.

## Host policy remains outside rendering

MCP/OAuth panels do not connect servers or manage credentials. Child panels and
the read-only child queue inspector do not create children or mutate execution
queues. Transcript search/export and typed snapshot review operate on host-supplied
source/artifacts, not Agent persistence or resumable execution authority. Hosts
retain permissions, scope, storage and lifecycle fences.

See [API/import reference](/packages/tui/reference) and the
[terminal verification scope](https://github.com/litterzhang/xdog/blob/main/docs/tui-pi-parity.md).
