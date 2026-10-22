# Goal：问题记录、下一步责任与提醒边界

本轮只修改 `xdog.skills.goal`、打包 Goal skill 和 Goal 测试。没有新增
sender、worker、scheduler 或 Claw 消息工具耦合。按后续约定增加可选宿主
回执只读查询适配，不实现宿主输出存储或发送。

## API 变化

### 新的 user wait

`Goal` 数据类末尾增加三个字符串字段（默认空，保留原位置参数顺序）：

- `question`：用户需要回答的明确问题；
- `impact`：缺少答案具体阻断什么；
- `resume_when`：什么实际答复/事实足以供 main Agent 判断恢复。

工具 `wait` 接收同名顶层字段。例如：

```json
{"action":"wait","until":"user","reason":"最终目标未定",
 "question":"交付 A 还是 B？","impact":"不能确定最终交付目标",
 "resume_when":"用户明确选择 A 或 B"}
```

`until` 省略仍默认 `user`，同样要求三个非空、有界、无 NUL 的字符串。
三个字段与 waiting 状态、版本、历史、生命周期来源写入同一个原有 SQLite
事务；失败整体回滚。它们只适用于 user wait，其他 action / wait 类型拒绝。
退出该 wait 后当前记录清空问题字段；历史版本保留原始问题。
`plan` / `report` / `update` 不自动回答、覆盖或释放已有问题。

这三项是 **数据，不是执行指令、用户身份或授权**。main Agent 根据真实上下文和
原授权判断 `resume_when` 是否满足，再调用原有精确 ID/version/reason 的
`activate`。时钟、任务事件、自动输入、问题中的“同意”文本均不能释放 user wait。
原有 pause、host wait、完成判断和 user 授权边界不变。

### 兼容旧 v2

存储格式继续是 `xdog.goal.v2`；解码仅允许完整的新字段集合或原来完全缺少三个
字段的旧集合，拒绝部分新字段、未知字段。旧记录读入内存时三项为空，并明确显示
“not recorded”，不从 reason 猜问题，不伪造已发送、已送达或已回答。
只读、历史和共享 view 不重写旧 payload 或 digest；后续实际变更才产生新版本，
既有 revisions/reminders 的 payload/digest 不变。未提供原始回执的事实仍不可得。

**前向兼容限制**：旧版本的严格 v2 解码器不能读取包含新字段的新记录；本轮实现
的是新版本读取旧记录，不承诺降级可写或替旧二进制做迁移。没有生产数据升级操作。

### 展示事实

- `/goal` 将状态后面的下一步责任、问题、影响和恢复条件放在任务统计之前；
  `active` 明确不等于 Agent 正在运行。
- compact status 的 `next` 增加 `owner`：`main_agent`、`task_workers`、`host`
  或 `user`。这是展示建议，不授予执行权。宿主可用性/投递不确定性仍独立显示。
- status 增加派生 `question` 对象（非 user wait 为 null），不改变
  `current.question` 字符串或工具参数。含 `id/text/impact/resume_when/availability`、
  `delivery` 和保存不等于送达的 notice。`availability` 为 `recorded` / `not_recorded`。
  同时保留 compact `question_record`，含相同 id/availability/notice 和取自同一次
  查询的 `delivery_status`，不重复问题正文或输出事实。共享 view 同样提供此字段。
- 新问题的 ID 为 `goal-question:<goal_id>:<user-wait-version>`，从实际 wait 来源及
  immutable revision 验证得到，不使用后来 plan/update 的版本。同一 user wait 的
  三个问题字段相同且 reason 未变/省略时，ACK-loss 重试只返回原记录，不增版本、
  不新增 ID/wake；此无副作用返回不释放等待，也不授予原本没有的变更权限。
  仅修改 reason 仍需原用户授权并增加记录版本，但保留问题 ID。三个问题字段变更
  才建立新关联；退出后重新进入 user wait 也是新问题。
  原问题/来源未记录时 ID 为 null，不编造或关联用户任意 reference。
- 共享 `xdog.goal_view.v2` 的 item 增加同形 `question`，其 text/impact/resume_when
  各自保留 160 字符预览及 `question.preview_fields` 截断标记；沿用 `next.owner`。
  完整事实仍可通过原工具的 snapshot/page 接口读取，不增加可写 view。
- tasks wait 需任务负责方提供事实，由 main Agent 检查；host wait 需实际宿主恢复；
  paused 需用户显式 resume；completed 无待执行 Goal 工作，用户可选择新 Goal。
  completed 的 `next.owner=user` 指可选的新目标，不表示仍欠原目标的工作。

Goal 本轮保存和展示问题，并可查询宿主实际输出回执，**保存不等于送达**。
没有 reply 绑定或主动 sender。原 reminder `delivery` 专指发给 main Agent 的
ACP 输入，不是问题的用户送达；问题投递事实独立位于 `question.delivery`。

#### 可选宿主只读端口

