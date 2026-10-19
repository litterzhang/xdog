# Claw → Eyes 托管观察器实现计划

## 当前决策与状态（2026-10-18）

状态：**已完成（complete）**。最新删除范围的8项验收全部完成：
待实现0、待验证0、已完成8、缺外部环境0、延期0，**剩余0**。
基线：`fcacaa4`。稳定编号、行为范围、验收、状态和证据以
`claw-eyes-scheduler-requirements.json` 为准。

用户最新决定**取代旧CS-07“迁移并删除旧引擎”范围**：

- 彻底删除Claw旧task scheduler实现，包括TaskScheduler、`task`工具、专属数据模型、
  运行时注入和导出；同时删除迁移模块、备份/转换/幂等标记、迁移存储兼容及确认状态流程。
- 无`tasks_file`配置或旧路径解析；无论Eyes是否启用，启动均不检查、读取、备份或导入
  `scheduled_tasks.json`，也不访问旧自定义任务路径。不因旧文件存在、损坏或不可读而影响启动。
- **用户旧数据保留，不删不改**；删除实现不等于清理磁盘数据，也不提供自动迁移或兼容运行。
  用户明确提出新调度请求时，通过Eyes工具新建observer，不把旧记录自动转换为Eyes任务。
- **保留Eyes scheduler source、goal runner周期及历史transcript标签**（如`scheduler`、
  `goal_runner`）。历史标签只用于旧会话读取/展示，不是旧调度执行入口。
- 旧interval导入为`needs_confirmation`的策略已废止，不能再作为当前支持能力或验收条件。

旧兼容实现及相关测试/文档已完成收口，并重新通过全部门禁。下方历史记录仅用于追溯，
不作为当前兼容能力或最新测试结果。

## 保持不变的架构边界

复用现有scheduler source/Detector/Router/通用ACP handler；Eyes不依赖Agent或Claw。
Claw负责群归属、借用既有inbox存储所有者、恢复、取消准入、执行/投递状态。
游标和不可变待发送消息一致落盘后才能发送；ACK丢失保留原ID/内容；未知副作用不重放。
独立Eyes默认生命周期不变。Claw显式启用托管后才恢复自己创建的时间observer；不自动恢复普通文件观察。
Eyes interval采用固定相位，恢复已有Eyes托管状态不等于读取或迁移旧任务文件。
工具显示不在本次范围；`eyes`工具仅做宿主范围管理，不引入第二Agent循环或额外daemon。

## 实施分工与完成条件

- Claw实现：删除旧调度及全部迁移兼容，保留用户数据、goal runner和历史标签；保持ACP、
  执行/投递、取消栅栏和gateway托管边界。
- Eyes实现：保留嵌入核心、严格时间检查点及managed scheduler actor，CLI语义不变。
- 文档收口：仅编辑本计划、需求JSON、根AGENTS.md、Claw README/SCHEDULER及Eyes skill。
  不编辑`docs/agent-ui-features.md`或`workflows/`，不改生产/测试/发布工作流，不commit。

最新CS-07必须验证：配置与符号/迁移路径删除；Eyes开/关均不访问任何旧任务文件；旧文件
存在、损坏、不可读等情况下不导入、不生成observer、不阻止启动，且用户旧数据原样保留。
还须验证Eyes时间源、goal周期和历史标签未被误删；不能把保留历史标签理解成保留迁移兼容。

已冻结源码/测试并执行完整门禁：全8包`scripts/test.py`（既有独立MCP SDK启用）、
Ruff、strict mypy所有8命名空间、Flow示例、锁校验、9项目全新构建和wheel-only/API/元数据验证。
不以删弱、skip/xfail或过滤安全、协议、终端、防丢测试换通过。准确披露可选外部CLI跳过。
**以下历史结果不替代本次重新执行的验收。**

未得到后续明确授权，不commit/push/bump/tag/发布、不重启用户服务或修改权限/凭据。

## 最新完整验收结果

证据：`/tmp/xdog-claw-eyes-retirement/summary.json`及同目录日志。

| 门禁 | 最新范围结果 |
| --- | --- |
| 全8包 scripts/test.py，独立MCP SDK启用 | **6355 passed，0失败，7既有可选外部CLI skipped；125.16秒** |
| Ruff全packages | 通过 |
| strict mypy全部8命名空间 | 556源码，无错误 |
| Flow示例 / 离线锁 | 17通过 / 通过 |
| 9项目全新离线构建 | 18产物 |
| wheel-only/API/源/README | 27模块、12项移除导入被拒、832源成员、8 README元数据通过 |
| 独立Eyes源及wheel隔离 | 不导入harness、不激活ACP，嵌入API导入/关闭通过 |
| 冻结输入 / 无关dirty | 1327个package文件、9份用户文件哈希保持一致 |

