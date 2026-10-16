# Agent UI simplification — implementation goal

Date: **2026-10-16**. Status: **complete in the working tree; not committed, deployed or released**.

This goal implements the user's explicit removals and simplifications, replacing
older UI-goal scope where they conflict. The current source baseline is `e9f27f66f87808c8f9cd25f452f9cab3a4a3f8d5`.
The older feature guide/audit OPENs are historical evidence, not extra requirements
or permission to restore removed features. No requirement is marked complete merely
because its implementation was once complete under a previous goal.

## Requirement counts

**26 top-level requirements: 25 user-numbered decisions + 1 Linux-only addition.**
Current categories: **0 pending implementation, 0 pending verification, 0 missing
external environment, 26 complete, 0 deferred**. Internal code tasks are not counted
again. Conditional decisions remain in scope; they are not silently deferred.

Machine-readable source: [requirements ledger](agent-ui-simplification-requirements.json).

| ID | Action | Scope | Verifiable acceptance | Status |
| --- | --- | --- | --- | --- |
| UI-012 | remove | 仓库差异审查UI：删除/diff、审查用Alt+D及专用浏览组件；保留Agent通过工具读取/审查Git。 | 两客户端命令/快捷键/公开组件无差异审查入口，普通工具仍可读取真实Git差异，审批及基础编辑不变。 | 已完成 |
| UI-020 | remove | 子任务草稿RPC：删除Coding RPC子草稿访问、保存、删除协议。 | 不再接受子草稿RPC或导出其处理器；本地保留范围按19/21/23实现，不因删除协议改变本地权限。 | 已完成 |
| UI-034 | remove | Coding复杂RPC控制：删除供外部前端驱动Coding的复杂RPC模式；与20/38/39/40/41/42共享执行任务，不重复算需求。 | CLI不再启动复杂RPC；相关模块/导出/设置/支持声明清理；原生TUI、print和ACP均复用单控制器且可工作。 | 已完成 |
| UI-036 | remove | 插件终端输入拦截/观察：删除插件key/paste/release处理和观察API；保留基础输入、快捷键和审批保护。 | 插件不能注册/接收按键粘贴释放事件；本地输入、受保护审批、快捷键和光标仍正确。 | 已完成 |
| UI-038 | remove | RPC历史/二进制专用读取：删除RPC结构化历史和二进制读取协议；保留本地历史、普通导出及会话持久化。 | 专用RPC读取入口不可用；本地结构化历史/图片持久化和普通导出可往返，不丢工具配对或附件。 | 已完成 |
| UI-039 | remove | RPC实时事件/进度传输：删除RPC传输层；保留内部Agent事件和原生进度。 | 无RPC进度订阅/协商/转发；Agent run/tool/usage及两原生界面进度和取消仍可用。 | 已完成 |
| UI-040 | remove | 远端主草稿协议：删除Coding RPC和Claw gateway草稿专用协议；保留本地主草稿。 | 旧远端草稿包不产生读写/模型调用；本地保存/恢复与退出保护不变，无独立远端草稿能力声明。 | 已完成 |
| UI-045 | remove | 非Linux凭据适配：删除Keychain/Windows Credential Manager代码、辅助进程、测试、选项；保留Linux共享存储保护。 | 原生平台worker及入口删除；旧后端名在I/O前明确拒绝且无降级；Linux文件和Secret Service显式配置/取消/秘密保护回归通过。 | 已完成 |
| UI-041 | remove | RPC扩展输入框/会话/宿主控制：随34删除，不另保留外部协议；先分离本地共用逻辑。 | 无相关RPC控制协商或处理器；本地允许的功能按32收窄，ACP controller/inbox不受损。 | 已完成 |
| UI-042 | remove | RPC自定义UI/子终端：随34删除；本地基础问答和审批组件单独保留。 | 无RPC自定义面板/子终端/布局传输；保留本地共享基础交互、安全焦点与取消。 | 已完成 |
| UI-018 | simplify | 带图队列两阶段取回：去用户UI；保留底层两阶段能力和必要保护；核对主Agent可调用入口。 | 无取回操作面板/命令；通过主Agent受权限约束入口验证prepare/commit与旧owner拒绝、附件校验/不自动重放。 | 已完成 |
| UI-019 | simplify | 本地子任务草稿：精简手动管理；保存/恢复范围先审计后收口。 | 记录获准的最小保存/恢复合同；删去多余目录管理，保留合同内的附件/来源/owner保护且不误动用户现存草稿。 | 已完成 |
| UI-021 | simplify | 子任务自动保存交互：移除子任务自动保存交互；保留主草稿复用组件。 | 子任务无autosave开关/状态/UI任务；主草稿保存/崩溃保护仍正确，线程和文件owner清理。 | 已完成 |
| UI-022 | simplify | 主输入框草稿：保留保存/恢复核心，减少多草稿目录和管理。 | 稳定主草稿可保存恢复含图内容；减少目录/管理UI，不静默删除现有数据或覆盖未保存输入。 | 已完成 |
| UI-023 | simplify | 草稿保存入口与预览：保留主保存/快捷键/图片预览，减少子任务UI。 | 主保存快捷键/预览无需子任务管理面板，保留图片大小/类型/所有权检查。 | 已完成 |
| UI-024 | simplify | 草稿写入后退出：保留必要退出保护；删已删除草稿功能分支。 | 保存中退出/失败/取消不丢主草稿、不假报成功、不残留任务；清理已删除子草稿功能的退出分支，仍保留手工I/O所需的退出保护。 | 已完成 |
| UI-026 | simplify | MCP数据与补全：保留Schema校验和多类型内容，减少手动参数补全UI。 | Schema/参数校验及text/image/audio/resource/structured结果保持；删除选定的手动补全入口，不绕过审批。 | 已完成 |
| UI-028 | simplify | MCP OAuth：根据真实服务依赖收窄注册、认证、登录组合。 | 先列真实服务/方式依赖；最小保留合同有受控接入/取消/拒绝测试；无未审计的旧凭据降级或自动登录。 | 已完成 |
| UI-029 | simplify | Linux凭据存储：按部署在文件/Secret Service中取舍；必要显式管理；不静默降级。 | 记录部署选择与迁移边界；无无效配置回退，秘密不进日志/argv，原用户存储未经授权不改动。 | 已完成 |
| UI-031 | simplify | 扩展自定义UI：保留基础问答/确认/选择；精简嵌套、浮层、自定义面板。 | 基础ask/confirm/select可执行并安全取消；移除复杂UI注册/传输/布局路径，不破坏审批焦点。 | 已完成 |
| UI-032 | simplify | 扩展宿主访问：保留模型/用量/工具只读；按需收窄输入框修改/会话控制/补全。 | 确定的最小只读API有测试；被移除写控制不再可调用，保留操作仍受单controller/权限约束。 | 已完成 |
| UI-035 | simplify | 扩展参数补全细节：与26/32统一取舍；保留时必须正确处理引号、光标、替换。 | 不能只移除边界保护；保留补全时有literal/cursor/stale-response用例；整体删时不留API/配置。 | 已完成 |
| UI-037 | simplify | 扩展布局与渲染：减少复杂插件布局；保留基础TUI/Unicode/裁切/光标修复。 | 旧复杂布局入口清理；宽字符、emoji、ANSI裁切、缩放、光标和基础审批/进度仍正确。 | 已完成 |
| UI-047 | simplify | 主草稿状态/崩溃保护：保留主保护、精简子侧和状态显示。 | 主草稿写失败/重启恢复/幂等与退出保护通过；不再启动仅供删除功能使用的自动任务。 | 已完成 |
| UI-048 | conditional-simplify | MCP额外客户端能力：保留简单显式Roots；先查依赖，再减Sampling/Elicitation/续接/Tasks。 | 服务依赖清单及保留理由可审计；无真实依赖才移除Sampling；保留能力有权限/取消/不自动模型或工具调用测试。 | 已完成 |
| PLATFORM-LINUX | remove | Linux-only平台追加：移除所有其他非Linux专用代码/测试/配置/支持声明，保留Linux共享代码。 | 运行/构建入口无非Linux适配选择；剪贴板/图片/终端Linux行为保持；历史审计清晰标识为旧记录，不当当前支持。 | 已完成 |

