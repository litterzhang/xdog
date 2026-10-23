# Flow 竞品分析与产品定位

> 历史分析（2026-08-04 快照）：保留当时的竞品判断、能力评价与路线图，
> 不代表当前 CLI、依赖边界或已交付能力。当前使用方式见 [Flow README](../README.md)
> 与同目录使用指南；下文的旧结论不在本次文档更新中重写。
>
> 更新日期：2026-08-04

## 摘要

Flow 是一个**面向开发者和 Coding Agent 的、类型化、可编译、local-first 工作流
格式与工具链**。它使用 JSON 描述节点、端口和边，同一份 artifact 可以由人通过
TUI/Web UI 编辑，也可以由 Agent 自动生成；验证后既能解释执行，也能编译成独立
Python 模块，两条执行路径运行同一个 frontier transition kernel。

Flow 最接近的轻量竞品是 **Apache Burr**，而不是 Temporal 或 Airflow：

- **Burr**：Python-native 的 AI 应用状态机，强项是 tracking UI、持久化、恢复与
  streaming。
- **Flow**：可移植的 typed workflow artifact，强项是显式端口契约、独立 Python
  codegen 和 coding-agent CLI backend。

Flow 最准确的定位是：

> **Flow 是面向开发者和 Coding Agent 的本地优先、类型化工作流格式与编译器。
> 人可以可视化编排，Agent 可以生成 JSON，经过验证后即可独立运行或定期调度。**

---

## 1. 初心与产品前提

Flow 的核心目标不是提供另一套 Python Agent API，而是解决两个互补的问题：

### 1.1 开发者可视化编排固定流程

开发者通过 TUI 和未来的 Web UI 组合 Agent、script、human、subflow 与 typed edge，
把稳定流程保存成文件，并按需或定期运行：

```text
TUI / Web UI -> workflow.json -> validate -> run / scheduling install
```

Web UI 应当是 workflow JSON IDE、graph editor 和 run inspector，而不是另一个拥有
用户系统、模型市场、向量数据库和远程 worker fleet 的 Dify 类平台。

### 1.2 Agent 将成功流程固化为 workflow

AI Agent 时代，一次成功操作可以被固化为可重复 SOP：

```text
自然语言/历史步骤 -> Agent 生成 JSON -> validate -> 自动修复 -> 人 review -> 安装运行
```

Agent 能写 Python，但受约束 JSON 的输出空间更小，可以使用 schema、精确错误路径和
重复 validate 构成可靠修复闭环。Workflow JSON 因而不是配置附件，而是 Human/Agent
共同编辑的 canonical intermediate representation。

### 1.3 对架构的含义

- TUI、Web UI 和 Coding Agent 是同一个 artifact 的不同编辑器；
- loader/validator 是面向人和 Agent 的编译器前端；
- frontier executor 是解释器；
- codegen 是独立 Python 后端；
- scheduling 是可选部署适配器；
- Git 是协作、review、版本和回滚层。

### 1.4 存在必要性

如果 Flow 只是“用 Python 编排几个 LLM 节点”，它的必要性很弱，Burr、LangGraph、
Pydantic AI 或普通 async function 都能完成。

如果 Flow 坚持成为**人和 Agent 共同创建、验证、审查、部署并重复执行的 typed workflow
artifact**，它具有明确价值。市场上单项能力很多，但“Git-native JSON IR + 精确验证 +
Coding CLI backend + interpret/compile 一致 + local-first scheduling”的组合具有辨识度。

---

## 2. 对标产品

### 2.1 Apache Burr

Burr 是当前最直接的对标产品。它将应用建模为 Python action、共享 state 和
transition 组成的状态机，并提供 persistence、tracking UI 和 streaming action。

Burr 更适合直接嵌入 Python 服务；Flow 更适合把工作流作为可审查、可生成、可
编译和可独立部署的 artifact。

### 2.2 LangGraph

LangGraph 是低层、stateful、long-running Agent runtime，结合 LangSmith 后拥有
成熟的 durable execution、human-in-the-loop、memory、tracing、evaluation 和托管
部署能力。

