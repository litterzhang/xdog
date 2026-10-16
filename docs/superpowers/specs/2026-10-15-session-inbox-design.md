# Session 持久收件箱：离线接收与显式恢复

> **Historical checkpoint (2026-10-15), not the current message contract.**
> Message approval, human wake credit and one-message-only turns are superseded
> by the approved 2026-10-16 unified Inbox/batch design. Recorded past results
> and review findings below are preserved, not relabeled as fresh verification.
> Current protocol: `docs/acp-session-routing.md`; current acceptance evidence:
> `docs/unified-inbox-batch-verification.md`.

日期：2026-10-15
状态：原设计及 endpoint/inbox 补充设计与实施计划已获用户批准。实现与旧 host 删除已完成，完整测试、构建与隔离验证已通过；最终独立审核发现的五项 Important 问题均已通过 RED→GREEN 回归修复，全量测试及重建隔离验证通过；验收与裁决见 `docs/eyes-verification.md`、`docs/acp-review-resolution.md`。以 `docs/acp-session-routing.md` 为当前协议说明，保留本文中的历史方案与修订标记。
关联设计：`2026-10-15-acp-session-routing-design.md`。

> **2026-10-15 回调边界修订：** 见 `2026-10-15-acp-endpoint-callback-design.md`（已确认）。Eyes 必须能直连 ACP endpoint，不依赖启动 coding CLI；下文短命 stdio 接收进程仅可作为可选客户端入口，不再是离线投递的必经路径。独立、显式启动的接入服务可在所有 TUI/session owner 退出后继续收件；接入服务也未运行时明确不可达。消息持久化、权限和显式恢复规则不变。

## 1. 用户意图和范围

session 是消息的稳定归属，TUI 不是消息的所有者。TUI 不打开，甚至 session 进程不运行时，也应能向指定 session 投递消息，不丢失消息、不为了接收消息启动模型。

已确认的默认执行策略：重新打开 session 后先展示积压消息，由用户确认或按已有权限机制批准 resume 后再执行，不自动执行全部积压消息。

补充设计替换原设计中的“离线只能拒绝、观察随 live owner 结束、无持久待处理队列”边界。原设计中的通用 ACP、单一执行 owner、权限不升级、TUI 只展示、不保留旧 event handler host 继续成立。

非目标：不引入数据库、网络 broker、永久 session daemon、TCP 远程入口、系统服务安装或模型自动启动；不承诺执行副作用恰好一次。

## 2. 三个独立生命周期

1. **Session 身份和历史**：持久存在，使用明确 ID 寻址。
2. **活跃 session owner**：拥有 runtime 和 writer lease，可为 TUI、RPC 或独立 ACP 模式；进程退出不会让 session 身份失效。
3. **观察进程**：Eyes 经用户显式 start/stop 管理；使用持久收件模式时，不因 TUI/目标 owner 退出而自动结束。Eyes 自身退出不自动重启。

有无 TUI 不决定是否可执行。只有活跃 owner、匹配的项目/branch、以及当前有效的执行批准同时存在，controller 才能消费消息。没有 owner 时只接收，不创建 AgentSession。

## 3. 收件与执行是不同操作

```text
Eyes / ACP Client
        │ standard stdio ACP
        ▼
Coding ACP Adapter（接收模式不创建 AgentSession）
        │ 明确的 XDOG enqueue 扩展
        ▼
Session Inbox（落盘确认）
        │
        │ 活跃 owner + 上下文匹配 + 显式批准
        ▼
Session Controller → AgentSession
        │
        ├── TUI / RPC 展示
        └── 持久结果和状态查询
```

标准 `session/prompt` 继续代表一次完整执行，不把持久入队伪装成 `end_turn`。未运行 session 的 prompt/原有 attach 路径继续明确失败；离线收件必须选择 enqueue。

新增 XDOG 扩展，经 initialize metadata 协商版本 1：

- wire `_xdog/enqueue`（SDK `ext_method('xdog/enqueue', params)`）：验证目标和消息、持久保存，返回 `queued`、同内容 `duplicate`、内容冲突或容量错误。
- wire `_xdog/inbox_status`：只读查询目标 session 中的消息状态，不启动 owner。

