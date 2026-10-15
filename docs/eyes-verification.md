# Eyes implementation verification — 2026-10-15

Spec: `docs/superpowers/specs/2026-10-14-eyes-design.md`.
Plan: `docs/superpowers/plans/2026-10-14-eyes.md`.
Implemented in the requested checkout `/data/workspaces/pyspace/xdog`; no worktree,
merge, push or publication was performed.

## Requirement evidence

| Requirement | Implementation / authoritative checks |
| --- | --- |
| Flow-like independent package | frozen models, strict loader, async executor, adapters, argparse CLI; eyes declares no runtime dependencies |
| Sources/handlers through a skill | packaged `eyes-observers`, JSON example, existing packaged-skill discovery/loader test |
| Coding does not know sources | coding imports no eyes modules; generic protocol, inbox, binding and mode owners |
| Actual file changes activate handling | real file/runtime/Unix transport test and normal SDK end-to-end custom-history/model-input test |
| Evidence not instructions | registered handler instruction stored separately; delivery cannot replace it; canonical external message projects labelled untrusted data |
| No new agent loop | native normal turn path and RPC `_handle_prompt`; full prompt/compaction/permission/persistence paths retained |
| Baseline, content changes, noise | scanner/detector tests for same-content saves, net changes, rename, debounce/max-delay and source error recovery |
| Confinement and limits | no-follow descriptor scans; regular-file/file-count/file-size tests; explicit overflow and bounded presentation |
| Session/branch lifecycle | generation-specific private descriptors, navigation revocation, context retirement and cancellation tests |
| Duplicate and retry policy | real lost-ACK client test, immutable delivery IDs, thread-serialized host admission and 4096-ID no-eviction test |
| Self-edit loop prevention | end-to-end model stub edits its watched source; exactly one turn, later evidence queued and budget paused |
| Resume permissions | actual PermissionManager ask/deny fail closed; explicit allow-all permits; end-to-end runtime resume deny/allow checks |
| Independent process | real CLI start/stop and concurrent-start tests; one runtime/registration; last stop retires socket |
| Heartbeat ownership | injected-clock five-second retries and fifteen-second failure test; descriptor revocation stops runtime without replay |
| Resource cleanup | scan-thread cancellation, incomplete socket shutdown, active delivery cancellation, source/context shutdown tests |
| Installed artifacts | wheel and sdist build; Twine; isolated no-deps install; import/CLI/skill/example/license/py.typed inspection |

## Fresh verification results

- `uv sync`: resolved 115 packages; environment synchronized.
- `uv run python scripts/test.py`: all eight configured paths passed;
  **5,837 passed / 67 skipped**, 54.16 seconds. Package counts: eyes 95, ai 813,
  flow 583, tui 688, site 42, agent 1766, claw 917, coding 933.
- `uv run ruff check packages`: passed.
- Strict mypy for all eight namespaces: passed, 589 source files. Existing unused
  override notes are not newly introduced type errors.
- `uv run xdog-flow test packages/flow/examples/ --allow-script-stub`: 17 passed.
- Shipped eyes example: actual CLI validation passed.
- Skill frontmatter validator: passed; package skill discovery and loading passed.
- `uv build --package xdog-eyes`: wheel and sdist built.
- `uvx twine check`: both artifacts passed.
- Wheel installed with `--no-deps` in `/tmp/xdog-eyes-isolated`; isolated Python
  import and CLI validation passed, with no ai/agent/coding/claw/flow present.
- CI YAML parses and includes eyes import, tests, strict types, example/wheel checks.
- `git diff --check`: passed.

The test model is local/deterministic: no live paid/provider request was needed.
Skill reference examples and their runtime/permissions contracts are validated;
no separate autonomous-agent pressure benchmark was run. Final review was inline,
consistent with the approved no-subagent plan.

## Deliberate boundaries

Only local Unix file polling and current-session notifications ship here.
Definitions are reusable, activity is ephemeral; no crash replay, automatic
coding startup, API/web sources, script handlers, Windows transport or claw/flow
adapters are advertised. Polling may miss transient changes; historical file
contents are not stored. Permission gates/private same-UID capabilities are not
an OS hostile-code sandbox.

Publication requires externally configuring the `pypi-xdog-eyes` environment and
PyPI trusted publisher. The automatic publish matrix is intentionally not
expanded before that provisioning; users can select eyes for the existing manual
publish workflow after setup. No publication or external provisioning is part of
this implementation.
