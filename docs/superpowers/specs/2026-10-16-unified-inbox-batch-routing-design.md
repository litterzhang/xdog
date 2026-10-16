# 统一 Inbox、通用事件路由与批量 Turn 设计

日期：2026-10-16
状态：用户于 2026-10-16 批准本文；实施计划见 `../plans/2026-10-16-unified-inbox-batch-routing.md`，待用户审核。产品代码尚未按本文修改。
执行边界：原 checkout `/data/workspaces/pyspace/xdog`、`feat/eyes-observation`，继续 Native，不创建 worktree，不自动 push/PR/发布。

## 1. 用户确认的意图

本修订以用户连续确认的要求为依据：

1. Eyes 的事件先进入 Router，再由 route 分发给不同 Event Handler；ACP 只是其中一种 handler，不是 Eyes 的唯一处理抽象。
2. 外部 callback/message 进入 session、排队和开始 agent turn 都不需要用户批准。审批属于 agent 工具操作，继续使用目标 session 原有权限策略。
3. 在线 session 同样适用：空闲立即处理、忙时排队，不需要消息批准或人工 resume。
4. 所有外部 callback/ACP 消息统一先持久写入 Inbox；离线不丢失，owner 启动后自动消费。TUI 人类输入仍可直接提交 controller，无需为此次变更强制改成 Inbox。
5. SessionController 可以一次消费多条消息。五条消息进入同一个 turn，历史中保留五条独立输入，不拼接成一个大文本，不分别执行五个 turn。
6. 定义 `max_messages_per_turn`，默认 10，设为 1 恢复单条消费。

本修订在实施后替代 2026-10-15 三份 ACP/endpoint/inbox 设计中的消息审批、一轮唤醒预算、必须人工 resume 和每条消息独占 turn 的语义。单 owner、工具权限、来源保留、精确 session 寻址、持久 ACK 与模型完成分离、未知副作用不重放等要求继续有效。

## 2. 当前代码依据和变更边界

- `eyes/executor.py` 已按 `RouteDef` 匹配事件，但容量检查直接使用 ACP 编码，`HandlerDef` 仅为 ACP，`runtime.py` 的工厂、Entry、状态与恢复也直接绑定 `AcpHandler`。不能只改架构图。
- `coding/core/sessions/controller.py` 已是所有模式的唯一执行调度器，但 `_pump` 每次选一项，自动输入受 `_budget` 约束。
- `coding/core/sessions/inbox_runtime.py` 通过虚拟 `session_inbox_approve` 工具调用审批，再生成执行尝试；watch 目前只发布快照，不自动消费。
- `coding/core/sessions/store.py` 的 InputStore 是请求历史 sidecar，重读未完成项变为 interrupted；它不是可重放的持久 Inbox。
- `agent.Agent.prompt` 已支持 `list[AgentMessage]`；coding 的 `_execute_message`、turn 适配及事件关联仍按单输入实现，需要一条真正的批量执行路径，而非循环调用单条发送方法。
- `interactive/session_events.py` 的 `_pending_messages` 是 controller 的展示镜像，不是第二个模型调度器。

不新增数据库、broker、永久系统服务、TCP/Windows transport、业务 handler 或 claw/flow 专用适配。Eyes 仍不依赖 agent/ai/coding/claw/flow；接入服务仍由外部显式启动，无 owner 时不构造 AgentSession 或运行模型。

## 3. 总体架构与选择

```text
Source → Detector → Event → EventRouter
                              ├─ EventHandler: ACP
                              └─ 注册的其他 EventHandler
                                       │ endpoint + exact session
                                       ▼
                          coding ACP ingress / adapter
                                       │ 持久 intake
                                       ▼
                                 Session Inbox
                                       │ 自动领取，不审批消息
TUI 人类输入 ──────────────────────────┤
                                       ▼
                          唯一 SessionController
                          每批最多 N 条独立消息
                                       ▼
                            AgentSession → Agent
                                       │ 工具操作
                                       ▼
                             现有 Permission Hook

TUI/RPC：订阅输入、batch、工具、输出与状态，不执行第二个消费循环。
```

