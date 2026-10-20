# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Repository and toolchain

XDOG is a local-first Python 3.12+ `uv` workspace; its core tools do not require a hosted control plane or database. Nine distributions share the `xdog.*` namespace under `packages/`: `ai`, `agent`, `flow`, `coding`, `claw`, `tui`, `site`, `eyes`, and `skills`. Run development commands from the repository root so the root `pyproject.toml` configuration applies.

```bash
# Create/update the workspace environment
uv sync

# Core test, lint, and type-check commands used by CI
uv run python scripts/test.py  # all configured test paths, balanced package workers
uv run pytest -q              # direct pytest alternative; supports normal selection flags
uv run ruff check packages
uv run mypy --strict \
  -p xdog.ai -p xdog.agent -p xdog.tui -p xdog.flow \
  -p xdog.site -p xdog.claw -p xdog.coding -p xdog.eyes -p xdog.skills

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
claw   -----> agent/ai + tui + eyes
site   -----> flow/agent/ai (workflow demo integration)
tui           standalone terminal rendering/components
```

### `xdog.ai`: model and protocol foundation

`packages/ai/src/xdog/ai/` defines the provider-neutral model, context, streaming event, completion, embedding, and web-search interfaces. `providers/runtime.py` resolves provider/model names; the production provider currently registered is GitHub Copilot. Files under `protocols/` implement Anthropic/OpenAI wire formats used according to model metadata—they are not separate configured providers. `proxy.py` exposes an Anthropic-compatible local HTTP/SSE facade over the same runtime.

Keep provider/authentication, model routing, and wire-protocol adaptation separate when changing this layer.

### `xdog.agent`: reusable tool-calling runtime

`packages/agent/src/xdog/agent/agent.py` owns stateful conversations, lifecycle events, cancellation, steering/follow-ups, skills, and persistence snapshots. `agent_loop.py` converts history to an `xdog.ai` context, streams a response, validates and executes tool calls, appends results, and repeats until the turn ends.

Coding, claw, and SDK-backed flow agent nodes reuse this runtime. Flow's external CLI backends intentionally use `CliRunner`; do not introduce a second in-process tool loop for SDK-backed nodes. Tool calls execute in parallel by default, while preparation/finalization hooks are serial. Callers enforce tool permissions through hooks—the generic tools themselves are not a security boundary. Built-ins are registered at import time and include `bash`, `filesystem`, `current_time`, and `submit_result`.

Agent's generic extension `ViewRegistry` exposes explicitly granted owner-bound
read-only JSON snapshots through `api.read_view`, independently of TUI approval.
Actual client identity and pinned bindings fence reads and later callback output;
unavailable is not an empty dataset. Queries/snapshots are finite bounded JSON.
Coalesced `view_changed` hints run on the existing worker owner without starting
workers/models. This layer knows no Goal schema and owns no business lifetime.
See `docs/extension-owner-views.md` and `docs/shared-goal-extension-verification.md`.
The shared Goal display is separately bound by actual capability adapters; loading
this generic protocol alone still grants no business view or task authority.

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

`packages/coding/src/xdog/coding/main.py` selects interactive TUI, one-shot print, or the independently managed ACP ingress/session client. Coding's former JSON-lines frontend-control RPC mode is removed. `core/sdk.py` resolves layered configuration, model metadata, permissions, skills, tools, and persisted state before constructing an `xdog.agent.Agent`. Configuration precedence is global settings, project `.coding/settings.json`, then CLI overrides.

`core/agent_session.py` rebuilds the prompt and checks compaction before each turn, then adapts agent events to the selected UI mode. Session history, settings, summaries, and branches are persisted as JSON by `core/session_manager.py`. `core/sessions/` owns the single controller, writer lease, route ownership and separate durable automatically consumed inbox; TUI/SDK/ACP share that execution path, and offline ingress never constructs a second Agent. Project context discovery is non-recursive from the working directory: `AGENTS.md`, `AGENTS.md`, `.coding/INSTRUCTIONS.md`, and `.coding/instructions.md`, each capped at 64 KiB. Permissions belong in the coding client's pre-tool hook, not in the generic agent runtime. Preserve the default fail-closed behavior: read-only calls may run automatically, mutating calls require approval, and unattended execution may mutate only when `allow-all` was explicitly selected. Permission gates are not an OS sandbox.

