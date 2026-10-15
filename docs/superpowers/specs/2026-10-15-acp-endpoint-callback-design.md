# ACP endpoint 回调：与 coding CLI、TUI 生命周期解耦

日期：2026-10-15
状态：用户已于 2026-10-15 确认本文及关联 inbox 设计。实施补充计划 `../plans/2026-10-15-acp-endpoint-inbox.md` 已编写，等待审核；尚未开始该计划的产品代码修改。
关联：`2026-10-15-acp-session-routing-design.md`、`2026-10-15-session-inbox-design.md`。

## 1. 用户意图与替代范围

用户明确纠正：callback 不应依赖 coding CLI，应使用 ACP；随后确认 ACP session service 的生命周期独立于 TUI，Eyes 只连接服务、不负责启动 coding。

必须满足：

- Eyes handler 的目标是 ACP endpoint 和明确的 session ID，不是 coding executable。
- coding 不知道 Eyes 的具体 source；来源和证据只是通用 session input 数据。
- TUI 是输入端和展示端，不是回调入口、消息所有者或离线收件前提。
- TUI 和 session 执行进程都未运行时，已显式启动的 ACP 接入服务仍可持久收件；收件不创建 Agent、不运行模型。
- 没有任何接收进程运行时，不可能收到网络/Unix 请求：返回不可达，不能假装已经持久接收，也不能由 Eyes 偷偷启动 coding。
- 保留单一执行 owner、现有权限、明确批准后恢复，以及旧 event handler host 的彻底删除。

本文替代原 ACP 设计 §3/§10 和计划 Tasks 8/9 中“Eyes 必须启动 stdio coding 桥接”的部分；替代 inbox 设计 §3/§4/验收 1 中“短命 coding CLI 接收进程是离线投递必经路径”的部分。其余已批准实现继续有效。本文不追认既有 Task 8 的 command-only 接口为最终设计。

## 2. 方案选择

1. **只直连活跃 session owner 的 ACP endpoint**：最少新增机制，但 owner 退出后无法实现离线收件，单独采用不满足完整目标。
2. **独立、显式管理的轻量 ACP 接入服务（选择）**：在线请求路由给唯一 owner；离线请求写 session inbox。不需要 TUI，也不需要在回调时启动 coding CLI。
3. **由 Eyes 启动 coding stdio bridge（不作为 coding 回调路径）**：可以作为通用第三方 ACP 互操作选项，但不能满足本次用户要求。

独立接入不等于永久模型 daemon：没有系统服务安装、自动开机启动、自动 owner 恢复或自动模型执行。服务由用户/调用方显式启动和停止；停止 TUI 不停止它，停止 Eyes 也不停止它。

## 3. 组件与数据流

```text
Eyes source → detector → event → ACP handler
                                    │ endpoint + session ID
                                    ▼
                         通用 ACP 协议 adapter
                                    │ 注入 coding backend
                                    ▼
                       轻量 session 接入服务
                         ├─ prompt → 活跃 owner 的 ACP endpoint
                         │                       │
                         │                 session controller
                         │                       │
                         │                 唯一 AgentSession
                         └─ enqueue → 持久 inbox │
                                         │      │
                              明确批准后由 controller 领取
                                                │
                                    TUI/RPC 展示输入、状态、输出
```

- `xdog.agent.acp` 提供通用 adapter/backend contract 和 transport helper；不导入 coding 或 Eyes，不拥有 inbox 策略。
- coding 接入服务只组合 backend、私有 transport、owner 定位与 inbox；不建立第二套 Agent loop。
- coding 既有 owner endpoint 继续提供活跃会话访问；接入服务通过 ACP 转发，不能加载一份可写历史冒充 owner。
- Eyes 仅依赖 ACP SDK；不导入 coding、不读取 coding owner registry 或 inbox 文件，也不自己计算 coding socket 路径。
- inbox 存储根由服务启动配置决定，客户端不能传入存储目录或更换服务的数据根。

## 4. Eyes 定义与连接接口

version 2 handler 使用明确的连接描述；现有未完成迁移的 version 2 尚未作为稳定格式交付，直接修订，不保留两种含糊的顶层入口。

