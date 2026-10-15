# ACP Endpoint 与 Session Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Execution method already selected: Native; no implementation subagents. Preserve the approved final independent whole-branch review.

**Goal:** Eyes 不依赖 coding CLI 而直接经 ACP 向指定 session 投递；独立接入服务在 TUI/owner 不运行时持久收件，显式批准后由唯一 controller 执行；删除旧 event host 并验证完整闭环。

**Architecture:** 保留已提交的 agent ACP、coding controller/lease/owner endpoint 与 TUI presenter。Eyes 新增判别式连接配置；轻量 coding ACP ingress 只路由标准 prompt 或持久 enqueue，不创建模型 runtime。独立 inbox 存待审消息/attempt，controller 在现有 turn 生命周期内领取和结算，TUI/CLI/ACP 展示同一事实。

**Tech Stack:** Python 3.12+、uv、`agent-client-protocol>=0.12.1,<0.13; python_version < '3.15'`、asyncio、frozen dataclasses、Unix-local transport、fcntl locks、fsync/atomic replace、pytest/pytest-asyncio、Ruff、strict mypy。

**Specs:**
- `docs/superpowers/specs/2026-10-15-acp-endpoint-callback-design.md`（用户于 2026-10-15 确认）。
- `docs/superpowers/specs/2026-10-15-session-inbox-design.md`（用户于 2026-10-15 确认，endpoint 补充设计优先）。
- `docs/superpowers/specs/2026-10-15-acp-session-routing-design.md`（其余已批准约束）。

**Status:** 本计划等待用户审核；Native 执行选择不变，尚未开始本计划的产品代码修改。

## Global Constraints

- 原 checkout `/data/workspaces/pyspace/xdog`、现有 feature branch；不建 worktree，不自动 push/PR/发布。
- 不重新实现已完成原 Tasks 1–7。原 Task 8 的 command-only 接口由 R1/R6 替换；原 Task 9 由 R7 替换；原 Task 10 由 R8 完成。
- Eyes 只依赖 ACP SDK，不 import ai/agent/coding/claw/flow，不读取 coding registry/inbox，不启动 coding CLI。
- `connection` 明确 Unix endpoint 或通用 stdio argv；Unix 绝不创建子进程。无 shell、隐式 latest/current/session/new fallback、TCP、Windows、系统服务安装或自动启动模型。
- 接入服务显式 start/stop，生命周期独立于 TUI、owner 和 Eyes；服务离线明确不可达。服务关闭不取消其他客户端/人类 turn，不删除已 ACK inbox。
- enqueue/status 不构造 AgentSession、不取得 session writer lease、不运行模型、不要求活跃 owner 或先 load。
- 同一 session 唯一执行 owner，模型 history 仅由持 lease 的 controller 写；所有工具权限仍经原 hook。证据、自报 source、enqueue 或 resume 请求不能自授人类身份/预算/权限。
- 接收 ACK 只在消息/目录 fsync 后；标准 prompt 等最终结果，update 不是 completion；prompt outcome-unknown 不重发。
- inbox 单条最多 1 MiB、128 条非终态、4096 个权威去重记录；重复 ID/相同内容返回原状态，内容冲突拒绝；不截断证据、不自动驱逐去重 ID。
- 每个消息的 `message_id` 与每次执行的 `attempt_id` 分开；批准绑定当前 owner generation/branch，不持久化永久授权；running 崩溃 interrupted，不自动重放副作用。
- runtime directories 0700、regular files/locks/socket 0600、UID/type/mode/no-symlink；不将同 UID 防护称为安全沙箱。
- SDK optional-import 与 Python `<3.15` marker 保留；非 ACP validate/native/RPC/navigation 不因缺 SDK 失败。
- 保留旧 `xdog.external_event.v1` 历史只读兼容，但物理删除旧 host/capability/env/transport；InputStore 不成为可执行 inbox。
- TDD 每任务真实 RED→GREEN、记录日志、局部验证、提交；全分支验证与独立审核后才可宣称完整目标完成。

## Review Focus

1. 磁盘已提交但 enqueue ACK 丢失，或并发重复 ID：不得换新 ID/第二次入队；R2/R3/R6 断连和并发测试。
2. 两个 ingress/observer 启动，live socket 超时或 PID 重用：不能推断进程已死、偷锁或删除活 socket；R3/R7 lease/timeout/inode 测试。
3. 批准等待期间 branch/owner 变化、approved 队列未运行便退出：不得跨上下文消费或保留批准；R4/R5 generation/close 测试。
4. inbox 已标 running 后进程崩溃，以及终态 fsync 失败：不能重放工具副作用或继续后继自动工作；R2/R4 subprocess crash/fault injection。
5. ACP 接入在线但 owner 不在线，或任意 SDK 导入缺失：enqueue 可用不等于 prompt 可执行；R3/R5/R8 no-Agent/optional-import 测试。

---

## Files / responsibilities

| Unit | New files | Existing integration |
|---|---|---|
| Eyes connection | `eyes/connections.py` | `models.py`, `loader.py`, `acp_client.py`, `handlers/acp.py` |
| Durable data | `coding/core/sessions/inbox_models.py`, `inbox_store.py` | `config.py`（共享 inbox 根） |
| Lightweight ingress | `coding/acp/ingress_backend.py`, `ingress.py`, `ingress_main.py` | `acp/client.py`, `acp/cli.py`, generic adapter contract |
| Owner consumption | `coding/core/sessions/inbox_runtime.py` | `controller.py`, `runtime.py`, `agent_session.py` |
| Review/display | `coding/core/inbox_commands.py` | `core/slash_commands.py`, native/RPC session event presenters, CLI args/main |
| Eyes delivery | none | `events.py`, `executor.py`, `handlers/acp.py`, `acp_client.py` |
| Eyes lifecycle | `eyes/local_control.py` | `runtime.py`, `process.py`, `control.py`, `cli.py` |
| Deletion/docs/release | `docs/acp-session-routing.md`, regression tests | original Task 10 exact deletion set, READMEs/AGENTS/skill/examples/build audits |

