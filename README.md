# xdog

[![CI](https://github.com/litterzhang/xdog/actions/workflows/ci.yml/badge.svg)](https://github.com/litterzhang/xdog/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/xdog-flow?label=xdog-flow&amp;cacheSeconds=3600)](https://pypi.org/project/xdog-flow/)
[![Release](https://img.shields.io/github/v/release/litterzhang/xdog?label=release)](https://github.com/litterzhang/xdog/releases)
[![Python](https://img.shields.io/pypi/pyversions/xdog-flow?cacheSeconds=3600)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-xdog.942295.xyz-informational.svg)](https://xdog.942295.xyz)

**A local-first toolkit for building, running and scheduling LLM workflows — no
control plane, no database, no hosted service.**

**本地优先的 LLM 工作流工具链 —— 无控制面、无数据库、无托管服务。**

📖 **[xdog.942295.xyz](https://xdog.942295.xyz)** — documentation, design notes and blog

---

## What this is · 这是什么

xdog is a monorepo of small, composable packages that share one idea: **what you
build should be a file you own, running on a machine you control.**

xdog 是一组小而可组合的包，共享同一个想法：**你构建出来的东西，应该是一份你拥有的文件，
跑在你控制的机器上。**

The centrepiece is **flow** — a typed workflow format and compiler. A workflow is
one JSON file with typed ports and explicit edge mappings. It is validated as a
whole *before* anything runs, executes locally, and compiles to a standalone
Python module that needs nothing from flow at run time.

核心是 **flow** —— 一个带类型的工作流格式与编译器。一个工作流就是一份 JSON 文件，
带类型化端口和显式的边映射。它在**任何东西跑起来之前**整图校验，本地执行，并能编译成
运行时不依赖 flow 的独立 Python 模块。

| Package | Import | CLI | What it does · 做什么 |
|---|---|---|---|
| [`xdog-flow`](https://pypi.org/project/xdog-flow/) | `xdog.flow` | `xdog-flow` | Typed workflow format, validator, interpreter, Python compiler, systemd scheduler |
| [`xdog-ai`](https://pypi.org/project/xdog-ai/) | `xdog.ai` | `xdog-ai` | Unified LLM provider API — chat, embeddings, web search, Anthropic-compatible proxy |
| [`xdog-agent`](https://pypi.org/project/xdog-agent/) | `xdog.agent` | `xdog-agent` | Agent runtime: tool calling, structured output, steering |
| [`xdog-coding`](https://pypi.org/project/xdog-coding/) | `xdog.coding` | `xdog-coding` | Interactive coding-agent CLI with session management |
| [`xdog-claw`](https://pypi.org/project/xdog-claw/) | `xdog.claw` | `xdog-claw` | Agent orchestration runtime |
| `xdog-eyes` | `xdog.eyes` | `xdog-eyes` | Declarative observation, evidence-to-event routing, session-bound activation |
| `xdog-site` | `xdog.site` | `xdog-site` | Local documentation and trusted workflow demo site |
| [`xdog-tui`](https://pypi.org/project/xdog-tui/) | `xdog.tui` | — | Terminal UI library with differential rendering |

Every package shares the `xdog` namespace, so they install side by side without
colliding with anything else on PyPI.

所有包共用 `xdog` 命名空间，彼此并存，也不会和 PyPI 上的同名项目冲突。

---

## Eyes · 主动观察

`xdog-eyes` follows flow's file-first shape: versioned JSON, frozen models,
validation, asynchronous execution, CLI and the packaged **eyes-observers** skill.
Sources collect evidence, detectors emit events and routes invoke generic ACP
handlers. Coding never needs to know a concrete source or start an event host.

```bash
xdog-eyes validate eyes.json
xdog-eyes start eyes.json          # explicitly approved observation; endpoint/session configured in v2
xdog-eyes status REGISTRATION_ID
xdog-eyes stop REGISTRATION_ID
xdog-eyes resume REGISTRATION_ID  # data reconnect or target-approved prompt credit; not self-approval
```

The callback connects directly to an explicit Unix ACP endpoint and session ID,
not a coding CLI subprocess or TUI widget. An independently managed lightweight
receiver supports `delivery: "enqueue"` while TUI/session owners are offline,
without constructing an Agent or model. Queued receipt is not execution: opening
TUI displays pending evidence, and `/inbox approve ID [ID...]` is a separate exact,
permission-gated action. `delivery: "prompt"` instead requires a live owner and
waits for its final ACP result. Unknown prompt outcomes are never blindly replayed.

本地 Eyes 负责采集、事件和路由；coding 通过通用 ACP/session controller 接收并展示。
收件持久化但不自动执行，观察注册不崩溃重放或自动启动。默认权限、来源标签和单一执行
所有权保持不变。首版仅文件轮询、本地 Unix/可选通用 stdio ACP，不增加远程服务或
claw/flow 专用 adapter。详见 [包说明](packages/eyes/README.md) 与
[ACP/session/inbox 协议](docs/acp-session-routing.md)。

---

## Quick start · 快速上手

### 1. Install · 安装

Requires **Python 3.12+**. 需要 **Python 3.12+**。

```bash
pip install xdog-flow
```

That pulls in `xdog-ai`, `xdog-agent` and `xdog-tui`, and puts `xdog-flow` and
`xdog-ai` on your PATH. Nothing else is needed — no server, no database, no
account.

这会一并装上 `xdog-ai`、`xdog-agent`、`xdog-tui`，并把 `xdog-flow` 和 `xdog-ai`
放进 PATH。没有别的前置条件 —— 不需要服务、不需要数据库、不需要账号。

Prefer an isolated tool install, or want the coding agent as well:
想要隔离安装，或者也要那个编码 agent：

```bash
uv tool install xdog-flow       # isolated, still on PATH · 隔离安装,命令照样可用
pip install xdog-coding         # the interactive coding agent · 交互式编码 agent
```

### 2. Log in to a provider · 登录 provider

```bash
xdog-ai providers        # what's available · 有哪些 provider
xdog-ai login copilot    # device-code flow · 设备码登录
```

Credentials stay on your machine; nothing is sent anywhere except the provider itself.
凭据只存在本机；除了 provider 本身，不会发往任何地方。

### 3. Chat · 对话

```bash
# One-shot · 单次提问
xdog-ai chat copilot gpt-5.6-sol "Explain the CAP theorem in three sentences."

# Interactive — omit the message · 省略消息进入交互模式
xdog-ai chat copilot gpt-5.6-sol

# Handy flags · 常用参数
xdog-ai chat copilot gpt-5.6-sol "Summarise today's news" --web-search
xdog-ai chat copilot gpt-5.6-sol "What is in this image?" -i photo.png
xdog-ai models copilot --sync     # refresh the model list · 刷新模型列表
```

### 4. Your first workflow · 第一个工作流

Save this as `calculator.json`. It is the smallest workflow that uses both node
kinds: a deterministic **script** node builds an arithmetic problem, and an
**agent** node solves it *by running a `bash` command* rather than doing the
arithmetic in its head.

把下面这段存成 `calculator.json`。它是同时用到两种节点的最小示例：确定性的
**script** 节点构造算式，**agent** 节点**通过执行 `bash` 命令**求解，而不是靠脑内心算。

```jsonc
{
  "name": "agent-calculator",
  "provider": "copilot",
  "defaults": { "model": "gpt-5.6-sol" },
  "entry": "make_problem",
  "state": { "a": "347", "b": "895" },        // seeds the reserved $in source
  "nodes": [
    {
      "id": "make_problem",
      "type": "script",
      "inputs": [ {"name": "a", "schema": {"type": "integer"}},
                  {"name": "b", "schema": {"type": "integer"}} ],
      "code": "def make_problem(ctx, a, b):\n    return f'{a} + {b}'",
      "outputs": [ {"name": "problem", "schema": {"type": "string"}} ]
    },
    {
      "id": "solve",
      "type": "agent",
      "inputs": ["problem"],
      "tools": ["bash"],
      "prompt": "Compute this by running a bash command: {{$.problem}}",
      "outputs": ["answer"]
    }
  ],
  "edges": [
    {"from": "$in",          "to": "make_problem", "map": {"a": "a", "b": "b"}},
    {"from": "make_problem", "to": "solve",        "map": {"problem": "problem"}},
    {"from": "solve",        "to": "$output",      "map": {"answer": "result"}}
  ]
}
```

Note the typed ports: `a` and `b` arrive as **integers** (coerced from the strings
`"347"` / `"895"`), so `make_problem` returns `"347 + 895"` — not `"347895"`. Data
moves only along declared edges; there is no shared global state.

注意类型化端口：`a` 和 `b` 以**整数**送达（由字符串 `"347"` / `"895"` 强制转换），
所以 `make_problem` 返回 `"347 + 895"` 而非 `"347895"`。数据只沿声明的边流动，
没有共享全局状态。

**Run it · 运行**

```bash
# Validate before executing; --json gives every error at once, machine-readable
# 执行前校验；--json 一次给出全部错误，机器可读
xdog-flow validate calculator.json
xdog-flow validate calculator.json --json

# Offline dry run — no model calls, no login needed, but the whole graph runs
# 离线试跑 —— 不调模型、不需登录，但整张图真的跑一遍
xdog-flow run calculator.json --dry-run --input a=12 --input b=30

# For real · 真实执行
xdog-flow run calculator.json --input a=12 --input b=30
```

Every run prints the same envelope · 每次运行都返回同一种结果信封：

```json
{
  "success": true,
  "message": "Workflow completed",
  "output": { "result": "42" },
  "context": {
    "workflow": "agent-calculator",
    "durationMs": 3120,
    "tokensUsed": 812,
    "lastNode": "solve"
  }
}
```

**Draw it, compile it, schedule it · 画图、编译、调度**

```bash
# Topology as ASCII / Mermaid / SVG · 拓扑图
xdog-flow graph calculator.json
xdog-flow graph calculator.json --mermaid

# Compile to a standalone module and run it with plain python
# 编译成独立模块，用普通 python 直接跑
xdog-flow generate calculator.json -o workflow.py
python workflow.py

# Run the companion test suites — model turns are stubbed, everything else is real
# 跑配套测试套件 —— 只 stub 模型调用，边/条件/循环全部真跑
xdog-flow test .  --allow-script-stub

# Install a systemd timer from the workflow's own `schedule` block
# 依工作流自带的 schedule 块生成并安装 systemd timer
xdog-flow scheduling install calculator.json
```

Or edit one in the interactive terminal builder · 或用交互式终端编辑器：

```bash
xdog-flow build my_workflow.json
```

**The examples ship with the package** — no clone needed:

```bash
EXAMPLES=$(python -c "import importlib.resources as r; print(r.files('xdog.flow') / 'examples')")
xdog-flow test "$EXAMPLES" --allow-script-stub    # 11 suites, no network
xdog-flow graph "$EXAMPLES/depins_enrich/depins_enrich.json"
```

**示例随包分发**，不需要 clone。

You can also run an example in your browser at
**[xdog.942295.xyz/havefun/flow](https://xdog.942295.xyz/havefun/flow)**.

### 5. Teach your coding agent · 让编码 agent 学会

`xdog-flow` also ships a **skill** — a `SKILL.md` in the open
[Agent Skills](https://agentskills.io/specification) format that teaches an
agent to author flow workflows. Installing the package is the whole
installation step:

`xdog-flow` 同时附带一份 **skill** —— 开放
[Agent Skills](https://agentskills.io/specification) 格式的 `SKILL.md`，教 agent
写 flow 工作流。装包就是全部安装步骤：

```bash
pip install xdog-coding xdog-flow
xdog-coding
> /skills                  # what's available · 有哪些
> /flow-workflows          # activate it · 激活
> /unload flow-workflows   # and take it back out · 撤下
```

The command appears because the package is installed and disappears when it is
uninstalled — nothing is registered and nothing is copied, so there is no copy
to drift out of date with the package it documents.

命令因为包被安装而出现，因为卸载而消失 —— 不注册、不拷贝，也就没有会与它所描述的包脱节的副本。

Two things here are not how the format's reference clients behave, deliberately:

这里有两点是**刻意**不同于该格式参考实现的：

- **A skill can be unloaded.** Its instructions go into the system prompt, which
  is rebuilt every turn, rather than into the transcript, which cannot be
  edited. Elsewhere an invoked skill occupies the context window for the rest of
  the session.
  **可以卸载。** 指令进的是每轮重建的系统提示词，而不是无法修改的会话历史。别处一旦调用就占满余下整个会话。
- **The model cannot unload one.** A skill is often a constraint — "always run
  the tests before deploying" — and the party being constrained should not hold
  the release. It may suggest `/unload`; the user decides. An author can declare
  `scope: turn` for a one-shot skill that retires itself.
  **模型不能自己卸载。** skill 常常是约束（「部署前必须跑测试」），被约束者不该握着弹射按钮。
  它可以建议 `/unload`，由用户决定。作者可声明 `scope: turn` 让一次性技能自行退场。

Any package can carry skills in `<package>/skills/<name>/SKILL.md`, and a skill
written for another client works here unmodified.

任何包都能在 `<package>/skills/<name>/SKILL.md` 里携带技能；为其他客户端写的技能可原样使用。

---

## Where to next · 下一步

| | |
|---|---|
| **[xdog.942295.xyz](https://xdog.942295.xyz)** | documentation, design notes, blog · 文档、设计笔记、博客 |
| **[CHANGELOG](https://github.com/litterzhang/xdog/blob/main/CHANGELOG.md)** | what changed since 1.0.0 · 1.0.0 以来的变更 |
| **[flow's README](https://github.com/litterzhang/xdog/blob/main/packages/flow/README.md)** | the full workflow schema, execution model and CLI · 完整 schema、执行模型与 CLI |
| **[examples/](https://github.com/litterzhang/xdog/tree/main/packages/flow/examples)** | runnable examples · 可运行示例 |
| **[depins_enrich/](https://github.com/litterzhang/xdog/tree/main/packages/flow/examples/depins_enrich)** | a case study — a workflow that runs unattended every four hours and writes real commits · 案例研究：每四小时无人值守运行并产生真实提交的工作流 |

---

## Development · 开发

```bash
git clone https://github.com/litterzhang/xdog.git
cd xdog
uv sync                                  # editable install of every package · 全部包 editable 安装

uv run pytest packages/flow/tests -q     # test one package · 单包测试
uv run ruff check packages               # lint
uv run mypy --strict -p xdog.flow        # type check
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
```

---

## License · 许可

Copyright (c) 2026 HugeMan <942295.xyz>

Licensed under the **GNU Affero General Public License v3.0 or later** — see
[LICENSE](LICENSE). Fork it or offer it as a service, and share your changes.

**What flow compiles for you is yours.** `xdog-flow generate` copies parts of
flow's own runtime into its output, so the AGPL would otherwise follow them into
every compiled workflow. The [flow Generated Output Exception](LICENSE-EXCEPTION.md)
— an Additional Permission under AGPL section 7, modelled on the GCC Runtime
Library Exception — lets you convey generated modules, portable bundles,
scheduling units and workflow definitions under any terms, including proprietary
and commercial ones.

**flow 为你编译出的东西归你。** `xdog-flow generate` 会把 flow 自身运行时的一部分复制进
产物；若不豁免，AGPL 就会随之进入你编译的每一个工作流。
[生成物豁免](LICENSE-EXCEPTION.md)（AGPL 第 7 条的 Additional Permission，
参照 GCC Runtime Library Exception）允许你以**任何条款**分发生成的模块、可移植 bundle、
调度单元与工作流定义，包括专有与商业条款。