```json
{
  "id": "review",
  "type": "acp",
  "connection": {
    "transport": "unix",
    "path": "/absolute/private/runtime/acp.sock"
  },
  "session": "EXPLICIT_SESSION_ID",
  "delivery": "enqueue",
  "instruction": "检查变化文件及相关测试；遵守目标会话权限。"
}
```

- `connection` 是有 discriminator 的 frozen model，拒绝未知字段和混合配置。
- `unix` 要求绝对路径、合法 UTF-8、无 NUL；长度遵守实际本地 socket 限制。连接检查私有目录/socket 的 UID、类型和权限，拒绝 symlink；这不是防御敌对同 UID 进程的沙箱。
- 通用 `stdio` 选项为 `{"transport":"stdio","command":["third-party-acp-agent"]}`；argv 不经过 shell，不要求或注入 coding 参数。`unix` 分支不得创建 subprocess。
- `session` 无 latest/current/new fallback；`delivery` 为 `prompt | enqueue`，缺省 prompt。enqueue 必须显式协商 inbox 扩展。
- endpoint 由外部配置/用户提供。第一版不增加自动发现编码、不扫描 coding 目录、不通过 source 反推会话。
- 路径是连接地址，不是授权令牌。会话项目、分支、权限和来源校验仍由服务/owner 完成。

stdio 与 Unix 都复用同一套 ACP initialize、能力协商和请求/结果处理。Unix 是 XDOG 明确记录的自定义本地传输，不声称所有 ACP 客户端都原生支持它。不因此升级已锁定 SDK 或切换 ACP 协议版本。

## 5. 三个独立生命周期与协议语义

### 5.1 接入服务

- 可用 Python API/module 显式启动，不要求调用 coding 的交互式 CLI；启动方式不进入 Eyes callback 定义。
- 在加载 agent/provider 之前分派接收模式。导入/启动接入服务和执行 enqueue/status 不构造 AgentSession、不解析模型或获取模型认证。
- 使用当前 UID 的私有 runtime directory、flock 服务 lease 和 generation。所有启动方式共享唯一服务 lease；不以 PID 存活替代锁或 live handshake。
- 启动失败释放自己的资源；不删除另一个活服务的 socket。关闭只移除自己 inode/generation 的 socket/记录。
- 服务只管理自己的连接，不因一个 peer 断开而关闭其他 session owner。无永久系统 daemon、TCP、Windows transport 或自动启动。

### 5.2 在线执行：标准 prompt

- initialize 后显式 load session；接入服务定位并核对现有 owner 的 session、cwd、generation，再绑定该 peer 的目标上下文。
- 标准 prompt 必须由活跃 owner controller 执行，流式 update 和最终结果转发给原 peer；服务本身不运行模型。
- unknown/inactive/project mismatch 明确失败；不自动恢复磁盘会话，不把失败 prompt 偷换成成功 enqueue。
- peer/request scoped cancel 只影响该 peer 自己的提交。连接失效结算对应请求，不关闭 TUI/其他 owner。
- owner/branch generation 变化后旧绑定失效，不自动改投同 ID 的新 owner。
- 已发送 prompt 的超时/断开继续是 outcome-unknown；不自动重发，不将 update 视为完成。

### 5.3 持久收件：协商的 XDOG 扩展

- 保留 inbox 设计的 `_xdog/enqueue`、`_xdog/inbox_status`，明确不是标准 prompt 方法或 completion ACK。
- enqueue/status 不要求活跃 owner 或先成功 load；必须验证持久 session 存在、cwd/branch 匹配、存储安全、消息身份与容量。
- 服务对消息 fsync/原子发布后才返回 durable ACK；同 message ID/同内容返回同一事实，冲突拒绝。
- TUI/owner 关闭后仍可以 enqueue。服务也关闭则不可达；既有 durable 消息不受影响。
- 只有经批准的当前 controller 可以领取 inbox；服务不在后台主动消费，不因 online 状态或客户端自报字段授予执行权。
- inbox 的文件锁、配额、状态机、attempt ID、崩溃后 interrupted 和展示规则沿用关联 inbox 设计，不把 InputStore 变成执行队列。

