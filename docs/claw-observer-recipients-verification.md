# Observer original recipients — verification

Date: 2026-10-19. This is a fixed-recipient milestone of the assistant-core
refactor, not whole-goal completion or the final report-streaming architecture.

## Implemented contract

A frozen bounded `ReplyReference` contains scope, original channel binding,
recipient and channel-selected routing context. It contains no prompt, images,
execution identity or permission flag. Metadata is detached on construction and
on projection. It is private host context, not an Eyes definition/tool/provenance
argument. An unadmitted input cannot leave that capability on an idle Agent.

Actual Weixin and Native input routes supply this reference to the same original
Core execution/tool context; the Eyes tool only passes that typed context.
ObserverJournal owns the immutable reference alongside the definition. Duplicate
names with different creator references are conflicts, not retargets. Journal
format is `xdog.claw.observers.v2`: old formats are refused without modifying
original bytes; there is no migration/dual read.

The report worker sends only to that reference. Weixin uses original
account/sender/context even after its latest-peer/token caches change. Stable
chunk correlation IDs retain partial-send facts; uncertain/partial sends are not
automatically retried. Native uses a connection-specific generic notice channel
outside the group-broadcast channel list. Disconnect unregisters it; a new socket
cannot adopt it. A missing original endpoint remains known-not-sent/pending;
no chosen creator destination is not-required, never a guessed group recipient.
Status projections do not expose context tokens or live binding IDs.

Source stop still does not suppress accepted reports. Explicit
`cancel_notifications` stops unsent reports, independently from collection or
Agent execution. Known offline report delivery no longer pins completed source
execution/collection; the source cursor and immutable occurrence outbox remain
transactionally committed. Report recovery never reruns the model.

## Evidence

- `test_reply_references.py`: detached codec, no input/image/authority fields,
  exact schema/version rejection, real SQLite duplicate/name/scope/reopen tests,
  old-format original-byte retention, rejected/failed admission clears capability.
- `test_observer_recipients.py`: **actual model/tool loop and Weixin requests**
  create from Alice, accept Bob later and trigger both scheduler and real file
  evidence; callback still addresses Alice/original context. Tests cover no
  creator destination, forged reply arguments, duplicate name/different creator,
  new application/ACP receiver report recovery with no second callback-model turn,
  partial chunk ACK uncertainty/no replay, and actual Native sockets with the
  original creator online or disconnected versus another same-group peer.
- Existing journal/owner/source/notification-cancel/namespace/permission tests stay
  enabled. Report fixtures now explicitly select their controlled external sink;
  a test sink's deliberate restorability is not production Native reconnection.
  The old test expecting report fan-out to unrelated channels was replaced by
  actual unrelated-channel isolation, plus actual partial targeted-send coverage.
- Full8 root: **6900 passed,34 skipped**, Claw **1228 passed**,142.83s.
  `/data/t.Vy5SaQ/observer-recipients-root.log`.
- Strict8 mypy **576 source files**; Ruff packages passed. Flow examples
  **17 passed**; lock and diff checks passed. No paid provider/user-service action.

## Artifacts

Six wheels+six sdists: `/data/ordist.LE2Ty1`; Twine and runtime Python source-byte
parity passed (`source-parity.json`). Five fresh wheel-only environments under
`/data/t.Vy5SaQ/xdog-acp-isolated.zzsossao` passed the tracked official SDK
stdio/Unix, Coding ingress/owner, Claw Core/observer namespace, output recovery
and Native busy-intake probes. The synthetic explicit output rejection error log
is intentional. No publish, push, merge or user data/cache deletion.

## Remaining full-objective work

Observer reports still aggregate the completed receipt; this is not final IN-08
per-assistant-message streaming. Unify feature report streaming/cancellation with
generic output receipts through the shared Goal host notification/output ports,
without a second transport authority or policy in ACP/Core controllers.

Standalone optional `xdog-goal`, its self-owned storage/async host ports, explicit
review/cleanup semantics, two real host adapters, generic read-only extension
views and one shared Goal UI extension are not implemented by this milestone.
The legacy GoalManager/native Goal UI and inert queue configuration declarations
still need their explicit cutover/removal. Final nine-package, Goal-only/two-host
installed verification and fresh-context whole-branch review remain mandatory.
