# Agent UI follow-up simplification

Date: 2026-10-16. **Complete in working tree; not committed, version-bumped or released.**

Baseline: `c647573e0f792a8def26a462b628017c7f28054b`.
This is a new six-item goal, not a reopening/double-count of the completed26-item cleanup.

## Exact scope and counts

**6 requirements completed; 0 remaining: 0待实现 /0待验证 /0缺外部环境 /6已完成 /0延期.**
Protected30/33/46 and29 are invariants, not extra rows. Prior removals under32 etc. are not recounted.

| ID | Behavior scope | Verifiable acceptance | Status |
| --- | --- | --- | --- |
| UI-013 | 用built-in planning skill和普通任务材料替换PlanOwner/PlanState/update_plan/专用计划模式UI；先修正并迁移真正的宿主只读门禁。 | 旧allow_session、allow-all、延迟批准不绕过硬只读；Coding/Claw等价、扩展/MCP/子任务受限；旧planning恢复不变可写；显式用户解除；无第二planner/Agent loop。 | 已完成 |
| UI-016 | 移除用户手工编辑/排序/删除/取回队列UI，只读检查/状态及必要停止保留；主Agent与SDK队列管理保留。 | 只读界面及旧mutation包不调用backend写控制；实际agent_control(queue)仍可在权限下操作；owner/revision/selected/delivery/image receipt/写前持久化/取消及并发保护验证。 | 已完成 |
| UI-017 | 与保留的46共同验证输入/初始任务/附件日志、history准入与settlement及uncertain恢复；不新建日志管理产品。 | 当前Linux进程崩溃边界下可恢复数据一致或明确uncertain；没有自动重放/重启或exactly-once声称；主Agent显式恢复入口可用且受权限/owner保护。 | 已完成 |
| UI-027 | 移除resource/catalog/combined主动watch SDK/UI/任务和订阅专用协议；分离共享URI校验。 | 无watch入口/订阅后台工作或隐式read/model调用；普通tools/resources/prompts/手动refresh/最小目录失效、Roots精确续接及SSE/GET恢复/取消/noPOSTreplay保持。 | 已完成 |
| UI-043 | 以当前exports审计残留；保留必要controller/只读查询/原生用户控制/基础问答；不重建或重复计数已移除插件写控制。 | 有当前公开API对应矩阵和拒绝测试；30注册/hook/worker、33provider及正常内部JSONRPC仍可用；插件不能借旧包恢复会话/输入/队列/compaction控制。 | 已完成 |
| UI-044 | 保留env/resolver私有凭据与模型调用正确性；仅显式注册provider可用其OAuth回调，统一薄的login/refresh/logout入口，不扩建通用认证平台。 | 秘密不进catalog/log/快照；cancel/expiry/withdrawal/路由capabilities与no fallback验证；ProviderOAuth与MCP PKCE区分；29两LinuxMCP后端及provider vault显式操作保留，无自动写入/迁移/降级。 | 已完成 |

## Work sequence and protected boundaries

1. Main first fixes the demonstrated deny/cached approval hole and establishes shared hard read-only semantics before removing planning gate/state/UI. Skill instructions are not permissions. Old planning snapshots fail closed; explicit user release only.
2. UI016 removes manual queue management but never main-Agent/SDK backend control, journaling, image receipts or selected-input/owner/revision/delivery safeguards. UI017 and protected46 are validated together without duplicate persistence products.
3. UI027 removes active subscriptions, not ordinary MCP/SSE/RPC. Extract URI/metadata safety helpers; preserve directory invalidation/manual refresh, Roots continuations and uncertain-call no-replay.
4. UI043 audits already-narrowed exports and only actual residuals. UI044 retains private provider auth and required callbacks; MCP public PKCE does not dictate provider callback protocols. User-retained29 file/SecretService/provider vault remains.

At most3 existing authorized child agents have disjoint source ownership: SDK subscription internals, TUI child queue surface, and provider-auth internals/current-export audit. Main owns blocking permission work, native/core integration, ledger and final gates.

## Required completion gates

Each item needs current-source behavior/absence/retained-security evidence. Then freeze and run:

```bash
XDOG_MCP_OFFICIAL_SDK_ROOT="$(cat /tmp/xdog-ui-simplification/authorized-mcp-sdk-root.txt)" uv run --no-sync python scripts/test.py
uv run --no-sync ruff check packages
uv run --no-sync mypy --strict -p xdog.ai -p xdog.agent -p xdog.tui -p xdog.flow -p xdog.site -p xdog.claw -p xdog.coding -p xdog.eyes
uv run --no-sync xdog-flow test packages/flow/examples/ --allow-script-stub
uv lock --check --offline
uv build --offline --all-packages --out-dir /tmp/NEW_PRIVATE_ARTIFACT_DIRECTORY
```

Also verify source/artifact metadata, retired/retained APIs, isolated imports and actual Linux terminal/lifecycle/transport/concurrency/crash/permission cases. No retained security test is deleted or relaxed to get green. Non-Linux native verification is not required.

