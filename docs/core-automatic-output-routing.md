# Generic automatic reply routing and unsent-output cancellation

This is the Core foundation for observer streaming, not proof that the observer
aggregate sender has already been removed. Source integration follows the
observer-message-output plan's Task2. No Goal/Eyes business logic enters ACP,
GroupController or the output store.

## Intake and immutable routing

A trusted application capability registers a short source ID, an async resolver
for original ReplyReference, and a readonly transport policy. This starts no
source/model/service. The resolver must verify its actual immutable canonical
intent/outbox, not source names, provenance or sender labels. Multiple claims or
capability replacement during lookup fail before intake commits.

`accept_external` resolves before durable submit/ACK, so accepted external input
retains its original destination across receiver restart and feature absence.
Routed host context contains only original channel/sender/necessary routing
metadata and a compact protected output marker. Canonical SessionInput/scope/
images are unchanged; origin stays external and human_authorized stays false.
Pasted metadata/provenance cannot create that protected context. ReplyReference
has no timestamp contract, so its redundant zero is reconstructed rather than
stored, preserving the existing64KiB host-context ceiling even for a maximum-size
reply and32-character source ID. No input/image/context allowance expands.

An existing external ID is returned only if full canonical message/scope/images
and actual non-human external binding match. Duplicate ACK recovery never asks a
later resolver or selects a later recipient, even when the original accepted
input had no destination. Different content conflicts. Accepted routing is not
rewritten after storage or inferred from the current active channel.

## Execution and output facts

Core reuses the existing on_assistant_message/durable_reply path. Each actual
assistant message/chunk has its original input ID, execution attempt, stable
ordinal/output ID and immutable recipient context. Reply context is also available
to actual nested host tools through the existing scoped ReplyReference, not a raw
provenance authority field. Agent execution and transport are separate facts.

If a persisted source policy is absent/unavailable, execution can still finish
and output stays durably pending, with no sending attempt or substitute endpoint.
Registration plus explicit output resume can send the original queued text to the
original account/context with the original output ID, without another model turn.
Unknown sends remain terminal for automatic recovery. A new TUI cannot borrow a
retired connection's replies; saved transport fallback still uses existing channel
account/endpoint validation, not group broadcast.

## Cancellation fences

Output journal `xdog.claw.outputs.v2` includes cancelled unsent records. The prior
v1 format is refused byte-preserving before recovery; no upgrade is performed.
Pending-only cancellation is scoped and idempotent, never changes actual sending,
delivered or unknown facts, and does not cancel model/input/worker execution.
A prior positively not-sent attempt retains its real ID/error/history after
cancellation. Cancelled outputs cannot resume or become a new output identity.

A transport policy is checked before beginning a send and again after the real
begin commit/before entering the sender. Cancellation there records known-not-sent
then cancelled, not unknown. Policy IO/capability disappearance stays pending.
Once the sender is entered, cancellation cannot undo it or rewrite its outcome.
Cancellation of another pending row does not wait behind a network sender. A
stale selected pending row that was explicitly cancelled is skipped without
failing the Agent; storage/identity failures are not disguised as cancellation.

## Evidence and remaining scope

Actual new+existing Core output/input focused suites48 passed before the final
whole-nine gate. Includes real Weixin channel with only external API transport
substituted: original alice/context/output ID survives restart and policy absence,
while a later bob cache/resolver is not used and no model replays. Real Core stores
prove cancellation, attempt histories, v1 refusal, callback/commit races, canonical
conflicts and maximum metadata/image boundaries. Protocol/store/sender owners are
not mocked; model/transport boundaries are scripted.

Current whole-nine/type/style verification is recorded by the plan ledger after
terminal results, not inferred from focused tests. ManagedObservers still has its
aggregate receipt.response sender at this foundation stage; removing it and using
actual per-message Core facts, independent report intent and cold artifacts is
mandatory before accepting IN-08 or the overall objective.

## Final Core foundation gate

All9 unfiltered paths using the recorded short /data TMPDIR/UV_CACHE_DIR/
TMUX_TMPDIR and `scripts/test.py --jobs 2`: **6856 passed,34 skipped**,164.16s.
Claw1033, Agent1929/15 skipped, Coding1251/12 skipped, TUI691, Goal78, Flow813,
AI729/7 skipped, Eyes290, Site42. Strict9 mypy595, Ruff packages, Flow17 and
lock/diff pass. Logs `observer-output-core-{root9,strict9,final-focused,flow17}.log`
under `/data/t.Vy5SaQ/`. The earlier cancellation-only6843 suite is a prior
snapshot, not substituted for this newer source. Original default4 timing and
root-full observations remain documented, with no test/timeout exclusions.

No current new-output-format cold artifact claim is made here; the previous
shared UI artifact receipts belong to their own milestone. Source adapter
integration, aggregate sender removal, observer formatv3 and updated cold output
artifacts remain separate mandatory tasks. No whole-goal completion claimed.
