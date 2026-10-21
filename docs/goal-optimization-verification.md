# Goal 可靠续跑与进展报告：最终验收

日期：2026-10-21。对应 `goal-optimization-requirements.json` 的 GO-01..GO-09，
承接简化版 GR 基线，不重复计数。当前 Linux；未提交、推送、发布、打 tag 或重启用户 gateway。

## 结果

- **9 项完成，剩余 0 项**；待实现 0、待验证 0、缺外部环境 0、延期 0。
- 最终完整九包：**7770 passed / 7 skipped，172.88 秒**。
- Ruff 通过；strict mypy **611 source files** 通过。
- Flow 示例 **17 passed**；`uv lock --check --offline` 通过。
- **10 个项目、20 个产物**构建与 Twine 通过；Site 既有描述元数据 warning 不作无 warning 声明。
- **9 个非 editable 安装环境**逐文件核对实际安装内容、最终 wheel 及当前源码/资源。
- 额外 8 个最终安装消费场景通过，包含两个真实宿主的私有 ACP 续跑及 Goal + Eyes 实际文件检测。
- 无关 9 文件（含 workflows 原 pycache）与此前基准 SHA-256 一致，HEAD 保持 `997a048`。

持久证据目录：`/home/zhang/.cache/xdog-goal-optimization-20261021/`。
旧 `/tmp/xdog-goal-optimization` 随宿主重启丢失，不能作为最终通过依据。本次重新从
实际工作区构建、安装、验收；不因旧日志/进程丢失而推断成功。安装环境和消费脚本均在
独立缓存目录，不使用当前用户服务、真实模型凭据或微信网络。

## 逐项证据

| ID | 行为与验收依据 |
|---|---|
| GO-01 | `test_reminder_coalescing.py`、`test_reminder_review.py`：合并两类原因、繁忙延后、原 accepted/pending 跨恢复背压、发送期间新增变化不丢、计划版本竞争不丢 Eyes 通知。真实两宿主 ACP 与 Eyes live 消费场景验证实际通道。 |
| GO-02 | 两宿主 `test_capability_execution_recovery.py` 与 store/runtime 测试：持久 started/settled 事实、真实执行顺序、失败/中断/旧格式 unavailable、回执写入失败、不重放、取消结算、原 owner/namespace/branch。创建及显式 resume 的提醒原因与状态原子写入；已 active 的 Goal 也能显式唤醒。 |
| GO-03 | `test_status_reads.py` 与真实 `/goal status` 消费：current、分类计数、最新报告、观察/投递/执行/下一步分开；读取不探测网络、不改变状态、不启动模型。 |
| GO-04 | `test_task_cli.py`、`test_files.py`：原子 CAS 更新、真实子进程/文件锁、冲突拒绝、路径及 no-follow 边界。CLI 仅 list/update，不承接主 Agent report 权限。 |
| GO-05 | `test_status_reads.py`：默认摘要不读计划正文；只读计划、单任务和组合筛选；续页内容 hash 与完整查询条件绑定，修改文件/切换筛选不能混页。完整 SDK status 合同保留。 |
| GO-06 | review race 测试覆盖 claim 前后及最终读取期间 pause/owner retirement；最后读取使用变更 generation 围栏。真实 Eyes 场景证明暂停关闭自有观察、期间文件更新不主动投递、恢复核对现状，同时无关 scheduler 保持运行。accepted 输入/工具/children 不被当作可倒退撤销。 |
| GO-07 | `test_progress_reports.py`、`test_status_reads.py`、两宿主 `test_goal_progress.py`、冷安装消费：绑定的主 Agent `goal(action="report")`，先 baseline 后对比报告；同一 progress.md 中可读/结构化历史，CAS/真实进程锁/原子写/损坏及超限保留；写入故障不假成功。 |
| GO-08 | skill、两宿主 main Agent report→pause 场景：无进展和恢复条件由主 Agent 判断；暂停不冒充完成；自动输入不能 resume、不能把受保护 wait 洗成 tasks wait；没有固定轮数或脚本进展判停。 |
| GO-09 | 最终九包、静态、示例、锁、20 产物、9 安装环境、8 实际安装消费及 dirty 对照，详见证据索引。客户端只增加通用执行事实，不含 Goal 业务调度/规划/验收。 |

