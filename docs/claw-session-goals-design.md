# Claw group-owned Goals and exit gate

## 范围与状态

用户于2026-10-18明确修正：**每个group只有一个逻辑主Agent，Claw只维护一条执行路线**。
Goal、task、Eyes事件均归group；session仅为上下文和历史存储，不是负责人或事件目标。
本决定取代旧目标文本中的session-owned/exact-session路由、旧session消息停泊及负责人迁移。
当前实现验收记录更新于2026-10-19；稳定需求编号及精确数量以
`claw-session-goals-requirements.json`为准。未通过最终门禁前不声称目标完成。

## 已实现结构

```text
用户目标 → group唯一主Agent自主规划
             ├─ assign_task(executor=main) → 主Agent工作并报告
             └─ assign_task(executor=child) → 专用任务子Agent
                                           ↓ task_report
                                  原子attempt JSON报告
                                           ↓
                          Eyes → 现有group ACP/inbox
                                           ↓
                             同一主Agent重读、接受、调整
                                           ↓
                      request_complete / Goal工作安全自然停止
                                           ↓
                       私有独立review子Agent实际取证并出finding
                                           ↓
                       宿主归档 + 条件/版本/成果快照栅栏
                              ├─ 通过 → 完成并通知主Agent
                              └─ 未通过 → 缺口/阻塞，不冒充完成
```

没有自动planner、另一套任务调度器、第二个主Agent或工具循环。任务worker、reviewer都复用
现有ChildSupervisor/Agent。脚本和测试仅是Agent取证工具，没有脚本退出码直接判成功、
空条件auto-pass或按完成文字关键词终止。

## Group与上下文

- GroupRuntime只缓存一个活动AgentSession。磁盘catalog/JSONL保存历史，不代表可切换执行路线。
- daily reset按既有UTC小时计算，idle reset按既有空闲配置计算；未完成Goal及活跃children阻止自动轮换。
- `/new`、`/reset`只轮换空闲group的上下文；不改Goal负责人，不删除任务/报告，不放宽readonly。
- 已移除Claw UI/协议和SDK的resume/fork/branch/goto历史激活；TranscriptStore不再提供旧激活写接口。
- 保留历史只读查看、普通导出、命名与compaction原历史归档。legacy `_branch_`文件名/目录字段
  是只读历史格式及归档复用，不是新的执行分支。Coding的分支能力不受影响。
- 新上下文先持久继承权限再发布active指针；失败回滚，回滚失败封闭存储等待恢复。
- 标准ACP的`session_id`仍承载`group:<id>`逻辑地址，不是磁盘session ID。未新增session定向路由。

## 单写者与数据

权威Goal状态在group数据目录`goals.json`中，格式版本2；旧versionless数据可读，未知版本、
损坏数据或重复键使Goal功能关闭并保留原文件，普通聊天仍可用，不能创建新Goal覆盖旧数据。

```text
<workspace>/.xdog/goals/<goal_id>/
  goal.md                  # 原始目标的可读表示
  contract.json            # 结构化原始description/conditions，更新须来自实际用户turn
  plan.md                  # 主Agent维护的策略/决策
  status.md                # 宿主生成，不由多个Agent覆盖
  tasks/<task_id>/
    assignment.md          # 主Agent分配说明
    <attempt_id>.json      # 指定执行者的原子报告
  reviews/<review_child_id>.json  # 独立finding；是否接受以权威Goal journal为准
```

任务报告的七个字段为`task_id/attempt_id/revision/state/summary/evidence/blockers`。
专用worker的`task_report`工具只接收后五个字段，身份和路径由宿主绑定；主Agent自执行通过
`goal.report_task`使用相同契约。新内容必须增加revision，同revision只允许完全相同的ACK丢失重试。
拒绝错身份、旧attempt、链接、非普通文件、重复JSON键和非有限值；文件与目录fsync，失败不假装保存。
长Goal完整说明存文件，子Agent短prompt只传引用，不靠截断原始验收条件适配控制帧。

`running/blocked/submitted`是执行者报告；`submitted`不是任务接受，更不是Goal完成。
主Agent核对后`update_task`接受或给出明确skip理由。worker停止但最后只有running报告，任务变为
interrupted并要求检查，不永久等待也不自动重新执行。用户放弃Goal时只收敛该Goal拥有的children，
不取消同group的无关工作，工作区和证据保留。

## Eyes与恢复

实时文件提示要求gateway显式`eyes_enabled: true`。观察范围仅task JSON，不观察宿主状态和review
输出，避免自通知循环。`owner/notify_only`是宿主journal字段，不能由事件provenance或工具参数伪造。
Goal内部消息复用group主Agent，但不把每次进展广播到微信；实际完成/阻塞走正常主Agent报告路径。

事件只提示重读最新文件：核对当前attempt/revision，重复和旧事件合并/忽略。接收入箱不是任务完成，
Agent执行成功也不是渠道送达或用户已读。文件首次基线和离线变化由启动对账/子Agent状态唤醒补齐。