Preserve original7unrelated hashes (`docs/agent-ui-features.md`, `workflows/`) and concurrent changes. No install/live provider/service call/restart/destructive migration/commit/push/tag/version bump/release without new authorization. Evidence lives under `/tmp/xdog-ui-followup/`; authoritative counted statuses are in `agent-ui-followup-requirements.json`.

## Initial evidence

The prior read-only probe reproduced cached allow_session surviving a change to deny without executing any tool. This new goal adds deterministic permission tests before changing the implementation.


## Historical permission prerequisite checkpoint (superseded by latest counts)

Eight deterministic cases reproduced the old cached/session and pending-approval
policy-change hole. The fix makes deny precede cached grants, revokes those old
grants on restriction, retires pending admissions and rechecks the policy
revision before accepting any delayed affirmative reply (including deny→ask).
No actual tool/model execution was needed to reproduce it. Permission/batch/image
retained checks pass27; targeted Ruff passes. UI013 remains pending implementation
until shared hard-readonly/legacy restore/skill+native plan removal is complete.
Counts remain4pending implementation/2pending verification; no requirement is
counted complete just from this prerequisite.

## 历史集成检查点（已由最终验收取代）

2026-10-16：6 项剩余，3 待实现、3 待验证、0 缺外部环境、0 已完成、0 延期。UI-044 定向修复已完成，转待完整回归验证。其余分支定向证据见 JSON 清单；原生集成仍进行中，分支测试不代表需求验收通过。

### 首次完整回归检查点（已由最终验收取代）

6 项实现候选已集成，全部转待验证，剩余 6 项（0 待实现 / 6 待验证 / 0 缺外部环境 / 0 已完成 / 0 延期）。首次八路径完整回归 6 个失败，禁止计作完成：3 个旧持久化 fixture、1 个真实只读初始帧问题、Coding inbox 旧初始化污染新 owner 和 Eyes socket 发布权限窗口两处受保护生命周期竞态。它们是既有验收的修复项，不新增需求计数；修复后重新冻结、完整回归和重建产物。

## 最终验收（2026-10-16）

**准确需求计数：总计 6，已完成 6，剩余 0。** 待实现 0、待验证 0、缺外部环境 0、延期 0。上一轮 26 项、历史需求及保护项不重复计数。

- UI-013：删除独立计划状态/工具/面板，加入 `xdog-planning` 内置 skill 与宿主 `/readonly on|off|status`。修复缓存和延迟批准绕过，保留旧 planning 恢复限制、持久化、分支/导航保护和显式解除；skill 不授予权限。
- UI-016：用户队列仅只读 list；主 Agent/SDK 管理、恢复、owner/revision/image receipts 和写前持久化保留。
- UI-017：保留最小输入/附件日志与46崩溃一致性，真实进程死亡和主 Agent 显式恢复已验证，无自动重放。
- UI-027：移除主动 MCP watch 的 SDK/UI/gateway/后台 listener，保留普通 MCP、Roots、显式刷新、响应 SSE/GET 恢复与取消。
- UI-043：审计现有导出；保留必要只读/基本对话及30/33/privateworker通信，不重复移除已删插件写入口。
- UI-044：保留唯一薄的显式 provider auth 路径和29凭据后端；修复异步取 key 期间凭据过期的准入竞态。

首轮全量发现的问题均已解决：旧导航 fixture 已迁移但保留全部崩溃断言；修复真实只读首帧多渲染、旧 inbox 初始化污染 successor、Eyes canonical socket 权限/ready 发布竞态。它们是现有验收范围内的修复，不另计需求。

最终冻结完整门禁：

| 门禁 | 实际结果 |
| --- | --- |
| `scripts/test.py` 全部8路径，启用已有独立MCP SDK | **5932 passed，0 failed，7个既有 opt-in 外部 CLI skip** |
| Ruff 全 packages | 通过 |
| strict mypy 全8包 | 542 source files，无错误 |
| Flow examples | 17 passed |
| 离线 lock 检查 | 通过 |
| fresh offline build | 9个构建目标、18个产物 |
| 产物/隔离import/API/README/source校验 | 通过；移除9个模块不可导入，必要API保留 |
| 冻结一致性与无关改动 | 1322个包内输入文件哈希不变，原7个无关文件哈希保留，stage为空 |

7个 skip 是未启用的 Codex/Claude 实际 CLI 集成，不声称通过；没有 retained-security deselection。非Linux原生验证不在授权范围，不声称通过；未调用真实provider、未安装依赖、未写真实账户凭据。

完整证据：`/tmp/xdog-ui-followup/final-integration/summary.json` 和同目录日志；逐项证据见 `agent-ui-followup-requirements.json`。初次失败日志保留为 `tests-initial-failure.log`，最终完整回归是 `tests.log`。

未 commit/push/tag，未改版本、发布或重启用户服务；HEAD仍为基线 `c647573e0f792a8def26a462b628017c7f28054b`。
