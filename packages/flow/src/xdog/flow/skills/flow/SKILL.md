---
name: flow
description: Author and run flow workflows — turn a recurring multi-step process into a saved, replayable workflow.json (typed agent/script/subflow nodes + edges) and execute it with xdog-flow. Agent nodes can call a coding-agent CLI (claude/codex) for their step, so no provider/API key is needed. Use when asked to crystallize/automate/save a process as a workflow, or to build a typed pipeline (classify→route, draft→critique→revise, research→summarize).
---

# flow — author & run workflows

Use this skill to turn a recurring, multi-step process into a **saved, replayable
workflow** and then run it. flow is a typed, single-machine workflow kernel: you
describe nodes (agent / script / human / subflow) and the typed edges between
them as JSON, then `xdog-flow run` executes it — or `xdog-flow generate` compiles
it to a self-contained Python module.

The key move: an **agent node can call a coding-agent CLI** (`claude`, `codex`)
for its step instead of a hosted provider. So this CLI can crystallize your own
workflows and run them, with each agentic step shelling back to a CLI — no API key
or provider to configure.

## When to use

- The user asks to "save / crystallize / automate this process as a workflow."
- A task is a fixed pipeline of steps (classify → route, draft → critique → revise,
  research → summarize) worth replaying deterministically around the LLM steps.
- You need typed, inspectable orchestration rather than an ad-hoc prompt chain.

## Workflow JSON at a glance

```jsonc
{
  "name": "cli-triage",
  "entry": "classify",                 // optional; else derived from $in edges
  "state": { "report": "..." },        // $in seed values (the workflow's inputs)
  "nodes": [
    {
      "id": "classify",
      "type": "agent",
      "backend": "claude-cli",         // run this step via the claude CLI (no provider)
      "model": "sonnet",               // CLI --model (alias or id); optional
      "inputs":  [{ "name": "report", "schema": {"type": "string"} }],
      "prompt":  "Classify:\n{{$.report}}\nFields: severity, area, summary",
      "allowed_tools": [],             // NARROW the CLI's tools; [] = none (default)
      "outputs": [                     // >1 port or a structured port => structured output
        { "name": "severity", "schema": {"type": "string"} },
        { "name": "area",     "schema": {"type": "string"} },
        { "name": "summary",  "schema": {"type": "string"} }
      ]
    },
    {
      "id": "route",
      "type": "script",
      "inputs": [{ "name": "severity", "schema": {"type": "string"} }],
      "code": "def route(ctx, severity):\n    return {'page': severity in ('high','critical')}",
      "outputs": [{ "name": "triage", "schema": {"type": "object"} }]
    }
  ],
  "edges": [
    { "from": "$in",       "to": "classify", "map": { "report": "report" } },
    { "from": "classify",  "to": "route",    "map": { "severity": "severity" } },
    { "from": "route",     "to": "$output",  "map": { "triage": "result" } }
  ]
}
```

Core rules:
- **Ports + edges are typed.** An edge `map` is `{ source_output_port:
  destination_input_port }`. A `{{$.x}}` in a prompt must be a declared input port.
- **$in / $output** are the reserved source/sink. `state` seeds `$in`; edges to
  `$output` collect the workflow's result.
- **Structured output**: an agent with >1 output port (or one structured/object
  port) must return an object with those fields. flow maps this to the CLI's native
  schema flag automatically — you don't write the schema.

## CLI agent nodes (the closed loop)

Set `"backend": "claude-cli"` or `"codex-cli"` on an agent node to run it via that
CLI. Then:
- **No provider needed.** A workflow whose agent nodes are all CLI-backed omits
  `provider` entirely — the CLI owns auth. (An SDK agent node, i.e. no `backend`,
  still needs a top-level `"provider"`.)
- **Tools are narrowed, not provided.** `allowed_tools` is an allow-list of the
  CLI's own tools — built-ins (`"Read"`, `"WebSearch"`) or MCP tools
  (`"mcp__github__create_issue"`). `[]` (the default) = no tools, tightest sandbox.
  flow ships no tools.
- **MCP servers (optional).** A node may declare `mcp_servers` to bring a tool the
  base CLI lacks; flow generates the CLI's MCP config for that node. Secrets use
  `${ENV_VAR}` (resolved at run time — never hardcode a token):
  ```jsonc
  "mcp_servers": { "github": { "command": "npx", "args": ["-y", "@mcp/github"],
                               "env": { "GITHUB_TOKEN": "${GITHUB_TOKEN}" } } }
  ```
- The CLI binary is found on `PATH`; override with `FLOW_CLI_BIN` (or
  `FLOW_CLI_BIN_CLAUDE_CLI` / `FLOW_CLI_BIN_CODEX_CLI`).

A workflow may **mix** SDK and CLI agent nodes and stays a normal, portable JSON
artifact — it is not locked to CLI execution.

## Other node types

- **script**: an inline `code` function `def name(ctx, *inputs) -> value/dict`, or a
  `run` ref `"module:callable"`. Deterministic; runs in-process.
- **subflow**: `"type": "subflow"` with `"subflow"` set to an inline child workflow
  object **or** a path `"./child.json"`. Its input/output ports are *derived* from
  the child's signature — don't declare them. See `examples/essay_writer.json`.
