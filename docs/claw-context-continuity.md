# Claw 会话轮换与记忆连续性

## 修复范围

自动 daily/idle 轮换和原生显式新建上下文共用交接流程。Claw group 仍只有一个主 Agent；
旧 session 是只读来源，不是可切回的执行路线。没有增加调度器、后台 Agent 或 Goal 状态副本。

之前有两个确定的代码缺口：轮换直接创建空 JSONL；记忆搜索的「历史」主要是压缩时生成的
Markdown，不包含普通轮换保存的权威 JSONL。提示词不能弥补这两个存储/宿主缺口。

## 三类数据各司其职

| 数据 | 职责 |
| --- | --- |
| 原始 JSONL、压缩恢复分支 | 完整聊天证据与工具调用/结果，不重新执行，不恢复历史权限 |
| `xdog.claw.handoff.v1` 交接 | 当前工作、确认范围、未完成事项、阻塞和来源引用；不是新指令或授权 |
| daily / MEMORY.md | 重要事实、决策、偏好、非显而易见的经验；不是恢复工作队列 |

现有权威任务文件保持权威；交接只引用它们，不复制一份任务状态。每日/长期记忆不是交接的成功门槛。

## 轮换提交

1. 保持原 group 执行/准备锁，确认原主 Agent 空闲、没有未结算历史、没有活动子 Agent。
2. 在已有 IO worker 读取原始 JSONL、源摘要和精确消息位置；已恢复的内存历史必须与持久源一致。
3. 用当前选择的模型和原 stream 执行静默记忆维护，只提供当前确实允许的 memory 工具。
   记忆工具被禁用时不自动恢复它。冷恢复也尊重保存的模型、reasoning 和空工具选择；
   未恢复的扩展 provider 不退回默认 provider；冷路径若原扩展 hooks 尚未恢复，显式标记维护
   不可用并保留原文兜底，不绕开 veto/privacy hooks 调用基础模型或执行器。
4. 复用已有工具循环维护 daily/MEMORY，并做一次不提供工具的交接摘要请求。维护与摘要各有
   60 秒上限，调用方取消原样传播；模型失败不是要求用户重新讲述整个任务的理由。
5. 摘要失败/不可用时保存有限近期原文预览、源 session、消息位置和源摘要，标记 `recent_only`。
   连续安静轮换保留有限原始引用，不递归堆叠摘要、不丢掉最初的来源。
6. 通过既有 undo journal 在同一事务内写入新 JSONL 首条交接、catalog 和当前指针。
   写入失败回滚；源内容在摘要期间改变则拒绝提交。绝不以空白上下文掩盖不可用历史。
7. 首轮恢复自动读取这条规范自定义上下文，投影时明确标注历史与非授权边界。它不会被当成人类
   输入事件，不触发已接受工具或任务的自动重放。后续压缩也保留 handoff 与规范 ACP 输入内容。

底层同步组合/持久化 helper 无法执行异步维护，仍保存确定性的原文兜底；产品路由使用异步流程。

## 维护权限与结果

轮换维护只暴露 memory，不提供 bash/message/子 Agent 等操作。读取只限 MEMORY.md 和 memory/；
写入只限工具自己的 daily/MEMORY 入口。原有宿主 veto、参数重写和真实执行器身份校验仍生效，
**参数重写后再次验证有效参数的维护范围**。只读模式下普通工具调用不能借维护标记获得写权限。
该标记是宿主内部调用参数，不是模型可以提交的工具字段。这里是操作协议边界，不是 OS 沙箱；
受信工作区与底层文件系统仍按宿主既有策略管理。

`memory_status=checked` 只说明维护检查成功，不声称创建了日志或完成了任务。只有有值得保存的新事实
时才需要写入，避免强制写空日记/工具流水。失败单独记录为 failed，交接仍能提供来源。工具实际写入
仍通过原来的 DailyLog/LongTermMemory；不建立另一份记忆存储。

## 搜索与证据回读

`memory(action="search", query=...)` 同时返回普通记忆结果和当前 group 的权威 JSONL 历史结果。
JSONL 读取使用现有 store 的提交锁，直接读当前数据，不依赖向量/Markdown 索引跟上归档：
返回 `storage: live canonical JSONL` 和 `index_mode: not_required`，不伪造索引已完成的进度。

需要不依赖关键词的兜底时：

```text
memory(action="history", mode="recent")
memory(action="history", mode="search", query="delete_repo")
memory(action="history", mode="get", session_id="...", position=0)
```

- 优先遍历明确的 previous_session_id 链，再查看同 group 其他历史。
- 包含压缩恢复分支、工具调用参数和工具结果；返回 session/branch/message 位置及调用 ID、工具名、
  错误标记，方便关联。不会把图片原始字节或私有 thinking 当成搜索文字。
- 返回项/字符数/扫描数有界；`next_offset` 和 `next_char_offset` 允许继续读取，不把截断冒充完整原文。
- 列表是实时分页，不是快照；并发追加/压缩可能改变当前上下文的 offset。引用关闭的旧上下文和具体
  原文消息核验重要范围；不能仅依靠一段搜索预览作出删除决定。
- 跨 group、过期 reader、不可用文件明确拒绝/报错；检索未命中不证明「不存在」。
- 新 reader 由当前主 Agent 宿主绑定；不会因为子 Agent 继承工具名就自动复制父 Agent 的对话服务句柄。

## 审计与验收

新上下文 catalog 保存 previous_session_id 和 handoff 元数据：旧/新 session、UTC 时间、
`daily_reset` / `idle_reset` / `manual_reset` 等原因、摘要状态、memory_status、源消息数与 digest。
`/sessions` 的现有条目可显示来源/轮换原因/交接状态，主 Agent 也可通过 history 读取。
旧记录没有的原因不反向猜测或伪造。

离线集成验收使用真实 Orchestrator、Agent 工具循环、存储和 memory 工具：
确认两个 GitHub 对象 → 权限不足、未执行删除 → daily 轮换与记忆写入 → 用户要求开代理继续 →
新主 Agent 获取交接 → 查最近历史并准确读回原始用户范围。测试不连接真实 provider，不执行 shell
或仓库删除。它验证数据/权限链路，不承诺所有真实模型都能正确作出语义判断。

另外覆盖：摘要失败、写入回滚、源变化拒绝、取消不切换、未持久化历史拒绝、连续轮换引用稳定、
ACP 内容不丢失、跨 group/陈旧 reader、参数重写不能突破维护范围，以及后续压缩保留交接。

## 本次验证（2026-10-28）

- `uv run python scripts/test.py`：九包全仓 **10,348 passed、34 skipped**，383.61 秒。
- 追加冷路径 hooks 保护后，最终 Claw 全量 **1,997 passed**，148.24 秒。
  两轮有重叠，不直接相加宣称独立测试总数。
- `uv run ruff check packages` 和九包严格 mypy 均通过；mypy 检查 636 个源文件。
- Claw wheel/sdist 构建成功，wheel 确认包含全部新增交接/历史/维护范围模块。
- 未连接真实模型、执行删除或改写用户现有历史来验收本次场景；没有自动重启 gateway、提交或发布。
