# UI-064 argument cancellation settlement repair

Checkpoint: October 11, 2026. This records both the production receipt defect and
the separate physical test synchronization issue found in the completed8191-case run.

## Confirmed ordering failures

The physical argument-completion test queried `is_group_running()` from the test
host after injecting Ctrl+C. That snapshot could precede buffered completion/cancel
frames on the actual frontend connection. A later completion then reserved the
group and the counter command was correctly refused as busy. A host-side idle
snapshot was not proof that the original frontend request had settled.

A deterministic real-socket batch additionally established a production defect:
completion + cancellation/capability-withdrawal + a successor control were sent in
one write. The original argument coroutine never entered. The gateway emitted its
canceled receipt while its done callback had not yet released the group reservation,
then rejected the successor. Only afterward did the done callback release ownership.
This is not fixed by retries, sleeping, early reservation release, or relaxing the
idle guard.

## Narrow repair

In `GatewayServer.cancel_argument_job`, the **unstarted** branch now awaits the
canceled original task before publishing its canceled receipt. The existing done
callback remains the only reservation-release owner, so it cannot later release a
successor's reservation. Cancellation of a started worker remains nonblocking for
the connection reader and retains its existing cleanup path.

The physical test now opts into a test-bootstrap-only observer of the actual
frontend poll result. A fresh nonce prevents reuse of an old empty-editor snapshot.
It waits for the original completion request, queued request and cancellation to
settle before issuing the counter command. This observer changes no production
state or admission policy. Original completion, no-command-execution, draft, resize,
terminal and model-count assertions remain, with no increased deadline.

Changed paths:

- `packages/claw/src/xdog/claw/core/runtime/gateway.py`
- `packages/claw/tests/test_argument_cancel_settlement.py`
- `packages/coding/tests/fixtures/terminal_client.py`
- The named argument-completion test region in
  `packages/coding/tests/test_terminal_acceptance.py`

## Evidence

`/tmp/claw-argument-diagnostic/HANDOFF.md` retains natural and controlled wire traces,
real-batch red results, exact source hashes and reversal proof. Removing only the
approved changes recreates the three existing files' prior hashes; unrelated changes
were preserved.

-15 gateway tests passed, including cancel, duplicate cancel, capability withdrawal,
 exact original/successor reservation-release pairs and started slow-cleanup reader
 responsiveness.
-6 actual physical terminal tests passed.
-3 additional delayed-ingress physical checks passed.
-Ruff clean on all four changed paths.

Logs: `repair-gateway.log`, `repair-physical.log` and the final handoff artifacts in
that directory. The earlier complete root was never interrupted; its8188pass/3fail
result remains diagnostic. Updated static/distribution proof and a fresh complete
source-frozen root invocation are required before final gates close.
