# 子 Agent 宿主运行时重构：实现与验收

**验收完成：SA-01～SA-10 共 10 项完成，剩余 0 项。最终 review 发现的风险已修正、定向复现验证并重跑完整回归。**

日期：2026-10-21。实施基线：`ff653e44abb8279a456fbc60389186cdad0597fa`。
需求唯一清单：`child-delegation-requirements.json`。审计依据：`child-delegation-design.md`。
实现验收阶段未 bump、commit/push/tag、发布或重启 gateway；工作区版本仍为 2.4.4。
验收后按用户新增授权提交并推送 main；不改版本、不打 tag、不发布、不重启服务。

## 1. 最终代码边界

| Package | 实现 |
| --- | --- |
| `xdog.agent` | `supervision.py` 的 ChildBackend/ChildClient/ChildHandle、数据协议/模型工具 schema；`supervision_policy.py` 的 ParentHandle/纯委派检查/只读 skill 选择；通用 Agent 单实例 runtime 仍在原位 |
| `xdog.coding` | `core/child_agents.py` 的 CodingChildren；同目录 child_runtime/store/extensions.py 拥有任务、存储、worker、图片返还租约和清理 |
| `xdog.claw` | `core/runtime/child_agents.py` 的 ClawChildren；同目录 child_runtime/store/extensions/returns.py 拥有对应生命周期 |
| 仓库测试 | `test_support/child_runtime/` 单份旧验收契约，由两个真实宿主分别收集；生产包不导入测试支持 |

Agent 中的旧 ChildSupervisor 及 supervision_store/extensions/return 域运行时模块已删除，
没有兼容别名反向导入宿主，也没有把旧类移到 Agent 的 hosting 子目录。
通用 `settle_on` 归 `xdog.agent.cross_loop`，Goal UI 等调用方不再借子运行时模块引用它。

### loop 的具体选择

- **Coding**：保留由 CodingChildren 管理的专用 child 线程/loop；它不在 Agent SDK 中。
  child 不依赖某一次 UI `asyncio.run()` 或父 turn 存活，关闭必须真正结算并 join。
  本次没有把它合并到 SessionController，避免把边界修复扩大为 SDK owner 释放时机重写。
- **Claw**：借用 gateway/application 的原宿主 loop，不创建子服务线程，不把借用的 loop 当成自己的资源停止。
- 关闭视图/连接不等于取消任务；整个 Coding 进程/session 或 Claw application 关闭仍需完成实际清理。

## 2. 已接入的行为

1. Child 持有宿主注入的真实父句柄；不从 parent_id、role 或快照恢复授权。
2. tools/skills 越界在返回 child ID、创建 registry/task/worker 之前整体拒绝。
3. 冻结工具来源 identity、schema 和 action 子集；父新增能力不自动扩大旧 child。
4. 每次准入、异步审批之后、实际 executor 入口再次检查。并行批次中已准备的第二个调用不能借用撤销前的批准。
5. Coding 的 child 审批与父、兄弟独立；无审批入口拒绝。Claw 保留 readonly/MCP/extension 当前策略。
6. Child 无父 history/system/input/context transform；独立 system prompt，任务与材料通过首条 user 输入交接。
7. skills 仅注入选定目录和内容，只读 list/load，不提供父完整可写 manager。
   默认 SkillManager 使用 bounded/no-follow reader；FIFO/symlink 不会变成无限读取。
8. Coding 主聊天展示真实双向消息：稳定 ID、原 owner/lease、去重、方向与可查完整正文；工具前正文标记 received，不伪装 child 完成。
9. 独立 model/thinking 使用实际 adapter metadata；工具/图片/容量检查和归档保留。模型不可用/撤回不静默 fallback；不剥离 child 自己的有效签名。
10. 保留完整 paired history、分片存储、write-before-ACK、图片两阶段返还、压缩前归档、取消等待与 crash interrupted 不重放。
    已开始且实际完成的副作用不能因为随后撤权而伪装回滚。

旧 `instructions` 不再进入 system，也不静默丢弃：运行首轮与初始任务恢复均使用 SDK `child_task(spec)`，
将它明确作为 user 材料。旧 inherit_context=true 只作为历史数据可读，不允许新工作重新继承父历史。

## 3. Linux 完整回归

```sh
XDOG_MCP_OFFICIAL_SDK_ROOT=/home/zhang/.cache/xdog-goal-optimization-20261021/mcp-sdk \
  uv run python scripts/test.py --jobs 4
```

**8435 passed，7 skipped，0 failed；九个配置测试路径全部完成，194.87 秒。**

| 包 | passed | skipped |
| --- | ---: | ---: |
| ai | 738 | 7 |
| agent | 1840 | 0 |
| flow | 813 | 0 |
| coding | 1889 | 0 |
| claw | 1848 | 0 |
| tui | 712 | 0 |
| site | 42 | 0 |
| eyes | 326 | 0 |
| skills | 227 | 0 |

7 个 skip 是原有外部 Codex/Claude CLI 显式 opt-in 集成用例，未声称通过。
官方 MCP SDK 环境已启用，不用缺 SDK 跳过代替验证。本轮没有新增跨平台验证或支持声明。

共享旧契约不是删弱后凑数：原有 203 个测试函数有对应映射，两个宿主各收集相同契约。
线程断言按真实 owner 分别检查：Coding 实际 join；Claw 实际结算任务、writer、close future 且原 loop 仍运行。
SIGKILL/SIGTERM、队列 ACK 丢失、图片身份、工具 pairs 与原副作用不自动重放仍由真实进程检查。

