# Shared packaged Goal display — projection/resource milestone

> Command naming update: the current display command is `/goals`; the skill is `/goal`.
> See `docs/extension-public-commands.md`. Qualified names below are historical/internal identities.

Date: 2026-10-19. Scope: Task2 of the approved shared Goal extension plan. This is
**not yet proof of actual Coding/Claw view bindings or final whole-goal acceptance**.

## Boundaries implemented

- Generic Agent selected-extension lookup checks the explicit configured root
  first, otherwise installed namespace `extensions/<configured-id>` resources.
  It copies nothing and starts no process during lookup/constructor. Only actual
  initialization of an explicitly configured trusted ID runs its Python worker.
- Corrupt explicit resources and declared/dangling links cannot fall back silently
  to another implementation. Multiple distinct installed candidates for the same
  ID fail rather than select by import order. Physical aliases are deduplicated
  without changing the configured basename used by the existing manifest check.
- The Goal-owned `GoalView` uses only owner-checked GoalService list/status reads.
  It accepts safe goal_id, strict offset and limit1..32 (default16), refusing writes,
  arbitrary paths/methods/owners and coercion. It returns deterministic revision,
  actual statuses, separate task state counts, UTF-8-bounded labelled previews,
  total and continuation offset. It writes no Goal history/outbox or work intention.
- The same shipped `goal_ui` Python worker uses only `api.read_view("goals")`,
  bounded status text, namespaced query command and session_start/view_changed
  refresh. It imports no TUI, storage or harness and executes no Goal mutation.
  Hint values are not truth. Unavailable/malformed snapshots are not empty Goals.
- `/goals` queries the display; `/goal` remains skill loading. Existing
  configured ID and manifest identity rules are unchanged; no new renderer or
  plugin framework is introduced. Unload clears UI, not the Goal/service state.

## Real evidence

Initial missing resource lookup/projection/worker: **21 RED**, one pre-existing
local-corruption behavior already passed. Implemented cases22 green. Additional
malformed snapshot and actual submitted report checks preserve submitted !=
accepted != completed. One report fixture initially omitted required evidence;
it was corrected to an actual workspace evidence file, not a Goal policy change.

A real namespace package link then exposed a separate RED: resolving the selected
path before manifest validation let `alias_ui` run an `other_ui` manifest. The
resolver now preserves selected lexical identity and uses physical resolution
only for duplicate detection. Actual wrong-identity worker activation is refused.

Combined generic view/resource/projection/actual-worker suite: **41 passed**,
3.07s. Tests use real temporary namespace packages, actual readonly GoalStore,
the existing worker subprocess and owner-loop bridge; no fake ExtensionRuntime
or mock Goal storage. The controlled Goal Host boundary used for projection unit
setup is not presented as actual Claw/Coding adoption. Business host ports remain
idle during display reads; actual original Goal stays active after UI unload.

Logs `goal-ui-package-{red,alias-red,final-focused}` and strict/root verification
logs are under `/data/t.Vy5SaQ/`. Actual two-host views, stale-owner UI cleanup,
child scope denial, UI failure while a real worker completes and rebuilt cold
same-extension artifacts remain Tasks3/4. Overall acceptance remains open.

## Task2 current source gates

All9 unfiltered paths via `scripts/test.py --jobs 2`: **6808 passed,34 skipped**,
158.59s. Agent1918/15 skipped, Goal78, Coding1245/12 skipped, Claw1007, Flow813,
AI729/7 skipped, TUI686, Eyes290, Site42. Strict9 mypy593 source files, Ruff
packages, Flow17 examples, lock/diff passed. No test deadline or selection was
changed. Earlier default4 timing observations remain in the Claw cutover record.
Logs `goal-ui-package-{root9,strict9,flow17}` preserve actual commands/results.
These are source/runtime-worker gates; Task4 still must rebuild and verify the
same resource through both real installed harness adapters.
