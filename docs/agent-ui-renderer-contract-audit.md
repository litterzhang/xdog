# UI-037 — finite renderer/container public contract

Checkpoint: October 11, 2026. This subordinate audit reconciles UI-037; it does not
create another requirement or edit the central ledger. The historical “arbitrary /
richer rendering remains OPEN” clauses in passes 57, 77, 86, 95 and 97 are mapped
below to named, composable contracts. No unbounded canvas, host-executed extension
component, direct terminal escape API or arbitrary third-party renderer parity is
claimed. Non-Linux native validation is deferred, not inferred from Linux.

## Public contract and existing implementation

| Contract | Actual public surface and ownership | Verification |
| --- | --- | --- |
| Native component | `Component.render(width)`, `set_height(height)` hint, `invalidate()`, `handle_input()` / `handle_paste()`; optional focused cursor marker. A component owns its state/cache; height is a hint, not universal clipping. | Dedicated `test_renderer_contracts.py`, existing TUI component/render/lifecycle tests |
| Native container | `xdog.tui.Container` and `components.container.Container` are the same vertical concatenator. Add/remove/clear and recursive `invalidate()` already exist. It is not an automatic bounded height allocator or input/focus manager. | Dedicated nested cache invalidation/alias/removal test; actual worker fixture wraps a cached component in `Container` |
| Worker frame | `api.custom(title, render, on_input, ...)` runs callbacks in the owned worker. `custom_frame(layout, width, height)` emits only sanitized `lines` and one optional cell-coordinate `cursor`. Callbacks, components and private markers do not cross to the host. | Agent custom codec/broker tests; both actual native host/worker suites; new dedicated host matrix |
| Composition | Frozen `CustomText`, `CustomSpacer`, `CustomChild`, `CustomRow`, `CustomColumn`, `CustomPadding`, `CustomBox`, `CustomComponent`; fixed/weighted allocation and explicit alignment/wrapping. | `test_custom_layout.py`, new padding/wrapping/cache tests, existing real structured worker paths |
| Scroll canvas | `CustomViewport` owns bounded virtual dimensions and worker-managed cell/row offsets, clamps on resize, preserves styled whole glyphs, hides offscreen cursors. It is not unbounded lazy paging. | Existing viewport limits, styled cropping and cursor tests; actual Coding/Claw parent/child scroll/resize; Linux structured-scroll terminals |
| In-view layers | `CustomStack` / `CustomLayer` are opaque worker rectangles, not host overlays. They blank their footprint, translate/occlude one cursor, reject conflicting visible cursors, and skip invisible component rendering. | Existing layer/cursor/limits tests; both real parent/child structured popup paths; Linux popup/resize terminals |
| Unicode/cursor | Practical supported grapheme families: combining marks, variation selectors, emoji modifiers, ZWJ sequences, keycaps, paired regional indicators. Styles are zero-width even inside a cluster. Adapted worker cursor markers inside a cluster round left before container/viewport translation. No claim of complete Unicode segmentation conformance. | Dedicated regressions, new actual parent/child wire-and-painted-cursor matrix, existing physical Linux cursor checks |
| Cache invalidation vs repaint | Worker state/cache invalidation remains explicit (`component.invalidate()` / recursive `Container.invalidate()`); `api.request_render()` schedules an invocation-owned coalesced repaint. It does not invalidate arbitrary component caches, execute completion, or confer focus. Host `ExtensionCustom.invalidate()` independently revokes old geometry/painted receipts. | Dedicated actual worker invalidation + nested/resize tests; Agent custom-refresh and both native live-repaint tests |
| Nested lifetime | Original worker/view/lease identity controls input and completion. Parent components survive nested questions, but old focus/geometry evidence does not. Cancellation and worker teardown remain broker/host-owned. | Both native custom/nested suites and actual new nested cache recovery cases |
| Host presentation | Inline, temporary fullscreen and positioned host overlays use `ExtensionCustomPresentation`, `InlineLayout`, and owned `TUI` overlay handles. Live hide/show/focus/unfocus is separately negotiated via existing `api.request_overlay`; input authority stays with the host. | Both real parent/child custom/live-overlay suites; Linux inline/fullscreen/overlay, background repaint and ordinary parent editing tests |
| Limits/failure | Validate before rendering, including hidden nodes: 256 nodes, depth 12, 32 children/layers, 128 KiB source/frame, 200 host rows, 8,192 bytes/output line; bounded virtual canvases and retention. Zero-size content does not invoke component callbacks. | Existing node/cycle/coercion/text/component/canvas/cursor tests plus zero-size cache test |

