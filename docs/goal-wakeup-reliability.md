# Goal 任务变化自动续跑断点（2026-10-21）

## 现场事实与归因边界

只读检查 `goal-f288fecb434942de9cf6765ec912937a` 的 registry.sqlite3、任务 JSON、
原 session 输入记录和 child `87578632c197` 快照：

- delayed-write 为 completed/v2；child 持久状态 completed，settling=false。
- wake_signals 有 3 条 `tasks:<delivery-id>`，该 Goal 的 reminders 为空。
- Goal v3 为 waiting/tasks；后续人工诊断将其改为 v4 waiting/user。
- 已保存证据：`/tmp/xdog-goal-wakeup-investigation-20261021.json`（0600，只读取证）。
- 现场没有保留当时每个检查点的 children/draft/producer/control snapshot，不能拿当前
  deferred 或当前 idle 反推历史，也不能断言单一根因。证据中的工具自报 UTC 文本不代替
  宿主真实 settlement。以下各缺陷均由隔离屏障测试独立复现。
- 未对现场 Goal 发出 resume/change/complete，未重放其已完成操作，未启动另一次现场测试。

## 去重验收清单

| ID | 行为范围 | 可验证验收标准 | 状态 |
| --- | --- | --- | --- |
| GW-01 | Coding 可用状态信号 | 原 child 完成真实清理/持久化后、草稿从有到无、最后 producer 释放均触发 generic owner.available；重复空轮询不造信号；不伪造 main settlement | 已完成 |
| GW-02 | Claw child 可用状态 | 与原 group/session/Agent/extensions/runtime/loop/generation 绑定；child 未结束落盘、其它 child 仍活动、已退役 owner 不发出可用信号 | 已完成 |
| GW-03 | Goal 版本竞态 | 投递准备期间 plan 改版本，已有持久 tasks cause 不会因旧检查退出而永久搁置；owner/paused/user/host 边界仍有效 | 已完成 |
| GW-04 | Goal epoch 竞态 | 旧 execution/epoch 的失败检查不能将用户显式恢复的新 continuation 改为 host wait；原未知投递仍不自动重放 | 已完成 |
| GW-05 | 真实自动链路 | 实际 child 调 permission-gated bash，以官方 task_cli CAS 写临时任务；真实 Eyes 检测→Goal outbox→私有 Unix ACP→原 Agent report/complete；child/draft/producer 三种阻塞解除都无需新用户消息/UI poll | 已完成 |
| GW-06 | 完整回归 | 最终九包测试、Ruff、九包 strict mypy；针对性 owner fence/取消/多 child/unknown receipt 测试保留 | 已完成 |

当前剩余 **0 项**：待实现 0、待验证 0、缺外部环境 0、已完成 6、延期 0。
这里统计的是本轮断点修复验收，不代表用户现场 Goal 已完成。

## 实现边界

Coding/Claw 只补通用宿主可用信号；不导入 Goal 业务、判断任务完成或授予权限。
子生命周期使用真实 runtime 的独立订阅，不依赖可替换 TUI publisher；跨 loop 回原 owner
后重读事实，迟到 callback 必须通过原身份/退役检查。子 Agent 的终态不是主 Agent 的
execution.settled，也不是 Goal 完成结论。

Goal 的 causes/outbox、版本/epoch、待重查策略仍属于 xdog.skills.goal。
不用定时器盲目重复 prompt、不创建第二个模型循环、不重放 unknown/已启动的副作用。
等待用户、等待宿主和用户 pause 仍需原有显式授权才能释放。

## 验证证据

- 修复前 Coding 3 个 availability 测试均失败；修复后通过。
  `/tmp/xdog-goal-availability-before.log`、`/tmp/xdog-goal-availability-after.log`。
- 真实 Eyes/ACP 三种阻塞端到端及 generic availability：7 passed，7.90s，
  `/tmp/xdog-goal-real-eyes-acp-wakeup.log`。模型为可控测试模型，无联网模型调用；
  Eyes、child 工具执行、CAS、ACP、Inbox/原 Agent 执行及持久化未 mock。
- Coding Goal/队列/steer/ports：50 passed，12.70s，
  `/tmp/xdog-goal-coding-availability-regression.log`。
- Claw capability/child/retirement/Goal 定向：124 passed（子任务证据）。
- 上轮多行命令显示对齐补测：91 passed，12.08s；
  `/tmp/xdog-goal-system-alignment.log`，仅将首行字符串空格改为 Text 整块 gutter，
  保留 intentional task 缩进、折行、中文、安全过滤和 Transcript 原留白。

- Goal 版本/epoch 强制交错：先失败 3 项，修复后新 11 项通过；Goal 全套
  297 passed。只在进行中的 wake 被实际 change-generation 作废后补查一次，
  不把每次 plan/report 变成新提醒原因；active、waiting/tasks 和 accepted receipt
  背压都覆盖，不引入默认 create 自动唤醒路径。
- Coding/Claw 最终宿主 availability、三种真实 Eyes/ACP 场景：19 passed，10.65s，
  `/tmp/xdog-goal-host-availability-final.log`。
- 最终全回归日志：`/tmp/xdog-goal-wakeup-final-regression-rerun.log`：
  **9301 passed、7 skipped，全部九包通过，228.32s**。
- 静态日志：`/tmp/xdog-goal-wakeup-ruff.log`、`/tmp/xdog-goal-wakeup-mypy.log`。

### 全回归发现的测试隔离问题

第一次完整回归仍遍历了全部九包，222.91s；skills 收集错误及 Coding 一个故障注入测试失败：

- native presentation 测试的相对 import 在 skills 独立按包收集时无 parent；改为仓库已有的
  `packages.skills.tests.goal...` 绝对 import，单包完整验证 323 passed。
- Inbox 第 N 次 fsync 注入拦截了进程内所有线程，后台清理可能先消耗边界，导致目标 enqueue
  不失败而总 calls 达到 9。现在只针对同步 enqueue 所在原线程计数及注入；每个原有 1..8
  边界仍全部执行失败/恢复/同 ID 去重/冲突保护断言，并在每个边界前强制一次其它线程 fsync，
  证明它不消费注入点。未改 Inbox 产品代码，未删除测试或延长超时。
- 原失败证据保留在 `/tmp/xdog-goal-wakeup-final-regression.log`，最终全套另存 rerun 日志，
  不把定向通过或前次部分通过当作最终全回归。

- 最终全 packages/test_support Ruff 通过；九包 strict mypy **621 source files** 通过。
- 此前断点取证时现场 Goal 为 waiting/user（version 4）；该历史记录不是实时状态。取证/修复未自动恢复或重启用户客户端/gateway。
