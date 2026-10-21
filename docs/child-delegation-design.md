# 子 Agent：现状审计与宿主运行时边界设计

日期：2026-10-21。审计基线：`ff653e44abb8279a456fbc60389186cdad0597fa`。
本文保留实施前审计与设计依据，不代替实现验收。最终实现与验证见 `child-delegation-verification.md`；历史审计须与 HEAD 和当前实现区分。
需求唯一计数源：`docs/child-delegation-requirements.json`（SA-01～SA-10）。

## 1. 已确认的产品约束

- 每个 child 绑定一个真实父 Agent；tools、skills、调用权限不得超过父的可委派范围。
- child 不继承父对话历史、system prompt、对话变换器、审批缓存或完整可写 skill manager。
- 父可设 child 独立 system prompt；任务与材料通过第一条 user 消息传递。
- Coding 主聊天区可见实际父→子/子→父消息，UI 不是执行所有者。
- Agent 提供 SDK 操作、协议和纯数据校验；各宿主维护 child runtime。
- child 支持独立 model/thinking；使用真实模型能力，错误明确拒绝，换模型不扩大授权。
- 不新增 child 运行时长、轮数、总 token、并发数量或记录数量的固定寿命限制。
- 不改变当前不允许 child 再创建后代的产品边界；本轮不扩展任意深度 agent 树。
- 保留 Linux 权限、取消清理、崩溃一致性、图片、模型路由、压缩归档等既有保护。

## 2. 现有功能与实际归属

| 功能 | 当前实现与入口 | 审计结论 |
| --- | --- | --- |
| 创建/查看/等待/取消/关闭 | `agent/supervision.py: ChildSupervisor.start/control/tools`；统一 `agent` 模型工具 | 已有，必须保留；SDK 外观与执行调度混在同一类 |
| 实际执行 | `ChildSupervisor._ensure_loop/_bridge/_run/_settle` | Agent 包创建 `xdog-child-owner` 线程、独立 loop、task 并执行 Agent；不是仅有 SDK |
| Coding 宿主适配 | `coding/core/child_agents.py: CodingChildren` | 有工厂、工具准入、独立审批和扩展，尚不拥有完整 runtime |
| Claw 宿主适配 | `claw/core/runtime/child_agents.py: ClawChildren` | 有工厂、只读/MCP/扩展策略及跨 loop 服务桥，尚不拥有完整 runtime |
| 父绑定 | 两 host factory 捕获 session；`agent/capabilities/children.py: ChildPort` | 有真实进程内绑定基础，但不是统一、受限、可撤销的父句柄；ChildPort 依赖具体 Supervisor 及其私有字段 |
| 工具范围 | 两 host `capture/create` 与 `ExtensionRuntime.sync_tools` | 父有显式 active selection 时检查子集；selection=None 时上限仍可能为 None，不能证明之后的扩展注册不会扩张 |
| Coding 审批 | 每 child 新建 `PermissionManager`；交互 UI 绑定 child/request/owner | 已有 ask/无审批通道拒绝/不复制父会话批准；需统一补父授权版本及审批后能力复核 |
| Claw 权限 | readonly、MCP allowed、extension allowlist | 已有，保留；统一父能力实时撤权检查仍需补齐 |
| skills | child Agent 未接入独立 skills placement；Claw 复用父 tool_ctx | 未实现选定子 catalog；Claw tool_ctx 包含父 `_skill_manager`，不能视为只读子集 |
| 父历史/system | HEAD `ChildSpec.inherit_context=True`；factory 拷贝完整历史边界；`child_prompt` 拼父 prompt | HEAD 不符合新决定；当前未提交改动改为 false/独立 prompt，但 host 的父变换器与上下文捕获还没全部移除 |
| 扩展上下文 | `agent/supervision_extensions.py: ChildExtensions` | 独立 worker/entries 已有；HEAD 会执行父 context transform，工作区已部分去掉；Coding factory 仍调用父 base input/context transform |
| 模型/推理/图片能力 | `supervision_model.py` 与两 host factory | 已有，复用实际 provider metadata 和图片验证；不能凭 prompt 选路或虚构模型容量 |
| 压缩 | `supervision_model.py: ChildModel.compaction`；Supervisor 在 summary 前归档 | 已有真实 metadata 才启用的 built-in policy；宿主必须接管归档及失败处理，Agent 通用 compaction 不重写 |
| 过程输入 | steer/follow-up、队列编辑/排序、停止后恢复、带图两阶段取回 | 已有，多处 receipt/revision/owner 保护；保留主 Agent 管理入口，不能因搬 runtime 丢失 |
| 持久化/恢复 | `agent/supervision_store.py: ChildStore`；admission/inputs/returns sidecar、分片 history、compaction archive | 已有原子持久化和不自动重放；当前由 Agent 监督器协调，需归宿主。存储记录不是运行权限 |
| 扩展交互/worker | `ChildExtensions`、child dialogs、pending message bindings | 已有独立 owner；宿主应负责创建与关闭，父 pre-tool 策略不能随父 context transform 一起删掉 |
| Coding UI | `/agents`、child panel/source、审批；未提交 `child_messages.py` | HEAD 有面板、工具进度/历史查看；主聊天双向消息是未提交中间实现，不计作完成 |
| Claw 控制/显示 | gateway 原有 child 命令、TUI 和固定 group 路由 | 保留必要产品通信，不因清除 SDK 运行时而删 gateway/MCP/worker 通信 |
| 关闭 | Supervisor `abort/close/aclose`，两 host lifecycle 调用 | 已有等待 writer/worker 清理；调用方还检测 Supervisor 私有线程，迁移必须一起解耦 |
| Flow/Goal | Flow 源码未发现直接使用 ChildSupervisor；Goal UI 引入 `settle_on` | 不给 Flow 自动启用 child runtime；Goal 只改通用 bridge 的依赖，不引入 child/Goal 调度耦合 |