备选方案：在线直接 prompt、离线才 Inbox 会保留两套入口和不同的恢复事实；在 Inbox 新建独立 agent worker 会违反单 owner。选择统一持久 intake，复用现有 controller 做唯一调度。controller 内存索引负责当前工作，Inbox 是未处理消息的持久事实，不形成两个独立消费者。

## 4. Eyes 的 Router 与 Handler 边界

- EventRouter 只按 detector、event type 和 route 配置匹配，保留事件次序；同一事件可匹配多条 route，分别投递，各 route 的状态和 delivery ID 独立。
- 定义 handler 公共协议和按 type 注册的工厂。生命周期包括初始化、投递、状态快照、数据连接恢复和关闭。ACP 的连接、session、prompt/enqueue 编码及 payload 大小检查由 ACP handler 自己负责。
- runtime、executor 和 router 不读取通用 handler 上的 ACP 专属字段，不导入或判断 `AcpHandler`；状态通过统一 snapshot/metadata 取得。
- handler definition 按 type 判别并交给对应校验器；未知 handler 明确拒绝，不动态 import 用户给出的任意代码。内建仍只有 ACP，通过测试中的第二种独立 handler 证明扩展，不增加无关产品功能。
- 保留现有定义 version 2 的 ACP 配置和 routes；不引入 source 到 coding 的类型依赖。先完成 handler 初始化与验证，再开始采集。
- prompt 和 enqueue 仍是 ACP handler 的不同协议交付模式，不提升为两套 Router。

## 5. 统一 Inbox，无消息审批

### 5.1 在线与离线

所有 coding ACP 外部输入先使用相同 Inbox 存储和验证路径；包括标准 prompt、XDOG enqueue、现有 `--session ID --prompt` 和接入服务转发路径。接入层转发在线请求时使用同一稳定 message ID，不能在 ingress 与 owner 各生成一条消息。

- enqueue 在持久发布后返回 queued/duplicate；是否在线不改变 ACK 语义。
- 标准 prompt 仍要求成功绑定活跃 session，内部经过 Inbox，等待所关联 batch 的最终结果；不得把 durable ACK 伪装成 PromptResponse。
- owner 不在线时，enqueue 保存待处理消息；标准 prompt/既有 attach 不偷偷恢复 owner，不将 inactive 失败变成 enqueue 成功。
- owner 启动时自动接入同 session/cwd/branch 的待处理 Inbox；在线新消息自动进入同一调度路径。打开 TUI 或调用 list/show 均不弹出消息审批。
- session/branch/generation 校验、容量、存储权限或上下文错误仍可拒绝或阻止调度。这些是有效性与安全边界，不是消息审批。
- 默认未处理消息按现有调度优先级：人类输入优先，同优先级保留接纳顺序。Inbox 的顺序使用持久接纳顺序，不依赖目录遍历或重启后的文件名排序。

### 5.2 状态和身份

新的语义状态为：

```text
pending → claimed(owner generation / attempt / batch) → running → terminal
   ▲                 │                                  │
   │ 未开始即失去 owner                                 │ 非正常终止
   └─────────────────┘                                  ▼
                                                   interrupted
```

`claimed` 只是内部调度租约，绝不是批准。message ID、controller request ID、attempt ID、batch ID 职责分离；每次状态改变保持持久去重和明确的所有权验证。

写入 running 必须发生在模型/工具开始之前。崩溃后的 pending 可自动继续；可证明尚未运行的 claimed 可在新 owner 下重新排队；running 恢复为 interrupted，不自动重复副作用。已有失败、完成或用户取消的任务不自动重试。明确的 interrupted retry 表示用户检查未知效果后重新发起执行，不调用额外的“消息批准”权限工具；该次执行中的工具权限仍照常生效。

所有新 pending 消息都可自动处理，包括修订前 pending_review 的积压。旧 approved 只有在确定从未运行时才映射为待处理；旧 running 始终转 interrupted。旧记录/attempt 的来源、错误和历史事实保留，不能把旧批准标记当作新执行权限或删除历史。引入明确的记录版本迁移和旧格式读取，不把所有未知状态默认为可执行。branch 不匹配的消息保留并展示原因，不静默改投其他分支。

### 5.3 权限和预算

删除 `session_inbox_approve` 这一虚拟工具及消费前的 permission hook 调用；删除 `/inbox approve` 必经流程和 controller/Eyes 的 human-credit、one-turn budget、budget-targets 以及人工授权 resume 门槛。

