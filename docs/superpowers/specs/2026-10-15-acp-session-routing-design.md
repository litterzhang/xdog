# ACP 会话路由与 Eyes 迁移设计

日期：2026-10-15
状态：原设计已于 2026-10-15 获批并按 Native 实施，Tasks 1–8 已提交，Tasks 9–10 及离线收件尚未完成。用户随后要求 callback 不依赖 coding CLI；受影响部分由下述已确认补充设计修订，实施补充计划等待审核。
替代范围：本文获批并实施后，替代 `2026-10-14-eyes-design.md` 中的专用宿主协议、绑定和调度方案；保留 Eyes 的 source/detector/route、文件采集与 Flow 风格产品形态。

> **2026-10-15 架构修订：** 用户已确认 Eyes 直接连接 ACP endpoint、接入服务生命周期独立于 TUI、不由 Eyes 启动 coding。见 `2026-10-15-acp-endpoint-callback-design.md`（已确认；补充计划 `../plans/2026-10-15-acp-endpoint-inbox.md` 待审核）。下文 §3/§10 的强制 coding stdio bridge 与 command-only handler 不再作为最终方案；保留原文用于说明已实施迁移的历史依据。离线收件同时受 `2026-10-15-session-inbox-design.md` 约束。

## 1. 用户意图与验收目标

用户已确认以下方向：

- `xdog.agent` 提供可复用的 Agent Client Protocol（ACP）支持。
- coding 通过薄 adapter 将 ACP 请求路由至指定 session；不能依赖 TUI 控件执行任务。
- coding 提供 `--session <id>`，可把任务送到已打开的目标会话，不仅是恢复磁盘历史。
- TUI 必须展示外部输入、排队状态、assistant 输出、工具活动与最终结果，并能从历史恢复展示。
- Eyes 是 ACP client；移除上一版 Eyes 专用 event handler host，不保留双轨运行或旧协议兼容服务器。
- 仍在原仓库目录和当前功能分支工作，不创建 worktree。

成功闭环：启动 coding TUI 获得 session ID，运行 Eyes 文件观察定义；文件变化后，Eyes 通过 ACP 向该 ID 投递，目标会话串行执行，TUI 与 ACP 调用方都看见相应消息与结果。普通 ACP 客户端不需要了解 Eyes。活跃会话不出现第二个持有相同历史的 Agent。

非目标：第一版不实现 TCP/HTTP 远程入口、独立永久 session daemon、跨进程崩溃重放、Windows 本地传输、claw/flow ACP adapter、通过会话导航自动迁移观察任务。

## 2. 现有代码依据

- `coding/core/sdk.py` 先构造 `Agent`，再把它交给 `AgentSession`；`AgentSession` 拥有 runtime、权限、持久化、扩展和压缩。
- `coding/main.py` 在模式选择之前创建 session。添加 attach CLI 时必须先解析模式，不能先创建第二个 session 再连接 owner。
- `coding/modes/interactive/interactive_mode.py` 持有 session 并订阅 agent 事件，但还拥有 worker、输入排队、导航及 producer 清理逻辑。
- 现有 RPC 模式有自己的 turn/control 调度，不等同于标准 ACP。
- `coding/core/session_manager.py` 的原子文件替换不是进程间独占锁。只改 CLI 参数或重复加载 JSON 无法保证单一 owner。
- 上一版 `coding/core/external_events/`、TUI/RPC external-event dispatcher 与 `eyes/transport/` 共同构成专用协议；需要整体替换，而不是只改模块名。
- `agent/external_event.py` 是消息历史格式和 LLM 投影，不是 socket host；已保存的 `xdog.external_event.v1` 历史必须仍可读取。

## 3. 总体结构

```text
Eyes / 外部 ACP Client
       │ 标准 ACP over stdio
       ▼
xdog-coding --acp --session ID（薄桥接，不创建 AgentSession）
       │ ACP over 私有 Unix socket
       ▼
Coding ACP Adapter ──► Session Router ──► Session Controller
                                             │
TUI 输入 / 现有模式输入 ──────────────────────┘
                                             │ 唯一执行所有权
                                             ▼
                                        AgentSession
                                             │
                                             ▼
                                            Agent

Controller 会话事件 ──► TUI 展示
                   └─► ACP session/update 与 prompt 结果
```

