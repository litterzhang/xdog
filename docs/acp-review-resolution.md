# Independent ACP/session review resolution — 2026-10-15

> **Historical checkpoint (2026-10-15), not the current message contract.**
> Message approval, human wake credit and one-message-only turns are superseded
> by the approved 2026-10-16 unified Inbox/batch design. Recorded past results
> and review findings below are preserved, not relabeled as fresh verification.
> Current protocol: `docs/acp-session-routing.md`; current acceptance evidence:
> `docs/unified-inbox-batch-verification.md`.

Review: one fresh, read-only whole-branch review of
`283d12db873e14d290675d880a4eb0f22bc7ef71..e13c28ed5d4294487b7195e52267b1e8ee2a2a46`,
covering all 21 feature commits and 175 changed files, not only deletion.
Reviewer independently ran 189 focus tests and 4014 four-package tests, inspected
the approved specs/rulings and reproduced five Important issues. No Critical or
Minor findings were reported. The initial verdict was **with fixes required**.

The implementer verified each finding against current source and the independent
reproducers. All five retained Important severity and were fixed in one TDD fix
pass; this is **not** a claim of a second reviewer approval. The approved Native
process uses the fresh review once, then tests/full verification to resolve its
findings.

| Finding | Reproduction / RED | Implemented resolution / GREEN |
|---|---|---|
| F1 SDK-only navigation crossed already admitted branch context | SDK pre-turn initialization held while restoring a saved branch; queued inputs executed in new branch with old records | SDK and runtime controls both use the active session owner. Same-loop sync misuse is refused; threaded navigation reserves that owner, rotates generation and discards old queued work. SDK operation/control refs retain transient owner until cleanup; enabling runtime cannot create a second active owner. |
| F2 public compaction wrote after writer close | Actual summary stream blocked while runtime close returned and another writer acquired the lease | Public async compact reserves the common owner; nested/automatic compaction reuses it. Runtime and SDK-only close cancel and await summary cleanup before writer release; no history commits after close. |
| F3 terminal persistence failure looked successful | Injected terminal fsync failure still returned SDK response, print exit 0, normal RPC end | Preserve persistence cause and check final status even without a cause. SDK raises, print returns nonzero without a false final answer, RPC emits is_error/error while retaining actual partial provider content. Side effects are not asserted safe to repeat. |
| F4 attach answer included load replay | OLD QUESTION + OLD ANSWER + NEW ANSWER returned as one new assistant | Load/replay has no final-answer sink; only current prompt AgentMessageChunk text is collected, not user or thinking chunks. |
| F5 non-off RPC thinking bypassed owner | Real RPC high/etc returned owner-guard error while off worked | All six legal reasoning settings use the same owner-control path, retaining model capability validation. |

Permanent regressions: `packages/coding/tests/test_acp_review_regressions.py`
(17 cases), plus existing SDK/compaction/RPC lifecycle/mode-routing tests. RED logs
preceded production changes; SDK-only and runtime compaction shutdown both tested.

The first full fix run exposed two compatibility failures. RPC direct SDK
adaptation now consumes the same controller result (preserving partial error
content). The earlier transient-owner test was strengthened: new input must not
reuse a closing owner **or write before its close receipt settles**; it proceeds
once that receipt is released, rather than permitting concurrent cleanup/writes.
These are part of the same fix pass, not ignored failures.

## Reviewed items set aside and implementer disposition

- Enqueue ACK loss/concurrent dedup: accepted as already covered by real stable-ID,
  conflict, namespace and concurrency tests. No extra execution was found.
- Listener timeout/PID reuse/inode cleanup: accepted with actual lock/socket/child
  evidence; timeout remains insufficient proof of death.
- Inbox approval context/CAS: accepted as covered; F1's SDK-only gap was separate
  and is fixed rather than hidden by inbox tests.
- Running crash/replay: accepted as interrupted + new reviewed attempt, not exactly
  once tool execution.
- Scheduler after terminal failure: accepted as fail-closed; F3's false adapter
  success is separately fixed.
- Ownerless intake/absent SDK: accepted with import blockers, real stdio/Unix
  persistence and wheel-only environments.
- Hostile same-UID sandbox: not promised by this design; private admission and
  permission gates do not become an OS sandbox. Cost if that boundary is ignored:
  same-user code can subvert state outside these guards.
- Paid provider/external IDE: not exercised, explicitly disclosed. Cost: those
  external integrations need their own deployment smoke tests.
- TCP/HTTP/Windows transport, permanent daemon/broker, API/web/script sources,
  claw/flow-specific adapters and exhaustive filesystem attack audit: approved
  first-release exclusions, not silently dropped requirements.

No Minor issues are deferred. No merge/main rollout, push, PR, publisher or system
service provisioning is authorized/performed by this review.

## Implementation rulings (complete ordered ledger extract)

