# Changelog

Notable changes from 1.0.0 onwards. All seven packages share one version number
and are released together.

The format is loosely [Keep a Changelog](https://keepachangelog.com/), and the
project follows [semantic versioning](https://semver.org/).

## [Unreleased]

Nothing yet.

---

## [2.3.0] — 2026-09-11

### Added

- Reusable permission, details, input, status, and transcript message components
  in `xdog-tui`, shared by Coding and Claw without changing execution policies.
- Current-directory session selection for `xdog-coding -r`, including explicitly
  labeled legacy sessions without directory metadata.

### Changed

- Compact thinking previews and tool summaries retain full content in details.
  Details and permission panels appear below the editor; details open at the
  beginning and support explicit follow navigation.
- Improved activity timing, contextual keyboard hints, terminal themes, bounded
  layouts, and user-message styling while preserving native scrollback.
- Synchronized runtime version constants with the workspace release version.

### Fixed

- Restored session model, context usage, and user-message history presentation.
- Prevented multiline tool summaries from corrupting terminal row accounting.
- Preserved valid reasoning replay identities in proxy conversions and skipped
  invalid Responses reasoning identities without writing warnings into the TUI.

### Validation

- Added component, session-picker, and actual tmux streaming/resize regression
  coverage. Windows Terminal/SSH key interception and IME still need manual checks.

---

## [2.2.0] — 2026-09-10

### Added

- **Lossless protocol-native proxying** (`xdog-ai`). `/v1/messages`,
  `/v1/messages/count_tokens`, `/v1/responses`, and
  `/v1/chat/completions` now route through immutable native requests when the
  selected model advertises the exact wire protocol. Current, beta,
  vendor-specific, and unknown future fields survive without being projected
  through provider-neutral message types, and native JSON/SSE status, errors,
  framing, and safe response headers are retained.
- **Anthropic token counting** (`xdog-ai`). Native Anthropic-capable models use
  the upstream count endpoint without generating a message. Known models that
  lack that capability receive a deterministic estimate marked with
  `x-xdog-upstream-protocol: best-effort`; authentication, transport, and
  upstream failures never fall back to estimation.
- **Inline terminal rendering primitives** (`xdog-tui`). Main-buffer rendering,
  bounded details, inline layouts, event queuing, and expanded terminal
  lifecycle handling are now reusable library components, with acceptance and
  regression coverage across coding and claw.

### Changed

- **Coding and claw TUIs now share the compact inline behavior** (`xdog-coding`,
  `xdog-claw`, `xdog-tui`). Prompt editing, assistant output, tool execution,
  permission prompts, footer rendering, scrollback preservation, and terminal
  restoration now use the common TUI implementation.
- **Copilot model metadata records exact generation protocols** (`xdog-ai`).
  Native Responses, Chat Completions, and Anthropic requests are admitted only
  when the synchronized catalog advertises the requested protocol; Responses
  keeps a strict stateless best-effort fallback while Chat remains
  native-only.

### Security

- **Proxy credentials stay listener-local** (`xdog-ai`). Client
  `Authorization` and `x-api-key` headers are excluded from native upstream
  requests, vendor-resolved credentials remain authoritative, and transport
  failures are returned without exposing sensitive upstream details.

---

## [2.1.2] — 2026-08-13

### Fixed

- **`submit_result` rejected every non-object result** (`xdog-flow`,
  `xdog-agent`). An agent node whose single output port is an array could never
  succeed. The tool declared `result` as an object, and argument validation
  runs *before* the per-call schema, so the array the node's own schema
  demanded came back as "expected object, got list" — the tool errored, the
  sink stayed empty, and the run failed with "agent did not submit a result via
  submit_result".

  That message blames the model, and the model was innocent; watching the event
  stream it said so itself: *"the submission tool rejected the array (it
  requires an object)"*. `result` is now untyped — JSON Schema for "any value" —
  leaving `flow_output_schema` as the only validator, which is where the
  contract actually lives. The system prompt agreed with the old, wrong rule
  and now branches on `agent_submits_object`, in both engines.

  No stub suite could catch this: a stub *is* the submitted result, so it
  supplies the very value the tool exists to elicit. It appears only against a
  real model, and only for a node whose one output port is not a plain string.

### Changed

- **Renamed `x-dog` to `xdog`.** The import namespace and every published
  package were already `xdog`/`xdog-*`; only the repository and the
  human-facing string were hyphenated.

  **This moves your state.** Config is now `~/.config/xdog` and data
  `~/.local/xdog`; the scheduler's bundles and registry move from
  `~/.local/share/xdog-flow` to `~/.local/share/xdog/flow`. Nothing migrates
  automatically and there is no compatibility fallback, so **after upgrading
  you will appear logged out** — with no error, because nothing looks in the
  old place. Move them yourself:

  ```console
  $ mv ~/.config/x-dog ~/.config/xdog
  $ mv ~/.local/x-dog ~/.local/xdog
  $ mv ~/.local/share/x-dog ~/.local/share/xdog        # claw, coding state
  $ mv ~/.local/share/xdog-flow ~/.local/share/xdog/flow
  ```

  `xdog-flow` remains the command name and the systemd unit name; only the
  directory moved. GitHub redirects the old repository URL, so existing
  remotes and the links in 2.1.1's metadata keep working.

---

## [2.1.1] — 2026-08-12

### Fixed

- **A WeChat channel is one conversation, not one per sender** (`xdog-claw`).
  The group id was derived from who was speaking, so every peer got a private
  session, workspace, `MEMORY.md` and `IDENTITY.md`. Asking the agent its name
  returned its routing key, because in a group nobody had ever configured, the
  routing key *was* the identity.

  The conversation is now a property of the channel, chosen once at login:

  ```console
  $ xdog-claw channel login --weixin --group main
  ```

  Everything keyed by group follows it — session, memory, persona, goals,
  scheduled tasks. Reply addressing stays separate, in the channel's own peer
  map, and now tracks whoever spoke most recently rather than only the first
  sender. A change of peer is logged: on a personal bot it means someone else
  reached it, which belongs in the journal rather than being inferred from a
  stray answer.

  Existing `weixin:<peer>` groups are left on disk; there is no history
  migration. Note that **no peer admission check ships with this** — every
  sender on a channel reaches the bound group. See
  `docs/one-agent-many-channels.md`.
- **Expired Copilot credentials say so** (`xdog-ai`). The proxy failing after a
  few days was never a refresh bug: the Copilot JWT refreshes correctly. What
  expires underneath is the stored GitHub OAuth token, and GitHub's 401 escaped
  as a bare `HTTPStatusError` that the proxy rendered as a **500 `api_error`** —
  so the client reported an internal error for something that is neither
  internal nor a bug, and nothing said that one login would fix it.

  A 401/403 from the token exchange now raises `AuthExpiredError` naming the
  command, and the proxy returns a 401 `authentication_error`. A 500 from GitHub
  is deliberately left generic: telling someone to sign in when signing in
  cannot help is worse than a plain error. Each exchange logs its expiry — never
  the token — because a silent refresh and no refresh at all look identical from
  the outside, which is what made this take days to pin down.

---

## [2.1.0] — 2026-08-11

### Added

- **`skills` on agent nodes** (`xdog-flow`). A node names skills and their
  instructions reach the model:

  ```jsonc
  {"id": "author", "type": "agent", "skills": ["flow-workflows"]}
  ```

  They resolve from a `skills/<name>/SKILL.md` directory **beside the workflow
  file** — part of the artifact, like the sibling modules a `run:` node imports,
  and copied into a bundle — or from any installed package. Never from the
  machine's own skill directories: a workflow that picked up whatever happened
  to be on disk would behave differently for two people with no way to tell from
  the file. An unresolvable name fails `validate`, because an agent asked to
  produce a format it was never shown does not fail — it produces something
  plausible and wrong.
- **`xdog-flow scheduling stop` / `start`**, and `install --no-start`.
  Installing and arming are separate decisions.

### Changed

- **The Agent decides where a skill's text goes**, for every product. The index
  (one line per skill) goes at the front of the system prompt; an active
  session-scoped body goes behind it, still in the cacheable prefix; a
  `scope: turn` body goes in as a message, because it will be removed again and
  moving it in and out of the prefix costs a full uncached re-send twice.
  Resolution stays with the caller — only it knows where to look.
- Reading skills no longer creates directories. Constructing a `SkillManager` to
  *look at* skills used to `mkdir` the directory it looked in.
- **Inter-package dependencies are pinned** (`xdog-agent>=2.1.0`). All seven
  packages share a version and are released together, but nothing enforced that
  at install time, so a resolver could pair this release with an older sibling
  and fail at run time with an ImportError.

### Fixed

- **`coding` sent every tool definition twice** — once as API tool definitions,
  once rendered into the system prompt. About 616 tokens on the four builtins,
  on every request. It was not a fallback for models without native tool
  calling: the section was emitted unconditionally, and nothing read
  `supports_tool_calls`, a field that had existed on `AgentConfig` and the `ai`
  `Model` type all along with no reader.
- **`supports_tool_calls` is now filled and honoured.** flow, coding and claw
  resolve it from the provider; the Agent leaves tools out of the request when
  it is False, since the protocols write `body["tools"]` with no check of their
  own. The field is tri-state: `None` means "nobody looked" and behaves like
  True, so a caller that says nothing is unaffected.
- **The `flow-workflows` skill was invisible in a checkout.** It was
  force-included into the wheel from outside the package, so discovery found it
  in production and nothing in development — the one environment where a
  `skills:` reference gets written and tested.
- **A bundle was missing `pyyaml`.** The vendored dependency list is written by
  hand and drifted the moment a bundle first imported the skills package; the
  unit died with `ModuleNotFoundError: No module named 'yaml'`. A test now scans
  the vendored sources for unguarded third-party imports.
- **`scheduling uninstall` deleted a bundle out from under a run.**
  `disable --now` disarms a timer but does not end a run in progress. Uninstall
  now stops first.

---

## [2.0.1] — 2026-08-10

### Fixed

- **A non-required loop-carried input port broke the compiled engine.** The
  interpreter passes every declared port, defaulting an unfed one to `""`; the
  generated module builds its inputs from the edges that actually fired, so a
  port that is absent by design on the first loop pass was missing entirely and
  the node function took it positionally:

  ```
  TypeError: node_implement() missing 1 required positional argument: 'report'
  ```

  The workflow ran correctly interpreted and died compiled. It had passed
  `validate`, passed its test suite, and run by hand three times; it failed
  inside a systemd timer, which is the environment least able to explain itself.
  Generated signatures now default their optional ports.

### Changed

- `scheduling install` can record a workspace without `--confined`. Tying the
  two together made one of them unreachable: a workflow whose script nodes shell
  out cannot be confined — which includes anything that runs a test suite — and
  such a workflow had no way to say *where* it should work. It silently got the
  bundle's own directory.

### Added

- `packages/flow/examples/service_builder/` — an unattended workflow that builds
  a service from a one-paragraph idea, one hour-sized increment per run, halting
  itself when the acceptance criteria are met or when it stops making progress.
  See [`docs/service-builder.md`](docs/service-builder.md).

---

## [2.0.0] — 2026-08-10

A run now has a **workspace**, and each node kind is held to it differently. This
is a major version because it changes behaviour for workflows that already work:
a script node that reads or writes outside its workspace now fails.

### Added

- **Every run has a workspace** (`xdog-flow`), defaulting to
  `<workflow dir>/runtime`. Relative paths in the `filesystem` tool resolve
  there, and it is where a node's output belongs. On by default, enforcing
  nothing by itself. `--workspace DIR` overrides it, `--allow-path DIR` grants
  another tree.
- **Agent nodes are briefed.** Every agent node's system prompt now names its
  workspace and granted directories, with an instruction not to go outside them —
  whatever tools the node declares. Nothing verifies that the model obeys, and
  the docs say so: a node's access cannot be inspected at one chokepoint, so this
  is a promise the agent keeps.
- **Script nodes are audited.** A PEP 578 audit hook refuses reads and writes
  outside the workspace, covering a node's whole `code` including its top-level
  statements. `ctx.workspace`, `ctx.allow_paths` and `ctx.confined` tell a script
  where it stands.
- **`--confined`** additionally refuses calls the hook cannot follow —
  `subprocess`, `ctypes`, `os.system` — and refuses up front any workflow whose
  agent nodes leave the process (the `bash` tool, a CLI backend).
- **`xdog-flow scheduling install --confined`** records the grant in the systemd
  unit it writes, or in the install registry for hook workflows. The grant is
  never part of the workflow file: a workflow that could declare its own access
  would not be confined by it.
- Subflows inherit their parent's workspace, grants and confined flag.
- `docs/script-node-confinement.md` — what a child process plus Landlock would
  add, measured rather than argued, and why it is not built yet.

### Changed

- **BREAKING:** a script node can no longer read or write outside its workspace.
  Grant what it needs with `--allow-path`, or point `--workspace` at the
  directory it already uses.
- `execute()` takes `workspace`, `allow_paths` and `confined`. All optional; the
  workspace defaults rather than being absent.
- Import roots for the audit hook are the interpreter's own trees, not all of
  `sys.path`. The old behaviour made a compiled bundle grant itself its own
  directory while the interpreter granted nothing, so the same workflow behaved
  differently depending on how it was run.

### Fixed

- Confinement reached the interpreter but not codegen, so a workflow that
  `--confined` refused to let write outside its workspace wrote there happily
  once compiled. The generated module now reads its bound from the environment.
- A structured agent node's `submit_result` instruction was appended to the raw
  system prompt, dropping the workspace briefing for exactly the nodes doing the
  most work — and only in the interpreter.
- `scheduling install --confined` recorded a workspace but never `FLOW_CONFINED`,
  which is what the bundle gates on, so the flag refused unconfinable workflows
  and then ran the rest unconfined.
- A script node's top-level `code` ran outside its own bound in both engines —
  in the compiled one, at import time, before `main()`.

---

## [1.1.0] — 2026-08-07

### Added

- **`inherit` on agent nodes** (`xdog-flow`). An agent node can start from
  another agent node's session — its messages and system prompt — instead of
  cold:

  ```jsonc
  {
    "id": "critique",
    "type": "agent",
    "inherit": { "from": "research" },
    "system_prompt": "You are now a harsh reviewer of your own work.",
    "prompt": "Critique the findings you just produced."
  }
  ```

  The node's own `system_prompt` and `model` override what it inherited. Tools
  are never inherited, so a node's capabilities stay readable from the node
  itself.

  The edge between the two may carry an empty `map`: it establishes order and
  reachability while the session carries the context. That keeps the format's
  central promise — data moves only along things the graph can see — because
  `inherit` is a static reference the validator checks, not an ambient channel.

  A node may inherit from itself, which in a loop is the point: it keeps its own
  context across iterations, so a reviser remembers what it already tried. The
  first pass has no session, which is not an error — the same lenient rule as a
  non-required loop-carried port.

  Strict at load, lenient at run. Because a missing session is tolerated, a typo
  in `from` would otherwise do nothing and never say so; the reference is
  therefore checked before anything runs, and only its absence is forgiven
  later. Ten rules, each with a distinct message. Two of them exist because they
  fail silently otherwise: a `deterministic` source returns memoised ports
  *without running*, so it can never produce a session at all; and a fan-out
  worker runs N times under one node id, so "the" session is ambiguous. CLI
  backends are rejected in both directions — the CLI owns its own session, and
  flow can neither read nor seed it.

  New example: `packages/flow/examples/research_critique.json`.

- **`Agent.dump()` / `Agent.restore()`** (`xdog-agent`). An `Agent` instance is
  a session; these are its two projections, covering the history, the system
  prompt (string or block form), the model, and the value half of
  `StreamOptions`. `StreamOptions.cancel` is excluded — an `asyncio.Event` is a
  live handle, not something that belongs in a file. Message serialization moved
  here from `xdog-coding`, where two other packages had been reimplementing it.

- **`sessions`** in the flow checkpoint schema, so a resumed run does not hand
  an inheriting node an empty context. Optional on read: a checkpoint written
  before this feature still resumes.

### Fixed

- **Restoring an `xdog-claw` session silently lost data.** Its transcript format
  flattened message content to a string, dropping every image, every thinking
  block including its `thinking_signature` — the continuity token for extended
  reasoning — and all but the first part of a tool result. Nothing failed; the
  restored agent was simply having a different conversation from the saved one.

### Changed

- **claw transcript entries changed shape** (`xdog-claw`, internal). An entry is
  now the agent's lossless dict with claw's `timestamp` and `usage` alongside. A
  tool result's role is `"toolResult"`, not `"tool"`, and `content` is a list of
  typed parts rather than a string — read it with `entry_text` /
  `entry_tool_calls`. Old transcripts are not readable; claw has no users, and
  keeping a lossy reader would only have preserved the bug above.

---

## [1.0.0] — 2026-08-06

First stable release. Seven packages under one `xdog` namespace, installable
side by side:

| Package | Import | What it does |
|---|---|---|
| `xdog-flow` | `xdog.flow` | Typed workflow format, validator, interpreter, Python compiler, systemd scheduler |
| `xdog-ai` | `xdog.ai` | Unified LLM provider API — chat, embeddings, web search, Anthropic-compatible proxy |
| `xdog-agent` | `xdog.agent` | Agent runtime: tool calling, structured output, steering, skills |
| `xdog-coding` | `xdog.coding` | Interactive coding-agent CLI with session management |
| `xdog-claw` | `xdog.claw` | Agent orchestration runtime |
| `xdog-tui` | `xdog.tui` | Terminal UI library with differential rendering |
| `xdog-site` | `xdog.site` | The documentation site |

What makes it 1.0 rather than another 0.57: every package passes
`mypy --strict`, and all seven are in the CI gate along with the full test suite
and `ruff`. Reaching that meant fixing real defects rather than adding
annotations — among them a coding CLI that could not process a single message,
a claw package that raised on import, and three packages that did not declare
dependencies they import.

The checks that landed alongside them are the durable part: CI imports every
distribution it publishes, compares each package's declared dependencies against
its actual imports, builds a session and rebuilds its system prompt, and asserts
that every file the shipped skill points at exists. Each of those reproduces a
failure that had already shipped.

Also in 1.0:

- **Stable error codes on `xdog-flow validate --json`** — eighteen of them, with
  a `hint` on about a third. Without a code, a caller reacting differently to
  "that port does not exist" and "those two types are incompatible" has to
  pattern-match English, which makes every reworded message a silent breaking
  change.
- **Skills** in the open [Agent Skills](https://agentskills.io/specification)
  format, discovered from installed packages: `pip install xdog-flow` provides
  `/flow-workflows`, and uninstalling removes it. Two deliberate departures from
  the format's reference clients — a skill can be **unloaded**, because its
  instructions go into the system prompt rather than the transcript; and the
  **model cannot unload one**, since a skill is often a constraint and the
  constrained party should not hold the release.

[Unreleased]: https://github.com/litterzhang/xdog/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/litterzhang/xdog/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/litterzhang/xdog/releases/tag/v1.0.0
