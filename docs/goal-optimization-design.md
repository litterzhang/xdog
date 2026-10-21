# Goal：可靠续跑与主 Agent 进展报告

日期：2026-10-21。续接 `goal-simple-design.md`；可计数验收为
`goal-optimization-requirements.json` 的 GO-01..GO-09。

## 不增加的职责

Goal 仍是目标/任务记录、稳定主 Agent 绑定和消息提醒。规划、执行、子 Agent
分派、进展判断、完成判断都由当前主 Agent 按 skill 完成。没有独立 planner、
reviewer、任务轮数门限、模型轮询或第二 Agent loop。Coding/Claw 只提供通用
执行事实、原 owner 和 ACP 地址，不识别 Goal 的业务状态。

## 一个提醒入口

宿主 execution.settled 和 Goal-owned Eyes 的任务变化均先写入 Goal 的幂等原因
索引，再由同一个 coalescer 投递。Eyes 的公开 handler port 只负责将检测提交给
Goal；无 Goal callback 的独立观察 API 保持原语义。生成的 eyes.json 仍是数据。

- `wake_signals` 只存原因 ID/顺序，不复制任务状态或执行结果。创建及显式 resume
  与 Goal 记录在同一事务写入原因；已 active 的 Goal 也可显式 resume 唤醒。
- 忙碌、用户输入优先或活动 children 时只累积原因。
- 一个 Goal 至多一个尚未消费的提醒。Inbox 的 accepted ACK 不是执行完成；
  查原始 message receipt，pending/running 阻止新投递。
- 提醒带本次覆盖的原因 watermark。发送期间出现的新变化留给下一次，不误清除。
- 原 ID、不可变内容和 owner/namespace/branch 仍由现有 ACP 机制验证。本地 claim 后
  再检查 owner、生命周期变更 generation 和快照；读取期间 pause/retire 不得穿透成
  新的传输请求。已开始的传输按原结果结算，不将其误当成可撤回的远端操作。
- 明确未发送 unavailable 只能重试同一 ID/内容；unknown 进入 host wait，不换 ID
  盲重发；启动时核对持久原因/outbox 和真实 host 回执，不根据闲置时长猜测完成。
- 用户明确恢复 paused/user/host wait 后建立新的 continuation epoch；旧失败或
  unknown 记录仍保留，但不能在新 epoch 反复重新暂停。

观察的初始 baseline 不报告历史变化。因此启动/恢复时还检查任务文件内容摘要；
暂停/离线期间的实际变化成为新的原因，不重放暂停期间的 tick。摘要只是派生
变化提示，主 Agent 必须重新读权威任务文件。

## 续跑前的 goal report

统一 `goal` 工具增加 `action="report"`，不是另一个 worker/report RPC：

```json
{
  "action": "report",
  "goal_id": "goal-...",
  "report": {
    "expected_version": 0,
    "progress": "baseline",
    "summary": "首次对实际状态建立基线",
    "completed": [],
    "evidence": [],
    "blockers": [],
    "next_steps": ["第一个可验证的行动"]
  }
}
```

每次续跑开始时，主 Agent 先检查身份/Goal 状态，读取上次报告、当前任务和实际
产物证据，判断上一轮的变化，调用 report 保存后才继续工作。首次是 baseline，
后续为 advanced / unchanged / regressed。后续 expected_version 为上一报告 version。

唯一报告权威为 `.xdog/goals/<id>/progress.md`：同一个 Markdown 中包含可读说明及
结构化报告段，不双写 JSON/数据库。结构、首轮 baseline、CAS、文件锁、no-follow、
原子替换和有界读取由代码保证；语义进展由主 Agent 判断。损坏、权限、写入失败、
版本冲突和容量错误都明确失败，不能静默丢报告或继续声称已记录。

主 Agent 比较连续报告。多次无实质进展且没有新的可行方法时，先保存报告，再
主动 `goal pause`，说明已尝试的方法、阻塞和恢复条件并告知用户。不设固定轮数
自动判停，不以测试数、消息数、文件数替代实际需求进展。

## 暂停是明确边界

- 主 Agent 可以主动收紧 active/waiting(tasks) 为 paused，但不能凭自动输入恢复。
- 受保护 user/host wait、已暂停 Goal 和用户目标修改仍保留授权围栏。
- paused 后 Goal runtime 不主动续跑，并停止该 Goal 自己的 Eyes registration。
- 只有显式 resume 才恢复观察及提醒；重新读任务和报告核对期间变化。
- 不终止当前工具/children、不撤销已被 Inbox 接受的输入、不影响其他 observers。
- 旧排队提醒必须先查当前状态，不能用旧指令绕过暂停。
- native 命令沿宿主安全点执行；反馈不冒充已经中止当前工作。

## 按需读取与任务更新

`/goal status` 与 model status 默认是摘要：current、任务分类计数/有限预览、
latest_report/report_path、execution、automation、delivery 和解释性 next。
active 不表示 running；观察健康和传输状态分开。读取状态不探测网络或创建模型。

`goal(action="tasks", task_id=... / status=... / worker=...)` 可读单项或筛选分页；
`goal(action="plan")` 只读计划，给 content 才写。续页 hash 绑定实际文件内容及
原查询条件，变化或切换查询不能混页。SDK status(detail=True) 保留完整合同。

worker 可通过原权限门控的 filesystem/bash 调用
`python -m xdog.skills.goal.task_cli ... list|update`；复用 TaskFiles CAS/原子写和
单任务单写者协议，不引入 DB/RPC，不赋予执行权限。报告只由主 Agent 通过绑定的
Goal report 工具提交。任务 JSON 始终是任务唯一权威。

## 验证与保留

要求当前 Linux 域/并发/真实两宿主/传输/关闭测试、完整九包回归、Ruff/strict mypy、
Flow 示例、锁文件和适用构建及非 editable 安装。非 Linux、真实付费 provider 和
微信网络不声称验证。本轮不发布/tag，不重启用户 gateway，保留前序及无关 dirty。