请求字段：`session_id`、`cwd`、稳定 `message_id`、`instruction`、结构化 `evidence`、`source`、`provenance`；不接受 permission、origin、人类身份、批准或执行命令字段。文本和来源标签都不能代表授权。

enqueue/status 允许针对已存在但不活跃的 session，不要求先成功 `session/load`。读取既有 session 的身份、工作目录和 branch 只为验证，不创建可写会话或取得执行权。不存在的 session 拒绝，不回退 latest/current，不隐式创建。

原有 bridge 和通用 adapter 复用，不再增加旧 register/deliver/heartbeat 协议或 Eyes 专用 event host。

## 4. CLI 行为

拟新增显式入队模式：

```bash
xdog-coding --session SESSION_ID --enqueue --prompt '检查变化文件'
```

在线和离线都返回清晰的入队状态与 message ID，不假装任务已经完成。既有 `--session ID --prompt TEXT` 保持在线执行并等待结果的含义，不能因目标不在线就悄悄切换成功语义。

入队适配进程经 stdio 可以短暂启动，但不启动 TUI、不构造 Agent、不调用模型。无常驻后台进程也能完成接收。

## 5. 文件存储与并发

采用当前用户私有的每 session 收件目录，独立于模型 history 和 owner socket：

```text
$CODING_DIR/inbox/<validated-session-id>/
  inbox.lock
  pending/<hash-of-message-id>.json
  running/<hash-of-message-id>.json
  terminal/<hash-of-message-id>.json
```

- 目录 0700，消息与 lock 文件 0600，检查 UID/type，拒绝 symlink 与路径越界。
- opaque message ID 不直接作为文件名，使用确定性 hash，并验证文件内的原始 ID。
- 入队写临时文件、fsync 后原子发布；成功落盘前不回复 queued。
- 短期 inbox lock 保护去重、容量判断和状态迁移。它不同于长时间 session writer lease：离线写收件箱不允许写模型历史。
- 相同 message ID + 相同规范化内容返回原记录状态；同 ID 不同内容拒绝，不能替换已审核内容。
- 初始限制：每 session 最多 128 条非终态消息，单条最多 1 MiB；最多保留 4096 个权威去重记录。满额显式拒绝并提示人工归档，不静默丢证据或驱逐 ID。
- 归档属于显式管理操作，不能宣称归档后仍有无限期跨重试去重保证。

收件内容是不可执行数据，instruction 也必须经过目标 harness 的现有权限策略。文件里不能存模型凭据、旧 capability token 或可以绕过审批的授权描述。

## 6. 状态、上下文与批准

```text
pending_review → approved → running → succeeded / failed / cancelled
       ▲             │         │
       │ owner 退出   │         │ 非正常终止
       └─────────────┘         ▼
                         interrupted
                         （需要人工处理）
```

- 离线接受的消息绑定稳定 session ID、验证过的项目和 enqueue 时的 branch；不绑定已消失的进程 generation。
- 领取时将执行批准绑定到当前 owner generation 与 branch。branch 不匹配的消息保持待审，不静默改投新 branch。
- 新 owner 打开后展示 pending 和 interrupted；不把磁盘记录直接变成 runnable 队列。
- 确认可以选一条或一个明确批次，不默认批准所有积压；同一自动唤醒预算仍约束当前批次实际启动。
- 普通 enqueue、状态查询、客户端自报 human/allow-all、加载 skill 均不能补预算或批准消息。
- 在有明确 headless owner 的情况下，允许通过其已有权限机制批准恢复；这与是否存在 TUI 无关。enqueue 自身绝不调用 resume。
- 正常关闭时尚未执行的 approved 消息失去当前 generation 的授权，回到待审；不会把批准持久化成永久执行许可。
- 已经 running 的任务在崩溃后标 interrupted，不自动回到 pending 执行。用户可以检查实际文件和工具副作用，再明确创建新执行尝试。
- message ID 与 execution attempt ID 分开：一次已审核消息产生一个受当前 owner 管理的 attempt；来源及 attempt 关联持久保存。

