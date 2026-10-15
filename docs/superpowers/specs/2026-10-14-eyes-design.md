# XDOG Eyes：声明式观察与会话事件投递

> Superseded for transport, binding and scheduling by the approved ACP/session and endpoint/inbox designs dated 2026-10-15. This is a historical design, not the shipped host protocol; see `docs/acp-session-routing.md`.


日期：2026-10-14
状态：用户已批准，按关联实施计划实现。本文不是实现计划。

## 1. 意图与成功标准

用户希望增加一个独立包，作为现有 agent harness 的“眼睛”：从定义的来源采集证据，将值得关注的变化转换成事件，经路由交给处理器，进而唤醒 coding、claw 或 flow。

第一版的具体闭环是：文件变化通知一个正在运行的 coding 会话。coding agent 通过随包提供的内置 skill 编写观察定义、注册 source 和 handler；coding harness 本身不知道文件 source 的类型或实现，只提供通用外部事件接收能力。

已经确认的边界：

- 新包采用类似 flow 的“声明式定义 + CLI/runtime + 内置 skill”产品形态。
- source、detector、handler、route 的语义属于 eyes，而不是 coding。
- 活动注册是会话级的。coding 退出后不继续观察，也不重新启动 coding。
- 事件排队、合并，在 coding 空闲时处理，不打断当前回合。
- eyes 不执行 agent 工具循环，不授予权限，不拥有 coding 的会话历史。
- 定义文件可以保留和复用，但不代表活动注册可以持久恢复。

成功标准：用户要求观察某些文件后，agent 能借助 skill 创建并校验定义，经现有权限流程启动观察；外部文件内容发生变化时，eyes 提供可追溯的事件，coding 使用正常会话路径处理；重复保存不会造成唤醒风暴，agent 自己修改文件不会导致无限循环，会话退出不会留下继续投递的观察进程。

## 2. 当前代码依据

相关现有边界：

- `packages/flow/src/xdog/flow/skills/flow-workflows/SKILL.md`：flow 通过 skill 教 agent 编写定义并使用 CLI，而不是把工作流构建逻辑写进 coding。
- `packages/agent/src/xdog/agent/skills/discovery.py`：从安装的 `xdog.*` 包发现 `skills/<slug>/SKILL.md`，可供 eyes 复用。
- `packages/coding/src/xdog/coding/core/agent_session.py`：coding 会话拥有权限、扩展、子 agent、持久化和资源清理。
- `packages/coding/src/xdog/coding/modes/rpc/rpc_mode.py`：现有 RPC 使用 stdin 控制流，不是独立 CLI 可以连接的会话服务端。
- `packages/coding/src/xdog/coding/core/context_messages.py`：已有消息接纳与上下文所有权边界，可以在实现时评估复用，但它本身不等于自动唤醒入口。
- `packages/agent/src/xdog/agent/agent.py`：已有 follow-up 和输入队列，但不能绕过 coding 的正常回合准备直接调用底层 agent。
- claw 已有定时任务，flow 已有 HTTP/文件 hook；第一版不迁移这些功能。

## 3. 方案选择与总体结构

选用独立的 eyes runtime 与通用宿主事件入口。未选用把 `FileSource` 实例注册到 coding 内部的方案，因为它会让 coding 理解 source 类型；也不在第一版构建可以自动启动 harness 的持久 daemon。

```text
用户意图
  → coding agent 加载 eyes-observers skill
  → 编写 observer.json
  → xdog-eyes validate / start
  → eyes runtime（独立进程，绑定宿主上下文）
      sources → evidence → detectors → events → routes → handlers
  → 本地通用事件入口
  → coding 待处理事件
  → 正常会话回合与 UI 输出
```

包：`packages/eyes`，发行名 `xdog-eyes`，命名空间 `xdog.eyes`，CLI `xdog-eyes`，skill 名 `eyes-observers`。

eyes 核心只依赖自身模型和本地运行能力，不依赖 coding、claw、flow 或 LLM。coding 不导入 eyes，也不注册具体 source。工作区 umbrella 安装 eyes，使随包 skill 和 CLI 可用。未来 harness 可以实现相同的宿主协议，不要求重写 eyes。

第一版支持 coding 的交互模式和 RPC 模式。单次 print 模式不会为结束后的进程保留监控；未提供通用事件能力的宿主会导致 `start` 明确失败。第一版以本地 Unix socket 为传输，不承诺 Windows 支持；协议模型不绑定 Unix，以便以后增加其他传输。

## 4. 声明式定义与注册模型

定义采用版本化 JSON。校验在启动任何观察之前完成：标识唯一，引用存在，参数类型正确，未知类型和未知字段被拒绝。文件 source 根目录相对定义文件解析，不依赖 CLI 当前目录；项目之外的观察范围必须显式填写，仍受启动权限控制。

