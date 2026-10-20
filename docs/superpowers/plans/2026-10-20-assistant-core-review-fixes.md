# Assistant-core wholebranch review fixes — single TDD pass

> **For agentic workers:** superpowers:executing-plans inline. One authorized fix
> pass for the existing final reviewer; no second reviewer or implementation agent.

**Goal:** Correct all five accepted Important findings before final acceptance.
**Spec:** Original2026-10-19 assistant-core spec/all47, parent final-acceptanceTask2;
`docs/claw-assistant-core-independent-review.md` records actual reviewer/grades.
**Architecture:** Keep existing Core receipt/controller/dispatcher and Goal service.
Bound aggregate receipt display explicitly rather than faulting completed business
work. Terminal/advisory capacity events belong to Core, not Goal policy. Actual
Goal/outbox/output transports remain independent, original-ID and owner fenced.
**Stack:** Existing Python/uv/SQLite/async/frozen models, real pytest processes.

## Global constraints
Same checkout; no worktree/push/merge/publish/user data/cache/service/credentials
changes or real providers. /data temp/cache roots, Bash login=false. No new Goal
flag/model loop/message approval/automatic effect replay, no weaker permissions,
no implicit worker/source/notification cancellation, no fixed business quotas.
Minors stay deferred; all reviewer Declined items retain explicit spec rulings.

## Review focus
1. Result projection must be explicit and truthful, preserve real complete history
   and terminal state; genuine store corruption/commit failure still fences scope.
2. Goal abandonment wins races without killing an actual independent reviewer or
   auto-resuming autonomous work; fact/evidence retention remains separately visible.
3. Readiness/capacity triggers are owner-bound/coalesced and stable-ID receipt first;
   no active-Goal polling, standard prompt resend, sender retarget or duplicate send.
4. Terminal callback retirement must cover detached/withdrawn pending input and GC
   attachments without removing a successor's call or releasing its group ownership.
5. Final installed output probes must use actual gateway readiness, not a manual
   recovery call that would conceal the production path.

### Task 1: Fix I-1 and I-5 terminal receipts/live-call lifetime
- [x] Actual >1MiB successful aggregate and UTF8/error projection + successor
  regressions; actual queued native cancel and detached callback/attachment weakref
  regressions. Observe correct RED before source changes.
- [x] Use existing terminal receipt path with explicit bounded projection/reference
  to durable session transcript, no false storage failure or schema migration.
  Retire exact original live-call on terminal withdrawal; preserve other attempt
  and running producer fences. Keep actual store failures fail-closed.
- [x] Existing Inbox/native/admission/outputs/lease/no-replay suites and fresh
  focused regression. Record actual RED->GREEN, commit; no overall completion.

### Task 2: Fix I-2 terminal Goal authority
- [x] Real SDK reviewer held through abandon; passed/continue/blocked/failure
  outcomes and concurrent final-check scenario RED as applicable.
- [x] Keep abandoned/completed state authoritative under final locked apply while
  retaining late review facts/evidence and original reference; no implicit cancel.
- [x] Goal review/service/store + actual two-host review suites; commit proof.

### Task 3: Fix I-3/I-4 readiness and capacity obligations
- [x] Actual GatewayServer restart Eyes disabled with durable pending original
  Weixin output, no manual resume/new input/model. Actual Claw child report under
  sustained Inbox cap1 then release, cancellation/shutdown. Observe RED first.
- [x] Owned output recovery after actual channel ready. Generic Core terminal/capacity
  advisory hook drives coalesced Goal outbox receipt-first idempotent retry, scoped
  owner/lifetime subscription. No Goal/Eyes policy in ACP/Controller or new polling.
- [x] Update installed regression consumer to exercise real production gateway path.
  Preserve known-unsent/unknown and no arbitrary Native peer destination.
- [x] Full unfiltered9 with pinned independent SDK(optional27 now explicitly tested),
  strict9/Ruff/Flow17/lock/diff, focused gate and commit. Return parentTask2 grade/
  fix receipt then final Task3 rebuild/Twine/parity/real isolated consumers after
  last runtime change; all47 actual verdicts before goal complete. No re-review.
