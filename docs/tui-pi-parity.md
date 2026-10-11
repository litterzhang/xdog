# Terminal UI stability

This records the completed terminal-stability phase for Coding and Claw in the
main terminal buffer, preserving native scrollback. Remote coding and fullscreen
were excluded from that phase only. The expanded active goal is tracked in
`agent-ui-gap-plan.md`; it now includes optional fullscreen, search, extensions,
and the other identified UI/capability gaps, excluding OS-enforced sandboxing.
Existing unrelated library scaffolding does not imply application support.

## Implemented behavior

- Coding serializes slash commands separately from agent turns. Session/model/
  skill mutations require an idle agent; rejected commands and submissions made
  during a mutating command stay editable. Read-only commands keep an active
  turn's phase/timer. Command failures are UI events, not thread tracebacks;
  Escape cancels owned asynchronous command work. Completed skill follow-ups
  remain in the draft if cancellation raced with their result.
- Clear/compact/branch changes refresh Coding's visible context. Shrinking a
  scrolled durable transcript re-anchors the owned tail so Coding/Claw's editor
  remains visible after clearing. Native scrollback is never erased or replayed.
- While either application owns the terminal, standard root-console warning/error
  logging publishes immutable UI events. Messages are sanitized/redacted and
  capped at 4,096 characters; existing file/non-console handlers continue and
  logging configuration is restored on exit. This does not intercept arbitrary
  stdout/stderr writes or non-propagating extension-specific log handlers.
- Permission confirmation requires a shown action and context. Hidden, clipped,
  action-only previews and unpainted resize states cannot authorize a call.
  Space/modified Enter do not approve; repeated/release Enter is ignored when
  reported by the keyboard protocol. Escape still denies. Late decisions for a
  closed worker loop fail closed without crashing the UI or granting a rule.
- Claw fences outbound requests and inbound events to their connection generation.
  Socket acceptance is not readiness: ping/pong and any chunked history must
  finish before sending. Offline submissions stay editable; lost/uncertain
  requests are never automatically replayed on reconnect. Restored text is not
  proof that a tool's effects were undone: review files/results before resending.
- Claw's read/write tasks are canceled and awaited as a pair. Write failures,
  EOF and bounded handshake/drain timeouts reconnect; malformed event frames are
  ignored with UI feedback. Backoff caps without growing huge integers. I/O
  reports connection state via immutable events, never by mutating UI state.
- Claw gateway status requests retain an active turn's phase/timer. Reset/clear
  are rejected locally while busy. Pending control replies/errors release their
  own wait state, then resume queued messages without dropping them. Unscoped
  gateway rejections restore input; an inactive abort acknowledgement settles
  cancellation rather than waiting forever for a nonexistent terminal event.
- Coding owns its agent-turn async task, including pre-turn compaction. Both
  clients clean up owned workers on keyboard exit, EOF and exceptional terminal
  exits. Coding clears queued work, cancels the agent and closes approvals before
  disposal; terminal stop does not depend on successful disposal. Worker joins
  are bounded so an external blocking extension cannot indefinitely hold exit.
- The focused shared editor uses the terminal's hardware cursor, hidden while
  repainting and when a permission/details panel takes focus. Unfocused editors
  do not paint a synthetic cursor. Cursor visibility changes produce one frame;
  unchanged frames still emit no bytes. Every painted row resets SGR styling,
  and terminal exit resets styles and ends synchronized output.
- Overlay lifecycle uses the same focus owner for keyboard, paste and cursor
  rendering. Hide/pop, temporary visibility changes and out-of-order removal
  restore the base target or the top visible capturing dialog. Compositing uses
  display-cell/grapheme boundaries, clamps tiny-terminal geometry, and treats
  covered transcript rows as transient so dialog text does not enter scrollback.
- Both UI event consumers yield after 64 events or 8 ms per tick, retaining
  FIFO ordering and immutable payloads. The time budget is checked between
  handlers, not inside a handler; a single expensive callback can exceed it.
- Ctrl+L forces a repaint of the visible owned rows, including while a panel
  is focused. It never clears the screen/scrollback, submits input, or resolves
  permissions. Coding's double-Ctrl+C window uses monotonic time.

- Shared Unicode editor and inline layout. Details and permission panels appear below the editor. Permission, autocomplete,
  recent details and queue share a bounded area. Both clients show contextual
  shortcut hints when height permits. Spare height adds a blank row before status.
