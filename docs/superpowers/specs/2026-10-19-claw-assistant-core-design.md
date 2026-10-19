# Claw 个人助手核心与可复用 Goal — 架构设计

日期：2026-10-19。基线：`33df908`。状态：**用户已确认，授权自主推进设计与实现，不再逐项索取审批**。

## 1. 意图与成功标准

Claw 首先是长期个人助手；自治任务是可选能力，不是普通聊天、记忆、工具执行或提醒的前提。
用户已经逐项确认：统一普通输入生命周期；steer/follow-up 属于 Agent；ACP 不包含 Eyes/Goal
业务；Agent 显式决定取消观察/通知/任务与申请验收；Goal 自行维护存储并能供 Coding 直接复用；
Goal UI 由同一个 Python 扩展提供，通用渲染在 `xdog.tui`，两宿主仅做适配。

本设计把这些结论展开为可实现的协议与边界。下文的内部接口名、存储组织、配置及阶段划分是
此次书面设计的具体建议，须随本文审核，不把此前口头确认视为这些细节已经获批。

成功不是更换目录或增加抽象，而是：关闭 Goals 后助手仍完整；三类入口共享可信接收语义；
执行状态没有多个权威所有者；Goal 实现在 Claw 和 Coding 中实际运行；取消、断线、崩溃及
权限边界仍然可靠；主 TUI、ACP 和 Session 无 Goal 专用状态机。

## 2. 现状与要消除的耦合

- `claw/acp/inbox.py` 当前拥有持久输入，还初始化 observer 业务表。
- `claw/acp/backend.py` 当前消费输入、查询 Goal/observer 并安排渠道报告。
- `core/runtime/gateway.py`、`orchestrator.py`、ACP 及 session 分散维护执行/预留状态。
- `core/queue.py` 的 collect 溢出删除最旧消息；微信上游队列和 TUI 队列有不同保证。
- `core/planning/goal_manager.py` 依赖 GroupRuntime/AgentSession，正常结束安全点隐式申请验收，
  业务写入有直接同步 fsync 路径。
- `channels/tui/tui_app.py` 直接保存 Goal、解释任务状态并绘制 Goal 摘要。
- Coding 与 Claw 已共享 Agent、ChildSupervisor 和基础扩展 UI，但 Coding 没有可复用的 Goal 实现。

## 3. 约束与非目标

- 原 checkout、本地 feature branch；不创建 worktree，不强推，不自动发布、升级版本或推送。
- Python >=3.12；Claw 产品平台为 Linux；私有 Unix ACP 的现有 SDK 版本界限不变。
- 不引入托管服务、外部 broker、TCP/HTTP 接入、第二主 Agent 或新的工具执行循环。
- 不提供公开 Claw Python SDK；复用 Goal 的契约放在独立包，不稳定导出内部运行时。
- 不恢复 Coding 已删除的 frontend RPC 或 Claw 历史分支激活。
- 无旧格式迁移、升级、双读兼容或旧 scheduler 导入；不删除/转换用户旧数据。
- 不改变工具授权。Coding 保持默认 fail-closed；Claw 保留现有可信工具/readonly/MCP/extension 边界。
- 消息接收及自动消费不需要审批；工具执行仍独立受权限控制。
- 不恢复子 Agent 固定运行超时、累计 token/model-call 配额、固定并发/记录数业务上限。
- 不承诺 exactly-once 副作用、断电硬件验证、付费模型验证或跨宿主接管同一 Goal。
- 不恢复任意 Python renderer、复杂扩展表单、自定义 widget 布局或远端代码执行型 UI。

## 4. 包边界与装配

```text
Claw TUI / 微信 / ACP
         | adapters
         v
Claw application: InputService -> GroupController -> AgentSession -> xdog.agent
         |                                             |
         | optional host bindings                      | native steering/supervision
         +--------------------+------------------------+
                              v
                         xdog.goal
                              |
                  Goal storage / tools / roles
                              |
                      Goal Python UI extension
                              |
                 generic extension protocol/data
                         /              \
               Coding adapter       Claw adapter
                         \              /
                         xdog.tui
```

