# xdog-coding

**Interactive coding-agent CLI.**

A terminal coding agent with session management, built on
[`xdog-agent`](https://pypi.org/project/xdog-agent/) and the
[`xdog-tui`](https://pypi.org/project/xdog-tui/) rendering layer.

```bash
uv run xdog-coding
```

## Interactive terminal

The interface uses native scrollback, one status row, and a shared multiline
editor. Enter submits or queues; Shift/Alt+Enter inserts a newline. Ctrl+O opens
recent tool/reasoning details: Left/Right chooses an entry and Page Up/Page Down
scrolls. Escape closes autocomplete/details first, then cancels active work and
restores queued text ahead of the current draft. Ctrl+Z suspends on POSIX.

Permission prompts take priority. Up/Down chooses an action, Enter confirms,
Escape denies that call, and Page Up/Page Down scrolls the command summary.
The editor draft stays intact when panels open or the terminal resizes.
See [terminal verification](../../docs/tui-pi-parity.md); remote coding and
fullscreen application modes are excluded.

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
