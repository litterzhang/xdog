# Claw owned session IO — preparation milestone

Date: 2026-10-19. **Task1 source preparation only.** Full turn history durability,
startup/status/other application callsite audit, current installed artifacts and
the parent47-item/whole-branch review gates remain incomplete. This does not claim
that all ordinary IO or the full assistant-core objective is finished.

## Actual ownership and data flow

Async application execution/control routes use `GroupRuntime.prepare_session`.
Workers read/check/create/reset metadata and load detached `PreparedSession` /
`PreparedHistory` data under the existing store lock. Settings, access, selected
tools/skills, model/reasoning, canonical history/channel/compaction data and
extension entries remain owned by their existing stores. The frozen snapshot
contains no Runtime, Session, Agent, tool callback or new business database.
The original execution loop constructs and publishes the actual Agent only after
IO settlement; the existing GroupController still owns actual execution.

Preparation serializes cache initialization, pins original generation/group/store/
workspace/current identity and refuses stale cached bindings, rather than silently
borrowing/replacing a different committed parent. Cancellation waits the actual
worker before releasing the real group operation. A committed context may remain
without an Agent after cancellation; the next real input reuses that context,
never reruns the cancelled prompt or creates a second main loop.

Prompt IO reads workspace/memory/bootstrap with detached tool names/descriptions
and model/path values on an owned worker. Only the original loop updates the
Agent prompt. Bootstrap retains its existing one-time read/delete semantics;
there is no new business task, observer, timer or Goal lifecycle. Synchronous
composition helpers remain for standalone setup/tests; no production async route
calls `get_or_create_session` anymore. Broader startup, status, MCP browser and
other application IO/cancellation paths still require Task3 inspection.

Agent's optional frozen `SkillPlacement` uses the **same existing placement
engine**, with no filesystem skill reread at adoption and no second manager,
permission engine or storage. Selected slugs must match. Session bodies/index
stay cacheable; turn bodies stay transient messages and are consumed once.
Explicit later skill selection resolves off-loop before owning-loop adoption.
Claw's existing normalized skill order is preserved in the detached snapshot;
valid unsorted native sidecar bytes are never migrated/rewritten by preparation.

A native cold inventory request keeps preparation inside its existing cancellable
job, not the socket reader. Actual ping/cancel intake stays responsive while that
job waits on metadata commit. Original requested/actual session identities and
existing read-only inventory boundaries remain in force, with no model call.
ACP and channels receive no Goal/Eyes policy or a second handler/Agent loop.

## Concrete runtime evidence

`packages/claw/tests/test_owned_session_io.py` uses actual Core input/store/
controller, Agent, filesystem fsync/open, stdio skill resources and a private
native Unix socket. Only model transport is scripted; actual writes/reads/joins
are delegated, not mocked away:

- Actual metadata fsync is off the model/event-loop owner for cold preparation and
  daily reset; native reopen retains exact context, readonly access, selection and
  original user history. The Agent/model still runs on its original loop.
- Actual workspace/local-skill/history reads happen off-loop, and real memory
  text reaches the model. The earlier combined read test was RED even after
  metadata fsync moved, correctly exposing constructor skill and prompt reads.
- A real post-fsync commit barrier holds metadata before actual admission/Agent
  publication. Another group executes while it remains held. Explicit pending
  withdrawal leaves the original unexecuted, and the successor executes once.
- Repeated cancellation of a real reserved group preparation holds the owner
  until physical IO exits; afterwards the one committed context is reused by an
  actual input, with no original model call or abandoned worker.
- A real different committed context cannot be borrowed by the cached accessor
  (observed RED then GREEN). Valid native `(z,a)` saved skills preserve old sorted
  body placement and exact sidecar bytes (observed RED then GREEN).
- A real cold native inventory request previously prevented that same connection
  from reading ping while its metadata IO was held (RED at original3s deadline).
  Moving preparation into the existing job passes, with no model request.

`packages/agent/tests/test_prepared_skills.py` also proves constructor/later
selection adoption does not ask the filesystem manager again, session-prefix
identity and one-run turn-skill retirement match the shared runtime, and wrong
selected slugs refuse adoption. Existing original skill behavior remains tested.

Focused actual IO/ownership/Goal display/access/tools suites97 passed19.05s before
the final native-reader case. Native inventory + both old/current tool browser and
prepared-skill suites23 passed3.99s. Final unfiltered whole-nine source gate: **6903 passed,34 skipped**,176.61s,
Claw1077/110.85s and Agent1932/15 skipped. Strict all9 mypy596 files, Ruff packages,
Flow17, lock/diff pass. Command `uv run python scripts/test.py --jobs 2`, with
`TMPDIR=/data/t.Vy5SaQ`, `UV_CACHE_DIR=/data/t.Vy5SaQ/uv-cache`,
`TMUX_TMPDIR=/data/t.Vy5SaQ`. Final logs `claw-owned-session-final-{root9,strict9}.log`
and `claw-owned-session-flow17.log`. The earlier6900 suite precedes final
cached-owner/skill-order/native-reader regressions and is not substituted. Logs under `/data/t.Vy5SaQ/` use `claw-owned-session-*`.

## Subsequent durable turn commit and remaining application audit

At this preparation milestone the full-turn real descriptor probe still failed:
12 actual fsync calls, two on the event-loop owner (the original had12/12).
The subsequent Task2 implementation in
`docs/claw-owned-transcript-commit-verification.md` replaces final ordinary
history+metadata writes with one actual owned transaction. Its whole-turn probe
now passes with23 actual fsync and zero on the model loop, plus actual data-sync,
rollback/cancel and process-death/no-replay proofs. These are distinct phases;
the earlier preparation-only6903 gate is not a final all-application IO claim.

Task3 still must audit startup/status/MCP/image and other async/cancellation IO
paths, especially readonly status callers now that history reads correctly wait
behind the actual commit lock. Parent final-acceptance Task2/all47 review and final
current artifact gates remain active. No push, merge, publication, paid provider
or existing user data/service mutation is authorized.
