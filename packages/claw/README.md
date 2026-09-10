# xdog-claw

**Agent orchestration runtime.**

Runs and supervises long-lived agent sessions: scheduling, a gateway, tool
memory, and persistence backed by SQLite.

## Interactive terminal

`uv run xdog-claw tui` connects to the configured gateway using the main terminal
buffer and native scrollback. It shares Coding's Unicode multiline editor and
bounded layout. Goals/todos summarize retained state; queued messages keep their
full text while only a compact preview appears.

Ctrl+O opens recent tool/reasoning details. Left/Right changes entries and Page
Up/Page Down scrolls. Escape closes autocomplete/details before cancelling a
run. Cancellation and busy rejection restore submitted and queued text ahead
of a newer draft; input submitted during cancellation stays editable. Ctrl+Z
suspends on POSIX. Remote coding and fullscreen application modes are excluded.
See [terminal verification](../../docs/tui-pi-parity.md) for tests and limitations.

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