Prefix `eyes/` above means `packages/eyes/src/xdog/eyes/`; `coding/` means `packages/coding/src/xdog/coding/`. Test paths below are exact repository-root paths.

## Shared interfaces (implement once; consumers must use these names)

### Connections — R1

```python
@dataclass(frozen=True)
class UnixConnection:
    path: Path
    transport: Literal['unix'] = 'unix'

@dataclass(frozen=True)
class StdioConnection:
    command: tuple[str, ...]
    transport: Literal['stdio'] = 'stdio'

ConnectionDef = UnixConnection | StdioConnection

@dataclass(frozen=True)
class HandlerDef:
    id: str
    type: Literal['acp']
    connection: ConnectionDef
    session: str
    instruction: str
    delivery: Literal['prompt', 'enqueue'] = 'prompt'
```

`AcpClient(connection: ConnectionDef, session_id: str, cwd: Path, *, delivery: Literal['prompt','enqueue']='prompt')` retains `connect/prompt/generation/close` and adds `enqueue(parameters: dict[str, JsonValue]) -> dict[str, JsonValue]`, `inbox_status(parameters: dict[str, JsonValue]) -> dict[str, JsonValue]` in R6. Prompt mode requires explicit load; enqueue mode negotiates extensions and validates target via inbox_status without load. Eyes-local `JsonValue` must not import agent ACP types.

### Inbox records/store — R2

```python
InboxState = Literal['pending_review','approved','running','succeeded','failed','cancelled','interrupted']

@dataclass(frozen=True)
class InboxMessage:
    session_id: str
    cwd: Path
    message_id: str
    instruction: str
    evidence: tuple[JsonValue, ...]
    source: str
    provenance: dict[str, JsonValue]

@dataclass(frozen=True)
class InboxRecord:
    message: InboxMessage
    branch_id: str
    status: InboxState
    owner_generation: str = ''
    attempt_id: str = ''
    request_id: str = ''
    error: str = ''
    attempts: tuple[InboxAttempt, ...] = ()

@dataclass(frozen=True)
class InboxAttempt:
    id: str
    request_id: str
    generation: str
    branch_id: str
    status: str
    error: str = ''

@dataclass(frozen=True)
class EnqueueReceipt:
    duplicate: bool
    record: InboxRecord
```

`JsonValue` in coding inbox comes from `xdog.agent.session_input` (not ACP, so no SDK requirement). Canonical snapshots deep-copy JSON like existing SessionInput; frozen dataclasses alone do not make nested dicts immutable.

```python
class InboxStore:
    def __init__(self, sessions_dir: Path, root: Path): ...
    def enqueue(self, message: InboxMessage) -> EnqueueReceipt: ...
    def records(self, session_id: str, cwd: Path) -> tuple[InboxRecord, ...]: ...
    def approve(self, session_id: str, message_ids: tuple[str,...], *, cwd: Path,
                branch_id: str, generation: str) -> tuple[InboxRecord,...]: ...
    def claim(self, session_id: str, message_id: str, *, generation: str,
              branch_id: str, attempt_id: str, request_id: str) -> InboxRecord: ...
    def finish(self, session_id: str, message_id: str, *, generation: str,
               attempt_id: str, status: str, error: str='') -> InboxRecord: ...
    def retire(self, session_id: str, generation: str) -> None: ...
    def recover(self, session_id: str, generation: str) -> tuple[InboxRecord,...]: ...
```

`InboxAttempt` is defined before InboxRecord (or use postponed annotations). Append attempt identities/results instead of overwriting the prior attempt when retrying; the immutable message body is not duplicated. Bound the encoded record including attempt history to 2 MiB and refuse a new attempt explicitly when full; never prune prior attempt evidence automatically.

Store methods are synchronous locked disk operations; callers in async paths use `asyncio.to_thread`. `approve/claim/finish/retire/recover` are trusted owner-only APIs, never exposed as raw client mutation methods. Actual session writer lease is checked by R4 before any call. `recover` only called by new owner after lease acquisition, never by ingress readers.

`get_inbox_dir() -> Path` in coding config returns `get_data_dir() / 'inbox'`, i.e. `$CODING_DIR/inbox` when CODING_DIR is explicit. Ingress options permit explicit `sessions_dir` and `inbox_dir`; owner uses the same configured store, not a path supplied in an enqueue request. Add optional `inbox_store: InboxStore | None = None` to SessionRuntime's constructor; the default is `InboxStore(session.session_manager._dir, get_inbox_dir())`, while Python API tests/custom integrations pass the identical explicit store to ingress and owner. Never derive an inbox path from client payload.

### Ingress — R3

```python
@dataclass(frozen=True)
class IngressConfig:
    sessions_dir: Path
    inbox_dir: Path
    address: Path

class IngressBackend:
    extensions: dict[str,int]  # enqueue/inbox_status/session_info/resume v1, only implemented paths
    # Implements existing agent.acp.contracts.Backend; new_session always rejects.

class IngressServer:
    @classmethod
    async def start(cls, config: IngressConfig) -> IngressServer: ...
    async def aclose(self) -> None: ...
```

No AgentSession factory on ingress. Each ACP peer has its own backend/remote attachment tracking. Owner attachment uses existing `AcpAttachment` and verified registry, never a second local SessionRuntime. `xdog/session_info` and `resume` require a loaded owner; enqueue/status do not.

`xdog/enqueue` parameters exactly mirror InboxMessage with JSON lists and cwd string. Returns `{session_id,message_id,status:'queued'|'duplicate',message_state,branch_id}`; `queued` is the durable receipt, not execution completion. `xdog/inbox_status` accepts `{session_id,cwd}` and optional `message_id` or integer `cursor` (mutually exclusive); returns `{session_id,cwd,branch_id,messages:[...],next_cursor}`. Page at most 64 bounded status summaries (IDs/state/branch/attempt IDs, no full evidence); validate cursor range 0..4096. This prevents terminal dedup history from exceeding the 1 MiB ACP frame. Single-ID status returns at most one summary. Owner UI details read full content locally via the store, never embed all terminal payloads in status. No client-set origin, permission, approved, owner_generation, attempt_id, storage path or branch override.

### Owner review — R4/R5

