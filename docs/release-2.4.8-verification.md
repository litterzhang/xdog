# 2.4.8 implementation and release verification

> **Historical / superseded SDK interface — 2026-10-24:** Historical release evidence: the ToolRegistry name and contracts below predate ToolRepo.
> Current integration uses ToolRepo / SkillRepo, explicit registration and provider
> selection before open; see [SDK capability assembly](sdk-capability-assembly.md).
> Old names and result counts are retained as evidence, not compatibility aliases.


## Historical boundary at release

- Agent supplies the `ToolRegistry` SDK; every host owns its instances and lifetime.
  Defaults are bash/filesystem/current_time; submit_result remains opt-in.
- MCP is separate: `McpRegistry` registers server configurations, owns explicit MCP
  connections and caches original spec+client proxies. Agent consumes it through
  `mcp_registry`, independently of the ordinary ToolRegistry.
- `Agent.local_tools` contains ordinary tools; `state.tools` / `active_tools` merge
  visible local and MCP inventories for the existing request/execution loop. Local
  publication cannot remove MCP. Cross-source conflicts fail atomically.
- Child MCP manifests contain only actually granted original proxies, not parent
  connections, credentials or unrestricted registries. Stale proxies cannot adopt
  replacement connections or refreshed schemas.
- Extension model-tool registration/protocol is removed. Hooks, commands, basic UI,
  providers and read-only inspection remain. Retired tools/call provenance survives
  only as inert history, never executable registration or replay authority.
- Goal skill bodies are discoverable but not activated by default. Installed
  Flow/Eyes skills, application capabilities, CLI-only tools and the shared Eyes
  daemon retain the separately verified boundary described in the SDK/Eyes docs.

## Tests and checks

Final complete per-package evidence:

| Package | Passed | Skipped |
| --- | ---: | ---: |
| agent | 2131 | 15 |
| coding | 2531 | 12 |
| claw | 1877 | 0 |
| flow | 864 | 0 |
| eyes | 344 | 0 |
| skills | 726 | 0 |
| tui | 783 | 0 |
| ai | 738 | 7 |
| site | 43 | 0 |
| **Total** | **10037** | **34** |

The root runner covered all nine configured test paths. One Agent assertion exposed
an error-message compatibility mismatch; the descriptive duplicate-name error now
also preserves the existing unique-name contract. Targeted tests and the complete
Agent package were rerun successfully. These are final package results, not a claim
that the first root run was green. Skipped external-environment cases are not
counted as compatibility verification.

- Repository Ruff and strict mypy across all nine namespaces pass (**627 source files**).
- Diff whitespace checks and all published-package imports pass.
- Every workspace distribution and version-bearing runtime constant is **2.4.8**;
  the lock file is updated and locked offline sync succeeds.
- All **17** shipped Flow example tests pass.
- All ten workspace distributions build as wheels and sdists. Twine passes;
  the pre-existing site-sdist long-description metadata warnings remain (site is
  not part of the eight-distribution PyPI publication matrix).
- Fresh wheel-only environments verify standalone Eyes; Agent ACP; Coding ingress;
  Coding SDK-owner inbox consumption; and Claw ACP/Core/native/output/Eyes delivery.
  Credentials/config/state and daemons for these tests are temporary and isolated.

Workspace evidence is retained under `/tmp/xdog-248-*`, including root/final-Agent
logs, static checks, examples, builds, Twine and five-environment artifact checks.
The local release operation separately verifies the pushed commit/tag, GitHub jobs
and actual gateway restart; operational outcomes are reported to the user, not
assumed from tests.