新增发行包 `xdog-goal` / namespace `xdog.goal`，仅直接依赖 `xdog-agent` 与标准库。
不得 import Claw、Coding、TUI、Eyes、ACP 或 Flow。Agent 不反向依赖 Goal。
Claw/Coding 各以显式 opt-in 的 `goals` extra 安装 Goal，并以 `goals_enabled` 启用业务，
默认 false；展示扩展再通过现有显式 extensions 配置加载。缺依赖时启用失败，不静默回退。
工作区/root 开发安装包含 Goal 供测试，但安装不等于自动启用。
现有 Eyes 的显式启用保持独立：普通提醒不依赖 Goal。

不新建大而全的 service locator/event bus/plugin framework。应用入口按命令职责拆分，
GroupController 只持有执行所需状态，不把整个 Gateway 换名字搬进去。

## 5. 核心输入契约

### 5.1 数据与身份

复用 `xdog.agent.session_input.SessionInput` 作为文字/evidence 的 canonical 数据；图片等
现有附件保持独立边界与校验，不为统一而丢失附件或把内容拼成一条字符串。
Claw 的冻结 InputEnvelope 额外绑定：可信 owner/workspace、入口关联、稳定 message ID、
固定回复目标、宿主判定的权限来源，以及可选的宿主注册 admission binding。
这些宿主字段不从 evidence/provenance/source 标签恢复，也不由 ACP 调用者任选。

- `message_id` 标识一次提交；同 ID 同内容返回原收据，同 ID 不同内容拒绝。
- `attempt_id` 标识实际执行尝试；与 Goal 的 task attempt、Agent 输入 ID 分开。
- owner 对 Claw 是 group；session/context ID 在实际执行时关联，不将历史上下文当成另一条路线。
- 原始参数、附件 ID、来源与输入边界保留；不扩大现有 payload/context/image 容量保护。

### 5.2 持久接收与状态

普通新一轮消息统一通过 InputService 保存到应用持有的 GroupInbox，提交后才 ACK。
ACP 未启用也能使用 Inbox。容量满背压或明确拒绝，不能删除已接受消息。
Claw 第一阶段仍保持一次普通输入一次 turn；Coding 既有独立消息批处理、默认10与1..128限制不变。

```text
pending -> claimed -> running -> succeeded | failed | interrupted
   |          |
   +----------+-----> cancelled （仅未开始的有效撤回）
```

`claimed` 是执行准备，不是模型/工具已经开始。GroupController 持有 group 执行所有权，
在 host admission fence 后提交 running，再调用 Agent。准备期间取消必须能阻止开始。
事务已开始的提交即使调用者取消也必须结算；不得提前释放 lease 或误报未提交。
启动恢复 pending/unstarted claims；running 转 interrupted，不能自动重跑。
显式 retry 建立新 attempt，保留原收据，不把原先未知副作用视为不存在。

### 5.3 回复归属与入口交接

回复目标在提交时由宿主固定，不借用后来消息的微信接收人或 TUI 连接；内部通知默认不是广播。
输入、执行结果与渠道送达是不同事实。Claw 只保存通用交付收据；Goal 业务状态不进 Inbox schema。
微信保持逐条 assistant message 交付，不能在最终结束时重复发送聚合正文。
逐条输出及最终结果使用稳定关联 ID；已知未发送可以恢复，未知发送不自动重发。
离线 TUI 的结果留在持久结果/历史中，不把另一连接自动变成原回复接收者。

TUI 未 ACK 时保留文本；ACK 丢失用相同 ID 查询/重试，不自动生成新 ID。
微信接收游标只越过已持久接收的输入；批次部分提交/重启利用原渠道 ID 去重。
附件需要在 ACK 前进入宿主可恢复的受限存储，不能只保存临时内存指针或过期下载句柄。

已持久接收的普通消息不因入口断线自动撤回；断线只是解除展示订阅。
用户明确取消执行仍通过 original owner/run 路由；关闭宿主将 running 标为 interrupted。
不会因断线把 live steering 重新投递成普通消息。

## 6. steer/follow-up 是 Agent 语义

Agent 独占队列、checkpoint、消费顺序、selected/consumed/returned 事实及其收据。
Claw 只验证 original owner/run 和入口访问范围，转发现有 `queue_input`/控制接口。
普通消息不因忙碌自动转换；移除 Claw collect/steer/steer-backlog 混合调度策略。
steer 不强杀正在执行的工具；follow-up 的消费时机由现有 Agent loop 决定。
目标结束后明确返回未送入原运行，不偷偷创建下一轮；恢复信息遵从 Agent 的真实收据。

## 7. Gateway、执行协调与 Session