- **human**: pauses for an external signal.

Edges also support conditions (`when`: equals/contains/gt/gte/lt/lte/and/or/not),
bounded loops (`loop: {max: N}` on a back-edge), and dynamic fan-out
(`fan_out`/`fan_in`) — see the shipped examples.

## Commands

```bash
xdog-flow validate workflow.json     # fast structural + type check (do this first)
xdog-flow run      workflow.json     # execute; prints success/message/output/context JSON
xdog-flow run      workflow.json --input key=value   # override a $in seed
xdog-flow generate workflow.json -o out.py           # compile to a Python module
xdog-flow graph    workflow.json     # print the ASCII diagram
xdog-flow test     workflow.json     # run workflow.test.json (see Testing)
xdog-flow scheduling install  workflow.json     # install a scheduled workflow (see Scheduling)
```

Workflow: **write JSON → `validate` (fix any reported errors — they are precise) →
`run`.** Iterate on the JSON, not on prose.

## Testing — write `workflow.test.json` next to the workflow

Stub only the boundaries a test cannot reason about; everything else runs for real.

```json
{
  "cases": [
    {
      "name": "critical finding blocks the release",
      "inputs": {"repo": "/fixture/repo", "base_ref": "main"},
      "agents": {
        "plan_checks": {"checks": [{"name": "security", "focus": "auth"}], "rationale": "..."},
        "audit": [
          {"when": {"check": {"name": "security"}}, "then": {"finding": {"severity": "critical"}}},
          {"then": {"finding": {"severity": "low"}}}
        ]
      },
      "subflows": {"report": {"final_report": "# Blocked", "quality_score": 9, "report_feedback": "ok"}},
      "expect": {
        "output": {"risk": {"status": "blocked", "release_allowed": false}},
        "calls": {"audit": 3, "revise": 0}
      }
    }
  ]
}
```

Rules for writing one:

- **Stub every agent node** — an unstubbed one fails rather than calling a model.
  Also `signals: ["name"]` to pass a human gate, `subflows` for a whole subflow.
  Script nodes run for real (stubbing them needs `--allow-script-stub`).
- A stub is the node's **output ports**. Either a constant object, or a rule list
  where the first match wins and the selector-free default comes last.
- Selectors: `when` (deep-subset match on the node's inputs — use for fan-out),
  `index` (fan array position), `round` (1-based activation — use for loops).
  All are stable under concurrency.
- `expect` takes exactly one outcome — `success` (default), `error: "<substring>"`,
  or `paused: "<human node id>"` — plus `output` (deep subset of `$output`) and
  `calls` (node id → invocation count; `0` asserts a branch was skipped, and a
  fan-out node reports its instance count).
- Objects match as subsets, arrays must be the same length, `bool` only matches
  `bool`.

Stubs are validated against the node's declared output ports, so a wrong port name
or a missing required field fails immediately. See `docs/testing.md`.

## Scheduling (optional) — make a workflow fire on its own

Add a top-level `schedule` block, then `xdog-flow scheduling install <wf.json>` (Linux/systemd):

```jsonc
// active / timer — fire on a schedule
"schedule": { "mode": "timer", "every": "15m", "inputs": { "report": "..." } }
"schedule": { "mode": "timer", "cron": "0 9 * * 1-5" }   // or a 5-field cron

// passive / hook — fire when an event delivers a signal (needs a `human` node
// with the same signal, which then proceeds instead of pausing)
"schedule": { "mode": "hook", "signal": "new-ticket",
              "listen": { "type": "http", "path": "/hooks/triage", "port": 8787 } }
```

- `xdog-flow scheduling install <wf.json>` — build the bundle + install the scheduler
  (`--dry-run` to preview the systemd units without touching the OS).
- `xdog-flow scheduling list` — list installed scheduled workflows.
- `xdog-flow scheduling uninstall <name>` — uninstall one.

Each firing is an independent `python <bundle>` run; the engine is unchanged.
See `examples/digest_timer.json` (timer) and `examples/triage_hook.json` (hook).

## Examples to imitate

In `examples/`:
- `cli_triage.json` — a CLI agent node (classify) + a script router. Pure-CLI, no
  provider. The canonical shape for this skill.
- `digest_timer.json` — a **timer**-scheduled workflow (weekday 9am cron).
- `triage_hook.json` — a **hook**-scheduled workflow (webhook delivers a signal).
- `essay_writer.json` + `essay_compose.json` — a subflow (draft→critique→revise) via
  a path reference; structured output across the boundary.
- `trip_planner.json` — typed multi-agent pipeline with JSONPath sub-field mapping.
- `agent_calculator.json` — an agent node with a tool; `refine_loop.json` — a loop.
- `release_readiness.json` + `release_report.json` — a substantial local-repository
  demo: SDK agents with filesystem/bash tools, dynamic fan-out audits,
  deterministic risk scoring, a report subflow, bounded review loop, and weekly
  scheduling.
- `release_readiness.test.json` + `release_report.test.json` — the matching test
  suites: fan-out stubs selected by input value, a loop pinned to its `loop.max`
  bound, and a whole subflow stubbed out.

Read the closest example, copy its shape, and adapt.
