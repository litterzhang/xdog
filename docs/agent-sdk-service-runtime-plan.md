# xdog-agent 后台服务运行时：功能 Review 与实施计划

日期：**2026-10-12**。状态：**本轮实施与当前Linux验收完成；准备随2.4.0发行**。

本文覆盖跨进程恢复、compaction、宿主工具注册、运行事件和关闭，以及正在进行的skill实现。
此前设计中的持久化事件/ACK协议已按用户最新要求移除；以下内容取代旧方案中的对应设计。

## 1. 已确认的范围

- **不支持持久化事件**：不新增持久化事件sink、`on_checkpoint`落盘屏障、工具执行前的
  持久化意图hook、事件日志重放或最终提交ACK协议。
- **工具部分仅支持注册及现有调用机制**：不新增业务提交/校验驱动的Agent行为、终止工具协议、
  结构化RunResult或统一错误分类；现有工具、错误处理和权限检查保持兼容。
- **保留dump/restore**：SDK导出和恢复数据，宿主自行选择何时保存、保存在哪里。
- **Compaction复用Coding现有策略，只有一种内置实现，自动触发默认关闭**；
  手动和显式开启的自动模式共用实现，不另设计策略。
- 保留结构化观察事件和内存状态一致性边界；事件送达不表示已保存，不能保证进程崩溃后可恢复
  到最后一次事件。事件也不能作为数据库事务提交点。
- 不增加业务领域逻辑、不重启服务、不发布版本；按本清单实施与验证，撤销项不重新引入。

## 2. 准备支持的功能

以下六组是review视图，不与验收清单重复计数。保留已有F编号；原F5错误分类已撤销，F4收敛为工具注册。

### F1. Skill加载与生命周期

- 宿主显式配置SkillManager、加载来源和激活的skills；不让快照自行发现或安装代码。
- 提供可显式注册的只读`skill`工具，支持`list`和`load`；支持限制可见slug和读取大小。
- `load`返回skill内容，不执行其中命令，不自动激活，不授予新工具权限。
- session scope进入稳定system prefix；turn scope在实际进入模型调用的本次逻辑run中有效。
- 未进入模型请求的失败不消耗pending turn skill；已消费的turn skill不带入新任务。
- 支持已打包的`flow-workflows`（来自xdog-flow），不让agent反向依赖flow。
- dump/restore保留激活状态、scope和内容版本；恢复不重复拼接system/skill前缀。

状态：显式SDK加载已有基础；只读工具和生命周期调整有未提交WIP，尚未完成最终验收。
跨进程skill状态恢复属于后续工作。详细历史见[Skill调查](agent-sdk-skills-investigation.md)。

### F2. 完整的跨进程会话dump/restore

支持：

- 结构化消息：text/image/thinking、tool-call及tool-result关联，不只保存文本transcript。
- 原始system instructions、模型逻辑ID、可序列化生成参数及运行设置白名单。
- Skill状态、compaction摘要/边界/代次，以及恢复所需的工作上下文。
- 快照格式版本、数据校验及已有旧格式的明确兼容规则。
- session/run关联信息；不新增业务submission状态或结构化运行结果字段。
- 已知未完成工具的恢复状态；不完整的历史不得直接作为合法模型上下文继续运行。

不保存provider客户端、认证配置、工具callable、网络连接、锁、事件循环和后台任务。
普通消息可能含业务敏感数据，保存和访问控制由宿主负责。

恢复顺序：**校验纯数据 → 宿主绑定可信provider/tools/skills/权限 → 重建状态 → 等待显式运行**。
恢复本身不调用模型、不执行工具、不加载快照指定的任意模块、不恢复旧审批或扩大权限。
Skill内容版本不匹配必须显式处理，不能悄悄套用同名新指令。

首版继续使用现有消息codec支持的内联数据；不新增对象存储/blob下载框架。

### F3. 一种内置Compaction

