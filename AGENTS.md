# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Repository and toolchain

XDOG is a local-first Python 3.12+ `uv` workspace; its core tools do not require a hosted control plane or database. Eight distributions share the `xdog.*` namespace under `packages/`: `ai`, `agent`, `flow`, `coding`, `claw`, `tui`, `site`, and `eyes`. Run development commands from the repository root so the root `pyproject.toml` configuration applies.

```bash
# Create/update the workspace environment
uv sync

# Core test, lint, and type-check commands used by CI
uv run python scripts/test.py  # all configured test paths, balanced package workers
uv run pytest -q              # direct pytest alternative; supports normal selection flags
uv run ruff check packages
uv run mypy --strict \
  -p xdog.ai -p xdog.agent -p xdog.tui -p xdog.flow \
  -p xdog.site -p xdog.claw -p xdog.coding -p xdog.eyes

# Package, file, and individual-test examples
uv run pytest packages/flow/tests -q
uv run pytest packages/agent/tests/test_agent_tools.py
uv run pytest packages/agent/tests/test_agent_tools.py::test_name
uv run pytest -k 'expression'

# Validate the shipped workflow examples (also run by CI)
uv run xdog-flow test packages/flow/examples/ --allow-script-stub

# Build and inspect a distribution; substitute another workspace package as needed
uv build --package xdog-flow --out-dir dist
uvx twine check dist/*
```

Pytest uses `asyncio_mode=auto` and importlib import mode. Ruff is configured for Python 3.12 with a 120-character line length. Package-local `pyproject.toml` files allow tests to be run from within an individual package, but root paths are `packages/<package>/tests`, not `tests/<package>`.

Useful entry points after `uv sync`:

```bash
uv run xdog-flow --help       # workflow CLI
uv run xdog-coding            # interactive coding agent
uv run xdog-claw --help       # persistent assistant runtime
uv run xdog-ai --help         # model/provider CLI
uv run xdog-agent --help      # generic agent CLI
uv run xdog-site              # documentation/demo site
uv run xdog-eyes --help       # observation CLI
```

## Architecture

Dependency direction (`A -> B` means A builds on B):

```text
flow   -----> agent -----> ai
coding -----> agent/ai + tui
claw   -----> agent/ai + tui
site   -----> flow/agent/ai (workflow demo integration)
tui           standalone terminal rendering/components
```

### `xdog.ai`: model and protocol foundation

`packages/ai/src/xdog/ai/` defines the provider-neutral model, context, streaming event, completion, embedding, and web-search interfaces. `providers/runtime.py` resolves provider/model names; the production provider currently registered is GitHub Copilot. Files under `protocols/` implement Anthropic/OpenAI wire formats used according to model metadata—they are not separate configured providers. `proxy.py` exposes an Anthropic-compatible local HTTP/SSE facade over the same runtime.

Keep provider/authentication, model routing, and wire-protocol adaptation separate when changing this layer.

### `xdog.agent`: reusable tool-calling runtime

`packages/agent/src/xdog/agent/agent.py` owns stateful conversations, lifecycle events, cancellation, steering/follow-ups, skills, and persistence snapshots. `agent_loop.py` converts history to an `xdog.ai` context, streams a response, validates and executes tool calls, appends results, and repeats until the turn ends.

Coding, claw, and SDK-backed flow agent nodes reuse this runtime. Flow's external CLI backends intentionally use `CliRunner`; do not introduce a second in-process tool loop for SDK-backed nodes. Tool calls execute in parallel by default, while preparation/finalization hooks are serial. Callers enforce tool permissions through hooks—the generic tools themselves are not a security boundary. Built-ins are registered at import time and include `bash`, `filesystem`, `current_time`, and `submit_result`.

### `xdog.flow`: typed workflow engine and primary product surface

A flow definition travels through:

```text
JSON or metadata-bearing SVG
  -> loader and whole-graph validation
  -> readiness/frontier executor
  -> script | SDK agent | external CLI agent | human | subflow nodes
  -> mapped outputs, trace, and token usage
```

The core invariant in `models.py` and `loader.py` is node-private typed ports connected by explicit edge mappings. `$in` and `$output` are synthetic source/sink nodes; there is no shared global workflow state. Conditions, bounded loops, joins, fan-out, retries, isolation, session inheritance, and schema compatibility are validated before execution.

`executor.py` schedules ready nodes concurrently; it dispatches script, SDK-agent, external-CLI-agent, human, and subflow nodes while handling checkpoints/resume, memoization, loops, failure policy, token budgets, and human pauses. `runners.py` implements the SDK and external CLI agent backends. Structured agent output is captured through the injected `submit_result` tool rather than parsed from prose. Agent history crosses nodes or loop iterations only through explicit `inherit` configuration.

`codegen.py` and the interpreter are expected to preserve behavioral parity. Generated workflows containing subflows still import parts of `xdog.flow`; do not assume every generated module is completely standalone. Review `LICENSE-EXCEPTION.md` when changing codegen, portable bundles, scheduling-unit generation, or copied runtime templates because it defines the generated-output licensing boundary. The `xdog-flow` CLI provides `validate`, `run`, `test`, `generate`, `graph`, `build`, and scheduling commands. The headless builder model is under `builder/`; its TUI and SVG document support sit on top of that model.

### `xdog.coding`: interactive coding client