- Gateway/渠道/ACP：解析、协商、限长、关联、订阅和响应；无消费 pump、Goal 判断或业务表初始化。
- InputService/GroupInbox：持久接收、去重、查询与通用取消/收据。
- 每 group 一个 GroupController：唯一 busy/reserved/running 权威；调度、admission、终态结算，
  在安全边界协调上下文轮换、实际 Agent 控制与宿主关闭。
- AgentSession：当前上下文、prompt、历史、compaction、Agent 事件；不认识 GoalManager。
- 小型应用命令服务：模型、工具、skill、历史等各自的原有权限和修改边界，不塞进 Controller。

用户输入不被其他 group 的后台工作饿死；后台容量控制不伪装成所有模型/子 Agent 的总配额。
UI 可缓存展示状态但不能凭缓存决定运行所有权。取消必须等待真实 producer/工具清理结算。

## 8. Eyes 与 ACP 解耦，保留可靠投递

ManagedObservers 属于可选应用能力，持有自己的定义、游标、immutable outbox、明确取消状态和
通用交付结果关联；不借 ACP backend 获得业务存储，不在 ACP 中找 Goal。
时间计算仍属于 Eyes；file/scheduler -> detector -> router -> handler 架构不变。

既有同事务保障不能因拆模块丢失：source cursor + whole pending delivery 必须在 observer
自己的事务一起提交。向核心 Inbox 的交接使用稳定 message ID/content 的幂等接收：
先提交 outbox，再接收；ACK 丢失重试同 ID。无需跨 Goal/Eyes/Inbox 建立分布式事务。
只有宿主在创建投递时绑定的 admission token 可以标识内部归属。外部 provenance 无权选择。

取消观察由 Agent 显式 `eyes.cancel`；取消通知和取消 worker 执行是独立操作。
宿主记录并兑现明确取消，在 group admission 下校验已经指定撤回的输入，不能误伤无关工作。
取消观察停止新采集及尚未交接的投递；已经由核心 ACK 的消息不因 observer.enabled=false
自动失效。撤回这些输入需要另一个显式、限定 IDs 的输入取消操作；开始后的执行取消也须显式。
ACP/Controller 不因 Goal completed、observer disabled 或来源标签自行推断撤回，业务适配器
也不得以“有效性检查”为名恢复这个隐式决策。既有 managed cancel 的组合行为须拆开并测试。
已开始副作用不能被这些操作回滚。
Goal 完成、一次主 Agent turn 结束或扩展卸载，都不隐式取消 observer/通知/子任务。
普通文件观察不自动恢复；既有明确托管时间恢复及 owned-file 恢复仅兑现已保存的注册，
不创建新意图，不改变 standalone Eyes 生命周期，不自动重放 running/unknown。
Agent 不在线时宿主仍兑现先前明确取消/暂停，不能依赖模型再次醒来才能阻止执行。

## 9. Goal 核心、独立存储与宿主端口

### 9.1 Goal 自有状态

GoalService、冻结模型、状态转换和 GoalStore 放进 `xdog.goal`。
GoalStore 自持有 lease、私有目录及 versioned SQLite journal，管理 criteria revisions、task
assignments/attempts、reports、acceptance、review evidence/results 和业务通知 outbox。
workspace 下的 assignment/report/review 文件属于 Goal；status/plan 展示不得被 Claw 当成执行权威。
由 worker 通过 bound `task_report` 提交，GoalStore 接受后报告视图可重新生成；原始证据保留。

数据根目录、workspace 和 owner binding 由宿主明确提供；不能从用户 JSON 路径或 provenance
选择。原 Goal 数据与新格式使用不同标识，unsupported format 明确失败，不导入、不升级、
不静默创建空状态掩盖旧状态，也不自动删除旧文件。用户可显式选择新的空配置/目录。
Goal 的初始化/关闭/事务均自行维护；Claw 不建 Goal 表，不保存 Goal 状态副本。

### 9.2 小型宿主契约

GoalHost 由 `xdog.goal` 定义为数据及小型异步端口，不接受整个 Session/Runtime/Controller：

- 提供不可由模型伪造的 owner/workspace 与当前操作授权上下文。
- 通过现有 ChildSupervisor 请求启动/查询/明确控制 worker 或 reviewer；提供已注册的角色工具。
- 将 Goal 已提交的通知 outbox 用稳定 ID 交给宿主原有输入路径，并查询接收收据。
- 注册绑定 owner 的只读视图；不暴露数据库、任意方法调用或运行时 Python 对象。