## Implementation sequence and shared boundaries

1. Remove unambiguous non-Linux credential adapters first, keeping both explicit
   Linux choices temporarily. Feature29 still needs deployment review; this is not
   an implicit decision to keep both forever or downgrade a selected native store.
2. Map RPC callers into native/shared/controller/ACP responsibilities; remove the
   dedicated Coding RPC surface and Claw remote-draft protocol, not ACP endpoints
   or eyes registration/activation, controller leases or the durable inbox.
3. Remove repository-diff UI and extension terminal interception/observation;
   preserve basic editor shortcuts, approvals, native progress and Git tool access.
4. Simplify child/parent draft surfaces, retaining parent persistence/crash safety
   and the main-Agent entry/protection for underlying queued-image transfer.
5. Audit MCP services/configuration and deployment needs, then settle19/28/29/48.
   No sampling deletion simply because examples/tests have no dependency, no secret
   logging, live login/service probe, or destructive migration to discover usage.
6. Reduce extension controls/layout/completion and MCP manual UI consistently;
   validate retained rendering, quotes/cursors/replacements and lifecycle behavior.
7. Reconcile current docs/exports/options and old audit references; final complete
   configured tests, Ruff, strict eight-package mypy, workflow examples and artifacts.

### Current checkpoint

Source audit confirms that Coding RPC is an actual separate mode while newer ACP
and `core/sessions/` share one owner/controller. They must not be cut together.
It also found Keychain/WinCred implementations in a dedicated helper plus a native
store dispatcher, while Linux Secret Service already uses its own owned helper.