路径表中的 `agent/`、`coding/`、`claw/` 分别指对应 `packages/*/src/xdog/*/`。

### 工作区验证（不是 HEAD 发布版本验证）

命令：

```sh
uv run pytest packages/coding/tests/test_child_agents.py \
  packages/claw/tests/test_child_agents.py \
  packages/coding/tests/test_child_active_tools.py -q
```

结果：**49 passed / 5 failed，7.97s**。没有跳过失败或削弱断言。

- 1 项旧测试要求继承父历史，与已确认的新语义冲突：改为验证不继承且首轮交接有效。
- 4 项失败涉及 `--instructions` / preset instructions 不再出现在 system prompt。
  当前中间实现直接忽略旧 instructions 会丢任务知识，不能只改断言让测试通过。
  设计：新 API 用独立 `system_prompt` 与首轮任务；旧调用的非空 instructions 明确转为首轮 user 的材料段，
  不转为父 system、不丢弃。旧持久记录仅展示，不借此重新启动模型或回放副作用。
- 49 项通过只证明本次选择的现有行为，不能替代新增父句柄/skills/运行时边界验收。

另有上一实现阶段启动的 Agent 全组选测 PID `58665`，最后确认仍在等待；未结束、未计通过，
未因本轮审计终止。fixture future 未完成只是待验证推断，不作已定位根因。
当前 stopped child 的 prompt 修改仅更新配置，不代表自动恢复同一 child 的下一轮执行；
消息去重也仅是进程内事实投影，不承诺重启自动补发。

### Coding UI 补充审计

- `interactive_mode.py:1045–1320`：面板、presets、snapshot、control、transcript reader 直接依赖 Supervisor，后续改走宿主端口。
- `interactive_mode.py:2801–2863` 与 `child_messages.py:55–104`：已有未提交 owner/lease fencing、去重、退订实现，可保留纯 presenter；不能把它当执行授权。
- skills picker 当前读父 Agent 私有 `_skills`，需要改为父句柄提供的实际可委派目录。
- `session_events.py:110–115` 的 producer cancel 关联 children.abort；应明确区分取消 UI 操作和取消实际子工作。
- 消息主日志是内存投影，历史不伪造重放；完整 child transcript 仍由原历史入口读取。
- UI worker 报告相关 195 项通过、定向 Ruff/mypy 通过；属于子任务证据，未据此标记需求完成。

## 3. 目标边界

```text
主 Agent / 原生用户命令
    → Agent SDK: ChildClient / ChildHandle（校验参数、调用注入端口）
    → 宿主 ChildController（实际 admission、权限、任务、持久化、事件、清理）
    → 普通 Agent 实例（复用原模型/工具循环）

child 的工具调用
    → child 独立 hook
    → 真实父句柄的实时授权检查 + child 已获授权
    → 宿主原权限/审批策略
    → 真正工具
```

### 3.1 Agent 包保留什么

建议在 `xdog.agent.children` 暴露小接口（名称可在实现时统一）：

- frozen `ChildConfig/ChildSpec/ChildSnapshot/ChildMessage` 等数据协议。
- `ChildClient`：spawn/list/presets；`ChildHandle`：inspect/wait/steer/follow-up/cancel/close 等 SDK 操作。
- `ChildBackend` Protocol：由宿主注入操作实现；SDK 不寻找全局 runtime，不从快照加载代码。
- 受限 `ParentHandle` 的协议及纯委派校验；真实实现由宿主创建和保管。
- 统一 `agent` 工具 schema/参数解析：同一 SDK 客户端调用相同 backend，不另建 model loop。
- 纯队列校验、history/record codec、纯状态转换可复用；它们不执行 IO、不持有任务、不决定恢复和重试。
- 普通 `Agent` 单实例 runtime、compaction、通用 owned IO/跨 loop settle helper 保持原用途。