session 是路由目标，TUI 是输入端与事件消费者。没有 TUI 时，独立 ACP 模式仍可创建和执行会话；这不意味着关闭 TUI 后自动保活。首版进程退出仍结束其持有的会话，Eyes 不接管其所有权。

## 4. 包和模块边界

### 4.1 `xdog.agent.acp`

协议组件不导入 coding、Eyes、TUI、claw 或 flow：

- adapter：ACP 初始化、会话操作、prompt、cancel，接受注入的 backend。
- backend contract：创建/加载/定位会话、提交任务、订阅更新、取消调用方拥有的任务。
- events：将通用执行事件映射为 ACP message/tool-call 更新和终止结果。
- client/transport helpers：仅处理 SDK 连接和生命周期，不持有 harness 策略。
- generic Agent backend：让 `xdog-agent acp` 独立可用，不需要安装 coding。

协议处理使用官方 Python `agent-client-protocol` SDK，而不是自行复制 JSON-RPC。实施基线为本次核验的 0.12.1，依赖范围 `>=0.12.1,<0.13`，lockfile 固定实际解析版本。SDK 支持的 Python 上限需在打包验证中明确，不默认为未来解释器版本已受支持。非 ACP 入口不能在导入时启动任何服务。

### 4.2 coding session 层

建议新增 `coding/core/sessions/`，拥有 request/event 模型、controller、router、进程 owner 注册和锁。

建议新增 `coding/acp/`，拥有 coding backend、stdio CLI、Unix transport 与桥接。协议能力复用 `agent.acp`；产品权限、历史和 TUI 策略不进入 agent 协议包。

### 4.3 Eyes

保留 sources、detectors、routes、executor、definition、CLI、skill；新 ACP handler 管理客户端连接及请求结果。Eyes 可以依赖 ACP SDK，但不能依赖 `xdog.agent`、ai 或 harness，仍不执行工具循环。

## 5. Session Controller 是唯一调度入口

- 每个活跃 session 只有一个 controller 和一个执行 owner，所有产生 turn 的入口提交到同一队列。
- TUI、现有 RPC 和 ACP 不得分别直接调用 `AgentSession.send_message()` 形成竞争路径。
- controller 管理输入接纳、队列、运行、取消、完成和失败；输入具有 request ID、origin、连接身份与捕获的上下文 generation。
- controller 事件使用跨线程安全方式分发到 TUI；socket I/O、模型流和文件 I/O不能阻塞终端主循环。
- 必须等待 producer、工具、权限对话和后处理完全结算后释放 turn owner；不能只依赖 `agent.is_idle`。
- 导航、压缩、工具集合切换、模型/auth 变更和终端工作必须通过共同的 reservation/fence 与 turn 互斥。将现有 TUI busy 检查原样搬进 ACP 不是完成重构。
- 人类输入优先于尚未启动的自动任务。第一版外部输入忙时排队，不实现自动插话/steer。
- 队列最多 128 项；单任务输入最多 1 MiB，超限在入队前拒绝，不截断证据。请求完成 future 与展示事件分开，完成仅在会话执行和必要持久化结算后产生。
- 排队任务绑定接纳时的 session/branch generation；导航后取消旧 generation 的待执行任务，不偷偷在新 branch 执行。

## 6. 本地所有权与寻址

- private runtime directory 为当前 UID 所有、权限 0700；owner registry 文件为 0600，socket 只允许当前用户访问。拒绝符号链接、错误 owner、非预期文件类型和越界地址。
- owner 记录 session ID、项目根目录、socket 地址和进程实例 generation；PID 单独不能证明身份，连接后必须核对 live owner。
- 同一 session 的进程锁必须在加载可写历史之前获取，并保持到所有执行/清理完成；所有 coding 可写模式，包括 TUI、print、RPC、ACP 和恢复入口均遵守。锁冲突明确报错，不偷锁、不删除另一个活进程的 socket。
- router 只定位当前进程已持有的活跃 session。跨进程 attach 通过 owner registry 和 ACP 连接；不能访问另一个进程的 Python 对象。
- 第一个版本只向当前 live session/branch 投递。TUI 切到另一 session 后，旧 session 没有活跃 controller 时返回 inactive；不在隐藏状态偷偷运行旧会话。
- capability token、旧 descriptor 环境变量及 Eyes route 注册表不再存在。若需要会话 ID 工具环境提示，仅提供非秘密的 `XDOG_SESSION_ID`，它不能授予权限，必须覆盖继承的过期值。
- 这是同 UID 的本地接入保护，不是敌对同 UID 进程之间的沙箱。

