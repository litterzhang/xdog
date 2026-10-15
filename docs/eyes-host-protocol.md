# Local host event protocol v1

This protocol connects an observer to an existing host context. The host does
not import eyes, identify file sources, execute source logic, or trust evidence
as a replacement for registered processing instructions. The transport is a
private local Unix socket, not a network service or an OS security sandbox.

## Capability descriptor

A host places a JSON descriptor in a directory owned by the current UID with
mode 0700. The regular descriptor file has mode 0600. A tool subprocess receives
its path in `XDOG_EVENT_CONTEXT`; the model does not receive the capability token.
The socket must be in that directory. Descriptor fields are exactly:

```json
{
  "version": 1,
  "address": "/tmp/private-host-context/host.sock",
  "token": "opaque-random-capability-at-least-16-characters",
  "owner": {
    "host_instance": "host-id",
    "session_id": "session-id",
    "branch_id": "main",
    "generation": "generation-id"
  }
}
```

A descriptor is not a persisted authorization grant. The host compares both
capability and owner to the current live context. Context navigation revokes the
old generation, even if the user later returns to the same session/branch.

## Framing and validation

Each connection carries one request and one reply as UTF-8 JSON plus newline.
Maximum frame size, including the newline, is 1 MiB. Duplicate JSON keys,
nonfinite numbers, more than 32 nested JSON levels, unknown fields/actions and
wrong types are rejected. Booleans are not protocol version integers.

Requests have exactly `version`, `action`, `token`, `owner`, `body`. Replies have
exactly `version`, `status`, `body`. Bodies are objects. The request token is
never included in a reply, log, CLI output or canonical event message.

## Actions

| Action | Body | Result |
| --- | --- | --- |
| `hello` | `{}` | `ok` with host capabilities |
| `ping` | `{}` | `ok` only while owner is current |
| `register` | `registration_id`, `route_id`, `instruction` | `accepted` or identical-registration `duplicate` |
| `unregister` | `registration_id` | remove route bindings and pending inputs; idempotent `accepted` |
| `deliver` | `registration_id`, `route_id`, `delivery_id`, `events` | admission result, not execution success |
| `status` | optional `registration_id` | `ok` with observation-independent processing states |
| `resume` | `registration_id` | `accepted` only after existing host permission policy allows it |

Protocol IDs are nonempty UTF-8 strings up to 256 bytes without NUL. A root
branch may have an empty branch ID. Registered instructions are nonempty UTF-8
text up to 65,536 bytes. An identical `(registration_id, route_id, instruction)`
registration is idempotent; a changed instruction requires unregister/register.
On partial registration failure, the observer attempts unregister before any
source collection begins. A lost register ACK is covered by the same cleanup.

A delivery never carries an `instruction` field. The host looks up the already
registered instruction. Events are source-neutral envelopes containing `id`,
`source_id`, `detector_id`, `type`, finite `observed_at`, nonempty structured
`evidence`, and optional JSON `payload`. The host treats evidence as data; it
must not use an evidence property as an address, credential or command. One
frame holds at most 256 events, with unique event IDs. Semantic file validation
belongs to eyes, not the host.

## Admission versus processing

Admission statuses:

- `accepted`: the host owns the pending input; it may still be waiting or paused.
- `duplicate`: this delivery ID was already admitted in this generation.
- `busy`: not admitted; retry with the same immutable delivery ID and contents.
- `paused`: not admitted; wait for an explicit permitted resume/status change.
- `stale-owner`: terminate this context's activity, never reroute to another one.
- `rejected`: invalid registration, data or authorization; do not start a model.
- `ok`: non-admission control response.

Control/status presentation is bounded independently of authoritative state. Replies
include total counts and a `truncated` flag when rows do not fit; processing rows
are newest first, and display includes at most the latest eight constituent
delivery IDs with a full delivery count. Dedup records are not discarded. A
registration filter is applied before the presentation budget.

Processing status is separately reported as waiting, running, succeeded,
failed, cancelled or discarded. Admission ACK is not a completed agent task.
Retried deliveries retain both ID and contents, including when the connection
ends without an ACK. New observations cannot be merged into a delivery after
its first attempt; use a subsequent delivery instead.

## Ownership and limits

The host keeps at most 128 pending route items, 256 evidence paths per item and
4,096 admitted IDs per generation. It reports overflow and pauses rather than
silently losing evidence or evicting IDs that would allow repeated execution.
The host prioritizes human inputs and reserves its normal session turn owner
before consuming an automatic input. Every automatic start consumes the
context's one-turn wake budget; success/failure does not replenish it.

There is no durable queue or restart replay. Observer heartbeat is every five
seconds; activity ends after fifteen seconds without confirmed host ownership.
Normal context close revokes authorization immediately. Same-UID hostile-process
isolation is outside this protocol's guarantees.

`docs/fixtures/eyes-host-v1.json` is a nonsecret contract example for both sides'
wire-level tests; it is not a real capability descriptor.