**不保留：**创建子调度线程/loop、运行 task 注册表、监督执行 coroutine、恢复扫描/自动执行、
child 磁盘路径与写盘策略、worker 生命周期、宿主审批/UI 路由。
不能把现有 Supervisor 改名为 Agent 内的 hosting/runtime，就声称满足边界。

### 3.2 宿主拥有什么

- Coding 的 `CodingChildren` 与 Claw 的 `ClawChildren` 成为真实 child controller，分别负责创建、执行和清理。
- admission 必须在实际父 owner 上完成；生成 child ID、父句柄、授权快照，再开始异步创建。
- 持有各 child 的 Agent/task、独立权限管理器、扩展 worker、持久化协调器与订阅。
- 存储 IO 使用已有 owned IO 工具离开 loop；存储协议解析可共用，恢复决策只在宿主。
- 唯一任务执行仍是普通 Agent；不复制 `agent_loop.py`。
- 两宿主的权限、group/session 生命周期不同，不强行抽成一个带几十个回调的万能 Supervisor。
  优先共用协议、codec、纯转换与测试契约；少量宿主编排重复可以接受，不新增 distribution。

### 3.3 loop 选择不能机械替换

- Coding 已有 `SessionController` owner，但 SDK 单次调用结束可能释放其生命周期。
  为不把本次边界重构扩为整个 session controller 生命周期重写，最终实现保留 **Coding 宿主持有的专用 child 线程/loop**，
  所有启动、取消、持久化结算及 join 都在 Coding package；它不是 Agent SDK 的后台运行时。
  child task 不属于 UI 临时 `asyncio.run()` 或主 Agent 某一轮的 task。
  未声称已经复用 SessionController，也不承诺 Coding 进程退出后后台继续执行。
- 宿主需要提供专门的 child-control admission 通道；不能把 cancel/wait 放进必须等待父 turn 空闲的串行队列，
  否则父正在调用 wait 或 child 正等审批时会死锁。
- Claw 使用 gateway/application 的长期 loop；child 服务与 group 服务同 loop 优先，跨 loop 只用通用 bridge。
- parent turn 自然结束、UI 面板关闭、连接断开，不等于 child runtime 结束。
  Coding 整个终端退出仍走现有 cancel/dispose；这不是后台守护服务，不承诺进程退出后继续运行。
- application/context 真实关闭先撤销新 admission，再取消/等待 child、存储写入和扩展清理，最后释放 owner/lease。
  close 的取消信号不能让已提交但未确认的 writer 失去归属。

## 4. 真实父句柄与子集授权

`ParentHandle` 是宿主生成的进程内能力对象，不是 parent_id 字符串，也不直接暴露父 Agent/session。
内部绑定原父实例/owner generation；只提供 live/revision、可委派能力查询和受控检查。
SDK 可为 child 暴露只读 `parent: ParentHandle`（根 Agent 为 None），由宿主构建时注入；
它不是访问父 messages/system/凭据或控制父运行的通用入口，也不进入 dump/restore 数据。
child 执行上下文维护该句柄；模型、快照、role 标签和路径都不能重新构造它。

### 创建和变更

1. 读取父**当前真实可委派集合**，排除父专用工具及不可下放 capability。
2. 显式请求逐项核对；任一 tools/skills/action 越界，整体拒绝，不能悄悄裁剪。
3. tools 未指定可解析为当前全部可委派工具，但结果必须冻结成明确集合；不能留下表示无限范围的 None。
4. skills 默认空；`null` 如保留则同样在本次 admission 解析为父当前可委派 catalog，而非运行时自动继承。
   父可委派 catalog 与父当前激活 skills 不等同；仅已安装但未授予父使用的内容不属于可委派集合。
5. 父以后新增能力不自动给旧 child。未来若显式重新授权，只能由父经同一 admission 路径生成新授权版本。
   本轮不为此增加泛化热重配 API；已有 child 允许收紧，越原上限的配置变更拒绝。

### 执行期

```text
本次可用 = child 已获授权 ∩ 原父当前可委派能力 ∩ 宿主当前策略
```

- 使用工具 identity/provider binding + generation，不能只比同名字符串；合法代理工具保存原始身份映射。
- 每次工具/skill 操作、每次模型上下文准备前检查；hook 改写参数后及审批 await 后复查。
- Coding 检查父 permission manager 身份/版本；父 approve、兄弟 approve 均不转给 child。
- Claw 保留 readonly/MCP/extension allowlist；绑定 capability 也需明确子授权，不能泛化为绕过审批的名称豁免。
- 父失效后拒绝新的模型/工具执行，但宿主取消、查看已有证据和清理通道始终可用。
- 撤权不撤销已开始工具的实际副作用；宿主发出取消并结算真实状态，不能声称回滚。
- shared workspace/bash 仍不是 OS 安全沙箱；skills 子集仅约束 SDK 注入与加载入口，不能承诺禁止所有文件读取。