## 7. CLI 与 ACP 会话语义

拟议命令：

```bash
xdog-agent acp
xdog-coding --acp
xdog-coding --acp --session SESSION_ID
xdog-coding --session SESSION_ID --prompt '检查变化文件'
```

- `xdog-agent acp`：通用 agent backend，标准 stdio ACP。
- `xdog-coding --acp`：独立 coding ACP 模式，可创建会话；断开时结算并关闭自己持有的资源。
- `--acp --session ID`：标准 stdio 到已有 owner 的 ACP 桥接。不能创建 Agent、加载可写历史、覆盖目标配置或顺便启动 TUI。
- `--session ID --prompt TEXT`：用同一 ACP client 路径提交任务、输出结果。不是新的私有投递协议。
- `--session` 必须精确匹配；unknown、inactive、project mismatch 分别返回可读错误。不自动恢复或新建。`--resume-id` 保留为显式恢复历史的入口，两者互斥。
- `--session` 与模型/工作目录/权限覆盖等会修改目标 owner 的参数组合要拒绝，不能由附加客户端改变会话策略。`--session` 缺少 prompt 且未选 ACP 时返回用法错误。
- 标准 ACP 支持 initialize、session/new、session/load、session/prompt、session/cancel 和流式 session/update；只声明真实支持的 capabilities，未支持的配置或内容类型明确报错。
- 固定 ID bridge 只允许已绑定目标。不能通过 new/load 切到任意 session；需要创建新 session 的客户端使用不带 `--session` 的入口。
- load 的 cwd 必须符合目标 workspace；非空 MCP server/额外目录配置若没有完整接入权限与生命周期则拒绝，不静默忽略。
- session/load 需要的历史重放仅发给请求 peer，不重新写历史，也不在 TUI 重复展示。
- 不承诺 generic ACP 自带去重、自动重试或“恰好一次”。这些是客户端和 controller 的显式设计。

## 8. 持久消息、TUI 展示与来源

- 接纳外部输入时生成规范化 session input 记录，保留 request ID、origin、客户端自报来源、instruction、证据及 Eyes 可选的 event/delivery provenance。来源文本是标签，不是身份或授权证明。
- 输入和状态由 controller 负责记录一次，不由 ACP adapter 与 TUI 各自插入；queued、running、terminal 状态关联同一 request ID。状态日志与模型消息分开，排队输入不可提前进入当前模型上下文。
- 已接纳待执行任务在历史中可见；导航/退出使未执行项获得 cancelled/discarded 终态。崩溃后只展示 interrupted，不自动执行记录中的 queued 项。
- 提交/入队失败时不产生看似已接受的聊天消息；最终输入 admission 失败不能留下可被继续执行的半条模型消息。
- TUI 实时显示来源、处理要求、证据摘要和任务状态，复用 assistant/tool 渲染。历史重开能恢复同样的外部来源与最终状态，而不只有“recorded input”占位文案。
- ACP peer 获得自己请求对应的输出；不能把其他客户端或人类 turn 的实时输出误归给该请求。用户明确 load 时允许重放目标会话历史。
- 历史中的 `xdog.external_event.v1` 保留解码和只读渲染兼容，不能因此保留旧 transport、host 或 route 注册服务。新通用 ACP 输入使用 source-neutral session input 格式。
- LLM 投影明确区分处理要求与不可信证据。任何 event payload、路径或自报 metadata 都不能赋予权限、冒充人类或覆盖目标 session。

## 9. 权限、取消与自动工作预算

