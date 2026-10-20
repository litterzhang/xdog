# Common medium-level skills hub

## Intent and boundaries

User requests: ignore/delete local .claude state, verify Eyes has Flow-equivalent
built-in skill discovery, and replace the single-purpose xdog-goal distribution
with a reusable common medium-level skills hub. Use the existing checkout and
prior autonomous implementation instruction, not another approval loop. Do not
remove existing .claude directories/worktrees, publish, push, alter user data,
bump versions, invoke providers or add a service.

Recommended: one optional distribution xdog-skills, code namespace xdog.skills,
with independently owned domain modules. Alternatives rejected: rename only the
distribution while retaining xdog.goal (misleading namespace); move Flow/Eyes
engines into the hub (unnecessary dependency/lifecycle coupling).

## Package layout

packages/skills/src/xdog/skills/
  __init__.py                # inert hub identity, no automatic business imports
  py.typed
  goal/                      # existing Goal models/store/service/host/tools
    __init__.py
    roles/worker.md, reviewer.md
    resources.py             # skill from hub root; roles from goal module
  skills/goal/SKILL.md       # generic packaged skill discovery convention
  skills/xdog-planning/      # existing common planning guidance moved from Agent
  extensions/goal_ui/         # existing generic extension discovery convention

Goal tests move to packages/skills/tests/goal. Hub/discovery/packaging tests sit
at packages/skills/tests. No speculative new skill, registry, plugin protocol,
shared database or task runner. New common skills later add their own slug and,
only when necessary, isolated domain code. The hub depends only on Agent; neither
Agent/TUI nor Eyes/Flow acquires a dependency on the hub.

## Integration and preserved semantics

Replace xdog-goal metadata/dependencies/CI/build consumers with xdog-skills and
xdog.goal Python imports with xdog.skills.goal. Coding/Claw optional extra becomes
skills; all/full includes it. Availability checks probe top-level xdog.skills,
which does not throw if absent and does not import a nested Goal module.

Keep business identifiers except requested skill rename: goal skill, goal tool, goal_context/task_report,
goal_ui extension, view IDs, journal paths, schema strings xdog.goal.*, original
owner bindings, approval policies, exact input/message/attempt identities and
notification semantics. No old import/distribution shim; no data migration or
schema rewrite. Merely installing/discovering the hub starts no model/storage/UI.
Goal code remains owned and lifecycle-bound through existing thin host adapters.

Eyes remains independent and ships eyes using the same namespace-based
packaged_skills and progressive loading as flow-workflows. Add actual discovery,
manager and cold wheel evidence rather than a second registration mechanism.

## Local-state exclusion

Keep existing .claude ignore, add explicit Hatch global build exclusion so root
sdist cannot traverse/include ignored nested worktrees. Verify real archives;
Git ignore alone has already proved insufficient. No directory deletion.

## Acceptance

1. Real root wheel/sdist exclude every .claude path, preserving canonical source.
2. Existing Eyes test and real default SkillManager find/load eyes and
   flow-workflows as packaged skills, no local copies or work activation.
3. New skills wheel contains inert root, Goal domain/roles, goal skill and goal_ui;
   no old xdog.goal module or xdog-goal dependency/namespace in current runtime.
4. Goal formats and actual on-disk history remain unchanged; existing real
   worker/report/reviewer/notice/rotation/readonly tests pass after namespace move.
5. Fresh Skills-only plus Coding/Claw isolated actual consumers work with new wheel;
   import/discovery alone does not start work and both hosts use same UI resource.
6. Full nine configured package paths, strict9 namespaces (xdog.skills replaces
   xdog.goal), Ruff, Flow examples, lock and current diff pass. Record limits and
   do not treat previous assistant-core artifact snapshots as current rename proof.

## Risks and checks

Do not globally replace Goal schema strings with a Python import rename. Move
skills/extensions to hub root so generic discovery needs no nested special case.
Check absence of the optional parent without nested find_spec exceptions. Update
external verifier wheel globs and helper paths; preserve historical proof files
and business receipts as historical rather than retroactively rewriting them.

## User clarification during implementation

Rename Eyes packaged skill eyes-observers to eyes in its OWN independent package;
rename the hub Goal skill goals to goal. No aliases. Flow/Eyes runtime or skill
resources must not move into xdog-skills. The hub includes medium-level skills
and their extension/support code, with Goal as the first domain. Preserve plural
Goal view keys and namespaced UI commands: those are not the skill slug.

## Source-discovered completion details

Existing xdog-planning is common medium-level guidance and moves unchanged from
Agent into the hub; its name remains xdog-planning. No new skill is invented.
Flow’s tracked development .claude/skills is excluded from its OWN artifacts as
well as root archives, without deleting the local/tracked file. Both changes
have actual pre-change failing discovery/archive regressions.