**用户已指定：采用Coding现有策略，不另起一套算法。** 提取其通用部分到`xdog.agent`，
Coding通过兼容封装复用；不能让`xdog.agent`反向import `xdog.coding`。
不做策略注册表、插件框架或外部摘要算法注入。

已核对的策略来源：

- `coding/core/compaction/compaction.py`：`prepare_index`、`compact_messages`、摘要内容投影。
- `coding/core/compaction/utils.py`：历史token估算和从后向前选择近期保留窗口。
- `coding/core/defaults.py`：自动触发比例0.80、近期保留窗口目标比例0.50。
  0.50是保留窗口的选择目标，不是“摘要后总输入一定低于50%”的保证。
- `coding/core/prompt_templates.py`及现有摘要system prompt：复用摘要的内容要求与包装语义，
  不把摘要伪装为新的用户业务提交；SDK快照需能识别摘要及其边界。
- `agent/summarization.py`：复用当前adapter的无工具摘要调用、额外摘要要求、失败及清理检查。

沿用行为：摘要较早历史、保留近期历史；默认比例以Coding为基线，最终受实际输入预算和
输出预留约束；手动force沿用“保留目标不超过历史估算一半”的选择方式。摘要覆盖任务状态、
决定、关键改动/材料路径、未解决问题及下一步。

**为满足已确认需求，只做必要的SDK适配：**

- Coding当前`retained_turn_start()`保留完整最近user turn；单个user turn包含长工具循环时，
  可能没有可用切点。SDK需允许在已完成的工具批次之间切分，保留任务目标/有效skills及完整
  tool-call/result单元。这是同一策略的安全边界扩展，不增加第二种策略。
- 现有粗略字符估算不能当作精确计数；补齐请求开销、可信模型容量与预留空间的预算检查。
- Coding的session保存、恢复branch、扩展策略覆盖留在产品层，不搬入SDK公共压缩接口；
  SDK只更新一致内存状态并提供原文导出，不要求持久化ACK。
- 提取时保留Coding现有接口与产品行为的兼容测试，不直接改动其已有持久化策略。

入口（名称为建议，非已经实现的API）：

- `await agent.compact(...)`：宿主手动调用，不隐式打开后续自动压缩。
- 显式开启auto-compaction：每次主模型请求前检查，包括同一次prompt中的连续工具轮次。
- 默认关闭auto-compaction：不偷偷摘要、不截断历史、不新增固定轮数上限。
- restore不自动调用摘要模型；自动压缩是否开启由当前宿主显式配置。

正确性要求：

1. 使用当前模型实际上下文容量，或宿主显式提供的容量，以及输出预留/安全余量。
   不把默认200000当作所有模型容量；容量未知时不能猜测并启用自动压缩。
2. 计算输入时包含system、有效skills、工具schema和消息等开销；估算需明确为估算。
3. 宿主可追加摘要要求，如任务目标、失败原因、待办、材料引用，但不是替换内置策略。
4. 只在工具批次完成的边界压缩；工具调用与结果成对保留，必要system/skills继续有效。
5. 先生成并验证候选，再同步替换SDK状态和loop上下文；失败不修改原历史并明确报告。
6. 压缩后可dump并在新进程恢复。摘要模型不获得业务工具权限，用量单独计入run统计。
7. 保留原文导出能力：手动调用的结果提供被压缩历史；自动模式通过结构化压缩事件提供
   原文归档数据。宿主可以保存，但SDK不等待持久化ACK、不保证归档已落盘。
8. 已知无法容纳必要上下文，或压缩没有足够进展时明确报告容量/压缩失败，不循环摘要或发送已知超限请求。

并发手动压缩首版拒绝：不允许在活跃模型流或工具批次中抢改历史；自动模式在内部安全边界执行。
原文交付不能导致SDK永久累计全量归档；未消费事件的背压、取消和资源释放需验证。