Flow 无法与其平台和生态规模竞争，但本地部署更简单，工作流定义更独立，也不绑定
LangChain/LangSmith 生态。

### 2.3 Pydantic Graph

Pydantic Graph 是轻量 Python async workflow/FSM 库，借助 Python generics 和节点
返回类型表达 state、dependencies、transition 与终止类型，IDE/mypy 体验很强。

Flow 的 JSON Schema 和端口检查更适合配置化 artifact；Pydantic Graph 的静态
Python 类型体验更强。

### 2.4 Prefect、Dagster、Temporal

这些产品更偏生产运维平台：

- **Prefect**：Python-native orchestration、deployment、event automation、UI/Cloud。
- **Dagster**：数据资产、lineage、partition、backfill、asset observability。
- **Temporal**：跨故障和长时间跨度的 durable distributed execution。

Flow 的目标不是成为这些平台的缩小版。它选择单机、低基础设施成本和可编译 artifact，
因此缺少它们的 worker fleet、控制平面、Kubernetes、backfill、SLA 和多租户能力。

---

## 3. 建模方式

### Flow

Flow 使用 node-private ports 和显式 edge mapping：

```json
{
  "from": "plan",
  "to": "write",
  "map": {"outline": "outline"}
}
```

优势：

- 数据依赖明确；
- 不依赖共享全局 state 中的隐式字段；
- loader 可以检查端口、schema、required input 和 JSONPath 子字段；
- 图本身可以被 Git、AI、TUI、CLI 和网站共同处理。

代价：复杂动态逻辑写成 JSON 比原生 Python 繁琐。

### Burr

Burr 更接近：

```text
Python action + shared application state + transition
```

它更适合在一个 Python 应用内部表达复杂控制逻辑，但 state 字段的读写没有 Flow 的
端口 mapping 那么显式。

### 结论

- 可审查、可生成、可跨工具处理：Flow 更有优势。
- 单一 Python 应用内的复杂动态逻辑：Burr 更自然。

---

## 4. 类型安全

Flow 在加载阶段检查：

- source/destination port 是否存在；
- JSON Schema 是否匹配；
- required port 是否被 feeding edge 提供；
- JSONPath 子字段类型；
- fan-out item 类型；
- subflow 输入输出签名；
- inline script 参数是否与端口一致。

生成模块还可以通过 Ruff 和 mypy strict。

Flow 的类型安全主要是**运行前 JSON Schema 检查**，不是完整的 Python 静态类型传播。
Pydantic Graph 通过泛型和节点返回注解，让 IDE/mypy 直接理解 transition 和终止类型，
在 Python-native 静态类型体验上更强。

---

## 5. Interpret == Compile

这是 Flow 最大的差异化能力。

```bash
xdog-flow run workflow.json
xdog-flow generate workflow.json -o workflow.py
python workflow.py
```

两种执行方式共享：

- frontier readiness；
- conditional edge；
- ordinary AND join；
- multi-edge loop AND join；
- heterogeneous loop max/strict；
- fan-out/fan-in；
- failure isolation；
- coherent frontier-batch checkpoint。

生成模块不是简单地在运行时重新读取 JSON：节点函数、静态图 metadata、frontier kernel
和 checkpoint interceptor 会被编译或内联到 Python 源码中。

Burr、LangGraph 和 Pydantic Graph 都是 Python runtime，本身不需要相同形式的 codegen，
但也没有 Flow 的：

```text
JSON artifact -> interpreter -> standalone Python artifact
```

交付链路。

---

## 6. Checkpoint 与恢复

### Flow 当前优势

- checkpoint 在完整 frontier batch 后提交；
- 并行 sibling 共用一个一致性边界；
- loop counter、generation invalidation 和 completed view 一致保存；
- checkpoint schema 小且固定；
- interpreter 和 generated module 都支持自恢复；
- `CheckpointStore` 是可替换的 Protocol。

### Flow 当前限制

- 内置持久化主要是 JSON file；
- 语义是 batch-level at-least-once，不是 exactly-once；
- isolation/failure decision 不持久化；
- fan-out 中途失败会重跑整个 fan group；
- 没有 run history、time travel、fork 或可视化恢复界面；
- 不承诺 interpreter/generated checkpoint 相互交换。