Preserved here before removing the plan-local scratch workspace. Each entry
records the implementation choice and its tradeoff; these do not expand the
approved first-release scope. Task 1 heading normalization has documentation-only
cost; its temporary enqueue refusal ended with Task 6 implementation.

- Task 1: Ruling: use standard Task N headings while preserving R labels — task-start/task-done scripts only recognize Task headings; documentation-only formatting change, no scope change.

- Task 1: Ruling: delivery=enqueue parses for the approved shared model, but connection explicitly refuses until Task 6 implements durable extensions — prevents a partially migrated config from silently running prompt/model work. Cost: enqueue unavailable in this intermediate commit, required by Task 6 before final delivery.

- Task 2: Ruling: InboxStore reads private session identity JSON without SessionManager/SessionData construction; existing sessions directory may be 0755 but must be UID-owned and non-writable by group/others, while inbox/runtime remain strict 0700/0600. Existing SessionManager intentionally creates the session directory with normal mkdir defaults; blindly requiring 0700 there would reject legitimate existing identities. Cost: session names may remain listable under existing permissions, never writable by others.

- Task 3: Ruling: expose InboxStore.identity(session_id,cwd) as the public SDK/model-free scope read used by status — an empty inbox still needs project/branch validation, so status cannot infer target identity from a first record. Cost: one small read API, no new permission or executable path.

- Task 3: Ruling: include LocalServer.close ordering fix in this task — independent ingress must survive owner exit with active ACP attachments, and the inspected stdlib implementation plus RED tests prove old ordering deadlocks. Cost: shared owner endpoint is touched, verified with bridge and full coding regressions.

- Task 4: Ruling: default InboxStore root is sibling of the actual SessionManager directory — matches get_inbox_dir for default config, but keeps custom/test sessions out of ambient user data. Explicit inbox_store still supports a separately configured shared root. Cost: a custom SessionManager needing a non-sibling inbox must inject the explicit store.

- Task 4: Ruling: retain navigated-away writer leases until every control settlement finishes, rather than release at synchronous SessionData swap — RED real new_session navigation showed the old lease was closed before approval retirement. Runtime-owned on_control_settled releases retained leases; no UI hook is overwritten. Cost: old session cannot be acquired until navigation cleanup actually ends (required ownership boundary).

- Task 4: Ruling: gather all producer/control settlement receipts even when one fails, and retain cancellation settlement — RED proved asyncio.gather's fail-fast behavior exposed a completed control while another cleanup remained live. Cost: failing control waits for the slowest actual cleanup rather than returning early; no second owner or blind restart.

- Task 4: Ruling: failed retirement/endpoint close reports its error only after owner/resource settlement and releases writer leases — RED proved previous Runtime.close returned early on endpoint failure and stranded leases on close-hook failure. Next owner still recovers stale approved/running facts to review/interrupted, never replays. Cost: shutdown errors remain explicit, but cannot be mistaken for still-running/authorized work or permanently hold a stopped owner.

- Task 4: Ruling: InboxStore.approve accepts optional expected records and checks them atomically under the inbox lock — exact content shown at permission time must match execution. RED content swap after approval request otherwise ran a different instruction. Cost: externally edited record requires a fresh review; no permission-rule reuse for changed content.

- Task 4: Ruling: narrow approved-inbox wake credit to the selected request IDs; ordinary human input/authorized global resume still grants ordinary global credit. RED showed an older unrelated ACP request could otherwise consume the specific inbox approval. Cost: an unselected earlier automatic request stays paused until its own legitimate credit; headless explicit ordinary prompts are not starved.

- Task 4: Ruling: recover at serve_local itself, not only Runtime.open_endpoint — existing CodingBackend and SDK tests call serve_local directly. RED publication assertion showed stale approved data otherwise remained when advertising a new owner. Cost: all owner endpoint entry paths perform model-free inbox initialization.

- Task 5: Ruling: support immutable PurePath in generic TUI EventQueue snapshots — frozen InboxRecord carries its cwd, and navigation SessionHistory crosses this queue. RED proved rejection; generic immutable-path roundtrip test passed. Cost: standard immutable path values are now accepted by the standalone TUI queue, with no filesystem I/O or application policy added.

- Task 5: Ruling: use Core inbox_views for bounded summary/receipt projections, cli/enqueue for SDK-free one-shot intake, and RPC inbox helper for request-task lifecycle — these are focused extra files needed to preserve module boundaries, not new agent loops. Cost: a few small modules instead of embedding view/transport policy in controller or inbox store.

- Task 5: Ruling: serialize snapshot publication by owner-loop read-start sequence, without holding an execution lock while painting or introducing storage revisions — RED showed a delayed poll could overwrite a newer succeeded record with pending_review. All reads publish monotonically; stale completed polls return the latest known view. Cost: stale callers receive newer already-known facts, never rewind durable state.