### F4. 宿主工具注册（不新增业务控制协议）

- 复用已有工具注册接口，由宿主提供工具名称、描述、参数schema及callable实现。
- 注册的工具通过现有SDK工具循环调用，结果按现有工具消息机制返回。
- 宿主可以注册`report_progress`、`submit_init`、`submit_recipe`等任意名称；
  SDK不按名称推断提交、校验、阻塞或结束行为。
- 工具业务逻辑、校验及保存由宿主实现；不新增“拒绝必须继续修改”或“接受必须立即结束”的协议。
- 不新增terminal工具标记、独占批次、提交状态、RunResult或六类结束原因。
- 不新增fatal/recoverable错误体系；保留现有错误、权限及工具调度行为，不借范围收敛移除安全检查。
- 跨进程恢复时工具callable由宿主重新注册，不从快照反序列化可执行对象。

原F5“明确的错误分类”已按用户要求撤销，不另列功能组。
已有`submit_result`及Flow适配不删除、不改变其语义，也不把此前提交协议方案藏入兼容层。

### F6. 结构化观察事件与一致快照边界（不是持久化事件）

支持异步消费的run/turn开始和结束、tool开始/进度/结束、compaction开始/完成/失败、usage事件。
事件附run关联标识；主模型与摘要用量区分。现有UI订阅方式保持兼容。

- 保持现有事件/结果接口兼容，观察消费不吞结束通知；不新增结构化RunResult或结果协议。
- 明确批次安全边界：SDK及loop消息已更新，全部相关工具/finalizers已结算，关联完整。
- 若在该边界提供快照，提供冻结的数据副本；不能让消费者稍后从可变Agent读取另一时刻的状态。
- `dump()`在idle允许；忙碌时不能随意导出半个工具批次作为“可继续快照”。首版拒绝不安全调用，
  必要的中断恢复导出显式标记不可直接继续。
- 观察事件消费可有背压，但**不等待宿主落盘确认**。不新增保存失败即停止的checkpoint hook。
- 取消/关闭不能被无人消费的事件队列永久卡住；事件缓冲不无限增长。

这保留“状态一致”的安全点语义，移除旧方案“每个安全点保存成功后才执行下一步”的语义。
若宿主在观察回调里写文件失败，SDK不因此提供自动停机保证；宿主自行处理并主动取消。

### F7. 可取消、可彻底关闭的运行时

- 新增`await agent.aclose()`和异步context manager，复用现有取消/等待能力。
- 开始关闭即禁止新请求，停止模型流，向正在执行的工具传递取消。
- 等待SDK创建的producer、工具和后台任务退出，清理自身资源。
- 只关闭SDK所有或宿主明确移交的资源；共享provider客户端仍由宿主负责。
- 重复/并发关闭幂等；关闭完成后不再调用模型或工具，不复活旧Agent。
- 中断工具区分已完成、未开始、取消及结果未知；恢复不自动重放未确认的有副作用操作。

边界：SDK不能安全强杀任意吞掉取消的Python callable或不可中断的native线程。
不配合的工具必须明确报告未完全关闭；需要硬隔离时由宿主管理worker，本轮不建设worker平台。

## 3. 不支持持久化事件之后的恢复边界

正常目标仍是：

```text
Process A: create → init → dump → 宿主保存 → aclose
Process B: 宿主读取 → restore → build → dump → 宿主保存 → aclose
Process C: 宿主读取 → restore → feedback / repair
```

SDK可以准确恢复**实际保存的快照**，但无法知道保存后、进程崩溃前发生的所有外部副作用。
快照若带未确认操作，则恢复后阻止隐式继续/重放，交给宿主对账；旧快照根本未记录的新操作，
SDK不能凭空还原为unknown，也不能保证模型不会再次提出语义相同的业务操作。

因此不承诺任意SIGKILL点自动续跑、exactly-once或零数据丢失。业务工具的幂等键、数据库事务、
外部操作对账由宿主实现。SDK不自动重放记录中的工具，但这不能替代宿主的业务级去重。

