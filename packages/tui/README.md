# xdog-tui

**Terminal UI library with differential rendering.**

A small terminal UI toolkit that redraws owned rows in the main terminal buffer
while preserving native scrollback. No curses dependency.

`PromptEditor` supplies shared Unicode editing, undo/redo, history and atomic
paste. `InlineLayout` budgets the editor and prioritized auxiliary panels from
terminal height. The renderer uses cursor reports at startup, resize and resume.

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