UI-045 candidate removes that non-Linux helper/dispatcher and platform-only tests,
rejects old backend names before I/O, narrows extension-provider native storage to
Linux Secret Service, and updates current credential documentation. It leaves Linux
file/Secret Service stores, no-fallback semantics, helper timeouts/cancellation and
existing host settings precedence intact. Dedicated removed-adapter tests are
replaced by negative Linux boundary tests, not deletion of retained Linux tests.

Focused credential/config/provider tests: **32 passed**. These cover controlled
fixtures and unavailable-helper refusal; they do not claim live Secret Service or
non-Linux validation. Scoped Ruff passes. Final source/package gates remain pending.

## Acceptance and execution policy

- Inspect code before edits; separate shared components before removing adapters.
- Unknown/removed controls must fail closed without implicit execution or data loss.
- No replacement Agent loop, second session controller, permission expansion or
  removal of existing snapshot/skill/compaction/owned-cleanup capabilities.
- Use the current balanced test runner for final full coverage; targeted tests and
  source gates run at safe checkpoints. Do not omit retained security tests to pass.
- Preserve pre-existing untracked `docs/agent-ui-features.md` and `workflows/`, and
  other concurrent edits. No commits/push/tags/releases, production gateway restarts or user-store
  migrations are authorized by this goal. The user subsequently authorized local
  dependency preparation with `uv sync` for validation (see checkpoint below).
- Native non-Linux testing is not required. This goal removes those adapters and
  support claims; it does not relabel older deferred platform checks as passed.


### Initial environment limitation

The first broad `mypy --strict -p xdog.agent` check reports 11 errors in unchanged
ACP modules because optional `acp`, `acp.interfaces` and `acp.schema` are absent
from the current environment. This is **not** a reason to remove ACP or suppress
its types. The final eight-package gate is not passed; resolve the dependency
verification environment at a later checkpoint without installing anything
silently. The credential modules' targeted check is recorded separately and
cannot substitute for the complete source gate.

Evidence: `/tmp/xdog-ui-simplification/credentials-mypy.log` and
`credentials-targeted-mypy.log`. Goal remains active; no requirement is marked
complete at this checkpoint.

## Checkpoint: diff UI and Linux-only shared paths

UI-012 implementation candidate removes both clients' `/diff` dispatch, command
completion entry, review-only Alt+D binding, focused browser routing/lifecycle,
and standalone `RepositoryReview` / `ReviewPanel` exports/implementation. It does
not remove ordinary tool-output `Diff` rendering, basic editor word deletion,
Agent bash/filesystem tools, permissions, session navigation or local history.
Removed-browser tests are replaced by negative API/shortcut and retained real Git,
permission-denial/draft/focus tests. Focused UI regression: **107 passed**;
scoped Ruff and four changed-module strict mypy checks passed. Final integration
is still pending; UI-012 is not marked complete.

PLATFORM-LINUX remains **pending implementation** as a whole; it is not a second
count for each adapter. The clipboard slice removes macOS/Win32 helpers,
DIB-only conversion/color-profile code, their exclusive implementation tests,
Apple helper fallback and now-unused terminal fixture overrides. It retains
Linux Wayland/X11 reads/writes, image admission, explicit missing-helper feedback,
private output fallback, timeout/cancellation and owned process-group reaping.
Controlled Linux clipboard regression: **23 passed**; not live desktop proof.