Coding reuses optional `xdog-skills` through the `skills` extra. Installed package
presence supplies the capability; there is no Goal enable setting. Startup runs on the same
session owner, not import/TUI/ACP. CodingGoalHost binds exact inbox namespace,
session/branch/workspace, existing supervised role children and original-ID durable
intake; source labels grant no authority. SDK capability ownership retains the same controller's
supported own-thread callback loop across awaits; callers dispose their session
and teardown joins actual children/Goal commits before releasing that owner.
Idle navigation retires the original capability without moving stored Goals.
No turn-end review/Goal summary is hardcoded into Coding; readonly/default ask
stays fail-closed. See `docs/coding-goals.md` and the adapter verification record.

### `xdog.claw`: persistent multi-channel assistant

Claw is an application, not a public Python SDK. Its supported user surfaces are
`xdog-claw`, configuration, channels and the explicitly documented gateway/ACP
protocols. The root package deliberately re-exports no runtime classes. Internal
modules remain importable for application composition and tests, without a stable
embedding API promise. Reusable agent functionality belongs in `xdog.agent`.
Do not remove required gateway/MCP/extension-worker communication as SDK cleanup.

The main path is:

```text
TUI Unix socket / Weixin / existing ACP bridge
  -> Orchestrator.route_message
  -> core/inputs InputStore + application-owned InputService
  -> per-group GroupController (fenced operation/input owner through receipt settlement)
  -> GroupRuntime's active AgentSession
  -> prompt rebuild and pre-turn compaction
  -> xdog.agent stream/tools
  -> JSONL transcript persistence and channel output
```

Ordinary turns now persist protocol-independent immutable envelopes (including
host-only context, attachments and reply target) before application admission.
`claimed` is not `running`; preparation failure and execution failure are distinct,
and crashed started work is never automatically replayed. Busy ordinary input is
not implicit steering. Explicit steer/follow-up remains Agent-owned input control.
Native `run_ack` follows the actual durable commit; the reader stays responsive
during that commit. Native/ACP connection detachment does not cancel accepted
application work. Busy Native intake commits into the same Inbox; connection-local
result waiters are not execution authority. Native controls/snapshots/abort bind
the original admitted message+attempt+session, so a queued or delayed old view
cannot touch another run or an explicit retry. Commands reserve fenced tokens;
pre-start cancellation and late cleanup can release only their original token.
The old mixed MessageQueue and global Gateway running table are removed.
GroupConfig has no queue mode/debounce/per-group-concurrency/trigger-pattern
knobs; only live pending capacity and daily/idle reset settings remain. Agent
explicit input modes and Eyes detector debounce are separate live APIs.
Async application routes now use owned `prepare_session` IO with detached frozen
PreparedSession/PreparedHistory, then construct/publish the Agent on its existing
loop. Workspace/prompt IO and the shared Agent SkillPlacement resolve off-loop;
no snapshot grants tool permission or changes Goal lifetime. Cold native tool
inventory preparation lives inside its existing cancellable job, not the reader.
Ordinary history now uses an original-owner immutable batch on an owned worker,
checking semantic prefix/context rather than length alone. Existing undo journal
covers actual JSONL/catalog/index before cache/cursor/receipt publication; cancel
settles actual commit, failure cannot enter append fallback or auto-reset into
another model, and real process recovery never replays tools. History reads share
the commit lock; remaining startup/status/MCP/image/application IO and cancellation
callsite audit is not complete. See `docs/claw-owned-session-io-verification.md`
and `docs/claw-owned-transcript-commit-verification.md`.