```python
class InboxCoordinator:
    def __init__(self, runtime: SessionRuntime, store: InboxStore): ...
    def is_submission(self, request_id: str) -> bool: ...
    async def snapshot(self) -> tuple[InboxRecord,...]: ...
    async def approve(self, message_ids: tuple[str,...], *, peer_id: str) -> tuple[str,...]: ...
    async def retry(self, message_id: str, *, peer_id: str) -> str: ...
    async def starting(self, value: SessionInput) -> None: ...
    async def finished(self, value: SessionInput, result: TurnResult) -> None: ...
    async def retire(self) -> None: ...
```

`SessionRuntime.inbox: InboxCoordinator` is constructed once from the configured store before start/endpoint publication; `controller.inbox` points to the same coordinator. Coordinator lives on runtime/controller owner loop; `approve` returns controller request IDs after acceptance, never waits for their results inside `controller.control`. `retry` only acts on explicitly selected interrupted message, creates a new attempt after permission approval. Controller has optional `inbox` coordinator independent of UI-owned `before_turn/after_turn`; do not overwrite native/RPC hooks. Core lifecycle hooks call coordinator before any model execution and before settling submission result. `SessionEvent(kind='inbox_snapshot', data=tuple[InboxRecord,...])` is display-only, bound to session/generation; request_id may be empty. Capture it under startup/navigation control reservation and dispatch through immutable UI events, not direct component calls. Snapshot pending/approved/running/interrupted plus at most 16 most recent terminal entries; older completed attempts remain in history or explicit paginated inspection, not an unbounded startup payload.

---

## R1: Eyes direct ACP connections, preserving third-party stdio

**Files:**
- Create `packages/eyes/src/xdog/eyes/connections.py`.
- Modify `models.py`, `loader.py`, `acp_client.py`, `handlers/acp.py` under Eyes source root.
- Modify `packages/eyes/tests/conftest.py`, `test_loader.py`, `test_acp_handler.py`, `test_acp_delivery.py`, `test_executor.py`.
- Create `packages/eyes/tests/test_acp_unix.py`.

**Consumes/produces:** Uses current SDK connect/spawn functions; produces ConnectionDef/HandlerDef/AcpClient interfaces above. R6 adds enqueue; R1 only changes connection shape and prompt transport.

- [ ] **1. Write RED parser and real Unix ACP tests.** Migrate fixture definitions to nested connection, existing stdio helper to `StdioConnection(tuple(argv))`. Add parser case:

```python
def test_unix_handler_is_frozen_and_does_not_require_command(tmp_path, observer_data):
    from xdog.eyes.loader import parse
    from xdog.eyes.connections import UnixConnection
    handler = observer_data['handlers'][0]
    handler.pop('command', None)
    handler['connection'] = {'transport':'unix', 'path':'/tmp/private/acp.sock'}
    result = parse(observer_data, base_dir=tmp_path)
    assert result.handlers[0].connection == UnixConnection(Path('/tmp/private/acp.sock'))
```

Import Path in the test. Add a real official-SDK Unix fixture in `test_acp_unix.py`: private short temporary directory, `asyncio.start_unix_server`; per accepted peer use an `acp.Agent` with initialize(load_session=True), load_session asserting exact `target/cwd`, prompt emitting one AgentMessageChunk and returning PromptResponse(end_turn), cancel setting an Event. Await server peer tasks during teardown. Use SDK `run_agent(agent, writer, reader)`; fixture has no coding imports. After server setup patch `xdog.eyes.acp_client.spawn_agent_process` to raise AssertionError; AcpHandler(UnixConnection) connect/deliver must succeed with `process is None`, close must not close the server. Reuse existing `delivery()` from test_acp_handler. Add malformed connection parameter cases: relative path, symlink, wrong mode, FIFO, mixed argv/path, empty argv, shell string, bool transport, unknown fields.

- [ ] **2. Observe RED:**

```bash
uv run python -m pytest packages/eyes/tests/test_loader.py packages/eyes/tests/test_acp_unix.py -q -n 0
# Expected: new ConnectionDef/handler parser or direct transport missing; not fixture failure.
```

- [ ] **3. Implement minimal shared connection lifecycle.** Parse nested discriminator with field paths; reject top-level command. Unix validates private endpoint and uses `asyncio.open_unix_connection(limit=1MiB)` + SDK `connect_to_agent`; register connection close and writer.close/wait_closed on AsyncExitStack in reverse-safe order. Stdio retains spawn/env/cwd/stderr behavior. All paths negotiate same ACP semantics; failed initialize/load closes only owned resources. Cancellation during connect closes writer/process even before conn assignment. No imports of coding registry, no reconnect.
- [ ] **4. GREEN/regressions:** run all loader/executor/ACP tests including generation/budget/permission/unknown-result tests. Keep temporary legacy-runtime gate until R7. Run Eyes strict mypy/Ruff.
- [ ] **5. Commit:**

```bash
git add packages/eyes
git commit -m "feat(eyes): connect ACP handlers directly to Unix endpoints"
```

## R2: Durable session inbox with identity, quotas and crash-safe state

**Files:**
- Create `packages/coding/src/xdog/coding/core/sessions/inbox_models.py`, `inbox_store.py`.
- Modify `packages/coding/src/xdog/coding/config.py`.
- Create `packages/coding/tests/test_inbox_store.py`, `test_inbox_crash.py`.

**Consumes/produces:** Store APIs and records above, existing session JSON naming/ID validation; no ACP SDK imports or AgentSession construction.

- [ ] **1. Write RED durable/dedup contract.** Use existing `coding_session` fixture only to provision persisted identity; store never uses its Agent.

```python
def test_message_identity_survives_store_reopen(coding_session, tmp_path):
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore
    message = InboxMessage(coding_session.session_id, tmp_path.resolve(), 'm1',
                           'Review', (), 'Eyes', {})
    sessions = coding_session.session_manager._dir
    root = tmp_path / 'inbox'
    first = InboxStore(sessions, root).enqueue(message)
    again = InboxStore(sessions, root).enqueue(message)
    assert first.duplicate is False and again.duplicate is True
    assert first.record == again.record
    assert first.record.status == 'pending_review'
```