### Burr 优势

Burr 提供 application ID、partition key、SQLite 和自定义 sync/async persister，支持
从 action sequence 恢复和 fork 历史运行；tracking UI 还能查看并继续历史 execution。

### 判断

Flow 的 checkpoint 内核已经具备一致的语义，但产品层明显弱于 Burr。值得增加的是
SQLite/history/inspector，而不是继续扩展 checkpoint 内部字段。

---

## 7. Observability

这是 Flow 当前最明显的短板。

### Flow 已有

- `NodeStarted` / `NodeFinished` / `NodeFailed`；
- verbose 日志；
- 每个节点 input/output 缩略（事件字段 + 两个引擎逐字一致的日志行）；
- runtime stack；
- token total；
- 结构化 success/failure result envelope；
- ASCII/Mermaid/SVG；
- TUI builder。

### Flow 缺少

- CLI backend/binary/model 日志；
- edge transition 日志；
- checkpoint commit/load 日志；
- timeline；
- per-node token/cost breakdown；
- run history UI；
- state diff；
- 完整 input/output inspector（现有的是单行缩略，不是可展开的完整值）；
- tracing exporter。

用户目前看到：

```text
NodeStarted node=summarize step=0 | article=Decentralized physical infrastructure…(+2841)
NodeFinished node=summarize step=0 duration_s=4.212 | summary=Three themes recur across…(+188)
```

节点之间流动了什么现在能看见了。仍无法从日志判断该节点是否调用了 `claude-cli`、实际 binary
和 model 是什么。

### Burr/LangGraph

Burr tracking 会记录 graph、step、inputs、state、result 和 timestamps，并提供本地 UI。
LangGraph 配合 LangSmith 后在 tracing、evaluation、state inspection 和部署监控上更成熟。

### 判断

Flow 下一阶段最应该投入 observability，而不是继续增加执行语义。

---

## 8. Streaming

Burr 支持同步和异步 streaming action，通过 `stream_result()` / `astream_result()` 逐步
消费 token 或 metric，并在结束时取得最终 result/state。

Flow 当前只向工作流调用者提供最终结果：

- SDK agent 内部可能消费流式事件，但不向 workflow caller 暴露 delta；
- CLI agent 只解析最终 envelope；
- generated module 在结束时输出 result envelope。

因此 Flow 暂不适合强实时对话 UI、token streaming 和长步骤 progress display。

建议未来复用已有事件系统，增加：

```text
AgentOutputDelta
CliOutputDelta
EdgeTraversed
CheckpointCommitted
```

而不是修改节点的最终返回协议。

---

## 9. Coding-agent CLI backend

这是 Flow 的独特优势。

```json
{
  "type": "agent",
  "backend": "claude-cli",
  "model": "sonnet",
  "allowed_tools": ["Read"],
  "mcp_servers": {}
}
```

能力包括：

- 不需要单独配置 provider/API key；
- 复用 Claude Code 或 Codex 已有认证；
- 每节点独立 backend；
- CLI tool allow-list；
- MCP server pass-through；
- `${ENV_VAR}` secret；
- CLI/SDK/script/subflow 可以混用；
- 支持解释执行和独立 Python codegen。

Burr、LangGraph 和 Pydantic Graph 可以由用户自行封装 subprocess action，但没有把
coding-agent CLI 做成这种一等 workflow backend。

---

## 10. Human-in-the-loop

Flow 已有：

- human node；
- signal；
- checkpoint；
- `FLOW_SIGNALS`；
- HTTP/file/socket hook listener；
- generated module pause/resume。

LangGraph 的 human-in-the-loop 产品更成熟，可以在 UI 中 interrupt、inspect、修改 state
并 resume。Flow 的优势是机制简单、单机可部署、可编译；缺点是没有交互式 run UI。

---

## 11. 调度与部署

Flow 当前提供：

```bash
xdog-flow scheduling install workflow.json
xdog-flow scheduling uninstall workflow-name
xdog-flow scheduling list
```

并支持：