- coding backend 永远使用原有 Session 权限 hook，不直接调用裸 Agent 绕过权限。
- 有本地人工审批端时，由现有审批机制处理。无审批端且未明确 allow-all 时，写入/执行请求 fail closed；Eyes client 对反向 permission request 默认拒绝，不能模拟人工批准。
- 通用独立 ACP backend 可以调用 ACP permission callback，但必须服从已配置的最低权限约束。客户端返回批准不等于覆盖一个显式 deny 策略。
- 绑定活跃 coding session 的 ACP 输入默认是外部输入，不会补充人工唤醒预算。metadata 标记 human、resume 或 allow-all 都不能升级权限。
- 每次本地明确人类输入或经原有权限机制批准的恢复，提供一个自动 turn 预算。成功、失败、取消均不补充。独立标准 ACP 会话的正常交互不套用 Eyes 观察预算；Eyes 自身仍限制自动重复触发。
- 预算耗尽的任务有可见 paused 状态。controller 提供通用受权恢复操作；Eyes resume 只能申请该操作，不能自行确认。若 stdio 协议需扩展，使用 SDK 的具名扩展机制并显式协商，不伪称 ACP 标准方法。
- session/cancel 只影响该连接拥有的排队/执行请求。多个连接不能通过 session ID 取消人类或其他连接的 turn；本地人类取消保留管理权。
- bridge 断连取消或结算该 peer 拥有的请求，不关闭 TUI，不留下永久 turn reservation；连接复用和客户端退出必须有测试。

## 10. Eyes 定义和生命周期迁移

新定义使用 version 2，旧 version 1 的 `session.notify` 明确报迁移错误，不偷偷绑定另一个 session。source/detector/route 的内容语义不变。

```json
{
  "version": 2,
  "name": "review-source-changes",
  "sources": [{"id": "files", "type": "file", "root": ".", "include": ["src/**"]}],
  "detectors": [{"id": "changes", "type": "file.change", "source": "files", "debounce_ms": 500, "max_batch_delay_ms": 2000}],
  "handlers": [{
    "id": "review",
    "type": "acp",
    "command": ["xdog-coding", "--acp"],
    "session": "EXPLICIT_SESSION_ID",
    "instruction": "检查变化的源文件及相关测试；遵守目标会话权限。"
  }],
  "routes": [{"id": "review-route", "detector": "changes", "handler": "review", "event_types": ["file.created", "file.modified", "file.deleted"]}]
}
```

- `command` 是 argv 数组，直接启动，不做 shell 字符串拼接。Eyes 使用标准 ACP load/prompt 指定 session；不向任意第三方命令强加 coding 专用 `--session`。coding 无固定 ID 的 ACP 模式 load 遇到活跃本地 owner 时内部 attach；被其他进程持有且不可连接时拒绝，不绕过锁加载。
- 每个 handler 配置明确目标 session，不默认 latest/current，也不要求持有 `XDOG_EVENT_CONTEXT`。所有 handler 连接、目标校验成功后才开始采集，部分失败关闭已建立连接。
- Eyes 管理自己的私有 runtime/control 文件；这些是观察 CLI 的控制面，不是 harness 的事件接收 host。start/status/stop/resume 保留，作用域和目标 session 明确展示。
- 首版仍只支持本地、live target，不加入持久 daemon。目标 owner generation 或 branch 变化时停止投递，不自动连接同 ID 的新 owner。
- 文件采集、baseline、防抖、净变化、容量上限和错误时保留可信 baseline 保留。Eyes 不把完整历史内容伪称为证据。
- 单个 handler 串行 prompt；收到 prompt 完成才报告处理完成，stream/update 不等于完成。
- 不同于旧 deliver ACK，标准 session/prompt 是请求到最终结果的生命周期。对于已发出但响应丢失的请求标为 outcome-unknown 并暂停；不盲目重发造成第二次 agent 执行。尚未发送的本地失败可安全重试。
- 保留稳定 event/delivery/request 标识作追踪，不声称标准 ACP 提供跨连接幂等。容量溢出暂停，不能静默丢证据。
- 更新 skill：编写 version 2、明确目标 ID、校验、审批启动、检查 TUI/ACP 结果、停止与授权恢复；加载 skill 不启动观察。

## 11. 必须删除与必须保留

