# Claw 宿主职责与操作归属重构

## 目标与边界

保留一个 group 一个主 Agent、现有模型工具循环和持久化协议。按资源所有权拆分实现，
不增加调度器、平行状态机或业务层事件总线。已有输入/历史/输出/子 Agent 的记录仍各自权威。

| 事实 | 权威 |
| --- | --- |
| 输入与 attempt 状态 | InputStore / InputService |
| group 操作准入、reservation、上下文变更中的阻挡 | GroupController |
| 当前 AgentSession 与资源 | GroupRuntime |
| 历史提交 | TranscriptStore |
| 子 Agent 执行 | Claw child runtime |
| 渠道投递 | OutputStore / OutputDelivery |
| Goal 生命周期 | xdog.skills.goal |

## 实施计划

1. 审计原入口、取消结算与 owner 校验；补交错回归测试。
2. 在原 GroupController 收口控制操作准入：排队后复核 owner，取消后结算原 scope，
   用实际 scope 计数替换应用层 navigation set，不让一个操作清理掉另一个操作的阻挡。
3. Gateway 只保留进程/endpoint/渠道生命周期；连接协议由独立 NativeConnection 承担，
   显式注入应用、回执读取、输出函数及实际 viewer 集合，不传 Gateway 本身。
4. 迁出模型控制、MCP 控制、上下文准备、session 提示词/工具策略/压缩流程，保留原应用入口，
   新模块仅依赖相关资源。不引入 mixin 或通用 service locator。
5. 验证 Claw 与跨包能力/子 Agent/渠道测试，再执行全仓测试、Ruff 和 strict mypy。

## 不能退化的语义

- 接收、执行、历史持久化、渠道送达是不同事实；不能用一个 running 代替。
- 连接断开不撤销已接受输入；旧 reservation/attempt 不影响后继。
- 普通输入不隐式 steer；上下文轮换不切换历史执行路线或重放工具。
- 子 Agent 的检查/通信在父 Agent 忙碌时仍能工作；不能用统一大锁封死这些控制。
- 元数据与 MCP 只读查询仍可在执行时读取，权限不扩大。
- 发布异步准备结果前重验实际 owner；取消等待者不等于取消实际提交。
- Goal/Eyes/Flow 边界不变；关闭结算原持久化与执行资源，不自动重试未知效果。

## 实际模块边界

```text
GatewayServer                 进程、渠道、endpoint、连接 task 的关闭
  └─ NativeConnection         一个连接的协商、UI job、对话/查看者绑定与 detach
       ├─ NativeRequests      输入提交、回执读取、握手/历史投影
       └─ native_controls     模型/推理/access 请求校验与回执
            └─ Orchestrator   应用入口、实际输入/输出和 group 路由
                 ├─ model_controls / mcp_controls / access_controls
                 ├─ context_controls / extension_controls
                 └─ GroupRuntime   原 group 资源
                      ├─ group_lifecycle       prepare / reset / close
                      └─ AgentSession          当前 Agent 适配与 turn
                           ├─ session_tools    真工具身份、权限、pre/post、维护工具
                           ├─ session_prompt   提示词与实际 ACP 地址
                           └─ session_compaction  原归档/提交/取消结算流程
```

控制模块接收实际 `GroupRuntime`、适用时的 `GroupController`，不接收整个 Orchestrator/Gateway。
扩展命令只额外注入原 `input_service` 获取函数以初始化真实能力绑定；不增加 service locator。
NativeConnection 注入实际应用、原输入查询、viewer 集合和 transport writer，不反向持有 Gateway。
原应用入口仍用于已有协议调用，但不保留另一份实现、mixin 或兼容运行路径。

连接协商与多种 UI job 仍有不可忽略的协议复杂度。本次没有为达到任意文件行数限制而
再造一套连接调度器或抽象 UI 状态机；协议状态只属于原连接，不能成为执行权。

## 生命周期收口

