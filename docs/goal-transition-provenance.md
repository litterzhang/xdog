# Goal 生命周期变更来源（2026-10-21）

## 解决的问题

用户通过原生 `/goal resume` 恢复 Goal 后，自动轮次只看到 active 与提醒中的自动来源，
看不到这次状态变化的实际入口，因而再次询问已经完成的恢复授权。
以前 registry 保存了状态/版本，但没有变更来源。此改动补足可观测性，不重新授予权限。

## 验收清单

| ID | 行为范围 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| GT-01 | 来源归属 | 实际 native 命令、模型工具、runtime、内部 service 分开；记录 action/actor/origin/规范 command/调用授权事实、实际 from/to 和版本 | 已完成 |
| GT-02 | 原子与完整性 | transition 与 goals/revisions/current/wake_signals 在同一 leased SQLite 事务内；注入 transition 写失败后全部回滚；重开校验内容、摘要与真实前后 revision，一旦损坏先拒绝再 recovery | 已完成 |
| GT-03 | 查询与展示 | status/summary（包括模型及 `--json`）和原生变更反馈包含 last_transition；UI 紧凑展示；plan/report 不覆盖最近生命周期变更，status/history 查询不新增 revision | 已完成 |
| GT-04 | 历史边界 | 现有 v2 registry 缺来源时 unavailable，不反推/补造旧记录；只添加受约束附表，不改 Goal v2 编解码或导入旧 journal | 已完成 |
| GT-05 | 权限与 skill | 模型不能以 actor/origin/user_authorized 参数伪造来源；native resume 不重复索取同次授权，不等于故障已修复、不扩权、不重放未知副作用；来源缺失本身不应让 active Goal 重新 wait(user) | 已完成 |
| GT-06 | 真实宿主与回归 | Coding/Claw 实际原生命令入口验证；完整九包测试、Ruff、九包 strict mypy | 已完成 |

当前剩余 **0 项**：待实现 0、待验证 0、缺外部环境 0、已完成 6、延期 0。
此清单仅指本轮变更来源能力，不代表用户的现场 Goal 已经完成。

## 数据与边界

- `OperationContext.origin` 仅由实际 Goal 调用路径提供；不新增任何可由模型传入的 provenance
  参数。`user_authorized` 是原调用的事实，不等于新的工具许可。模型调用发生在用户轮次时，
  actor 仍是 agent，不能显示为用户亲自执行 slash。
- Goal 自己的 `transitions` 附表以 `(goal_id, version)` 关联既有 revisions；同一事务产生
  前后状态及摘要。只保存规范命令（例如 `/goal resume`），不额外复制原始用户输入或凭据。
- `last_transition` 包含 availability、format、goal_id、version、action、origin、actor、
  user_authorized、command、from、to。已有 plan/content 版本可高于最近生命周期版本；
  若后来出现未记录来源的状态变化，不继承先前的 actor，改为 unavailable。
- origin：native_command / model_tool / runtime / service。actor：user / agent / host / unknown。
  这些只用于解释已被宿主接纳的操作，不参与权限批准。来源标签本身不成为授权。
- native `/goal` 与模型 status 使用同一真实 owner-bound service。Coding/Claw 产品源码
  不增加 Goal 业务 schema/dispatch；两者只是继续使用原来的 generic capability。
- 当前 Goal 已 active 时，不仅因 historical unavailable 制造 blocker 或重新设置 user wait；
  当前仍 paused 或 waiting/user/host 时，也不能拿历史变化来解除约束。
- 本轮没有操作用户正在运行的 Goal、重放其子任务、修改用户权限、重启服务、提交或发版。

## 证据

- 原子写、真实 native/model/runtime 来源、伪造字段拒绝、历史未知、重开、故障注入、
  当前 Goal/版本校验：`packages/skills/tests/goal/test_transition_provenance.py`。
- formatter 边界及 skill：`packages/skills/tests/goal/test_transition_presentation.py`。
- 集成定向（skills、Coding/Claw Goal）：445 passed，18.00s；
  `/tmp/xdog-goal-transition-integrated.log`（其中含 4 条既有 fork 弃用警告）。
- 最终回归日志：`/tmp/xdog-goal-transition-final-regression.log`：
  **9364 passed，7 skipped，全部九包通过，229.32s**。
- 静态日志：`/tmp/xdog-goal-transition-ruff.log`、`/tmp/xdog-goal-transition-all-mypy.log`。

- 最终 Ruff 全 packages/test_support 通过；九包 strict mypy **622 source files** 通过。
