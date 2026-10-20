# Common medium-level Skills hub — current verification

2026-10-20. This is the package/resource/exclusion follow-up after the earlier
assistant-core verification; earlier artifacts/paths/slugs are historical snapshots.
Current runtime/resource commit: `92a6a76`. Exact hashes and isolated environments:
`common-skills-hub-artifacts.json`. No version bump or PyPI release was performed.

## Delivered layout

- `xdog-skills` / `packages/skills`: common medium-level skills and their support
  and extension code, not another harness/daemon/engine.
- Goal domain: `xdog.skills.goal`; root `skills/goal/SKILL.md`; shared explicit
  `extensions/goal_ui`; domain-local worker/reviewer roles.
- Existing `xdog-planning` moved byte-for-byte unchanged from Agent into this hub.
  Agent retains only generic skill discovery/loading and low-level access/runtime.
- Independent `xdog-eyes` ships skill **eyes** (formerly eyes-observers).
  Independent `xdog-flow` retains flow-workflows. Neither engine nor their skills
  are copied into the hub; Skills depends only on Agent.
- Default SkillManager discovers installed resources without copying/registration.
  Metadata is listed automatically, full skill bodies load on demand. Root import
  or discovery starts no Goal service/model/observer/UI. Display remains explicit.
- Coding/Claw extra is `skills`; optional parent absence is safe. No old distribution
  or `xdog.goal` import facade is provided. Separate installations should remove
  the obsolete xdog-goal package before installing xdog-skills; otherwise duplicate
  old/new goal_ui providers intentionally fail generic ambiguity checks.
- Skill names are goal and eyes. Plural business view keys, Goal journal paths,
  formats, tool identities, permissions and exact execution ownership are unchanged.

## .claude exclusion

Local root .claude remains intact and already had a Git ignore entry. Explicit
Hatch exclusions now protect root AND Flow artifacts (Flow has a separately
tracked development .claude/skills resource). Actual20 wheels/sdists contain no
.claude member. No local worktree or developer skill file was deleted/untracked.
Root and Flow real archive regressions both pass, not just a grep of .gitignore.

## Behavioral RED to GREEN

- Root sdist exposed local worktrees: RED1 +Eyes2pass; explicit exclusion GREEN3.
- Requested new hub/skill identities: RED7, then new Skills/Goal/Eyes106 passed.
- Genuine host worker/report/readonly/review/owner lifecycle:54 passed.
- Existing planning still resolved to Agent: actual path RED1; moving unchanged
  resource resolves from hub. Flow archive leak RED1/root1pass; exclusion fixes it.
- Combined follow-up actual archives/access/discovery/metadata:50 passed. Task2
  full related Skills/Eyes/Claw packaging final:115 passed.
- Initial full run failed two stale Claw Provides-Extra assertions expecting goals.
  Corrected exact expected set to skills and strengthened independent skills/eyes
  marker checks. No runtime guard, deadline or permission assertion was weakened.

## Final source and installed proof

- Unfiltered nine configured test paths, jobs2, independent MCP SDK fixtures on:
  **7085 passed /7 skipped /241.38s**. All27 optional SDK cases ran; seven real
  Codex/Claude CLI opt-ins remained intentionally disabled. No paid/provider call.
- Strict9 mypy:605 source files. Ruff packages/scripts, Flow17, lock/diff pass.
- Ten distributions/20 wheels+sdists freshly built after final resource move;
  Twine20 passes (existing Site description warnings). Exact645 canonical
  source/resource members +29 Flow examples match current wheels and sdists;
  root canonical wheel and all nine canonical sdist trees match as well.
- Four fresh outside-checkout consumers pass: Skills-only (inert import, default
  goal+xdog-planning discovery, real worker/acceptance/independent review/store/UI
  without installed/imported hosts/TUI/Flow/Eyes); actual Coding and Claw base
  then optional Skills (real permissions/worker/reviewer/notices/owner/UI rotation);
  Eyes/Agent/Coding/Coding-owner/Claw ACP/Native/original-output recovery.
- Additional clean combined wheel environment discovers goal/xdog-planning from
  Skills, eyes from Eyes and flow-workflows from Flow. Old slugs absent, no skill
  copies or business activation from discovery. A separate old immutable artifact
  interpreter creates a fresh same-format journal using xdog.goal; current
  xdog.skills.goal reads exact original history/evidence/notice ID, recovering the
  entered notice as unknown, with no migration/replay. This is NOT legacy goals.json
  upgrade support; unsupported old formats still fail closed.
- Both real hosts use unchanged aggregate Goal UI digest
  2959d6de1764806ef3b078b0149d3f633164b4b4a37679eda47a09c40a90f3c7.

## Scoped independent review

Reviewer Copernicus, agent01a156db-9293-7991-a090-07877ebcbe42, read-only range
`ebecb87..92a6a76`: **zero Critical/Important/Minor findings**. No edits, builds,
full-suite reruns, extra worktrees/agents or real providers. Reviewer inspected
current source/diffs and concrete final gates/artifact/cold logs and independently
reported final7085/7 log completion. Reviewer closed after its result.

Declined-to-judge rulings: prior assistant-core branch review is outside this
new bounded task (cost: no second independent review of earlier history; current
full tests still cover its runtime). Plan-only uncommitted clarification is audit
bookkeeping, inspected by main (cost: no separate reviewer for planning prose).
Live publishing/provider credentials remain unexercised (cost: no external-account
or production-provider conformance claim).

## Other rulings and limits

1. Existing planning belongs in the common medium-level hub, and Flow developer
   state stays local but not in archives. Cost: planning now requires optional
   Skills installation, documented in active Agent/Coding/Claw READMEs.
2. Corrected a self-imposed no-reviewer plan sentence to follow the applicable
   required final-review skill. Cost: one bounded read-only review while main
   completed proof; no repeated review loop or implementation delegation.
3. User subsequently explicitly authorized commit/integration/push to main,
   superseding earlier local-only constraint. No tag or PyPI upload is authorized
   by that integration step; publishing configuration/setup instructions are a
   separate user-requested follow-up. Never force-push or bypass branch protection.

No new deferred minors from this scoped review. Earlier assistant-core historical
minor records were not silently changed or misrepresented as part of this task.