普通外部消息与在线新消息都可以自动开始下一个 turn，不能在 UI、SDK、ACP 或 Eyes fallback 留下第二套隐性信用检查。新 coding capability 不再声明需要此种唤醒预算；旧 resume 扩展/命令不能在新路径授予“消息执行权限”，也不应为恢复旧 API 而保留审批支路。连接恢复、限流暂停和未知结果人工检查与权限批准分开表达。

工具调用仍走现有 PermissionManager。无人审批且未显式 allow-all 时，受限工具失败关闭；外部数据不能改变权限模式，不能成为 human 来源，Eyes 也不能反向替用户批准工具。限流、过滤、合并未投递的重复文件事件和有界容量仍可防止失控，但不把消息执行权绑定到一次人类输入。

## 6. SessionController 的批量消费

### 6.1 配置与选取

- `max_messages_per_turn` 默认 10，严格整数，范围 1..128；拒绝 bool、0、负数和超过当前队列容量的配置。
- 配置遵循 coding 既有 global → project → 显式 SDK/启动 overrides 的优先级；恢复会话时保存/恢复其有效值。附加客户端不能替远端 owner 覆盖配置。具体配置装配和 CLI 是否需要独立 flag 由实施计划明确，本需求不以新 flag 为前提。
- controller 空闲且 producer/control/工具/压缩等真正结算后，从已就绪任务中选取最多 N 条；不为凑满 N 人为延迟，不把尚未完成持久 admission 的消息选入。
- 批次成员固定后，新消息留给后续批次。控制操作继续互斥，导航 fence 仍然有效。
- 同一批仅包含相同 session、branch 和 generation 的输入；保留选择次序、独立 message/request ID、附件和来源。
- 数量限制外，批次输入编码总量不得超过 1 MiB，并继续检查实际模型上下文与图片能力；超出时缩小批次，不截断或拼接消息。合法单条不能因批量包装增加的固定开销永久卡在队首；确实无法执行的单条必须得到可见错误。

### 6.2 一批只执行一次 Agent turn

```text
历史
user: message1
user: message2
user: message3
user: message4
user: message5
assistant: 本批次回复
```

AgentSession 的批量路径只进行一次 turn-start/prompt 重建、必要 compaction、agent.prompt(message_list)、turn-end 和后处理，不是 for 循环调用五次 send_message。一个 turn 内仍可包含正常的多轮模型和工具调用。

普通人类输入是独立 UserMessage；外部输入保留 canonical session_input 的 ID、source/provenance 和不可信证据标记，各自投影为独立 user 输入。保留来源不意味着强行把所有内容包装成一个文本消息。不得通过直接手工 append history 绕过 agent 生命周期、图片验证、context capture、skills、扩展钩子或权限路径。

### 6.3 批次结果、取消和故障

- 每个成员保持自己的 durable 状态和 accepted/result 关联；共享 batch ID、agent run、assistant 回复及工具活动。
- 批次运行不是 N 次独立执行。共享结果不能被 UI 渲染 N 遍；每条输入仍显示一次。参与的 ACP prompt 调用方等待同一批结果，并获得明确的 shared-batch 关联信息；不向未参与也未显式 load 的 peer 广播。
- batch 开始前，单条取消/编辑/移动遵循现有所有权限制；不能修改已经选定并开始处理的消息。
- batch 已经启动后，一名远端 peer 的 cancel/断开只结束该调用方的等待/订阅，不能取消其他成员的共享执行，也不能声称已经从模型上下文移除了其输入。持久执行状态继续反映真实结果，而非因为客户端不再等待就标为未执行。
- 只有一名成员的 batch 可按现有请求取消语义终止；本地明确 abort/退出可以取消整个 batch。所有取消仍需等待真实 producer/tool/summary 清理，不提前释放 writer。
- 批次执行失败按同一次 run 为各成员记录结果，不自动把成员拆开重跑。terminal fsync 失败继续 fail closed，SDK/print/RPC/ACP 不能误报成功。
- 所有实际 running 成员在崩溃后显示 interrupted；不得只恢复其中一条而让其他条被当作未执行。

## 7. UI、协议和历史兼容

