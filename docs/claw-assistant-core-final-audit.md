# Claw assistant-core final audit — in progress

Date: 2026-10-19. Full objective remains active. This is a requirement/proof
inventory plus concrete audit findings, **not** a final47/47 pass or a replacement
for the original spec. Source baseline ea682f9 plus the currently verified
retired-extension fix; merge base33df9083c95e4f838e0eaf34532d3cd3ea6deee0.

## Evidence rules and current gates

Existing stage evidence is retained. An evidence file or green suite does not
by itself prove a broader contract. Each row below pins actual source/test paths
that exist now; their coverage/final verdict must still be inspected row by row.
No additional row is marked passed merely by producing this inventory.

Current resource-fix source gate:6876 passed/34 skipped,171.03s; strict9 mypy595,
Ruff packages, Flow17 and lock/diff pass. See
`docs/claw-retired-extension-verification.md` for actual runtime barriers and
RED-to-GREEN evidence. This proves the resource fix, **not VERIFY-01** below.

Cold output/Goal/UI milestone artifacts at `/data/observer-message-dist.LN5URO`
predate group-config and resource fixes. Their authentic previous receipts remain
useful stage evidence but are not current final artifact parity. Final wheels/
sdists, Twine, Goal-only/both-host same extension and ACP/native/output consumers
must run after the last runtime fix. No final whole-branch reviewer has been run;
the one review is deferred until known required implementation gaps are closed.

## Confirmed closed findings

- R-01: actual old extension owners retained by the application until shutdown.
  Weak registry releases closed histories; actual closing jobs remain discoverable.
- R-02: cancelled/timed extension/MCP joins returned before actual persistence and
  owner-thread settlement. Owned actual join now waits despite repeated cancel.
- R-03: late cleanup error disappeared after synchronous close. Actual original
  cleanup result is retained/reported without replaying cleanup.

All three have observed behavioral RED and GREEN, actual stdio/atomic persistence
or weakref/context-rotation proofs, not fake backend/store/close replacements.

## Acceptance findings — source fixes and still-open obligations

### F-01 — ordinary Claw persistence still blocks its event-loop owner

The actual first ordinary input, with an explicitly scripted model and private
fresh data root, records **12 actual fsync calls on the event-loop thread**.
`/data/t.Vy5SaQ/claw-owner-io-probe-red.log` records descriptor paths/thread IDs
and a failing behavioral assertion; all writes were real, not a fake persistence
method. Probe root `/data/t.Vy5SaQ/claw-owned-io-probe-x3x9ixp6` is disposable
fixture data, not user data. Sources: `GroupRuntime.get_or_create_session` calls
context creation/reset directly; `AgentSession.run_turn` calls `_persist_turn`
and its failure/cancel path calls `_persist` synchronously. Prompt rebuild and
metadata/history reads also require an explicit ownership/IO audit.

Required next work: capture immutable owner state on its existing execution loop,
perform ordered disk operations off-loop, settle actual commits before metadata/
history or execution receipts publish, and preserve cancellation/no-replay/owner
fences. Do not solve this by moving Agent execution to a second loop or by merely
wrapping the entire mutable Session in a worker.

### F-02 — corrupt authoritative active index: source fix verified

`TranscriptStore._load_index` catches malformed JSON/IO errors and returns an
empty index. A fresh private fixture seeded with malformed `sessions.json` was
opened and `create_session` called; it **did not reject and did not preserve the
original bytes**. Actual receipt:
`/data/t.Vy5SaQ/claw-active-index-corruption-probe-red.log`, fixture root
`/data/t.Vy5SaQ/claw-active-index-probe-x9v8ez_h`.

The source fix distinguishes true absence from unreadable/corrupt/invalid data,
uses a no-follow/nonblocking regular-file read, rejects duplicate JSON keys and
invalid group-key/session/basic metadata bindings, and refuses two groups sharing
the same current session file. No original bytes are replaced. Native shared
indexes with distinct sessions remain valid: `owner_group` is only the existing
uncatalogued historical archive recovery hint, not a new authorization flag.
There is no repair/migration/conversion or new format compatibility layer.

