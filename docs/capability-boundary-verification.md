> **历史快照（2026-10-20）**：本文记录此前冻结实现，涉及 Goal worker/reviewer/attempt
> 的流程已由主 Agent + task files + ACP 提醒设计替代。不要据此推断当前功能或验收状态；
> 当前入口见 `goal-simple-design.md` 与 `goal-simple-requirements.json`。原始证据保留不改写。

# Goal / Eyes 能力边界验收

日期：2026-10-20；平台：当前 Linux。需求单：`capability-boundary-requirements.json`。
本记录只覆盖本轮边界重构，不以之前的审计结论替代本轮验证。

## 交付范围

- Goal 业务宿主绑定统一到 `xdog.skills.goal.runtime`；删除两份 Coding/Claw Goal adapter。
- Claw 的观察者模型、journal、调度、恢复、取消、工具迁至 `xdog.eyes.hosting`；删除 Claw 的 observer 业务目录及 `tool_eyes.py`。
- `xdog.agent.capabilities` 通过可信安装的 `xdog.capabilities` 入口点装配能力；发现阶段不打开存储、不启动模型或观察者。
- Coding session、Claw group/application 提供通用 owner-bound 端口。Goal 仍仅直接依赖 Agent/stdlib，Eyes 不导入 Agent/AI/Coding/Claw/Flow/Skills/TUI。
- Eyes 工具通过通用 descriptor 适配；Claw onboarding 从 metadata 合并能力工具目录，保留显式 allowlist，不静默扩大已有配置权限。
- 保留原 Goal owner/path、observer v3 格式、原消息与输出 ID、原收件人、ACK-loss 幂等、未知执行不自动重放、独立取消。
- 旧 parent 的工具不能借新 parent 用户授权；普通 child 不能继承 parent-only 能力工具；角色授权绑定真实 Supervisor 和工具对象。
- 关闭必须结算实际 startup、晚到资源、子执行/提交和后台工作；单个 detach/display/file/handler cleanup 失败不能跳过其他独立资源。

## 验证方法

源码回归使用仓库 `scripts/test.py --jobs 2`，所有九包均纳入，不过滤失败测试。通过授权的本地 MCP official SDK fixture 运行相关互操作用例，不调用真实付费 provider。

完整回归首次发现两处旧测试契约未迁移：一处仍要求 Eyes 在 Claw built-in registry 注册；另一处仍 monkeypatch 已删除的 Goal `find_spec`。已改为断言 Eyes 来自安装 capability metadata、发现不绑定服务，以及通过通用 discovery 模拟无可选包。没有删除测试或放宽产品权限。

另外，最终复核补充 Goal 关闭异常回归：同步 unsubscribe 失败、一个 display 关闭失败、另一个延迟关闭时，storage 必须等全部结算才关闭；取消等待者不能提前释放 owner；重复关闭不重复执行副作用。

最终独立代码 review 还复现并修复两处迁移退化，仍归原有需求而不重复计数：

- **EYES-R1 / CB-10**：reply 为空不再跳过 scope 形状校验。所有记录在恢复前验证；相对路径、父级穿越、NUL、空 owner、UTF-8 超长等拒绝且数据库字节不变。只验证持久数据形状，不用 live group registration 拒绝旧 journal。
- **EYES-R2 / CB-07**：确认 live workspace 丢失或改变后仅隔离相应 observer 为 needs_review；其他组继续处理后续 hints。跨 receipt/output await 的 owner 变化也覆盖，原始 scope、namespace、reply、outbox/Core receipt/output ID 不变，不触发模型重放。未发生 owner 失配的协议错误、真实存储错误仍明确传播。

这些修复经过真实 ApplicationPorts / Core InputStore / OutputStore / LeasedSqlite 定向回归，review 与修复证据见原始证据根的 `final-review-eyes.md`、`eyes-p2-freeze.json`。

## 最终结果

**本轮 12 项需求全部完成，剩余 0 项。** 需求数与测试数分别统计。

| 验证 | 最终结果 |
|---|---|
| 当前 Linux 九包完整回归 | **7,587 passed / 7 skipped**，339.71 秒 |
| Ruff | 全 packages 通过 |
| strict mypy | 九包 **613 source files** 通过 |
| Flow 示例 | **17 passed** |
| 离线锁文件检查 | `uv lock --check --offline` 通过 |
| 构建 | **10 projects / 10 wheel + 10 sdist** |
| Twine | 20 产物通过；Site 有既有 description 字段 warnings |
| 产物完整性 | wheel / sdist 各 924 个源码与资源文件字节匹配，退休模块缺席，入口点/依赖正确 |
| 安装完整性 | 8 个非 editable 安装环境逐文件与最终 wheel 对照通过 |
| 隔离宿主 | Coding/Claw base + Goal-only、Eyes-only、ACP 五环境、Claw Goal + Eyes 组合与 observer output 全通过 |
| 最后源码复核 | 725 个 packaging/source/script 输入文件保持冻结时字节一致 |
| 用户无关 dirty | 9 / 9 文件 hash 不变，无 staged 文件 |

7 个跳过均为已有显式 opt-in Claude/Codex CLI 集成测试，不是失败或非 Linux 验证通过；当前 Linux MCP official SDK 互操作使用已授权本地 fixture，未因此跳过。

最终完整回归命令：

```bash
XDOG_MCP_OFFICIAL_SDK_ROOT=<authorized-local-fixture> \
  uv run --no-sync python scripts/test.py --jobs 1
```

此前并行 v3 回归出现子进程 / 扩展 worker / tmux 启动超时，保留失败日志。当时 swap 已满，内存和 IO 等待高；没有修改运行代码、测试超时或筛选测试来消除失败。等 cold 验收结束，仅降低包级并发，原 runner 的全部九包随后通过（包内原 worker 设置保持不变）。本轮已结束的旧临时验收目录移至私有磁盘缓存，并保留原路径 symlink 与全部证据；未终止用户服务或运行中的测试。

最终产物证据：`/tmp/xdog-capability-boundary/f.sxrRt8/verification.json`。
全回归及各包统计：`/tmp/xdog-capability-boundary/final-regression-summary.json`。
最终聚合源码 SHA-256：`8bfa988f6b5ae2ecc074f7dd02eeb9422bdd63235756bd94b4950dd7d3dee049`。
构建与 cold 安装使用离线缓存；仅独立 Twine 校验工具环境通过正常网络补齐依赖，没有执行 publish。

本轮代码保持未提交，版本未提升；没有 push / tag / 服务重启。

临时原始证据根：`/tmp/xdog-capability-boundary/`。

## 范围与限制

- 不新增 Goal 自动续跑、后台模型轮询、第二 Agent loop 或固定子 Agent 运行预算。
- 不发布、打 tag、commit/push 或重启用户 gateway。
- 不声称真实微信网络、真实付费模型或非 Linux 原生环境通过。
- 独立安装验收使用本轮新构建 wheel 与私有环境，不依赖工作区 editable 回退；模型和渠道网络以脚本 peer 替代，但存储、owner、工具绑定、真实宿主及 ACP/输出路径按实际实现运行。
- 用户无关 `docs/agent-ui-features.md` 与 `workflows/` 共 9 个文件（含原 pycache）逐文件 hash 核对，不覆盖、不清理。前序 skill/tool rename、Responses optional schema、Eyes 安装发现等未提交改动保留。