Shared persistence now always takes the existing Linux flock/fsync paths instead
of retaining non-POSIX no-lock/no-directory-sync downgrades. MCP stdio retains its
Linux process group and owned cleanup. Eyes uses Linux's107-byte Unix pathname
capacity with new ASCII/UTF-8 boundary tests; no ACP routing/receiver/controller
behavior is removed. Native job-control signal refusal remains fail-closed.
The unchanged `test_draft_crash_windows.py` tests **crash timing windows on Linux**,
not Windows OS; these security/recovery tests are retained. Runtime OS environment
metadata and a vendor wire-protocol User-Agent token are not platform adapters
and are not rewritten under this cleanup.

Persistence/transport/terminal/eyes-loader regression: **144 passed** with explicit
repository and eyes source on PYTHONPATH. The first attempt exposed that this
venv also lacks an eyes editable install; it failed54 loader cases on imports,
not platform behavior. Source-path verification fixes module discovery without
installing or altering dependencies. Optional ACP SDK is still absent, so this
does not claim the ACP integration/full eight-package gate passed.

All-package Ruff, eight changed-module strict mypy, offline lock consistency and
`git diff --check` pass. Current readmes and all nine distribution classifiers
state Linux-only support; older audits are explicitly superseded for changed
scope. No version bump, dependency install, commit/push/release, gateway restart,
or user-data migration was performed. Full configured suite/examples/artifacts
remain required after the remaining implementation.

Next critical slice: remove the dedicated Coding RPC surface after migrating
retained mixed-test invariants to SDK/print/native/controller tests. Source audit
found no native/ACP imports of the RPC implementation outside `main.py`; tests
still share `_handle_prompt`, so deleting all consumers blindly is not acceptable.

## Parallel implementation checkpoint

User explicitly authorized **at most3 child agents**. Three workers were created
and reused, not more: RPC→queued-image return; terminal API→complex extension UI;
MCP picker→native host integration. Parent owns server/core integration, scope
choices, historical reconciliation, current ledger and final regression.

Dedicated Coding RPC is removed, including CLI/public parameter and all23 modules.
Its76-path manifest and migration details are in
`/tmp/xdog-ui-simplification/rpc-removal-report.json`. Mixed safety cases are
migrated, not discarded with the protocol: image capability withdrawal, context
limits, handled input/finalization failures, controller/Inbox inspection/cancel
isolation, borrowed ownership, draft attachments and local history/export/tool
pairing remain tested. Bounded worker result336passed with2ACP-dependent cases
explicitly deselected due the absent optional SDK; strengthened retained history
case1passed. These are NOT a full-suite green claim.

Claw remote parent-draft server/client/receiver/shortcut and plugin terminal
transport are removed. Removed-protocol tests7passed and native gateway/site
regression92passed. Local parent persistence is retained. MCP manual picker shared
UI is removed; protocol backend and correct extension argument completion remain.
Its sharedUI/schema/content/cancel tests172passed before native host integration.
Terminal interface worker reported113focused tests passed; larger complex SDK
custom/form/widget and hostUI cleanup remains under implementation, not closed.

All26 requirements remain unfinished: **13待实现、13待验证、0缺外部环境、0已完成、0延期**.
This reflects candidates awaiting frozen integration, not13 completed features.
Whole TUI786passed and combined234passed are earlier checkpoints invalidated as
final gates by newer edits; do not reuse them as the final complete regression.

Dependency/source findings for19/28/29/48 are in
`agent-ui-simplification-dependency-audit.md`; no service is inferred from a test
fixture. The checked deployment has zero MCP servers and default file credentials.
The scope decisions are not silently converted to unimplemented deletions.
Final environment preparation needs ACP SDK/eyes/build tools; user was asked for
`uv sync` permission, and no install has occurred. This does not defer a requirement
or block unrelated implementation.

Count validation: `python tools/agent_ui_simplification_checklist.py`.
The old ledger explicitly points to its successor and its renderer labels its
64-row counts/evidence as historical, not active source gates or new requirements.

### Authorized validation environment preparation

User explicitly approved `uv sync` on2026-10-16. `uv sync --dev` succeeded,
installing the workspace editables including eyes and optional ACP SDK0.12.1.
Evidence: `/tmp/xdog-ui-simplification/authorized-uv-sync.log` (exit0).
This resolves the earlier missing-ACP/eyes environment limitation; it does NOT
retroactively convert deselected/skipped tests into passing results. Rerun the
relevant ACP/eyes checks and final full gates. No live gateway/service was
restarted, and no commit/tag/release was made. Standard isolated build preparation
for final local artifacts is within this approved validation setup, not publishing.