示例是第一版应支持的格式：

```json
{
  "version": 1,
  "name": "source-test-review",
  "sources": [
    {
      "id": "source-files",
      "type": "file",
      "root": ".",
      "include": ["src/**"],
      "exclude": ["**/__pycache__/**"],
      "poll_interval_ms": 1000
    }
  ],
  "detectors": [
    {
      "id": "file-changes",
      "type": "file.change",
      "source": "source-files",
      "debounce_ms": 500,
      "max_batch_delay_ms": 2000
    }
  ],
  "handlers": [
    {
      "id": "review-tests",
      "type": "session.notify",
      "target": "current",
      "instruction": "检查变化的源码是否需要更新相关测试；遵循当前会话权限。"
    }
  ],
  "routes": [
    {
      "id": "review-source-changes",
      "detector": "file-changes",
      "event_types": ["file.created", "file.modified", "file.deleted"],
      "handler": "review-tests"
    }
  ]
}
```

source、handler 分别注册，route 表达订阅关系。启动定义时 runtime 创建活动注册标识；标识只在该 runtime 和宿主所有权期间有效。多个 route 可以引用同一个 source，采集只执行一次；同一事件可以匹配多个 route，每个 route 有独立的投递标识。

第一版不支持任意 Python 回调、脚本 handler、动态表达式或远端地址。`session.notify` 的 `instruction` 是经用户授权启动的定义中的处理要求；事件载荷是证据数据，不能覆盖该要求、宿主身份或权限。

## 5. CLI 与内置 skill

CLI 命令：

- `xdog-eyes validate <definition>`：校验定义；不启动采集、不联系模型。
- `xdog-eyes start <definition>`：从宿主上下文获得投递能力，启动或连接当前宿主的 eyes runtime，注册定义，返回活动注册标识。
- `xdog-eyes status [registration-id]`：显示观察状态、基线进度、最近采集错误、投递状态和宿主暂停原因。
- `xdog-eyes stop <registration-id>`：停止该注册，不再生成新投递。
- `xdog-eyes resume <registration-id>`：请求宿主重新授权自动处理，不能自行清除权限限制；返回接受或拒绝状态。

同一宿主中重复 `start` 相同名称和相同规范化定义是幂等操作，返回现有标识；同名不同定义拒绝，需先停止再重新注册。不同会话不共享活动注册。

skill 随包存放在 `xdog/eyes/skills/eyes-observers/SKILL.md`，使用现有 packaged-skill 发现机制。它说明如何将用户意图转换为定义、选择小范围匹配、校验并请求启动、检查事件和停止监控，以及证据与指令的区别。

加载 skill 不启动观察。编写定义和启动/恢复监控仍经过宿主原有的文件写入与命令执行权限流程。不得因为命令名是 `xdog-eyes` 就自动授予 `allow-all`。

skill 的提示词放置遵守现有 session/turn scope 机制，不添加额外的全局提示词拼接路径。

## 6. 通用宿主事件协议

coding 实现与 source 无关的本地事件服务。它只理解所有权、事件封装、接纳状态和执行调度。

宿主为当前上下文提供私有能力描述，通过工具子进程环境中的 `XDOG_EVENT_CONTEXT` 指向该描述文件。描述包含协议版本、本地地址和授权能力；不得包含模型凭据。目录仅当前用户可访问，能力文件权限为 `0600`，socket 不对网络开放。CLI 不在输出、日志或定义文件中打印授权能力。

能力绑定 `(host-instance, session-id, branch-id, generation)`。上下文切换或能力撤销后，旧 generation 不再接纳事件；切回同名会话也不会复活旧能力。工具子进程环境获得能力不代表 source 数据能够获得或修改它。

协议版本 1 提供握手、注册/注销投递关系、存活检查、事件投递和状态查询。注册请求只提交 route 标识、handler 处理要求和活动注册所有权，不提交 source 实现；宿主保存这些已接纳的处理要求，后续事件不能替换它们。注销使未执行输入失效。注册全部成功后才启动采集，失败时回滚已经建立的关系。事件至少含：

- 协议版本、事件标识、活动注册标识、route 标识；
- source 标识、事件类型、观察时间；
- evidence 列表和宿主无关的结构化载荷；
- 经注册确认的 handler 处理要求。

宿主不根据 event type 实现文件逻辑；所有载荷按不可信外部证据展示。保留可见的外部事件来源，不能伪装成用户亲自发送的消息。

投递响应区分 `accepted`、`duplicate`、`busy`、`paused`、`stale-owner`、`rejected`。`accepted` 表示宿主已接纳，不表示模型处理成功；`duplicate` 表示同一投递已接纳。接纳后的处理状态另外显示为等待、运行、成功、失败、取消或丢弃。

