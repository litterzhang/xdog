# Coding 队列、会话权限及工具效果分类（2026-10-21）

## 本轮验收范围

| ID | 范围 | 验收条件 | 状态 |
| --- | --- | --- | --- |
| QP-01 | Esc 提前发送队列 | 只提升原生用户的 next-turn 输入；原 ID、顺序、图片和未发送草稿保留；仍是原 batch，检查点落盘后才允许下一模型请求 | 已完成 |
| QP-02 | 会话权限命令 | `/permissions` 查询/切换四种模式；仅内存当前会话，未知值不改状态；busy/审批/子执行不延迟提权 | 已完成 |
| QP-03 | 效果分类 | read/write/execute 与真实范围分开；Goal plan/report/task_update 在 ask 下免审批；只读/deny/ask-all 仍有效 | 已完成 |
| QP-04 | 外部消息布局 | 同列灰色 `•`，紧凑预览，不重复 Request；完整正文/证据/身份仍能 Details、transcript 导出 | 已完成 |
| QP-05 | 父子通信元数据 | 方向 accent，ID/label/status/message ID 使用 dim；正文、完整导出、去重和 owner fence 保留 | 已完成 |
| QP-06 | 子工具动作配置 | 无 action 的 bash 不能配置 `tool_actions`；创建前拒绝无效/通配符假设，不绕过父工具/动作/readonly/审批 | 已完成 |
| QP-07 | 完整回归 | 九个配置测试路径、Ruff、strict mypy、实际 PTY 和针对性检查点/失败测试 | 已完成 |

## 1. Esc 队列提升

主输入框忙碌时，按 Esc 把本机原生用户尚未开始的 next-turn 消息按队列顺序加入
原 Agent 的 steering 队列。不是重新 submit，不产生新 ID、新 Agent 或第二个执行循环。

- 审批确认、受保护弹层、详情/导航等仍优先处理自己的 Esc。键释放/重复不能触发提升。
- 有可提升消息时不取消当前模型或工具；没有时保留原有取消行为。
- 接纳到 steering 队列不等于已经运行：原 durable receipt 仍 queued/start_order=0。
  在通用 Agent 的 awaited input checkpoint 中，原消息的 running/batch 记录提交后才继续模型。
- 普通转向发生于安全检查点，不强杀当前模型流/有副作用工具。
- 主机/分支/generation、peer、人类 origin、原 owner 与当前活动 turn 均须匹配；
  Goal/Eyes/ACP 等外部输入不借 Esc 转成用户授权。
- 保留图片原始字节，不将其文字化；保留输入框尚未发送的草稿。
- 已提升项不能被再次提升。若原 turn 已结束，未接纳的消息仍留在原队列。
- 输入检查点写入失败则停止，不进入下一模型调用；失败/取消不自动重放不确定效果。
- 原生输入自身的 payload/图片/队列容量边界仍保留，超出可提升范围不删除原排队消息。
- 现有 one-at-a-time steering 配置不变：多条输入可对应原 batch 内多个模型轮次，
  不能用“模型调用次数”判断是否重新创建了多个外部 turn。

实现归属：SessionController 保留执行及原 receipt；NativeSessionEvents 只是投影与
用户控制入口，UI 的 `_pending_messages` 不重新取得执行权。

## 2. 当前 session 的权限模式

```text
/permissions
/permissions status
/permissions ask
/permissions ask-all
/permissions allow-all
/permissions full-access
/permissions deny
```

`full-access` 是 slash 别名，实际值为 `allow-all`。模式切换仅修改当前会话的
PermissionManager；不写全局/project 配置，不把快照作为恢复权限的授权。
模型、审批、子执行或上下文切换活动时拒绝切换，不排队保存一次将来的提权。
会话查询可读取当前值；切换仍经过原 owner/context 与子权限准入 gate。

## 3. 用户确认的分类维度