Native components should author cursor markers at display-grapheme boundaries; the
worker adapter additionally normalizes malformed in-grapheme marker positions.
The bounded helpers are public from `xdog.tui.components.custom_layout`; they do
not need root-level re-exports to be public. Agent still has no TUI dependency.
No additional public renderer/container primitive or Agent API is necessary for
this finite contract. Applications retain permission policy, protocol negotiation,
input admission, original completion ownership and terminal lifecycle duties.

## Concrete remaining defects found and fixed

1. **Long-word wrapping split glyphs.** `wrap_text_with_ansi()` used character-wise
   long-word breaking: two ZWJ emoji at width two became four partial rows; flags
   and keycaps were also split/lost. The common renderer now segments whole
   graphemes across ANSI runs. Oversized glyphs occupy a blank row without an extra
   leading empty row. ASCII word boundaries and active style propagation remain.
2. **Native clipping/slicing split styled graphemes.** The native overlay path's
   `slice_by_column()` and `truncate_to_width()` segmented individual style runs,
   unlike the existing custom canvas cropper. They now share ANSI-transparent
   grapheme segmentation, preserve styles from fully cropped glyphs, and retain
   boundary cursor-marker ownership. Exactly fitting Unicode/ANSI ellipses are no
   longer sliced by Python codepoint count.
3. **Adapted cursor measured a partial prefix.** `CustomComponent` previously
   measured text before the marker as an independent glyph string. A marker
   between a base and combining/ZWJ/keycap continuation could land after the glyph
   and remain falsely visible when horizontally scrolled. It now resolves a
   source boundary against the whole sanitized line before translating the cursor.
4. **Right padding was omitted from emitted rows.** `CustomPadding` reserved right
   space during allocation but omitted those cells on content rows. All four
   padding sides now occupy the bounded output rectangle.

The production edits are limited to `packages/tui/src/xdog/tui/utils.py` and
`packages/tui/src/xdog/tui/components/custom_layout.py`. The pre-existing dirty
implementations were inspected and preserved; `/tmp/ui037/*.before.py` records the
starting contents of those two files. No resets, installs, service changes,
central-ledger edits, excluded navigation/API edits, or Coding/Claw application
edits were made.

## Dedicated and existing evidence

New files:

- `packages/tui/tests/test_renderer_contracts.py`: renderer edge cases and public
  Container alias/cache/height ownership contract; **32 tests**.
- `packages/coding/tests/test_renderer_contracts_ui.py`: **12 actual host/worker
  cases**, Coding/Claw × parent/child × inline/fullscreen/overlay. A temporary owned
  plugin exercises styled long-word wrapping, in-grapheme marker normalization,
  explicit nested-container cache invalidation, nested question return, fresh
  narrow resize, parent draft retention and exact offline model-call counts. These
  test actual brokers/workers/hosts, not a direct helper masquerading as a host.

Red/green evidence: `/tmp/ui037/red.log` (19 failures before fixes),
`ellipsis-red.log` (3 failures), `padding-red.log` (1 failure), `layout-green.log`
(74 passed; dedicated plus existing layout). The original TUI regression ran
938 tests before the five additional final cases. Actual existing broker/native
host lifecycle matrix: **193 passed** in 176.58s (`hosts-lifecycle.log`). New host
matrix: **12 passed** in 32.00s (`renderer-host-edges.log`).

Actual Linux physical terminal matrix: **16 passed, 225 deselected**, no skips,
90.80s (`linux-terminal.log`). It selects these existing parametrized tests from
`packages/coding/tests/test_terminal_acceptance.py`:

- `test_actual_structured_layout_edit_nested_resize_and_terminal_owner`
  (Coding/Claw × inline/fullscreen/overlay × static/scroll).
- `test_actual_live_custom_background_repaint_without_keys_and_native_recovery`.
- `test_actual_live_overlay_parent_edit_background_controls_and_native_recovery`.

