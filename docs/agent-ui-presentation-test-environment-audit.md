# UI-049 / UI-055 presentation-test environment repair

Checkpoint: October 11, 2026. This does not add a requirement or relax any UI,
permission, viewport, cancellation, or platform acceptance condition.

## Reproduced cause

The complete run started at18:15:44 in a non-login Bash wrapper. A read-only
inspection of that owned pytest process's selected environment fields showed
`TERM=dumb`, `NO_COLOR=1`. The earlier interactive command environment had
`TERM=xterm-256color`.

`PermissionPanel` deliberately uses ASCII borders for a dumb terminal or an
explicit `XDOG_TUI_ASCII=1` setting. The test
`test_permission_shows_all_options_and_moves_highlight` nevertheless removed only
the Unicode `│ ` prefix before counting its selected-arrow row. In a dumb terminal
it counted zero rows, although all three options and the selected row were present.

The unchanged exact test passed in the ordinary command environment and failed
under explicit `TERM=dumb NO_COLOR=1` at that exact arrow-count assertion:
`/tmp/ui-final-20261011/final/permission-targeted.log` and
`/tmp/ui-final-20261011/final/permission-dumb-red.log`.

A broader run under the same explicit environment identified two analogous test
assumptions: the light-theme test removed `NO_COLOR` but left `TERM=dumb`, and the
normal-height permission-box test assumed Unicode corners. The production theme
and permission panel correctly honored the terminal settings. These were not
repaired by weakening production safety or forcing the entire suite to ignore
plain-terminal behavior.

## Test-only correction

- Permission option/highlight coverage explicitly exercises Unicode, forced ASCII,
  and dumb-terminal presentation. All original option, selected-arrow, movement,
  summary, and 24-row-budget assertions remain; only the expected border is selected
  from the explicit input mode.
- The light-theme test exercises both a color-capable terminal and a dumb terminal,
  retaining the original color assertions for the capable case and checking the
  identity/no-color behavior in the dumb case.
- The permission-box test exercises all three border modes while preserving summary,
  option visibility and height assertions.
- The existing `NO_COLOR` test explicitly starts from a color-capable terminal so
  its no-color result cannot accidentally be explained only by `TERM=dumb`.

Changed files are only
`packages/coding/tests/test_compact_layout.py` and
`packages/coding/tests/test_tui_usability.py`. No production code, timeout, skip,
selection rule, confirmation guard, or fixture control was weakened. The corrected
scope passes **66 tests** under `TERM=dumb NO_COLOR=1`:
`/tmp/ui-final-20261011/permission-theme-presentation-final.log`.

An initial helper command referenced a nonexistent standalone TUI permission test
file, and an intermediate test edit missed the module-level pytest import. Those
collection errors are retained in the earlier logs; they are not product regressions
or passing evidence. The final import/formatting corrections pass targeted Ruff.

## Whole-run consequence

The running full-root job was not interrupted. Its imported test definitions remain
the old ones. The two test-file edits explicitly invalidate that run's frozen-source
acceptance, even though production source is unchanged. Its exact completed result
must remain in the record, and a new frozen complete run must pass. Updated static,
source-distribution and example receipts must correspond to the corrected test/source
snapshot; no partial run or sum of tests substitutes for the final full-root gate.
