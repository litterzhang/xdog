# Claw input foundation checkpoint — 2026-10-19

This is an intermediate dependency of the assistant-core refactor, **not delivery
of unified ingress or completion of the47-item goal**. Existing ACP/channel/runtime
consumers are not switched yet. No second consumer/service was started and no user
data or service was touched. All objective acceptance items remain pending.

## Implemented and exercised

- Protocol-independent immutable input envelope: stable content, explicit scope,
  original reply target, separately bounded images and host-only authority fields.
- Private single-owner sqlite intake and execution-attempt journal; no Goal/Eyes/ACP
  lifecycle imports or business tables in this new store.
- Durable duplicate ACK, conflicting ID refusal, full-capacity rejection without
  deletion, ordered scope isolation and one claimed/running attempt per scope.
- Separate claimed/running boundaries, explicit unstarted withdrawal, new retry
  attempts retaining history, stale attempt rejection and actual context binding.
- Pending/claimed recovery versus interrupted running; running is never retried by
  duplicate delivery or reopening storage.
- True second-process lease exclusion and ungraceful process exit (`os._exit`), not
  mocked restart. No hardware power-loss claim.
- Real sqlite transaction paused at commit, repeated awaiting-task cancellation,
  concurrent close and second-owner attempts. Disk error wins over caller cancel;
  successful late commit remains queryable after the canceled ACK.
- Existing invalid permissions/symlinks, unknown formats and logical corruption
  reject without rewriting original database bytes or repairing permissions.

TDD:16 envelope cases failed before the module;11 initial store cases failed before
InputStore. Ownership tests initially showed4 genuine logical-corruption failures
and15 characterization passes. Fix validates payload/identity/status/attempt facts
read-only before *any* recovery write. Additional permission/image-identity cases
were retained. Final foundation tests: **50 passed in1.82s**.

## Verification at this checkpoint

- Whole-root `uv run python scripts/test.py`: **6723 passed,34 skipped**,
  all8 configured paths completed in96.73s. No new skips were introduced.
- `uv run ruff check packages`: passed.
- Strict mypy over all8 namespaces:565 source files passed.
- `git diff --check`: passed.
- Fresh Claw wheel/sdist both include all3 new input modules:
  `/tmp/claw-input-foundation-dist.SG9YVn`. This is packaging presence, not a
  fresh-environment ingress or Goal interoperability claim.

Full root output and per-task RED/GREEN/ownership rulings are retained under this
plan's `.superpowers/sdd/2026-10-19-claw-input-foundation/` workspace until the final
whole-refactor review. Relevant permanent tests:
`packages/claw/tests/test_input_envelope.py`, `test_input_store.py` and
`test_input_store_ownership.py`.

## Still required

Application consumer/GroupController, durable TUI/Weixin handoff, thin ACP,
independent managed-observer storage, optional standalone Goal plus real Coding and
Claw adapters, generic extension views/shared Goal UI, removal of old active paths,
and final nine-package/artifact/independent-review gates. None is implied by this
foundation checkpoint or its green source suite.