- 移除 `_navigation_groups`。`GroupController.operation(context_change=True)` 用实际 live scope
  计数覆盖排队、执行、取消和 finally；前一个退出不能清除后一个操作的标记。
- 变更操作在获取原执行权之后调用 `require_current`，而不是只在收到请求时检查。
  重用现有 reservation / input / attempt，不增加另一种 owner token。
- 关闭先封住 controller 新准入，排队的旧变更不得开始。原 task 的嵌套结算仍可完成；
  不能由一个断开连接的 result waiter 释放实际执行权。
- 异步上下文准备在 IO/摘要后、发布前重新检查 open/owner/version。关闭与准备交错时，
  已完成的持久化不伪装回滚，但不能再发布新的 Agent。
- 只读 access、历史名称、MCP catalog/auth status/cancel，以及子 Agent 非 start 控制保留
  原独立路径，避免父 Agent 等子 Agent 时用统一大锁封死通信。
- `_source_revision` 保留其视图/上下文版本职责；`_session_prepare_lock` 保留准备与关闭的资源
  互斥职责。它们不是可替代 GroupController 的第二个执行控制器。
- 执行 snapshot 从这些实际 owner 派生；通知仍只是重新读取状态的提示，不是执行授权。

## 移除的无调用方旧入口

移除 Orchestrator 的 `_send_response`、`reset_group`、`abort_group`、`wait_group_idle`，
以及只供旧发送路径使用的 chunker。普通输入、精确 input/attempt 取消、异步上下文控制和
持久化输出仍走原产品入口。保留明确用于未发布资源组合/测试的 GroupRuntime 同步 helper；
产品路由不调用这些 helper。没有以“兼容”名义留下第二条应用执行或发送路径。

## 新增回归覆盖

- 两个上下文变更重叠时，第一个结束不清掉第二个 fence。
- 排队中的变更取消，不清掉正在执行的 fence。
- access/model/reasoning/child-start/extension/argument/compact/skill 排队后旧上下文失效，
  均不得准备或修改后继 Agent。
- 冷 argument completion 不创建上下文或启动 Agent。
- 关闭拒绝新操作与排队操作，但允许原执行结算。
- 在 check/prepare IO 期间关闭，不得发布 Agent。
- 应用 snapshot/source lease 与真实 scope 一致，不产生错误 idle 窗口。
- 原 gateway 关闭测试改为在新 NativeRequests 上注入同一个无回复故障，仍验证实际 socket
  与连接 task 的结算；没有弱化断开、不重放、权限或持久化断言。

## 验证记录

验证日期：2026-10-28。

- `uv run python scripts/test.py`：九个测试目录全部 exit 0，**10,368 passed、34 skipped**，
  388.68 秒。包括 Coding、Agent、Skills/Goal、Eyes、Flow、TUI、AI、Site 与 Claw。
- 最终移除无调用方旁路并补负向保护后再跑 Claw 全量：
  `uv run pytest -c pyproject.toml packages/claw/tests -q -n 2 --timeout=120 --timeout-method=thread`
  （独立 cache_dir），**2,014 passed**，144.80 秒。此轮与全仓结果重叠，不相加冒充独立总数。
- `uv run ruff check packages` 通过。
- 九包 `uv run mypy --strict -p ...` 通过，**651 个源文件**。
- `uv run xdog-flow test packages/flow/examples/ --allow-script-stub`：**17 passed**。
- Claw wheel/sdist 构建成功；检查最终 wheel，11 个迁出模块齐全、四个旧旁路方法不存在。
- 保留原权限、原输入/attempt 取消、历史事务、连接 detach、子 Agent、ACP 与渠道回执测试；
  唯一旧测试调整是把无回复故障注入从 Gateway 转到实际 NativeRequests，验收条件不变。

本次没有重启线上 gateway、提交、推送或发布版本。测试是行为证据，不承诺不存在任何回归，
也不冒充真实用户渠道的长期稳定性验证。
