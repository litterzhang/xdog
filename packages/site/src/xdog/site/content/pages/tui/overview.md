---
title: Overview
---

*Shared terminal rendering and input components, not an application runtime.*

`xdog-tui` is a Linux-only Python 3.12+ library. Components return ANSI string
rows; the engine differentially paints owned rows in the main terminal buffer
while preserving native scrollback. The default is not an alternate-screen app,
but explicit temporary fullscreen/alternate-screen surfaces are supported.

Coding and Claw share the rendering/input primitives while retaining their own
approval, session, queue, MCP and child policies. TUI does not import an Agent
runtime, call models or activate capabilities/skills.

## Highlights

- Main-buffer rendering, Unicode-aware clipping and focused cursor markers
- VT100/xterm and Kitty key parsing, remappable prompt actions and atomic paste
- Editors, autocomplete, fuzzy matching, undo, history and keybindings
- Shared permission/details/navigation/transcript and child presentation panels
- Capability-dependent Kitty/iTerm2 terminal images, not a native-platform promise

## Try it

There is no `xdog-tui` CLI. Install `pip install xdog-tui` (or `uv sync` in the
workspace) and import its library. Pure component rendering needs no terminal
session or network:

```python
from xdog.tui import visible_width
from xdog.tui.components import Text

component = Text("Hello, 终端", padding_x=0, padding_y=0)
lines = component.render(20)
assert all(visible_width(line) <= 20 for line in lines)
print("\n".join(lines))
```

Use `xdog.tui`, not the old bare `tui` import. Starting the real `TUI` loop needs
an interactive terminal and host-owned shutdown; its only direct runtime
dependency is `wcwidth`. See [design](/packages/tui/design),
[reference](/packages/tui/reference) and the
[package README](https://github.com/litterzhang/xdog/blob/main/packages/tui/README.md).
