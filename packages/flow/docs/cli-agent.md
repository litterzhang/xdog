# Agent backends and the installed Flow tool

Flow has two distinct CLI boundaries:

- The installed **`flow` model tool** invokes the public `xdog-flow` CLI to
  validate, run, test or generate a workflow. It is not an agent-node backend.
- A workflow **agent node** uses the in-process SDK by default, or explicitly
  selects `claude-cli` / `codex-cli` as its external one-shot backend.

Both node backends feed the same typed ports and frontier executor. Neither
introduces a second in-process tool loop. Commands here run from the repository
root after `uv sync`.

## Installed capability and skill

Installing `xdog-flow` in the host's environment exposes the `flow` capability
through `xdog.capabilities`, at **application scope**. Discovery advertises its
skill and tool; it does not activate the skill body, grant tool permission, run a
workflow or start a process. Explicit skill activation is separate from tool
registration. A workflow node's own `skills` list is explicit activation, not
passive discovery.

The tool accepts CLI argv, excluding the executable:

```json
{"arguments": ["validate", "/project/workflow.json", "--json"], "cwd": "/project"}
```

`cwd` is an optional absolute process directory, not a workflow `--workspace`,
owner namespace or permission grant. The tool starts the same-environment Python
with `-m xdog.flow`, without a shell. Pipes, redirection and shell expansion are
not interpreted. Supported operations are non-interactive `validate`, `run`,
`generate`, `graph`, `test`, `scheduling` and help. Interactive `build` is for
manual CLI use only; author JSON with normal filesystem tools instead.

Results are `{exit_code, stdout, stderr}`. A CLI JSON response is still a string
inside `stdout`; inspect the exit code before decoding it. Output is bounded and
overflow is an error. Cancellation/close settles the owned CLI process group,
not rollback or uninstall of an accepted schedule. No automatic retry follows an
interrupted call: reconcile possible effects first. **Flow has no daemon**;
explicitly installed OS schedules/listeners have their own lifecycle.

Normal host permissions apply even to validation, which may import custom code.
Tool/skill availability is not approval, and direct CLI execution is not a bypass
for a denied operation. Workflows and their imports must be trusted.

## Node backend selection

```json
{
  "id": "triage",
  "type": "agent",
  "backend": "claude-cli",
  "model": "sonnet",
  "prompt": "Classify this issue: {{issue}}",
  "inputs": ["issue"],
  "outputs": ["category"],
  "allowed_tools": ["Read"],
  "mcp_servers": {}
}
```

This is a node fragment: the parent must wire `issue` through an incoming edge.
Omit `backend` for the SDK. A workflow may mix SDK, CLI, script, human and subflow
nodes. SDK nodes use Flow's provider configuration (`copilot` in this workspace);
CLI nodes use the external program's authentication and model names. A pure-CLI
workflow does not need a Flow provider, but does need the executable and its
working authentication. There is no automatic authentication fallback.

Flow currently builds these adapter invocations (not a promise about future
third-party CLI releases):

| Backend | Invocation/output | Structured output |
| --- | --- | --- |
| `claude-cli` | `claude -p --output-format json`; user prompt on stdin | `--json-schema` JSON; reads `structured_output` |
| `codex-cli` | `codex exec --json --skip-git-repo-check`; system + user prompt on stdin | `--output-schema` scratch file; parses final `agent_message` text as JSON |

Verify the installed external CLI's compatibility before a live run. Flow's own
help does not execute either backend. `FLOW_CLI_BIN` or a backend-specific
`FLOW_CLI_BIN_CLAUDE_CLI` / `FLOW_CLI_BIN_CODEX_CLI` overrides binary resolution;
these are execution settings, not the installed Flow tool's executable selector.

## Structured SDK results

The output-port contract determines whether a node is structured: no outputs or
one plain string output uses assistant text; other contracts use structured data.
A single structured port receives the submitted value directly (including an
array or scalar). Multiple ports require an object keyed by port name.

`SdkRunner` uses the existing `xdog.agent.Agent`, injects `submit_result`, and
passes generic tool context:

- `result_schema`: the schema derived from the node;
- `result_sink`: a caller-owned dict, written as `result_sink["result"]` only
  after validation succeeds.

Generated SDK nodes use the same contract. Validation failure leaves the sink
unchanged so the model can correct its submission; finishing without a result is
an execution error. The retired `flow_output_schema` / `flow_result_sink` keys
are rejected, not compatibility aliases. Flow authors declare typed `outputs` with `schema` objects, not an
`output_schema` field; this is separate from the generic SDK context keys.

CLI backends use native schema flags instead; they do not expose the in-process
SDK submission tool or sink to the external program.

## Tools and MCP

SDK `tools` names resolve through the executor's fresh host-owned registry;
workflow-level tool manifests can load trusted `module:attribute` references.
There is no import-time global runtime registry. The SDK runner executes the
Agent's existing loop, rather than maintaining a Flow-specific one.

CLI tool configuration is separate:

- `allowed_tools` is forwarded to Claude's `--allowedTools` when nonempty.
  Codex has no corresponding per-tool allowlist in the current adapter; Flow
  does not turn those names into a sandbox policy. An empty list must **not** be
  treated as a verified deny-all guarantee over an external CLI's defaults.
- `mcp_servers` supplies opaque per-server configuration, translated into the
  selected CLI's format. `${ENV_VAR}` references are resolved from the execution
  environment. Claude receives a scratch MCP file; Codex receives a scratch
  `CODEX_HOME` containing `config.toml`. Check authentication/configuration in
  that environment rather than assuming the usual user config is inherited.
- SDK tools and external CLI/MCP tools are not interchangeable. Flow does not
  provide an OS sandbox or increase the external program's permissions.

## Generate and test without models

Interpreter and codegen preserve the same port projection and graph semantics.
Generated SDK nodes still depend on `xdog.agent`/`xdog.ai`; subflows call back
into `xdog.flow`. Pure CLI/script modules omit SDK wiring unless other features,
such as a tool manifest, need it. Do not infer universal dependency freedom from
`generate`; see [subflows](subflow.md).

```bash
uv run xdog-flow --help
uv run xdog-flow run --help
uv run xdog-flow validate packages/flow/examples/cli_triage.json --json
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
```

Companion tests replace **all agent turns**, including CLI backends, with
`StubRunner`; missing stubs fail instead of calling a model. Scripts/imports may
still have effects and require review. `run --dry-run` is only an SDK streaming
echo, not a stub for external CLI backends or scripts. See [testing](testing.md).
