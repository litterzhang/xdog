# Shared auxiliary-panel styling

Coding and Claw pass their existing main-UI style callbacks into a small immutable
`xdog.tui.components.panel.PanelTheme`. TUI owns no application palette and imports
neither host. A standalone TUI component stays plain unless its caller supplies
styles; installing/importing a package starts no worker or runtime.

## Appearance

- Headings use the host's main heading/accent style.
- Current rows use its bold accent, rather than an unstyled white/black selector.
- Hints and metadata use its muted style.
- Known active/success/failure states use accent/green/red respectively. These are
  display cues, never execution receipts or tool authorization.
- Transcript cursors/search hits/marked ranges have explicit theme styles. The
  source records, selection offsets, copied text and exported bytes are unchanged.

Coding's adapter reuses its resolved native/dark/light/plain and color-depth
settings, including the pre-start resume picker. Claw's adapter reuses its existing
main UI callbacks and supplies plain panel styles for `NO_COLOR`, `TERM=dumb` or
`XDOG_TUI_THEME=plain`; this does not introduce a new Claw-wide palette system.

## Propagation and safety

`InlineLayout` applies the host's panel theme when rendering the actual auxiliary
or existing fullscreen viewer. `ThemedPanel` forwards it to nested panels/forms,
including children created after opening a browser. Input form retheming changes
callbacks only; it does not replace drafts, cursor, focus, action or owner state.

Decorating rows adds no physical rows and consumes no visible columns. Existing
height/width budgets, review paging and confirmation gates remain unchanged.
Truncation precedes outer styling, and rows close any cropped inner ANSI styles.
This avoids leaking colors after narrow/CJK/emoji text or into the shell.

Untrusted strings are sanitized before host colors are added. In particular MCP
server/catalog/message fields cannot inject terminal control sequences via the
new style path. Image render payloads are not decorated: only the attachment
heading/fallback hint changes style. Styles never enter domain storage or source
records, and do not affect permission, cancellation, navigation or event routing.

## Coverage

The same helpers style navigation/history, transcript/tool inventory, details,
child roster/input/draft recovery, primary draft recovery, MCP/OAuth, local output
review, basic extension dialogs and attachment chrome. Existing tool-permission
prompts already receive their host theme and retain that explicit rendering path.

Tests cover semantic callback reuse, late/nested/fullscreen propagation, actual
Coding/Claw host bindings, native/dark/light/plain and `NO_COLOR`, small widths,
Unicode and reset closure, raw MCP escape rejection and immutable source data.
An owned real tmux test also checks the actual Coding session/transcript heading
RGB attributes rather than only searching component strings for escape codes.