另有真实 extension worker 正/反例：两个工具都完成准备后，第一个撤销 extension allowlist，
第二个已准备 alias 不得执行其写盘回调；未撤权时同一回调确实成功写入，避免因未接线而假通过。

### 最终 review 后的安全修正

首次整合全绿不等于没有边界问题。review 重新打开并关闭了以下路径，之后再次执行上面的完整回归：

- 重复 tool-call ID：在任何 validator/approval/effect 之前拒绝整个模糊批次，保存明确 provider error，
  不制造伪造 tool result，也不在可恢复 history 留下重复悬空调用。
- 两 host 的 effect admission 绑定实际 executor、原 ToolCall/assistant、参数对象与 canonical 内容、权限版本及一次性执行权，
  不能靠字符串 ID 覆盖、借用或重放授权；原 validator 与独立 child validator 在实际 effect 前重新检查，await 后复查权限。
- Claw 只等待自己创建的有限 admission task，不 join 可能在 finally 中等待同一个 aclose 的外层 requester。
- 显式 role capability bridge 最后检查原父、取消、运行状态和实际绑定，并保持原 schema/validator 上限。
- 两宿主接管并 drain 未投递消息/图片后实际调用 Agent.aclose，再 finalize；保留的 Agent 引用不能重新 prompt。
  close 失败仍保持 admission fence、执行其余清理并明确报告，不把失败说成已清理成功。

新增的真实 filesystem、独立 extension worker、隔离子进程关闭环、跨线程 role bridge 与 retained Agent 负例均进入最终全套。
源码复核在这组限定风险内未发现具体未关闭路径。

## 4. 静态、示例及产物

- `uv run ruff check packages test_support`：通过。
- 九包 strict mypy：**619 源码文件通过**。
- `uv lock --check`：通过；无依赖或版本变更。
- `uv run xdog-flow test packages/flow/examples/ --allow-script-stub`：**17 passed**。
- 根项目及九分发均构建 wheel/sdist，共 **20 产物**；Twine 全部检查通过。
  既有 xdog-site 长描述 metadata 警告保留，未扩为本轮无关修复。
- 对所有 wheel 中 Python 源码与当前仓库逐文件比对，未发现旧运行时代码或构建源漂移。

### 冷安装

均从上述 wheel 安装，使用独立 venv、`python -I`，没有 workspace/source-path 注入：

- **仅 ai+agent**：Coding/Claw/Flow/Eyes/Skills 均未安装时，Agent/SDK 可导入、构造、dump/restore；
  不自动创建线程/child/model；旧运行时模块不存在；父授权不进入或来自快照。
- **九 leaf 分发**：两个实际宿主都可创建、运行、等待和关闭 child；真实宿主 runtime 类型正确；
  使用与父不同的 model/thinking，只有显式任务/选定 skill 进入上下文；越父 tools/skills 准入直接拒绝。
  Coding 确实在自己的 host thread 执行；Claw 确实在原 application thread 执行。
  新产物还实测拒绝重复 ID 批次，且保留的 finalized Agent 引用再次 prompt 必须拒绝。

## 5. 需求验收定位

| ID | 主要证据 |
| --- | --- |
| SA-01 | Agent test_child_sdk_policy；两 host test_child_host_boundary / test_child_runtime 的 admission/identity/epoch 用例 |
| SA-02 | Coding child approval/UI lease/parallel prepared execution；Claw readonly/MCP/extension effects；test_child_extension_grants |
| SA-03 | SDK catalog/只读/撤权/FIFO/symlink；两宿主 selected skills 与无完整父 manager 用例 |
| SA-04 | fresh-history 拒绝、父 secret markers/transform 负例；两宿主冷安装 |
| SA-05 | 共享 protocol 的独立 prompt、busy 拒绝、queued/stopped 更新及不重放；TUI child settings |
| SA-06 | child_task 首轮/恢复、actual admission、steer/follow-up receipts、图片内容一致性 |
| SA-07 | Coding test_child_messages、原 owner/UI lease、长消息详情；真实主界面接线 |
| SA-08 | 两宿主完整共享契约及关闭/取消/crash/queue/image/compaction；九包全回归与产物 |
| SA-09 | 各 host-owned runtime/store/extensions；bare SDK 冷安装无宿主依赖/无后台 runtime；实际 loop/thread 结算 |
| SA-10 | test_supervision_model、两 host 独立模型/工具/图片/容量用例、child 签名保留及冷安装 |

## 6. 明确不承诺的事情

- 不是 OS sandbox。Skills 子集约束 SDK 注入/加载；授予 bash/filesystem 后，仍按宿主原权限和共享 workspace 边界执行。
- 没有新增持久化事件日志或 UI 重启补发机制；真实 child 结构化历史仍持久保留。
- stopped child 的 prompt 更新是元数据操作，不自动重启模型、继续旧副作用或重放任务。
- 原父句柄进程内有效，恢复需由宿主重新注入实际授权；不自动接管旧 owner。
- 不提供 Coding 进程退出后常驻 child daemon，不新增 child 寿命/token/并发上限。

完整命令日志、产物 hash、cold 脚本/结果和受保护文件 hash 存放于：
`/home/zhang/.cache/xdog-child-delegation-20261021/`。
受保护 `docs/agent-ui-features.md` 和 workflows 的 9 个原文件均与开始前 hash 一致。