无需在SDK引入执行租约、owner_epoch、CAS写盘、WAL/outbox、事件日志数据库或事务协调器。
保留session/run/tool关联和状态版本即可，不建立分布式持久化协议。

## 4. 实施顺序

| 阶段 | 内容 | 验收重点 |
| --- | --- | --- |
| P0 | 固定工具注册、快照、事件和关闭的接口边界 | 不新增业务提交或错误分类协议 |
| P1 | 完成skill WIP、生命周期及宿主工具注册示例/验证 | skill有效期正确，自定义工具按现有机制调用 |
| P2 | aclose、取消与资源所有权 | 关闭后无新调用，未结工具不谎报完成 |
| P3 | 版本化dump/restore及skill/工具恢复元数据 | 真实多进程恢复、无隐式执行或提权 |
| P4 | 提取Coding策略为SDK内置compaction、显式auto模式、观察事件和原文导出 | 单run多次压缩、失败不丢历史、快照可恢复 |
| P5 | Coding/Claw/Flow兼容及端到端验证 | 不另造工具loop，不双重注入或压缩 |

不自动更改Coding/Claw现有持久化策略，不因SDK默认关闭auto而擅自关闭产品已有功能。
适配层优先保证兼容；产品全面迁移其存储方案不属于本轮。

## 5. 去重验收清单

保留原SR编号，避免把撤销项改号或冒充完成；SR-19对应工具注册。将goal已经包含、此前另列
的F1本地加载/只读工具/单run生命周期补齐为SR-20至SR-22；不增加新范围，不与SR-03跨进程恢复重复。
**共22个编号：17项已完成，5项撤销（SR-09至SR-12、SR-14）；本轮剩余0项。**
当前需求分类：待实现0、待验证0、缺外部环境0、已完成17、已撤销5。原生Windows/macOS验证
作为用户明确排除的验证维度延期，不混入本轮需求数量；可选外部集成测试的跳过情况见最终验收记录。

| ID | 可验证要求 | 状态 |
| --- | --- | --- |
| SR-01 | 新进程恢复结构化消息、图像、thinking/tool关联；版本/未知codec/损坏数据明确检查。 | 已完成 |
| SR-02 | 快照不导入代码、换凭据、提权、扩大工具范围或自动调用模型。 | 已完成 |
| SR-03 | session/turn skill及compaction状态跨进程恢复；prefix不重复，已消费skill不复活。 | 已完成 |
| SR-04 | 快照中的未结工具可识别和关联；unknown阻止隐式继续或自动重放。 | 已完成 |
| SR-05 | 使用实际模型容量、输出预留和完整输入估算；未知容量不假定200k。 | 已完成 |
| SR-06 | 默认不自动压缩；手动不启用auto；显式开启后单prompt内多次压缩；两入口复用Coding策略，含长工具循环安全切点及兼容回归，保留关联/必要指令并接受额外摘要要求。 | 已完成 |
| SR-07 | 摘要/候选验证失败原历史不变；原文可导出但无持久化ACK；新进程恢复有效压缩快照。 | 已完成 |
| SR-08 | 超过常见固定轮数阈值仍可运行；不新增默认round/总token上限。 | 已完成 |
| SR-09 | 原“校验拒绝后继续修复，接受后立即结束且无额外模型调用”要求；不再实现。 | 已撤销：用户排除工具驱动的Agent行为 |
| SR-10 | 原终止工具独占/混合批次拒绝及接受后的执行屏障；不再实现。普通工具清理仍由SR-16覆盖。 | 已撤销：用户排除工具终止协议 |
| SR-11 | 原六类结束原因、结构化RunResult及submission状态隔离；不再实现。 | 已撤销：用户排除运行结果与错误分类 |
| SR-12 | 原新增fatal/recoverable宿主错误分类及穿透协议；不再实现，保留现有安全检查。 | 已撤销：用户排除运行结果与错误分类 |
| SR-13 | 事件含run关联/用量/压缩；保留现有接口，不吞结束通知、不无限积压或卡住关闭；不新增RunResult。 | 已完成 |
| SR-14 | 原“await持久化hook阻止下一步，写失败自动停止”要求；不再实现。 | 已撤销：用户明确排除持久化事件 |
| SR-15 | 安全边界快照与loop一致、工具关联完整；不安全dump被拒绝或明确不可继续，无落盘保证。 | 已完成 |
| SR-16 | 模型流中、工具中、压缩中和重复aclose；资源结算及关闭后无新调用。 | 已完成 |
| SR-17 | 中断测试明确已保存与未保存状态边界；已记录unknown不重放，不承诺任意崩溃点恢复或exactly-once。 | 已完成 |
| SR-18 | 独立进程A→B→C走init→build→feedback/repair宿主任务，包含压缩、重新注册工具及显式保存；不依赖SDK提交协议。 | 已完成 |
| SR-19 | 宿主注册名称/描述/schema/callable后，自定义工具经现有循环调用并返回关联结果；恢复由宿主重绑，不按工具名新增业务控制行为。 | 已完成 |
| SR-20 | 显式SDK skill加载、可运行示例；新构建agent-only产物独立工作，含Flow安装目标发现flow-workflows及12个例子。 | 已完成 |
| SR-21 | 只读skill工具list/load、可见范围/大小约束、安全文件读取及取消；不激活、不执行、不增加权限。 | 已完成 |
| SR-22 | session稳定前缀、turn请求投影及跨工具轮次有效；实际模型入口前失败不消费，使用后退休，忙碌激活拒绝。 | 已完成 |