## 5. 独立上下文与 skills

- 创建时不要先捕获父完整 AgentState 再“忘掉”消息；只取必要模型配置、工具身份和授权 metadata。
- child 的第一条 user 消息包含 task、材料、附件；system 使用 child 专用 prompt + 宿主必要安全/runtime 信息。
- 父 conversation input/context transforms 不调用；父 pre-tool 权限 hook 仍执行。
- child 具有独立只读 skill catalog 与激活状态，使用原 SkillPlacement/只读加载机制。
- Claw 不复制父 tool_ctx 字典；由宿主构建明确字段，注入 child 只读 skill manager 和受限服务。
- skills 的描述、加载与资源读取都限定在父允许且本 child 选中的集合；不带 create/patch/remove 权限。
- 独立 extension worker 仍受父注册身份/工具授权上限控制，不能经 initialize/on-event 后注册工具扩权。
- system prompt 更新为 idle-only；运行中明确拒绝，不强制重启、不重放。允许元数据更新不代表自动开启下一轮。

## 6. 消息、恢复与关闭

### 消息事实

- 父→子仅在实际 admission 后发事件；排队、admitted、消费/完成不是同一状态。
- 子→父展示完整 assistant MessageEnd 的对外正文，不能把 thinking/tool result/partial 当作完成回信。
  带 tool calls 的 assistant 正文应标为过程消息，不标成 child 完成。
- 两方向都带原 owner、child ID、稳定 message ID、状态；错误/取消内容可见但不伪装成功。
- Coding 主聊天区展示，较长内容用明确 preview + 完整详情；退订只影响显示，不取消 child。
- 事件不隐式写入父模型对话；父通过 SDK wait/inspect 或显式接收取得结果，避免重复注入。

### 恢复

- 保留完整结构化 tool pairs、输入 receipt、图片 identity、压缩前归档与不确定状态。
- 持久记录中父 ID 是审计 metadata，不是可反序列化的授权句柄；凭据/callable/锁/loop 不进记录。
- 重启后记录可读，运行中任务标 interrupted；不自动重放未确认的工具。
- 要继续执行必须由当前宿主重新绑定真实父句柄、重新授权并显式 admission；不自动接管旧父权限。
- Claw 继续一 group 一个逻辑主 Agent，不新增 branch 切换或以历史 session ID 路由 Goal/事件。
- 现有记录尽量保留读取，旧 `inherit_context=true` 只展示历史；不能把旧快照当新请求执行。

## 7. 实施顺序与验收

1. 固定 SDK/host Protocol、真实父句柄和纯数据协议；拆清 `ChildPort` 对具体 Supervisor/私有字段的依赖。
2. 两宿主接管 runtime、存储协调和生命周期；先迁一个再以相同契约迁另一个，非复制整个 Supervisor。
3. 接入工具/skill 子集、实时撤权与审批版本检查；修复父状态/transform/tool_ctx 泄漏。
4. 接入独立 system/首轮交接、旧 instructions 明确转换及双向消息 UI。
5. 移除 Agent 的旧 child runtime 实现与宿主对其线程私有字段的依赖，检查所有入口包括 print/SDK/ACP/native。
6. 定向安全/并发/故障测试，再完整 Linux 回归、Ruff、strict mypy、Flow examples、构建与适用冷安装验证。

必须新增的验证：

- 创建和变更时 tools/skills 越界整体拒绝，无 Agent/model/worker 先启动的副作用。
- 无 active selection 时冻结上限；同名工具替换、扩展迟注册、父撤权及审批期间撤权不能扩权。
- 父关闭/替换后旧句柄拒绝；cancel/清理仍可达；两个 child 审批互不污染。
- 父 prompt/history/private marker/父 transforms 不进入 child；任务材料实际进入首条 user。
- skill list/load/catalog/激活均为选定子集；写操作拒绝；不修改父激活状态。
- 父 turn 结束后 child 仍可推进；父 wait/child approval/cancel 并发无死锁；UI 断连不取消任务。
- 真实进程重启后只恢复证据，不自动模型调用/工具重放；存储写失败和取消后状态不虚报成功。
- 保留图片两阶段取回、防 stale receipt、完整 paired history 和 compaction archive-failure 测试。
- Agent SDK import/construct/list 不自动起 child 线程/loop；两个宿主关闭后其 child task/worker/writer 真正退出。

SA-01～SA-10 共 10 项，准确状态以需求清单为准；本文历史审计及部分旧测试通过不计作需求完成。
本轮不 commit/push/tag、不重启用户服务，不改动用户 docs/agent-ui-features.md 和 workflows/。