实施完成后删除：

- `coding/core/external_events/` 整个专用 host/binding/inbox/protocol 实现。
- `coding/modes/interactive/external_events.py`、`coding/modes/rpc/external_events.py` 及其模式接线。
- `AgentSession.enable_external_events`、旧 capability 生命周期、`XDOG_EVENT_CONTEXT` 注入和继承处理。
- Eyes 的 `handlers/session_notify.py`、旧 host client/protocol，以及依赖 capability descriptor 的 runtime/process/control 接线。
- 旧 `docs/eyes-host-protocol.md` 和旧 host fixture；以 ACP/session 文档和标准协议测试替换。
- 旧 host/inbox 专属测试，用新 controller/router/ACP 黑盒行为测试替代，不保留只为旧服务器服务的实现。

保留并迁移：

- 已提交的旧设计/计划作为历史文档，显著标记 superseded，不让旧协议看起来仍有效。
- 原有规范消息历史读取、source-neutral provenance、安全的工具子进程环境处理、文件观察测试。
- 现有 RPC 模式功能，不把旧私有 JSON-lines RPC 改名伪装成 ACP。
- Eyes 的 packaged skill、示例、构建产物验证与 Flow 风格校验器；根 AGENTS.md、包 README 和验证文档同步更新。

## 12. 测试与迁移验收

产品代码以 TDD 分任务实施，每个新行为先有观察到失败的测试。最低验收矩阵：

1. 通用 Agent backend 用官方 ACP SDK client 完成 initialize/new/prompt/update/cancel；无 coding 安装也可工作；stdout 只有协议帧。
2. 目标 TUI 正在执行时，ACP 请求排队；人类输入优先；前一 producer/control 完全结算才执行下一项。
3. 两个进程同时恢复或 attach 相同 ID，只有 owner 可以写历史；桥接不会创建会话，也不会关闭 owner。
4. unknown/inactive ID、错误 cwd、跨 session load、旧 generation、符号链接和错误权限目录均 fail closed。
5. 目标会话实时和重载后均显示外部输入、证据、状态、assistant 和工具；无重复、无伪造人类来源。
6. 取消只影响请求 owner；socket 断连、client crash、TUI 关闭、导航、compaction、权限等待都能释放资源且不误取消其他工作。
7. 默认权限拒绝无人审批的修改；有 TUI 时使用现有审批；Eyes 不能自行批准 resume 或改变 permission mode。
8. 自编辑产生第二批文件变化不会无限启动 turn；暂停/恢复及满队列清晰可见。
9. outcome-unknown 不自动重发；尚未发出的失败可重试；ACK/update 不被误报为模型完成。
10. subprocess 集成测试：真实 owner + stdio bridge + SDK client，确定性模型桩；完整文件变化到指定会话闭环。
11. 删除项审计证明不再引用旧 host、旧环境变量和旧 session.notify handler；旧历史仍可读取。
12. 全八包测试、Ruff、严格 mypy、Flow 示例、agent/coding/eyes wheel/sdist 和隔离安装验证通过。未执行的真实模型或外部 IDE 验证必须在交付中说明。

迁移顺序：先 agent ACP 抽象与标准互操作测试，再 controller/router/ownership，再 coding ACP 与 TUI 展示，再 Eyes ACP handler 和控制面，最后删除旧 host、更新文档并全量验证。不得交付同时运行的双入口实现。

## 13. 协议依据与审核边界

核验来源：ACP 官方 transports、session setup、prompt turn 文档及官方 Python SDK interfaces/example/quickstart。stdio 为标准互操作入口；本地 Unix socket 是明确记录的 XDOG 自定义传输，不称为所有 ACP client 原生支持的标准 transport。

参考入口（仅文档引用，不是运行时配置）：

- https://agentclientprotocol.com/protocol/transports
- https://agentclientprotocol.com/protocol/session-setup
- https://agentclientprotocol.com/protocol/prompt-turn
- https://github.com/agentclientprotocol/python-sdk

用户对本文的批准仅开启实施计划编写；实施计划审核并确认执行方式后，才进入产品代码重构。未得到新的明确请求，不自动 push、开 PR 或发布。
