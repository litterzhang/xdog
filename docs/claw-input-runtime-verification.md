# Claw application-input runtime checkpoint — 2026-10-19

This checkpoint makes the foundation execute actual application turns. It does
**not** complete the47-item refactor, Goal extraction or the final ingress cutover.
All work stays in the original checkout/feature branch; no user gateway, credentials,
real model/provider, hosted service or publication was used.

## Actual runtime changes

- Orchestrator owns InputStore/InputService, including when ACP/Eyes/TUI are absent.
  Ordinary live inputs persist before scheduling and native run ACK follows commit.
- One scope consumer calls the existing group exclusion and actual AgentSession.
  Claim/admission/start and actual context ID remain distinct; no new model loop.
- Ordinary busy input remains a separate durable turn. It is never silently steered;
  explicit Agent input controls remain supported and retain identified receipts.
- Input projection preserves canonical external content, original metadata/kind,
  images (including exact blank image-only text), host authority and original reply
  descriptor. Host context is separate from evidence/provenance. Envelope format
  is now `xdog.claw.input.v2`; there is no v1 upgrade/dual decoder.
- Detached native/ACP waiters do not own accepted application execution. Native
  intake IO no longer blocks the socket reader. Explicit abort and application
  shutdown retain actual Agent cancellation/settlement responsibilities.
- Duplicate terminal requests return their durable result without another model
  call or retaining a new presentation owner (weak-reference regression).
- Generic settled hints run after durable result/idle-cache settlement. A failed
  optional subscriber cannot change execution success or replay model work.

## Tests and corrections

Consumer tests use the real Agent and sqlite, not a mocked consumer. Initial RED10
was followed by fixes for RED2 shutdown/withdrawal races, RED2 cancelled-executor
stranding, and RED1 cleanup-disk-error loss. Real unstarted claim and terminal
commit races are also exercised. Application projection/runtime initially RED7;
native durable-ACK/failure/disconnect initially RED3; later blank-image and stale
view ownership regressions reproduced RED2. The settled-hint API was RED before
implementation. No characterization pass is presented as a reproduced failure.

The first broad cutover run had4 failures (1083 Claw passes):
-2 Goal/Eyes end-to-end tests waited only on the retired queue's guard. They now
 wait for actual application idle, including durable settlement, before reviewing.
-1 scheduled shutdown test used endpoint detach as application shutdown. The test
 now stops/restarts the actual application and retains its interrupted/no-success
 assertions. Endpoint-only detach has a separate actual-Agent regression proving
 core execution continues and later succeeds exactly once.
-1 human steering test depended on implicit queue mode. It now submits explicit
 Agent steering; negative cases actually consume untrusted input, attempt the Goal
 mutation and verify its refusal plus unchanged original criteria.

A separate affected run also caught the old recursive-drain timing assertion and
an active-context replacement in a restart helper. Assertions now await the exact
second receipt; interrupted-model tests explicitly restart their actual owner.
No skip, timeout increase, removed safety assertion or fake second model loop was
used to obtain the final green suite. Full rich live TurnResult remains available
for the original waiter; terminal retries use the canonical recorded result.

## Verified checkpoint

- Whole root: **6755 passed,34 skipped**, all8 paths completed in102.47s;
  Claw1092 passed. Full log: this plan's task-2-delivery-root.log.
- Input/native targeted suite:85 passed5.59s; endpoint-detach regression1 passed1.45s.
- Ruff packages passed; strict8 namespace mypy567 files passed.
- Flow examples17 passed; `uv lock --check` and diff checks passed.
- Fresh Claw wheel/sdist `/tmp/claw-input-runtime-dist.GgWRE9`: consumer/store/models
  and runtime input_adapter Python members byte-match checkout. This is not a
  Goal-only install or full fresh-environment interoperability claim.

## Outstanding boundary (required next, not delivered)

ACP still wraps some turns in its old leased receipt/observer bridge. That older
receipt can become interrupted when its waiter detaches even while the new core
execution continues; **it is not the final authority and must be removed**. Its
business hooks and schema must move out of the adapter; managed observers must
own cursor/outbox/report state and use stable core input receipts/namespaces.

Native pre-acceptance local queue/reconnect behavior and the Weixin monitor's
poll-cursor handoff still need the final durable channel protocol. Captured reply
metadata alone is not proof that all channel delivery/recovery uses it yet.
Gateway's remaining reservation bookkeeping/legacy queue helpers, old Goal manager,
implicit Goal policies and native Goal UI are also pending removal. Optional
standalone Goal, both real host adapters, shared extension views/UI and final
nine-package/fresh-install/fresh-review gates remain required. None is claimed
by this intermediate green checkpoint.
