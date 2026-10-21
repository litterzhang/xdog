# 子会话续聊与按需执行（2026-10-21）

## 边界与命名

- `xdog.agent.children`：惰性的 `ChildBackend` / `ChildClient` / `ChildHandle`、工具 schema 和数据协议。
- `xdog.agent.child_*`：配置、父句柄和委派校验、模型元数据、图片和队列数据操作；不管理子执行生命周期。
- Coding 的 `CodingChildren.runtime` / `CodingChildRuntime`、Claw 的 `ClawChildren.runtime` / `ClawChildRuntime`：各宿主自己的准入、工厂、运行、持久化及资源清理。
- 不提供旧 `xdog.agent.supervision*` 模块兼容壳；它们既不应被理解为运行时，也不应继续作为新代码入口。旧审计文档与测试文件名中的历史记录不是当前 API。

## 一轮完成不等于删除子会话

子会话 ID、独立消息历史和执行资源是不同对象。

1. 新建时，只从真实父句柄捕获允许委派的工具/skills，不继承父对话或 system prompt。
2. 本轮完成后捕获配对完整的历史、通用 Agent 快照元数据、有效运行设置和压缩状态；将未接纳输入单独保留，不自动执行。
3. 永久 `Agent.aclose()`，关闭本轮独立扩展和权限管理器；保留旧 Agent 引用也不能再次 prompt。
4. Coding 没有工作、准入、工具/持久化或桥接调用时，退出本轮子执行 loop/thread。下次操作可创建新一代执行 loop；前一代必须已实际退出，不并行借用退役 loop。
5. Claw 使用 gateway 原有 loop，收束本轮 Agent/任务，不停止宿主 loop，不增加维护空闲子 Agent 的线程。
6. 显式 `steer` / `follow-up` 发给一个安全完成的子会话时，先写入新输入的保守恢复记录，再新建 Agent，注入真实父权限/工具/客户端，恢复原历史，执行**这条新消息**。
7. 运行中的子 Agent 仍使用原来的 steering/follow-up 队列，不额外创建第二个 Agent。

注册表/历史仍存在，不代表 Agent、扩展 worker 或执行线程一直存活。`wait` 等待的是当前这一轮；结果仍使用原 child ID，calls/tokens 为累计值。

## 安全与恢复

- 自动启动、重放旧任务、重放未确认完成的工具都不属于续聊。
- `failed` / `canceled` / `interrupted`、未知输入状态、未配对历史不得借一条 follow-up 自动恢复执行。应检查或协调旧结果，必要时显式新建子会话。
- 旧格式记录仅在已确认完成、read-only、不继承父历史、skills 明确为空、历史完整配对且模型可验证时，允许受限续聊；只保留历史成功执行过的原生只读工具及完整参数集合。不臆造未知 skills 或 compaction 元数据，其余旧记录明确拒绝，不承诺任意旧记录无损续聊。
- 历史完整参数对象的枚举是宿主执行授权上限，不直接作为模型函数定义发送。模型侧使用显式 `type/properties/required` 参数描述；字段之间的组合限制仍由宿主的原始精确 Schema 校验，不能拼接参数、换路径、增加动作或借父权限扩权。普通工具 Schema 不被此兼容投影改写。
- 快照不存 provider 客户端、工具 callable、凭据、锁、loop 或后台任务。通用 Agent 快照版本检查和恢复校验保持有效。
- 已冻结的工具名、动作限制和 Schema 是上限。宿主重新从当前真实父 Agent 验证工具，再施加旧 Schema；父新增工具或放宽 Schema 不扩大旧子会话。
- 新 runtime 的审批缓存为空，原子会话上轮的会话批准不是下一轮批准。父的当前审批模式、权限撤销、readonly 和实际效果入口复查仍然有效。
- 执行端只接受新工厂实际验证的模型；恢复不根据模型名添加 provider、认证或 fallback。必要的模型/skills 已不可用时明确失败。
- 新消息持久化失败不启动模型；写入途中取消/关闭禁止晚启动。工厂、模型或权限校验失败后，已接收的新消息仍可作为未投递输入检查。
- 大历史沿用原有分片，仅存一份消息；快照元数据与消息一起恢复。压缩 generation 保留，原始归档不覆盖、不重用旧代数。
- 图片仍受原单次/累计大小、真实模型能力及两阶段返还保护，不以文本代替图片。

