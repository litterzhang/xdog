# 当前 Goal 目标（最新用户授权，优先于旧 session-owned 描述）

实现 Claw 的 group 主 Agent 管理 Goal、子任务及 Agent-only 结束验收门。

## 必须遵循的架构

- 每个 group 只有一个逻辑主 Agent，Claw 只维护一条执行路线。
- Goal、task、Eyes 通知归属该 group 的主 Agent；只使用现有 group ACP/inbox，不增加 session 定向地址或消息分发。
- session 是上下文及历史的持久化实现细节，不是另一个主 Agent 身份。上下文轮换不更换 Goal 负责人。
- 移除 Claw branch/fork/goto 分叉、回退及切换路线能力及相关入口；保留必要聊天、历史读取/普通导出和恢复；不改 Coding 分支能力。
- 主 Agent 自主规划、分配、等待、调整、取消和关闭子 Agent，不保留强制自动 planner 或另一套任务调度引擎。
- 移除 sub-agent 固定 timeout、模型调用数、累计 token、固定并发和记录数量业务限制，不换成隐藏运行上限；保留工具权限、模型实际上下文容量、协议大小保护、用户取消、宿主关闭和崩溃一致性。
- 主 Agent 单轮回复结束不关闭仍在工作的子 Agent；长工具循环复用原生 compaction，不能以累计历史预算暗中终止长任务。

## 功能与验收

1. 持久保存 Goal 原始条件、范围、修订、状态、任务分配与等待原因，目标跨上下文轮换仍由 group 主 Agent 负责。
2. Goal workspace 分清主 Agent、执行 Agent 和宿主汇总的单写者职责；任务具有稳定 task/attempt/revision。
3. 执行 Agent 原子维护自己的任务报告，报告 submitted 不等于负责人接受或 Goal 完成；校验身份、版本及迟到 attempt。
4. Eyes 复用既有 file source/router/ACP/inbox，文件事件只通知 group 主 Agent；重读当前文件、去重合并、防通知循环，来源内容不赋权。
5. 显式恢复 Goal 拥有的观察器并启动对账，不改变普通文件观察器默认不自动恢复的语义；持久通知与待办不丢失，不自动重放不确定副作用。
6. 完成申请及 Goal 工作正常停止安全点进入独立验收子 Agent；逐条件检查实际成果/证据，不靠文字关键词，不影响普通聊天，等待/取消/故障不是完成。
7. 脚本和测试只作为验收 Agent 取证工具，不直接判定 Goal 成功，无条件 auto-pass 删除；宿主验证验收归属、修订和成果新鲜度后才能提交完成。
8. 新增打包可发现的 claw-goals skill 及 worker/reviewer 说明，明确创建、维护 workspace、分配、更新任务文件、处理事件和验收；对子 Agent 显式注入角色规则，删除旧创建 Goal 后 STOP 的冲突提示。
9. 安全空闲才续跑，用户输入优先，等待或无进展不空转。权限/并发/持久化/验收边界由代码保证，不只靠 skill。

## 工作方式及完成条件

- 最多 3 个子 Agent，以不重叠写集并行；恢复后先读交接、实际 diff 和运行中的测试状态。
- 稳定编号及准确计数以 docs/claw-session-goals-requirements.json 为准；设计以 docs/claw-session-goals-design.md 为准。
- 当前文件名保留历史 session 字样不表示采用旧 session-owned 架构。本文件明确取代旧目标正文中精确 session 绑定、旧 session 停泊和多路线相关描述。
- 保留上一目标 Eyes 未提交改动、docs/agent-ui-features.md、workflows/ 和其他无关改动；不 stash/reset/clean/add-A，不中断正常推进的测试。
- 完成当前 Linux 的针对性生命周期/权限/真实 Eyes/恢复/验收测试，以及全部 8 包回归（既有独立 MCP SDK）、Ruff、8 命名空间 strict mypy、Flow 示例、锁、全部项目构建及 wheel/skill 检查。
- 非 Linux 原生验证不在本轮范围，不声称已通过。不自动 commit/push/bump/tag/发布，不重启用户服务或修改凭据/权限策略。
- 仅在清单所有本轮需求验收通过、完整门禁通过且准确剩余 0 时完成目标；未完成内容明确交接，不用测试数量代替需求数量。