Add parameterized conflict/content validation cases, 128 pending/4096 identity limits, malicious IDs/JSON/unknown fields, chmod/symlink/FIFO rejection, non-existing session, wrong cwd, branch snapshot. Test processes simultaneously enqueue one ID and assert one canonical record. Monkeypatch os.fsync/os.replace to fail at each publication boundary; no queued ACK before durable publication, retry same ID never overwrites conflicting content.

- [ ] **2. Observe RED:**

```bash
uv run python -m pytest packages/coding/tests/test_inbox_store.py packages/coding/tests/test_inbox_crash.py -q -n 0
# Expected: inbox modules missing.
```

- [ ] **3. Implement storage/transition rules.** Validate session identity by bounded, private, no-follow JSON read of existing `session_<id>.json`; don't call SessionData.from_dict or SDK/session constructors. Root/session directories 0700, lock/files 0600, bounded finite JSON and no duplicate keys. Use per-session flock; atomic temp write+fsync+replace+directory fsync before receipt. Stable digest hashes original message_id; compare entire normalized immutable message, never client auth fields. A duplicate preserves its original branch binding even if current branch changed; never rebind old receipt to a new branch. Durability checks/fsync also precede a duplicate ACK, so retry after an earlier fsync failure cannot claim an uncommitted record as durable.

Maintain one authoritative record under `pending/`, `running/`, `terminal/`: transition under the same inbox lock, write updated record atomically at its current path then rename to destination and fsync both directories. Reader searches all three and uses record state as authority, never duplicates by copying/unlinking. A crash between state update and directory rename is reconciled by current owner recovery; pending/running location mismatches never imply permission to execute. Contradictory duplicate files fail closed rather than arbitrarily picking one. Persist running/attempt before model execution; recover stale approved→pending_review, stale running→interrupted. Only new lease-owning owner invokes recovery; ingress read/status never downgrades live approved/running. Terminal persistence failure propagates to controller to stop subsequent work. IDs include interrupted attempts until explicit operator review/retry; no automatic archive/eviction.
- [ ] **4. GREEN plus process crash tests.** Subprocess exits via os._exit after running publication and before terminal publication; fresh owner recovery yields interrupted once, no execution. Test terminal state published but not moved is preserved. Capture per-UID mode checks and concurrent fsync/lock evidence.
- [ ] **5. Commit:**

```bash
git add packages/coding/src/xdog/coding/core/sessions/inbox* packages/coding/src/xdog/coding/config.py packages/coding/tests/test_inbox*
git commit -m "feat(coding): persist session inbox without creating agent runtimes"
```

## R3: Independent lightweight ACP ingress and owner routing

**Files:**
- Create `packages/coding/src/xdog/coding/acp/ingress_backend.py`, `ingress.py`, `ingress_main.py`.
- Modify `packages/coding/src/xdog/coding/acp/client.py` only for reusable attachment close/validation.
- Create `packages/coding/tests/test_acp_ingress.py`, `test_acp_offline_inbox.py`, `test_acp_ingress_lifecycle.py`.

**Consumes/produces:** R2 store and existing Backend/AcpAdapter/AcpAttachment/OwnerRegistry. Exposes IngressConfig/IngressServer above. Uses SDK ext_method with no leading underscore; SDK adds wire prefix.

- [ ] **1. Write RED no-owner ACP enqueue test.** Provision identity with SessionManager.create_session(model='', working_dir=tmp_path), then close provisioning process. Start ingress via Python API, not CLI; official SDK Unix client initialize and ext_method('xdog/enqueue', canonical payload). Monkeypatch `create_agent_session` and `AgentSession.__post_init__` to raise if invoked (the current AgentSession dataclass defines __post_init__); stronger subprocess instrumentation records constructor/model-call attempts and asserts zero. Query inbox_status without load; verify durable file, then load/prompt rejects inactive. No owner socket/lease is created. Add live-owner test reusing `test_acp_bridge.py` setup: ingress prompt reaches existing controller and peer cancellation cannot affect human input.

```python
async def test_ingress_cannot_create_a_hidden_agent(tmp_path):
    from xdog.agent.acp.contracts import Peer
    from xdog.coding.acp.ingress_backend import IngressBackend
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.sessions.ownership import OwnerRegistry, runtime_directory
    from acp import RequestError
    backend = IngressBackend(InboxStore(tmp_path/'sessions', tmp_path/'inbox'),
                             OwnerRegistry(runtime_directory(tmp_path/'sessions')))
    with pytest.raises(RequestError):
        await backend.new_session(tmp_path, Peer('peer', object()))
```

The test intentionally does not require Client methods because new_session must reject before using peer. Import pytest in that file. Backend constructor is `(store: InboxStore, registry: OwnerRegistry)`.

- [ ] **2. Observe RED:**

```bash
uv run python -m pytest packages/coding/tests/test_acp_ingress.py packages/coding/tests/test_acp_offline_inbox.py packages/coding/tests/test_acp_ingress_lifecycle.py -q -n 0
# Expected: ingress module missing / new_session wrongly creates runtime.
```

- [ ] **3. Implement backend routing.** Compose a remote-only backend, do not reuse CodingBackend.new_session with factory fallback. load verifies registry/current owner then attaches via ACP, remembers peer/ref generation; prompt/cancel/info/resume forward to that bound remote, with bounded update buffers and settlement. enqueue/status validate exact fields, finite JSON, cwd and persisted session through store using to_thread, before any load. No factory/config/model/auth imports. Request errors are explicit; no fallback prompt→enqueue or load→restore. Advertise only implemented extension versions; denied/unavailable owner resume returns refused/error, never an invented approval. Retain one-turn budget solely at target owner for prompt.
- [ ] **4. Implement service lifecycle and test GREEN.** `IngressServer.start` acquires flock before binding/publishing, rejects insecure directories/address files; returns live server; `aclose` settles its peer tasks and removes only matching inode/generation. PID/timeout never authorizes stealing a live lease. `python -m xdog.coding.acp.ingress_main --sessions-dir PATH --inbox-dir PATH --socket PATH` explicitly runs service; protocol endpoint is Unix, stdout/stderr are diagnostic only, SIGTERM settles owned connections without closing owner processes. Test simultaneous starts, startup cancellation/failure, live lease without metadata, stale socket with free lease, inode replacement, slow peer, service stop preserving durable files. Both module entry and Python API share this lifecycle.
- [ ] **5. Commit:**