普通文件观察器仍不自动恢复。Goal显式恢复自己的file observer时，先核对原不可变outbox、namespace、
原message ID及完整内容，再接回既有inbox，完成后才建立新基线。未领取工作可恢复；已经运行、
interrupted或发送结果unknown不盲重放。取消、需审核及不匹配owner不能被resume重新启用。

Goal主Agent输入在发起前记为running；崩溃恢复为interrupted，要求检查原效果后显式继续。
独立reviewer的结构化finding和宿主持久终态回执归group，跨上下文/进程仍可核对并提交，不靠重跑模型恢复。
单个group的长turn不占住其它group的Goal协调；只在安全空闲时派发，用户排队输入优先。

## 结束门与权限

- 只有私有review角色才有`goal_review`；标签、普通spawn或自称reviewer不赋予身份。
- 原始条件按顺序逐项返回status/evidence/reason，缺项/空证据/非法finding拒绝，可在同次review中修正。
- finding先持久归档；Goal revision或选定成果变化使旧finding失效。链接证据路径拒绝，缺失路径
  可记录为absence以支持删除目标。默认快照排除运行journal、依赖和缓存；需要Git元数据等内容时应显式
  指定相关相对路径。是否覆盖了充分证据仍由review Agent判断，不把哈希相等当业务通过。
- 验收故障或不可建立安全快照保持未完成并明确原因。取消/错误/输入被hook处理/等待不冒充正常完成。
- 声明Goal工作的输出标注“尚未验收完成”；正式完成状态只能来自Gate。普通聊天不进入Goal结束门。
- `goal.update`条件变更、pause、abandon及恢复用户暂停必须来自实际用户turn；文件/后台事件不授权这些控制。真实用户的steering/follow-up/编辑器队列消息在被消费后同样有效，
  不要求另开session，也不在仅入队时提前授权。
  blocked不是用户暂停，主Agent获得新证据后可显式resume，不得自动重放不确定操作或降低验收标准。
- 无工作turn和未变化的协调状态不形成自动空转。验收缺口保存并可见，不以预算或暂停冒充成功。
- 这些是宿主权限与一致性保护，不是OS sandbox；继承工具仍只拥有宿主既有权限。

## 子Agent长任务

移除固定120秒/600秒上限、16次调用、累计32k tokens、3并发和16记录业务限制及对应配置/CLI选项。
由主Agent管理启动、等待、调整、取消、关闭。保留真实模型容量、单消息/图片/文件协议限制及生命周期清理。
完整历史分片并校验后原子提交索引，不截断工具配对来换长运行。

Generic Agent仍默认不自动compact。Coding/Claw child factory按实际model metadata显式启用既有built-in
策略，先持久归档完整原历史再开始摘要，失败保留历史。未知容量不会冒充已启用；摘要单请求超时是
上下文操作的I/O约束，不是任务累计预算。没有真实provider数天运行的保证，归档磁盘由宿主管理。

## 验证与发布边界

验收包括真实worker原子报告→Eyes→Unix ACP/inbox→同group主Agent→独立取证Gate→渠道报告，
独立三进程恢复Gate回执且不重跑模型，以及权限/元数据事务在真实进程中断后的恢复。
最后还须完整八包回归、独立MCP SDK、Ruff、strict mypy、Flow示例、锁、全项目构建与wheel/skill审计。
不删弱安全测试或以skip换通过；非Linux原生验证不在范围。

保留无关dirty，未授权commit/push/bump/tag/发布或重启用户服务。源码/产物/门禁精确证据见JSON清单。

## 最终验收（2026-10-19）

**12项已完成，剩余0**；待实现0、待验证0、缺外部环境0、延期0。

- 全8包：**6685 passed，7项既有可选外部CLI skipped，0失败；123.07秒**，独立MCP SDK启用。
- Ruff通过；8命名空间strict mypy：560源码通过；Flow示例17通过；离线锁通过。
- 9项目新构建18产物；wheel-only 32模块导入、15项旧导入被拒、837源成员与8 README元数据核对通过。
- 独立Eyes不导入harness或激活ACP。
- 实际Claw Goal只读worker：32次业务请求、80320 reported tokens、10次原生摘要及10份durable archives；父历史不变，工具配对保留。
- 真实Goal报告→Eyes→Unix ACP/inbox→同group主Agent→独立实际取证Gate→完成报告通过；含observer创建ACK缺口。
- 三个独立解释器恢复Gate回执并提交完成，不重跑模型；上下文轮换readonly事务的真实进程中断恢复通过。
- 实际用户steering/follow-up/编辑器输入在消费后授权，取消/取回后旧授权失效；system/ACP伪造来源不能改变Goal条件。
- 1345个package输入冻结哈希一致；9份无关用户文件保持原样；无stage、commit、push、tag、版本变化或用户服务重启。

上述仅本地Linux与可控适配器验收，不宣称真实provider数天持续运行、真实微信或非Linux原生已验证。
证据：`/tmp/xdog-session-goals/final/summary.json`及同目录门禁日志/产物核对。较早候选证据单独归档，不充作最新通过。
