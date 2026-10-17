# Behavior-preserving code reuse refactor

Date: 2026-10-16. Base: `b6492827fbf8812052a12775a65aba974e4aad7a` (merge pushed).
**Complete in working tree. 3 requirements completed, 0 remaining: 0待实现, 0待验证, 0缺外部环境, 3已完成, 0延期.**
Authoritative per-item scope/acceptance/status/evidence: `code-reuse-requirements.json`.

## Frozen first-pass scope

- **RF-001:** One typed, stdlib-only owned-thread primitive. Remove generic disk-work imports from feature-specific extension/image modules; preserve legacy image helper callable compatibility and cancellation-first semantics.
- **RF-002:** One reusable error-first owned Task/Future join. Replace only equivalent loops in actual ACP/gateway consumers; keep locks, registries, shutdown order, group/session policy and close-task ownership local. Do not conflate transaction result precedence with cancellation-first image/disk delivery.
- **RF-003:** One pure validated canonical session-input text renderer reused by model, ACP and transcript reading. Avoid unnecessary encode/decode cycles without loosening validation, flattening structured history or changing display policy.

This finite pass does not delete supported features, reopen previous UI goals, add dependencies/frameworks, merge distinct routing policies, or claim all possible cleanup is finished. Eyes stays independent; TUI remains policy-free. No new cross-platform adapters/verification.

## Why these boundaries

Actual consumers import `_owned_disk` from `extensions.runtime` for unrelated session persistence/navigation and compaction, or image-copy helpers for generic durable work. Identical shield/join loops recur in both ACP layers and gateway cleanup. Those loops encode different precedence contracts: extracting one indiscriminate helper would change cancellation behavior. Canonical input rendering additionally performs redundant validation/serialization cycles at Claw intake and transcript reads; one neutral renderer can preserve the exact untrusted-input banner and JSON text.

Preserve permission/read-only gates, approvals, explicit credential operations, cancellation/close lifecycle, persistent IDs/ACK/history boundaries/no replay, native chat/MCP/extension internals, SDK snapshots/skills/compaction, and Flow parity/licensing. Preserve original unrelated `docs/agent-ui-features.md` and `workflows/` bytes; no stash/reset/clean/add-A.

## Validation and completion

Establish focused characterization and deterministic concurrency/cancellation tests before extraction. Each item needs real migrated consumers and evidence, not cosmetic changes or hypothetical reuse. Then freeze and run all eight configured `scripts/test.py` paths with existing authorized independent MCP SDK enabled, Ruff, strict mypy all eight packages, Flow examples, offline lock, fresh offline builds and API/import/source/README artifact checks. Existing optional external CLI skips are disclosed, not counted passed. No retained security-test deselection.

No installs, live provider/credential operations, service restarts, destructive migrations, version bump/tag/release, or automatic commit/push for new refactoring. The user authorized pushing the previous merge; it is already pushed. New goal completion requires all three rows and final gates, with exact remaining counts updated here and in the JSON ledger. Evidence: `/tmp/xdog-reuse-refactor/`.

## Historical implementation checkpoint (superseded by final verification)

All three candidates are implemented, not yet accepted as complete. Baseline
characterization passed before extraction (ownership16, canonical input19);
post-extraction unit48 and focused integration178 passed. Ruff and strict mypy
all8packages (547sourcefiles) pass. A strict typing probe confirms ParamSpec
positional/keyword-only arguments and exact result types without casts; a now
redundant navigation-result cast was removed.

`async_work.run_owned` has32direct call sites. `wait_owned` replaces8equivalent
owner loops (plus its reuse inside the canceled-thread settlement); no generic
consumer still imports `_owned_disk` from extension runtime or a child-image
helper for unrelated disk/validation work. The legacy image helper remains a
thin one-argument forwarding function with its original name and signature. The canonical formatter has strict typed/serialized entry points
and is shared by model projection, Claw ACP intake and transcript reads; each
projection validates once. UI redaction/bounds remain at display call sites.