Tracked integrity cases:13 behavioral negatives plus the native positive, with
actual original-directory byte snapshots and real normal/dangling symlinks.
The first proposed loader overinterpreted the historical archive hint and failed
an existing valid shared-index test; that restriction was removed, preserving the
positive. The real cross-group session-file alias then failed its own RED before
adding uniqueness validation. Existing related catalog/access/navigation/name
suites plus the new tests:61 passed6.27s. Fresh all9:6890 passed/34 skipped,
169.59s (`claw-active-index-root9.log`); strict9/Ruff/Flow/lock/diff are recorded
by the current ledger. The final whole-objective current-pointer/derived-catalog
and lease/recovery audit, including genuinely missing pointers, remains open;
passing this bounded fix does not satisfy all of VERIFY-01.

### F-03 — ordinary appended transcript durability needs its own proof

`TranscriptStore.append_turn` appends/closes the JSONL file without an explicit
flush/fsync; `increment_turn` syncs metadata files, not that original log file.
The F-01 real descriptor trace does not prove new transcript entries were made
durable before the succeeded execution receipt. Source inspection identifies a
missing proof/implementation obligation: owned ordered batch persistence must
sync actual history and publish its cursor/metadata only after commit. Failure or
cancellation cannot silently append the same partial batch again or rerun tools.
No hardware power-loss/exactly-once guarantee is claimed or required.

F-01/F-03 and the remaining recovery audit keep VERIFY-01 open, even though the
existing whole-nine suite passes. Formal remaining IO work must include actual
thread/commit barriers, corruption byte-preservation and original-ID/no-replay
proofs, not relaxed tests, deadlines or a narrower interpretation of the spec.

## 47-item source/test inventory

Prefixes below expand to actual repository paths:
- `C:` = `packages/claw/src/xdog/claw/core/`
- `c:` = `packages/claw/tests/`
- `A:` = `packages/agent/src/xdog/agent/`
- `a:` = `packages/agent/tests/`
- `G:` = `packages/goal/src/xdog/goal/`
- `g:` = `packages/goal/tests/`
- `K:` = `packages/coding/src/xdog/coding/`
- `k:` = `packages/coding/tests/`
- `T:` = `packages/tui/src/xdog/tui/`
- `S:` = `scripts/`
- `D:` = `docs/`
- `P:` = `packages/goal/`
- `ACP:` = `packages/claw/src/xdog/claw/acp/`