```bash
git add packages/coding/src/xdog/coding/acp packages/coding/tests/test_acp_ingress* packages/coding/tests/test_acp_offline_inbox.py
git commit -m "feat(coding): serve ACP ingress independently of session and TUI owners"
```

## R4: Inbox approval/attempts through the sole controller

**Files:**
- Create `packages/coding/src/xdog/coding/core/sessions/inbox_runtime.py`.
- Modify `controller.py`, `runtime.py` in that directory and `packages/coding/src/xdog/coding/core/agent_session.py` for retire/navigation settlement.
- Create `packages/coding/tests/test_inbox_controller.py`.
- Modify `packages/coding/tests/test_session_controller.py`, `test_session_turns.py` for lifecycle regressions.

**Consumes/produces:** InboxCoordinator above; no replacement of before_turn/after_turn. Approval API stays owner-local until R5 exposes permission-gated surfaces.

- [ ] **1. Write RED model-free open and explicit approval tests.** Create inbox message, open runtime with execute counter and bound=True. snapshot/recover/start must yield pending_review and execute counter zero even after human_input. Explicit coordinator.approve selected IDs invokes original PermissionManager hook with synthetic `session_inbox_approve` tool call containing exact message IDs, branch and captured generation; denied approval changes nothing. Allowed approval submits only selected records and returns request IDs, not completed turns. Assert approval of two messages grants at most one automatic turn; second remains approved/paused until another explicit authorized resume.

```python
async def test_opening_owner_never_consumes_pending_inbox(coding_session, tmp_path):
    from xdog.coding.core.sessions.runtime import SessionRuntime
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore
    store = InboxStore(coding_session.session_manager._dir, tmp_path/'inbox')
    store.enqueue(InboxMessage(coding_session.session_id, tmp_path, 'm1','Review',(), 'Eyes', {}))
    runtime = SessionRuntime(coding_session, bound=True, inbox_store=store)
    runtime.start()
    coordinator = runtime.inbox
    try:
        assert [r.status for r in await coordinator.snapshot()] == ['pending_review']
        runtime.controller.human_input()
        await asyncio.sleep(0.05)
        assert coding_session.messages == []
        assert (await coordinator.snapshot())[0].status == 'pending_review'
    finally:
        await asyncio.wrap_future(runtime.close())
```

Add asyncio import. Add blocked producer/permission/branch-rotation tests, cancelled admission rollback, close of queued approved, crash of running, duplicate retry attempts and terminal-persist failure.
- [ ] **2. Observe RED:**

```bash
uv run python -m pytest packages/coding/tests/test_inbox_controller.py -q -n 0
# Expected: InboxCoordinator missing, not model/provider access.
```

- [ ] **3. Implement coordinator with fenced permission approval.** Capture session/branch/generation and selected record content; await permission outside any inbox disk lock; recheck under controller reservation before store.approve. Reject empty/duplicate IDs, branch mismatch, non-pending status, concurrent conflicting approvals and stale owner. Set controller one-turn credit only after permission and context checks; budget selection/decrement in controller treats `self.bound or self.inbox.is_submission(job.value.id)` as requiring automatic credit, so standalone bound=False owners cannot bypass inbox approval/budget (ordinary explicit stdio ACP prompt semantics remain unchanged). Submit canonical SessionInput(origin='acp') with provenance linking message_id/attempt_id without overwriting untrusted original provenance. Stable request_id generated once per approved attempt; keep owner-only mapping, never derive privilege from supplied provenance.
- [ ] **4. Integrate core lifecycle, GREEN.** `starting` claims approved record with exact generation/attempt/request ID before model work; storage failure prevents execution. `finished` is invoked once from controller `_finish` (including pre-run cancelled/discarded jobs), not solely `_run_job` finalization, and persists terminal result before submission result is released; disk failure marks controller broken. Unstarted cancelled/discarded/closed attempts return pending_review, running unsuccessful attempts record actual terminal/interrupted state. Do not wait on controller submission result from inside control. Retire under context/close settlement; join producers before releasing writer lease. Recover only with live lease and before endpoint/history publication. Native/RPC hooks remain independently installed and still run. Add an owner-loop refresh snapshot API; read/status never consume work.
- [ ] **5. Commit:**

```bash
git add packages/coding/src/xdog/coding/core packages/coding/tests/test_inbox_controller.py packages/coding/tests/test_session_controller.py packages/coding/tests/test_session_turns.py
git commit -m "feat(coding): execute reviewed inbox attempts through the session controller"
```

## R5: Inbox review/display, optional CLI and ACP adapter access

**Files:**
- Create `packages/coding/src/xdog/coding/core/inbox_commands.py`.
- Modify `core/slash_commands.py`, `cli/args.py`, `main.py`, `acp/cli.py`, `acp/backend.py` under coding source root.
- Modify `modes/interactive/session_presenter.py`, `session_events.py`, `interactive_mode.py`; `modes/rpc/session_events.py`, `rpc_mode.py`.
- Create `packages/coding/tests/test_inbox_display.py`, `test_inbox_commands.py`, `test_acp_enqueue_cli.py`.

**Consumes/produces:** R2/R4 records/coordinator; existing SessionHistory gains `inbox: tuple[InboxRecord,...]=()` (frozen) and generation-bound inbox_snapshot events. Generic ACP stays source-neutral.

- [ ] **1. Write RED read-only display/review tests.** Open session with pending/interrupted messages; assert source/evidence/instruction/status visible without model call or regular user-message masquerade. Snapshot captures current branch inbox before endpoint publication; subsequent inbox status refresh does not duplicate messages/attempts already in model history. Add `/inbox` list, `/inbox show ID`, `/inbox approve ID [ID...]`, `/inbox retry ID` parser tests. No implicit all; retry only interrupted and through same permission/context fence.