## 7. Controller 与历史展示

领取 running 只能由持有 session writer lease 的 controller 完成。收件箱是持久待审事实；controller 的内存执行队列仍是唯一运行调度器，不增加第二个 agent loop。

TUI、RPC 和 ACP status 都从同一个 inbox/controller 状态读取：显示来源、自报标签、处理要求、证据摘要、待审/排队/执行/终态。排队不能提前进入模型上下文，真正模型接纳时记录规范 session input；用 message ID/attempt ID 关联去重，不重复 append 一条输入。

TUI 重开后可以看到离线收到的内容及状态。旧 `xdog.external_event.v1` 历史继续只读兼容，但不恢复旧 host。

原先仅展示用的 InputStore sidecar 不自动升级成可执行重放队列；明确批准的 inbox 消息领取后才创建 controller submission。两类文件的职责和错误诊断必须分开。

## 8. Eyes 行为

ACP handler 增加显式 `delivery: 'prompt' | 'enqueue'`；缺省 prompt 保持原通用协议语义。需要离线保留的 coding 观察定义选择 enqueue。

- prompt 可连接任意兼容 ACP agent；发送后结果未知仍暂停，不能盲目重发。
- enqueue 必须协商目标支持 XDOG inbox 扩展，未支持时明确拒绝，不回退为立即启动模型的 prompt。
- enqueue 使用稳定 message ID，ACK 丢失可以按同 ID/同内容重试，因为 inbox 提供显式持久去重；不能把这个保证泛化到标准 ACP prompt。
- Eyes source/detector/route 不理解 coding 内部 inbox。handler 负责协议投递，runtime 展示 target 和收件/处理两个不同状态。
- TUI 退出后 enqueue 观察可以继续；达到 inbox/证据容量边界暂停并报告。没有 owner 不代表 delivery 成功执行。
- 加载 skill 不启动观察；观察仍需用户明确授权，允许后台采集不等于授权工具修改项目。

## 9. 验收

1. 没有 TUI、没有 owner 进程时，真实 stdio ACP enqueue 成功，磁盘有完整消息，未创建 AgentSession、未调用模型、未创建 owner socket。
2. 同 ID/同内容在丢 ACK 后重试只保留一条；同 ID 不同内容拒绝；并发写入和容量边界保持一致。
3. 目标 session 不存在、工作目录不匹配、目录/文件 owner 或 mode 不对、symlink、保留字段伪造都 fail closed。
4. 打开目标 session 可见离线消息，但模型调用次数仍是零；显式确认后才通过同一个 controller 执行。
5. 确认范围、预算耗尽、审批拒绝、branch 变化、关闭再开均不授予额外执行权限。
6. crash-at-running 显示 interrupted，不自动重复工具副作用；queued/approved 不会无审批重放。
7. Eyes enqueue 模式在 owner 退出后继续采集和入队；Eyes 退出后不会自动重启；停止观察不会删除已收件消息。
8. 在线/离线 TUI 展示及恢复一致，原有 ACP prompt、--session 在线执行、权限和标准客户端互操作没有回退。
9. 旧 event host 和旧 capability 不存在；离线入队仅通过复用的 ACP adapter 与 session inbox 完成。
10. 完成后执行全仓测试、lint、strict mypy、Flow 示例、打包和隔离安装验证；未做的真实 provider/IDE 验证明确说明。

## 10. 审核与实施交接（获批实施前的历史记录）

本文自审：离线接收与执行 ACK 已区分；message 与 attempt 身份已区分；批准明确绑定当前 owner 而非永久授权；TUI 生命周期与 session/Eyes 生命周期已分开；不声称副作用恰好一次；没有借持久化恢复旧 event host。

本文已获确认；inbox 和 endpoint 实施补充计划见 `../plans/2026-10-15-acp-endpoint-inbox.md`，审核后按已选择的 Native/TDD 方式执行。已批准且不受修订影响的 ACP/session 工作仍可继续；受 endpoint 接口和服务生命周期修订影响的 Tasks 8/9 接线必须先完成补充设计与实施计划审核，不能继续强制 command-only 路径。
