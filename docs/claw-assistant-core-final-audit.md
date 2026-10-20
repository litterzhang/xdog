# Claw assistant-core final local acceptance

**2026-10-20: 47/47 original requirements passed; zero remaining required
assistant-core items.** The records below this final section are chronological
historical findings/checkpoints, not current open gates. Authority remains the
original design plus `claw-assistant-core-requirements.json`; exact current
runtime/artifact evidence is `claw-assistant-core-final-artifacts.json`.

## Final verification

- Final runtime commit `67120ee`, following `1e243af` and `fc0c23f`. No subsequent
  runtime edits: final changes only record audit/proof. Both Claw and Coding have
  no Goal enable setting: package presence supplies builtin skill/capability,
  ordinary chat never implicitly delegates, UI extension remains separately
  explicit. Goal journal belongs to Goal, canonical inputs/history to hosts.
- All9 configured test paths, unfiltered, jobs2 with pinned independent MCP SDK:
  **7077 passed, 7 skipped / 225.80s**. All27 previously optional SDK fixtures ran;
  seven remaining skips are real Codex/Claude CLI opt-ins, intentionally not
  activated. No real provider, credentials, account delivery or user service ran.
- Strict9 mypy603, Ruff packages/scripts, Flow17, `uv lock --check`, current
  `git diff --check`: pass. Whole-range cosmetic EOF blanks are explicitly
  deferred below, not falsely reported absent.
- Ten distributions /20 fresh wheels+sdists built under
  `/data/assistant-core-final-dist.grR66i`. Twine20 passes, retaining existing
  Site description metadata warnings. Exact644 canonical package source/resource
  members plus29 explicitly force-included Flow examples match wheel and sdist
  bytes. Umbrella canonical wheel/source trees also match; ancillary archive
  contents are NOT covered by canonical runtime parity (caveat below).
- Four fresh outside-checkout consumers pass: Goal-only with no installed/imported
  harness/TUI/ACP; actual Coding and Claw base without Goal then real worker,
  report, acceptance, independent reviewer, notices and owner/view lifecycle;
  Eyes/Agent/Coding/Coding-owner/Claw ACP plus Native and per-message output paths.
  Claw installed output uses actual Gateway restart with Eyes disabled, no manual
  resume or model/new input, same output ID/original recipient/context once.
- Both hosts load the same packaged Goal UI digest
  `2959d6de1764806ef3b078b0149d3f633164b4b4a37679eda47a09c40a90f3c7`.
  Separate installed >1MiB cumulative actual SDK probe reads20 parent +20 exact
  original-role context pages; no installed/imported harness/TUI/ACP.
- The ONE fresh wholebranch reviewer completed33df908..ef3e0db (66commits), found
  five Important and no Critical. All five have actual behavioral RED->GREEN
  in the single fix pass: oversized terminal receipt/withdrawn callback lifetime,
  abandoned Goal vs late reviewer, channel readiness, capacity-driven notices.
  Related task final suites65/60/99 passed, then the current full suite above.
  No second reviewer and no fabricated new reviewer approval.

## Rulings made (chronological, including cost)

1. Remove inert max_concurrent/trigger_pattern beside old queue_mode/debounce,
   preserving real global semaphore, capacity and reset settings. Cost if wrong:
   undocumented internal constructor clients fail explicitly, no compatibility.
2. Track retired closed extension owners weakly; actual unsettled work strongly
   owns its runtime and shutdown takes strong snapshots. Cost if wrong: premature
   resource loss; actual held fsync/join/cancellation probes specifically cover it.
3. Join one actual MCP/extension cleanup owner, without arbitrary thread-join
   timeout, and report original late failure. Cost: shutdown can wait on a genuinely
   unsettled owner rather than falsely claim cleanup; tool RPC deadlines remain.
4. Treat legacy owner_group metadata as an archive hint, not an authorization gate
   rejecting valid native multi-group catalogs. Actual session-file alias checks
   and controller/group binding remain required. Cost if wrong: cross-scope access;
   genuine duplicate-file binding is explicitly rejected by regression tests.
5. No paid-provider/real-account conformance: prohibited scope. Cost: no production
   account/provider certification, only actual scripted transport boundaries.
6. Hardware power-loss exactly-once/device guarantees excluded. Cost: process
   crash/fsync ordering is verified, not hardware certification.
7. Non-Linux/TCP, migration and cross-host Goal takeover excluded. Cost: those
   capabilities are unavailable; supported ownership/rejection is verified.

## Deferred minors

- Claw README reconnect paragraph still describes old draft/resubmission wording;
  the actual Native original-ID reconciliation/control fences are correct.
- Cosmetic extra blank EOF in observers/models.py and Goal LICENSE. Not mixed into
  runtime fixes and not silently changed.

## Existing packaging caveat (not publication approval)