```python
@pytest.mark.parametrize('args', ['approve', 'approve --all', 'retry', 'approve m1 m1'])
async def test_inbox_commands_never_implicitly_select_a_batch(coding_session, args):
    from xdog.coding.core.inbox_commands import inbox_command
    result = await inbox_command(coding_session, args)
    assert result.output.startswith('Usage: /inbox')
    assert coding_session.messages == []
```

Return `CommandResult(output='Usage: /inbox ...')` for malformed syntax; the actual existing dataclass has output/exit_requested/prompt/rich_output and no is_error field. Do not add a new global error field merely for this feature. Core command consistency requires no TUI import. Add terminal-injection sanitization, /details full content, no stale inbox on navigation, refresh under late enqueue.
- [ ] **2. Observe RED:**

```bash
uv run python -m pytest packages/coding/tests/test_inbox_display.py packages/coding/tests/test_inbox_commands.py packages/coding/tests/test_acp_enqueue_cli.py -q -n 0
# Expected: command/coordinator/presenter integration missing.
```

- [ ] **3. Implement one review path and bounded refresh.** `inbox_command(session: AgentSession, args: str) -> CommandResult`; read-only list/show offload disk reads. approve/retry enter controller owner API and return accepted IDs; no nested control self-await or busy loop. TUI reads a frozen inbox snapshot at startup/navigation and via low-rate owner-loop refresh while open, rendering through the main-thread event queue. RPC emits keyed inbox snapshots and accepts the same permission-gated command actions through its normal request/control surface; no independent scheduler. Busy/producer/control fences unchanged. Treat input/attempt linking explicitly to avoid duplicate external entries and to distinguish pending from running/history.
- [ ] **4. Add optional CLI/stdio access without callback dependency, GREEN.** `--session ID --enqueue --prompt TEXT` requires explicit stable `--message-id ID`, cannot combine with resume/model/permission overrides; receipt output distinct from prompt result. `main.run_agent` dispatches enqueue before create_agent_session. Direct CLI calls InboxStore, or stdio ACP ingress backend for ACP clients; neither startup is required by Eyes Unix path. Existing `--session ID --prompt` remains live execution only. `xdog-coding --acp` adapter exposes enqueue/status via shared ingress component without creating AgentSession for these extensions, while existing explicit new/load behavior stays tested. No client extension for direct approve mutation; resume continues existing permission gate. Cover no-owner enqueue subprocess, SDK absent non-ACP paths, and no model/auth calls.
- [ ] **5. Commit:**

```bash
git add packages/coding
git commit -m "feat(coding): display and explicitly review durable session inbox messages"
```

## R6: Eyes enqueue mode and honest receipt telemetry

**Files:**
- Modify `packages/eyes/src/xdog/eyes/acp_client.py`, `handlers/acp.py`, `events.py`, `executor.py`.
- Modify `packages/eyes/tests/fixtures/acp_agent.py` for optional inbox extension modes.
- Create `packages/eyes/tests/test_acp_enqueue.py`; modify `test_acp_delivery.py`, `test_executor.py`, `test_loader.py`.

**Consumes/produces:** R1 ConnectionDef/delivery, R3 enqueue/status extension, R2 stable message receipt. DeliveryResult gains explicit `queued` status; RuntimeEvent emits `delivery_queued`, not delivery_succeeded. Existing prompt statuses unchanged.

- [ ] **1. Write RED enqueue-without-load and ACK semantics tests.** Real SDK peer advertises inbox extension, rejects load/prompt, accepts inbox_status then enqueue; assert handler.connect succeeds, delivery returns queued and model completion is never claimed. Use delivery.id as message_id, never regenerate on retry. Existing Unix test peer is expanded to inbox/no-inbox/ack-lost/conflict/owner-exit modes; no coding imports. Add executor regression:

```python
async def test_enqueue_does_not_load_or_prompt_an_inactive_owner(tmp_path):
    from dataclasses import replace
    from packages.eyes.tests.test_acp_handler import definition, delivery, rows
    from xdog.eyes.handlers.acp import AcpHandler
    record = tmp_path/'peer.jsonl'
    handler = AcpHandler(replace(definition(record, mode='inbox'), delivery='enqueue'), cwd=tmp_path)
    try:
        await handler.connect()
        result = await handler.deliver(delivery('stable-message'))
        assert result.status == 'queued'
        assert not [r for r in rows(record) if r['kind'] in {'load','prompt'}]
        queued = [r for r in rows(record)
                  if r['kind'] == 'extension' and r['method'] == 'xdog/enqueue']
        assert queued[0]['params']['message_id'] == 'stable-message'
    finally:
        await handler.close()
```

Also exercise observe with real scanner/clock fixtures, wait for delivery_queued then change a second file; registration must continue and must not consume automatic execution budget for simple collection. Assert emitted events contain no delivery_succeeded for either queued receipt.
- [ ] **2. Observe RED:**

```bash
uv run python -m pytest packages/eyes/tests/test_acp_enqueue.py packages/eyes/tests/test_executor.py -q -n 0
# Expected: enqueue negotiation/status/result telemetry unsupported.
```

- [ ] **3. Implement explicit delivery branch.** Enqueue connect negotiates inbox versions and validates session/cwd/branch via inbox_status, not load; no owner_generation pin for durable enqueue. Bind stable session/project/branch identity; branch mismatch pauses for review, does not silently rewrite target. Build canonical parameters from instruction/evidence/source/provenance; do not duplicate untrusted evidence into executable instructions. Validate message byte limit before send. Require response exact session/message identity and queued/duplicate state; malformed response becomes unknown, not success.
- [ ] **4. Retry/permission/telemetry GREEN.** No prompt budget or resume call for enqueue; enqueue never executes. On lost ACK, pause; explicit reconnect to the same verified endpoint/session/project can query/retry same stable ID/content, not create a fresh message. No automatic process reconnect or endpoint discovery. Duplicate receipt retires pending delivery just like queued but telemetry preserves duplicate message_state. Quota/conflict/unsupported extension pauses; no fallback prompt. Prompt tests retain not-sent-only retry, no generation migration and unknown-result freezes. SDK permission callback remains cancelled.
- [ ] **5. Commit:**

