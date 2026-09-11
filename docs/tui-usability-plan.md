# Coding TUI usability implementation

Approved in conversation on 2026-09-11. Preserve the main terminal buffer,
native scrollback, existing permissions, and unrelated AI changes. No gateway
restart or commit is required.

- [x] Explicit activity phases and monotonic elapsed time; terminal cleanup.
- [x] Details focus, visible empty state, `/details`, `/status`, navigation and
      paused streaming follow; reasoning opens at the beginning.
- [x] Details and permission panels below the editor, bounded row allocation and
      contextual keyboard hints; preserve drafts and small-terminal behavior.
- [x] Compact priority-based metadata and clearer permission panel.
- [x] Terminal-native/light/no-color theme options and stable tool summaries.
- [x] Regression tests, actual tmux acceptance, long-history measurement,
      and documentation of platform-specific manual acceptance.

Full-history selection and remote/fullscreen modes remain outside this change.
Actual IME and desktop-terminal interception require manual checks on those
platforms; automated Linux/tmux coverage must not be presented as proving them.

## Validation

Regressions cover details commands, empty feedback, focus/draft recovery,
paused log following, new entries, monotonic status phases, metadata priority,
plain/light themes, tool duration and permission summary position. Actual tmux
acceptance covers `/details`, Ctrl+O and cursor restoration alongside resize,
permissions, cancellation and Claw socket behavior.

A local warm-render measurement of 1,000 messages / 3,000 rows over 20 repetitions
gave median 0.57 ms and maximum 0.63 ms. This measures component rendering only,
not terminal latency, and did not justify a new history cache.

## Manual platform acceptance (not executed)

- Desktop terminal / VS Code: Ctrl+O reaches the application; `/details` works
  as an alternative; light/dark contrast and text selection remain readable.
- Chinese IME: composition follows the cursor after resize, panel transitions,
  and multiline paste.
- SSH / tmux: fragmented keys, narrow widths and resize during streaming.
- Windows profiles: key encoding, Unicode width and bracketed paste.

Final automated validation: 430 TUI/Coding/Claw tests passed; Ruff passed;
strict mypy passed for 164 source files. Manual platform checks remain open.

## Follow-up implementation

- [x] Replace independent UI busy/permission/phase flags with immutable
  `RunStatus` transitions. Worker synchronization stays separate. Test delayed
  progress, cancellation, permission resume and parallel tool completion.
- [x] Complete the permission box with width/height-aware fallback.
- [x] Use two compact tool text rows while retaining full output and rich images.
- [x] Add 16/256/truecolor negotiation and contrast checks for light semantic colors.
- [x] Add sustained-output and resize acceptance plus a repeatable renderer benchmark.

The previous warm-component timing is not an end-to-end performance result.
The broader benchmark is reproducible with:

```bash
.venv/bin/python packages/coding/tests/fixtures/benchmark_tui.py
uv run pytest packages/coding/tests/test_terminal_acceptance.py -q
```

Local 1,000-message / 300-frame measurements (component composition and ANSI
generation, including growing tool output): stream median 4.49 ms, P95 7.32 ms;
resize median 108.14 ms, P95 110.88 ms; unchanged frame output 0 bytes. Median
stream output was 298 bytes. Resize invalidates historical wrapping, so its
cost is materially larger than warm rendering. No hard timing limits are used
in CI because host load and terminal implementations differ.

The actual tmux sustained-output test approved a local fixture command, resized
four times, and verified live details plus draft/cursor recovery. One local run
measured draft visibility at 8.5 ms and resize visibility at 36–247 ms, with
27,433 captured output bytes. These timings include tmux polling and cursor
position negotiation, and are saved in the test's `latency.json` artifact.

Manual IME, Windows, VS Code and the user's Ctrl+O interception remain unverified.
A full-history entry picker remains a separate feature, as originally deferred.

Follow-up validation: 440 TUI/Coding/Claw tests passed; Ruff passed; strict mypy
passed for 165 source files. Cancellation rendering depends on UI state, not
the worker's cancellation flag, including the window before a terminal event
is consumed.

## Resume and input bug fixes

- User text serialized as content blocks now replays in the transcript and editor history.
- The footer shows used tokens / prompt capacity even at zero usage, and reserves
  space for the model when activity text is long. Saved model and cache usage are
  covered by an actual disk save/resume regression.
