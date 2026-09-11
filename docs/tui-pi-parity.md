# Terminal UI stability

This replaces the earlier broad Pi-parity roadmap. The scope is Coding and Claw
in the main terminal buffer, preserving native scrollback. **Remote coding mode
and fullscreen application mode are explicitly excluded.** Existing unrelated
library scaffolding does not imply application support.

## Implemented behavior

- Shared Unicode editor and inline layout. Details and permission panels appear below the editor. Permission, autocomplete,
  recent details and queue share a bounded area. Both clients show contextual
  shortcut hints when height permits. Spare height adds a blank row before status.
- Permission summaries scroll with Page Up/Page Down. Up/Down selects an action;
  the selected action stays visible at small heights. Escape denies that call.
  At one terminal row only the editor fits; enlarge the terminal to approve.
- Ctrl+O opens focused tool/reasoning details; Escape, Ctrl+C or Ctrl+O restores
  the input draft. Coding also provides `/details [close]` and `/status`.
  Empty panels show feedback. Left/Right changes entries; Page Up/Page Down
  scrolls. Details open at the top for both reasoning and tools. Home goes to the top;
  End jumps to the latest output and enables following.
- Coding distinguishes waiting, reasoning, responding, tool execution, approval
  and cancellation. Elapsed time is monotonic; terminal events release approval
  focus and clear the timer. Tool summaries retain final duration. Narrow status
  rows prioritize activity, queue and context; `/status` exposes full metadata.
- Claw uses monotonic elapsed time across busy phase changes, clearing it on
  return to idle. Its details hints follow focus and disappear on short terminals.
- Queue previews are bounded; the status keeps the queue count while another
  surface is visible. Cancellation restores queued text ahead of a newer draft.
  Claw keeps submissions made during abort settlement editable.
- Turn stamps and permission identity checks reject stale UI work. Full tool
  output remains in state independently of compact transcript previews.
  Permission producers capture the originating stamp at registration. Both
  clients enqueue immutable snapshots and reconstruct owned payloads on the UI
  thread. Transport failures restore Claw's active and queued drafts once.
- Small layouts suppress status/decorations before hiding edit space or the
  permission action. Hidden work summaries have an omission count. Detail page
  sizes follow the actual panel budget, with scrolling clamped at both ends.
  Live tool output is available in details, with newest output in the preview.
- Startup, resize and resume use bounded cursor-position queries. The renderer
  clears vacated owned rows instead of the screen or native scrollback, and
  erases transient rows before scrolling newly appended conversation output.
- Input framing consumes fragmented/coalesced/late terminal replies. Bracketed
  paste stays atomic across delays; oversized paste is rejected without running
  its remainder as keystrokes. EOF restores terminal modes.

## Verification

Regression tests under `packages/tui/tests`, Coding's `test_compact_layout.py`,
and Claw's `test_inline_behavior.py` cover input, Unicode cursor boundaries,
owned-row painting, panel priority and delayed events.
`test_terminal_lifecycle.py` covers startup/runtime failures, EOF, missing
cursor reports, and signal-handler restoration. Exact-screen tests combine
queue, permission, details, width/height changes and native history. Idle-poll
tests assert zero output bytes, and changed-tail tests assert one complete frame.

`packages/coding/tests/test_terminal_acceptance.py` launches the actual Coding
CLI with an offline provider and the production Claw socket client against a
controlled Unix socket server. Each test owns a private tmux server and isolated
XDG state. Neither the live gateway nor paid model APIs are used.

```bash
uv run pytest packages/coding/tests/test_terminal_acceptance.py -q
```

These tests exercise permission denial, resizing, multiline paste, suspension,
queue/cancel recovery, tool details and late-event suppression. They capture
`terminal.raw`, `pane.txt`, `visible-pane.txt`, `cursor-observations.txt`, and
`termios-result.txt` in pytest's temporary test directories. Assertions use the
current visible pane and actual cursor coordinates; resize checks wait for a new
cursor query and completed repaint. They check terminal-mode restoration and
absence of screen/scrollback clears or alternate-screen entry. Tests skip
explicitly without POSIX or tmux.

Runtime evidence is limited to the tested Linux/tmux profile. Actual IME popup
positioning, desktop terminal reflow differences and Windows console behavior
still need platform-specific manual checks. A green suite does not establish
that those environments were tested.

## Deferred and excluded work

Remote host/client protocols, controller leases, serve/connect commands, and a
fullscreen application viewport are outside this plan. Full extension/theme
ecosystems, images/diagrams and full-history search/selection remain separate
projects. Basic theme presets, saved transcript replay and renderer benchmarking
are implemented as documented in `tui-usability-plan.md`. Existing helpers are left intact
and must not be described as finished application features.
