# 主 Agent Goal 重构验收

日期：2026-10-21；当前 Linux / Python 3.13.12。需求：`GR-01..GR-12`，见
`goal-simple-requirements.json`；设计：`goal-simple-design.md`。

## 实现结果

- Coding/Claw 默认依赖 xdog-skills，无需 skills extra、Goal enable 配置或 goal_ui 选择；包存在不创建 Goal/worker/model。
- 安装能力通过现有 ExtensionRuntime 贡献 native `/goal` create/status/pause/resume/clear/history；模型使用实际 owner-bound Goal AgentTool。只显式覆盖同名 skill 命令入口，技能仍可加载，不能覆盖宿主 builtin 或别的 extension。
- 首个 Coding slash 无需模型 warmup；Claw 普通真实用户频道输入也识别 native 命令，走原 Core receipt/output 路径；ACP/ExternalInput 中的同样文本不获用户命令权限。
- 当前主 Agent 按 skill 自行规划、选择现有工具/可选子 Agent、维护任务并判断完成。没有 Goal worker/reviewer runner、role tool、attempt/digest 验收或第二个模型循环。
- 每个真实 owner 至多一个当前 Goal（paused/waiting 仍占用）；完成/清除归档，历史只读，新目标不继承旧任务或完成标记。Claw group 稳定，Coding 精确 session/branch。
- 任务唯一权威为 workspace/.xdog/goals/<id>/tasks/*.json；主 Agent tool 更新用 expected_version，外部 worker 按 skill 原子更新其分配文件。DB 不保存第二份任务状态。格式/版本/IO 故障不是空列表。
- 通用 settled/available/retiring 事件只陈述执行事实；Goal 决定是否发送提醒。提醒通过原 ACP enqueue，原 ID/内容/namespace/branch 校验；未获回执不能换 ID 盲重试，不调用 Agent.prompt 另开执行器。
- user/host wait 和 user pause 不能洗成 task wait 再自动恢复。旧 parent/错误 loop 的工具拒绝，真实存储故障传播；发送前原子 claim 与 pause/version 竞争有边界。
- Goal 生成 Eyes 配置并只拥有自己的任务目录观察。缺 Eyes/真实 ACP 地址时明确 unavailable；不安装包、不启动外部 daemon。开始必须等初始 baseline 真正 ready，避免快 worker 的第一次完成被吞为初始基线。
- pause/clear/close 不撤销已接受输入、终止 unrelated child 或其它观察。关闭结算实际 startup/IO/callback/观察/通知，独立错误不会跳过剩余清理。
- .xdog 属本地状态，XDOG 发行配置显式排除，skill 要求不默认提交/发布该目录。用户项目 ignore 不被自动修改。
- 旧 journal.sqlite3/goals.json 留在原地，status/history 显式报告位置；不自动迁移、删除或启动旧 worker/reviewer。

## 最终源码回归

| 检查 | 结果 |
|---|---|
| 九包完整串行回归 | **7,605 passed / 7 skipped**，**339.64 秒** |
| Ruff | 全 packages 通过 |
| strict mypy | 九包 **610 source files** 通过 |
| Flow 示例 | 17 passed |
| 离线锁检查 | uv lock --check --offline 通过 |

完整命令：

```bash
XDOG_MCP_OFFICIAL_SDK_ROOT=<authorized-local-sdk> \
  uv run --no-sync python scripts/test.py --jobs 1
```

7 个 skip 是已有显式 opt-in Claude/Codex CLI 集成用例，不是 Goal 功能失败，
也不是非 Linux 验证通过。当前 Linux MCP official SDK 用已授权本地 fixture 实际运行。

本轮旧 worker/reviewer 专用用例随已删除的合同退休，改为实际主 Agent/tool/task-file
断言；非 Goal 测试没有删除或放宽。首次全回归发现默认 slash 初始化过宽，破坏本地
name/model/thinking/skill 等控制：已改为只按已安装 declared_commands 启动 native
能力，并新增“不获取 owner、不改变 AgentState identity”回归。旧依赖断言也更新为
用户要求的默认 Skills/ACP、可选 Eyes，不通过改产品回退新目标来换绿。

后续独立审计补强等待状态借权、发送 claim/pause 竞争、旧 parent 跨 await 写入、
readonly IO close join、错误聚合和观察初始 baseline readiness。最后完整回归包含全部修复。

## 发行物与真实安装验收

**本轮 GR-01..GR-12 全部通过，剩余 0 项。**

- 10 projects / 10 wheel + 10 sdist，Twine 全部通过；只有 Site 既有 long-description warnings。
- wheel/sdist 源码资源字节检查通过，退役的 Goal reviewer/worker/roles/host adapter 未进入发行物；默认依赖、入口点、私有目录排除均有实际 METADATA/包内容证据。
- 8 个隔离安装环境最终 payload 与最终 wheel 对照通过；无 editable/仓库源码回退。最新 Skills 的 readiness Python 与 workspace hygiene 技能资源已重装对照，Goal-only 消费再验通过。
- 四个原 cold 脚本通过；SDK owner 持有期间与真实关闭后释放都验证，不为“默认 Goal”删除批次、权限或最大输入断言。
- Coding/Claw 无 Eyes 默认安装的真实 native create → settled → 私有 ACP → 原主 Agent 两轮工作 → complete 通过，没有替代 Agent 或原始用户权限标记。
- Claw 双能力组合中，真实 JSON 变化经 Goal-owned Eyes 到原主 Agent，读取/恢复 task wait 并完成；pause/clear 结算自有观察但不撤销 accepted input 或停止无关 Eyes scheduler，原 readonly gate 仍拒绝写入。
- public start readiness 组合再验通过：初始 baseline 由 TaskObserver.start 等待，探针不再自行私下等待 baseline；更新最终 wheel 后的实际链路仍通过。

原始证据根：`/tmp/xdog-goal-simple/`；最终产物根：`/home/zhang/.cache/gs/p.NFKvUC`。
整体安装/组合证据由 `artifact-verification.json`（证据根）和产物目录中的日志、
`installed-final/`、`e2e-claw-v2/result.json`、`e2e-coding/result.json`、
`e2e-dual-ready/result.json`、`integrity.json`、`provenance.json` 保存。
较早失败尝试及旧 baseline 探针结果保留，未冒充最终通过。

本轮验证使用新 wheel / 私有非 editable 环境，不依赖仓库源码回退；模型与网络输出
使用脚本 transport/peer，不调用真实付费 provider 或微信服务。四个验收脚本覆盖：

- Goal-only（无 Coding/Claw/Eyes/TUI）技能、native 命令、数据/历史、未知发送恢复。
- Coding/Claw 默认安装即可取得 Goal，实际主 AgentTool 链、原权限、UI、轮换和关闭。
- ACP 五环境的独立 intake/owner、批次边界、最大载荷、原 ID、输出恢复与独立取消。
- 额外真实链路：两宿主 settled → ACP → 原主 Agent 继续；Goal-owned Eyes 文件变化 → ACP → 主 Agent resume/complete，含 public baseline readiness、pause/clear 和原权限。

第一 cold 安装的网络等待尝试已保留；确认是本轮自有安装进程后正常终止，后续用
离线缓存。没有打断正常测试、修改权限策略或停止用户服务。Cold SDK owner 旧断言
“调用结束即释放 controller”已更新为默认 Goal 的正确生命周期：同 owner 保留到
aclose，随后实际线程退出、writer lease 可重新取得；批次/消息/权限断言保持。

## 保护与限制

用户无关 docs/agent-ui-features.md 与 workflows 共 9 文件（含原 pycache）逐字节 hash
不变；前序未提交改动保留。未 commit/push、未 bump/tag/publish，未重启当前 gateway。
当前已运行进程不热换代码，下次正常启动才采用本轮实现。

非 Linux 原生环境、真实付费模型行为与真实微信网络不在本轮验收范围，不声称通过。
老 journal 只保留并报告位置，不声称迁移旧历史或恢复旧任务。裸 SDK 无实际接收端时
Goal 管理仍可用，但自动 ACP 投递明确 unavailable。