验证优先使用离线provider和真实Linux子进程；运行相关测试、Ruff、strict mypy及适用构建检查。
不调用真实provider、不重启gateway；原生Windows/macOS验证不作为本轮阻塞。

## 6. 本轮明确不做

- 新增结构化RunResult、统一结束原因/错误分类、业务校验驱动的继续/终止、terminal工具及提交批次协议。

- 持久化事件、checkpoint落盘屏障、工具意图日志、事件重放、分布式租约/事务协议。
- 多种compaction策略、策略插件、自定义摘要算法框架或默认开启自动压缩。
- SDK内置数据库、对象存储/blob下载、业务幂等平台、任意崩溃点自动恢复。
- 招聘/recipe领域工具逻辑、自动重放有副作用操作、强杀任意线程/工具的平台。
- 新的固定轮数限制、额外的业务总token预算框架、OS sandbox或权限扩张。
- 发布、服务重启、无关Claw memory/scheduler功能扩展。

## 7. 现状与证据（历史观察，不代表本轮实现）

此前离线探针：`/tmp/xdog-sdk-service-plan/current-behavior.json`。

- 此前submit_result额外模型调用、stream.result无结构化结果，以及before_tool_call的模拟OSError
  转为工具反馈等探针，仅保留为历史观察；对应新提交/错误协议已撤销，不再作为本轮待修缺口。
- 当前核心循环无固定最大轮数；已有dump/restore、低层await emit、取消/等待和消息边界辅助。
- SDK内部自动压缩、完整快照语义和aclose仍需要按review后的方案补齐。

源码入口：

- `packages/agent/src/xdog/agent/agent.py`、`agent_loop.py`
- `packages/agent/src/xdog/agent/{core,types,events,event_stream}.py`
- `packages/agent/src/xdog/agent/{messages,history,summarization}.py`
- `packages/agent/src/xdog/agent/tools/tool_submit_result.py`、`tool_def.py`
- `packages/flow/src/xdog/flow/runners.py`
- Coding/Claw的`core/agent_session.py`、`core/runtime/session.py`