### Current candidate and retained-path checkpoint

Current authoritative categories: **10待实现、16待验证、0缺外部环境、0已完成、0延期**
(total26, unfinished26). UI-018 and UI-031/037 now have implementation candidates,
not final completion. UI-018 actual main-Agent tool supports typed image output,
private process-local owner rotation/cancellation and original revision/token/
receipt/retained-byte guards. Its207-case bounded matrix and expanded50-case
image-context checks pass (overlapping commands; not257 distinct requirements).
Remaining native transport glue is being reconciled by the client owner.

Complex plugin UI is removed at SDK/host layers; internal native review fields
were extracted before deleting public form/custom codecs, retaining their seven
security tests. Generic native overlays/fullscreen/rendering are not removed.
Worker reports469retained SDK/child tests before final codec deletion and54private
review/renderer/approval checks; final consolidated source/test gates pending.

Post-authorized-sync **280ACP/Eyes tests passed**, including all eyes tests and
Coding ACP suites with no deselections. This supersedes the earlier missing-SDK
limitation, not unrelated implementation. Retained Flow suite **813passed** and
workflow examples **17passed**. These are current retained-path checkpoints, not
a final frozen workspace result; avoid repeatedly running broad suites while the
same files are still changing.

Sampling SDK and native adapters are now being removed after the bounded actual
service audit; Elicitation/Roots and shared fixtures must not disappear with it.
UI-048 stays pending implementation until its complete retained capability
contract is settled and implemented. No OAuth/store/draft-scope deletion is
inferred from the Sampling decision.

### Latest integration notes

Direct `uv run pytest` cross-package fixture discovery initially failed because
the repository namespace was not on sys.path under importlib collection. Root
pytest now adds **only the repository root** (`pythonpath=["."]`), not package
runtime source paths. Direct-console regression31passed without a PYTHONPATH
workaround. This is test harness configuration, not a change to installed runtime
module discovery. Existing official `python scripts/test.py` remains the final
complete configured runner.

Parent review caught a new retirement-guard bug during Sampling cleanup: a stale
retired callback could clear a newer MCP panel's busy state. The retained-owner
regression went red1failed/25passed, then green26passed after pure refusal stopped
mutating the panel. The stale-owner test was preserved, not weakened.

The UI018 worker's RPC→return task finished; that same agent was closed then
resumed for UI032 SDK readonly-host cleanup. There are still at most3 known child
agents, with native clients owned by a different worker to avoid file collisions.
Draft scope19 has a focused user clarification pending (same-session transient
child recovery vs child disk UI); no draft data is removed, and other work
continues. UI028/029/048 audit decisions remain in scope, not deferred by silence.

### Historical UI021/032 and core lifecycle checkpoint

At this historical checkpoint: scope26, unfinished26: **8待实现、18待验证、0缺外部环境、0已完成、0延期**.
The authoritative current counts are at the top of this document and in the ledger.
UI021 removes the child autosave controller/controls/tasks only, retaining primary
and manual-child IO barriers; its198focused checks pass. UI032 now removes11SDK
host write/control methods and keeps10readonly queries, basic dialogs and business
registration. SDK349checks and native823offline/19socket/77followup checkpoints
pass. Parent core routes now provide readonly queries/basic dialogs without
message/compaction/tool-selection/context/editor grants or after-run controllers.
Native USER controls remain separate. Its34combined core/child/provider/compaction
checks pass and three changed core modules typecheck strictly.

A parent cleanup temporarily dropped the default gateway dispatch call, which
caused unanswered ping. It was restored with only retained capability families;
66core cases passed. A targeted process already running the broken imported
version stalled during teardown. Its identity/log/zero-progress and cleanup are
recorded in `ui032-owned-hang-evidence.json`; only owned pytest813323 received
SIGINT then SIGTERM after it failed to settle. Other tests, old unrelated orphan
workers and user services were not terminated. This interruption is not a pass.

A new bounded test then reproduced a real CURRENT LINUX shutdown ordering issue:
`server.wait_closed()` was awaited before canceling handlers that own open client
transports. Shutdown now closes the listener, rejects late accepts, cancels/joins
handlers, then awaits server closure. Red1failure is preserved; the complete
shutdown/native gateway/input/image group46passed with this new case. No service
was restarted. Core after-run mutation modules were removed, generic socket test
helpers extracted, pure retired controls changed to refusal tests, and all three
native compaction cancellation modes retained. This is within retained lifecycle
acceptance, not an extra requirement count.

