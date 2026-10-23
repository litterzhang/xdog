"""Dynamic (Features + Roadmap) content for the ``flow`` package.

flow's static pages (Overview / Design / Reference / Examples) are markdown under
``content/pages/flow/``; its Features and Roadmap stay in Python here so flow
shares the same ``PackageDocs`` mechanism as the other packages. The feature and
phase data is maintained against the current CLI and runtime, including deployment limits.
"""

from __future__ import annotations

from xdog.site.content.docs import Feature, PackageDocs, Phase

_FEATURES = (
    # --- Modeling: how a workflow is shaped ---
    Feature(
        "Node-private ports", "Typed inputs/outputs wired by explicit edge mappings — no shared flat state.", "Modeling"
    ),
    Feature(
        "JSON Schema ports",
        "A port declares a JSON Schema (scalar or nested object/array) plus one `required` flag.",
        "Modeling",
    ),
    Feature(
        "Type-native wire",
        "Port values are live Python (int/float/bool/list/dict), not stringified — structure flows between nodes intact.",
        "Modeling",
    ),
    Feature(
        "$in / $output nodes",
        "Reserved source + sink: state seeds $in; edges to $output collect the result. `entry` is optional (derived from $in).",
        "Modeling",
    ),
    Feature(
        "Conditional edges",
        "equals / contains / numeric gt / gte / lt / lte / and / or / not guards over a source output port.",
        "Modeling",
    ),
    Feature(
        "Bounded loops + cycle detection",
        "Cycles use bounded loop.max with optional when, or strict while with its own condition/bound; exhaustion is a normal bounded stop or strict non-convergence error.",
        "Modeling",
    ),
    Feature(
        "Script nodes",
        "Inline code or a run: module:func reference, imported with the workflow dir on path.",
        "Modeling",
    ),
    Feature(
        "Sub-workflows",
        'A type:"subflow" node runs another workflow as one opaque node — inline or a `./child.json` path ref; ports are derived from the child\'s signature.',
        "Modeling",
    ),
    Feature(
        "Edge type checking",
        "An edge requires the source and destination port types to match — including a sub-field's type via its schema.",
        "Modeling",
    ),
    Feature(
        "Typed workflow signature",
        "$in carries an optional in_schema (else it's inferred from consumers); the $output signature is derived from the sink edges — so a workflow has a checkable I/O type.",
        "Modeling",
    ),
    Feature(
        "Validate before running",
        "Unknown ports, unfed required inputs, ambiguous producers, unbounded cycles, schema mismatches and prompt-root typos fail before node execution; custom imports may still have effects.",
        "Modeling",
    ),
    # --- Data flow: interpolation & mapping ---
    Feature(
        "JSONPath interpolation",
        "A prompt reads a field with `{{ $.plan.tasks[0] }}`; both engines share one jsonpath-ng evaluator.",
        "Data flow",
    ),
    Feature(
        "Strict interpolation",
        "Prompt/condition roots are checked against declared ports; supported JSONPath sub-fields use the source schema, but not every complex path is statically typable.",
        "Data flow",
    ),
    Feature(
        "Sub-field edge mapping",
        'An edge maps a nested field: `"map": {"$.verdict.within_budget": "flag"}`, type-checked against the source schema.',
        "Data flow",
    ),
    Feature(
        "Structured $in seed",
        "Initial state carries type-native JSON; a structured seed stays a dict/list, not a Python repr.",
        "Data flow",
    ),
    # --- Execution: how a run behaves ---
    Feature(
        "Shared frontier kernel",
        "Interpreter and generated Python run the same completion-driven frontier scheduler: stable parallel readiness, conditional inputs, AND joins, and heterogeneous bounded-loop groups.",
        "Execution",
    ),
    Feature(
        "Dynamic fan-out",
        "A fan_out edge maps a node over a runtime-sized array (once per element, in parallel); fan_in:list preserves index-ordered values and fan_in:concat flattens array results one level. The whole fan group is one checkpoint unit.",
        "Execution",
    ),
    Feature(
        "Node/driver separation",
        "Node functions consume typed inputs and return outputs; a driver owns retry, storage, memo, budget and checkpoint. Scripts/tools may still have external effects.",
        "Execution",
    ),
    Feature(
        "Concurrency caps",
        "max_concurrency bounds how many nodes run at once; a separate fan_max_concurrency bounds instances within one fan-out (both default to unlimited).",
        "Execution",
    ),
    Feature(
        "Structured run result",
        "CLI and generated Python print one success/message/output/context envelope with workflow/run id, timing, tokens, and last node; execute() retains the detailed internal runtime container.",
        "Execution",
    ),
    Feature(
        "Structured event stream",
        "on_event streams NodeStarted / NodeFinished / NodeFailed with per-node duration and tokens.",
        "Execution",
    ),
    Feature(
        "Metrics aggregation",
        "A MetricsCollector consumes the event stream into a per-node + per-run snapshot (runs, duration, tokens, failures).",
        "Execution",
    ),
    Feature(
        "Cost budget",
        "execute(max_tokens=N) aborts a run with WorkflowBudgetExceeded once cumulative agent tokens pass the ceiling.",
        "Execution",
    ),
    Feature(
        "SDK streaming dry-run",
        "run --dry-run echoes DRYRUN:<model> for SDK nodes only; scripts/CLI backends still execute, structured submission may fail, and generated modules have no equivalent switch.",
        "Execution",
    ),
    # --- Resilience: surviving failure & long runs ---
    Feature(
        "Per-node retry",
        "A RetryPolicy(max, backoff) retries a failed node before giving up (default: fail-fast). Retry lives in the driver.",
        "Resilience",
    ),
    Feature(
        "Failure isolation",
        "on_error:isolate records a failed branch in runtime.failed and skips only its sub-tree.",
        "Resilience",
    ),
    Feature(
        "Checkpoint & resume",
        "A CheckpointStore persists coherent frontier batches by run id; resume restores outputs, completion, loop counters, trace, memo, and token usage with at-least-once batch semantics.",
        "Resilience",
    ),
    Feature(
        "Human-in-the-loop",
        "A human node pauses awaiting a named signal; deliver it and resume the run to continue.",
        "Resilience",
    ),
    Feature(
        "Deterministic reuse",
        "deterministic:true reuses recorded output by (node, input hash); it does not roll back effects or close an effect/checkpoint crash window.",
        "Resilience",
    ),
    # --- Agents & tools ---
    Feature(
        "Host-owned SDK tools",
        "Each executor creates a fresh registry: echo, SDK defaults bash/filesystem/current_time, and opt-in submit_result. Node tool selection and structured injection use the existing Agent loop; no global runtime inventory.",
        "Agents & tools",
    ),
    Feature(
        "Structured SDK submission",
        "SDK and generated SDK nodes pass generic result_schema/result_sink; validation success writes result_sink['result']. A single structured port accepts its native value, multiple ports split an object; CLI backends use native schema flags.",
        "Agents & tools",
    ),
    Feature(
        "Installed Flow tool",
        "The application-scoped flow capability invokes non-interactive public CLI argv without a shell and returns exit_code/stdout/stderr. No Flow daemon; manual build stays outside the tool. Normal host permissions apply.",
        "Agents & tools",
    ),
    Feature(
        "Skill discovery, explicit activation",
        "Installed metadata advertises the flow skill without loading its body or granting tools. Explicit activation (including a node's skills list) loads instructions; discovery starts no workflow or process.",
        "Agents & tools",
    ),
    Feature(
        "Agent multi-output ports",
        "An agent fans its submit_result object across several typed output ports by field name — each wired independently.",
        "Agents & tools",
    ),
    Feature(
        "Derived output schema",
        "An agent's structured-output contract is derived from its output ports (no separate output_schema), validated by fastjsonschema.",
        "Agents & tools",
    ),
    Feature(
        "Agent web search",
        "SDK agent nodes can enable a built-in web_search tool with its own browsing model.",
        "Agents & tools",
    ),
    Feature(
        "JSON-declared custom tools", "A module:func tool manifest, loaded at run and generate time.", "Agents & tools"
    ),
    Feature(
        "CLI agent backend",
        'An agent node can set backend:"claude-cli"/"codex-cli" to run one turn by shelling out to a coding-agent CLI instead of the in-process SDK — no provider/API key needed (the CLI owns auth); structured output maps to the CLI\'s native schema flag.',
        "Agents & tools",
    ),
    Feature(
        "CLI tool configuration",
        "Nonempty allowed_tools is forwarded to Claude; Codex has no per-tool allowlist in this adapter. Empty does not mean verified deny-all. External CLI tools/MCP are separate from Flow's SDK registry.",
        "Agents & tools",
    ),
    Feature(
        "Per-node MCP servers",
        "A CLI agent node declares mcp_servers (an opaque pass-through spec); flow generates the CLI's MCP config, with ${ENV} secret interpolation — the JSON carries the reference, never the token.",
        "Agents & tools",
    ),
    # --- Scheduling: firing a workflow on its own ---
    Feature(
        "Active / timer scheduling",
        'A schedule:{mode:"timer"} block fires the workflow on an interval or cron expression; xdog-flow scheduling install writes a Linux systemd user timer; the CLI does not install an automatic crontab fallback.',
        "Scheduling",
    ),
    Feature(
        "Passive / hook scheduling",
        'A schedule:{mode:"hook"} block configures event-to-signal routing. The live entry point serves loopback HTTP; accepted file/socket metadata is not deployed watchers/socket activation.',
        "Scheduling",
    ),
    Feature(
        "Shared hook listener",
        "Hook entries share one listener, but the rendered module name and /usr/bin/python3 environment require operator correction/review. HTTP 202 after spawning is not durable execution or workflow completion; no built-in HTTP auth.",
        "Scheduling",
    ),
    Feature(
        "Scheduling lifecycle",
        "scheduling install/list/start/stop/uninstall manages local registry and systemd state. --dry-run skips deployment but still loads trusted code; list is registry facts, not live next-fire status.",
        "Scheduling",
    ),
    # --- Codegen & authoring ---
    Feature(
        "Code generation",
        "Compile typed JSON to Python with inlined helpers and shared graph semantics. SDK nodes import xdog.agent/xdog.ai; subflows call xdog.flow at runtime; custom imports still apply.",
        "Codegen & authoring",
    ),
    Feature(
        "Interpret == compile",
        "Interpreter and generated module agree node-for-node — enforced by a cross-engine parity suite on every feature.",
        "Codegen & authoring",
    ),
    Feature(
        "Portable bundle",
        "generate --portable vendors selected sources: SDK nodes/tool manifests select ai/agent, subflows add flow. This is not a recursive child/custom dependency closure; inspect requirements. --offline downloads selected wheels at build time.",
        "Codegen & authoring",
    ),
    Feature(
        "Runtime overrides",
        "The generated module honours FLOW_INPUTS (JSON merged into $in) and FLOW_PROVIDER — parity with the interpreter's --input / --provider.",
        "Codegen & authoring",
    ),
    Feature(
        "Human + Agent authoring",
        "xdog-flow build edits the same Git-friendly JSON that Coding Agents generate through the Flow skill; a local Web UI is the next editor surface.",
        "Codegen & authoring",
    ),
    Feature(
        "Four diagram renderers",
        "Text listing, layered ASCII, Graphviz SVG (with fallback), and Mermaid; node boxes are colour-coded by type (agent/script/human/subflow).",
        "Codegen & authoring",
    ),
    # --- Testing: proving a workflow behaves ---
    Feature(
        "Companion test suites",
        "xdog-flow test runs a workflow's <name>.test.json — a workflow path, a suite, or a whole directory; exit 1 on failure drops it into a pre-commit hook or CI.",
        "Testing",
    ),
    Feature(
        "Stub only the boundaries",
        "Agent turns (SDK and CLI alike), human signals, and whole subflow nodes are stubbable; script nodes need an explicit --allow-script-stub. Edges, conditions, loops, fan-out, coercion and $output collection always run for real.",
        "Testing",
    ),
    Feature(
        "Stubs on the production path",
        "A stub is injected at the provider call, after prompt interpolation and before output parsing — so it is validated by the node's own required-field check and coercion, and a broken {{ $.path }} still fails.",
        "Testing",
    ),
    Feature(
        "No live agent fallback",
        "StubRunner answers every agent node, including CLI backends; missing stubs fail, not dial out. Unstubbed scripts and imported custom code can still use files, processes and the network.",
        "Testing",
    ),
    Feature(
        "Concurrency-safe selectors",
        "A stub rule selects on when (deep-subset match on inputs), index (fan array position), or round (activation ordinal) — never on completion order, so a fan-out case is not flaky.",
        "Testing",
    ),
    Feature(
        "Three-way outcome + calls",
        "expect takes one of success / error substring / paused-at-node, plus a deep-subset output match and a calls map that covers executed and skipped nodes, fan instances, and loop iterations in one number.",
        "Testing",
    ),
    Feature(
        "Suite validated at load",
        "A stub aimed at a missing node, the wrong node type, or an undeclared output port fails before anything executes — and a selector that never fires is a failure, not a silent fall-through.",
        "Testing",
    ),
)

