# Goal：移除新的 wait(host)，保留历史与原始回执围栏

日期：2026-10-22。本文更新此前 Goal lifecycle / optimization / questions 文档中
把宿主故障转换成 `wait(host)` 的规则；历史验证记录不代表新写入契约。

## 当前契约

- 新的 `goal.wait` 只支持 `until=user|tasks`。工具 JSON schema、参数校验、
  service 和 store 写入入口拒绝创建 host wait。用户问题的 question / impact /
  resume_when、问题引用及只读实际投递查询保持原行为。
- `failed`、`interrupted`、`cancelled` / `canceled`、`preparation_failed` 是执行
  故障；不确定 enqueue / 原 accepted receipt 缺失是投递故障。它们不是 Goal
  业务状态，不自动修改 status、version、reason、question 或 last_transition，
  不自动 pause，不生成用户问题或重复工具审批。
- registry.v2 中新增独立 `runtime_faults` 表。按原 Goal、source、identity 幂等
  记录事实，并与原 revision 关联。状态摘要 / model 工具暴露 runtime_faults；
  原生文本给出有界故障提示。读取状态不发送请求、不改生命周期。
- 故障历史与当前续跑围栏分离。`preparation_failed` 明确未执行，只留故障历史，
  不要求额外 Goal pause/resume；但失败本身和普通 available 都不制造重试。
  新的任务变化或真实成功执行事实才能触发新的提醒。
- failed/cancelled/canceled 可以由同 owner 后续的真实成功执行自然解围：先固定
  已知故障快照，再核实 `latest_execution` 的实际 execution_id、succeeded 及
  durable settled_at。只有该快照中的已知失败可被取代；迟到旧成功、事实不可用、
  不匹配的执行、其它 Goal/owner 或读期间新增的故障都不能被清除。
- `runtime_fault_state` 保存故障时的 cause 水位及后续成功 ID，不改原故障记录、
  Goal revision 或 outbox。故障前的原因不会因为宿主恢复扫描再次启动工作；新
  cause 允许的也只是新提醒，不重试原始模型输入。恢复证明持久化后重启仍有效。
- interrupted/unknown 的执行效果仍不明确：它们与 delivery 不确定性一样保留
  围栏，不因普通成功或用户 pause/resume 的 epoch 切换消失。用户恢复 Goal 不是
  原副作用不存在的证明。本次不增加效果审查/解除接口。
- unknown outbox 和投递不确定性不会因为新 epoch 消失。即使 pause/resume，
  unresolved unknown 仍阻止新自动提醒；不会用换 ID 的方式把同一工作重发。
  本次没有新增“确认丢失”、撤回输入、重试工具或修改宿主 receipt 的接口。
- 明确未发送的 unavailable 保留原 ID/不可变正文再试；accepted 不是成功执行。
  新 epoch 也要查原 accepted receipt：pending / claimed / running 继续背压；
  查不到原回执按不确定处理，不能当作从未接受。preparation_failed 不锁住后续
  新工作；已知 failed/cancelled 可被真实后续成功取代；interrupted 仍阻止续投。
  原输入始终保持原 accepted 历史，实际 attempt_id 若可用也用于故障关联。
- receipt 查询跨越 Goal 版本/恢复边界时，旧结果不得把新 continuation 写入
  故障围栏；原 availability 检查与故障落盘也有原 Goal 快照校验。

## 历史 host wait

历史 v2 的 `wait_for=host` 原样解码，revision、payload、digest 和来源不改写。
模型保留这个值只是为了读历史，不代表新 API 支持创建它。恢复/attach、宿主
available、任务事件均不得把它规范化成 active，也不得重投旧 outbox。

只有实际用户授权的、带所见版本和恢复证据的 `activate`，或显式用户 pause /
clear，才能转换该历史状态。`resume` 仍只处理 paused，不释放任何 wait。
自动输入不能借先改 wait(tasks) 再 activate 来绕过授权。转换不证明故障已修复、
不授予工具权限、不解除 unknown / accepted-pending 的原 receipt 围栏。

## 宿主恢复与观察生命周期

Coding / Claw 仍只提供通用 capability owner / input / execution ports。宿主的
初始化 / 激活负责重新接入 capability；本次不修改宿主包或新增 Goal 特判。

