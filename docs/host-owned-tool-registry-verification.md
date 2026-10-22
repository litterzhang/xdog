> Historical first-stage evidence: MCP was subsequently separated into McpRegistry for 2.4.8. See docs/release-2.4.8-verification.md for the final boundary and release checks.

# Host-owned tool registry verification

## Contract

- Agent exports an instance-based `ToolRegistry` SDK, not a global registry/runtime.
- SDK default factories: bash, filesystem, current_time. submit_result is opt-in.
- Coding sessions/children and Claw groups/children own actual tool inventories.
- Capability, MCP and host injections pass through registry publication, retaining
  permissions, original identity checks, explicit active selections and owner lifetime.
- Extension model-tool registration, discovery/call protocol and proxies are removed;
  commands, hooks, providers, UI and read-only inventory queries remain.
- Retired tools/call provenance may survive as inert historical data, never as live
  callback admission, tool authorization or a replay path.

## Tests

Final complete per-package results:

| Package | Passed | Skipped |
| --- | ---: | ---: |
| agent | 2114 | 15 |
| coding | 2519 | 12 |
| claw | 1866 | 0 |
| flow | 864 | 0 |
| eyes | 344 | 0 |
| skills | 726 | 0 |
| tui | 783 | 0 |
| ai | 738 | 7 |
| site | 43 | 0 |
| **Total** | **9997** | **34** |

The initial unfiltered `scripts/test.py --jobs 2` run exposed stale Goal-default
body and removed child-internal-field assertions. Those fixtures were updated to
check discoverability without activation and actual remaining child grants.
Coding and Eyes were then fully rerun successfully. This table combines final
package runs; it does not claim the initial root command was green.

Named regression contracts include:

- `agent/tests/test_tool_registry.py`: independent definitions and actual instances,
  no singleton/loop, real bash cwd isolation, atomic conflicts, stale disposal and
  active selection retirement without model execution.
- `agent/tests/test_snapshots.py`: inert historical tool context survives snapshots
  while the Extension tool interface and live admission method remain absent.
- `coding/tests/test_tool_registry.py`: SDK/Capability/MCP/child/inspection inventories,
  child selection ceilings and historical context navigation.
- `claw/tests/test_tool_configuration.py`: group-local catalogs, default versus empty
  selection, image opt-in and stale-session registration cleanup.
- Extension tests retain actual worker commands/hooks/providers/UI, explicitly reject
  retired tool registration and protocol methods, and retain read-only tool queries.
- Flow interpreter/generated modules use the same SDK-backed inventory; full Flow
  tests and all **17** shipped workflow-example tests pass.

## Static and package checks

- Repository Ruff and diff whitespace checks pass.
- Strict mypy across all nine namespaces passes: **627 source files**. Remaining
  unused-section notes are configuration notes, not suppressed type failures.
- Agent/Flow/Coding/Claw wheels build and pass Twine checks.
- An isolated extracted-wheel process checks actual wheel module origins, SDK default
  tools, no Claw registration leakage, absence of Extension tool APIs and rejection
  of tools/list and tools/call. This is an artifact smoke check with the existing
  environment's dependencies, not a fresh-environment full product certification.

Logs for this workspace run are under `/tmp/xdog-host-registry-*`, including the
initial root output, final Coding/Eyes reruns, JSON result summary, static checks,
builds and Twine checks. No production gateway restart, real channel send, version
bump, commit or push was performed.
