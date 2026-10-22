# Goal：主 Agent 的目标记录与自动提醒

2026-10-20 用户确认的新设计；替代旧 Goal worker/reviewer/attempt 验收协议。
后续可靠续跑、报告和按需读取合同见 `goal-optimization-design.md` 与 GO-01..GO-09；
下列 GR 验证保留为简化版基线，不覆盖后续变更。
本轮需求编号 `GR-01..GR-12` 见 `goal-simple-requirements.json`。旧验证报告仅对其冻结版本有效。

## 边界

- Goal capability 是可信安装的 native extension，使用现有 `xdog.capabilities` 发现和 `ExtensionRuntime` 命令目录；不是第二套插件 loader，也不提升只读 goal_ui worker。
- Coding/Claw 默认依赖 `xdog-skills`，提供 Goal 技能索引、能力工具和 `/goal` 命令。安装/列出不创建 Goal、模型或子 Agent；显式已有工具白名单/readonly/审批依旧生效。
- Skill 描述主 Agent 的规划、已有工具执行/子 Agent 分派、任务文件更新、处理事件及完成判断。Goal 不调用模型、不创建子 Agent，不保留必选 reviewer、role tool、attempt/report digest 验收系统。
- 宿主只发布通用生命周期事件、提供 owner 快照与原 ACP 地址，不识别 Goal 状态。ACP 只是把自动消息提交到原 Inbox/Controller，不能赋予用户权限或另建执行器。
- Eyes 是可选文件观察层；Goal 生成 v2 配置，只通过真实 Eyes CLI 管理共享 daemon 中的任务目录 registration、读取 JSONL 事件并 ACK，不导入 Eyes SDK。Claw 本身只加载 Eyes 注册 tool；Eyes 不依赖 Goal/Agent。该边界由 2026-10-22 的 `eyes-daemon-goal-hosting.md` 修订。

## 用户与模型入口

`/goal [create <outcome>|status|pause [reason]|resume|clear [reason]|history]`。
创建命令只保存最小目标并提醒原主 Agent 按 skill 规划，不启动另一个规划模型。

原生 `goal` AgentTool 提供 create/status/history/update/plan/task_update/wait/pause/resume/complete/clear。
plan 只保存模型提供的 plan.md；task_update 只写任务文件；complete 只保存主 Agent 自己的完成判断。
命令及工具调用同一 runtime/service。工具仍走现有宿主 pre-tool gate；native 用户命令仅在真实用户控制范围临时持有授权，自动 ACP 输入不能伪装为该控制范围。

## 存储和单当前目标

稳定 owner = 宿主提供的 namespace + workspace + 主 Agent 标识；Claw group 不随聊天上下文轮换改变，Coding 精确绑定 session/branch。
每 owner 至多一个当前 Goal，active/waiting/paused 都占用绑定。completed/cleared 释放绑定，成为只读历史；不能把历史 Goal 恢复成新任务。

新 `registry.sqlite3` 是 `xdog.goal.registry.v2`：owner/current/Goal revisions/reminder outbox，无任务状态表。
Goal workspace = `<workspace>/.xdog/goals/<goal-id>/`：

```text
plan.md
 tasks/<stable-task-id>.json    # 任务唯一权威，模型/worker可读写
 eyes.json                     # runtime生成，未纳入观察范围
 .task-write.lock              # SDK task_update写入互斥，不是外部任意写入的OS沙箱
```

任务包含 ID、title、description、status、worker、depends_on、summary、evidence、version。
状态为 pending/in_progress/blocked/completed/cancelled。worker 是不透明跟踪标签，不是权限凭据。
SDK 更新必须带 expected_version，原子替换；外部 worker 依 skill 保证单任务单写者、先读最新版本并原子更新。
文件以 bounded no-follow regular-file IO 读取，格式错误/冲突不能变成空任务列表；状态读取不从数据库缓存拼出另一份任务事实。

旧 `journal.sqlite3`、`goals.json` 不改写、不删除、不自动迁移或执行旧 worker/reviewer。当前状态/历史入口明确给出旧存储位置，避免默默覆盖为“从未有目标”。旧文件中任意状态或代码名称不成为新的执行授权。

## 生命周期和消息

宿主 `execution.settled` 只在真实历史/输入回执结算后发出；仍 busy 时可由后续 owner.available 提示重新检查。
Goal callback 不在控制器锁内等待模型；订阅通知只安排 owner 上的异步工作。

仅 active、宿主空闲、无优先用户输入/活动子执行且 owner 仍匹配时提交提醒。
提醒先持久化为 outbox，再用原 ID 通过 ACP `xdog/enqueue` 扩展发送；不能以普通 prompt 的断线作为未执行证明。
真实发送前原子 claim 再检查 Goal/version/current/pending。ACK 丢失核对同 namespace/branch/原 ID/不可变内容，未知结果不换 ID 盲重发。

- 新 wait 只支持 user/tasks；历史 host wait 保持只读兼容，只能显式用户转换，
  不重放旧工作。详见 `goal-host-faults.md`。
- waiting(user/历史 host) 和 paused 只能由真实用户恢复，不能先改 waiting(tasks) 再洗成自动 resume。
- waiting(tasks) 可在任务文件通知后由主 Agent 决定恢复。
- failed/cancelled/interrupted/preparation_failed 宿主执行记录独立故障、阻止自动
  重放，不自动修改 Goal 状态/版本或进入 host wait。
- pause/clear/complete 停止未来提醒和自有 Eyes；不撤销已接受消息，不自动终止子 Agent 或其它观察者。
- endpoint 不可用要关闭旧观察并明确报告 unavailable；重新可用可重试绑定，但不重放未知消息。
- 本轮不实现跨进程自动恢复工具执行。owner/工具过期后不能借后继 parent 授权；关闭结算真实 IO、通知、观察和已接受的本地义务后才释放 journal。

## 安装及验证

默认安装 Coding/Claw 都包含 Skills；Claw 采用与 Coding 同样的版本限定 ACP 依赖，Gateway 按通用 capability requirements 启动原私有接收端，不依赖 Eyes 是否安装。
裸 SDK 没有实际接收端时明确缺少自动投递能力，不隐式创建另一个 owner/Agent。Goal 的记录、工具、技能仍然可用。

当前 Linux 验证包括域状态/文件/并发、native 命令、真实 host controller/receipt、ACP/ACK loss、Eyes文件观察、两个宿主默认安装/权限/上下文轮换/关闭和最终完整回归。真实付费 provider、微信网络和非 Linux 原生验证不在本轮范围。