Primary-draft storage audit: UUID snapshot filenames are lexically sorted, not
latest/timestamp ordered. Stored scopes are opaque transient identities, not a
stable group/project namespace. Therefore22/23/24/47 use a bounded explicit
ID/provenance snapshot selector and full preview, NO latest guessing, migration,
pruning/delete or new timestamps. Existing primary store/autosave CAS semantics
and manual-child storage remain unchanged pending19. Main recovery is being
decoupled from opening the child browser.


## Earlier coordinated verification checkpoint (superseded below)

At that checkpoint the ledger had **26 unique requirements: 4待实现 / 22待验证 /
0缺外部环境 / 0已完成 / 0延期**. The four implementation boundaries pending
user confirmation are UI-019, UI-028, UI-029 and UI-048; they are not waived.
The latest user approval concerned temporary independent MCP SDK test setup,
not deletion of child manual persistence or further MCP capabilities.

UI-022/023/024/047 now have candidate implementation: a standalone primary draft
save/recovery surface, at most32 explicit snapshots with ID/provenance, full
text/image review, protected focus and text/images-only restore. Create-only
manual saves, exact-owner autosave/CAS, crash/write-failure/quit protection and
existing user files remain. No latest guessing, migrations or automatic pruning.
Evidence: `primary-draft-minimal/report.json` (274 focused checks).

UI-032 generic broker and validators now reject retired controls before request
allocation/host dispatch; all six inert runtime mutation handler attributes are
gone. The historical context image codec was extracted rather than deleting
persisted history support. See `ui032-broker-report.json` (492 bounded checks).
The earlier private `_ReviewFields` extraction was transitional: its removed
control consumers are gone, so this unused component was subsequently removed.
Native `_JsonReview`, basic dialogs, provider auth and retained Elicitation remain.

Approved temporary setup installed MCP SDK core/server2.3.1 under `/tmp`, with
npm scripts disabled and no workspace dependency addition. All formerly optional
independent SDK checks plus loopback Sampling retirement checks now pass:
**61passed, no skips** (`authorized-mcp-independent-tests-python-module.log`).
Direct package-selected pytest initially used the package-local configuration
and failed to import repository test helpers; package-local pytest configs now
add the repository root too, without injecting runtime source directories.
The import collection check covers43 cases and succeeds without PYTHONPATH.

The frozen source checkpoint passed Ruff and strict mypy for **all8 packages,
552 source files**, offline lock consistency, and built **9 distributions /
18 artifacts**. Isolated artifact checks verify12 imports from extracted wheels,
5 deleted imports rejected, Linux metadata, absent retired modules and232
identical overlapping umbrella/package code members. These checks do not publish
or install those artifacts. The complete configured eight-package runner is
currently executing with the approved independent MCP SDK environment enabled;
its result is not yet claimed. Workflow examples pass17 cases.

All seven originally unrelated file hashes remain unchanged. No commit/staging,
push/tag/release, user-service restart or user-data migration was performed.


### Full-run failure audit and Linux EOF/shutdown correction

The first complete runner finished with failed Coding/Claw paths, not a pass
(`workspace-candidate-regression.log`, exit1). Six other package paths passed.
Two old-import test workers were confirmed stranded for more than5minutes with
unchanged owned test sockets: Coding catalog capability withdrawal and Claw's
loopback confidential OAuth review. The underlying Linux bug is cancellation of
an already admitted client-finally cleanup after EOF: it skips writer closure,
leaving server.wait_closed() stranded. A bounded deterministic test reproduced
it (red1) and a single shielded/joined client cleanup owner fixes it. The transport
now closes even when a cleanup step fails; repeated cancellation cannot abandon
admitted cleanup. Retained shutdown/OAuth/catalog/combined-watch checks pass18.

Only the identified hung workers829602/831127 received SIGINT, then SIGTERM when
they did not settle; no healthy worker, Codex process, user service or unrelated
orphan was terminated. The peer owned by829602 exited too. The interrupted tests
remain failed in the original run; a fresh runner is required. Evidence:
`client-eof-owned-hang-evidence.json`, `client-eof-shutdown-red.log`,
`client-eof-shutdown-fixed-final.log`.

The same full run exposed stale extension snapshot fixtures that still included
removed terminal fields (Claw argument completion); validation correctly rejected
them. Fixtures now validate the retained contract and keep all stale/cancel/
full-context assertions (red3 -> green8). A native approval test also assumed
pending permission admission and async UI publication were simultaneous. Its
replacement explicitly holds publication, proves no effects while unpainted,
releases delivery and bounds polling for the exact batch approval, preserving
both deny/allow-once checks (5batch display checks pass). This fixes a test
scheduling assumption, not a new permission grant or stamp bypass.


