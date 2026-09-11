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

Permission prompts take priority. Up/Down chooses an action, Enter confirms,
Escape denies that call, and Page Up/Page Down scrolls the command summary.
The editor draft stays intact when panels open or the terminal resizes.
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