## 7. 文件 source 与证据语义

第一版采用异步调度的轮询实现，不引入平台 watcher 依赖。目录遍历和文件散列在受控后台线程中执行，不能阻塞 agent 事件循环；同一 source 不重叠扫描。

- 第一次完整扫描建立基线，不生成已有文件的新增事件。`start` 注册后，状态区分 `baselining` 和 `watching`；只有后者开始对基线之后可观察的变化负责。
- 匹配使用 root 相对的 POSIX 路径。`*` 匹配一个路径段内的字符，`?` 匹配单个字符，`**` 匹配零个或多个路径段；include 后应用 exclude，exclude 优先。第一版不支持字符类和否定模式，校验时拒绝它们。
- 只观察普通文件，不跟随文件或目录符号链接，不读取 socket、设备或 FIFO。散列文件时再次检查类型，降低扫描期间类型替换的风险；这不是敌对代码沙箱。
- 通过文件内容散列判断修改；单纯 mtime 变化或相同内容重复保存不产生修改事件。mtime/大小可作为扫描优化，但不能作为唯一变化依据。
- create/delete 根据前后快照判定；rename 表达为旧路径删除、新路径新增，不猜测重命名身份。
- 轮询不能保证捕获两个扫描之间发生并消失的所有变化。skill 和 CLI 明确说明该限制。
- 每条 evidence 含 source、相对路径、前后存在状态、可用的前后内容散列、大小和观察时间。默认不存储完整内容或差异；agent 后续读取的是当前文件，不是历史快照，不得声称能够恢复原始内容。
- 扫描期间不稳定的文件延后重试；读取失败不是删除。root 不可访问时报告采集错误，保留最后可信基线，不制造批量删除。明确观察到 root 不存在时，同样暂停并报错，不自动把整棵树当作删除。
- 同一合并窗口内，路径从窗口初态到终态进行净变化判定：新增后删除不产生事件，修改后恢复原内容不产生事件，已存在文件删除后以不同内容重建归为修改。
- 静默达到 debounce 或达到 max batch delay 时发出待合并变化，避免持续写入永远阻止事件生成。每个事件内的路径具有相同事件类型；同一窗口的新增、修改、删除分别生成事件，route 的待处理项可以同时引用这些事件。

文件 source 必须有资源上限：第一版默认最多扫描 10,000 个匹配文件，单文件最多散列 10 MiB；超限时进入明确的错误状态，不悄悄把遗漏当成删除。source 配置允许显式调整这两个上限，并校验其为正数。取消扫描需要等待已开始的线程工作安全退出后再释放资源。

## 8. coding 接纳与空闲调度

事件由模式层的通用输入调度协调，不能在 socket 回调中直接调用 `Agent.prompt()`。

- 人类输入优先，当前回合不被自动事件打断。
- 模型流结束不一定代表空闲；子任务、权限操作、压缩、上下文导航、扩展控制和清理仍可能拥有会话。使用现有所有权边界接纳新回合，不仅检查 `is_streaming`。
- 空闲后，把 handler 的处理要求和带来源标签的证据送入正常 coding 会话路径；提示词重建、压缩、工具权限、扩展钩子和历史持久化照常生效。
- 同一 `(registration, route)` 的未执行文件事件可合并，但保留组成事件标识以支持去重；不同 handler 的处理要求不能混合。
- TUI 显示观察活动、待处理变化和暂停原因；RPC 提供对应结构化状态和事件。
- 事件默认不自动进入模型历史；只有正常回合接纳时才记录为可追溯的外部输入。
- 停止注册后，尚未执行的关联输入取消；已经开始的回合不强制中断。会话切换或关闭使旧输入失效，不能转投新会话。

## 9. 防循环、队列与失败处理

不能从文件 watcher 可靠判断写入来自用户还是 agent，因此第一版不把“执行期间忽略全部变化”当成自触发保护。

采用保守的宿主自动唤醒预算：每个 coding 上下文在一次明确人类输入或获准 resume 后，最多启动一次外部事件回合。启动时消耗预算；成功、失败或取消都不自动补充。达到上限后，继续观察和合并证据，但暂停进一步自动回合并显示原因。agent 自己调用 resume 不会被当成人类输入；resume 需要宿主现有权限流程明确接受。

这是明确的第一版折中：新的真实外部变化也可能需要用户恢复处理，但保证自修改不会无限消耗模型。未来可以加入工具写入的精确来源记录；第一版不以不可靠的时间窗口推断来源。