These use isolated test-owned tmux terminals/offline workers, not user services.
They check native main-buffer/termios recovery; the broader renderer evidence is
not merely a screenshot/helper result. New dedicated host cases cover both child
owners too; no claim is made that every Cartesian combination was physically
painted on every operating system.

### Final root-config verification

Run from repository root with `uv run --no-sync` (existing environment; no sync or
installation). Final aggregate command includes all `packages/tui/tests`, the new
host file, both native `test_extension_custom_ui.py` / `test_extension_nested_ui.py`
files, Agent custom/custom-refresh/live-overlay files, and the three exact Linux
terminal test nodes listed above. Result is recorded in
`/tmp/ui037/final-regression.log`; renderer/test source hashes and exit code are in
`final-source-{before,after}.log`. No whole-workspace frozen-source assertion is
made while other owners work concurrently.

Final aggregate: **1,174 passed, 1 failed**, 330.51s, **zero skips**. The four
renderer/test source hashes matched exactly before/after. The failure was the
existing Claw test
`test_extension_nested_ui.py::test_claw_nested_stack_connection_leases_restore_same_parent_and_draft[-False]`,
line 115: after restoring the parent and sending Enter, `_extension_pending` did
not clear before its eight-second deadline. The new renderer cases and all Linux
terminal cases passed in this aggregate. **Do not describe this aggregate as green.**

Bounded follow-up without source changes: exact failing case plus the Claw nested
file **8 passed** in 15.56s (`nested-rerun.log`); a temporary diagnostic wrapper
recording frame acceptance/request/generation state ran the exact parent case
**12 times, all passed** in 21.08s (`nested-diagnostic.log`). The earlier 193-case
native/Agent lifecycle group had also passed. Non-reproduction is not proof the
timeout is resolved. The Claw owner reports no nested completion/fresh-frame logic
changes, and was sent the precise failure for final integration triage.

Final changed-file Ruff is clean; root-config strict TUI mypy reports **111 source
files, no errors** (unused-override notes only); diff whitespace check clean:
`/tmp/ui037/final-static.log`.

## Handoff recommendation and remaining needs

**Recommend accepting UI-037's named bounded renderer/container contract as
implemented and verified**, replacing the historical unspecified “arbitrary
renderer” OPEN clause with the matrix above. No additional renderer primitive or
Coding/Claw/Agent production patch is requested for that contract. The dedicated
actual-host tests cover both parent/child owners, not helpers alone; current Linux
native terminal validation ran and passed. Central status remains the main
owner's decision; this audit does not edit it.

**Retain the observed Claw nested-completion timeout as an explicit final
integration follow-up.** It must not disappear into an all-green summary or be
called fixed merely because the 8-case and 12-repeat reruns passed. Main/Claw
should triage/recheck it during the final native/lifecycle gate; reopen the named
nested lifecycle portion of UI-037 if a real contract regression is demonstrated.
The present evidence establishes no missing renderer/container API, and adding
one would not address that timeout. No stale-frame/idle/approval guard was weakened
or test timeout increased. Non-Linux native execution remains deferred.

### Follow-up: nested timeout cause identified during UI-064 triage

The Claw owner subsequently reproduced the timeout with 2 ms transport latency
(13 passing cases / one failure) and traced the causal sequence in
`/tmp/ui064/trace-4-0.002.json`, sequence ranges 79–84 and 99–122. A rejected old
resize (generation 8) produced a negative ACK without generation correlation.
When that ACK arrived at frontend generation 12 it cleared the **current** request
marker, causing a duplicate generation-12 resize. That resize advanced the broker
revision ahead of Enter's painted revision; the broker correctly refused the
completion and published `review_required`.

This replaces the earlier “cause not reproduced” assessment, not the test outcome.
The proposed correction is a generation-correlated gateway ACK and frontend
handling that lets an obsolete negative ACK settle its own inflight accounting
without invalidating the current request/paint evidence. Current-generation
negative ACK behavior and generationless older-peer behavior need explicit tests.
The broker's current-frame/completion checks must remain intact. At this receipt,
the fix is pending main's coordinated source-freeze invalidation and deterministic
red→green verification; **no code/test change or resolved claim was made by this
renderer/context worker**.