## 6. 权限、恢复与结果未知

- 所有执行继续走原 session 权限 hook，外部输入不增加自动预算；无人审批且未显式 allow-all 时仍 fail closed。
- Eyes 反向 permission 默认拒绝；resume 是申请批准，不是授予批准。接入服务不能替 Eyes 自动回答为人类同意。
- 服务转发 owner 扩展时只声明真正端到端可用的能力；不得因为通用 adapter 有一个方法就宣称 owner 支持。
- durable enqueue 的 ACK 丢失可按稳定 ID/内容查询或重试；此保证来自 inbox，不扩展到 prompt。
- 第一版断连暂停并报告，不自动跨 endpoint/owner 重连。显式重连后，enqueue 只有在同一验证过的 session/项目存储上才能用原 ID 查询/重试；不得创建新 ID 规避未知结果。
- 观察 baseline/防抖/证据容量和 Eyes 本地 start/status/stop/resume 控制面仍属于 Eyes；这些不是 agent event host，不接收 coding 执行回调。

## 7. 验收与测试证据

1. 真实 Unix ACP peer，无 coding imports/executable，Eyes 完成 initialize/load/prompt/update/final；断言没有 subprocess spawn。
2. Eyes 定义不含 coding command；缺 endpoint、混合连接、错误路径/权限/类型、unknown session/cwd 均明确拒绝，不扫描或激活 source。
3. coding 真实 owner + 独立接入服务 + Eyes：文件变化路由到指定 session，实时/历史 TUI 展示来源、证据、状态和输出；没有第二个 AgentSession。
4. 所有 TUI/session owner 进程均未运行，独立 ACP 接入服务仍可 enqueue/status；完整消息落盘，模型/AgentSession/owner socket 创建次数为零。
5. 停止 TUI 不停止服务或 enqueue 观察；停止服务使后续投递明确不可达，不启动 coding；之前已 ACK 的消息仍在。
6. owner 打开后先展示消息，零自动模型调用；明确批准/预算允许后才经同一 controller 执行。
7. 两个服务竞争启动只有一个取得 lease；错误 UID/mode/symlink、stale socket、启动失败和清理均不偷锁、不误删活 endpoint。
8. prompt 断连结果未知不重发；enqueue ACK 丢失同 ID 去重；跨项目/branch、owner generation 改变和取消不误投其他任务。
9. 通用 stdio third-party ACP 测试保留；Python/SDK optional-import、权限、自动预算、包隔离与现有 CLI attach 行为无回退。
10. 删除旧 host、capability、transport、env 接线；全八包测试、Ruff、strict mypy、Flow 示例、构建和隔离安装，以及最终独立审核完成。未验证真实 provider/外部 IDE 明确记录。

## 8. 状态与实施交接

当前树已有 agent ACP、coding controller/owner endpoint/TUI 展示，以及 Eyes command-only ACP handler；独立接入服务、Unix Eyes client 和 durable inbox 均未完成。原 Task 9 尚未修改产品代码。不能以既有局部测试通过声称完成此补充设计。

本文自审：没有把“没有 TUI/owner”混同为“没有接入进程”；没有把 durable ACK 当执行完成；没有把 stdio 与 ACP 本身混同；没有由 Eyes 读取 coding 内部目录；没有自动启动模型或绕过单一 owner；没有以新名称恢复旧 event host。

本文及关联 inbox 设计已获确认；受影响任务和 inbox 实施计划已更新为 `../plans/2026-10-15-acp-endpoint-inbox.md`，等待用户审核；沿用 Native 执行选择，不自行改为实现子代理或 worktree。当前不自动 push/PR/发布。

## 9. 协议依据

2026-10-15 核对 ACP 官方 v1 transports 与 extensibility：允许保留协议生命周期的自定义 transport；扩展方法使用下划线前缀并协商支持。以下仅为文档来源，不是运行时地址：

- `https://agentclientprotocol.com/protocol/v1/transports`
- `https://agentclientprotocol.com/protocol/v1/extensibility`
