# xdog-skills

Common medium-level skills with their domain support and extension code. The hub
currently contains **goal** (explicit outcomes, worker reports, evidence-backed
review and a shared read-only display) and **xdog-planning** (ordinary task
planning guidance, previously shipped in Agent). Other common skills can add
independent modules/resources here without becoming another distribution.

Flow and Eyes remain **independent engine packages**, not hub contents:
`xdog-flow` ships `flow-workflows`; `xdog-eyes` ships `eyes`.

## Layout and discovery

```text
xdog.skills/
  goal/                 # Goal domain contracts/store/service/tools and role resources
  skills/goal/SKILL.md   # Built-in goal skill, discovered by existing Agent SkillManager
  skills/xdog-planning/  # Ordinary planning guidance, no separate tool/state machine
  extensions/goal_ui/    # Explicitly configured shared read-only Python display
```

Install `xdog-skills` directly, or use `xdog-coding[skills]` /
`xdog-claw[skills]`. Python Goal APIs are under `xdog.skills.goal` (for example,
`from xdog.skills.goal.service import GoalService`). There is no old
`xdog.goal` import shim or `xdog-goal` dependency.

Package presence makes **goal** available through normal packaged discovery,
without copying into a user directory or setting an enable flag. Skill metadata
is listed automatically; its full instructions are loaded on demand, including
`/goal` in supporting clients. Import/discovery never starts a model, host, store,
observer, receiver, worker or UI. Availability is not task delegation.

## Goal domain

The hub's only direct dependency is `xdog-agent`; it does not depend on either
host, TUI, Flow or Eyes. Each domain owns its business code and data; this hub
adds no common database, scheduler, execution loop or plugin registry.
Claw/Coding reuse the Goal domain through their existing small host adapters.

GoalStore retains the same owner-bound private leased/versioned storage and
`xdog.goal.journal.v1` / `xdog.goal.snapshot.v1` identities. Renaming Python modules
or the skill does not rename stored business formats or existing journal paths.
No legacy data is imported/upgraded. Wrong owner, corruption, unsupported format
or concurrent writer fails closed.

Parent acceptance and independent review remain explicit. A submitted report is
not acceptance, and acceptance is not completion. Only `request_complete` starts
a reviewer; status/idle/turn end do not. Host tool permissions and readonly
boundaries are unchanged; skill instructions and source labels grant no authority.

## Packaged read-only display

The wheel carries `extensions/goal_ui/extension.yaml` and `main.py`. When explicitly
configured as a trusted extension, the existing Agent worker can load it directly
from the package without copying it to a user directory. Installation or built-in
skill discovery does not execute that display worker.

`GoalView(service).read(query)` provides a Goal-owned finite JSON projection:
optional safe `goal_id`, nonnegative strict `offset`, strict `limit`1..32 (default16).
It only reads owner-checked GoalService list/status; it never reconciles work,
drains notices, accepts reports, generates artifacts or starts an Agent. Titles,
outcomes and summaries have UTF-8-bounded, explicitly labelled previews. Revisions
are deterministic and pagination preserves all records. Submitted and accepted
counts remain distinct; no preview is completion/permission authority.

The single Python extension reads a host-granted `goals` view and contributes
ordinary bounded status/command text. Its query command is
`/ext-goal_ui-goals [GOAL_ID | page OFFSET]`; `/goal` loads the packaged skill.
Missing/malformed views say unavailable, never zero Goals. It imports no host,
store or TUI and exposes no mutation commands. The normal shared TUI ExtensionView
renders its generic status output; the main application needs no Goal widget.

The actual Coding and Claw adapters now bind this same display implementation to
their real owners. Coding follows exact session/branch scope; Claw follows stable
group data across context rotation. Actual children do not inherit the parent's
view grant. UI failure/unload never closes business storage, cancels work or stops
notices. A restricted generic `display_only` manifest profile gives this worker
no validation/tool/provider authority, so its failure cannot veto execution.
Cold installed same-extension validation is exercised by both host consumers. See `docs/shared-goal-extension-verification.md` for source/runtime
evidence and limits. Headless Goal tools and durable notices remain independent.

## Cumulative state and bounded data reads

Authoritative Goal revisions retain accumulated tasks, attempts and reports
without an aggregate business byte/count quota. Individual field/report/evidence
safety checks and the256KiB tool frame remain. Large responses use an explicit
`xdog.goal_data_page.v1` envelope with a readable canonical JSON fragment `text`,
selector, `sha256`, byte `offset`, `total_bytes`, `next_offset` and `next_query`.
Each raw fragment is at most64KiB. Join exact bytes in offset order and verify
the checksum/total before treating the data as complete.

A mutation response marked `snapshot_committed` is successful: retrieve its
remaining data rather than replay the action. `goal` action `read` takes an
explicit Goal/version plus optional offset/limit; current later changes do not
replace that historical snapshot. Lists use a revision-fenced continuation; a
changed collection is an explicit error requiring a fresh listing, never a torn
merged list. Small unpaged complete responses retain their existing JSON shape.

Bound worker/reviewer `goal_context` accepts only section/offset/limit and always
uses its original role contract/version. The same read-only host/extension/MCP
gates still apply. This does not add a journal format upgrade, second store,
business polling/model loop or automatic task delegation. SQLite/machine storage
limits remain physical constraints, not a package-imposed cumulative quota.