- Permission summaries scroll with Page Up/Page Down. Up/Down selects an action;
  the selected action remains visible when space permits. Escape denies that call.
  At one terminal row only the editor fits; at two rows an action-only preview
  can fit but cannot confirm. Enlarge the terminal to view context and approve.
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

## Reference patterns

This work borrows interaction principles rather than copying a complete CLI.
The [Claude Code interactive reference](https://code.claude.com/docs/en/interactive-mode)
was checked on 2026-10-04: recovery redraw preserves the conversation/draft, and
Escape belongs to the active dialog before interrupting background work. The
existing Pi-derived main-buffer component architecture remains in place, but
focus, cursor and row ownership are tested here rather than assumed from that
heritage. XDOG keeps its existing Ctrl+Enter newline convention and fail-closed
approval policy; no external shortcut or permission policy is imported wholesale.

## Verification

Regression tests under `packages/tui/tests`, Coding's `test_compact_layout.py`,
and Claw's `test_inline_behavior.py` cover input, Unicode cursor boundaries,
owned-row painting, panel priority and delayed events. Claw's
`test_tui_transport.py` covers connection generations, write/read cancellation,
blocked drain, long backoff, handshake/history readiness, command/run interleaving,
and a real Unix socket reconnect without replaying uncertain input. Gateway
validation failures retain run IDs so they cannot be confused with an in-flight
status query. `test_interactive_commands.py` covers command/session exclusion,
cancellation races, error reporting, and context replay. Permission tests cover
closed-loop decisions, hidden/truncated actions and confirmation during resize.
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
queue/cancel recovery, tool details, recovery repaint and late-event suppression.
Additional Claw cases disconnect a busy controlled gateway, preserve active/queued
text plus the live draft through reconnect, explicitly resubmit, and exit during
an offline retry. Command cancellation/logging and clearing long Coding/Claw
transcripts verify draft/cursor recovery and preserved native history. No real
gateway daemon is restarted. They capture
`terminal.raw`, `pane.txt`, `visible-pane.txt`, `cursor-observations.txt`, and
`termios-result.txt` in pytest's temporary test directories. Assertions use the
current visible pane, actual cursor coordinates and cursor visibility; resize checks wait for a new
cursor query and completed repaint. They check terminal-mode restoration and
absence of screen/scrollback clears or alternate-screen entry. Tests skip
explicitly without POSIX or tmux.

Runtime evidence is limited to the tested Linux/tmux profile. Actual IME popup
positioning, desktop terminal reflow differences and Windows console behavior
still need platform-specific manual checks. A green suite does not establish
that those environments were tested.

## Verification snapshot — 2026-10-04

- Workspace pytest: **2,286 passed, 7 skipped**.
- TUI/Coding/Claw pytest: **650 passed**, including actual private-tmux clients.
- Ruff across `packages`: clean. Strict mypy across all seven namespaces:
  **329 source files**, no errors (only unused-override notes).
- Shipped flow examples with `--allow-script-stub`: **17 passed**.
- Existing AI-package files and `uv.lock` were fingerprinted around final
  verification and remained unchanged. No commit or live gateway restart was made.

An interleaved offline comparison of the HEAD renderer/cursor implementation and
these changes used the same host, 1,000 messages and 150 frames per run, in
baseline/current/current/baseline order. Streaming medians were **14.81/14.80 ms**;
resize medians **246.28/246.81 ms**. Both emitted **zero bytes on unchanged frames**.
These are component composition plus ANSI-generation measurements, not transport
or desktop latency. They show essentially unchanged renderer cost on this host;
no speedup or cross-platform timing guarantee is claimed. The standard 300-frame
benchmark remains `packages/coding/tests/fixtures/benchmark_tui.py`.

## Historical stability-phase exclusions

These exclusions describe the earlier stability phase, not the expanded active
goal. See `agent-ui-gap-plan.md` for current scope and per-feature status.

Remote host/client protocols, controller leases, serve/connect commands, and a
fullscreen application viewport are outside this plan. Full extension/theme
ecosystems, images/diagrams and full-history search/selection remain separate
projects. Basic theme presets, saved transcript replay and renderer benchmarking
are implemented as documented in `tui-usability-plan.md`. Existing helpers are left intact
and must not be described as finished application features.