## Latest verified checkpoint — remaining4, goal active

The authoritative current ledger contains **26 unique requirements**:
**4待实现 / 0待验证 / 0缺外部环境 / 22已完成 / 0延期**. Remaining IDs are
**UI-019, UI-028, UI-029 and UI-048**; all await the bounded scope confirmation
already requested from the user. Completion of the22 independent requirements
is based on the frozen current source, not a claim that the whole goal is done.
Any further implementation must rerun the complete final regression gate.

The fresh configured runner passed **5719 tests across all8 package paths**
in110.11seconds, with the approved independent MCP SDK2.3.1 enabled. Its7 skips
are the existing opt-in Codex/Claude CLI integration checks, NOT MCP tests or
non-Linux validation. No external-client interoperability pass is claimed.
Current Linux physical Coding/Claw terminal review/cancel/resize/termios tests
ran. Ruff passes all packages; strict mypy passes552 source files across all8
packages; workflow examples pass17cases; offline lock consistency passes116
packages. Evidence: `workspace-postfix-{regression,ruff,mypy}.log` and
`workspace-candidate-examples.log` in `/tmp/xdog-ui-simplification`.

Fresh post-fix builds pass for root+8 distributions,18 artifacts. The artifact
manifest (`postfix-artifacts.json`) records hashes/sizes, Linux metadata,
retired-module absence, isolated imports from extracted wheels, retained SDK
lifecycle/snapshot/skill/compaction and readonly extension API presence, removed
extension mutation APIs and no Coding run_agent(rpc=...) signature. No artifacts
were published or installed.

The readonly command-catalog regression was repaired without loosening the worker
validator: a separate trusted broker entry admits host routing metadata only for
commands; pending questions and answer validation stay free of host-only fields.
Workers still cannot inject command_menu. Native readonly/broker/catalog checks
pass163. The physical terminal tests now exercise retained Elicitation instead
of removed Sampling, preserving all4 host/cancellation cases and no-model,
result-withholding, resize and termios assertions. The worker's consolidated
readonly/terminal migration checks pass67. Current READMEs have been reconciled.

UI-024 acceptance wording was corrected to the user's original boundary: remove
exit branches for **deleted** draft features while retaining necessary in-flight
manual-write barriers. This is not authorization to delete UI-019 manual child
persistence. It prevents accidentally weakening exit protection or double-counting
the still-pending child-draft scope under a second requirement.

The seven originally unrelated file hashes remain unchanged; no staged files,
commits, push/tags, service restart, release, credential/draft migration or user
file deletion. All child agents are closed after handoff. Goal remains active.


### Remaining4 preparation, no additional removal approved

Source trace now distinguishes modern Roots input continuations from optional
Elicitation/Tasks and ordinary SSE recovery. A new12case roots-only baseline plus
the existing loopback retirement cases passes35checks without registering any
optional review/task handler. Only tests/fixture and audit text changed after the
5719passed full-workspace checkpoint; no new full-run total is claimed.

The UI-029 trace found that the native credential helper is also used by
extension-provider explicit save/restore/forget, not just MCP credentials.
Deleting Secret Service globally would retire those optional vault operations
too; it must not silently replace them with plaintext files. This consequence is
now part of the pending confirmation. OAuth saved-profile binding, primary shared
draft primitives and Agent-owned return backups are explicitly retained boundaries.
See `agent-ui-simplification-dependency-audit.md` for source paths and contracts.
Counts stay4remaining/22complete; no scope is silently removed or deferred.


### Scope-confirmation blocker audit

The same unresolved UI-019/028/029/048 scope confirmation now spans three
consecutive goal turns. The previous turn made real progress:12new roots-only
cases and shared-continuation/provider-store source evidence. Current revalidation
confirms those tests settled normally, the exact4 remaining IDs, successful full
checkpoint/artifact results and unchanged unrelated originals. No outstanding
safe implementation remains before choosing the unanswered boundaries; repeatedly
rerunning unchanged suites is not progress. The goal is therefore **blocked,
not complete or user-paused**. No automatic continuation is treated as approval.
All26 requirements, original completion gates and the4 remaining pending statuses
are preserved. User confirmation or revised scope unblocks implementation.


## User clarification — remaining scope implementation resumed

