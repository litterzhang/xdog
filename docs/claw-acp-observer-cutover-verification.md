# Thin ACP / managed observer cutover — 2026-10-19

This completes the actual adapter/journal switch, not the whole assistant-core Goal.
The old source implementations `acp/inbox.py` and `core/observers/store.py` are
deleted, without touching user data, importing old databases or providing upgrades.

## Runtime boundary now exercised

- ACP backend owns only target/peer bindings and prompt waiters. It delegates
  enqueue, wait, namespace and status to the existing application service. It has
  no database, consumer pump, observer/Goal lookup or report policy.
- Namespace/status are identical for native and ACP submissions. Receiver detach/
  restart cannot close core storage, stop the Agent or relabel running interrupted.
- ManagedObservers accepts the application, owns ObserverJournal and uses the
  unchanged generic Eyes ACP handler for actual intake. Its cursor+whole immutable
  outbox transaction precedes original-ID idempotent handoff to core.
- Report reconciliation and live display are application/capability subscriptions,
  not ACP completion hooks. Known-not-sent original-attempt reports recover without
  rerunning Agent; unknown sends stay needs-review. Actual process replacement,
  namespace corruption, late attempts and immutable pending evidence remain tested.
- Source cancel stops collection/unsent transport only. Accepted work and its
  reports continue. `eyes.cancel_notifications` separately stops unsent reports
  without stopping execution/source. Explicit core withdrawal is separate.
- Generic bootstrap pause accepts durable intake but waits for configured features
  before consumption; this is readiness, not message approval. Goal-specific
  legacy hint projection is isolated in a temporary planning adapter, not in ACP
  or core schema, pending the separate optional Goal extraction.
- Session extraction no longer stores the UI fallback `(no response)` as an actual
  model reply. Empty successful execution and failed report delivery remain distinct.

## Verification and failures addressed

Initial3 RED receipt tests reproduced a different ACP namespace, missing native
receipts and interrupted adapter state for still-running core work. They now pass.
Real Unix SDK prompt/enqueue/status/cancel/reconnect and standalone Eyes file/time
interop continue through the sole actual Agent execution path. New readiness and
separate notification-cancel tests were RED before implementation.

Old internal tests now query actual core scopes and independently owned observer
transactions; they do not use compatibility facades. Claim is checked as unstarted,
not aliased to running. Cancellation tests assert accepted work survives source
stop, and explicit withdrawal separately fences admission. The prior old-schema
policy-default test now refuses the unsupported schema byte-identically instead
of reintroducing the user-rejected upgrade path. Fault triggers target the actual
observer journal, not another database. Actual application shutdown is tested
separately from endpoint restart; no safety tests are skipped or loosened.

The first full root caught one remaining Eyes interop test using the retired
internal server constructor and receipt path; updated it to the actual new owner.
A subsequent run exposed two documentation assertions requiring the explicit
unrelated-active-chat guarantee. That guarantee remains true and is now explicitly
stated in both docs. Neither run was treated as a green completion gate.

Final root: **6805 passed,34 skipped**, all8 configured paths103.03s, Claw1133.
No new skips, pending-task or unretrieved-exception output. Ruff packages plus
artifact script passed; strict8 namespaces569 files passed;17 Flow examples and
lock/diff checks passed.

## Fresh installed consumers (actual execution)

Six freshly built wheels + six sdists passed Twine. All runtime Python members of
all12 artifacts byte-match checkout; manifest includes SHA256/member counts:
`/tmp/claw-thin-acp-dist.F8JTXh/source-parity.json`.

The extended `scripts/verify_acp_artifacts.py` ran five new wheel-only environments
outside the checkout: Eyes-only, Agent stdio, Coding ingress, Coding SDK owner and
Claw generic Unix ACP. Final environments: `/tmp/xdog-acp-isolated.m9scgqvl`.
Claw's real socket probe verifies identical namespace, running after endpoint
restart, eventual success, duplicate enqueue/no second model call and independently
closed observer journal. It made exactly one deterministic model call, no paid
provider request. Existing Coding six-independent-input batch and maximum1MiB
continuation probes still execute. These are8-package checkpoint proofs, not the
future standalone Goal-only/nine-package acceptance.

## Remaining whole-objective work

Native pre-acceptance queue/reconnect and Weixin poll-cursor/captured output routing
still need their final channel handoff. Gateway legacy reservation helpers and
optional Goal/default configuration remain to refactor. Goal must own its storage
in `xdog-goal`, be exercised by real Coding and Claw adapters, explicitly request
review/cleanup, and display through one Python extension/shared tui components.
Old Goal implicit policies/native UI and the temporary hint adapter must disappear.
Full nine-package/fresh Goal artifacts and independent whole-refactor review are
still mandatory; no overall completion, merge, push or publication is claimed.
