# Claw channel durable intake handoff

Date: 2026-10-19. This is a partial milestone of the assistant-core refactor,
not the standalone Goal/shared-extension UI release.

## Actual application path

`Weixin getUpdates -> await Channel.on_accept -> Orchestrator.route_message(wait_for_result=False)
-> application InputService/InputStore -> bounded presentation FIFO -> original-ID result wait`.
The cursor advances only after all supported user messages in the batch are
committed. Agent completion is not part of acceptance. Reoffered upstream IDs
are namespaced by account and sender; absent reliable upstream identity is an
error before cursor ACK. Timestamp projection does not read the current clock.
Partial intake, capacity rejection and cancelled acceptance leave the old cursor.
An unknown application target cannot report successful durable acceptance.

Assistant messages use the originating channel and captured sender/context, not
all group channels or the Weixin latest-recipient/context caches. An error is
emitted by the sole actual executor, not once per duplicate result waiter.
Weixin explicitly supports restoring this destination after application restart;
generic/TUI channels deliberately do not claim that a new connection can recover
an old connection's reply ownership.

## Evidence

- `test_weixin_durable_poll.py`: actual Core SQLite commit barrier, partial-batch
  failure/reoffer, receiver stop; actual cursor file bytes are checked.
- `test_weixin_application_intake.py`: actual Orchestrator/InputService/session,
  deterministic gated model and substituted external Weixin network boundary.
  Busy acceptance, two peers/immutable contexts, replay without new execution or
  repeated error, receiver disappearance/application restart, changed body/context
  conflict, missing identity, per-sender/account client IDs, unknown target and
  full-core-capacity rejection are exercised.
- Existing channel, monitor, input runtime and Orchestrator tests remain enabled,
  including progress delivery, per-message output order, typing cleanup and no
  aggregate-final-body resend.

## Remaining work (not implied by this milestone)

Captured input destinations are not a durable output outbox. A channel failure
can still escape the current per-message callback into the Agent turn; pending
known-not-sent assistant output does not yet have an independently recoverable
send receipt. Accepted native TUI drafts/reconnect, observer-created immutable
reply references and execution-bound typing need the subsequent application
handoffs. IN-06/IN-08 remain pending. Optional `xdog-goal`, two real host adapters,
shared Goal extension UI and removal of the legacy active Goal path remain open.