| access | scope | 含义 | ask 的当前策略 |
| --- | --- | --- | --- |
| read | workspace | 工作区内读取/查询 | 免审批 |
| read | outside | 工作区外读取/查询 | 免审批；执行工具自己的路径规则仍有效 |
| read | tool_data | 工具管理的自身数据/文件 | 免审批 |
| write | workspace | 工作区内写入 | 审批 |
| write | outside | 工作区外写入 | 审批 |
| write | tool_data | 真实工具绑定管理的自身状态 | 指定安全状态写免审批；需要确认的业务控制仍可要求审批 |
| write | external | 对外消息/提交等外部效果 | 审批 |
| execute | unknown | 通用执行、未可靠声明的效果 | 保守审批 |

- `ask-all` 保留显式全询问模式；`deny` 只允许读取，不因为 tool_data 放行写入。
  `allow-all` 关闭常规询问，但不取消 readonly、父子权限子集或业务所有权校验。
- Goal 的 `plan(content=...)`、`report`、`task_update` 已明确免除 ask 审批。
  其它 Goal 写操作保留原审批要求；其独立用户授权/暂停释放/目标所有权规则始终有效。
- 这里已接入 Coding 原生 filesystem/current_time、实际子控制查询及 Goal capability。
  未声明可信效果的扩展/MCP/其它工具继续归为通用执行，不能凭名字/readOnlyHint 自行免审。
- 文件范围按真实 workspace 与 `Path.resolve(strict=False)` 结果区分，包含符号链接外指。
  解析在既有 owned IO 上执行，不阻塞 Agent 的事件循环。
- 路径看起来属于 `.xdog` 不代表 tool_data。filesystem 写 `.xdog/goals/...` 仍按普通文件写审批；
  只有正在绑定的真实 Goal tool 对其受控存储操作，才可取得 tool_data 效果。
- Agent 的 ToolEffect 只有冻结数据及组合校验，不含 host 审批策略或 Goal schema。
  CapabilitySet 仅向原 live binding 的实际工具查询效果；同名伪造、复制或已退役工具不可免审。
- 参数经扩展改写后重新分类；等待批准后还会重查效果。会话授权规则不跨效果改变借用。
- 不将任意 bash 的 `sleep`、`pwd`、`cat` 推断为只读；通用 shell 不是受限文件 API。
  这些权限 gate 不构成 OS sandbox，也不能限制受准 bash 仅访问提示中指定的一个文件。

## 4. 现场 child 配置问题

`9abdffdf1bbb` 的原配置是 `tool_actions={"bash":["*"],"filesystem":["read"]}`，
但 bash 只有 command/timeout_ms/description，不是含 action 的多动作工具。
`*` 没有通配符含义；执行时拒绝是正确的，旧缺陷是创建前没有报出不可能使用的配置。
现在共享 ChildDelegation.capture 提前验证，并反馈正确格式：

```json
{"tools":["bash","filesystem"],"tool_actions":{"filesystem":["read"]}}
```

这不是新的授权：仍要满足父实际工具、动作、readonly 与当前审批模式。没有修改现场
child JSON、自动授予 bash、重放 sleep/task-cli 或重启用户服务。
child 的 completed 只表示该模型轮次结束，不表示业务任务成功或 task 文件已完成。

## 验证证据

- 核心集成及新效果策略：464 passed，`/tmp/xdog-effects-integrated-tests.log`。
- 队列检查点、重复提升、图片及失败停止：7 passed，`/tmp/xdog-queued-steering-checkpoints.log`。
- 第一次九包完整回归发现默认技能 fixture 与原 PTY 可视范围的四处旧预期，均保留并加强
  原技能绑定/turn 过期、restore 后写拒绝、粘贴/确认/resize/退出断言；没有删除安全测试或延长超时。
- 最终九包回归日志：`/tmp/xdog-queue-permissions-final-regression.log`：9222 passed、7 skipped，九包全部通过，225.26s。
- 全 packages/test_support Ruff 已通过；九包 strict mypy 620 源文件通过。
  完整日志分别为 `/tmp/xdog-queue-permissions-ruff.log` 和
  `/tmp/xdog-queue-permissions-final-mypy.log`；mypy unused-section note 不是错误。
