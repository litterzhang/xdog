# A short, focused test suite

Run from the repository root after `uv sync`:

```sh
uv run python scripts/test.py
```

CI uses this same command. The runner reads every `testpaths` entry from the
root `pyproject.toml`, starts up to four package jobs, and uses two pytest workers
inside each of the four larger packages. It avoids making each worker collect
unrelated packages. All package output and nonzero exits are reported.

The direct alternative, `uv run pytest -q`, still runs the entire retained suite
with six workers and small dynamically assigned batches; it is about 63 seconds
on the measured machine because of repeated full-repository collection.
No test paths, failures, or slow tests are silently deselected to meet the
one-minute feedback target. Existing optional-dependency/platform skips remain.
The target includes pytest startup and collection, but not environment installation.
Runtime depends on the machine and load; it is not a guarantee on every CI runner.

For debugging, disable worker processes or select the relevant package:

```sh
uv run pytest -n 0 packages/agent/tests/test_agent_loop.py -q
uv run pytest -n 0 packages/flow/tests -q
```

## What earns a test

Prefer a focused behavioral assertion that can catch a meaningful defect:

- provider parsing, routing, stream termination, and typed content;
- the agent loop, tool permissions, cancellation, ownership, and persistence;
- typed workflow validation/execution, checkpointing, and codegen parity;
- terminal rendering, protocol safety, and exceptional-exit cleanup;
- real worker/transport and application smoke tests at integration boundaries.

Do not add tests just for dataclass construction/defaults or standard-library
copy behavior when a behavioral test already exercises the same contract.
Avoid repeating the same full-stack worker/socket/terminal scenario for every
host, presentation, callback, delivery mode, and tiny timing variation.
Put exhaustive validation at the layer that owns it; keep a representative
integration probe instead of reproducing that matrix in each client.

## Deliberate coverage tradeoff

The collected suite shrank from **8,614 to 5,774 cases** (including the 67
optional/platform skips); some reductions consolidate checks rather than remove
their input values.

The pruning removes feature-by-feature native/RPC/physical-terminal matrices,
repeated worker projections, identical timing repetitions, and trivial model
construction checks, stress-sized fixture replicas, and duplicate desktop-service
projections. It keeps focused lower-level tests and a small set of
real integrations. Product-specific edge cases previously covered only by those
matrices are no longer exhaustively tested. This is a smaller suite, not a claim
of equivalent full-stack coverage.

The concurrent CI-investigation synchronization fixes and its deterministic
regressions were preserved. Shared painted-review helpers used by retained MCP
task terminal tests now live in `packages/coding/tests/fixtures/terminal_review.py`.

When a defect needs a regression, prefer a deterministic schedule or barrier
and the smallest failing boundary over duplicating another full product matrix.
Use `uv run pytest -q --durations=30` to watch the cost of new coverage.

## Failures found while validating the smaller suite

The retained tests exposed two synchronization issues: steering must wait for
an actual stream-start event, and Coding argument completion needs the same
frontend-settlement observation already used by Claw before Ctrl+C. These tests
were fixed with observed barriers, not deleted or retried.

A native credential-helper cleanup race could also let `stdin.close()` raise
`BrokenPipeError` after the helper was reaped, masking the safe public failure
and preventing the other pipe from closing. Cleanup now tolerates that secondary
OS error. A deterministic regression was demonstrated failing before the fix
and passing afterward; the original cancellation/ownership tests remain.

## Measured result

On the local machine with a prepared workspace environment, the full runner
completed all seven configured test paths with **5,707 passed and 67 skipped**:
**58.73 seconds wall time**, including runner/pytest startup and collection.
The one-minute target is for this full command, not a smoke-only selection.

Additional cost reductions retain the same checks: literal validation matrices
are grouped into fresh-scope per-value checks, dependency imports are scanned
once, and scheduler registry tests no longer provision another real uv environment
on every install. The dedicated provisioning test remains.