Intentionally not unified: Eyes' standalone lifecycle; Coding inbox's error-
consuming `_settled` drain; extension temporary-file preparation that must recover
and close partial resources; and distinct Coding session/branch vsClaw group
routing/lease policies. No false "one helper fits all cancellation" abstraction.

Final frozen full regression, offline build and artifact checks remain required.

### Signature compatibility audit

The first all-eight-path run passed6002cases and all gates, but final review
found that a callable alias broadened the image helper's introspection signature.
The alias was replaced with a thin forwarder preserving the exact legacy
`settled_image_work(work)` name/module/signature as well as behavior. Both unit
and wheel-only probes now assert that compatibility. No worklist item was added
or removed; all final gates are rerun on the tightened candidate.

## Final acceptance — 2026-10-16

**Exactly3completed requirements,0remaining.** Pending implementation0, pending
verification0, missing external environment0, deferred0. No old UI requirement or
protected invariant is counted as a new refactoring item.

| ID | Accepted result |
| --- | --- |
| RF-001 | Typed `run_owned`,32direct call sites; no generic extension/image private imports; exact legacy image-helper signature preserved. |
| RF-002 |8equivalent owner join loops replaced by `wait_owned`; thread delivery and transaction/close precedence remain distinct; owner locks/task identity/cleanup stay local. |
| RF-003 |One canonical formatter shared by model, ACP and transcript rendering; strict typed/raw entry points, byte-equivalent prompts, no redundant encode envelope; UI policy remains separate. |

Current frozen gates, all rerun after the exact-signature audit:

| Gate | Result |
| --- | --- |
| `scripts/test.py`,all8configured paths,existing independent MCP SDK enabled | **6002passed,0failed,7existing opt-in external CLI skips** |
| Ruff all packages | Pass |
| strict mypy all8namespaces |547sourcefiles,no errors |
| Flow examples |17passed |
| Offline lock | Pass |
| Fresh offline builds |18artifacts |
| Isolated artifact/API/source/README verification |16module imports,retired9modules absent,owned helpers+renderers+legacy signature probes pass |
| Frozen source/test/doc inputs |1335package files unchanged through gates |
| Unrelated workspace preservation |9original unrelated file hashes retained;stage empty |

Production changes touch20sourcefiles including the one new neutral helper
module, with44net fewer source lines. That metric is secondary: the primary
improvement is real reuse and explicit ownership/error contracts. Existing test
sources were not modified;39new tests extend coverage. No dependencies or
supported features were removed/added as a workaround, and no second Agent loop,
new service framework, permission shortcut or replay path was introduced.

7optional Codex/Claude CLI integration cases were not enabled and are not claimed
passed. Non-Linux native validation is outside scope; no live provider, real
credential write, installation, service restart, version bump, tag or release.
The prior merge remains pushed at `b649282`; these new refactors are **not
committed or pushed**.

Per-item evidence: `code-reuse-requirements.json`. Full command outputs, frozen
hashes, reuse audit and artifact manifest: `/tmp/xdog-reuse-refactor/final/summary.json`
and adjacent files. This completes the frozen first pass, not every possible
future cleanup in XDOG.

## Authorized delivery update — 2026-10-17

The later explicit commit/push authorization supersedes the historical local-only
restriction above. Related RF+RR implementation was committed in16b8737 and pushed
to main. The integrated corrections at82a0f9876f39eab3afd2ac2a51e862d33689fbe9
passed exact-commit GitHub run39033187499 (test and secrets,all build/test/static/
example/artifact steps). Full latest local regression:6177passed+7optional CLI
skips,strict550sourcefiles,17examples,18artifacts. See the follow-up ledger for
latest counts and evidence;this does not reopen/double-count completed RF items.
Unrelated features/workflows remain uncommitted;no version/tag/release/restart.