| ID | Original requirement | Current source/test proof targets | Final audit state |
|---|---|---|---|
| CORE-01 | Goal 包未安装时普通聊天/记忆/工具/历史完整，无 Goal/ACP/Eyes 依赖；包存在自动提供 built-in skill 与能力，无 Goal enable 开关，不自动委托任务 | `C:runtime/group.py`; `K:core/agent_session.py`; `c:goal_installed_consumer.py`; `k:goal_installed_consumer.py` | 待逐项终验 |
| CORE-02 | 自治委托与能力启用分离，不自动将普通请求升级成 Goal | `G:skills/goals/SKILL.md`; `G:service.py`; `c:test_goal_host.py`; `k:test_goal_lifecycle.py` | 待逐项终验 |
| CORE-03 | 当前 checkout 原地实施，无 worktree、付费模型、用户服务操作或自动发布 | `D:superpowers/plans/2026-10-19-claw-final-acceptance.md` | 待逐项终验 |
| IN-01 | TUI/微信/ACP 普通新输入共享应用持久 Inbox，先提交再 ACK | `C:inputs/consumer.py`; `C:runtime/channel_adapter.py`; `ACP:backend.py`; `c:test_native_busy_intake.py`; `c:test_weixin_application_intake.py` | 待逐项终验 |
| IN-02 | 稳定 message ID 去重，同 ID 不同内容拒绝，attempt 与消息身份分离 | `C:inputs/store.py`; `c:test_input_store.py`; `c:test_acp_core_receipts.py` | 待逐项终验 |
| IN-03 | 容量满背压/明确拒绝，不能删除已经接收的输入 | `C:inputs/store.py`; `c:test_input_runtime.py`; `c:test_input_store_ownership.py` | 待逐项终验 |
| IN-04 | claimed 与 running 分离，取消在 admission 边界阻止未开始工作 | `C:inputs/consumer.py`; `C:runtime/group_controller.py`; `c:test_group_controller.py`; `c:test_input_consumer.py` | 待逐项终验 |
| IN-05 | pending/unstarted 安全恢复，running 转 interrupted，未知副作用不自动重放 | `C:inputs/store.py`; `c:test_input_store_ownership.py`; `c:test_input_start_stop.py` | 待逐项终验 |
| IN-06 | 固定可信回复目的地；用户、内部通知和其他连接不串线或错误广播 | `C:outputs/routes.py`; `C:outputs/targets.py`; `c:test_output_source_routes.py`; `c:test_observer_recipients.py` | 待逐项终验 |
| IN-07 | 微信部分提交/游标与 TUI ACK 丢失安全；接收前附件可恢复且维持原边界 | `c:test_native_submission.py`; `c:test_weixin_application_intake.py`; `c:test_native_receipts.py` | 待逐项终验 |
| IN-08 | 执行结果与渠道送达分离，逐消息回复不与最终聚合重复，未知发送不自动重发 | `C:outputs/delivery.py`; `C:observers/manager.py`; `c:test_observer_message_outputs.py`; `c:test_output_delivery.py` | 待逐项终验 |
| IN-09 | 断线不是已接收普通消息的撤回；明确取消仍绑定原执行 | `ACP:backend.py`; `c:test_acp_core_receipts.py`; `c:test_native_busy_intake.py` | 待逐项终验 |
| IN-10 | Claw 首阶段不顺便改变 per-turn 语义；Coding 既有独立输入批处理不回退 | `C:inputs/consumer.py`; `k:test_batch_turns.py`; `k:test_acp_batch_intake.py` | 待逐项终验 |
| RUN-01 | steer/follow-up 队列、checkpoint 和消费收据仅由 xdog.agent 管理 | `A:agent.py`; `a:test_identified_inputs.py`; `a:test_input_handled.py`; `c:test_orchestrator.py` | 待逐项终验 |
| RUN-02 | 无忙时自动 steering、steer-backlog 双重入队或 run 结束后的隐式新 turn | `C:runtime/orchestrator.py`; `c:test_input_runtime.py`; `c:test_group_config_contract.py` | 待逐项终验 |
| RUN-03 | 每 group 一个执行 owner，Gateway/ACP/UI 不另维护执行权威 | `C:runtime/group_controller.py`; `c:test_group_controller.py`; `c:test_native_busy_intake.py` | 待逐项终验 |
| RUN-04 | Gateway 仅协议/连接；Session 仅上下文/Agent；应用命令按职责隔离 | `C:runtime/gateway.py`; `C:runtime/orchestrator.py`; `C:runtime/session.py`; `c:test_group_context_controls.py` | 待逐项终验 |
| RUN-05 | 取消与 shutdown 等待真实 producer/存储结算，保留用户优先与跨 group 隔离 | `C:inputs/consumer.py`; `C:runtime/group_controller.py`; `c:test_input_start_stop.py`; `c:test_retired_extensions.py` | 待逐项终验 |
| BOUND-01 | ACP 无 Goal/observer 查询、业务 schema 初始化、消费 pump 或报告策略 | `ACP:backend.py`; `c:test_acp_core_receipts.py`; `S:verify_acp_artifacts.py` | 待逐项终验（既有阶段通过） |
| BOUND-02 | Eyes 自管 cursor+immutable outbox，向核心 Inbox 幂等交接，取消边界不丢失 | `C:observers/journal.py`; `c:test_observer_journal.py`; `c:test_observer_pending_reports.py` | 待逐项终验（既有阶段通过） |
| BOUND-03 | Agent 显式停止观察/通知/worker，三种操作互不隐含，无完成后自动取消 | `C:observers/manager.py`; `G:tools.py`; `c:test_observer_message_outputs.py`; `g:test_notifications.py` | 待逐项终验 |
| BOUND-04 | 宿主兑现显式取消，不误伤无关聊天，不因 provenance 获得内部身份 | `C:runtime/input_adapter.py`; `C:observers/journal.py`; `c:test_observer_recipients.py`; `c:test_goal_host.py` | 待逐项终验 |
| BOUND-05 | 独立/托管观察恢复语义保持明确，ACK 不等于模型或报告完成 | `C:observers/manager.py`; `c:test_eyes_delivery.py`; `c:test_eyes_process_recovery.py`; `S:verify_claw_observer_output_artifact.py` | 待逐项终验 |
| GOAL-01 | 独立 xdog-goal，仅依赖 Agent/标准库，不依赖 Claw/Coding/TUI/Eyes/ACP/Flow | `P:pyproject.toml`; `S:verify_goal_artifact.py`; `D:goal-kernel-verification.md` | 待逐项终验 |
| GOAL-02 | Goal 自行维护全部业务存储/lease/outbox，核心无 Goal 状态表或副本 | `G:store.py`; `G:notifications.py`; `g:test_store.py`; `g:test_notifications.py` | 待逐项终验 |
| GOAL-03 | 没有旧格式迁移/双读兼容；明确拒绝且不删除或改写原数据 | `G:codec.py`; `G:store.py`; `g:test_store.py`; `c:test_goal_host_recovery.py` | 待逐项终验 |
| GOAL-04 | 小型异步宿主端口，无整个 Runtime/Session 引用或宿主私有字段访问 | `G:host.py`; `G:service.py`; `C:runtime/goal_adapter.py`; `K:core/goal_adapter.py` | 待逐项终验 |
| GOAL-05 | 宿主原 supervisor 创建 worker/reviewer，权限/readonly/MCP/extension 边界不放宽 | `C:runtime/goal_adapter.py`; `K:core/goal_adapter.py`; `c:test_goal_host.py`; `k:test_goal_adapter.py` | 待逐项终验 |
| GOAL-06 | 主 Agent 规划、分配、接受报告；worker submitted 不等于任务接受/Goal 完成 | `G:service.py`; `G:worker.py`; `g:test_agent_lifecycle.py`; `g:test_service.py` | 待逐项终验 |
| GOAL-07 | 仅显式 request_complete 启动验收，无普通 turn-end 隐式 reviewer | `G:service.py`; `g:test_review.py`; `c:test_goal_host.py`; `k:test_goal_lifecycle.py` | 待逐项终验 |
| GOAL-08 | 逐条件独立验收及 revision/attempt/实际证据指纹通过才可提交完成 | `G:review.py`; `G:evidence.py`; `g:test_review.py`; `g:test_agent_lifecycle.py` | 待逐项终验 |
| GOAL-09 | 通知由事实/明确委托产生并去重，对账不因 active 状态周期调用模型 | `G:notifications.py`; `G:service.py`; `g:test_notifications.py`; `c:test_goal_host_recovery.py` | 待逐项终验 |
| GOAL-10 | criteria 修改/用户暂停释放依据真实用户授权，不由事件来源或展示扩展赋权 | `G:tools.py`; `G:host.py`; `g:test_tools.py`; `c:test_goal_host.py`; `k:test_goal_lifecycle.py` | 待逐项终验 |
| GOAL-11 | Claw group 与 Coding 精确 session/branch adapter 各自真实运行同一 Goal 实现 | `C:runtime/goal_adapter.py`; `K:core/goal_adapter.py`; `c:goal_installed_consumer.py`; `k:goal_installed_consumer.py` | 待逐项终验 |
| GOAL-12 | 上下文轮换/分支变化安全，不自动跨宿主接管同一 Goal，无 Goal 专属 Session 状态机 | `C:runtime/goal_adapter.py`; `K:core/goal_adapter.py`; `c:test_goal_host_recovery.py`; `k:test_goal_lifecycle.py` | 待逐项终验 |
| GOAL-13 | 保留子 Agent 无固定运行/累计配额约束，不以新架构偷偷恢复业务预算 | `a:test_supervision_config.py`; `a:test_supervision_compaction.py`; `D:claw-goal-cutover-verification.md` | 待逐项终验 |
| UI-01 | 同一 Goal Python 展示扩展实际供两宿主使用，主 TUI 无 Goal 业务判断 | `G:extensions/goal_ui/main.py`; `c:test_goal_extension_views.py`; `k:test_goal_extension_views.py` | 待逐项终验 |
| UI-02 | 通用 UI renderer 在 xdog.tui；宿主仅连接/布局/owner 适配，包不依赖 Goal | `T:components/extension_view.py`; `D:shared-goal-extension-verification.md` | 待逐项终验 |
| UI-03 | 最小通用只读 extension view；bounded snapshot/提示，跨 owner 读取拒绝且无任意调用 | `A:extensions/views.py`; `G:ui_views.py`; `a:test_extension_views.py`; `g:test_ui_views.py` | 待逐项终验 |
| UI-04 | 扩展禁用/故障/卸载仅影响展示，清理 UI/订阅但不改变 Goal 执行状态 | `A:extensions/runtime.py`; `G:ui_binding.py`; `c:test_goal_extension_views.py`; `k:test_goal_extension_views.py`; `c:test_retired_extensions.py` | 待逐项终验 |
| UI-05 | 不恢复任意 renderer、复杂 widget/layout，headless 不依赖 TUI 或展示扩展 | `G:extensions/goal_ui/extension.yaml`; `S:verify_goal_artifact.py`; `D:shared-goal-extension-verification.md` | 待逐项终验 |
| VERIFY-01 | 所有关键 fsync/阻塞 I/O off-loop 且取消后提交结算；corruption/lease 失败关闭 | `C:persistence/transcript_store.py`; `C:runtime/session.py`; `c:test_input_store_ownership.py`; `c:test_retired_extensions.py`; `g:test_store.py` | 不满足：F-01/F-03；F-02 有源运行时证明，整体恢复仍待核查 |
| VERIFY-02 | 权威状态与派生视图分离，视图失败可重建且不产生第二权威 | `G:views.py`; `C:observers/journal.py`; `g:test_ui_views.py`; `c:test_observer_journal.py` | 待逐项终验 |
| VERIFY-03 | 最终删除活动旧队列/Goal runner/原生 Goal UI，不保留双 owner 或双业务实现 | `c:test_group_config_contract.py`; `D:claw-retired-extension-verification.md` | 待逐项终验 |
| VERIFY-04 | 九包源测试、Ruff、九 namespace strict mypy、Flow examples 与 lock 检查实际通过 | `S:test.py`; `D:claw-retired-extension-verification.md` | 待逐项终验 |
| VERIFY-05 | Goal-only 和两宿主 fresh wheel-only 执行、资源打包与 Twine 验证通过 | `S:verify_acp_artifacts.py`; `S:verify_goal_artifact.py`; `S:verify_coding_goal_artifact.py`; `S:verify_claw_goal_artifact.py` | 待最后源码后的新产物，不可使用旧 wheel 代替 |
| VERIFY-06 | 全部条目具备证据，审核问题结算；阶段通过不冒充整个目标完成 | `D:claw-assistant-core-requirements.json`; `D:superpowers/plans/2026-10-19-claw-final-acceptance.md` | 未完成审计/单次整分支审核 |