- systemd user timer；
- cron/every；
- HTTP/file/socket hook；
- shared listener；
- portable bundle；
- 无 systemd 环境的 crontab fallback。

它很适合本地或单机部署，但没有 Prefect/Dagster/Temporal 的控制平面、远程 worker、
Kubernetes、队列、SLA、alert 和 backfill。

---

## 12. 能力矩阵

以下评分表示相对定位，不是绝对质量：

| 能力 | Flow | Burr | LangGraph | Pydantic Graph |
|---|---:|---:|---:|---:|
| 轻量、本地运行 | 5 | 4 | 3 | 5 |
| 声明式可移植 artifact | 5 | 2 | 2 | 2 |
| Python 原生灵活性 | 3 | 5 | 5 | 5 |
| JSON/端口契约 | 5 | 3 | 3 | 2 |
| IDE 静态类型 | 3 | 3 | 3 | 5 |
| 独立 Python codegen | 5 | 1 | 1 | 1 |
| Coding CLI backend | 5 | 1 | 1 | 1 |
| Checkpoint 产品能力 | 3 | 4 | 5 | 1 |
| Streaming | 1 | 5 | 4 | 2 |
| 本地调试 UI | 2 | 5 | 2-5¹ | 2 |
| 托管部署/运维 | 1 | 2 | 5¹ | 1 |
| 生态与集成 | 2 | 4 | 5 | 3 |

¹ LangGraph 结合 LangSmith 时。

---

## 13. Flow 的核心优势

### 13.1 产品辨识度

Flow 不是另一个 Python Agent wrapper，而是：

```text
typed workflow JSON
-> interpreter
-> standalone Python compiler
-> shared frontier semantics
```

### 13.2 可审查性

端口、边、条件、schema、输入输出均显式，适合 Git review、AI 生成、TUI 编辑和静态
验证。

### 13.3 低部署成本

```text
workflow.json -> generate -> python workflow.py
```

无需独立 scheduler server 或 workflow control plane。

### 13.4 Coding Agent 工作流

Claude Code、Codex、MCP 和本地开发自动化是 Flow 最有区分度的应用方向。

### 13.5 执行内核一致

frontier 重构后，interpreter/codegen 不再维护两套控制流；join、loop、fan、checkpoint
和 concurrency 均有明确且经过 parity tests 的语义。

---

## 14. Flow 的核心劣势

1. **Observability 薄弱。** 缺少 run history、timeline、state diff 和本地 inspector。
2. **没有 Streaming。** 不适合需要实时 token/progress 的交互产品。
3. **Persistence 产品层不足。** 缺少 SQLite adapter、list/show/fork 和历史 UI。
4. **复杂逻辑的 Python DX 不如 Burr/Pydantic Graph。** JSON 会比原生 Python 冗长。
5. **生态规模小。** Storage、tracking、deployment 和 integration adapters 很少。
6. **能力边界明确。** fan-out 没有实例级 resume，subflow checkpoint 粗粒度，
   generated subflow 仍依赖 Flow，调度以 Linux/systemd 为主。

---

## 15. 基于初心的推荐路线图

衡量优先级的核心指标不是“比 LangGraph 多多少功能”，而是：

> 一个开发者或 Agent，能否在五分钟内把成功流程固化成可验证、可审查、可定期执行的 workflow？

### P0：Web UI — Workflow JSON IDE

这是两个初心中尚未实现的最大部分。第一版应当保持 local-first 和单用户，不建设平台：

```bash
xdog-flow web
```

最小能力：

- 打开和保存本地 `workflow.json`；
- graph canvas 与节点/edge 属性表单；
- typed port、mapping、condition、loop 与 subflow 编辑；
- JSON preview 和 Git-friendly 文件输出；
- validate、run、generate 与 scheduling 操作；
- 查看结构化运行结果。

UI 直接读写 canonical JSON，不维护第二份数据库 workflow model。

### P1：Agent authoring 闭环

让 Skill 之外的工具接口也对 Agent 友好：

```bash
xdog-flow schema
xdog-flow validate workflow.json --json
xdog-flow graph workflow.json --json
```

Validator 返回稳定的机器可修复错误：