TUI 和 RPC 从唯一 controller/Inbox 状态展示 pending、claimed、running、terminal/interrupted、来源、证据及 batch 关联。去除“批准消息后才接纳”的文案和交互，保留只读 list/show，以及明确 cancel/retry 的管理操作。用户工具审批对话保持可用，RPC 等待工具许可时不能阻塞读取 permission_response。

agent 包的标准 ACP backend 不被迫依赖 coding Inbox。coding 的标准 ACP 仍使用官方 SDK、stdio 和现有 Unix-local transport；新状态或 batch 元数据作为明确的 XDOG 数据，不伪装为标准 ACP 新方法。旧 canonical 输入历史继续可读，旧审批事实只是历史。未实施前，当前协议仍以 `docs/acp-session-routing.md` 和现有代码为准，本文不提前宣布新功能已交付。

## 8. 验收和验证

1. 用两种不同 handler 的测试实现验证同一事件的多 route 分发、类型校验、独立故障/状态/清理；非 ACP handler 无需 session/connection 等字段，executor 不生成 ACP payload。
2. 无 TUI/owner 的真实 ACP enqueue 落盘，零 Agent/model 构造；owner 启动自动消费，零消息审批回调。
3. 在线空闲立即消费；在线忙碌连续收到多个消息自动排队并在下一 turn 批量消费，不调用 resume，不要求人类输入补预算。
4. controller 同时持有五条就绪消息时，实际 Agent 只启动一次 turn，历史及 provider-neutral context 中出现五条独立有序输入，非一个拼接输入；外部 provenance 和图片归属保持。
5. 上限 3 的五条消息产生 3+2 两批；上限 1 退化为逐条；默认 10，配置层级、保存恢复、严格整数与范围验证有测试。
6. 当前 batch 运行时的新输入只进入后续批次；跨 branch/generation、人工优先、control/producer/compaction settlement 仍满足单 owner。
7. 混合人类/外部来源不授予权限。无审批端且未 allow-all 的受限工具被拒绝；真实 TUI/RPC 工具审批仍可正常完成。
8. shared batch 的 per-message 状态、单次输出、多 ACP 等待、peer-scoped cancel/断开以及本地全局 abort 不相互误伤；取消不能虚假声称撤回已经执行的输入。
9. 真进程 crash：pending 自动消费、未运行 claimed 安全回收、running → interrupted；旧 pending_review/approved/running 迁移保留身份和历史，不盲目执行未知副作用。
10. 稳定 ID/内容去重、ACK 丢失、同 socket 不同 inbox namespace、容量、累计字节/上下文、权限/类型/UID/symlink 和导航 fence 回归通过；满队列不会丢证据或并发启动第二个 loop。
11. 实际标准 ACP prompt 在内部写 Inbox 但只在完整结果后完成；enqueue ACK 不代表模型完成。现有 SDK/print/RPC/TUI/attach 不误报持久化失败。
12. 更新 AGENTS、协议、README、packaged skill、示例和验收文档。全八包 tests、Ruff、strict mypy、Flow examples、必要 wheel/sdist/隔离 ACP 冒烟检查通过；真实付费 provider/IDE 若未测试明确披露。

采用 TDD，每项关键行为先观察 RED 再实现 GREEN；保留已有无关修改。Native 实施不分派实现子代理；完成后按已选流程进行一次独立全范围审核，修复 Important/Critical 后以回归和全量验证结算。不自动创建新 worktree、push/PR/发布。

## 9. 书面设计自审和交接

- 路由与 ACP handler 职责已分开；没有为了通用性虚构第二种业务 handler。
- 在线/离线均无消息批准；恢复、重试和工具权限没有混名为消息审批。
- 固定批次与单 turn、多条独立历史消息已区分；每成员状态和共享输出都有定义。
- Inbox 是持久事实而非第二个 agent loop；人类优先和单 owner 保留。
- 新 pending 自动处理不等于 running 副作用重放；旧格式迁移和取消边界明确。
- 没有声称产品已更新，也没有以会话批准替代本书面设计及后续实施计划审核。

下一步：审核 `../plans/2026-10-16-unified-inbox-batch-routing.md` 的具体文件/接口/TDD 步骤；继续沿用 Native，实施计划获审后进入产品修改。
