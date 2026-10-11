# Argument-completion fixture teardown settlement

Date: 2026-10-11. Requirement IDs: **UI-049, UI-054, UI-055, UI-064**;
source-specific static/artifact gate **UI-056** is revalidated after the edit.
This is not a new feature requirement.

## Completed frozen run and exact failure

`/tmp/ui-final-20261011/settled-final/pytest.xml` and `pytest.log` record
**8,199 passed, one failed, zero skipped**, 8,200 collected cases, root exit 1.
All 1,427 scoped source/test/fixture/config files stayed unchanged. The run
completed normally and was not interrupted. All 33 previously default-skipped
opt-in cases ran and passed. It is a failed gate, not final acceptance.

The only failure was
`test_argument_completion_ui.py::test_actual_both_client_argument_editor_selects_without_submission_or_side_effects[select-coding]`.
Selection, literal-token replacement, undo, no-command-execution and no-model-call
assertions had all passed. Cleanup at line 193 called `session.extensions.aclose()`;
`McpManager.aclose()` then attempted `call_soon_threadsafe(loop.stop)` on an already
closed loop.

Final Undo can launch a new completion. `_request_exit()` correctly delegates
session disposal to the final original completion thread. This direct test fixture,
which does not enter the native `InteractiveMode.run` terminal lifetime, then
concurrently closes the same extension manager. The native production teardown
already joins its original completion threads; the test omitted that barrier.

## Reproduction and minimal repair

The isolated original-node trace in
`/tmp/argument-ui-family/original-delay-2.{log,json}` demonstrates overlapping
fixture manager close and native completion-thread disposal with a 30ms completion
scheduling delay. The completed root traceback independently matches this exact
failure, not merely its test name.

The six-line **test-only** change snapshots original `_argument_threads` under its
lock after `_request_exit()`, joins them asynchronously using the native five-second
join bound, and asserts they have stopped **before** direct fixture resource close.
It preserves cancellation, sole native disposal ownership, all existing assertions
and deadlines. No production, permission, protocol, skip or retry behavior changes.
Original/proposed hashes and exact patch are in
`/tmp/argument-ui-family/source-proof.json` and `proposed-test-only.patch`.

The private staged six-file family passed 62 cases. All eight UI variants passed
twice with delayed completions (`joined-delayed.log`, `joined-delayed-final.log`).
The initial `delayed-ui.log` contained an invalid async-test cloning experiment;
its six cloning errors are excluded from product evidence, not silently called
product regressions. Two other original-node delay repetitions passed; the race
is not represented as deterministically failing on every schedule.

Main applied the reviewed patch only after the root completed and its traceback
matched. Current repository verification is recorded separately in
`/tmp/argument-ui-family/integrated-six-files.log`. The earlier command-error log
`integrated-family.log` used a misspelled test filename and ran zero tests; it is
not acceptance evidence.

## Remaining final gate

The saved named matrix joins the existing root XML directly: 14 rows, 360 modules,
no missing modules/test bases; only L1/S2 share this same failure. No additional
matrix run or scope expansion is needed. A new unchanged-source passing root plus
refreshed source-dependent preflight remains required. Private or targeted passing
results do not replace it. Five external-environment requirements remain unfinished.

For the next run only, a separately self-tested read-only pytest observer in
`/tmp/ui-final-runner/ui_live_failure.py` immediately saves failed report tracebacks.
It neither changes collection nor retries/changes outcomes, avoiding another wait
until the end of the whole suite just to see a failure stack. No dependency was
installed, no service restarted, and no other workspace changes were overwritten.

## Final acceptance — 2026-10-11 22:38:48 +08

The integrated six-file family passed **62 cases in14.83s**. The subsequent full
`teardown-final` root passed **all8200 cases, no errors/failures/skips**, with
pytest/source-check/preflight exits0 and all1427 scoped members/hashes unchanged.
All14 saved Linux terminal/safety rows and their modules/test bases passed in that
same invocation. Main independently verified XML, source and row receipts and
closed UI-049/054/055/064. No further matrix run was necessary. Exact final outcome:
`agent-ui-teardown-final-skips-2026-10-11.json`. Overall goal still has five required
external-environment items; this local acceptance does not waive them.