```json
{
  "path": "$.nodes[2].inputs[0]",
  "code": "UNFED_PORT",
  "message": "..."
}
```

标准 Agent 流程应是：create → validate → repair → preview → human review → install。

### P2：模板与示例库

人和 Agent 都更擅长模仿高质量模板。优先覆盖：

- classify → route；
- draft → critique → revise；
- collect → summarize；
- coding task → implement → test → review；
- scheduled digest；
- event hook → triage；
- human approval；
- fan-out research。

模板质量和覆盖面比增加更多节点类型更重要。

### P3：Run history 与 inspector

固定、定期执行的流程必须可诊断。先用 SQLite 和简单页面/静态 HTML：

```bash
xdog-flow runs list
xdog-flow runs show <run-id>
```

展示 timeline、node inputs/outputs、backend/model、tokens、edge transition、checkpoint
和失败原因。Web UI 后续直接消费同一 history store。

### P4：CLI/Agent 可观测性

```text
CliStarted node=summarize backend=claude-cli binary=claude model=sonnet
CliFinished node=summarize exitCode=0 tokens=123
CheckpointCommitted runId=... reason=frontier-batch
EdgeTraversed source=a destination=b
```

这是 run inspector 的事件基础，也能立即提升 CLI 使用时的信任感。

### P5：Streaming events

Streaming 从原先的高优先级降为中等。固定、定期 workflow 更需要 progress、logs 和最终
结果，而不是 token-by-token UI。未来从 SDK/CLI 产生 delta event，保持 final result 和
checkpoint 协议不变。

### P6：Doctor 与 SQLite persistence

```bash
xdog-flow doctor workflow.json
```

检查 CLI binary/version、PATH/auth、structured-output flags、MCP environment 和 systemd。
SQLite checkpoint/history store 复用现有 `CheckpointStore` seam，不重写 executor。

---

## 16. 暂不建议投入

- 分布式执行；
- 多租户/auth；
- compensation/rollback；
- 跨机器 checkpoint；
- 通用 telemetry 平台；
- 复杂 secret manager；
- fan-out instance-level resume，除非真实任务的重复成本已经明显。

---

## 17. 最终判断

Flow 有存在必要，但前提是坚持产品初心。

如果它发展成“又一个全功能 Agent runtime”，会直接进入 LangGraph、Burr、Prefect 等
成熟产品的优势区，差异化和投入产出都会下降。

如果它坚持服务于：

```text
人通过 TUI/Web UI 编排固定流程
+ Agent 通过受约束 JSON 固化成功流程
+ Git review 与精确 validate
+ local run / standalone compile / scheduling
```

那么它拥有清晰且特定的价值。JSON 不是护城河，图执行也不是；真正的价值是以下组合：

1. Human 和 Agent 共用一份 canonical workflow IR；
2. Validator 为 Agent 提供可靠的生成—修复闭环；
3. Workflow 是 Git-native、可 review 和可回滚的 artifact；
4. `interpret == compile` 将编辑格式与部署格式连接起来；
5. Claude Code、Codex 和 MCP 是一等节点；
6. 整套系统 local-first，不要求托管控制平面。

推荐产品描述：

> **Flow is a local-first, typed workflow format and compiler for humans and
> coding agents. Design workflows visually or generate them with AI, validate
> them, then run or schedule them as standalone Python.**

中文：

> **Flow 是面向开发者和 Coding Agent 的本地优先、类型化工作流格式与编译器。
> 人可以可视化编排，Agent 可以生成 JSON，经过验证后即可独立运行或定期调度。**

---

## 参考资料

- [Apache Burr documentation](https://burr.dagworks.io/)
- [Burr state persistence](https://burr.dagworks.io/concepts/state-persistence/)
- [Burr tracking and UI](https://burr.dagworks.io/concepts/tracking/)
- [Burr streaming actions](https://burr.dagworks.io/concepts/streaming-actions/)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [Pydantic Graph](https://pydantic.dev/docs/ai/graph/graph/)
- [Temporal documentation](https://docs.temporal.io/)
- [Prefect documentation](https://docs.prefect.io/v3/get-started)
- [Dagster documentation](https://docs.dagster.io/)