The root umbrella sdist's pre-existing broad Hatch default includes local
`.claude` ancillary trees, including old checkouts, in addition to the canonical
current sources. The first additional umbrella parity probe incorrectly matched
nested checkout paths; an anchored canonical-path check proves actual current
runtime bytes, not ancillary content cleanliness. These ancillary files are not
installed by the package wheels and do not alter the verified runtime contract.
No such directory was edited/deleted, and no artifact was published. **Do not
publish the local umbrella sdist** without a separate packaging-exclusion audit.
This is an existing distribution-scope limitation, not a claim that ancillary
user/worktree contents were certified by this assistant-core verification.

## Historical audit and checkpoints (superseded where marked)

# Historical assistant-core audit — in progress at 2026-10-19

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

Current Task3 checkpoint after dynamic/native/memory/image/monitor fixes:
**6961 passed,34 skipped**,190.25s; strict9 mypy600, full Ruff packages/scripts,
Flow17, lock/diff pass. The first attempted full run hung in real Weixin shutdown
and was explicitly recorded failed after diagnosis; the fresh complete run above
includes its bounded reproducer/fix. See `docs/claw-owned-tool-io-verification.md`.
F-04 optional paths and parent47/review/artifacts remain open. No requirements
have been promoted merely because this suite is green.

New optional-memory/application-cleanup source checkpoint: **6979 passed,34
skipped**,193.12s; strict9 mypy600, full Ruff packages/scripts, Flow17, lock/diff
pass. Declared-minimum aiosqlite0.20 plus actual native vec/BM25:36 passed2.64s.
See `docs/claw-owned-hybrid-memory-verification.md`. Remaining ACP/source spool
IO and actual F-05 ordinary user-text hiding keep Task3 and acceptance open;
these source passes do not promote47 rows or validate old cold artifacts.

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

### F-01 — ordinary Claw persistence: bounded source fix verified

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

The original F-01 counterexample above is historical, not the current runtime.
`ae1e4fc` detached session preparation and `39547e0` history commit moved the
actual ordinary-turn IO off-loop without moving Agent execution. The tracked
whole-turn probe now observes23 real fsync calls and zero on the model loop.
See `docs/claw-owned-session-io-verification.md` and
`docs/claw-owned-transcript-commit-verification.md`. Startup/status/channels and
native dynamic controls have their own later checkpoints. This closes that
specific ordinary-turn counterexample, not all of VERIFY-01.

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

### F-03 — ordinary appended transcript durability: source fix verified

`TranscriptStore.append_turn` appends/closes the JSONL file without an explicit
flush/fsync; `increment_turn` syncs metadata files, not that original log file.
The F-01 real descriptor trace does not prove new transcript entries were made
durable before the succeeded execution receipt. Source inspection identifies a
missing proof/implementation obligation: owned ordered batch persistence must
sync actual history and publish its cursor/metadata only after commit. Failure or
cancellation cannot silently append the same partial batch again or rerun tools.
No hardware power-loss/exactly-once guarantee is claimed or required.

That original append-only path is no longer the production turn writer.
`transcript_commit.py` commits a detached, original-owner and semantic-prefix
bound history batch through the existing undo journal. Real JSONL fsync and the
commit marker precede cursor/metadata adoption and execution success; failed
history never enters a second append fallback. `test_owned_transcript_commit.py`
includes actual post-IO barriers, rollback failure/reopen and five subprocess
crash points after a genuine model/tool effect. See
`docs/claw-owned-transcript-commit-verification.md`. This closes F-03's original
counterexample, without claiming hardware exactly-once or all source IO audited.

### F-04 — remaining optional-memory and application IO audit

The previous ordinary-turn fixes did not prove all tool paths. Current tracked
`test_owned_memory_io.py` originally observed six actual get/write cases on the
owner loop and one keyword-read cancellation escaping real IO (RED7). Tool IO
now uses owned workers; a shared memory write lock preserves read/check/append
ordering and the existing capacity policy. Actual cancellation retains its
original group fence and appends only once. Generated-image path checks were
on-loop, and reference/save cancellation returned before disk completion (RED3);
owned preparation/read/save and cleanup joins now cover these paths, with the
original provider/network boundary on its loop. Monitor shutdown also required a
real fix: a presentation callback could settle cancellation, then dispatch
waited on its next FIFO item forever. Two bounded cases reproduce that failure;
the monitor now owns an explicit stop token and settled cleanup task.

Optional hybrid/indexer/embedding/SQLite and cached workspace/cleanup counterexamples
now have tracked source fixes and actual IO/transaction/physical-thread proofs;
see `docs/claw-owned-hybrid-memory-verification.md` for observed RED and current
bounded/source/native-positive evidence. This is not a completed VERIFY-01.
Remaining Task3 source inspection includes actual ACP endpoint start/close and
workspace resolution, plus SourceSender chunk reads/spool close/repeated-cancel
settlement. Existing source has direct or unowned IO there. The parent UI/source
acceptance audit also needs a ruling on legacy Goal-prefix/channel filtering of
ordinary source text. No new search engine/model loop or weakened permission/
canonical route/private endpoint policy is authorized. Task3 and parent47 audit
remain open until those current counterexamples are settled and proved.

