# Goal／Eyes 与宿主的代码边界

本轮只重构已有功能归属，不新增 Goal 自动续跑、后台模型轮询或调度器。
验收编号与状态见 `capability-boundary-requirements.json`。

## 依赖与归属

- `xdog.agent.capabilities`：可信已安装入口点发现、能力生命周期、固定父运行时句柄和通用子执行授权桥。没有 Goal／Eyes schema、目标状态或观察策略。
- `xdog.skills.goal.runtime`：GoalStore／GoalService 的实际绑定、worker／reviewer、原始执行引用、通知、工具及只读 GoalDisplay。只消费公开的通用宿主端口，不导入 Coding／Claw，不接收原始会话或应用对象。
- `xdog.eyes.hosting`：观察者模型、schema、游标／immutable outbox、调度、恢复／取消、报告镜像及 `eyes` 工具。只依赖 Eyes 自身和 stdlib；通过注入端口使用宿主 IO／收据，不依赖 Agent 或任一宿主。
- Coding／Claw：控制器、writer lease、权限、真实工具执行、输入／输出回执与渠道目的地；仅提供通用端口和能力装配，没有 Goal／Eyes 专用 adapter 或业务分支。Claw 根包仍然不是公共 SDK。

## 安装与启动

`xdog.capabilities` 入口点提供惰性的 provider 元数据：name、支持的宿主 scope、工具名和通用传输需求。
Goal 支持 session／group；托管 Eyes 支持 application。Coding 中 Eyes 仍通过技能与通用 ACP 使用，不自动新增托管服务。

发现和导入入口点不打开数据库、执行模型、启动观察或 worker；只在真实宿主 owner 上调用 open。已保存数据永远不能指定模块、callable 或扩展权限。

宿主工具白名单保持有效：能力工具从普通 built-in 注册表分离，但不因此绕过配置或审批。无可选包时没有对应入口点，基础聊天不打开业务 journal。

## 宿主句柄

Goal 使用的公开 ParentPort 只包含 SDK Agent、通用 extension runtime、ChildPort，以及固定 owner 的 valid/user_authorized 回调。
应用会话、控制器或应用本身不进入能力包；scope、namespace、workspace、数据根及入队/收据方法由宿主核实。Coding 维持精确 session／branch；Claw 维持 group／workspace，历史引用不成为新的消息路由。

Eyes 使用 detached stdlib JSON 快照与 opaque reply 字符串。宿主验证 workspace／namespace、解释原始回复目的地，并提供 schema-neutral leased SQLite IO。Eyes 拥有 journal schema 和恢复事务，不直接操作 Core receipt 数据库，也不获得渠道对象。

## 生命周期与权限

- 启动由单个 owner task 负责；多个等待者共享。取消一个等待者不取消另一个等待者或已经采纳的宿主能力。
- 宿主关闭会等待真正的 startup，清理晚到 binding；关闭、工具／会话提交和子执行都必须结算后才释放 owner。某个能力关闭失败不能跳过独立的剩余资源清理。
- ParentPort 固定实际 parent；导航后旧句柄不能借新上下文授权。
- 角色工具由真实 Supervisor capture 绑定，按实际工具对象确认。角色名称、工具字符串、消息 provenance 不能伪造权限。子执行只能缩小父工具集合，readonly／审批／MCP／扩展 gates 不改变。
- 装配器不成为第二个 Agent loop，也不因 active Goal 自动轮询模型。

## 输入、输出与恢复

保留现有 Goal journal 路径／owner key、observer v3 格式、原始输入 ID、immutable 内容与 ACK-loss 幂等语义。不读写未知格式，不升级 legacy journal。

观察游标与 outbox 必须先原子提交，再交给原输入路径。执行是否开始／成功仍以 Core 为准。未知或 interrupted 工作不自动重放。已接受 managed time 的恢复与普通 file 不自动恢复的规则保持不变。

每条输出仍由 Core output receipt 管理。Eyes 只读取镜像事实，原 recipient/context/output ID 不改变；停止 source、取消通知和取消真实执行是不同操作。没有原始回复目的地不等于群广播。

## 验收范围

需要真实宿主路径覆盖基础无能力包、Goal-only、Eyes-only、两能力组合、原 parent/context 轮换、取消及关闭、ACK 丢失／崩溃恢复、原收件人输出、只读与工具身份防伪。
最终当前 Linux 九包回归、strict mypy、Ruff、Flow 示例、锁及新构建产物／隔离安装验证缺一不可。历史审计不替代当前源码证据；不声称真实付费 provider、真实微信或非 Linux 原生验证通过。