- 队列在内存中，进程崩溃后不恢复，也不承诺持久投递。
- 默认最多保留 128 个 pending route 项、每项 256 个路径，单次协议消息最多 1 MiB。
- 优先合并同一 route 的变化。无法合并且超限时拒绝接纳，明确报告 overflow，不静默丢弃；runtime 保留可承载的待投递批次并暂停该注册，用户可停止或重新建立基线。
- 临时连接错误和 busy 响应重试原投递标识，指数退避从 250 ms 到 5 s；不无限创建新事件标识。paused 不进行高频重试，等待宿主状态变化。
- 宿主维持当前 generation 内的接纳去重记录，最多 4,096 条；达到容量时暂停新投递，不能淘汰记录后重复执行旧事件。切换上下文或显式撤销并重新建立 generation 才清空记录，旧能力同时失效。
- stale-owner 或授权失效属于终止条件；拒绝格式不合法的事件并记录错误，不进入模型。
- 第一版不承诺外部副作用恰好一次，也不在回合失败后自动再次运行模型。

## 10. 进程生命周期与本地安全

每个有效宿主上下文至多一个 eyes runtime，多个注册共享它。CLI 控制入口同样只接受该上下文能力。启动握手与私有控制 socket 必须避免并发 start 产生多个 runtime。

正常 stop 注销指定注册；最后一个注册停止后 runtime 退出。宿主正常关闭或上下文切换时撤销能力并通知客户端。runtime 每 5 秒检查宿主存活，连续 15 秒不能确认所有权后停止采集和投递并退出，覆盖宿主异常死亡；退出期间不等待无限重试。

coding 不需要知道 runtime 是 eyes，也不负责启动 eyes：它只管理自己的通用事件服务和连接。eyes 自行管理观察进程。所有异步任务、socket、线程工作和子进程必须有明确取消/等待路径，禁止依赖 fire-and-forget 保证清理。

本地能力是应用授权边界，不是同 UID 恶意进程之间的安全隔离；权限 gate 也不是 OS 沙箱。source 数据不能选择宿主地址、注入 shell 命令或覆盖能力。第一版没有网络监听、自动权限升级或任意脚本执行。

## 11. 不在第一版范围内

- API、网页、仓库服务、日志流等其他 source。
- claw 和 flow 的投递适配器；保留协议扩展位置，但不声称它们已可用。
- 关闭 coding 后继续观察、自动启动 coding、持久后台服务或崩溃恢复。
- LLM 检测器、任意代码 detector/handler、动态生成 shell 命令。
- 原生文件事件 watcher、历史完整文件内容保存、可靠的写入者来源识别。
- 迁移现有 claw scheduler 或 flow hook，或增加第二套 agent 执行循环。
- 默认无人审批修改，或通过 skill 绕过 coding 的 fail-closed 权限行为。

## 12. 验收与测试要求

所有核心测试使用假时钟、临时目录、可注入扫描器/传输和假宿主，不依赖真实模型或外网。

1. 定义校验覆盖未知字段、重复标识、缺失引用、非法模式、错误参数和不支持的 source/handler。
2. source 覆盖初始基线、内容变化、相同内容保存、删除、rename、净变化合并、持续写入的最大延迟、符号链接、读取失败、root 失效和资源上限。
3. router 覆盖多订阅共享 source、事件类型过滤、一对多路由、幂等注册和停止。
4. 传输覆盖无能力启动失败、能力泄露防护、错误身份、旧 generation、重复投递、ACK 丢失、busy、paused、容量溢出与重试取消。
5. coding 覆盖空闲唤醒、忙时排队、人类输入优先、完整生命周期互斥、正常提示词准备/持久化、TUI/RPC 状态、会话/分支切换和取消旧事件。
6. 防循环测试让自动事件回合修改被观察文件：只启动预算允许的一次回合，后续变化仍可见但不自动再次执行；人类恢复后可再次处理。
7. 清理覆盖最后注册停止、宿主正常退出、异常死亡、并发启动、扫描线程仍在运行以及 socket 断开；不残留可继续工作的孤儿观察任务。
8. 权限回归覆盖启动/恢复需原有审批、外部事件不能更改允许工具、没有自动 allow-all。
9. 包集成覆盖 workspace 安装、CLI 入口、wheel 中的 skill、现有 packaged-skill 发现、独立安装不引入 harness 依赖。
10. 端到端测试：通过 skill 所说明的 CLI 注册临时项目观察，用假 coding 回合处理文件变化，确认接纳和处理状态不同、普通回合完成后仍继续观察。

验证需运行 eyes 测试、受影响 coding/agent 测试、Ruff、严格类型检查和发行包检查。实现计划再将这些验收项映射到具体文件与命令。

## 13. 下一阶段

本文经用户审阅批准后，再编写实现计划，分解为 eyes 定义/采集/路由、通用宿主协议、coding 接纳调度、CLI/skill/包集成与验证。批准讨论范围不等于批准尚未编写的实现计划；本阶段不修改产品代码。