### F-05 — generic source projection still interprets Goal-like user text

Actual application audit probe sends ordinary `UserInput` beginning `[Goal:`
through the real Orchestrator, Inbox, Agent and durable transcript, with one
scripted model response. The canonical user row is present unchanged, but
`runtime.capture_source` -> `source_records` returns only the assistant response.
`/data/t.Vy5SaQ/claw-neutral-source-audit-red.log` records the actual source and
failing assertion. No Goal was created, no old data was migrated, and no TUI or
real provider was started. The private fixture was removed after actual cleanup.

`transcript_source.py` filters user content using legacy Goal prefixes and also
skips goal_runner/scheduler channels. This remains an active Goal-specific rule
in a generic readonly source projection and hides legitimate ordinary input.
It is an unclosed parent UI/core-neutrality acceptance finding, not evidence
that a new Goal backend should be added. Port the actual probe to a bounded
tracked regression, preserve ordinary source text/identity, and remove only the
obsolete business filtering without changing tool redaction or source leases.
Task3/source47/review/artifact completion is still unproven.

### F-04/F-05 — source implementation verified, final acceptance still pending

`docs/claw-owned-ingress-source-verification.md` records actual fixes and current
proof for the remaining ACP/path/source/MCP/child-restore/constructor/native-model
IO counterexamples and literal Goal-like user-text projection. Known source
counterexamples F-01 through F-05 are now closed at their scoped implementation
boundary. All9 current:7022 passed34 skipped199.49s, strict601/fullRuff/Flow17/
lock/diff. This supersedes the preceding **historical finding** descriptions;
those RED receipts remain authentic, not claims that their old code is current.

The owned-IO Task3 post-commit all9 gate is complete (7022/34,197.91s);
execution has returned to parent Task2 all47 row inspection. No row is promoted
by a green suite alone. Source availability is not final
artifact parity, and wholebranch review/current consumers remain unperformed.

### F-06 — aggregate Goal data quota and post-commit tool errors

Source audit found authoritative encode/decode_goal capped the entire accumulated
Goal at1MiB, while parent/context tools serialize whole state into256KiB frames.
Actual valid160 tasks*8000-char Goal store put/reopen and reverse1500-task DAG
were RED2/2.03s (`goal-cumulative-state-red.log`): hidden aggregate quota and
Python recursion-depth limit. Bounded corrective plan:
`docs/superpowers/plans/2026-10-19-goal-cumulative-state.md`.

Task1 removes only the aggregate authoritative byte cap, preserves typed exact
format/individual field/report/notice/artifact bounds, makes DAG validation
iterative/order-preserving, and prepares immutable large snapshots on the owned
leased worker. Actual Goal81 pass7.33s include close/cancel/reopen-no-write.
Task2 now has actual exact bounded tool/context/list page reconstruction,
original version/criteria after concurrent mutation, real SDK worker/reviewer,
post-render owner fencing and known-committed successful receipt proofs. Current
source:7035 passed34 skipped212.21s, strict602/fullRuff/Flow17/lock/diff. See
`docs/goal-cumulative-data-verification.md`. This closes the bounded F06 source
counterexample, not all47/final review/current artifact acceptance. No
new goal, migration, enable flag or reviewer was introduced; parent47 is active.

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

1. Owned-IO/current-index/neutral-source implementation gates are complete at
   eba1158 with actual RED-to-GREEN and post-commit all9 proof. Reconfirm their
   acceptance scope row-by-row; they are not a substitute for review/artifacts.
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

## Requirement-by-requirement source inspection receipt

`docs/claw-assistant-core-source-audit.json` now covers all47 original IDs with
current actual mechanisms,43 named runtime-case locators (validated against
actual test AST symbols), source/gate verdict and exact remaining final proof.
This is not a new smaller checklist: every original requirement stays present.
Core ordinary intake/state/route/attempt, native Agent lanes, cancellation/output
certainty, Goal host/store/explicit review/criteria, real dual adapters, and generic
view/display-only guard paths were inspected; F06 was found and fixed by that
audit instead of passing from a green existing suite. No row is promoted merely
from a test name or inventory. Current source gate7035/34, strict602/fullRuff/
Flow17/lock/diff; Goal+actual both-host post-commit145 passed38.40s.

Current installed Goal-only/absent-base/both-host same-wheel/UI/ACP/native/output
proofs are still pending; old snapshots are not used. The original47 final
statuses remain unchanged until final judgment/artifacts establish their scopes.
The next step is the one fresh-context wholebranch review of actual main merge
base through complete implementation HEAD, not another per-task/fix reviewer.
