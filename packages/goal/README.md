# xdog-goal

Optional host-neutral Goal contracts and storage. Direct dependencies are
`xdog-agent` and Python's standard library; installing/importing the package does
not start a model, receiver, observer, worker or UI.

This package is being implemented under the approved assistant-core design.
Claw and Coding reuse the same kernel through small host adapters. Shared UI
cutover and final acceptance remain in progress. Package presence automatically
provides their built-in skill and Goal capability without an enable setting;
availability is not delegation to create a Goal or worker.
No legacy Goal data is imported or upgraded. A host supplies an opaque owner and
workspace; the package owns its private versioned storage and refuses a different
owner, corruption, unsupported format or concurrent writer.

The kernel provides explicit GoalService operations, bound worker reporting,
independent evidence-backed completion review, original role context and
disposable workspace views. Parent actions cannot submit a reviewer verdict.
Task submission is not acceptance, and acceptance is not Goal completion.
Only explicit request_complete starts review; status/idle/turn end do not.

Packaged session-scoped `goals` skill and worker/reviewer roles describe the
explicit workflow. Tool operation authority and readonly boundaries are supplied
by the host; instruction text or source labels do not grant permissions.
See `docs/goal-kernel-verification.md` in the repository for verification and
remaining shared UI/final acceptance work.

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
`/ext-goal_ui-goals [GOAL_ID | page OFFSET]`; `/goals` still loads the packaged skill.
Missing/malformed views say unavailable, never zero Goals. It imports no host,
store or TUI and exposes no mutation commands. The normal shared TUI ExtensionView
renders its generic status output; the main application needs no Goal widget.

The actual Coding and Claw adapters now bind this same display implementation to
their real owners. Coding follows exact session/branch scope; Claw follows stable
group data across context rotation. Actual children do not inherit the parent's
view grant. UI failure/unload never closes business storage, cancels work or stops
notices. A restricted generic `display_only` manifest profile gives this worker
no validation/tool/provider authority, so its failure cannot veto execution.
Cold installed same-extension validation and final architecture acceptance remain
in progress. See `docs/shared-goal-extension-verification.md` for source/runtime
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