- Shared details render below the editor in Coding and Claw; permission policy is unchanged.
- Shared Ctrl+Enter handling inserts a newline for Kitty and modifyOtherKeys encodings.
  Desktop interception/maximization still requires terminal-specific configuration.

Validation after these fixes: 438 component/client tests and six actual tmux
tests passed (the new resume case was rerun after fixing test synchronization).
Ruff and strict mypy for 165 source files passed. The actual resume test checks
user/assistant replay, model, cached context usage, recalled input and terminal
cleanup across process restart. Windows Terminal over SSH is the user's setup;
its Ctrl+Enter forwarding and local shortcut bindings have not been verified.

Message presentation follow-up: remove user-message vertical padding and avoid
rendering empty styled reasoning as a blank row in Coding and Claw. Preserve one
separator between ordinary messages. Coding user text has a subtle background in
native/dark/light themes; plain mode remains uncolored. The input hint now presents
Ctrl+Enter as the primary newline shortcut, retaining alternate key compatibility.

## Claw parity follow-up

- [x] Add height-aware Ctrl+Enter and detail-navigation hints to Claw.
- [x] Use monotonic time for elapsed status and double-Ctrl+C timing. Preserve
  elapsed time through waiting/streaming/running transitions; reset after idle.
- [x] Correct the stability document's stale layout and deferred-feature descriptions.

Manual Windows Terminal/SSH and IME acceptance remains outstanding. Full-history
search/selection remains explicitly deferred, and remote/fullscreen remains excluded.

## Transcript readability and row integrity

- Compact Coding tool headers normalize whitespace; multiline shell/Python commands
  use a script label and retain the full sanitized, redacted command in details.
  Embedded newlines must not escape a component's rendered row array.
- Tool result text precedes hidden-line metadata. Long-to-short streaming output
  and final-answer insertion are covered by terminal-cell tests for stale output
  and transient status leakage.
- Coding and Claw show readable compact Thinking text, skipping standalone Markdown
  headings when a body is available. Compact presentation is bounded to two body
  rows; complete reasoning remains in details and saved transcripts.
- Actual tmux streaming acceptance uses a multiline Python command and terminal
  resizing. This does not replace Windows Terminal/SSH manual acceptance.

## Shared retained message architecture

Approved in conversation: each message owns presentation, Transcript owns order
and spacing, InlineLayout owns region placement, and the renderer owns terminal
updates. Coding and Claw keep their existing permissions and event adapters.

- [x] Add shared ThinkingMessage and NormalMessage components. An assistant update
  handle retains both identities while the Transcript stores them as separate siblings.
- [x] Share user-message styling and compact tool-message rendering. Tool output
  uses a branch marker, bounded preview and full retained output for details.
- [x] Centralize boundary whitespace in Transcript: one blank row between visible
  messages; empty placeholders occupy no rows; internal paragraphs stay intact.
- [x] Preserve application-owned stream/tool ID maps, cancellation, history replay,
  and full detail discovery after splitting the component tree.
- [x] Test independent component identities, update order, spacing and bounded rows;
  verify both actual client TUIs with the existing isolated terminal fixtures.

Tool execution/state and rich-result adapters remain application-owned; arbitrary
shell output is not interpreted as authoritative file-edit counts. Remote/fullscreen
modes and a full-history picker remain outside this refactor.

Validation: 457 TUI/Coding/Claw tests passed, including isolated tmux acceptance;
Ruff and strict mypy passed for 167 source files. Transcript spacing preserves
image-protocol height reservations as well as internal paragraph breaks.

## Standalone panels

- Extract `PermissionPanel`, `DetailsPanel`, `InputPanel` and `StatusLine` into
  independent modules exported from `xdog.tui.components`.
- Keep Coding permission choices/decisions and metadata assembly in thin adapters.
  Claw uses the shared input, details and status components without a permission gate.
- Preserve `PromptEditor` and `BoundedDetails` imports as compatibility aliases.
- Panels own rendering, row budgets and local input handling. Applications retain
  run lifecycle, focus routing and permission policy; InlineLayout owns placement.
- Details still open below input at the first line; End explicitly enables following.
