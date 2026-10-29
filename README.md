# XDOG

> **Linux · Python 3.12+ · local-first**

[![CI](https://github.com/litterzhang/xdog/actions/workflows/ci.yml/badge.svg)](https://github.com/litterzhang/xdog/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/xdog-flow?label=xdog-flow)](https://pypi.org/project/xdog-flow/)
[![Release](https://img.shields.io/github/v/release/litterzhang/xdog)](https://github.com/litterzhang/xdog/releases)
[![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](LICENSE)

**Composable model and Agent SDKs, terminal applications, typed workflows and
JSON-defined observations—running on a machine you control.**

**本地优先的模型与 Agent SDK、终端应用、类型化工作流和 JSON 观察工具。**
不依赖托管控制面或外部数据库；需要持久化的功能使用本地 JSON、JSONL、Markdown
和 SQLite。真实模型调用仍需配置对应 provider 和凭据。

[Website and documentation](https://xdog.942295.xyz) ·
[Releases](https://github.com/litterzhang/xdog/releases) · [Changelog](CHANGELOG.md)

## Packages · 九个包

All packages use the `xdog.*` namespace. Eight distributions are published to
PyPI; `xdog-site` is the workspace's documentation/demo application, not part of
the PyPI release matrix. The root `xdog` project is the development umbrella.

| Package / documentation | Import | Entry point | Responsibility |
|---|---|---|---|
| [xdog-ai](packages/ai/README.md) | `xdog.ai` | `xdog-ai` | Provider authentication/model routing, typed model APIs and protocol adapters; Copilot and Antigravity |
| [xdog-agent](packages/agent/README.md) | `xdog.agent` | `xdog-agent` | Reusable tool-calling Agent, snapshots, cancellation, host-owned registry and capability SDKs |
| [xdog-tui](packages/tui/README.md) | `xdog.tui` | Library | Shared terminal rendering, input, messages, editors and components; no application policy |
| [xdog-coding](packages/coding/README.md) | `xdog.coding` | `xdog-coding` | Coding assistant with TUI/print modes, permissions, sessions/branches and independent ACP intake |
| [xdog-claw](packages/claw/README.md) | `xdog.claw` | `xdog-claw` | Persistent assistant gateway; one main Agent per group, TUI/Weixin channels, memory and durable input/output |
| [xdog-flow](packages/flow/README.md) | `xdog.flow` | `xdog-flow` | Typed workflow JSON, validation, execution, code generation, tests, builder and local scheduling |
| [xdog-eyes](packages/eyes/README.md) | `xdog.eyes` | `xdog-eyes` | Independent observation daemon; files, remote content, schedules and pushed events with explicit delivery |
| [xdog-skills](packages/skills/README.md) | `xdog.skills` | Installed capabilities/skills | `goal` and `plan`, Goal records/reminders and optional readonly display extension; no Agent runner |
| [xdog-site](packages/site/README.md) | `xdog.site` | `xdog-site` | Documentation, package catalog, blog and trusted/local workflow demonstration site |

Choose **Coding** for repository work, **Claw** for a persistent multi-channel
assistant, **Flow** for a repeatable process, **Eyes** for observing changes or
schedules, and **AI/Agent/TUI** when building your own application.

## Install and authenticate · 安装与认证

Install only the applications you need, in the same Python environment when they
should discover each other's capabilities:

```bash
pip install xdog-coding xdog-claw xdog-flow xdog-eyes
xdog-ai login copilot
xdog-ai models copilot --sync
```

`xdog-ai login antigravity` selects the other registered provider. Authentication
and available model capabilities differ by provider; an API's existence does not
mean every model supports embeddings, image generation or web search. Use the
[AI reference](packages/ai/README.md) and the actual model catalog.

For development or the site application:

```bash
git clone https://github.com/litterzhang/xdog.git
cd xdog
uv sync
uv run xdog-coding --help
uv run xdog-claw --help
uv run xdog-flow --help
uv run xdog-eyes --help
```

## Agent SDK · 嵌入自己的应用

新 SDK 使用宿主自有的 `ToolRepo` / `SkillRepo`，支持注册、子集选择，以及先选择再打开
Capability。传给 Agent 的 Repo 是附加合并；技能正文不默认激活。
完整、无需凭据的运行示例见 [Agent Capability SDK quick start](packages/agent/README.md#capability-sdk-quick-start)。
这些示例描述当前源码接口，使用已发布版本时请核对对应版本的文档。

## Coding and Claw · 两个应用

### Coding: interactive repository work

```bash
xdog-coding --permission-mode ask
```

Coding owns the session runtime, approvals and child Agents. In `ask` mode,
classified reads normally run without approval; mutating/execute operations use
the existing approval policy. `ask-all`, `deny` and explicit `allow-all` are also
available, including session changes through `/permissions`. These controls are
application policy, **not an OS sandbox**.

Sessions and branches are Coding features. Explicit steering is separate from
ordinary queued input. An independent private ACP receiver can durably accept
inputs while an owner is offline; the activated owner consumes them through the
same session controller and tool permissions, not another Agent loop.

### Claw: a persistent assistant

Configure a provider, model and groups as described in the
[Claw guide](packages/claw/README.md), then use:

```bash
xdog-claw gateway start
xdog-claw gateway status
xdog-claw tui --group main
```

Each group has one logical main Agent and one execution route. Context resets
rotate history, not the group identity or its Goal owner; Claw does not offer
branch switching or a public Python embedding SDK. TUI, Weixin and default
private ACP intake share the application's durable Inbox and group controller.

Normal chat replies retain their original route. Automatic ACP input has no
implicit chat recipient. For proactive delivery the main Agent uses `message`
to list currently bound channels and send **one message to one channel**, avoiding
sending the same reply to both TUI and Weixin. Execution success and actual
channel delivery are recorded separately; uncertain sends are not blindly retried.

## Flow · 把稳定流程保存成文件

A workflow is a versioned JSON definition with **node-private typed ports** and
explicit edge mappings. There is no shared flat workflow state. Script, SDK
Agent, external CLI Agent, human and subflow nodes share validation and scheduling
semantics.

```bash
# From this checkout; graph validation and stubbed tests need no model login.
uv run xdog-flow validate packages/flow/examples/agent_calculator.json
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
uv run xdog-flow generate packages/flow/examples/agent_calculator.json -o calculator.py
```

Interpret directly or generate Python with the same frontier semantics. Generated
artifacts are **not universally dependency-free**: SDK Agent nodes need their SDKs,
and subflow/generated-runtime paths can still import `xdog.flow`. Consult the
[Flow guide](packages/flow/README.md) for portable builds, runtime dependencies,
checkpoints, loops, fan-out, companion tests and `xdog-flow scheduling`.

Flow's installed application capability exposes a thin non-interactive CLI-backed
`flow` tool. **Flow has no daemon.** The packaged `flow` skill is discoverable,
not injected into every Agent's system prompt by default.

## Eyes · 观察变化、接收事件，再投递证据

Eyes owns one user-local daemon, normally at `~/.local/share/xdog/eyes` (honoring
`XDG_DATA_HOME`). An explicit `--directory` isolates that same daemon implementation;
there is no project/cwd observer runtime or supported embedded management SDK.

```text
saved JSON → file / remote / scheduler / event sources
                         → detectors → routes → handlers → ACP receiver
```

Sources acquire content; reusable detectors detect changes/conditions; routes select
handlers. File JSON, HTTP/HTML, RSS/Atom, GitHub resources, schedules and local
pushed events share the same bounded checkpoint/delivery path. Compare fields on
each detector independently, not on the source. Local producers publish stable
IDs through `xdog-eyes publish`; acceptance is not Agent execution or user notice.
See [observation contracts and event intake](docs/eyes-observation-contracts.md).

Copy and edit a [shipped definition](packages/eyes/src/xdog/eyes/examples/source-test-review.json)
to name a real receiver and existing destination, then:

```bash
xdog-eyes validate /absolute/path/to/eyes.json
xdog-eyes --owner repo-review start /absolute/path/to/eyes.json
xdog-eyes --owner repo-review list
xdog-eyes --owner repo-review status REGISTRATION_ID
xdog-eyes --owner repo-review pause REGISTRATION_ID
xdog-eyes --owner repo-review resume REGISTRATION_ID
xdog-eyes --owner repo-review cancel REGISTRATION_ID
```

The caller chooses a stable **owner** management namespace. It is not authentication
or a destination restriction: sources and destinations belong to the JSON, not
the current Coding session or Claw group. Registration starts/reuses the daemon;
closing a host tool binding does not stop accepted observations.

ACP `enqueue` success means durable intake, **not** model execution, Goal
completion or user-channel delivery. `prompt` has its own live-turn protocol
boundary. Pause/cancel does not undo accepted downstream work, and interrupted or
unknown effects are never automatically replayed. See the [Eyes guide](packages/eyes/README.md).

## Skills, Goal and tool ownership · 能力与边界

- `xdog-agent` discovers trusted installed `xdog.capabilities` providers.
  A capability can contribute skills, tools, runtime resources or extension hooks.
- Coding and Claw load application capabilities such as Flow/Eyes by default
  when installed. They do not install missing packages automatically. Skill
  discovery does not activate its body or grant tools; an Eyes binding ensures
  daemon availability without creating an observation.
- `xdog-skills` is included by Coding and Claw. Its `goal` and `plan` skills stay
  discoverable without default body activation. `/goal` commands and model tools
  are registered independently.
- The main Agent creates/plans a Goal, optionally delegates tasks, records progress
  and judges completion. Goal stores records/task files and sends signal-driven
  ACP reminders; it is not another planner, reviewer, scheduler or Agent loop.
  Completed Goals remain current until explicit clear or a new Goal. Pause stops proactive
  reminders and the owned observation; task waits may continue on relevant changes.
- Each host owns its `ToolRepo`, a separate `McpRegistry`, approvals and child
  runtime. Extension workers provide hooks, commands, UI and providers—not model
  tools. Child tools/skills and filesystem ceilings cannot exceed the actual parent.
- SDK `ToolRepo()` creates actual bash/filesystem/current_time tools; `builtin=False`
  starts empty. Register actual tools, select identity-preserving subsets, and
  explicitly unregister before replacement. `SkillRepo()` discovers packaged
  skills; `.register(directory=path, name="host")` adds a source without activating
  bodies. Conflicting names fail rather than silently overriding another source.
- SDK hosts use `CapabilitySet("application", tool_repo=..., skill_repo=...)`
  `.select(("flow",)).open()` to open only selected providers. Assembly
  `create_agent(tool_repo=..., skill_repo=...)` adds repositories by union; an
  empty supplemental selection cannot remove capability contributions. The
  read-only `skill` tool exposes instructions on demand; native Agent still
  accepts `tools`/`skills`. Flow CLI execution does not inherit host sandbox or
  approval hooks. See the [offline SDK example](docs/sdk-capability-assembly.md).
- SDK snapshots keep structured history and serializable state, not clients,
  credentials, callables, connections or host authority. Restore needs explicit
  live bindings, starts no model call and does not replay uncertain tool effects.

See [Agent](packages/agent/README.md), [Skills/Goal](packages/skills/README.md) and
[tool ownership](docs/host-owned-tool-registry.md) for the SDK composition contracts.

## Site · 文档与本地演示

```bash
uv sync
export XDOG_SITE_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
uv run xdog-site
```

The development server listens on `127.0.0.1:8080` by default. Reading the catalog
and docs does not load a model or start Eyes. HaveFun workflow execution needs the
workspace/Flow/Agent/AI integration and real provider configuration unless using
stubbed tests. It runs trusted code with the host's authority, **not a sandbox**;
do not expose it to untrusted workflow submissions. See the
[site README](packages/site/README.md) and [deployment guide](packages/site/deploy/README.md).

## Development · 开发与验证

Run from the repository root:

```bash
uv sync --locked
uv run python scripts/test.py
uv run ruff check packages
uv run mypy --strict \
  -p xdog.ai -p xdog.agent -p xdog.tui -p xdog.flow \
  -p xdog.site -p xdog.claw -p xdog.coding -p xdog.eyes -p xdog.skills
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
```

For targeted tests: `uv run pytest packages/site/tests -q` (substitute a package).
The [architecture guide](AGENTS.md) documents current boundaries. Historical design
and verification records under `docs/` are evidence of their dated scope, not a
promise that retired interfaces are still supported.

## License · 许可

Copyright (c) 2026 HugeMan. Source is **AGPL-3.0-or-later**; see [LICENSE](LICENSE).
The [Generated Output Exception](LICENSE-EXCEPTION.md) permits generated workflow
modules, portable bundles, scheduling units and definitions to be distributed
under other terms. It does not relicense the XDOG libraries themselves.