```bash
git add packages/eyes
git commit -m "feat(eyes): enqueue evidence through negotiated ACP inbox extensions"
```

## R7: Eyes-owned lifecycle/control, no coding context dependency

**Files:**
- Create `packages/eyes/src/xdog/eyes/local_control.py`.
- Rewrite `packages/eyes/src/xdog/eyes/runtime.py`, `process.py`, `control.py`, `cli.py`.
- Modify `packages/eyes/src/xdog/eyes/skills/eyes-observers/SKILL.md`, `examples/source-test-review.json`.
- Rewrite `packages/eyes/tests/test_runtime.py`, `test_cli.py`, `test_end_to_end.py`; create `test_local_control.py`.

**Consumes/produces:** Uses R1/R6 handlers only. Frozen `RuntimeLocation(directory: Path,socket: Path,generation: str)` (empty generation only for an unresolved location before handshake); `runtime_location(cwd: Path|None=None) -> RuntimeLocation`; synchronous `ensure_runtime(location: RuntimeLocation) -> RuntimeLocation` returns actual live generation, never mutates frozen input. Async `request(location, action: str, body: dict[str,JsonValue], *, timeout: float=5.0) -> dict[str,JsonValue]`. RuntimeLocation contains no harness capability/token.

- [ ] **1. Write RED independent CLI and pre-scan admission tests.** Delete temporary `test_v2_does_not_fall_back_to_the_retired_event_host_during_runtime_migration` only when replacing with tests proving no fallback and zero scans until all connections validated. Use real Unix peer, no old environment. Set coding executable lookup to fail; start/status/stop must still work. Include idempotent normalized definition and same-name/different-definition conflict.

```python
def test_validate_requires_neither_sdk_nor_coding_context(tmp_path, observer_data, monkeypatch, capsys):
    import json
    from xdog.eyes.cli import main
    monkeypatch.delenv('XDOG_EVENT_CONTEXT', raising=False)
    observer_data['handlers'][0].pop('command', None)
    observer_data['handlers'][0]['connection'] = {'transport':'unix','path':'/tmp/private/acp.sock'}
    path = tmp_path/'eyes.json'
    path.write_text(json.dumps(observer_data))
    assert main(['validate',str(path)]) == 0
    assert json.loads(capsys.readouterr().out)['valid'] is True
```

Add a subprocess import blocker for `acp` to make no-SDK validate assertion real. Definition serialization must roundtrip nested connection defaults and absolute roots, not retain old top-level command.
- [ ] **2. Observe RED:**

```bash
uv run python -m pytest packages/eyes/tests/test_runtime.py packages/eyes/tests/test_cli.py packages/eyes/tests/test_local_control.py -q -n 0
# Expected: legacy context/HostClient requirement or v2 start gate.
```

- [ ] **3. Implement private Eyes control lifecycle.** Per-UID short directory keyed by cwd, separate start lock/live owner lease, strict type/mode/UID/no-symlink checks, finite bounded JSON and action allowlist. Control only start/status/stop/resume/hello, never deliver events to coding. Lock inode retained. Runtime owns/publishes generation; starter returns verified live generation. Live lease/socket timeout means wait/error, never steal/delete/restart. Only free lease and verified refused/stale endpoint allow replacement. Reap/terminate only actual child handle this launcher created on its own failed startup. Clean socket by inode, records by generation; last registration stop cleans runtime and optional stdio peer, not ACP ingress/TUI/owner. CLI imports ACP only inside start/runtime activation, not validate/help.
- [ ] **4. Wire runtime and honest status, GREEN.** Connect/check every handler before creating Registration; partial failure closes all connected handlers and zero scan. Map routes to serial handlers; source health and each route's processing/queued/succeeded/paused/unknown states tracked separately, one success cannot clear another route's pause. Prompt owner changes pause/retire without automatic attach; enqueue owner exit does not stop collection; ingress disconnect pauses and preserves uncertain result diagnosis. Resume requests only negotiated owner authorization for prompt; enqueue has no execution resume grant. Explicit enqueue reconnect/retry uses same ID/content and context; stop warns about unsent evidence, never deletes durable inbox. No automatic crash replay/rebaseline or harness startup.
- [ ] **5. Real E2E, docs and commit.** Test actual file→Unix ACP ingress→live deterministic owner→TUI replay; second test files→enqueue while no owner→restart owner→display→explicit approve→one turn. Kill/stop TUI/owner and prove enqueue observer/ingress stay available; stop ingress and prove unavailable with no CLI spawn; stop Eyes leaves inbox/ingress intact. Skill/example select endpoint+session+delivery and distinguish durable queued from finished; approval to start observation is not permission to mutate. Run all Eyes and affected coding tests.

```bash
uv run python -m pytest packages/eyes/tests -q
uv run python -m pytest packages/coding/tests/test_acp_ingress.py packages/coding/tests/test_acp_offline_inbox.py packages/coding/tests/test_inbox_controller.py packages/coding/tests/test_inbox_display.py -q
git add packages/eyes packages/coding/tests
git commit -m "refactor(eyes): own observer lifecycle with direct ACP session delivery"
```

## R8: Remove old host, finish docs/packaging and independently audit goal

**Files:** Exact original Task 10 deletion set, verified with git ls-files before deletion; all remaining tests/docs/env wiring referencing old host. Add `packages/coding/tests/test_no_legacy_event_host.py`, `test_acp_optional_imports.py`; `packages/agent/tests/test_acp_packaging.py`, `packages/eyes/tests/test_acp_packaging.py`; create/update `docs/acp-session-routing.md`, `docs/eyes-verification.md`, package READMEs and root AGENTS.md.

**Consumes/produces:** All prior tasks. Final public paths only generic ACP + explicit endpoint + session inbox/controller; history compatibility not a host alias. Produces actual verification logs, requirement evidence table and independent review resolution.