## 最终命令与证据文件

```sh
XDOG_MCP_OFFICIAL_SDK_ROOT=<证据目录>/mcp-sdk uv run python scripts/test.py --jobs 4
uv run ruff check packages
uv run mypy --strict -p xdog.ai -p xdog.agent -p xdog.tui -p xdog.flow \
  -p xdog.site -p xdog.claw -p xdog.coding -p xdog.eyes -p xdog.skills
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
uv lock --check --offline
uv build --all-packages --offline --out-dir <证据目录>/dist
uvx --offline twine check <证据目录>/dist/*
```

- `final-regression.log/.exit`：最终完整九包，不能用之前失败尝试替代。
- `ruff.log/.exit`、`mypy.log/.exit`、`examples.log/.exit`、`lock.log/.exit`。
- `build.log/.exit`、`twine.log/.exit`、`dist/`、`artifacts.json`。
- `cold-verify_{goal,coding_goal,claw_goal,acp}_artifact.py.log/.exit`：独立安装及既有传输验收。
- `installed-environments.json`、`installed/*/payload.json`：实际非 editable 字节、wheel 与源码对照。
- `runs.json`、`run-*.log`：Goal-only 跨进程报告读取；两宿主默认安装/生命周期；真实
  native create→ACP→主 Agent report/工作→settled→第二次 report/complete；
  Eyes 真文件变化→Goal coalescer→原 ACP 主 Agent，以及暂停/恢复和无关观察独立性。
- `source-freeze.json`、`dirty-preservation.json`、`final-worktree.patch`：冻结和保留证据。

## 回归中发现并处理的边界

没有放宽产品权限或测试断言来换通过：

- transport receipt 的 `message_state` 与 inbox status 的 `status` 是不同字段。
- pause/retire 发生在本地 claim 后或最终 DB 读取期间，不能穿透成新传输；已经开始的传输
  仍按真实原 ID 结果结算，不宣称可以撤回远端效果。
- continuation epoch 不会隐藏仍 pending 的旧 accepted 输入；也不会永久重施用户已明确恢复前的旧失败。
- waiting(tasks) 不能因每次自然结束反复唤醒，也不能丢失进入 wait 前刚完成的 worker 通知。
- 复核修正了一个 TUI 测试的单帧假设：EventQueue 本来每帧有数量/8ms 预算。该测试现在
  强制一帧一个事件，跨帧排空已发布快照，保留工具只显示一次、成功结束及旧 generation
  不污染的全部断言。未放大生产预算、未加 sleep/更长超时、未删除或跳过测试。

原失败日志 `regression.log` 和单独 `frame-regression.log` 保留；前者不作通过证据。
重建 MCP 独立 SDK 使用 npm 离线缓存中锁定的 2.3.1，只供测试，不添加生产依赖。

## 语义与范围限制

进展是主 Agent 的语义判断：skill 和续跑提醒要求先提交报告再工作，SDK 校验结构及一致
持久化，不宣称能用代码证明模型已正确理解任务。多次无进展由主 Agent 主动 pause，
没有固定阈值。暂停后不主动续跑并停止自有 Eyes；明确 resume 才恢复。

报告/文件读取有明确容量边界，超限报错而不是截断；宿主退出后没有独立 daemon 继续
运行。旧无执行时间/顺序的回执明确 unavailable，不回填假历史，不自动重放副作用。
工具权限、readonly、审批、子 Agent 及已接受输入仍归实际宿主所有。

非 Linux 原生、真实付费 provider、真实微信网络均不在本轮验收范围，不声称通过。
7 个 opt-in 外部原生 CLI 检查没有启用，不算通过，也不替代当前 Linux 的已要求功能验证。