_FEATURE_CATEGORIES = (
    "Modeling",
    "Data flow",
    "Execution",
    "Resilience",
    "Agents & tools",
    "Scheduling",
    "Codegen & authoring",
    "Testing",
)

_ROADMAP = (
    Phase(
        "P1",
        "Per-node retry & timeout policy",
        (
            "Done: NodeDef carries a RetryPolicy(max, backoff); the executor retries a "
            "failed node (script or agent) up to max times with a per-attempt backoff, "
            "then re-raises — preserving fail-fast when retries are exhausted.",
            "Removes the 'one LLM hiccup fails the whole run' failure mode.",
            "Notably, this was implemented by flow itself: the tools/autoenrich workflow "
            "(build → gate → validate, with a bounded fix loop) wrote and self-reviewed it.",
        ),
        done=True,
    ),
    Phase(
        "P2",
        "Checkpoint & resume",
        (
            "Done: a CheckpointStore protocol (with a JSONFileCheckpointStore) persists "
            "coherent frontier-batch snapshots keyed by run id; resume reconstructs graph "
            "progress and skips already-committed work with batch-level at-least-once semantics.",
            "Turns long agent runs from all-or-nothing into recoverable. Codegen honours "
            "it too via FLOW_RUN_ID / FLOW_CHECKPOINT_DIR, so both run paths agree.",
            "Also implemented by the tools/autoenrich workflow itself.",
        ),
        done=True,
    ),
    Phase(
        "P3",
        "Structured event stream",
        (
            "Done: typed NodeStarted / NodeFinished / NodeFailed events carry per-node "
            "wall-clock duration and (for agent nodes) token usage; the executor "
            "delivers them via an on_event callback, and the generated module logs the "
            "same lifecycle to the flow.generated.events logger.",
            "The foundation for observability and live TUI/web progress. Also "
            "implemented by the tools/autoenrich workflow itself.",
        ),
        done=True,
    ),
    Phase(
        "P4.1",
        "Per-branch failure isolation",
        (
            'Done: a node marked on_error:"isolate" whose branch fails is captured '
            '(its error recorded in runtime["failed"]) and its downstream sub-tree '
            "skipped, while independent sibling branches still run to completion. The "
            'default on_error:"fail" keeps the fail-fast semantics.',
            "Implemented by the tools/autoenrich workflow itself; codegen mirrors it.",
        ),
        done=True,
    ),
    Phase(
        "P4.2",
        "Concurrency caps",
        (
            "Done: a workflow (or execute() override) can cap how many nodes run at "
            "once via max_concurrency; fan instances use a separate fan_max_concurrency "
            "limiter. Both default to unlimited; cap the inner group explicitly for provider limits.",
            "Implemented by the tools/autoenrich workflow itself; codegen mirrors it.",
        ),
        done=True,
    ),
    Phase(
        "P4.3",
        "Human-in-the-loop",
        (
            "Done: a human node awaits a named signal — absent, the run checkpoints and "
            "raises WorkflowPaused (the generated module exits with a PAUSED line); "
            "deliver the signal and resume with the same run id to pass the gate. Real "
            "stop-for-approval built on P2 checkpointing.",
            "Implemented by the tools/autoenrich workflow itself; codegen mirrors it.",
        ),
        done=True,
    ),
    Phase(
        "P4.4",
        "Deterministic nodes (recorded reuse)",
        (
            "Done: a node marked deterministic:true memoises its output keyed by "
            "(node id, input hash), so a retry or resume with the same input reuses the "
            "result when recorded. This is not exactly-once execution: effects before a "
            "durable record may repeat on retry/resume. Non-deterministic nodes default to running.",
            "Implemented by the tools/autoenrich workflow itself; codegen mirrors it.",
        ),
        done=True,
    ),
    Phase(
        "P5",
        "Expressiveness — structured data & JSON Schema",
        (
            "Done: the wire format is type-native — port values are live int/float/bool/"
            "list/dict, not stringified — so structure flows between nodes intact and an "
            "agent's structured result stays a real object.",
            "Done: ports carry a JSON Schema (scalar or nested), with one `required` flag "
            "replacing the old `optional`; an agent's structured-output contract is derived "
            "from its output ports (no separate output_schema) and validated by "
            "fastjsonschema; an agent can fan its result across several typed output ports.",
            "Done: interpolation, conditions and edge maps use JSONPath (`{{ $.plan."
            'tasks[0] }}`, `"$.verdict.within_budget"`) via one shared jsonpath-ng '
            "evaluator; edges are type-checked end to end, including a sub-field's type.",
            "Done: real cycle detection (an unbounded cycle fails validation with its "
            "path); `entry` is optional (derived from the $in frontier); the generated "
            "module honours FLOW_INPUTS / FLOW_PROVIDER overrides.",
            "Every item shipped with interpreter + codegen + a cross-engine parity test, so "
            "interpret == compile holds throughout. See docs/expressiveness.md.",
        ),
        done=True,
    ),
    Phase(
        "P6",
        "Expressiveness — closing the gaps",
        (
            "Done: numeric condition ops (gt/gte/lt/lte) so a loop can branch on a "
            "score, not just a string match; and strict interpolation — because ports "
            "are declared, every `{{ $.key }}` in a prompt or condition is checked "
            "against the declared ports at load time, turning a silent typo into a "
            "fail-fast. A loop with no `when` guard now warns.",
            "Done: a typed workflow signature. $in carries an optional in_schema (else "
            "it's inferred from how each seed is consumed — the consumer's type, not the "
            "seed's value); the $output signature is derived from the sink edges. This "
            "gives a workflow a checkable I/O type at both ends.",
            'Done: sub-workflows — a type:"subflow" node runs another workflow as one '
            "OPAQUE node (not inlined), authored inline or as a `./child.json` path ref. "
            "Its ports are derived from the child's signature; both engines call the "
            "same execute() on the child, so interpret == compile holds by construction. "
            "A generated module that uses one imports xdog.flow (vendored by --portable); "
            "child/custom dependency completeness still needs verification. See "
            "docs/subflow.md.",
            "Done: dynamic fan-out — a fan_out edge maps a node over a runtime-sized "
            "array and a fan_in edge gathers the results into an index-ordered list, with "
            "a dedicated fan_max_concurrency cap. The fan group stays ONE scheduler node, "
            "so the static graph stays static. See docs/fan-out.md.",
            "Every item shipped with interpreter + codegen + a cross-engine parity test, "
            "and the builder/graph views render subflow nodes and the typed signature.",
        ),
        done=True,
    ),
    Phase(
        "P7",
        "CLI agent backend — flow as a skill",
        (
            'Done: an agent node can set backend:"claude-cli"/"codex-cli" to run one '
            "turn by shelling out to a coding-agent CLI instead of the in-process SDK. A "
            "CLI agent node needs no Flow provider (the CLI owns auth); nonempty allowed_tools "
            "is forwarded to Claude, not a Codex per-tool sandbox. Its mcp_servers spec is converted into "
            "the CLI's MCP config with ${ENV} secret interpolation. Both engines shell the "
            "same command, so interpret == compile holds.",
            "Done: the SDK block (agent/ai imports + tool registry + _run_agent) is emitted "
            "when SDK nodes or a custom tool manifest need it. Pure CLI/script artifacts can "
            "omit that wiring, but subflows and arbitrary custom imports add dependencies.",
            "Done: a packaged skill and application-scoped CLI-only flow tool. Discovery "
            "advertises availability, not default skill activation or tool permission; "
            "non-interactive commands use the public CLI without a daemon. See "
            "docs/cli-agent.md.",
        ),
        done=True,
    ),
    Phase(
        "P8",
        "Scheduling — fire a workflow on its own",
        (
            'Done: a top-level schedule block. mode:"timer" fires on an interval '
            '(every:"15m") or a cron expression (translated to a systemd OnCalendar); '
            'mode:"hook" declares event routing that supplies a signal to a '
            "fresh run — reusing the human-node pause/resume primitive.",
            "Done: xdog-flow scheduling install builds the portable bundle and installs a "
            "systemd user timer or shared hook-listener route; list/start/stop/uninstall use "
            "a local registry. No CLI crontab/nohup fallback is installed. --dry-run previews changes.",
            "The scheduler wraps the built bundle — every firing is a fresh python <bundle> "
            "run, so the engine and interpret == compile are untouched. Linux/systemd "
            "only. The live listener entry point serves HTTP, not file/socket watchers; "
            "its rendered module path and system Python environment need operator review. "
            "See docs/scheduling.md for current deployment limits.",
        ),
        done=True,
    ),
    Phase(
        "P9",
        "Human + Agent authoring surfaces",
        (
            "Build a local Web UI as a Workflow JSON IDE: graph canvas, typed forms, JSON "
            "preview, validation, run/generate/scheduling actions, and structured results. It "
            "edits the same Git file as the TUI and Coding Agent skill — no second database model.",
            "Make Agent authoring machine-friendly with schema export and JSON validation errors "
            "carrying stable path/code/message fields, enabling create → validate → repair → review.",
            "Grow a high-quality template library, led by Flow Release Radar: local repo collection, "
            "SDK-tool audits, dynamic fan-out, deterministic scoring, report subflow, and scheduling.",
        ),
    ),
    Phase(
        "P10",
        "First-class workflow tests",
        (
            "xdog-flow test <target> [--case NAME] runs a workflow's companion <name>.test.json "
            "suite — a workflow file, a suite file, or a whole directory. Test data stays out of "
            "the production workflow definition.",
            "Stubs cover only the boundaries a test cannot reason about: agent turns (SDK and CLI "
            "backends alike), human signals, and whole subflow nodes. Script nodes need an explicit "
            "--allow-script-stub, because stubbing deterministic logic hides the thing under test.",
            "Stubs are injected at the provider call, so prompt interpolation runs for real and the "
            "stubbed value is validated by the node's own required-field check and coercion — the "
            "same code that validates a live model response. Missing agent stubs cannot reach "
            "a real provider/CLI, but unstubbed scripts and imported code retain their effects.",
            "Rules select deterministically under concurrency: when (deep-subset match on inputs), "
            "index (fan array position), round (activation ordinal). Assertions are one outcome — "
            "success, error substring, or paused-at-node — plus a deep-subset output match and a "
            "calls map that covers executed/skipped nodes, fan instances, and loop iterations.",
        ),
        done=True,
    ),
    Phase(
        "2026",
        "Beyond the kernel — as a library, not a platform",
        (
            "Deeper host-integration examples: run a flow graph as one activity inside a "
            "durable engine (e.g. Temporal) for cross-machine scale.",
            "Richer diagram/observability surfaces built on the P3 event stream.",
            "Distributed execution stays a deliberate non-goal — see Design for why the "
            "single-machine, interpret==compile kernel is the point.",
        ),
    ),
)

DOCS = PackageDocs(
    name="flow",
    features_intro="Current Flow capabilities and their limits, grounded in the typed workflow "
    "runtime and CLI. Generation is not universal dependency freedom; see Reference for deployment caveats.",
    feature_categories=_FEATURE_CATEGORIES,
    features=_FEATURES,
    roadmap_intro="Typed ports, the shared frontier kernel, subflows, fan-out, CLI backends and "
    "companion tests are shipped. Generated subflows still depend on xdog.flow, and scheduling "
    "has the deployment limits documented in Reference. The roadmap keeps Flow's purpose: "
    "one Git-native artifact authored through the TUI or Coding Agents, with a future local "
    "Web UI and richer inspection. P10 tests are already available; distributed execution "
    "remains a deliberate non-goal.",
    roadmap=_ROADMAP,
)