## Exit gates still required

1. Close remaining owned-IO/durability findings and complete current-index recovery audit with RED-to-GREEN
   tests and ordered ownership, without changing ordinary per-turn semantics or
   Coding's independent input batching.
2. Finish source/test/artifact inspection for every row, source authority vs
   derived projections, all named artifacts/commands and acceptance invariants.
3. One independent fresh-context whole-branch review from actual main merge base,
   settle every required finding with evidence-backed fixes.
4. Fresh unfiltered9/strict9/Ruff/Flow/lock/diff and rebuilt current runtime/resource
   wheel+sdist parity/Twine/actual external consumers. Only then update passed/
   remaining counts and consider overall goal completion. No push/merge/publish.

## Required implementation before final review

`docs/superpowers/plans/2026-10-19-claw-owned-transcript-io.md` implements the
still-unmet original section11 contract. It preserves the full objective and all47
rows; it is not a new/reduced goal, an approval request or a reason to declare the
final audit complete. It must finish before the single whole-branch reviewer and
parent final-artifact gates.

## Subsequent source evidence: ordinary history transaction

The owned-IO plan Task2 now has actual ordinary-path proof: a frozen original-owner
semantic-prefix checked batch writes/syncs JSONL/catalog/index via the existing
journal; only then store cache/owning-loop cursor/meta and execution outcome
publish. Actual metadata/rollback faults, a delayed marker+repeated cancel,
readonly read isolation and five real process deaths preserve original bytes or
true committed state, stable attempts and one real tool effect without automatic
model replay. See `docs/claw-owned-transcript-commit-verification.md`. The full
ordinary-turn probe now records23 actual fsync with zero on-owner (previously two
remained). This advances F-01/F-03 for that path, not all-application acceptance.

Remaining required Task3 inspection includes startup, same-group native status/
MCP reads (history now serializes correctly), image/custom-context/compaction IO
cancellation, missing current pointers/derived catalogs and saved-model sidecar
no-follow/fallback ordering. Final whole-nine/cold current artifacts, per-row47
verdicts and the single whole-branch review remain mandatory. No additional
requirement is marked passed merely by this source milestone.