GoalRuntime 在原 CapabilityHost 上检测可选 async 方法：

```python
async def message_delivery(reference: str) -> dict:
    return {"status": "untracked", "outputs": []}
```

仅传由实际 wait revision 派生的 reference。返回 status 为 `delivered`、`pending`、
`unknown`、`partial`、`cancelled`、`untracked`、`unavailable` 之一，outputs 为宿主当前 owner 实际输出事实的
对象数组。`untracked` 表示宿主无匹配关联记录，不能证明没发送过，也不能用来替代查询失败。
`untracked/unavailable` 可不带 outputs，展示补空数组但保留原状态及原因，
不据此宣称未发送。`delivered` 要有非空 outputs，每行必须有非空有界、无 NUL 的
字符串 id 且 row.status 必须为 delivered；无效行令查询结果 unavailable。
这仅校验传输事实结构，不是 Goal 任务验收。Goal 不从模型声称、工具成功文本、accepted 或
elapsed time 推断送达；聚合状态由真实宿主负责，unknown 不等于 delivered。
宿主可将全取消的输出摘要为 cancelled、含 unknown 的摘要为 unknown、
delivered 混合 pending/cancelled 摘要为 partial；Goal 原样展示合法聚合事实，
不重算业务验收。cancelled 仅是输出传输事实，不是 Goal lifecycle 状态。

`GoalService.message_delivery` 是可选注入的 async callable，不是模型工具参数。
无能力（例如尚未提供该端口的宿主）、原 ID 未记录、读取失败、超时或无效返回
均显式为 unavailable；保留“保存不送达”的说明，不误报为无发送。查询使用
5 秒 async deadline（取消仍须等待实际 owned IO 收尾），返回须为不超过 64 KiB
的有限 JSON；读取取消/owner retirement 沿原调用
传播，正常结果脱离宿主可变对象。该查询只在 status/summary/共享只读 view 中进行，
不产生发送、绑定、自动重试或解除等待，也不将回执写回 Goal。

宿主工具若支持主动发送及 generic reference，可由 main Agent 在原授权下用
question.id 关联实际输出。这只是关联数据，不授予路由权。skill 不依赖 Claw
或任何具体工具名。宿主 output journal/message tool 和 Coding/Claw 集成测试仍属
对应实现，本轮不做真实通道端到端送达承诺。

### 工具错误

保留 `error` 文本。参数结构错误增加：

```json
{"error":"update does not accept fields: reason; ...",
 "code":"unsupported_fields","action":"update","fields":["reason"]}
```

`code` 区分 `unknown_action`、`unsupported_fields`、`missing_required`、`type`；
`fields` 精确到 `task.expected_version`、`criteria[1]` 等路径。检查先拒绝跨 action
字段，再检查必需字段和严格 JSON 类型（布尔值不作为整数）。值域、CAS、权限和
存储错误仍走原有语义错误/实际异常路径，不把存储失败变成成功。
`update` 要求 version 和至少一项 title/outcome/criteria，**明确不接受 reason**，
goal_id 仍可省略以选择当前 Goal。`task_update` 创建仍需要 title；只是把原先
构造 Task 才发现的缺字段提前定位。纯 `tool_effect` 同步复用结构校验，不能为
不完整新 wait 或错误参数提供较宽的 effect 分类；这仍不是权限授予。

## 提醒 causes：先切边界，再截预览

`wake_state` 在原有事务快照中保留两条独立边界：

- `through`：按原逻辑筛选当前 authority epoch 的 accepted reminders 后计算的
  max(wake_through)，仅供 runtime 原有调度/故障恢复使用，显式恢复仍可唤醒。
- `cause_through`：该 Goal 全历史 accepted reminders 的 max(wake_through)，仅供
  正文 causes 增量使用。只选择
  `cause_through < sequence <= 当前快照 sequence`，再按降序取末 16 项。

跨 user/host wait 激活或 pause/resume 不会重置正文边界，已接受的旧 causes
不会因新 epoch 混回新正文。
不删 wake_signals，不改变原 message ID/payload/digest，不新增“已处理”游标。

例：先接受 through=8，新增到 12 时只带 9..12；再接受 12、新增到 17 时只带
13..17；再接受 17、新增到 22 时只带 18..22。不会因累计 last16 把旧 cause
混回新提醒。in-flight send 快照为 8、期间新增到 12 时，原消息仍只接受到 8，
后续 9..12 继续保留。重试仍复用原 ID/原正文，不把新 cause 塞入旧正文。

`wake_state` 新增 `through`、`cause_through`、`cause_count`；新 reminder provenance 增加
`triggers_count`、`triggers_preview_only`。`triggers` 仍为最多 16 个字符串的预览；
超出项仍在原 journal 内，以计数标记省略。它不是逐事件执行队列；main Agent
必须检查当前权威任务事实，不能把 cause 预览当作完整任务差异或完成证据。

三个边界独立：