上述证据是历史观察，不把已有WIP计为验收完成。2026-10-12用户已授权创建实施goal；
创建goal时未改运行时代码、未重新执行历史探针，后续实现按验收清单追加证据。


## 8. 实施预估与产品影响（启动时评估）

已创建持久实施goal，范围为六组功能、14项保留SR验收及F1全部加载/生命周期/打包行为。
初步工程时间估计为**6–10小时**（不作为硬截止或已消耗时间）：实现和定向验证约4–6小时，
产品兼容、完整回归和文档/构建约2–4小时。取消竞态、旧快照兼容或回归故障可能使估计增加；
在首轮相关回归后更新，不能为了满足估时跳过验收。用户未设置本goal的token预算。

| 产品 | 直接影响面 | 兼容要求 |
| --- | --- | --- |
| Coding | 压缩算法提取、skill生命周期、Agent事件/关闭 | 保留现有压缩入口、扩展覆盖、恢复分支及落盘行为；不双重压缩/注入，不改权限默认值 |
| Claw | 共用Agent循环、skill生命周期、取消与缓存session资源所有权 | 不把SDK新快照强制替换JSONL transcript；保留memory flush/现有压缩，不在每轮后永久关闭缓存Agent |
| Flow | SDK runner的dump/restore继承、节点skills及超时/资源释放 | 保留节点配置覆盖继承状态、submit_result sink与输出schema、token统计；外部CLI runner不重写 |

这不是零影响变更，但目标是不要求用户改变现有配置。SDK auto-compaction默认关闭，不能因此
改变产品已有自动压缩设置；不用新协议替换已有结果/错误语义。开发期间不重启运行中的gateway。
最终完整回归、静态检查、适用构建及跨进程测试通过前，不宣称三产品无回归或goal完成。


## 9. 实施检查点：2026-10-12

已按用户授权分派最多三个并行子agent；主会话负责Agent主循环、生命周期及集成，子任务分别
处理快照codec、Coding压缩提取、skills和后续产品兼容验证。工作区保留其他未提交修改。

本地候选已包含：

- `snapshots.py`及公开Agent dump/restore：版本、原始prefix、宿主skill重绑、恢复校验、诊断导出。
- 从Coding提取的`compaction.py`、`CompactionConfig`、手动及显式auto轮内入口。
- `aclose()`、请求流释放、取消清理、关闭后拒绝新调用；共享adapter不关闭。
- 有界观察流、session/run标识、独立结果等待、usage/压缩事件及可选冻结SnapshotEvent。
- 只读skill工具/示例、单run生命周期与版本化跨进程skill状态。
- Flow一次性Agent的finally关闭与继承兼容；生成代码一致性和Coding RPC投影继续验证。

已取得的定向证据（不能替代最终全量门槛）：

| 验收范围 | 源码/测试证据入口 |
| --- | --- |
| SR-01/02/03/04/15/18 | `test_snapshots.py`、`test_agent_snapshot_runtime.py`：公开API三进程、真实Flow skill、active-turn安全快照、默认不重放 |
| SR-05/06/07/08 | `test_sdk_compaction.py`、`test_agent_compaction.py`、`test_sdk_runtime_adversarial.py`：125工具轮次、多次压缩、配对、失败/取消 |
| SR-13/16/17 | `test_agent_event_buffer.py`、`test_agent_close.py`、adversarial测试：满缓冲取消、关闭失败不谎报完成、真实SIGKILL与外部副作用回执 |
| 工具注册（SR-19） | `test_agent_snapshot_runtime.py`、`test_sdk_runner_lifecycle.py`、`examples/service_runtime.py` |
| SR-20/21/22 | `test_skill_tool.py`、`test_skill_lifecycle.py`、`test_skill_sdk_usage.py`、`test_path_confinement.py`及新产物probe（最终产物待重建） |
| 产品兼容 | Coding/Claw `test_sdk_runtime_compatibility.py`；Flow `test_sdk_runner_lifecycle.py`及后续codegen parity |