The user has decided **UI-029 is not removed**. Keep explicit Linux file and
Secret Service choices, including extension-provider native credentials; never
migrate, silently downgrade or write credentials during ordinary login. The other
three previously proposed reductions now proceed under the stated boundaries:
UI-019 same-session transient child recovery/no disk UI, UI-028 pre-registered
public PKCE only, UI-048 configured Roots only/no optional interaction or Tasks.
Shared primary drafts/Agent backups and ordinary transport safety are preserved.
The former scope-confirmation blocker is resolved; no new approval loop is needed.
Current categories:3待实现 /1待验证 /0缺外部环境 /22已完成 /0延期.
Three existing child agents are reused with disjoint write sets: OAuth SDK,
child draft TUI, and optional MCP SDK; parent owns manager/native/core integration,
current ledger and final full verification. Previous frozen checks remain
checkpoint evidence, not the final gate for these new source changes.


### Final source integration checkpoint

Current unique counts: **0待实现 /3待验证 /0缺外部环境 /23已完成 /0延期**.
UI-029 is retained as the user decided, with explicit Linux file/Secret Service
choices and provider vault operations; no fallback or data migration. Its retained
safety checks pass. UI-019/028/048 implementations are now integrated; final gates
remain before closing them. Source strict types pass all8packages543files.

The three source slices have focused evidence: child TUI51/native118, public OAuth
and retained credentials/native228, roots-only MCP Python443/independentSDK24.
These counts are evidence, not requirement counts. Test-only negative migrations
preserve pending-owner/late-callback protection; an integration red18 exposed
unknown retired actions mutating native view IDs, and a bounded allowlist refusal
was restored before state mutation. Removing mutable Roots cleanup also exposed
an obsolete detach_roots call preceding detach_auth; it was removed, and the
unchanged public waiting-login cancellation test passes again. No safety test was
deleted to hide these bugs. The EOF shutdown-race test now gates retained
McpSender.close rather than removed ElicitationConnection.close.


## Final completion audit — 26/26, remaining0

All **26 unique requirements** now have verified completion:
**0待实现 /0待验证 /0缺外部环境 /26已完成 /0延期**. UI-029 is retained by
the user's latest decision, not silently deferred or deleted. No scope-confirmation
blocker remains. Earlier checkpoints/failures above are historical, not the final
result. Stable IDs and per-item final test sources are in the JSON ledger and
`/tmp/xdog-ui-simplification/final-integration/completion-audit.json`.

Final frozen full runner: **5782passed /7skipped**, all8 configured paths,
108.28seconds, independent MCP SDK enabled, no test selection/deselection. Skips
are the existing opt-in Codex/Claude CLI integrations; they are not claimed passed.
Strict mypy passes543sourcefiles across all8 packages, Ruff all packages,
workflow examples17passed, offline lock116packages and whitespace checks pass.

The preceding full run's one schema-helper timeout was investigated, not hidden
or skipped. A deterministic cold-start test reproduced it. A fixed private
readiness handshake now gives cold imports a separate bounded startup deadline
while leaving schema execution limits unchanged. Startup timeout, malformed ready
packet, validation timeout and cancellation all reap the exact helper. Retained
schema/Roots/HTTP and vendored Flow helper checks pass86; the final full runner
above includes these new cases. No schema is retried or given an unbounded budget.

Fresh root+8 distribution builds produce18artifacts. Checks verify all814 shipped
source/asset members against current workspace content, all8 configured README
metadata bodies, Linux classifiers, retired modules/APIs absent, retained SDK
lifecycle/snapshot/skill/compaction/readonly APIs, isolated wheel imports and a
real isolated schema-helper compile/validate/close. Site metadata has no configured
README; that existing absence is reported, not counted as a README check. Evidence
is `artifacts-after-schema.json`; all9 distributions are checked. No publication.

Actual Linux both-host terminal tests cover retired-key refusal, ordinary MCP
explicit prompt preview, no model/automatic submission, viewport resize, editor/
cursor/termios restoration and owned process/thread cleanup. Other retained
permission/controller/ACP/inbox/Flow/security/crash/transport gates run in the
complete suite. The two Linux credential backends and provider vault remain;
real SecretService interoperability is not claimed from simulated helper tests.
No live provider/vendor service or non-Linux-native validation is claimed.

All seven originally unrelated hashes remain unchanged, Git staging is empty,
HEAD remains e9f27f6. No commit/push/tag/version bump, release, gateway restart,
credential/draft migration or user-data deletion. All child agents are closed.
