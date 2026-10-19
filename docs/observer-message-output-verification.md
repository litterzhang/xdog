# Managed observer per-message replies

Milestone: source/artifacts through commit90e56a7. Subsequent final cleanup and
its mandatory rebuilt artifacts are tracked by the final-acceptance plan.

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

## Rebuilt cold installed artifacts

Current runtime artifacts: `/data/observer-message-dist.LN5URO`, **seven wheels
and seven sdists**, all14 Twine checks pass. Complete runtime/resource byte
parity against current `packages/*/src/xdog` passes for both wheel and sdist:
AI53/Agent124/Goal21/TUI89/Coding98/Claw94/Eyes37 files, including py.typed and
packaged skills/extension code. Identities are recorded in `source-parity.json`.
Claw wheel SHA256:
`153f0691be7456b2a223fe082be587151ae7dc0cbd2601b2a38dc399f0cce502`.
Goal wheel SHA256:
`19d5057368c18987711960ec41d16feb6d6de341027e36933162268cb253be6b`.

`verify_acp_artifacts.py` creates five non-editable wheel-only environments,
outside the checkout with a fresh private HOME/config root and no project
PYTHONPATH. Official ACP SDK0.12.1 is used. The new external
`verify_claw_observer_output_artifact.py` imports only installed runtime modules;
it verifies actual Weixin intake -> Agent eyes tool -> source journal -> scheduler
-> Unix ACP enqueue -> Core input/execution/output -> original sender. It proves:

- Alice's first message is sent while the second real Agent response is gated;
  Bob's later input/current channel mapping cannot claim it. Two distinct output
  IDs, no aggregate duplicate.
- Stopping source preserves accepted execution and both messages; stopping
  notifications leaves the first delivered, second cancelled, execution succeeded.
- A real not-sent network boundary leaves both original message outputs pending;
  after application/endpoint/source restart a forbidden model transport is never
  called. Original Alice/context and original output IDs deliver once, despite a
  different current peer. Source/journal/ACP/Core stores are not test substitutes.

The same external first-message probe fails at its delivery deadline against the
previous installed aggregate-sender artifacts, after the second response has
actually entered its gate (`observer-message-installed-red.log`). A first setup
attempt rejected an incorrectly permissioned scenario directory; it was corrected
before recording the behavioral RED. Fresh artifacts pass the behavior probe.
Expected `DeliveryNotSent` tracebacks in the cold log are intentionally exercised
network rejection, not hidden test failures.

Five-environment receipt:
`/data/t.Vy5SaQ/xdog-acp-isolated.iusd70l5/verification.json`, including retained
standalone Eyes validation, Agent stdio peer, model-free Coding Unix intake,
actual Coding owner, generic Claw ACP, ordinary output restart and native queued
controls. Dedicated updated observer output consumer is part of that same runner.

Same rebuilt Goal wheel and packaged extension digest
`2959d6de1764806ef3b078b0149d3f633164b4b4a37679eda47a09c40a90f3c7`
also pass actual cold lifecycles:

- Goal-only: `/data/t.Vy5SaQ/goal-only-installed-4ocsbfmh`; real SDK Agent/
  ChildSupervisor/tool IO, explicit acceptance and independent review, five
  scripted model calls/two Agents; no installed/imported TUI or host packages.
- Coding: `/data/t.Vy5SaQ/coding-goal-installed-1nj35292`; base ordinary turn
  without Goal, install only Goal then actual worker/report/accept/reviewer,
  three durable notices, same generic display unloaded during running worker,
  old exact owner unavailable/new owner empty. Default ask blocks attempted write.
- Claw: `/data/t.Vy5SaQ/claw-goal-installed-l_l85g3v`; base without Goal,
  install only Goal then actual worker/report/rotation/accept/review/reopen,
  three notices; same display unloaded during running worker, original stable
  group Goal remains visible in the fresh parent. No native Goal widget added.

Logs under `/data/t.Vy5SaQ/`:
`observer-message-{build,twine,parity}-current.log`,
`observer-message-installed-{acp,goal,coding_goal,claw_goal}-current.log`,
`observer-message-final-installed-acp.log` (strengthened pre-resume peer check).
Commands are the existing `scripts/verify_{acp_artifacts,goal_artifact,
coding_goal_artifact,claw_goal_artifact}.py <distribution-directory>`, run from
the repository only to create isolated external consumers. No paid transport,
provider, real account, user service or user state is used.

## Remaining gates

Final artifact-stage source gates: **6864 passed,34 skipped**,174.65s,
Claw1041/107.26s (`observer-message-final-root9.log`), same unfiltered jobs2 and
short /data environment as above. Fresh strict9 mypy595 files, Ruff packages and
artifact verifiers, Flow17, lock/diff pass. Current wheel/sdist runtime/resource
parity still matches source after these gates; only test consumers/docs changed
since the build. Final logs: `observer-message-final-{root9,strict9,flow17}.log`.

Inert queue/debounce cleanup,
all47 requirement-by-requirement audit and one final fresh-context whole-branch
review remain. This document does not claim the whole assistant-core objective
is finished. No push, deployment, publish, paid model or existing user data/service
changes were performed.