`packages/coding/src/xdog/coding/main.py` selects interactive TUI, one-shot print, or JSON-lines RPC modes. `core/sdk.py` resolves layered configuration, model metadata, permissions, skills, tools, and persisted state before constructing an `xdog.agent.Agent`. Configuration precedence is global settings, project `.coding/settings.json`, then CLI overrides.

`core/agent_session.py` rebuilds the prompt and checks compaction before each turn, then adapts agent events to the selected UI mode. Session history, settings, summaries, and branches are persisted as JSON by `core/session_manager.py`. `core/sessions/` owns the single controller, writer lease, route ownership and separate durable reviewed inbox; TUI/RPC/ACP share that execution path, and offline ingress never constructs a second Agent. Project context discovery is non-recursive from the working directory: `AGENTS.md`, `AGENTS.md`, `.coding/INSTRUCTIONS.md`, and `.coding/instructions.md`, each capped at 64 KiB. Permissions belong in the coding client's pre-tool hook, not in the generic agent runtime. Preserve the default fail-closed behavior: read-only calls may run automatically, mutating calls require approval, and unattended execution may mutate only when `allow-all` was explicitly selected. Permission gates are not an OS sandbox.

### `xdog.claw`: persistent multi-channel assistant

The main path is:

```text
TUI Unix socket or Weixin channel
  -> core/runtime/gateway.py
  -> Orchestrator and per-group queue (collect/steer/steer-backlog)
  -> GroupRuntime's active AgentSession
  -> prompt rebuild and pre-turn compaction
  -> xdog.agent stream/tools
  -> JSONL transcript persistence and channel output
```

Scheduler and goal work re-enter through the same orchestrator path as system input. A group runtime owns its workspace, tools, memory, goals, prompt builder, transcript store, and one active cached agent session. Transcripts are JSONL with a `sessions.json` index; `SessionManager` is a compatibility alias for `TranscriptStore`, not an LRU multi-session manager.

Prompt construction intentionally separates a cacheable static prefix from mutable workspace, memory, environment, and goal content. Compaction flushes durable memory, summarizes through the LLM, archives the transcript, then replaces older history. Memory uses vector+BM25 search when optional dependencies initialize and falls back to keyword search otherwise.

Importing `xdog.claw.core` intentionally registers claw-specific tools. Preserve these side-effect imports when reorganizing code.

### `xdog.eyes`: observation and ACP activation layer

Like flow, eyes owns a versioned JSON definition, frozen models, whole-definition
validation, asynchronous executor, CLI and packaged `eyes-observers` skill.
Sources collect evidence, detectors emit events, and explicit routes invoke ACP
handlers. Regular-file content changes use bounded no-follow polling.

Eyes depends only on the optional version-bounded ACP SDK, never on
agent/ai/coding/claw/flow, and does not execute tools or an agent loop. A coding
callback connects directly to an explicitly configured private Unix ACP endpoint
and exact session ID; it does not launch coding CLI or inspect coding registry.
Stdio is an optional generic third-party ACP transport. Loading a skill does not
start observation, a model, a receiver or TUI.

Coding's independently managed lightweight ACP ingress forwards live prompts or
persists negotiated XDOG enqueue messages without constructing AgentSession.
TUI/owner absence does not prevent durable intake while the receiver is running.
Opening a session displays pending/interrupted messages without consuming them;
exact approval/retry uses the sole controller, existing permissions and one-turn
credit. Message and execution-attempt identities differ; crash running becomes
interrupted, never automatic side-effect replay. Persisted inbox namespace,
project/branch and stable IDs make explicit enqueue ACK-loss recovery safe;
standard prompt outcome-unknown is never blindly resent. Source labels/evidence
are untrusted, not identities or permissions. New canonical input is
`xdog.session_input.v1`; old `xdog.external_event.v1` is historical codec only.

Definitions persist, registrations do not. Receiver disconnect pauses collection;
restart alone does not reconnect. No automatic harness/observer startup,
permanent installed daemon, hosted broker, TCP/Windows transport, API/web sources,
scripts or claw/flow-specific adapters. Eyes lifecycle controls are not agent
event delivery endpoints. Old host/capability/descriptor/env wiring is removed.
See `docs/acp-session-routing.md` and package README for protocol/commands/limits.

### `xdog.tui` and `xdog.site`

`xdog.tui` is a dependency-light component/rendering library, not an application policy layer. Components render ANSI line arrays; `TUI` performs differential updates in the main terminal buffer so scrollback is preserved.

`xdog.site` is a Flask documentation/marketing site with a workflow viewer/runner. The HaveFun runner trusts submitted workflow code, is not a sandbox, and is suitable only for trusted/local use. Deployments must set `XDOG_SITE_SECRET` to a strong externally managed value instead of relying on the development fallback. The demo integration dynamically imports flow/agent/ai and assumes the workspace/umbrella installation; check package metadata before treating `xdog-site` as independently runtime-complete.

## Cross-cutting invariants

- Core state models favor frozen dataclasses and replacement over in-place mutation.
- Agent and provider paths are asynchronous; avoid blocking I/O in their event loops.
- Skills with session scope belong in the cacheable prompt prefix; turn-scoped skill bodies are messages so removing them does not invalidate that prefix.
- Flow script workspace checks are audit/confinement guards against accidental access, not a hostile-code sandbox.
- Do not infer current capabilities from old module names or protocol adapters; check package registration and public exports.