ACP is now a thin adapter over the application-owned input service: it has no
receipt database, consumer, observer/Goal query or report-delivery policy. Its
namespace/status are the exact same facts used by native intake. Closing an ACP
endpoint detaches waiters, not application execution or storage. Endpoint lifecycle does not interrupt work; explicit original-input cancellation
and actual application shutdown/crash use their separate execution owners. No old-format upgrade is
provided; the retired `acp/inbox.py` and shared observer store are removed.
Managed observers own `<data_dir>/observers/journal.sqlite3`; core input receipts
live in `<data_dir>/inputs/inbox.sqlite3`. Source cursors and immutable outboxes
commit together; cross-store intake is original-ID idempotent handoff, not a SQL
join or a second authority. Source stop does not withdraw accepted input or stop
its report. `eyes cancel_notifications` is separate; actual pending withdrawal
uses the core input owner. No operation reverses started effects. A schema-neutral
Agent `LeasedSqlite` helper owns IO only, never domain schema or planning policy.
Observer journal formatv3 captures a host-only immutable ReplyReference from the
actual creating input/tool scope. Weixin reports use original account/sender/context;
Native notices use only the original live endpoint, never a newly connected peer.
No creator destination means not-required, not group broadcast; unavailable original
recipient remains known-not-sent/pending. Stop notifications separately; source stop
still does not retract accepted reports. No old journal upgrade is performed.
The broader assistant-core refactor remains in progress: Coding now adopts the
standalone optional Goal; Claw has a group-owned optional adapter replacing its
legacy Goal manager. Claw has optional skills/eyes/acp install extras and cold
base/two-host evidence. The same packaged Goal display and generic owner-bound
views are verified in both actual source and cold installed hosts. Managed
observers now use generic Core per-message output receipts, not an aggregate
report sender. Rebuilt cold artifacts verify the same output IDs/original peer
across restart without model replay and both independent cancellation modes; see
`docs/observer-message-output-verification.md`. Inert group queue controls are
removed; final47-item audit, generic retired-owner investigation, rebuilt final
artifacts and whole-branch review remain pending.
Ordinary output receipts live in `<data_dir>/outputs/journal.sqlite3`: per-message
send attempts distinguish delivered/pending/proven-not-sent/unknown independently
from Agent execution. Unknown sends are never automatically replayed. Native
staged submissions retain original IDs/images over ACK loss; local editable
unsent drafts remain process-memory state, not an implicit plaintext disk spool.
See `docs/claw-assistant-core-requirements.json` for exact acceptance status.

Eyes callbacks use thin ACP intake into the application-owned Core Inbox as canonical external input; optional Goal notices use the same application intake directly as canonical automatic inputs. Each group has one logical main Agent and one execution route; session files are context/history storage, never Goal ownership or event destinations. Both paths use the same GroupController and current AgentSession. A group runtime owns its workspace, tools, memory, prompt builder, transcript store, one active cached agent session and optional capability bindings; Goal owns its separate domain storage. Transcripts are JSONL with a `sessions.json` index; `SessionManager` is a compatibility alias for `TranscriptStore`, not an LRU multi-session manager.

Prompt construction intentionally separates a cacheable static prefix from mutable workspace, memory and environment content. Goal facts are queried through the optional capability, never a hardcoded per-turn summary. Compaction flushes durable memory, summarizes through the LLM, archives the transcript, then replaces older history. Memory uses vector+BM25 search when optional dependencies initialize and falls back to keyword search otherwise.

Importing `xdog.claw.core` intentionally registers claw-specific tools. Preserve these side-effect imports when reorganizing code.

Claw optionally exposes a private Unix ACP endpoint via `acp_socket_path` or
`gateway start --acp-socket`. `acp/backend.py` implements the shared Agent ACP
backend with explicit `group:<id>` addresses and exact workspace validation.
Prompt/enqueue first persist canonical session inputs in the leased group inbox,
then the application consumer uses the existing group exclusion/active AgentSession.
No TUI, message approval, second Agent loop or Eyes-specific handler is involved.
Pending/running survive receiver-only restart. Application shutdown/crash marks running interrupted, never replayed.
Group scope intentionally follows active conversations, unlike coding's exact
session/branch binding. Existing Claw tool policy remains unchanged. See
`docs/claw-eyes-acp.md` for setup, scope, display and recovery limits.

Claw's old TaskScheduler/task tool is removed. Explicit `eyes_enabled: true`
(default false) enables gateway-owned `eyes` create/list/status/cancel. The host
binds the current group/workspace/private ACP endpoint and owns a separate
definition/cursor/outbox/report journal. Execution facts are read from core receipt
APIs, never stored or changed by the ACP adapter. One observer has at most one in-flight execution.
Claimed queue work is not started: explicit core withdrawal fences admission under
the group lock. Source/reports/worker cancellation are distinct Agent decisions;
observer enabled/revision fields never automatically cancel an accepted input.
Known-not-sent reports recover without model replay; unknown sends/interrupted
execution require review. The 2026-10-18 decision removes all legacy task
scheduler implementation and migration compatibility: no `tasks_file` configuration,
no reading/importing `scheduled_tasks.json`, no migration backups or confirmation
states. Leave users' old data untouched; create new Eyes observers only on explicit
user request. Eyes scheduler sources and historical transcript labels remain;
no permanent Eyes daemon or second Agent loop is started.
See `docs/claw-eyes-scheduler-{plan.md,requirements.json}` for retired scheduler
scope; `docs/claw-assistant-core-requirements.json` tracks the current refactor.