- Task 5: Ruling: readonly /inbox list/show bypass model control reservation (disk reads only, captured owner rechecked) — RED showed a query queued behind a blocked model. Mutation/review still uses controller reservation. Cost: list/show reflects a concurrent read snapshot, not a promise of frozen future state.

- Task 5: Ruling: capture_history drains a pending context rebind under the original control reservation before reading the new inbox — RED delayed real new_session retirement produced old inbox lease errors. This keeps successor turns fenced and snapshots current without waiting on the same control future. Cost: navigation view capture waits for actual registered cleanup, as required by single ownership.

- Task 6: Ruling: add opaque persisted inbox_id to inbox_status — identical endpoint/session/cwd is insufficient to distinguish a replacement dedup store after ACK loss. InboxStore.scope_id uses a private atomic/fsynced UUID marker under a separate flock; per-session scope ID survives service restart but changes on store replacement/reset, without exposing storage paths or granting a capability. Cost: a small private metadata marker and one status field; mismatched namespaces require explicit rebaseline rather than unsafe replay.

- Task 6: Ruling: negotiate automatic_budget from session_info, not merely session_info+resume extension names — bound=False CodingBackend supports normal explicit ACP prompts without owner-managed automatic credit. Coding reports controller.bound; Eyes uses one local wake for false/missing flags. Existing target-owned human/resume budget tests remain true when the flag is true. Cost: third-party peers omitting the flag get conservative client-side credit, never an unlimited observer loop.

- Task 6: Ruling: observer capacity uses the actual prompt or enqueue body, with optional cwd supplied to observe — RED showed single-copy enqueue evidence was wrongly subject to prompt's doubled text+metadata byte cost. Payloads still reserve frame overhead and 256 unique paths/128 pending deliveries; evidence is never truncated. Cost: one optional observe keyword; R7 must pass its target cwd.

- Task 7: Ruling: RuntimeLocation includes the canonical cwd in addition to directory/socket/generation — a one-way directory hash cannot reconstruct a child process working directory or validate a project handshake. Cost: one internal frozen-model field; no harness source or discovery coupling.

- Task 7: Ruling: use transport EOF/process-handle state plus negotiated ACP target metadata rather than the retired fifteen-second custom heartbeat — the previous heartbeat test is replaced by prompt disconnect/no-reconnect and enqueue suspend/recover tests. A timeout does not authorize killing/replacing a listener. Cost: generic peers without metadata rely on actual stream closure; no invented standard ACP ping extension.

- Task 7: Ruling: allow explicit enqueue reconnect while idle via a data-only handler method and observer suspended event — otherwise an idle receiver failure could not resume until another file change created a pending delivery. Cost: one explicit local recovery method and optional observe suspended event; never grants execution credit or automatically reconnects.

- Task 8: Ruling: capability availability is detected without importing ACP; ordinary runtime/Inbox/TUI/RPC/navigation gracefully omit unsupported endpoints, while explicit ACP gives version/SDK guidance — source dependency marker alone cannot prevent implicit endpoint imports in a missing/future SDK environment. Cost: a small availability helper and truthful RPC ready capability; no silently accepting ACP when absent.

- Final: Ruling: enforce full SDK close settlement even when the earlier retirement test permitted new work during cleanup — single writer requirement is stronger than that intermediate expectation — cost: callers wait for actual cleanup; stalled cleanup never licenses another writer.

- Final: Ruling: reviewer set aside enqueue ACK loss/concurrent dedup — accepted as covered by stable-ID/conflict/namespace/concurrency integrations — cost if wrong: intake duplication or uncertainty; never claim exactly-once tool effects.

- Final: Ruling: reviewer set aside listener timeout/PID/inode behavior — retain actual lock/socket/child evidence, not timeout restart — cost: a genuinely stuck process needs explicit operator handling.

- Final: Ruling: reviewer set aside inbox approval/CAS and running crash/replay — existing exact-content/context checks and interrupted/fresh-review attempts stand; F1 independently fixed — cost: unresolved effects require human inspection rather than automatic progress.

- Final: Ruling: reviewer set aside scheduler terminal failure — fail-closed scheduling remains; F3 false-success adapters fixed — cost: failed persistence requires repair and manual effect inspection.

- Final: Ruling: reviewer set aside ownerless intake/absent SDK — real stdio/Unix/import-blocker and rebuilt wheel-only tests cover it — cost if wrong: unsupported environments must report unavailable rather than start models.

- Final: Ruling: hostile same-UID sandbox is not promised — private admission and permission gates remain guards, not OS isolation — cost: same-user hostile code can subvert files/state outside these guards.

- Final: Ruling: live paid provider/external IDE not exercised — deterministic SDK and subprocess checks stand with explicit limitation — cost: deployment needs external-integration smoke tests.

- Final: Ruling: TCP/HTTP/Windows, permanent daemon/broker, API/web/script sources, claw/flow adapters and exhaustive filesystem audit remain approved exclusions — no scope silently dropped — cost: those uses need future implementation/audit.
