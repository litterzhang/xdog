# Retired extension ownership and actual close settlement

Date: 2026-10-19. Milestone through977eabd. This is not final acceptance of the
assistant-core objective or proof of rebuilt final wheels.

## Findings and changes

1. GroupRuntime kept every disposed ExtensionRuntime in a strong list until
   application shutdown. Actual three context rotations, ordinary Agent turns and
   live successor reproduced retention even after the old extensions had really
   closed; both installed/absent Goal cases failed. A weak registry now retains
   shutdown discoverability without owning settled conversations indefinitely.
   This does not prune child records, add a retention quota or start another
   service. Existing closing worker threads/callbacks own their actual lifetime;
   shutdown takes a strong snapshot of still-live owners before awaiting them.
2. ExtensionRuntime used cancellable, timed thread joins. A real stdio extension
   persisted through the actual TranscriptStore; its atomic write had really
   fsynced/replaced before a barrier held memory publication. Repeated cancellation
   returned from close while the real persistence worker/owner thread remained
   alive. Both active and retired cases failed. MCP close now fences admission
   under the existing startup lock, starts one threadsafe close operation and
   joins its actual thread via `wait_owned`, without an arbitrary join deadline.
   ExtensionRuntime delegates to that generic lifecycle rather than inspecting
   its manager's private closed/thread fields.
3. After actual worker reaping, an injected cleanup-boundary error was lost by
   the prior synchronous-close/async-join path. A separate regression against the
   restored original source failed `DID NOT RAISE`. Async close now reads the
   original close result after thread settlement; repeating close reports the
   same error without replaying cleanup. The test wraps and calls the real stdio
   close before raising, never substitutes a fake worker/close.

Tool/transport RPC deadlines, permissions, validation guards and business
lifetimes remain unchanged. This removes join deadlines, not network/process
termination deadlines or tool authorization. Goal state and notification policy
are untouched. Unknown work is not restarted to obtain a cleaner close result.

## Evidence

`packages/claw/tests/test_retired_extensions.py` has five cases:

- Real repeated ordinary context rotation, Goal absent and installed, no model
  work from extension reads/retirement; closed generic owners become collectable
  before application shutdown after a live successor takes ownership.
- Actual contextual Python stdio worker, actual sidecar atomic write, real join
  delegated/observed (not mocked away), active and retired owner cancellation.
  Held IO keeps close pending despite repeated cancellation; release settles the
  actual thread and preserves exactly one durable entry with no model calls.
- Actual reaped stdio worker plus late cleanup error, repeated close returns the
  original error and only one real cleanup occurred.

The first error test's cleanup assumed a MemoryManager existed; that setup error
was corrected before recording the behavioral RED. Original and corrected logs
are retained, not conflated. Actual corrected retention RED2, close cancellation
RED2 and lost-error RED1 all become green. Focused existing+new ownership,
Goal display, active tool, MCP stdio and input request suites: **75 passed,11.05s**,
`claw-extension-close-final-focused.log` under `/data/t.Vy5SaQ/`.

Fresh unfiltered nine paths: **6876 passed,34 skipped**,171.03s;
Claw1053/107.11s (`claw-retired-final-root9.log`). Command:
`uv run python scripts/test.py --jobs 2`, with `TMPDIR=/data/t.Vy5SaQ`,
`UV_CACHE_DIR=/data/t.Vy5SaQ/uv-cache`, `TMUX_TMPDIR=/data/t.Vy5SaQ`.
Strict nine-package mypy595 files, Ruff packages, Flow17 and lock/diff pass.
Earlier default4 timing/full-root observations remain documented; no test,
physical terminal/socket check, deadline or xfail was removed to obtain this gate.

## Not finished

The final acceptance audit found a separate real gap: ordinary context creation
and turn persistence still fsync on the event-loop thread, and malformed active
index data can be silently ignored/replaced. Those are not fixed by releasing
extensions or by passing existing tests. VERIFY-01 and the overall goal remain
pending; see `docs/claw-assistant-core-final-audit.md`. The previous cold artifact
snapshot predates these lifecycle changes and final cleanup. Final current
artifacts and the one whole-branch review remain mandatory, with no push,
publication, paid provider or existing user data/service changes authorized.