skills runtime 在重新打开 registry 后的 attach，以及有效相同 parent 的再次
attach 上，恢复 refresh / 原始事实检查。不需要先启动新用户 turn，不创建模型、
不另建执行循环。仅使用原 durable causes、原 outbox ID 和实际执行 receipt。

`_stop_observer(*, suspend=True)` 对应 TaskObserver 的同名关键字协议：

- Goal pause、wait(user)、complete、clear：`suspend=True`，停止该 Goal 自己的
  注册，不影响其它 observer、accepted input、工具或 children。
- owner.retiring / runtime aclose：`suspend=False`，仅 detach 客户端；即使宿主
  intake 已关闭，也要结算 detach。daemon 可以继续保留注册和收集离线事件。
- 同 Goal endpoint 暂不可用或重新绑定、启动失败：只 detach，不把宿主可用性
  误当成 Goal 业务暂停。

TaskObserver/daemon event client 实现及冷安装/真实进程验证由并行接入负责；本文
不宣称仅靠 runtime mock 测试已验证整个 daemon。daemon 的原事件 ID 先进入
Goal 幂等 signal，再 ACK；同 ID 再交付不能制造新原因或新模型执行。

## 验证范围与限制

`test_host_faults.py` 覆盖多种执行故障不改状态/历史、跨重启围栏、历史 host wait
只读恢复与显式转换、原 pending/missing/interrupted receipt、同 parent 无新 turn
恢复、旧 receipt 查询与新 epoch 竞态，以及业务暂停和宿主 detach 的区别。
新增覆盖 preparation failure 后新任务可续跑、无新 cause 不重试、同 owner 后续
成功自然解围/重开去重、旧失败 receipt 不被重投、新故障不被旧恢复快照清除，
以及成功/epoch 都不解除 unknown、delivery、interrupted。
既有 questions/reminders、coalescing、transition provenance、wake race 测试保留。

目前没有解决未知投递的人工审查/解除 API；runtime 保守阻止自动续投，不能把
“用户 resume”解释成原未知副作用已不存在。Goal 也不负责恢复模型/工具执行。

前一快照本地命令记录（并行工作区，2026-10-22；不是以下 gate 修复后的结果）：

- `uv run pytest packages/skills/tests --ignore=packages/skills/tests/goal/test_task_automation.py -q`：677 passed。
- `uv run ruff check packages/skills`：通过。
- `uv run mypy --strict -p xdog.skills`：24 source files 通过。
- 包含并行 daemon 测试的完整 skills 测试：689 passed、1 failed；唯一失败是
  `test_live_goal_subscription_reconnects_after_daemon_restart` 的 daemon generation
  重连超时及随后 socket 缺失，属于并行 event client/automation 接入范围，不能
  据此宣称 daemon 整体恢复已验证。后续接入验证应使用更新后的实际结果。


## 2026-10-22 gate 修复验证说明

本次修复限定 runtime/store/test_host_faults/docs；不修改 automation 或 callback
bool ACK 段。主线报告的九包 9848 passed / 34 skipped 是本次 gate/ACK 修复前
快照，不能用来证明本次修复通过。gate 修复后重新执行的检查结果单独报告。

`task-snapshot:<sequence>:<digest>` 保留为权威任务文件的内容差异核对原因，
不是 daemon 原始 file 事件；不能据此伪造事件 ID、路径证据或发生时间。原 daemon
事件仍使用独立的 `tasks:<delivery.id>` 幂等原因。

Gate 修复后的最终受影响面检查：

- `uv run pytest packages/skills/tests/goal/test_host_faults.py -q`：43 passed。
- `uv run pytest packages/skills/tests packages/coding/tests/test_goal_adapter.py packages/coding/tests/test_goal_lifecycle.py packages/claw/tests/test_goal_host.py -q`：740 passed，4 warnings，15.48s。
- `uv run ruff check packages/skills`：通过。
- `uv run mypy --strict -p xdog.skills`：24 source files 通过。

以上是 gate 最终快照的 skills 全量和 Coding/Claw Goal 定向结果；不是九包最终全量。
九包最终复验仍由主线在 ACK、取消清理与 gate 全部收敛后执行。