Claw's optional `xdog-skills` capability is available automatically when installed;
there is no global/project/session Goal enable switch. Without the distribution,
ordinary assistant use opens no Goal journal. Application startup opens installed
Goal data and recovers committed notifications; active Goals alone never cause
model polling. Package presence also exposes the built-in `goal` skill through
existing namespace discovery; its body loads on demand without copying or manual
registration. Availability is not user delegation and creates no Goal/worker.
The adapter attaches parent tools to the existing active AgentSession and starts
workers/reviewers only through its existing ChildSupervisor. Core ordinary turns,
Gateway, ACP, prompt builder and native TUI contain no Goal lifecycle policy.
The former GoalManager, implicit turn-end review, Goal timer, task report watcher
projection and native Goal UI are removed, without an upgrade/compatibility path.

Goal owns `<data_dir>/groups/<id>/goals/journal.sqlite3` and its leased domain
history/evidence/outbox. Its owner is stable group + canonical workspace + Core
inbox namespace, unlike Coding's exact session/branch binding. Original worker
references retain their actual transcript/branch/child identity; after rotation,
terminal facts come from validated off-loop ChildStore reads, never an offline
Agent or replay. `goals.json` is refused byte-preserving when the Goal package is installed,
without creating a substitute empty Goal journal; package-absent ordinary chat
does not read it.

Only explicit `request_complete` creates independent review. Reports are proposals,
not acceptance or completion; actual user input authorizes protected user changes.
Role tools are narrow capabilities bound to real selected tool objects, not role
labels or tool names; readonly/MCP/extension gates remain. Goal status, source stop,
notification cancellation and child cancellation are independent explicit actions.
Unfinished Goals do not veto daily/idle context rotation; actual active children
still hold generic context ownership. `/new` rotates chat context, not group scope.
Shutdown settles admitted capability startup and actual children before releasing
Goal storage. Shared extension UI and final acceptance remain work in
progress; the old native Goal display is not retained as a fallback.

Child supervision has no fixed runtime deadline, model-call/token lifetime budget,
concurrency count or retained-record count. The parent owns lifecycle. Protocol
message/image/file bounds and tool permissions remain. Complete histories use
validated fragments instead of dropping tool pairs. Generic Agent auto compaction
still defaults off; Coding/Claw child factories explicitly enable the existing
built-in policy only with actual model metadata, and archive original paired
history durably before a summary request. Failure retains history; compaction is
context management, never a replacement task budget.

### `xdog.eyes`: observation and ACP activation layer

Like flow, eyes owns a versioned JSON definition, frozen models, whole-definition
validation, asynchronous executor, CLI and packaged `eyes` skill.
Sources collect evidence, detectors emit events, and EventRouter dispatches
explicit routes to registered EventHandler implementations; ACP is one built-in
handler. Its connection/payload details stay outside the generic executor/runtime. Regular-file content changes use bounded no-follow polling. Scheduler sources
support one-off zoned `at`, monotonic `every_seconds` and five-field `cron` with
explicit IANA timezone. `scheduler.due` detectors/events reuse the same routing
and bounded handler delivery; no task/agent execution occurs in a source. Paused
deadlines are skipped, not replayed; repeating schedules start after all handlers
are ready. See `docs/eyes-scheduler-source.md` for timing, evidence and lifecycle.

Eyes depends on the optional version-bounded ACP SDK and bounded-version croniter,
never on agent/ai/coding/claw/flow, and does not execute tools or an agent loop. A coding
callback connects directly to an explicitly configured private Unix ACP endpoint
and exact session ID; it does not launch coding CLI or inspect coding registry.
Stdio is an optional generic third-party ACP transport. Loading a skill does not
start observation, a model, a receiver or TUI.

Coding's independently managed lightweight ACP ingress forwards live prompts or
persists negotiated XDOG enqueue messages without constructing AgentSession.
TUI/owner absence does not prevent durable intake while the receiver is running.
An activated owner automatically consumes pending messages; there is no message
approval, human wake-up budget or approval-bearing ACP resume. Tool execution
still uses the existing permission policy. The sole controller selects fixed
batches using max_messages_per_turn (default10, strict integer1..128), preserving
independent input boundaries, source/attachments/IDs and one shared output stream.
list/show are read-only; cancel/retry are separately fenced Inbox controls. Message and execution-attempt identities differ; crash running becomes
interrupted, never automatic side-effect replay. Persisted inbox namespace,
project/branch and stable IDs make explicit enqueue ACK-loss recovery safe;
standard prompt outcome-unknown is never blindly resent. Source labels/evidence
are untrusted, not identities or permissions. New canonical input is
`xdog.session_input.v1`; old `xdog.external_event.v1` is historical codec only.

