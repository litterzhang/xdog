# Managed observer per-message replies

## Ownership and behavior

Eyes sources still collect evidence and route events through the registered ACP
handler to Claw's durable input service. No CLI callback or second Agent loop is
introduced. ManagedObservers registers the generic `observations` output source;
its resolver checks exact group/workspace, immutable canonical outbox message and
input namespace before Core commits/acknowledges the external input. The original
creator's ReplyReference is retained; source/provenance text cannot select a
recipient or grant user authority.

Core emits each actual assistant message through the existing durable reply path,
with independent output IDs, execution attempt and original channel/context.
ManagedObservers reads those Core facts; it never sends `receipt.response`.
The retired aggregate sender and observer begin/finish-send APIs are deleted.
Input response remains execution history, not a second transport payload.

Source stop does not retract accepted work or its replies. Explicit
`cancel_notifications` persists source-owned notification intent, then cancels
only pending Core outputs for its exact original message IDs. Future output is
fenced by that same policy. Already delivered or entered/unknown sends retain
actual facts; cancellation does not undo them, stop the Agent or change tool
permissions. Missing source policy/original endpoint leaves output pending,
never substitutes another peer. Restoring transport sends the same original
output without another model turn; unknown effects are never blindly replayed.

Output completion publishes readonly reconciliation hints, including after input
execution has detached. A failed optional listener cannot relabel a committed
send or veto execution. Observer projections fence original attempt and execution
status so a stale running observation cannot overwrite a terminal execution.
The bounded status projection is valid UTF-8 JSON and explicitly marks omitted
output/error detail; full Core receipt errors remain intact.

## Storage contract

- Core input journal remains `xdog.claw.inbox.v3`, envelope `xdog.claw.input.v2`.
- Core output journal is `xdog.claw.outputs.v2`, with explicit pending-only
  cancellation and retained send-attempt history.
- Observer journal is `xdog.claw.observers.v3`. It owns source cursor, immutable
  intent/outbox and notification policy, not a second sending authority.
- Old outputv1/observerv2 formats are refused before recovery and preserved
  byte-for-byte. No migration, dual read or compatibility facade is provided.
  Explicitly select a fresh data root for this development format; these changes
  do not authorize deleting or converting existing user data.

## Source-runtime evidence

`packages/claw/tests/test_observer_message_outputs.py` uses actual leased stores,
Weixin channel, managed source, private Unix ACP, Agent/tool execution and model
barriers. Only the model and external network boundary are scripted:

1. First message arrives at original Alice/context while the second response is
   still blocked; second message arrives once with another output ID, no aggregate
   duplicate. Cancelling source leaves both accepted outputs; cancelling
   notifications leaves `[delivered, cancelled]` and successful execution.
2. An optional output listener raises after a real send; output stays delivered.
3. Cancellation during an entered send followed by ACK loss stays unknown;
   resume/cancel never resends or reruns the model.
4. A large Chinese transport error retains full Core unknown detail while the
   observer's JSON projection stays bounded and explicitly marks truncation.

The corrected first-message test was also run against the prior aggregate sender
and failed at its delivery deadline while the second response remained blocked;
new behavior passed. This avoids assuming an incidental producer/drain scheduling
order. Logs: `observer-message-stream-corrected-red.log` and
`observer-message-stream-green2.log` under `/data/t.Vy5SaQ/`.

Related actual recipient/journal/source suites retain original account/context
across restart, no model replay, no new peer fallback, canonical/namespace
conflicts, source cursor progress despite pending reports, independent cancel,
real Core send crash recovery and stale attempt/status fences. The old test
interception of `_report` was replaced with the actual not-sent channel boundary,
not retained as a hidden sender. Focused current projection/recipient suite:
**44 passed,17.42s**, `observer-message-projection-current.log`.

The final related streaming/journal/recipient/pending/source suites pass
**70 tests,57.13s**, `observer-message-final-focused.log` (concurrent with
the whole-nine run). Fresh all-package Ruff and strict nine-package mypy
**595 source files** pass;
Flow shipped examples **17 passed**, lock and diff checks pass.

Fresh unfiltered all-nine gate: **6864 passed,34 skipped**,180.08s,
`observer-message-root9-current.log`; Claw **1041 passed**,116.47s. Command:
`uv run python scripts/test.py --jobs 2`, with `TMPDIR=/data/t.Vy5SaQ`,
`UV_CACHE_DIR=/data/t.Vy5SaQ/uv-cache`, `TMUX_TMPDIR=/data/t.Vy5SaQ`.
This retains all test paths and physical terminal/socket tests. Earlier default
four-worker timing observations and root-full constraints remain documented;
this result is not a claim to have diagnosed/fixed those earlier observations.

## Remaining gates

The prior shared Goal UI cold wheels are from an earlier Core/observer-format
snapshot. They are not proof of this new output path. The plan's Task3 must
rebuild wheels/sdists, check runtime/resource parity and Twine, and run external
wheel-only observer streaming, original-recipient recovery and independent
cancellation plus existing Goal-only, both-host shared UI and ACP/native/output
consumers. Inert queue/debounce cleanup, all47 requirement audit and one final
fresh-context whole-branch review also remain. This document does not claim the
whole assistant-core objective is finished. No push, deployment, publish, paid
model or existing user data/service changes were performed.