## 同轮 UI 修复

共享 TUI 的列表解析保留段落边界，行内代码、强调与正文不再被误拆为独立行。两宿主使用同一紧凑工具标题/摘要：

```text
 › 用户消息

 • 正常排版的 Agent 回复，包含行内代码和强调。

 • Ran pytest -q
   └ result preview · hidden lines; Ctrl+O
```

成功不重复显示 `success · 0s`；运行、失败、取消仍明确可见。完整参数、结果、图片及详情入口保留，角色标记和摘要不写回模型消息/导出正文。

## 验收清单

| ID | 行为范围 | 可验证条件 | 状态 |
| --- | --- | --- | --- |
| CC-01 | SDK/宿主命名边界 | 当前生产引用为 children/child_*、runtime，无旧兼容模块；独立 SDK 不创建子运行时 | 待验证 |
| CC-02 | 安全完成的子会话续聊 | 两宿主原 ID、原历史 + 新消息；真实双向事件；每轮不同且已关闭的 Agent | 待验证 |
| CC-03 | 空闲执行资源释放 | Coding 线程实际退出、下一轮重新启动；Claw 原 loop 仍可用；取消/关闭不产生晚启动 | 待验证 |
| CC-04 | 持久接纳与防丢 | 写失败不调用模型；工厂/权限拒绝仍保留新消息；旧中断不重放 | 待验证 |
| CC-05 | 委派与审批上限 | 父新工具/放宽 Schema 不扩权；当前撤权有效；新 runtime 不继承审批缓存 | 待验证 |
| CC-06 | 快照、压缩和图片 | 重建 owner 不调用模型；后续显式输入才执行；压缩状态/归档代数连续；既有图片契约通过 | 待验证 |
| CC-07 | 两宿主共享消息/工具布局 | inline Markdown 连续，用户空行与 `›`、灰色 `•` 角色标记保留；紧凑摘要、真实终态、完整详情及终端边界通过 | 待验证 |

最终验证命令、数量和已知限制将在实现后的验证节补充；不得把尚未执行的检查标成通过。

## 现场问题修复（2026-10-21）

- 父子通信投影同样使用灰色 `•` 和统一三列 gutter，不写回原始正文或覆盖身份/fencing。
- `05e4d7c94a34` 的 `Invalid schema for function filesystem` 源于 legacy 的 object-enum 授权上限被直接作为 provider 参数 Schema 发送；本次在 SDK 提供数据级参数投影，两宿主统一采用，真实效果授权仍检查原始上限。
- 修复不自动重放现场已失败的输入，不更改用户的子会话 JSON 或状态。`failed/interrupted` 仍需检查与显式恢复决策；不能仅靠错误文本就判定全部旧工具都可安全重放。
- 验证结果将在针对性及相关回归结束后记录；未运行真实模型 API 的内容不得视为已在线验证。

本次定向验证：`test_child_continuation.py`、`test_child_provider_schema.py`、`test_child_sdk_policy.py`、`test_child_messages.py` 合计 122 passed；两宿主 legacy 真实工厂、从磁盘记录重建宿主 owner、实际 Responses 参数构造、两轮显式续聊和未授权新路径拒绝均有覆盖。四包 strict mypy（422 源文件）及本次修改文件 Ruff 通过。真实 provider 在线调用未执行，现场数据只读检查且未重试。

相关子 Agent/权限/生命周期和共享 TUI 回归：2143 passed（46.74s），日志 `/tmp/xdog-child-schema-and-markers-regression.log`。定向日志 `/tmp/xdog-child-schema-and-markers-tests.log`，类型检查日志 `/tmp/xdog-child-schema-and-markers-mypy.log`。