Goal 不直接构建 Agent，不绕过 Coding permission manager 或 Claw readonly/MCP/extension gates。
reviewer 的取证工具来自宿主允许集合；角色文字不是安全边界；不得获得改变 criteria/接受任务权限。
证据指纹与读取实现在 Goal 内部，不依赖 Eyes 的内部文件 source 或宿主私有状态。

### 9.3 可选与显式决策

Goal 的角色资料/工具说明经现有 Agent skills 与通用上下文贡献路径装配；必要的持久工作摘要由
Goal 能力提供。业务可用性不依赖展示扩展：Session 不直接读取 GoalStore，也不在每轮硬编码
GoalManager 摘要；关闭展示扩展不删除 Agent 可查询的 Goal 事实。

Goals 默认不启用；安装或启用工具不等于用户委托长期工作。任务可以自然语言明确委托，
意图不明先询问。主 Agent 规划、分配、接受报告、等待和决定是否继续，不另设 planner。
宿主验证实际用户输入才能授权 criteria 修改及释放用户暂停，来源标签不能授权。
主 Agent 显式取消不再需要的观察/通知/worker；三个动作不能相互代替。

`request_complete` 显式启动独立 reviewer。普通 turn 结束、工具调用数为0、闲置或 Goal=active
都不隐式申请验收。只有完整逐条件意见、真实证据、正确 revision/attempt 和新鲜 fingerprint
能由 GoalService 提交完成；不能通过普通 worker 报告或脚本 exit code 直接完成。

worker 完成、报告更新、明确设置的提醒产生去重通知后，沿既有输入路径唤醒主 Agent。
后台对账可以修复同一义务的遗漏通知，不因“还没完成”产生周期模型调用。
暂停/取消/已处理的通知不能成为重新启动自治的理由。展示扩展失败不改变这些业务状态。

### 9.4 两宿主复用

Claw adapter 绑定长期 group；Coding adapter 绑定明确 session + branch，不借通用 Goal
绕过精确路由/权限。Coding 不新增 daemon、CLI 回调或第二个 SessionController。
上下文 compaction 保持绑定；Coding 分支/会话变化后的旧 Goal 不能自动转移到新 owner。
Claw 上下文轮换不改变 group 身份，Goal 视图由可选能力重建；核心 Session 不查 Goal 状态。
正在运行/settling 的实际资源仍由通用 owner 约束，不用 unfinished Goal 特判阻止所有轮换。

同一实现必须通过两个真实宿主 adapter 验证，不以 fake GoalHost 测试替代。
不支持两宿主同时写同一 Goal 或自动移交所有者，存储 lease 和 owner 校验明确拒绝。

## 10. 一个 Goal Python 扩展，通用 UI 在 tui

Goal wheel 包含 `extensions/goal-ui/extension.yaml` 和 `main.py`、skill 及 worker/reviewer 角色资料。
同一扩展通过现有可信 worker 在两宿主运行，输出 bounded status/notice/命令结果，
使用现有 input/select/confirm，不发送 Python renderer 或导入主 TUI。

为跨进程读取定义最小通用的 owner-bound **read-only extension view**：宿主显式将 view 名字
授权给某个已配置 extension ID；worker 请求 bounded JSON snapshot/query；宿主按当前 owner
提供结果，必要时发送 coalesced view-changed 提示。扩展重读快照，不把提示当权威状态。
通用 view 协议由 Agent extension 层提供，不出现 `goal.get` 或 Goal schema；Goal view 的
投影和授权 binding 由 Goal adapter 提供。不支持任意宿主方法、跨 owner 读取或写操作。
view 读取/刷新不启动模型、不需要 TUI 存在、不授权工具；刷新失败显示 unavailable，不假造空任务。

`xdog.tui` 只渲染既有通用 ExtensionView/交互组件；Coding/Claw 适配扩展数据、当前连接、
能力协商及布局。移除主 TUI 的 Goal state/专用 widget/进度判断/turn-end finalize。
Goal 扩展拥有 `/goals` 等查询命令，卸载清理其 UI 和订阅，不改变 Goal 数据/执行状态。
其他 native todo 展示不在本轮顺便重做，但不能把 turn-end 等同于 Goal 完成。
通用工具事件不包含用于原生 Goal UI 的专用分支；没有扩展也能用文本和工具操作 Goal。

