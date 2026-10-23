---
title: Overview
---

*Typed workflows for humans and Coding Agents.*

Flow is a local-first workflow format and compiler. A developer can compose a
fixed, repeatable Agent workflow in the TUI today (and a Web UI in the future),
while Claude Code, Codex, or another Agent can generate the same constrained
`workflow.json`, run validation, repair precise errors, and present a Git diff for
human review.

```text
Human TUI / Web UI ─┐
                     ├─> workflow.json -> validate -> run / generate / scheduling
Coding Agent + Skill ┘
```

The JSON file is the canonical intermediate representation. Editors do not own a
second database model. The loader is the compiler front-end; the frontier runtime
is the interpreter; codegen is the Python back-end; scheduling is an
optional local deployment adapter.

## Why Flow exists

Flow is not trying to be another open-ended Agent runtime or hosted low-code
platform. It crystallizes processes that have become stable enough to repeat,
inspect, schedule, and maintain:

- **Human/Agent symmetry** — people and Agents edit the same artifact.
- **Git-native workflows** — readable files, code review, history, and rollback.
- **Validate before execute** — ports, schemas, conditions, loops, fan-out, and
  subflows fail early with actionable errors.
- **Interpret equals compile** — direct execution and generated Python embed the
  same frontier transition kernel.
- **Local-first deployment** — no control plane is required; generate one Python
  artifact or install a systemd timer/hook.
- **Coding Agents as first-class nodes** — use the in-process SDK or invoke Claude
  Code/Codex CLI with tool and MCP declarations.

## Highlights

- Node-private JSON Schema ports and explicit edge mappings
- SDK agent, CLI agent, script, human, and opaque subflow nodes
- Conditional edges, heterogeneous bounded-loop joins, and dynamic fan-out/fan-in
- Coherent frontier-batch checkpoint/resume and deterministic memoization
- Structured success/failure result envelope with timing and token context
- Python code generation with inlined helpers and explicit runtime dependencies
- Interactive TUI builder plus ASCII, Mermaid, Graphviz SVG, and embedded SVG docs
- Linux systemd timer installation and hook configuration through `xdog-flow scheduling`
- Companion `*.test.json` suites via `xdog-flow test` — stub the model, run the graph

## Installed skill and tool

Installing `xdog-flow` in the host's environment contributes a discoverable skill
and a `flow` model tool at **application scope**. Discovery does not load the skill
body by default, grant tool permission, or execute a workflow. The tool invokes
non-interactive `xdog-flow` operations through same-environment CLI subprocesses;
Flow has **no daemon**. Interactive `build` remains a manual CLI operation.
See the [tool contract](/packages/flow/reference#installed-flow-tool).

**Interpret equals compile is a behavioral guarantee, not universal dependency
freedom.** SDK nodes still need `xdog.agent`/`xdog.ai`; generated subflows call
`xdog.flow` at runtime. Portable bundles vendor selected sources, not every
child/custom dependency. Scheduling also has deployment limits; inspect the
[Reference](/packages/flow/reference) before installing a real timer or hook.

## Flagship demo — Flow Release Radar

The Release Radar defines a local repository audit with a Monday timer. It runs
on that schedule only after explicit installation and arming:

```text
collect_repo (deterministic script)
    -> plan_checks (SDK Agent + filesystem/bash)
    -> audit × N (dynamic fan-out SDK Agents)
    -> score_risk (deterministic policy)
    -> report subflow (compose -> critique -> revise loop)
    -> structured $output
```

It demonstrates the product idea end to end: an AI-authored, human-reviewable JSON
workflow uses typed Agent and script steps, compiles to Python, and can be installed
as a local schedule.

```bash
uv run xdog-flow validate packages/flow/examples/release_readiness.json
uv run xdog-flow graph packages/flow/examples/release_readiness.json --mermaid
uv run xdog-flow generate packages/flow/examples/release_readiness.json -o release_readiness.py
uv run xdog-flow scheduling install packages/flow/examples/release_readiness.json --dry-run
```

## Try it

```bash
uv run xdog-flow --help
```

The commands above only display help, validate, graph, generate or preview units;
they do not run model turns or install schedules. Validation may still import
custom code and requires a trusted definition.

Or run a workflow live in the browser on the [HaveFun](/havefun/flow) page. Load a
shipped example, fill its inputs, and watch the execution log.

HaveFun executes trusted workflow code, not sandboxed submissions. A live run
may call models and tools; use only in a trusted/local deployment.