1. **accepted / ACK**：Inbox 接受原输入，允许 causes 按全历史 cause_through 分段；不是处理完成。
2. **执行回执**：原输入 pending/claimed/running 等继续背压；失败、interrupted、
   unknown 等保持原等待/人工检查规则；unknown 不盲重放副作用。
3. **main 检查**：succeeded 也不证明任务被接受、问题已回答或 Goal 完成。
   main Agent 仍检查原任务事实、保存进度评估、判断 activate/complete。

原 coalescing、revision race 复查和 explicit recovery epoch 规则保留。没有将时钟
改成新 wake cause 或自动 wait-release 条件。

## Skill 决策要求

明确区分：无可行授权工作的硬阻塞、可诚实声明限制并继续的信息限制、必须用户
作出的业务选择。可提高质量的信息不自动成为交付门槛，不擅自增加验收条件。
先检查可访问材料、推进独立任务；仅阻塞受影响任务。真的全局用户阻塞才保存一次
合并问题并等待。权限仍由宿主负责，权限拒绝不是聊天中的用户业务审批题。

## 验证范围

新增定向测试：

- `test_questions.py`：必需字段/默认 user、原子回滚、重开持久化、旧记录字节和
  digest 不变、时钟与自动输入无释放权、责任人和共享预览；
- `test_question_delivery.py`：稳定/幂等 wait reference、仅 reason 变更保留关联、重开、可选 host port、真实状态、
  无能力/无关联记录/无效事实/取消/owner 退役、只读无发送；
- `test_child_capability.py` 追加新旧 raw payload 下 scoped-child schema、字段注入、
  生命周期拒绝和实际父工具撤权测试，不扩大任务 grant；
- `test_action_errors.py`：action/字段/嵌套缺字段/严格类型定位、update 可省略 ID；
- `test_reminder_causes.py`：8→12→17→22、ACK 背压、超 16 预览、重开和发送 race；追加跨 user/host wait 与 pause/resume 的正文边界分离、
  旧 pending 回执背压及显式 resume 唤醒回归。
- skill/effect 的追加回归覆盖信息限制≠交付门槛及参数校验收紧。

已有 user-wait 测试仅补齐合法问题夹具、`next.owner` 精确快照及新的 skill 签名；
原生命周期、权限、回执和不重放断言保留。原 effect 的合法写入夹具补齐 update
内容和创建 task 的 title，不把非法请求算成成功写入。

执行 Goal 测试、Goal 源码/测试 Ruff 与 `mypy --strict -p xdog.skills.goal`；
不扩大到 Claw/Agent/Coding 测试、冷安装或端到端宿主问题投递验证。所有测试状态
在 pytest 临时目录；未操作服务或生产 Goal，未改版本或提交。

最终本地验证（2026-10-22）：

```text
uv run --no-sync pytest packages/skills/tests/goal -q
618 passed
uv run --no-sync ruff check packages/skills/src/xdog/skills/goal packages/skills/tests/goal
All checks passed!
uv run --no-sync mypy --strict -p xdog.skills.goal
Success: no issues found in 17 source files
```

mypy 仅提示根配置有本次选定包未使用的 overrides，不是类型错误。

### Review 定向修补验证

正文边界改用全历史 accepted 的 `cause_through`，epoch-local `through` 继续仅供
原 runtime 调度/恢复使用。新增跨 user wait/host wait 激活、pause/resume 的
v7-through17 → v11-through22 回归，验证新正文不重复旧 cause，旧 pending 原始
回执仍背压，显式 resume 无额外任务/执行事件也能唤醒，旧 accepted 行不变。

`delivered` 追加每行 id/status 的传输结构校验，10 组 malformed/mixed rows 均降为
unavailable，不做任务/Goal 验收；原查询取消、异常、无能力和 untracked 语义保留。

- 两个定向文件：42 passed（新增 14 用例先失败，再修补通过）；
- 完整 Goal tests：632 passed；
- Goal 源码/测试 Ruff：通过；strict mypy：17 源文件通过；范围内 diff check：通过。

输出 cancelled 适配：可选宿主查询现在接受并展示 cancelled；Goal lifecycle 不变。
追加回归验证 cancelled 仍保留 user wait/原历史且不产生发送，并验证输出行 cancelled
不能支持 delivered 聚合声明。定向投递测试 31 passed，完整 Goal tests 634 passed；
Ruff、strict mypy（17 源文件）和范围内 diff check 通过。


## 2026-10-22：宿主故障与历史 host wait 更新

本文中的 host wait 只指历史 v2 数据兼容，不再允许新建。宿主执行/投递故障
记录在独立 runtime_faults / outbox，不能自动把 Goal 改为 wait(host) 或 pause；
questions/reminders、问题 ID 和实际投递查询保持原契约。历史状态只能显式用户
转换，并保留原 accepted/unknown receipt 防重放围栏。当前规则及限制见
`goal-host-faults.md`，daemon 接入由独立验证记录负责。