## 11. 存储、异步结算和失败

核心 Inbox/执行收据/对话归 Claw；memory、Eyes、Goal 和 child runtime 各自维护业务存储。
不同存储之间用 immutable ID、outbox 与幂等收据交接，不制造共享可变全局 Goal 状态。

所有可能阻塞的读写/fsync 移到拥有明确顺序的 worker；取消等待实际磁盘提交结算。
持久化先于发布；unknown 不能宣称 failed-not-sent。发现 corruption、lease 冲突、schema
不支持、权限/绑定变化时 fail closed，并保留原数据。禁止后台尝试自动“修好”未知格式。
Goal/observer 视图生成失败与权威事务结果区分，允许从同一已提交 revision 重建视图。

## 12. 分阶段交付边界

每阶段有自己的实现计划和回归门，先完成依赖再切换消费者，不同时进行无边界大重写：

1. **助手输入与执行核心**：持久 Inbox + owner/controller + TUI/微信/ACP 入口，保留现有 Agent 语义。
2. **薄协议与可选 Eyes**：移除 ACP observer/Goal 依赖，完成 outbox/admission/report 交接；
   拆开 Gateway 连接控制与应用命令，旧混合 queue modes 从活动实现退出。
3. **可复用 Goal**：独立包、存储、角色与宿主端口，两个 adapter 跑通相同生命周期；
   切换并删除 Claw 旧 GoalManager/Tracker 活动实现及隐式 runner/turn-end 验收。
4. **共享扩展 UI 与收尾**：通用只读 view、同一 Goal 展示扩展、移除原生 Goal UI，
   可选依赖/配置隔离、冷安装产物和全仓回归。

阶段间允许未切换的现有模块暂留；最终不能有旧/新双执行 owner、双 Goal 权威或隐式兼容路径。
分支不作为可部署最终版本，直到所有阶段验收结束。实现时不重启用户 gateway 或更改真实配置。

## 13. 验收与证据

稳定编号和状态见 `docs/claw-assistant-core-requirements.json`；初始均 pending。
完成不是测试数量达标，而是每条契约有具体源码/进程/协议/产物证据。

关键跨模块场景：

- 无 Goals/ACP/Eyes 的 Claw 普通聊天、记忆、工具与历史可独立使用。
- TUI/微信/ACP 同时输入，容量满、部分 ACK 丢失、附件恢复、目的地隔离和未知发送。
- claim/开始边界取消、执行中崩溃、shutdown 结算，绝不自动副作用重放。
- 显式 steering 的 selected/consumed/returned 与工具 checkpoint，普通消息不隐式转 lane。
- Goal worker 通知到达时主 Agent 忙、owner 轮换、重复/迟到报告、不自动验收/取消。
- 同一 Goal wheel 和同一 UI 扩展分别在 Coding/Claw 使用，Coding 权限不放宽。
- 旧格式拒绝但文件未改动；lease 冲突/corruption 不能变成空成功；视图失败不撤销已提交事实。
- 扩展禁用/崩溃/断线/重连，跨 owner view 拒绝，无 TUI/headless 操作不依赖展示。

门禁：新增第九包 testpath/工作区/CI，`uv run python scripts/test.py`；
`uv run ruff check packages scripts`（若脚本既有例外，精确列出，不批量扩大忽略）；
strict mypy 现有8 namespace + `xdog.goal`；17个 Flow examples 的实际当前结果；
`uv lock --check`、构建所涉及的 wheels/sdists、Twine 和隔离 wheel-only 安装。
独立 Goal-only 环境禁止 import 安装宿主/Eyes/TUI/ACP；同一产物的两宿主验证必须是实际执行。
测试使用可控模型适配器与私有临时 socket/目录，不触碰付费 provider、凭据或用户服务。

## 14. 审核与下一步

用户在设计提交后明确确认，并授权不再索取阶段审批。采用 Native 方式在当前分支连续执行，
先完成阶段1的持久输入基础，再接入消费者与渠道；后续阶段沿同一总契约分别制定实施计划。
每个计划只关闭其实际交付，不缩减总目标。书面设计若变化须同步清单，并记录裁决。
不将设计提交、测试通过或某阶段完成当成整个 goal 完成。未处理 Important/Critical、缺少
两宿主运行证据、保留活动旧路径或未验证的恢复规则，均不能结束总体 goal。