- [ ] **1. Write RED deletion/optional-import audits.** Assert directories `coding/core/external_events`, `eyes/transport`, `eyes/handlers/session_notify.py` and old docs/fixture absent. AST/text audit product source rejects `enable_external_events`, `XDOG_EVENT_CONTEXT`, old HostContext/HostClient/register/deliver wiring; do not flag standard ACP ToolCall/session_update or historical decoder. Child interpreter blocks `acp` imports: Eyes validate/help, coding ordinary native/RPC/navigation work; explicit ACP invocation reports supported Python/SDK requirement. Simulated >=3.15 covers context rebind and RPC, not merely initial native guard.

```python
def test_old_capability_host_is_not_shipped():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[3]
    for relative in ('packages/coding/src/xdog/coding/core/external_events',
                     'packages/eyes/src/xdog/eyes/transport',
                     'packages/eyes/src/xdog/eyes/handlers/session_notify.py'):
        assert not (repo/relative).exists()
```

- [ ] **2. Observe RED then delete real implementations and migrate assertions.** Do not delete old tests merely to turn tests green: preserve permission/provenance/file observer/injection assertions under ACP/controller tests. Remove descriptor/capability/env startup and environment masking; retain safe subprocess env handling and nonsecret `XDOG_SESSION_ID` refresh. Old `xdog.external_event.v1` decode/render/LLM projection must still pass. Leave no private host alias behind.
- [ ] **3. Update all docs/artifact boundaries.** AGENTS/README/examples/skill show explicit ingress lifecycle, endpoint/custom Unix transport, exact session, prompt/enqueue distinction, stable ID, permission/approve/retry/unknown behavior and no automatic startup. Historical designs marked superseded without rewriting history. Agent/Eyes isolated wheels include py.typed/skill/examples/licenses; SDK marker and Python upper support accurate, no transitive harness dependencies in Eyes. Check Flow vendored import/dependency audit remains correct.
- [ ] **4. Run complete verification and artifact smoke tests.** Record exact current commit, interpreter and commands/output; never use old green logs as current completion evidence.

```bash
git diff --check
uv run python scripts/test.py
uv run ruff check packages
uv run mypy --strict -p xdog.ai -p xdog.agent -p xdog.tui -p xdog.flow -p xdog.site -p xdog.claw -p xdog.coding -p xdog.eyes
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
DIST=$(mktemp -d /tmp/xdog-acp-dist.XXXXXX)
uv build --package xdog-ai --out-dir "$DIST"
uv build --package xdog-agent --out-dir "$DIST"
uv build --package xdog-tui --out-dir "$DIST"
uv build --package xdog-coding --out-dir "$DIST"
uv build --package xdog-eyes --out-dir "$DIST"
uvx twine check "$DIST"/*
EYES_ENV=$(mktemp -d /tmp/xdog-eyes-venv.XXXXXX)
uv venv "$EYES_ENV"
uv pip install --python "$EYES_ENV/bin/python" "$DIST"/xdog_eyes-*.whl
"$EYES_ENV/bin/xdog-eyes" --help
```

Build an additional ai+agent-only venv and run official SDK deterministic stdio Agent test without coding. Build a coding venv from all local dependency wheels and run direct ACP ingress enqueue/status with model construction forbidden. Inspect installed distributions to prove Eyes has no ai/agent/coding/claw/flow; use no workspace PYTHONPATH in isolated commands. Test source/wheel packaged skill and definition loading, not help alone. Use fresh temporary artifact/env paths; do not overwrite existing user artifacts.

- [ ] **5. Whole-branch independent review and completion audit.** Request one fresh reviewer for the full original base→HEAD diff including approved specs, inbox/no-CLI requirements and actual test evidence. Native choice authorizes final reviewer, not implementation delegation. Use inherited allowed model; no model override unless user explicitly requests it. Fix findings with RED→GREEN then repeat affected verification; unresolved findings prevent completion. Build an evidence table for every original §12 acceptance item, endpoint §7, inbox §9, all deletion paths, commands, packaging and review. Inspect runtime/subprocess/disk evidence for no-Agent/no-CLI and no automatic replay, not only code search. Report untested actual provider/IDE integrations. Commit verified work, keep no-push boundary; only mark active goal complete when entire evidence table proves scope.

```bash
git add packages docs AGENTS.md uv.lock
git commit -m "refactor: remove legacy event host and verify ACP session inbox routing"
```

## Coverage / handoff self-review

| Requirement | Owning tasks |
|---|---|
| endpoint spec §1–§4, Eyes never starts coding/reads registry | R1, R3, R7 |
| endpoint spec §5.1 service lifecycle/lease/no-Agent | R3, R8 |
| endpoint spec §5.2 prompt/session/generation/cancel | R1, R3, R4, R7 |
| endpoint spec §5.3 + inbox §3–§5 durable receipt/dedup/quotas | R2, R3, R6 |
| inbox §6–§7 approval/attempt/recovery/display | R4, R5 |
| endpoint spec §6 + inbox §8 unknown/retry/budget/permissions | R3, R4, R6, R7 |
| endpoint spec §7.1–§7.10 tests | R1–R8 (each acceptance mapped above) |
| inbox §9.1–§9.10 tests | R2–R8 |
| original ACP design §12.1 generic agent interoperability | prior Task 1 + R8 isolated regression |
| original ACP design §12.2–§12.9 controller/UI/permissions | prior Tasks 2–7 + R3–R7 |
| original ACP design §12.10 real subprocess/file/TUI closed loop | R7 |
| original ACP design §12.11–§12.12 deletion/full verification | R8 |
| independent whole-branch review + no push/worktree | R8 + global constraints |

Execution dependency order: R1 → R2 → R3 → R4 → R5 → R6 → R7 → R8. Although some files are independent, implement natively in this order to keep shared interfaces stable. Each task may need multiple TDD cycles; do not pretend a large matrix is covered by one sample assertion. Before execution compare actual types/field names with this document and record any necessary ruling rather than silently diverging.

Self-review must confirm parser roundtrip/ConnectionDef compatibility, no ACP SDK import through InboxStore, IngressBackend conforms to Backend without runtime factory, CommandResult.output-based usage errors, no overwriting UI lifecycle hooks, no nested controller self-await, exact receipt statuses and generation/branch distinctions, and evidence completeness. User review of this written plan is the remaining implementation gate.