证据目录：`/tmp/xdog-sdk-runtime-implementation/`，子任务另有
`/tmp/xdog-sdk-skills/f1/`、`/tmp/xdog-flow-sdk-lifecycle/`。
当前公开三进程示例init/build/repair各两次模型调用、一次注册工具调用，build一次手动压缩，
每进程均完全关闭；未调用真实provider。七包strict mypy检查点覆盖552源码通过，之后修改仍需重跑。

此前探索性agent+ai回归为3002 passed/55 skipped/5 failed，其中旧skill生命周期断言及测试在途
修改已处理；不能当作最终绿色回归。一次单包pytest误选包内配置导致16个collection errors，
已改为显式使用根`-c pyproject.toml`重新运行，不改弱测试或忽略失败。

当前仍需完成：最新代码下全量回归及失败定位、RPC/codegen适配复核、新产物构建/隔离probe、
文档/状态最终审计。无服务重启、发布或原生非Linux验证。用法见
[SDK运行时说明](agent-sdk-service-runtime.md)。


## 10. 最终验收（2026-10-12）

**17项保留需求全部完成，5项已撤销需求未重新实现，本轮剩余0项。**

- 完整串行检查点：8459 passed、147 skipped；随后诊断导出修复新增1个回归用例。
- 最新代码全仓分组回归：**8460 passed、147 skipped、0 failed**。三个互斥分组覆盖完整收集的
  **8607个测试**，没有关键词过滤、忽略或遗漏；最后核对包源码/测试/项目配置哈希无变化。
  - AI/Agent/Flow/Site/TUI：5103 passed、55 skipped。
  - Claw：1115 passed、2 skipped。
  - Coding：2242 passed、90 skipped。
- Ruff全仓通过；七包strict mypy通过（552个源码文件）；Flow示例17 passed。
- 离线构建8个发行包、16个产物，核验元数据、依赖、许可证、RECORD、当前源码与skill资源。
- 10个隔离进程probe通过；新wheel的agent-only和with-flow目标均验证公开compact/dump/restore/aclose、
  125轮工具循环、跨进程init/build/repair；with-flow保留13个skill资源（含12个JSON例子）。
- 补齐finalizer故障后的诊断导出：原错误消息保留，未确认工具仍unknown，普通恢复继续拒绝，
  没有把它改成新的错误分类或业务终止协议。
- 9个原有无关dirty文件内容哈希保持不变；未stage/commit/push/tag，未改发行版本、未重启用户gateway。

147个跳过项是已有的可选集成门槛：7个真实Claude/Codex CLI用例，以及140个需要独立固定版本
MCP SDK的用例（部分还要求tmux）。它们**没有通过、不属于本轮新增SDK能力的必需验收**；
没有安装依赖或调用真实provider来补跑。原生Windows/macOS仍按用户授权延期。

生成模块的额外strict-mypy探针仍有11条历史诊断。用HEAD和当前codegen在相同依赖/模板下生成
同一工作流，11条按函数/源码/消息完全匹配，新增0条；七个源包的必需strict-mypy门槛通过。
本轮没有扩展为修复无关生成模板类型问题，也不声称任意生成模块已通过strict mypy。

证据：[机器可读验收清单](agent-sdk-service-runtime-evidence.json)，运行日志与哈希在
`/tmp/xdog-sdk-runtime-implementation/`。构建对应最终代码；本节与最终状态标记是验收后文档更新，
不改变已验证的运行代码。先前章节中的“待验证/候选”叙述保留为实施检查点历史，以本节和验收表为准。


## 11. 2.4.0发行准备

用户在goal完成后另行授权升级版本、提交、推送及tag。此前“未提交/未发布”的验收记录描述
的是goal完成时的历史状态；本轮仅同步发行版本与说明，不改动已验收的功能范围，不重启用户服务。
