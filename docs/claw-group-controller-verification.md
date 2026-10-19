# Claw single group controller and Native busy intake

Date: 2026-10-19. This is the input/execution milestone, not final completion of
the optional Goal/shared UI refactor.

## Actual active architecture

- `core/runtime/group_controller.py`: one controller per registered group. Frozen
  reservation tokens fence the pre-worker interval; the actual task owns the group
  during execution and cancellation cleanup. Same-task nesting shares that owner;
  another task cannot inherit it or release a running owner. Late/repeated release
  cannot release a successor. Human groups bypass the existing shared background
  capacity pool; waiting for a group does not occupy that pool.
- The existing application `InputService` consumer acquires this scope and retains
  it through the actual durable terminal/interrupt commit, not only through the
  Agent method. Durable admission confirms message/attempt/session identity on the
  actual owner. Optional completion notifications run after settled release.
- Host commands use the same fenced task factory/scope. Pre-start cancellation
  closes an unentered coroutine and settles its original fence. The old
  `core/queue.py` collect/steer/backlog implementation and Gateway global
  `_group_runs`/lock are deleted. No compatibility facade or second consumer.
- Native intake now commits while the group is busy, like ACP and Weixin. ACK is
  persistence, not execution. Native result waiters/connection state do not select
  the main Agent or acquire its execution authority.
- A Native view receives an internal execution future only at original actual
  admission. Control, queue snapshot and active abort compare that exact
  message+attempt+session. A queued view cannot steer another input; a delayed old
  view cannot affect either another message or an explicit retry of the same ID.
  Pending withdrawal additionally checks the original live callback binding, so
  a duplicate result waiter cannot borrow its controls.
- Pending cancellation commits through the ordinary Inbox; active cancellation
  signals/cancels only the original producer and waits for its existing cleanup.
  Withdrawal disk+cache settlement survives a cancelled ACK waiter, avoiding
  phantom pending capacity. The asynchronous abort adapter does not block reads.
  Early stop signals the real consumer once; close joins rather than recancelling
  its actual tool/Agent cleanup. A gated actual producer-cleanup regression proves
  terminal receipt publication cannot skip that cleanup.
- TUI retains explicit cancel intent across ACK loss; positive absence cannot
  re-submit a message the user already cancelled. Actual accepted outcomes remain
  authoritative. Images retain their post-validation session-owner recheck.

No message approval, Goal/Eyes business decision, new Agent/tool loop, TCP/broker,
fixed child lifecycle budget or permission relaxation is introduced. Source labels
are not leases, execution identities or permissions. Agent steer/follow-up queues
and consumption/return receipts remain `xdog.agent` semantics.

## Verification

- Final full8 runner: **6883 passed,34 skipped**, Claw **1211 passed**,139.00s.
  `/data/t.Vy5SaQ/group-controller-verified-root.log`.
- Focused actual Native/controller/input/image/TUI tests: **85 passed**.
  `/data/t.Vy5SaQ/group-controller-verified-focused.log`.
- Strict8 mypy: **575 source files**, no issues; Ruff packages and artifact
  verifiers passed. Flow shipped examples **17 passed**, lock/diff checks passed.
- Actual asyncio owner tests, actual Core SQLite commit/interrupt gates and
  deterministic-model Orchestrator tests prove the owner spans settlement.
- Actual multiple Unix clients prove busy commit, queued control refusal,
  exact pending/active/precommit abort, separate turns and no second main Agent.
  Gated old reply delivery proves stale views cannot control a successor or an
  explicit failed-input retry with the same message ID but a new attempt.
- Existing cancellation/permissions/navigation/image tests remain enabled. Five
  tests for removed collection/mixed-mode behavior were retired with that code;
  all three original concurrency contracts were ported to the new controller.
  The old internal tokenless-call trace assertion was replaced by observable
  canceled receipt/free ownership/successor behavior, with actual stale-token
  tests. No permission or cancellation assertion was disabled.

The final installed environments are under
`/data/t.Vy5SaQ/xdog-acp-isolated.8dr08q49`; the tracked
`scripts/verify_claw_native_artifact.py` is copied into that isolated environment
and exercises actual Unix frames/controller decisions, not a source import.

The initial full run exposed a real lost post-image-validation session recheck;
it was restored and its unchanged regression test passed. Earlier test-only
missing imports/fixture mistakes and the corrected RED/GREEN runs are recorded
in the plan ledger, not counted as successful full-suite evidence.

## Fresh installed artifacts

Six wheels + six sdists built at `/data/gdist.YhzlfK`; Twine passed. Every runtime
Python member byte-matches the source, manifest `source-parity.json` in that path.
Five fresh wheel-only consumers outside the checkout run official SDK stdio/Unix
ACP, Coding ingress and owner, Claw shared Inbox/endpoint restart and Eyes-only
installation. Claw also runs the tracked installed output recovery and Native
busy-intake probes: the queued peer cannot steer the first, withdrawing it does
not cancel the first, and the other pending input executes separately. Only
synthetic model calls; no paid provider, user service/data, push or publication.

## Still open in the whole objective

Observer-created report destinations still need the creating input's immutable
reply reference; ordinary output receipts do not fix feature report broadcasting.
Legacy GoalManager/implicit reviewer and native Goal UI remain transitional and
must be replaced with optional standalone `xdog-goal`, both real host adapters
and one shared extension UI. Old inert QueueMode/debounce declarations need final
configuration cleanup. Local editable unsent TUI drafts are process-memory, not
silently autosaved plaintext or a second execution Inbox. Final nine-package,
Goal-only/two-host artifact validation and fresh-context whole-branch review are
still mandatory; the overall47-item checklist is not complete here.
