# Common Skills Hub Implementation Plan

> Execute inline with superpowers:executing-plans and TDD. User's prior autonomous
> instruction applies; current checkout only, no new worktree/delegated agents.

**Goal:** Safely exclude .claude, verify Eyes built-in parity, and replace
xdog-goal with a reusable xdog-skills distribution without business changes.
**Architecture:** Domain-local xdog.skills.goal plus common root skills/extensions
using existing generic discovery. Host owners/storage/permissions unchanged.
**Tech Stack:** Existing Python/uv/Hatch/pytest, no additional runtime dependency.
**Spec:** docs/superpowers/specs/2026-10-20-common-skills-hub-design.md

## Global constraints
Keep data/schema/tool/skill/extension identities; no legacy import shim, version
bump, provider/account activity, user-state deletion, push or publish. Bash
login=false; /data/t.Vy5SaQ TMPDIR/UV_CACHE_DIR/TMUX_TMPDIR. Preserve historical
proofs as snapshots. New tests must fail on actual old package/exclusion behavior.

## Review focus
Nested optional-package lookup must not throw; discovery must stay generic and
inert; roles/UI resources must ship at expected paths; business format identifiers
must not be renamed; real archives must exclude local nested .claude state.

### Task 1: Exclude local state and verify Eyes discovery
- [ ] Add real root sdist assertion excluding .claude paths and compare current
  default-manager Eyes/Flow listing/loading without filesystem writes or runtime.
- [ ] Run behavioral RED for archive exclusion; explicit Hatch exclude, retain
  existing Git ignore and directory; run focused GREEN and build archive proof.

### Task 2: Move Goal into common Skills package
- [ ] Add inert hub/resource/layout/optional-package tests, observe old package RED.
- [ ] Move package/domain/tests and root resources; update imports selectively,
  preserve xdog.goal.* wire/data constants. Rename dependency/workspace/extras/CI
  and verifiers, update active README/AGENTS/docs, regenerate lock via uv sync.
- [ ] Run new tests, all actual Goal/Claw/Coding regressions; inspect stale runtime
  imports/metadata/resource selectors. Commit independently verified changes.

### Task 3: Whole workspace and installed evidence
- [ ] Full unfiltered9 with independent SDK fixtures, strict9/Ruff/Flow17/lock/diff.
- [ ] Fresh build all distributions, Twine/source/resource parity and root exclusion.
  Actual cold Skills-only/Coding/Claw/ACP consumers prove resources/no old package.
- [ ] Inline scope audit (no delegated reviewer): preserve owner/permissions/data
  identities, identify limitations, record evidence and keep branch local.
