# ACP input, explicit result reads and proactive messages

ACP is a private input transport into Claw's application-owned group Inbox, not
an installed chat channel or a user reply recipient. Claw is an application, not
a public Python SDK; use the CLI/configuration/channels and documented protocols.
Each group has one logical main Agent and execution route. Session transcripts
store context/history; they do not provide branch navigation or a second Agent.

## Default behavior

- `xdog/enqueue` returns a durable queued/duplicate ACK and original message ID,
  **not** an execution result or proof of user notification.
- Standard `session/prompt` waits for the original receipt, returning stop reason
  and `_meta.xdog` execution identifiers, **without agent-message chunks**.
- Both paths use the same group controller/main Agent and original tool policy.
  Results remain in Core input receipts and the session transcript. There is no
  second Agent, automatic channel broadcast or extra reply turn.
- Host metadata records `input_origin: acp`; the accepted input has no default
  reply channel/`ReplyTarget`. Untrusted channel/source/provenance labels cannot
  select a recipient, even if an input claims a nonempty channel label.
- Standard prompt execution failures remain ACP request errors. Validation,
  capacity errors, durable receipts and ACK-loss idempotency remain separate facts.
- The TUI defaults to `acp_updates: 0`, suppressing live automatic ACP completions.
  Explicit `acp_updates: 1` is a diagnostic subscription, not chat routing.
  Historical `channel: acp` labels are provenance, not delivery destinations.

Every gateway starts its private Unix ACP receiver at
`<data_dir>/acp/agent.sock`. `acp_socket_path` or `gateway start --acp-socket PATH`
overrides it. The prompt publishes the real endpoint, opaque session address,
cwd and enqueue shape; never guess these from a transcript or event label.
See [Claw/Eyes ACP setup](../../../docs/claw-eyes-acp.md).

## Application-owned intake and output

Native, messaging and ACP input share `<data_dir>/inputs/inbox.sqlite3`. ACP owns
no receipt DB, consumer, observer query or report-delivery policy. Detaching a
connection/endpoint only detaches waiters, not accepted application work or storage.
Receiver-only restart does not replay or cancel accepted work. Explicit original-input
cancellation and actual application shutdown/crash use separate owners; started
crashed execution becomes interrupted and is never automatically replayed.

Outgoing messages have independent application-owned facts in
`<data_dir>/outputs/journal.sqlite3`. Pending/sending/delivered/unknown/cancelled
outputs and proven-not-sent send attempts are distinct from Agent execution. An
intake ACK, successful turn or persisted question does not prove delivery. Unknown
sends are not automatically replayed, and original destinations are not reselected
on reconnect. Same-ID/content enqueue retry handles ACK loss, not exactly-once
model/tool/channel effects; never blindly resend outcome-unknown standard prompts.

## Explicit reads

`xdog/inbox_status` keeps namespace, status, attempt ID, execution session ID,
error and cursor fields. Selecting `message_id` also returns bounded `response`,
`response_offset` (default `0`) and `response_next_offset` in that message row.
Pass `response_next_offset` as the next request's `response_offset` until null.

Offsets count Python Unicode characters, not bytes. Each page contains at most
16,384 characters. The offset must be a strict integer in `0..1048576` and requires
a selected message. List/cursor pages omit response bodies; error previews remain
limited to 1,024 characters. These are bounded receipt results, not an unbounded
transcript export.

State, attempt/session identity and response come from one immutable receipt
snapshot. Across an explicit retry, compare `attempt_id` and restart pagination
if it changes. Reads do not invoke models, retry tools, send chat or release waits.
Clients formerly consuming streamed ACP answers must explicitly read the receipt.
Suppression is not transcript redaction; history remains inspectable.

## Explicit delivery

Ordinary Weixin/TUI messages keep their original captured reply bindings. Trusted
installed output sources may supply a frozen original `ReplyReference`, independently
of ACP input origin. Accepted routes are not reselected after ACK loss/restart.

A main Agent handling automatic input may use the normal permission-gated `message`
tool. First list current bindings, then select one exact host-issued ID in the
same operation. These are tool arguments, not additional CLI subcommands:

```json
{"action":"channels"}
```

```json
{"action":"send","channel":"EXACT_RETURNED_CHANNEL_ID","text":"Verified result"}
```

Each send targets **one currently bound channel of the current group**. Arbitrary
group/recipient fields and multicast are rejected. The host revalidates that the
listed recipient binding is still current, then freezes it for the send. Stale or
changed bindings fail closed; no inferred creator reply or fallback broadcast exists.
A permitted child grant still has the same group/channel fence.

Sending is one tool effect with its own `message_id`, `status` and per-output
receipts, not a default-route change or end-of-turn instruction. Do not repeat
sent text as an ordinary reply or retry unknown output. Optional `reference` is
only a bounded correlation string, never routing authority or permission.

## Goal questions

Goal's skill body is discoverable, not active by default. Once loaded, the main
Agent owns planning, task evidence and completion; reminders use this same ACP
intake, not a UI interceptor or Goal-specific execution loop.

New waits support only `user` or `tasks`. A new `wait(user)` requires nonempty
`question`, `impact`, `resume_when`. Saving it is **not sending it**. Read Goal
status for the real `question.id`; if notification is needed and permitted,
explicitly send through a listed `message` channel, optionally using that ID as
`reference`. Do not fabricate an ID/channel or duplicate a question after ACK loss.

Optional `question.delivery` reads actual correlated host output facts. Unavailable/
untracked does not prove nothing was sent; unknown/partial/cancelled does not mean
delivered. No output receipt or read activates Goal. The main Agent checks actual
authorized user input against `resume_when` before versioned `activate`.
Automatic task/clock/ACP input cannot release user waits, historical host waits or
user pauses. New `wait(host)` is rejected: host/tool/delivery faults are runtime
facts, not Goal business-state transitions. See [Goal states](../../skills/README.md#waiting-pause-and-question-delivery).

## Eyes and Flow boundaries

Installed Eyes is an application-scoped capability whose tool invokes the public
Eyes CLI and ensures the shared user-local daemon, without creating observations.
It owns registration/cursors/handler delivery, not later Agent execution or chat
receipt policy. Goal separately manages only its own task observation via that
CLI; Claw contains no Eyes runtime/private daemon adapter or observer journal.
Closing a host binding detaches, not daemon stop or cancellation of registrations.
Installed Flow likewise invokes public CLI argv, but has no daemon. Both tools
retain normal host policy and return actual CLI exit/stdout/stderr facts.

## Limits and verification

Existing persisted inputs/routes are not migrated or rewritten. A duplicate ID
keeps its accepted envelope. Original namespace/message/attempt/session fences,
bounded results and unknown-send/interrupted-execution review still apply.
No exactly-once or automatic replay guarantee is implied.

From the repository root, focused regression coverage is:

```bash
uv run pytest -q packages/claw/tests/test_acp_silent_inputs.py \
  packages/claw/tests/test_outbound_messages.py \
  packages/claw/tests/test_goal_host.py \
  packages/claw/tests/test_capability_owner_recovery.py
```

These tests use temporary stores/scripted model transports/private endpoints and
fixture-owned Eyes daemon isolation, not a running user's gateway or registrations.
They are regression checks, not proof of live provider or external-channel delivery.