测试数较旧范围6411减少56，是删除明确废止的迁移用例并新增移除保护回归后的净变化；
不是剩余需求数。Eyes调度测试重命名为`test_eyes_scheduler.py`和`test_eyes_integration.py`，
保留时间计算、权限、取消、并发、跨进程恢复不重放及报告链路验证。新测试直接防止
开启/关闭Eyes的gateway启动/重启打开旧任务文件，并确认文件原样保留及goal tick继续运行。
CS-01..CS-08全部已完成，准确剩余0；没有通过skip/xfail或过滤保留功能换通过。

未commit/push/bump/tag/发布，未重启用户服务；未做真实微信/付费provider或非Linux原生验证。

## 历史证据：仅适用于最新决定之前的迁移实现

以下保留原记录用于追溯，**不是最新删除范围的通过声明**。历史阶段曾将CS-01..CS-08
记为8/8 complete、剩余0；其中旧CS-07验收了私有备份、幂等导入、自定义`tasks_file`、
旧interval确认状态等行为。这一范围已被最新决定取代，相关通过记录不再是当前验收条件。
旧迁移测试可作为历史证据定位，不要求继续支持已废止的迁移能力。

### 历史候选与审查检查点

此前Claw+Eyes预回归1046通过，Claw/Eyes strict mypy检查122源码与Ruff通过。
旧scheduler测试当时通过新的真实ACP/Agent/渠道链路迁移核心行为，含真实三进程一次性
恢复和不重放用例；该“测试迁移”不等于当前支持用户旧任务数据迁移。

历史只读审查的先红后绿修复包括：文件handler所有权泄漏、已排队消息namespace准入、
执行提交后发送前崩溃造成的报告卡住，以及JSON整数/浮点转换破坏不可变outbox。
历史实现还处理了空回复不冒充已发送报告、checkpoint写失败可见并停止。
这些记录不能证明后续删除兼容代码后未回归。

### 历史最终门禁记录

| 门禁 | 旧迁移范围冻结实现的历史结果（未在本次重跑） |
| --- | --- |
| 全8包scripts/test.py，既有获准独立MCP SDK启用 | 6411通过，0失败，7既有可选外部CLI跳过；123.23秒 |
| Ruff全packages | 通过 |
| strict mypy所有8命名空间 | 557源码，无错误 |
| Flow示例 | 17通过 |
| offline锁校验 | 通过，第三方依赖锁记录未升级 |
| 9项目全新离线构建 | 18产物 |
| wheel-only/API/源/README核对 | 28模块、833源成员、8README元数据，全部通过 |
| 独立Eyes隔离检查 | 不导入harness、不激活ACP；嵌入API可导入和关闭 |
| 冻结package输入 | 1367文件保持一致 |
| 用户无关dirty文件 | 9份哈希保持一致，未stage |

历史三进程证据覆盖：保存已接受时间任务→新进程恢复过期at执行并报告→第三进程确认不重放。
已执行但未发送报告可用保存结果恢复，不重跑Agent；发送中断的未知结果不可盲重发。
历史namespace检查覆盖连接、实际Agent准入和渠道发送；完整Delivery/JSON数值表示保持。
历史工具status记录原instruction、next_due及最近64项执行/投递；list是有标记摘要。
observer的completed只表示一次性时间源已耗尽，实际执行/投递分别看occurrences。
这些仍是保留的行为要求，最新实现是否满足须重新验证。

历史包装仅修改未来发布矩阵配置，没有commit/push/bump/tag/实际发布或重启服务；
未验证外部PyPI发布者、真实微信或付费provider。非Linux原生验证范围外；7项可选CLI跳过
不计为通过。无关文件的历史备份/哈希也仅属于当时冻结快照。

历史命令/产物/API/红绿证据保留于`/tmp/xdog-claw-eyes-impl/`，旧最终摘要为
`/tmp/xdog-claw-eyes-impl/final/summary.json`；该临时摘要未被本次改写，其中旧迁移策略和
complete状态已过时。JSON清单将这些内容标为historical evidence，最新final_validation使用本次独立重新验证的结果。