Standalone definitions persist, registrations do not. Receiver disconnect pauses collection;
restart alone does not reconnect. No automatic harness/observer startup,
permanent installed daemon, hosted broker, TCP/Windows transport, API/web sources,
scripts or harness-specific Eyes handlers. Claw's ACP backend belongs to Claw,
not Eyes. Eyes lifecycle controls are not agent
event delivery endpoints. Old host/capability/descriptor/env wiring is removed.
See `docs/acp-session-routing.md` and package README for protocol/commands/limits.
The separate opt-in embedding APIs `ObserverService`, `initial_managed_state`
and `run_managed_scheduler` support a host-owned journal without changing CLI
semantics. Strict checkpoints bind source/definition and contain data only;
cursor+whole pending Delivery must persist before send, including original JSON
number representation. Busy managed schedules skip ticks without overlapping;
accepted one-offs survive downtime. Claw restores only its managed time observers,
never automatically restarts ordinary file observation. Eyes still imports no harness.

### `xdog.skills`: common medium-level skills and extension code

The `xdog-skills` distribution is an inert hub, not an agent harness. Domain code
lives in independent modules; shared `skills/<slug>` and `extensions/<id>` use
Agent’s existing generic discovery. It currently ships the `goal` skill and the
`xdog.skills.goal` domain, plus `goal_ui`. Flow and Eyes remain independent
packages; their engines/skills are not copied here. Installing/listing skills
does not start execution, storage or display workers.

#### Goal domain

The ninth distribution depends directly only on `xdog-agent` and stdlib. Frozen
models/codecs and GoalStore own private leased/versioned Goal history, reports,
review snapshots, evidence artifacts and notification outbox. Owner/workspace is
host-supplied, not tool JSON/provenance; wrong-owner/old-format/corrupt data is
refused byte-preserving. Recovery interrupts started/unknown work, never starts
an Agent or replays side effects. Package import starts no host/UI; real host ownership attaches an installed
capability automatically without delegating work.
GoalService uses small async host ports, explicit task acceptance and independent
review only on request_complete. Bound role tools read original history; Goal-local
no-follow evidence fingerprints reject stale completion. Skills and derived views
do not activate execution. See docs/goal-kernel-verification.md for the real SDK
Supervisor and cold Goal-only lifecycle evidence. Coding has an optional real host
adapter and cold installed lifecycle (docs/coding-goal-lifecycle-verification.md).
Claw has the group-owned adapter and no legacy backend. Goal now ships a readonly
projection and one packaged goal_ui worker with actual both-host bindings. Coding
uses exact session/branch and Claw current-parent/stable-group scope; shared
GoalDisplay owns only readonly data-read settlement, never business lifetime.
Known display_only profiles have no validation/tool/provider/entry authority;
unknown/default extensions keep fail-closed gates. Both native TUIs use generic
ExtensionView, not Goal schema. Same-wheel/same-extension cold Coding/Claw and
Goal-only-without-TUI consumers are verified; both-host owner/resource boundaries are verified. See `docs/shared-goal-extension-verification.md`.

### `xdog.tui` and `xdog.site`

`xdog.tui` is a dependency-light component/rendering library, not an application policy layer. Components render ANSI line arrays; `TUI` performs differential updates in the main terminal buffer so scrollback is preserved.

`xdog.site` is a Flask documentation/marketing site with a workflow viewer/runner. The HaveFun runner trusts submitted workflow code, is not a sandbox, and is suitable only for trusted/local use. Deployments must set `XDOG_SITE_SECRET` to a strong externally managed value instead of relying on the development fallback. The demo integration dynamically imports flow/agent/ai and assumes the workspace/umbrella installation; check package metadata before treating `xdog-site` as independently runtime-complete.

## Cross-cutting invariants

- Core state models favor frozen dataclasses and replacement over in-place mutation.
- Agent and provider paths are asynchronous; avoid blocking I/O in their event loops.
- Skills with session scope belong in the cacheable prompt prefix; turn-scoped skill bodies are messages so removing them does not invalidate that prefix.
- Flow script workspace checks are audit/confinement guards against accidental access, not a hostile-code sandbox.
- Do not infer current capabilities from old module names or protocol adapters; check package registration and public exports.
