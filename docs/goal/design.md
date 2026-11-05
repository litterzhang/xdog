# Goal：目标记录、任务与自动续跑

这是当前合同入口，合并早期简化设计、续跑优化、等待交互和宿主故障说明。
实现位于 `packages/skills/src/xdog/skills/goal/`；[历史验收](../archive/README.md)
只对应注明的源码基线，不代表旧接口继续受支持。

## 职责与能力

当前主 Agent 负责规划、执行、可选委派、进展判断和完成判断。Goal 保存记录、文件、
变更来源和提醒，不调用模型、不创建 worker/reviewer、不建立第二个 Agent loop 或验收器。
Coding/Claw 只提供通用 owner、生命周期、实际执行/投递回执和原 ACP 地址。

已安装 capability 提供实际工具、原生命令与可发现 skill。发现不创建 Goal，也不默认加载
skill 正文；SDK 接入见 [能力装配](../agent/capabilities.md)。权限仍由原宿主和工具检查，
自动消息、来源标签、worker 名称、任务文件都不能授予额外权限。

## Owner 与唯一事实

Claw 的 Goal 归稳定 group；上下文轮换和 `/new` 不迁移负责人。Coding 使用精确的
session/branch 绑定。每个 owner 至多一个当前 Goal，包括 waiting、paused 和 completed。

Goal 自有 `registry.sqlite3` 保存修订、当前绑定、提醒、来源与故障事实。
任务状态只以 `<workspace>/.xdog/goals/<goal-id>/tasks/*.json` 为准，不在数据库另存一份。

```text
.xdog/goals/<goal-id>/
  plan.md       主 Agent 的计划
  progress.md   可读进度和结构化报告，唯一报告权威
  tasks/*.json  稳定任务 ID、唯一状态、CAS 版本
  eyes.json     可选任务观察定义，不是执行权限
```

旧 journal/goals 文件不迁移、不导入、不重放，也不为了清理接口而删除用户数据。
格式、读取、容量和版本冲突必须明确报错，不能将损坏或 unavailable 当作空任务。

## 生命周期

| 状态 | 含义 | 如何继续 |
| --- | --- | --- |
| `active` | 可在真实 owner/输入/执行围栏允许时工作 | 主 Agent 工作，必要时由提醒唤醒 |
| `waiting` / `tasks` | 等待任务或证据变化 | 主 Agent 核验满足等待后 `activate` |
| `waiting` / `user` | 有明确的用户问题 | 取得真实用户答复/授权并核验后 `activate` |
| `paused` | 明确停止主动续跑 | 真实用户授权的 `resume` |
| `completed` | 主 Agent 已作出完成判断 | 保留当前只读结果；可显式 clear 或创建新 Goal |
| `cleared` | 当前绑定已清除，保留历史 | 需要新工作时创建新 Goal |

- `resume` 只恢复 paused，不用于解除 wait；waiting 使用 `activate`，需所见版本和满足条件的说明。
- active 的 `activate` 是幂等读，不生成新的修订或唤醒原因。
- `wait(user)` 必须保存 `question`、`impact`、`resume_when`，明确需要什么回答、影响什么、
  怎样继续。不能只写内部 reason 然后悄悄停下。
- 自动输入不能解除用户等待/暂停，也不能先改为 task wait 来绕过授权。
- 不支持 `wait(host)`。宿主故障是独立运行事实，不自动改变 Goal 状态、版本或问题。
- complete 保留当前结果，`/goal` 仍可查看，不自动 clear；历史目标不能恢复为可执行任务。
- 尽可能用已有信息和权限完成可交付部分。信息限制可说明假设；只有真实缺少必要决策或权限
  时才请求用户介入，不能将“提高质量所需的信息”冒充所有交付的必要条件。

## 计划、任务与报告

主 Agent 在已确认范围内维护计划，每个任务指定一个写入者。任务状态为
`pending`、`in_progress`、`blocked`、`completed`、`cancelled`，更新使用当前版本执行 CAS/原子替换。

子 Agent 优先使用 [窄工具委派](../agent/children.md)，只写获准任务，不管理整个 Goal。
主 Agent 更新自己的执行/验收任务及报告，而不是复制 worker 状态。`task_cli` 仅是经原权限
门控的外部 worker 入口，不是在工具委派被拒绝后绕过限制的途径。

每次续跑先核对当前 Goal、上次报告、任务和真实产物证据，再保存报告，之后才继续工作。
进展类别为 `baseline`、`advanced`、`unchanged`、`regressed`；expected_version 使用上一报告版本。
语义进展和完成由主 Agent 判断，不由脚本按测试数、文件数或固定轮数决定。
连续无实质进展且没有新方法时，报告已尝试内容及恢复条件并暂停，避免盲目循环。

## 一个提醒入口

```text
宿主 execution.settled / owner.available 或 Eyes 任务变化
  → Goal 持久化幂等原因与水位
  → 同一个 coalescer + durable outbox
  → 原 ACP enqueue / 原 Inbox
  → 原主 Agent 核对状态与报告后继续
```

- 忙碌、优先用户输入、活动子执行期间只积累原因，不建立并行执行路线。
- task wait 可因任务变化进入检查；user wait 和 pause 不因定时器或外部事件自动解除。
- 原因 ID/水位不是任务状态副本，发送期间新到的变化留给下一次，不能一并误清除。
- accepted ACK 只证明持久接收，不是 Agent 执行成功，也不是用户渠道已送达。
- 核对原 owner/namespace/branch/ID/不可变正文和真实回执；未消费的提醒产生背压。
  不确定效果不得通过换 ID、重建 owner 或切换 epoch 自动重放。

## Eyes、关闭与宿主恢复

Goal 只经公开 Eyes CLI 管理自己 owner 下的任务观察，读取 JSONL 事件并在原因持久化后 ACK。
Eyes 不知道 Goal 生命周期，也不判断任务完成。通用管理边界见
[daemon/tool](../eyes/daemon-and-tool.md) 和 [观察契约](../eyes/observation-contracts.md)。

pause、wait(user)、complete、clear 停止未来提醒及自有任务观察；不停止其它观察者、
不撤销已接受输入、不自动终止工具或子 Agent。宿主退场只 detach；同 owner 重新打开能力后
恢复原始事实检查，不创建另一个 Agent，也不重放未知的工具效果。

## 故障、来源与展示

执行失败、准备失败、取消、中断和投递不确定性单独记录，不被转换成 Goal 的业务等待。
已知失败是否可被同 owner 后续真实成功取代，必须按实际执行身份和 durable receipt 核实。
unknown/interrupted 不能靠普通成功、pause/resume 或来源标签清除。

变更来源记录实际入口、actor、规范命令、前后状态和版本。模型不能自报来源来伪造用户授权；
plan/report 不覆盖最近生命周期变更，状态查询不产生新修订。

状态默认是摘要：当前目标、任务计数/有限预览、最新报告、下一步责任人和独立运行/投递事实。
完整数据按显式 JSON/分页入口读取。中文正常输出有效 UTF-8 JSON；不对任意字符串做
`unicode_escape` 解码，字节分页和摘要仍遵循其真实 codec。

## 使用与验证

用户入口详见 [Skills README](../../packages/skills/README.md)、
[Coding README](../../packages/coding/README.md) 和 [Claw README](../../packages/claw/README.md)。
旧 GR/GO 等需求编号、配置快照和测试结果保留在归档中；当前代码验证入口见
[测试指南](../development/testing.md)，不把历史绿色结果作为本轮全量验收。
