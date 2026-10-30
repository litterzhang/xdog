# Claw 身份、权限与共享上下文验收

日期：2026-10-30。设计和配置见 `claw-input-identity-design.md`。
早期 XHS proposal 及按受众隔离历史的方案不代表当前实现。

## 当前职责

- `core/identity/{models,resolver,permissions,context}.py`：冻结的身份/来源、精确绑定、
  group 工具 profile、当前模型身份说明；无 audience/visibility 访问控制。
- `core/runtime/{input_adapter,identity_scope,session,session_tools,orchestrator}.py`：
  输入入队/恢复、角色撤销、实际模型工具清单、pre-tool 和同步 executor-entry 门禁。
- `core/runtime/child_agents.py`：子 Agent 原输入身份权限子集及持续复验。
- 现有 transcript/memory/compaction/handoff：一份 group 上下文、daily log 和长期记忆；
  没有独立公开摘要、受众 capsule 或按 channel/account/post 裁剪消息的逻辑。
- `channels/accounts.py`、`channels/xhs/`、`cli/xhs_login.py`：显式登录与账号绑定、通知输入、
  固定原评论的显式原帖读取、冻结回复/发布目标。
- `core/tools/tool_message.py`、`runtime/outbound_messages.py`：显式发送/发布，复用 output journal。
- Agent SDK 只提供通用 custom-history validator、队列上下文与同步 execution guard；
  不包含 Claw/XHS 身份或业务权限逻辑，不通过快照恢复回调/工具权限。

## 模型回复与硬权限的区别

所有身份使用同一 group 的正常系统提示、已选技能、长期记忆和普通历史/摘要。
宿主通过 system prompt 的当前身份及逐条 actor 标记要求模型区分说话人，外部回复不得
泄露管理员资料、其他人的私聊或宿主信息；既有 group 人格无需另建 PUBLIC.md。

**上述回复规则不是硬隔离保证。** 模型可以看到共享资料，宿主不再过滤。工具权限无法
阻止模型在回复中泄露已经看到的文字；这里接受的是模型遵守指令的行为边界。

以下仍是宿主硬约束：

- admin/system 也受配置的工具上限限制。外部文本/角色标签不能改变授权。
- 原输入的冻结上限与最新实际权限取交集；准备后、实际执行前撤权仍阻止执行。
- 子 Agent 只继承原主体真实工具子集；不同发言者不替换已经接受的原授权。
- 管理 steering/命令与 ordinary input 保持原执行所有权，不借共享上下文或旧消息升级权限。
- readonly、MCP、真实工具身份/注册、原输入及账号绑定门禁保持有效。
- 原始回复目标冻结，目的地撤销不重定向；unknown 发送不自动重放。

## 日志、压缩和轮换

DailyLog/长期记忆的数据格式、路径和工具机制未改；轮换/压缩前继续按现有规则检查值得
保存的新事实。提示词补充保留主体/来源和已验证程度，不把不同人的事实混成“用户偏好”。

Compaction 使用一份 group 摘要及近期完整消息，原文检查点/归档仍保留。切点保留与真实
输入配对的 actor。轮换仅使用原有 handoff，含摘要、近期原文引用和维护状态，无第二套
公开交接或受众存储。摘要、记忆、历史工具结果均不是新的用户授权或执行成功证明。

## 离线验收覆盖

- 精确账号/发送者绑定、未知发送者降权、ACP 不取得人类确认权、旧无身份输入不静默升级。
- profile 配置往返/格式错误/重复/未知引用；原角色分支与 user_tools 移除。
- admin/system/external 的实际清单、prompt 工具说明及 executor 权限一致。
- 并行准备期间撤权、回合中禁止扩权、子 Agent 原权限及不同发言者间的授权隔离。
- 不同渠道/账号/帖子发言都进入现有 group 上下文；外部输入可见管理历史/长期记忆，
  但仍无默认工具。这是明确验证共享设计，不把它当作硬保密测试。
- 实际压缩使用完整 group 历史并保留普通摘要；轮换保留单一 handoff 和原文，文件记忆不丢失。
- 现有 rotation 完整闭环继续通过 memory 工具写 daily log 并检索原始授权记录。
- XHS 原帖仅显式获取、readonly 下可读、令牌不交给模型、错误帖子/缺令牌/账号切换拒绝，
  过期 reader 失效。结果进入普通共享历史，不再测试已移除的受众结果隔离。
- 微信回复固定原 sender/context；主动通知只选已验证唯一 owner，不用最近联系人。
- XHS 基线/去重/ACK 丢失、关闭 replies 不积压、只发最终回复、发布资源冻结/越界拒绝、
  HTTP 成功仍是 unknown 而非平台送达证明。

## 验证记录

当前修改的首轮定向回归：76 passed，包含身份/配置/原帖/共享上下文与轮换日志闭环。
最终整包与静态检查结果在本文件后续记录；不把早期受众隔离版本的通过结果作为当前验收。

测试使用合成模型与 MockTransport。Claw 测试夹具隔离并清理私有 Eyes daemon，不操作用户
共享服务。未登录真实账号、抓真实帖子、真实回复/发帖、重启共享 gateway/daemon、提交或推送。
真实平台环境、验证码、账号限制及最终渠道投递仍需单独授权实测。

最终验证：

- `uv run pytest packages/agent/tests packages/claw/tests -q -n 2`：4328 passed，15 skipped。
- 定向回归：76 passed。旧的受众隔离用例已由共享上下文/单一交接摘要的验收替换，
  身份撤销、工具门禁、steering、原帖目标与令牌约束等安全断言继续保留。
- 全仓 `ruff check packages` 通过；九包 strict mypy 通过（681 source files）；
  `git diff --check` 通过。
