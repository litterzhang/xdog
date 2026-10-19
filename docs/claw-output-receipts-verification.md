# Claw per-message output receipts — verification

Date: 2026-10-19. This milestone is application Core/channel output, not the
optional Goal/shared UI deliverable and not whole-refactor completion.

## Implemented boundary

Input execution receipts remain authoritative in the application Inbox.
`core/outputs/store.py` owns a separately leased/versioned Core SQLite output
journal. An original frozen input/attempt/destination binding is stored once;
individual output chunks have stable ordinal identities and independent send
attempts. Text/input hashes and state/history validation run before recovery.
No Goal/observer business table or ACP completion callback is involved.

A channel callback commits output before transport. Success records delivered;
positive `DeliveryNotSent` records pending; other send failures/cancellation
inside transport record unknown. Unknown and delivered are never automatically
begun again. A crash during sending recovers unknown. Cancellation after a begin
commit but before calling a sender retains pending (positive not-sent knowledge).
The output service serializes per scope, not globally. Normal channel transport
failures do not escape into the Agent turn or erase its history/result.

Live channel instances have opaque application bindings. Only explicitly
restorable channels can recover a saved destination: Weixin uses the original
account/sender/context and the output ID as `client_id`. A newly connected TUI
is not a replacement for another connection. An unavailable endpoint leaves
pending output; no reply is broadcast to the whole group. Separate assistant
messages/progress/final notices remain separate; aggregate receipt text is not
sent again at completion.

## Verification results

- Actual full eight-package runner: **6835 passed, 34 skipped**, 110.41s;
  Claw **1163 passed**, 85.53s. Log `/tmp/claw-output-delivery-root.log`.
- Strict eight-namespace mypy: **573 source files**, no issues.
  Ruff packages and the two artifact verifier scripts passed; diff check clean.
- Flow example runner: **17 passed**. `uv lock --check` passed.
- New store tests: real SQLite commit/reopen/conflict/fences, positive rejection
  creates a new send attempt for the same output ID, actual child-process exit
  while sending, lease refusal, original bytes retained on unsupported/corrupt
  state, strict ordinals.
- New delivery tests: actual Orchestrator/session with deterministic model,
  ACK-loss success/history retention, pending output recovery with **zero new
  model calls**, original sender/context after cache changes, separate actual
  filesystem-tool progress/final output IDs, per-scope independence, no borrowing
  another TUI connection, cancellation before/inside transport.

## Installed artifacts

Freshly built six wheels and six sdists (AI/Agent/TUI/Eyes/Coding/Claw); Twine
checks passed. Every shipped runtime Python member byte-matches source, manifest
`/tmp/claw-output-receipts-dist/source-parity.json`.

`uv run python scripts/verify_acp_artifacts.py /tmp/claw-output-receipts-dist`
passed in five fresh wheel-only environments outside the checkout:
`/tmp/xdog-acp-isolated.vp7964dv`. It exercises actual official ACP stdio/Unix
transports, Coding ingress/owner, Claw shared Inbox endpoint restart and Eyes
independent installation. It now also runs the tracked
`scripts/verify_claw_output_artifact.py` in the isolated Claw interpreter:
first application commits a successful deterministic-model result despite an
explicit output rejection; after restart the same output ID reaches its original
peer/context exactly once without running the model again. The injected positive
rejection deliberately produces one channel error log, not an unexpected failure.

No provider, user credential/service, push, merge or publication was used.

## Remaining limitations and subsequent work

- Durable send facts do not promise exactly-once side effects or automatic retry
  of an unknown send. No unknown-output retry UI/command is introduced here.
- Native TUI draft/ACK-loss reconnect and its transport/display subscriptions
  still need the separate handoff. Old connections' pending output is not stolen.
- Observer-created report destinations still need capture of the creating input's
  immutable reply reference; their existing report delivery remains feature-owned.
- Execution-bound typing, sole-controller/Gateway reservation simplification,
  standalone optional `xdog-goal`, both host adapters, shared Goal Python UI
  extension and removal of active legacy Goal implementation remain open.
- The overall47-item checklist, fresh-context final whole-branch review and final
  nine-package/Goal-only/two-host installed validation are not completed here.
