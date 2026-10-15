# ACP Session Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Eyes 通过 ACP 将任务送到指定 coding session，复用单一执行 owner，并在 TUI 实时和历史中展示；彻底移除上一版 Eyes 专用 event host。

**Architecture:** `xdog.agent.acp` 提供注入 backend 的通用协议适配和独立 Agent backend。coding 统一回合执行与会话 controller，提供私有本地 owner 路由、标准 stdio ACP adapter 和 `--session` 桥接；TUI 只提交输入并消费会话事件。Eyes 只依赖 ACP SDK，保留声明式观察引擎，替换 handler 和控制面，不保留旧协议服务器。

**Tech Stack:** Python 3.12+、uv workspace、官方 `agent-client-protocol>=0.12.1,<0.13`、asyncio、frozen dataclasses、标准 stdio ACP、本地 Unix socket、fcntl locks、pytest/pytest-asyncio、Ruff、strict mypy。

**Spec:** `docs/superpowers/specs/2026-10-15-acp-session-routing-design.md`（用户已于 2026-10-15 批准）。

> **2026-10-15 修订待审：** Tasks 1–8 已提交，但 Task 8 的 command-only 接口不是最终交付。用户确认 callback 直接使用 ACP endpoint，不依赖 coding CLI；见 `../specs/2026-10-15-acp-endpoint-callback-design.md`。Task 9 尚未编辑产品代码，先完成补充设计/计划审核，再更新受影响的连接接口、独立接入服务和离线收件任务。本文原 Task 8/9 的 command-only 示例不能继续作为实现依据。

## Global Constraints

- 仍在原仓库目录和当前功能分支工作，不创建 worktree。
- `xdog.agent` 协议组件不导入 coding、Eyes、TUI、claw 或 flow。
- Eyes 可以依赖 ACP SDK，但不能依赖 `xdog.agent`、ai 或 harness，仍不执行工具循环。
- SDK 依赖范围 `>=0.12.1,<0.13`，lockfile 固定实际解析版本；SDK 的 Python 上限不得误报为项目已支持。
- 首版不实现 TCP/HTTP 远程入口、独立永久 session daemon、跨进程崩溃重放、Windows 本地传输、claw/flow ACP adapter。
- 队列最多 128 项；单任务输入最多 1 MiB，超限在入队前拒绝，不截断证据。
- private runtime directory 为当前 UID 所有、权限 0700；owner registry 文件为 0600。
- 同一 session 的进程锁必须在加载可写历史之前获取，并保持到所有执行/清理完成。
- 人类输入优先于尚未启动的自动任务。第一版外部输入忙时排队，不实现自动插话/steer。
- 新观察定义 version 2；旧 version 1 的 `session.notify` 明确报迁移错误。
- 同一会话只能有一个执行 owner。连接、模型流、文件 I/O 不阻塞 TUI 主循环。
- 外部输入不授予权限、不冒充人类，不补充人工唤醒预算。新 transport 不得绕过 session 权限、压缩、扩展或持久化。
- 标准 ACP 不提供跨连接幂等；已发送但结果丢失的 prompt 标为 outcome-unknown 并暂停，不能盲目重发。
- 删除旧 host/capability/投递协议；只保留旧 `xdog.external_event.v1` 历史读取及投影兼容。
- 不自动 push、创建 PR 或发布。实现完成后提供实际验证结果及未验证项。

## Review Focus

1. 启动在拿到锁后失败、或 owner 进程被杀：不泄漏 lease、不偷另一个活进程的 socket；Task 4 的进程测试覆盖。
2. 模型接纳前被拒绝/取消、或持久化替换失败：不能在 UI 显示虚假接纳，不能留下半条可执行输入；Tasks 2/3 的注入失败测试覆盖。
3. ACP client 很慢或断连：不能阻塞 TUI/其他客户端，不能取消人类任务；Tasks 5/7 的背压与断连测试覆盖。
4. TUI 导航和 after-run 工作与外部排队竞争：旧 generation 不在新 branch 执行，producer 结束而后处理未结束时不能释放所有权；Tasks 3/6 覆盖。
5. argv、文件名、来源 metadata 中的恶意字符串：不执行 shell、不升级权限、不伪造来源、不把证据作为指令；Tasks 2/8/9 覆盖。

---

## Execution boundaries and file map

这是一个有顺序依赖的架构迁移，不是十个独立功能：Task 1 可独立交付标准 ACP；Tasks 2–7 形成 session routing；Tasks 8–10 完成 Eyes 切换和旧 host 删除。中间提交允许存在尚未接线的新模块，但不能同时启动两套宿主服务。

| Slice | Files / responsibility |
| --- | --- |
| Generic ACP | `packages/agent/src/xdog/agent/acp/{contracts,adapter,backend,events,transport,permissions,cli}.py` |
| Canonical input | `packages/agent/src/xdog/agent/session_input.py`，消息验证与模型投影 |
| Coding controller | `packages/coding/src/xdog/coding/core/sessions/{models,store,turns,controller,router,ownership,runtime}.py` |
| Coding ACP | `packages/coding/src/xdog/coding/acp/{backend,server,bridge,client,extensions,cli}.py` |
| TUI adapter | `packages/coding/src/xdog/coding/modes/interactive/session_events.py`，展示与事件投影 |
| Eyes ACP | `packages/eyes/src/xdog/eyes/handlers/acp.py`、`acp_client.py`，不导入 xdog.agent |
| Eyes control | `packages/eyes/src/xdog/eyes/local_control.py`，仅观察进程控制，不承担 agent 事件接收 |
| Test support | 各包 tests 的 `conftest.py` 提供包内 fixture；不跨包导入测试模块 |
| Public docs | `docs/acp-session-routing.md`，更新包 README、skill、AGENTS.md 和验证记录 |

所有新包目录加 `__init__.py`。单元测试只使用确定性模型/工具桩；进程测试使用测试目录脚本，不能增加生产环境的假模型开关。

### Shared interface vocabulary

Task 1 定义 ACP `SessionRef`、`Peer` 和 backend contract；Task 2 定义 `SessionInput` 和持久 `InputRecord`；Task 3 定义 `Submission`、`TurnResult`、`SessionEvent` 和 `SessionController`；Task 4 定义 `SessionLease`/`OwnerRecord`；Task 5 组合这些接口。每个后续任务只能依赖已声明接口，不再新造私有投递协议。

Controller 的可公开跨线程对象使用 `concurrent.futures.Future`；不把 owner loop 的 `asyncio.Future` 暴露给 TUI 或另一个 loop。跨 loop 等待用 `asyncio.wrap_future()`。全部可写 session 操作在固定 owner loop 执行；TUI 回调只向线程安全事件队列投递展示事件。

## Task 1: Generic ACP adapter, independent Agent backend, and stdio CLI

**Files:**
- Create: `packages/agent/src/xdog/agent/acp/__init__.py`
- Create: `packages/agent/src/xdog/agent/acp/contracts.py`
- Create: `packages/agent/src/xdog/agent/acp/adapter.py`
- Create: `packages/agent/src/xdog/agent/acp/backend.py`
- Create: `packages/agent/src/xdog/agent/acp/events.py`
- Create: `packages/agent/src/xdog/agent/acp/transport.py`
- Create: `packages/agent/src/xdog/agent/acp/permissions.py`
- Create: `packages/agent/src/xdog/agent/acp/cli.py`
- Modify: `packages/agent/src/xdog/agent/cli.py`（argparse subcommand 和分派）
- Modify: `packages/agent/pyproject.toml`, `uv.lock`
- Test: `packages/agent/tests/test_acp_adapter.py`, `test_acp_backend.py`, `test_acp_stdio.py`
- Test support: `packages/agent/tests/acp_stdio_agent.py`（仅测试脚本）

**Interfaces:**
- `SessionRef(id: str, cwd: Path, generation: str)`：frozen dataclass。
- `Peer(id: str, client: acp.interfaces.Client)`：每个连接由 adapter 生成 ID，不能从 metadata 接受。
- `AcpUpdate = UserMessageChunk | AgentMessageChunk | AgentThoughtChunk | ToolCallStart | ToolCallProgress | SessionInfoUpdate`（从 SDK schema 导入各成员，在 contracts 定义此 union），`UpdateSink = Callable[[AcpUpdate], Awaitable[None]]`。SDK 0.12.1 没有名为 SessionUpdate 的通用可导入 alias，不引用虚构类型。
- `Backend.new_session(cwd: Path, peer: Peer) -> SessionRef`（async）。
- `Backend.load_session(session_id: str, cwd: Path, peer: Peer, emit: UpdateSink) -> SessionRef`（async）。
- `Backend.prompt(ref: SessionRef, peer: Peer, text: str, metadata: dict[str, JsonValue], emit: UpdateSink) -> acp.PromptResponse`（async）。
- `Backend.cancel(ref: SessionRef, peer: Peer) -> None`、`disconnect(peer: Peer) -> None`（async）。
- `Backend.extension(peer: Peer, method: str, params: dict[str, JsonValue]) -> dict[str, JsonValue]`（async）；默认 method-not-found。
- `AcpAdapter(backend: Backend)` 实现官方 `acp.Agent`；`AgentBackend(factory: Callable[[Path], Agent])` 为独立内存会话实现。session load 只恢复该 backend 已拥有的 session，不伪称跨进程磁盘恢复。
- `serve(adapter: AcpAdapter, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None`（async），调用官方 SDK；STDIO CLI 使用同一个 adapter。
- `JsonValue` 在 contracts 定义为有限 JSON 递归类型；非有限值、超过 32 层或未知字段的项目 metadata 拒绝。

- [ ] **Step 1: 写 adapter 的首个 RED 测试。**

```python
from pathlib import Path

import pytest
from acp import PROTOCOL_VERSION
from acp.schema import TextContentBlock
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream


async def test_generic_acp_roundtrip_without_coding(tmp_path: Path):
    from xdog.agent.acp.adapter import AcpAdapter
    from xdog.agent.acp.backend import AgentBackend

    def factory(cwd: Path) -> Agent:
        assert cwd == tmp_path
        return Agent(lambda *_: EventStream.empty(
            AssistantMessage(content=(TextContent(text="reviewed"),))),
            config=AgentConfig(model="test"))

    class RecordingClient:
        def __init__(self):
            self.updates = []

        async def session_update(self, session_id, update, **kwargs):
            self.updates.append((session_id, update))

    client = RecordingClient()
    adapter = AcpAdapter(AgentBackend(factory))
    adapter.on_connect(client)
    initialized = await adapter.initialize(PROTOCOL_VERSION)
    assert initialized.protocol_version == PROTOCOL_VERSION
    created = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    result = await adapter.prompt(created.session_id, [TextContentBlock(text="review")])
    assert result.stop_reason == "end_turn"
    with pytest.raises(Exception, match="session"):
        await adapter.prompt("missing-session", [TextContentBlock(text="review")])
```

上面无工具的单元测试只需要 RecordingClient.session_update；权限测试的 recording client 必须另外实现 SDK request_permission。正式连接和测试都通过 on_connect 注入 peer，不在生产 adapter 增加“无连接假 peer”分支。另加官方 SDK client + socketpair/stdio 线级测试，不能只测 Python 方法。

- [ ] **Step 2: 运行 RED。** `uv run pytest packages/agent/tests/test_acp_adapter.py -q`。Expected: 新模块不存在导致 collection/import failure；确认是目标模块缺失，不是 SDK 测试环境错误。
- [ ] **Step 3: 添加 SDK 依赖，执行 `uv sync`，实现 contracts、adapter、backend 和 transport。依赖使用 `agent-client-protocol>=0.12.1,<0.13; python_version < '3.15'`，避免 uv 对项目未设上限的 Python 范围解析失败；ACP 入口在 Python 3.15+ 给出清晰不支持提示，其他非 ACP 功能不因此在导入时崩溃。** adapter 将 SDK content 转成 text-only 输入，image/audio/resource 类型明确拒绝；非空 MCP/额外目录请求拒绝；load/new 的 cwd 必须为绝对存在目录；每个连接的 session 绑定、取消和输出隔离。

```python
async def serve(adapter, reader, writer):
    import acp
    await acp.run_agent(adapter, writer, reader, stdio_buffer_limit_bytes=1024 * 1024)
```

这里 writer/reader 的顺序依 SDK 0.12.1 `run_agent()` 实际签名，必须由真实 SDK client 的 roundtrip 测试锁定，不凭名称猜测。配置 SDK JSON-RPC 读写背压与 bounded output queue；连接关闭释放它自己的请求。

- [ ] **Step 4: 实现事件转换和权限桥。** MessageUpdate 文本 chunk 逐段转换，最终 MessageEnd 只补尚未发送的文本，避免双发；tool start/end 对应 ACP tool 状态；cancel、error、end_turn 区分。权限桥默认拒绝，无配置不能自动放开 shell/filesystem 工具；显式 deny 的结果不能被 client approval 覆盖。
- [ ] **Step 5: 添加 `xdog-agent acp` 分派。** 复用已有 model/provider 解析；仅选中 ACP 时才导入 SDK/开始连接；stdout 只能有 ACP，日志 stderr。测试脚本注入 factory，不使用真实 provider。
- [ ] **Step 6: 加入并运行测试矩阵。** 用参数化测试覆盖 missing session、bool version、未知 metadata、unsupported content、MCP、cwd、工具失败、权限拒绝、cancel-before-first-token、disconnect、同一 session 两个并发 prompt。stdio 子进程必须用 SDK spawn helper 测 initialize/new/load/prompt/cancel 和干净 stdout。
- [ ] **Step 7: GREEN 和提交。**

```bash
uv run pytest packages/agent/tests/test_acp_adapter.py packages/agent/tests/test_acp_backend.py packages/agent/tests/test_acp_stdio.py -q
uv run ruff check packages/agent
uv run mypy --strict -p xdog.agent
# Expected: 全通过；未接入 coding 也能运行协议测试。
git add packages/agent uv.lock
git commit -m "feat(agent): add reusable ACP adapter and stdio runtime"
```

## Task 2: Canonical session input and atomic request history

**Files:**
- Create: `packages/agent/src/xdog/agent/session_input.py`
- Modify: `packages/agent/src/xdog/agent/agent.py`, `agent_loop.py`（新 custom input 投影，与旧历史兼容并存）
- Create: `packages/coding/src/xdog/coding/core/sessions/__init__.py`, `models.py`, `store.py`
- Create: `packages/coding/tests/conftest.py`（coding_session 包内 fixture，保留未来已有 fixture）
- Modify: `packages/coding/src/xdog/coding/core/session_manager.py`
- Test: `packages/agent/tests/test_session_input.py`, `packages/coding/tests/test_session_input_store.py`

**Interfaces:**
- `SessionInput(id: str, origin: Literal['human','acp','automatic'], source: str, instruction: str, evidence: tuple[JsonValue, ...], provenance: dict[str, JsonValue])`：frozen；普通 ACP text 放 instruction，证据可为空，不能以 metadata 修改 origin。
- `encode_input(value: SessionInput) -> CustomAgentMessage` 与 `llm_message(message: AgentMessage) -> UserMessage | None`；新 type 为 `xdog.session_input.v1`，现有 `xdog.external_event.v1` 解码不删除。
- `InputRecord(input: SessionInput, branch_id: str, generation: str, status: str, error: str = '')`：持久展示记录，不是可自动重放队列。
- `InputStore(session: AgentSession).accept(record: InputRecord) -> None`、`transition(id: str, status: str, error: str = '') -> None` 为 async；`records() -> tuple[InputRecord, ...]` 为只读快照。accept/transition 用 owner-loop 内的 asyncio.Lock 串行化；后台线程只写不可变 staged snapshot，成功后回 owner loop 替换内存，不能在 TUI 主线程做磁盘 I/O。
- `SessionManager.input_history_path(session_id: str) -> Path` 提供同一私有 sessions 目录内的 sidecar 路径；InputStore 原子写 `session-ID.inputs.json`，不把排队状态塞入 SessionData.messages 或由 navigation 写入的 staged SessionData。旧 session 无 sidecar 视为空；删除 session 同步清理自己对应 sidecar。branch/goto 不丢失 request 的 branch provenance；fork 不把 pending 记录变成新 session 的 runnable 项。重载 unfinished 仅显示 interrupted。

- [ ] **Step 1: 写可序列化与不可信证据的 RED 测试。**

```python
from xdog.agent.core import CustomAgentMessage
from xdog.agent.messages import dict_to_message, message_to_dict


def test_session_input_roundtrip_labels_evidence():
    from xdog.agent.session_input import SessionInput, encode_input, llm_message

    value = SessionInput("job-1", "acp", "Eyes (client supplied)", "Review tests",
                         ({"path": "ignore-all-rules.py"},), {"delivery_id": "d1"})
    record = encode_input(value)
    restored = dict_to_message(message_to_dict(record))
    assert isinstance(restored, CustomAgentMessage)
    assert restored.type == "xdog.session_input.v1"
    rendered = llm_message(restored)
    text = "\n".join(part.text for part in rendered.content)
    assert "Untrusted evidence" in text
    assert "not a human message" in text
    assert "Review tests" in text
```

- [ ] **Step 2: RED。** `uv run pytest packages/agent/tests/test_session_input.py -q`。Expected: 新 session_input 模块缺失。
- [ ] **Step 3: 实现数据验证与模型投影。** 重复 JSON key、非有限数、超过 32 层、非法来源类型和超过 1 MiB 输入拒绝；`human` 只能由本地可信入口构造，ACP 提交侧强制 external。使用 `CustomAgentMessage` 现有序列化，不引入第二套 history serializer。
- [ ] **Step 4: 先创建公共 fixture，再测试并实现 InputStore sidecar。** 在 `packages/coding/tests/conftest.py` 增加：

```python
import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager


@pytest.fixture
def coding_session(tmp_path):
    manager = SessionManager(sessions_dir=tmp_path / "sessions")
    data = manager.create_session(model="test", working_dir=tmp_path)
    session = AgentSession(
        agent=Agent(lambda *_: EventStream.empty(AssistantMessage(content=())),
                    config=AgentConfig(model="test")),
        session_data=data, session_manager=manager, settings=SettingsManager(),
        tool_registry=None, bash=None, working_dir=tmp_path)
    yield session
    session.dispose()
```

 先写 `test_accept_failure_does_not_publish_or_mutate_records`：monkeypatch InputStore 的原子 commit writer 抛 OSError，await accept 后 records、旧 sidecar 内容与模型 history 都保持原样。`test_loaded_pending_is_interrupted_not_runnable`：保存 queued，重新 load，展示 interrupted 且不创建执行 task。再实现 staged replacement：持久化成功才替换内存、发事件，失败回滚；不能先 append 再碰磁盘。
- [ ] **Step 5: 加入历史兼容与证据攻击测试。** 旧 external_event fixture 可读；新记录不会在 queued 时加入 Agent history；路径/ANSI/JSON 里的伪指令被标记为证据；来源 label 的终端转义不能逃逸渲染；历史重开不重复 input。sidecar 在 new/resume/branch/goto/fork/save/load/delete 中均有确定行为。control reservation 期间接受 queued 项只写 sidecar，不会与 navigation 的 SessionData 原子替换竞争。
- [ ] **Step 6: GREEN 和提交。**

```bash
uv run pytest packages/agent/tests/test_session_input.py packages/agent/tests/test_external_event_messages.py packages/coding/tests/test_session_input_store.py -q
# Expected: 新格式和旧历史兼容全部通过。
git add packages/agent/src/xdog/agent packages/agent/tests/test_session_input.py packages/coding/src/xdog/coding/core packages/coding/tests/test_session_input_store.py
git commit -m "feat(coding): persist source-neutral session inputs and task states"
```

## Task 3: Single-owner controller and shared turn execution

**Files:**
- Create: `packages/coding/src/xdog/coding/core/sessions/controller.py`, `turns.py`, `runtime.py`
- Modify: `packages/coding/src/xdog/coding/core/sessions/models.py`
- Modify: `packages/coding/src/xdog/coding/core/agent_session.py`（提取 SDK turn body，共用 preparation/finalization）
- Test: `packages/coding/tests/test_session_controller.py`, `test_session_turns.py`

**Interfaces:**
- `TurnResult(status: str, response: AssistantMessage | None, error: str = '')`。
- `SessionEvent(session_id: str, generation: str, request_id: str, kind: str, data: object)`：frozen；只有当前请求的 output 才携带该 request ID。
- `Submission(id: str, accepted: concurrent.futures.Future[None], result: concurrent.futures.Future[TurnResult])`。
- `SessionController(session: AgentSession, *, bound: bool, execute: Callable[[SessionInput], Awaitable[TurnResult]] | None = None)`；execute 默认使用下面的 `run_turn`，测试可注入阻塞/失败函数。
- `start() -> None`, `submit(value: SessionInput, *, peer_id: str) -> Submission`：thread-safe；入队前把任务安排到固定 owner loop，accepted future 只在持久接纳成功后完成。
- `cancel(request_id: str, *, peer_id: str) -> concurrent.futures.Future[bool]`, `disconnect(peer_id: str) -> concurrent.futures.Future[None]`。
- `subscribe(listener: Callable[[SessionEvent], None]) -> Callable[[], None]`，listener 不执行阻塞 I/O。
- `control(operation: Callable[[], Awaitable[object]], *, name: str) -> concurrent.futures.Future[object]`：同一个 reservation 管理所有可变控制操作。
- `human_input()`、`set_human_pending(pending: bool)`、`rotate(branch_id: str)`：线程安全投递 owner loop。`resume(peer_id: str) -> concurrent.futures.Future[bool]` 必须走 PermissionManager 的审批。
- `close() -> concurrent.futures.Future[None]`：等待 task/工具/后处理/权限清理结束后退出 owner loop。
- `run_turn(session: AgentSession, value: SessionInput, *, emit: Callable[[AgentEvent], None], images: tuple[ImageContent, ...] = ()) -> TurnResult`：单一 preparation/prompt/finalization 实现，供 SDK wrapper、TUI/RPC controller 使用。AgentSession.send_message 有 runtime 时必须委托 controller，controller 的 execute 直接使用 run_turn，不能循环调用 send_message；无绑定 runtime 的纯 SDK 入口也先创建单一 controller，不保留另一条并发路径。

- [ ] **Step 1: 写控制 reservation 与跨线程 future 的 RED 测试。** 使用 Task 2 conftest 的 coding_session fixture；controller 的 close 与 session.dispose 必须幂等，测试失败也要 finally 关闭 controller。

```python
import asyncio


async def test_external_waits_for_control_and_returns_threadsafe_future(coding_session):
    from concurrent.futures import Future
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.controller import SessionController
    from xdog.coding.core.sessions.models import TurnResult

    started = []
    async def execute(value):
        started.append(value.id)
        return TurnResult("succeeded", None)

    controller = SessionController(coding_session, bound=True, execute=execute)
    controller.start()
    controller.human_input()
    try:
        controller.set_human_pending(True)
        ticket = controller.submit(SessionInput("a", "acp", "peer", "review", (), {}), peer_id="p1")
        assert isinstance(ticket.result, Future)
        await asyncio.wrap_future(ticket.accepted)
        assert started == []
        controller.set_human_pending(False)
        result = await asyncio.wrap_future(ticket.result)
        assert result.status == "succeeded"
    finally:
        await asyncio.wrap_future(controller.close())
```

另写独立的 cleanup settlement 测试：

```python
async def test_control_reservation_holds_until_cleanup(coding_session):
    import asyncio
    import threading
    from xdog.agent.session_input import SessionInput
    from xdog.coding.core.sessions.controller import SessionController
    from xdog.coding.core.sessions.models import TurnResult

    cleanup_started = threading.Event()
    cleanup_release = threading.Event()
    started = threading.Event()
    async def execute(value):
        started.set()
        return TurnResult("succeeded", None)
    async def control():
        try:
            return "body completed"
        finally:
            cleanup_started.set()
            await asyncio.to_thread(cleanup_release.wait)

    controller = SessionController(coding_session, bound=True, execute=execute)
    controller.start()
    controller.human_input()
    try:
        operation = controller.control(control, name="compact")
        assert await asyncio.to_thread(cleanup_started.wait, 2)
        ticket = controller.submit(SessionInput("b", "acp", "peer", "review", (), {}), peer_id="p1")
        await asyncio.wrap_future(ticket.accepted)
        assert not started.is_set()
        cleanup_release.set()
        await asyncio.wrap_future(operation)
        assert (await asyncio.wrap_future(ticket.result)).status == "succeeded"
    finally:
        cleanup_release.set()
        await asyncio.wrap_future(controller.close())
```

所有 async test 设置有限 timeout；以 Event/barrier 控制前后关系，不跨 loop await asyncio.Event。

- [ ] **Step 2: RED。** `uv run pytest packages/coding/tests/test_session_controller.py -q`。Expected: controller 模块缺失。
- [ ] **Step 3: 实现固定 owner loop、分级队列和 Future completion。** controller 线程只创建一次，所有 coroutine 和 Agent turn 在该 loop；队列项预先绑定 session/branch generation。人类优先、editor draft 阻挡自动队列、128/1 MiB 限额、close/rotate 时取消未运行任务。接纳失败只能让 accepted/result 失败，不能先广播 queued。
- [ ] **Step 4: 先写 shared-turn parity 测试，再抽取 `run_turn`。** 比较 SDK 与 controller 在 turn_start、prompt rebuild、pre-turn compaction、tool_end/message_end、turn_end、skill expiry、persistence、error/cancel 上的轨迹。TUI 当前 `_async_agent_turn` 的 context snapshot、自定义 continuation、image validation、drain_inputs 结算必须迁到可复用 turn hooks；不允许只复制 `AgentSession.send_message` 留第二条实现。
- [ ] **Step 5: 写并实现安全预算/取消测试。** 单个人类输入仅允许一个 attached ACP 自动 turn；success/failure/cancel 均不补预算；metadata human/resume 无效；resume 的 ask/deny/allow-all 及实际人工批准结果符合权限；p1 cancel/disconnect 不影响 p2 或 human。关闭时拒绝新提交，等清理再完成 close future。
- [ ] **Step 6: 写并实现失败与竞争矩阵。** persistence 在 queued/running/terminal 各阶段失败；两个线程同时 submit；navigation 与 submission 同时发生；branch rotation 后旧项 discarded；满队列拒绝；producer 结束而 after-run 仍占 reservation；local permission dialog/children/auth/tool-set 操作持有 reservation。所有测试用 Event/barrier，不能依赖脆弱 sleep。
- [ ] **Step 7: GREEN 和提交。**

```bash
uv run pytest packages/coding/tests/test_session_controller.py packages/coding/tests/test_session_turns.py packages/coding/tests/test_session_input_store.py -q
uv run mypy --strict -p xdog.coding
# Expected: 无跨 loop future/event 错误，所有 ownership 测试通过。
git add packages/coding/src/xdog/coding/core packages/coding/tests/test_session_controller.py packages/coding/tests/test_session_turns.py
git commit -m "refactor(coding): centralize session turns and execution ownership"
```

## Task 4: Process ownership, live session router, and secure registry

**Files:**
- Create: `packages/coding/src/xdog/coding/core/sessions/ownership.py`, `router.py`
- Modify: `packages/coding/src/xdog/coding/core/sdk.py`, `agent_session.py`, `session_manager.py`, `sessions/runtime.py`
- Test: `packages/coding/tests/test_session_ownership.py`, `test_session_router.py`
- Test support: `packages/coding/tests/session_owner_process.py`

**Interfaces:**
- `SessionLease.acquire(directory: Path, session_id: str) -> SessionLease`：nonblocking exclusive flock；`close() -> None` 幂等；不以 PID 判断可偷锁。
- `OwnerRecord(session_id: str, cwd: Path, address: Path, generation: str)`：不含旧 capability token。
- `OwnerRegistry(directory: Path).publish(record: OwnerRecord) -> None`, `lookup(session_id: str) -> OwnerRecord`, `remove(record: OwnerRecord) -> None`：只删除 generation 匹配的自己记录。
- `SessionRouter.register(controller: SessionController, record: OwnerRecord) -> None`, `resolve(session_id: str, cwd: Path) -> SessionController`, `retire(session_id: str, generation: str) -> None`；unknown/inactive/project mismatch 使用不同 error code。
- `SessionRuntime`（Task 3 runtime.py）组合 lease/controller/router registration；`close()` 完全结算后才释放 lease。SDK factory 和导航创建新 runtime 前必须先拿目标 lease。

- [ ] **Step 1: 写 RED。**

```python
import pytest


def test_second_writer_cannot_claim_same_session(tmp_path):
    from xdog.coding.core.sessions.ownership import SessionLease

    first = SessionLease.acquire(tmp_path, "s1")
    try:
        with pytest.raises(RuntimeError, match="owned"):
            SessionLease.acquire(tmp_path, "s1")
    finally:
        first.close()
    replacement = SessionLease.acquire(tmp_path, "s1")
    replacement.close()
```

- [ ] **Step 2: RED。** `uv run pytest packages/coding/tests/test_session_ownership.py -q`。Expected: ownership 模块缺失。
- [ ] **Step 3: 实现安全路径与 lease。** directory 的 UID/type/mode、组件 symlink、session ID 格式、socket 地址 containment 检查；先用 O_NOFOLLOW 打开再 fstat。相同进程多次 claim 也不得获得第二个 lease。session 文件首次创建使用预先选定 ID，拿锁后才可读写；加载旧文件同样先锁。startup 中途失败在 finally 释放。
- [ ] **Step 4: 实现 SDK/导航生命周期和 router。** factory 的 create/resume 流程在 load 前 claim；所有 CLI 可写模式共享。导航先安全获取新 owner，取消/结算旧 generation，成功发布新记录后才切换，失败不破坏旧会话。`AgentSession.dispose()` 同步入口不得在 producer 尚未结束时提前释放，退出路径必须等待 runtime close。
- [ ] **Step 5: 用进程测试覆盖 Review Focus 1。** subprocess 真实竞争同一 ID、拿锁后启动失败、SIGKILL 后重新 claim、PID reuse 模拟、旧 record 存在但 socket 不可连接、另一个 owner 记录存在时 cleanup 不删除；测试 registry publish 原子失败和 chmod/symlink 攻击。单一 flock 单测不够。
- [ ] **Step 6: GREEN 和提交。**

```bash
uv run pytest packages/coding/tests/test_session_ownership.py packages/coding/tests/test_session_router.py -q
# Expected: 无第二个历史 writer，失败后 lease 可重新获取。
git add packages/coding/src/xdog/coding/core packages/coding/tests/test_session_ownership.py packages/coding/tests/test_session_router.py packages/coding/tests/session_owner_process.py
git commit -m "feat(coding): enforce live session ownership and routing"
```

## Task 5: Coding ACP backend, Unix endpoint, and --session bridge

**Files:**
- Create: `packages/coding/src/xdog/coding/acp/__init__.py`, `backend.py`, `server.py`, `bridge.py`, `client.py`, `extensions.py`, `cli.py`
- Modify: `packages/coding/src/xdog/coding/cli/args.py`, `main.py`
- Test: `packages/coding/tests/test_acp_backend.py`, `test_acp_bridge.py`, `test_acp_cli.py`
- Test support: `packages/coding/tests/acp_owner_agent.py`

**Interfaces:**
- `CodingBackend(router: SessionRouter, registry: OwnerRegistry, *, fixed_session: str | None = None)` implements Task 1 Backend；session load 遇到 live owner 时内部 attach，不创建第二份 AgentSession。
- `serve_local(runtime: SessionRuntime) -> LocalServer`（async）；`LocalServer.close()` 结束 accept 并只断开自己 peer，不销毁无关 session。
- `attach(record: OwnerRecord) -> AcpAttachment`（async context manager）；验证 cwd/generation，标准 ACP over Unix stream。
- `run_bridge(session_id: str) -> int`（async）；标准 stdio adapter 连接现有 owner，固定 session，不能自建会话。
- `send_prompt(session_id: str, text: str) -> TurnResult`（async）；同一个 SDK client 路径。
- `run_acp(session_id: str | None) -> int`（async）；无 ID 时 lazy backend，new/load 前不创建 AgentSession。
- wire 方法 `_xdog/session_info` 扩展返回当前 peer 已绑定 session 的 ID/cwd/generation；SDK 调用写 `ext_method("xdog/session_info", params)`，SDK 自动加前缀，不能再传一个下划线；wire 方法 `_xdog/resume`（SDK 调用 `ext_method("xdog/resume", params)`）只调用 controller.resume 并等待现有权限批准。initialize metadata 公告扩展版本 1，未知扩展拒绝；不暴露旧 register/deliver/heartbeat 动作。

- [ ] **Step 1: 写 CLI 不创建第二个 session 的 RED 测试。**

```python
from click.testing import CliRunner


def test_attach_argument_conflicts_fail_before_sdk(monkeypatch):
    from xdog.coding.cli.args import cli
    from xdog.coding.core import sdk

    def forbidden(*args, **kwargs):
        raise AssertionError("attach must not create a local session")
    monkeypatch.setattr(sdk, "create_agent_session", forbidden)
    result = CliRunner().invoke(cli, ["--session", "s1", "--resume-id", "s1", "--prompt", "review"])
    assert result.exit_code == 2
    assert "--session cannot be combined with --resume-id" in result.output
```

- [ ] **Step 2: RED。** `uv run pytest packages/coding/tests/test_acp_cli.py -q`。Expected: 旧 CLI 报 No such option，而非断言要求的明确 conflict；不能让仅断言 exit_code=2 的测试在功能不存在时误报通过。实现 conflict 文案固定为 --session cannot be combined with --resume-id。
- [ ] **Step 3: 实现 coding backend 与本地 server。** 通过 Task 1 AcpAdapter 处理标准消息，通过 Task 4 router/registry 选择 owner；所有输出 sink 有每 peer 独立 bounded queue（最多 256 更新且总字节不超过 1 MiB）。慢 peer 超限关闭该 peer 并结算其请求，不阻塞 controller/TUI。不按 event source 动态 import source。
- [ ] **Step 4: 实现桥接与 CLI mode-first 分派。** main 在 `create_agent_session` 前处理 attach/ACP 模式；新 --session 与 resume/picker/model/cwd/config/thinking/permission/files/rpc 冲突必须显式拒绝；无 --acp 时要求 --prompt。保留现有 --resume-id 行为。stdio stdout 仅协议；单次 send_prompt 输出正常结果/错误码。
- [ ] **Step 5: 固定 session 和 peer 隔离测试。** 固定 bridge 收到 session/new 或另一个 session/load/prompt 时拒绝；未知/inactive/cwd mismatch 不回退 latest；load 历史只发当前 peer、不会写历史或广播 TUI；cancel/disconnect 只能影响自己的任务。测试 p1 慢读、p2 正常、human 运行的三方竞争。
- [ ] **Step 6: 权限和 extensions 测试。** attached client 的 allow-all/human/resume metadata 无效；TUI permission handler 不被 ACP client 替换；headless 不含 approval callback 的写操作拒绝。_xdog/resume 在 ask/deny 未批准时不能补预算；_xdog/session_info 不允许查询未绑定会话，错误 generation 不自动重连。
- [ ] **Step 7: 真实 stdio SDK + Unix owner subprocess GREEN 并提交。**

```bash
uv run pytest packages/coding/tests/test_acp_backend.py packages/coding/tests/test_acp_bridge.py packages/coding/tests/test_acp_cli.py -q
# Expected: SDK 跨两个传输完成一次任务，只有一个 AgentSession，stdout 干净。
git add packages/coding/src/xdog/coding/acp packages/coding/src/xdog/coding/cli/args.py packages/coding/src/xdog/coding/main.py packages/coding/tests/test_acp_backend.py packages/coding/tests/test_acp_bridge.py packages/coding/tests/test_acp_cli.py packages/coding/tests/acp_owner_agent.py
git commit -m "feat(coding): route ACP and CLI prompts to live sessions"
```

## Task 6: Route all coding modes through controller and retire old dispatch wiring

**Files:**
- Modify: `packages/coding/src/xdog/coding/modes/interactive/interactive_mode.py`
- Modify: `packages/coding/src/xdog/coding/modes/interactive/after_run_compaction.py`, `after_run_name.py`, `after_run_tools.py`, `message_inputs.py`, `provider_auth.py`
- Modify: `packages/coding/src/xdog/coding/modes/rpc/rpc_mode.py`, `navigation.py`, `extensions.py`, `terminal.py`, `children.py`, `parent_drafts.py`
- Modify: `packages/coding/src/xdog/coding/modes/print_mode.py`, `packages/coding/src/xdog/coding/main.py`（简易终端 fallback 也不能直接运行另一套 turn）
- Modify: `packages/coding/src/xdog/coding/core/agent_session.py`, `sessions/runtime.py`
- Delete: `packages/coding/src/xdog/coding/modes/interactive/external_events.py`, `packages/coding/src/xdog/coding/modes/rpc/external_events.py`
- Test: `packages/coding/tests/test_session_mode_routing.py`, `test_session_control_fences.py`；更新现有相关模式/权限/导航/图片/扩展测试。
- Delete/replace in this task: `packages/coding/tests/test_external_event_interactive.py`, `test_external_event_rpc.py`；将其有效调度断言迁至 controller 测试，以免删除 dispatcher 后 collection 失败。

**Interfaces:**
- 所有模式使用 Task 3 `submit` / `control`，不再实现另一段 prompt loop。
- `SessionRuntime` 在模式启动时绑定现有 session，启动 Task 5 local ACP endpoint；退出顺序：停止接纳、断 peer、清队列、结算 producer/control、close session、撤销 registry/释放 lease。
- TUI `_TurnStamp` 仍作为 display generation fence，不能取代 controller 的 request ownership；adapter 从 SessionEvent 映射 stamp。
- after-run producer 使用 controller 的 completion/reservation receipt；不能继续以常驻 owner thread 的 `is_alive()` 判断某次 turn 是否结束。

- [ ] **Step 1: 写参数化 RED 测试。**

```python
import pytest


@pytest.mark.parametrize("operation", ["compact", "navigate", "tools", "auth", "terminal", "child", "after_run"])
async def test_external_turn_waits_for_control_settlement(mode_controller, operation):
    blocked = mode_controller.begin_control(operation)
    ticket = mode_controller.submit_external("review")
    await mode_controller.accepted(ticket)
    assert not mode_controller.was_started(ticket)
    blocked.finish_body()
    assert not mode_controller.was_started(ticket)
    blocked.finish_cleanup()
    assert (await mode_controller.result(ticket)).status == "succeeded"
```

再加一个针对现有模式的 RED 迁移哨兵，防止上面的 generic controller 测试在 Task 3 已通过而掩盖未接线：

```python
def test_tui_does_not_own_a_second_agent_prompt_loop():
    import inspect
    from xdog.coding.modes.interactive import interactive_mode

    source = inspect.getsource(interactive_mode)
    assert ".agent.prompt(" not in source
    assert "NativeExternalEvents" not in source
```

该静态哨兵只证明旧路径移除，不代替后续真实 TUI/RPC 提交、审批、导航和事件测试。

本任务在 `test_session_control_fences.py` 定义 `mode_controller` fixture：使用真实 SessionController，begin_control 返回两个 threading.Event（body/cleanup）和对应 future，submit_external 构造 Task 2 SessionInput；accepted/result 用 wrap_future，was_started 由订阅的 running 事件判断。不是仅模拟 busy bool。

- [ ] **Step 2: RED。** `uv run pytest packages/coding/tests/test_session_mode_routing.py packages/coding/tests/test_session_control_fences.py -q`。Expected: 现有模式未使用 controller，fence/共用执行测试失败。
- [ ] **Step 3: TUI 改为 controller 提交。** `_start_turn` 只提交/绑定展示上下文；移除 `_async_agent_queue` 的独立取队列和 `_async_agent_turn` 的重复 preparation/prompt。保留图片校验、草稿恢复、扩展对话、上下文输入和 follow-up 的产品行为，通过 shared turns/hooks 执行。TUI editor/composer 状态调用 set_human_pending；文本渲染在主线程事件消费。
- [ ] **Step 4: 迁移所有 control reservations。** slash commands/navigation、compaction review、active-tools restore、provider auth、child supervisor、terminal、parent drafts、after-run name/tools/compaction 都通过同一 controller control reservation；拒绝 self-await；回调只能提交下一项，不在已持有 reservation 时同步等待自己。用 test matrix 逐个移除旧独立 busy 调度，不能只给 ACP 加一个 busy predicate。
- [ ] **Step 5: RPC 和 print 改为 controller submit。** 保留 JSON-lines RPC 原有协议、异步权限响应、图片上传、扩展和 navigation revision。RPC prompt/abort/control 的 ownership 使用 controller request，而不是新建 `_handle_prompt` 并发任务。print 和 main.py 的简易终端 fallback 等待 controller result，原有反馈/JSON 格式保持。
- [ ] **Step 6: 移除旧模式 dispatcher 和所有自动启用。** 删除上述两个 external_events.py、NativeExternalEvents/RpcExternalEvents 接线、旧 RPC external_events capability。旧 core host 模块在 Task 10 删除前不得被模式启动；Eyes 在 Task 9 完成前先明确不可用，不允许临时同时启动两套 host 来让旧测试通过。
- [ ] **Step 7: GREEN，验证现有产品行为没有回退并提交。**

```bash
uv run pytest packages/coding/tests -q
# Expected: 新 mode/control 测试和原有 RPC/TUI/permission/navigation/image/extension 测试全通过；仅剩旧 core host 历史测试在 Task 10 移除；已在本任务迁移的旧 native/RPC dispatcher 测试不得继续启动生产 dispatcher。
git add packages/coding/src/xdog/coding packages/coding/tests
# git diff --cached 审核范围，只提交本任务修改与迁移测试。
git commit -m "refactor(coding): share session scheduling across TUI RPC and ACP"
```

## Task 7: TUI live/reloaded external messages and correlated task states

**Files:**
- Create: `packages/coding/src/xdog/coding/modes/interactive/session_events.py`
- Modify: `packages/coding/src/xdog/coding/modes/interactive/interactive_mode.py`（实时事件与 _load_history）
- Modify: `packages/coding/src/xdog/coding/modes/interactive/components/chat_log.py`（如现有组件不支持 keyed update，添加 request-ID 索引）
- Test: `packages/coding/tests/test_session_message_display.py`, `test_session_history_display.py`

**Interfaces:**
- `SessionPresenter(chat_log: ChatLog).apply(event: SessionEvent) -> None`；只在 TUI 主线程调用，按 request ID 更新同一个输入/状态组件。
- `SessionPresenter.replay(records: tuple[InputRecord, ...], messages: tuple[AgentMessage, ...]) -> None`；恢复时按 request/branch provenance 合并，不重复显示已入模型历史的 canonical input。
- `external_input_text(value: SessionInput) -> str`：复用于 live/replay，以 External input 标签开头，sanitize 终端文本，显示来源、instruction、bounded evidence summary。

- [ ] **Step 1: 写 RED：queued 不等于人类消息，replay 不重复。**

```python
def test_external_display_labels_source_and_evidence():
    from xdog.agent.session_input import SessionInput
    from xdog.coding.modes.interactive.session_events import external_input_text

    value = SessionInput("r1", "acp", "Eyes\x1b[2J", "Review tests",
                         ({"path": "src/a.py"},), {"delivery_id": "d1"})
    text = external_input_text(value)
    assert "Eyes" in text and "Review tests" in text and "src/a.py" in text
    assert "\x1b" not in text
    assert "External input" in text
```

- [ ] **Step 2: RED。** `uv run pytest packages/coding/tests/test_session_message_display.py -q`。Expected: session_events 模块缺失。
- [ ] **Step 3: 实现 live 展示和去重。** queued/paused/running/succeeded/failed/cancelled/discarded 更新单一 keyed component；assistant/tool events 用现有渲染；listener 在后台线程只入 `_event_queue`，不得直接操作组件。ACP 请求与 local human 的来源标签不同，不写入用户 editor history 冒充手输。
- [ ] **Step 4: 实现 replay。** 从 Task 2 InputStore 和模型消息恢复相同内容，旧 external_event 显示原处理要求与证据而不是占位系统文字；崩溃后 unfinished 显示 interrupted，不启动执行。queued 输入与同时运行 assistant 的 chronology 明确按事件顺序而非混淆因果。
- [ ] **Step 5: 展示与请求隔离测试。** 同一 SessionEvent 重发不重复 append；两 peer 与 human 交错、取消、navigation 后迟到消息、history load 重放、ANSI/path source spoof、超过摘要上限时显示 count/展开说明。慢 ACP reader 不影响 TUI 展示，TUI 断开不把别的 peer 输出发送到错误 request。
- [ ] **Step 6: GREEN 和提交。**

```bash
uv run pytest packages/coding/tests/test_session_message_display.py packages/coding/tests/test_session_history_display.py packages/coding/tests/test_acp_bridge.py -q
# Expected: live/replay 一致，来源清楚、状态可见、无重复。
git add packages/coding/src/xdog/coding/modes/interactive packages/coding/tests/test_session_message_display.py packages/coding/tests/test_session_history_display.py
git commit -m "feat(coding): display routed session inputs and durable task states"
```

## Task 8: Eyes version 2 definitions and standard ACP handler

**Files:**
- Modify: `packages/eyes/src/xdog/eyes/models.py`, `loader.py`, `executor.py`, `events.py`
- Create: `packages/eyes/src/xdog/eyes/acp_client.py`, `handlers/acp.py`
- Modify: `packages/eyes/pyproject.toml`, `uv.lock`, `packages/eyes/tests/conftest.py`
- Test: `packages/eyes/tests/test_acp_handler.py`, `test_acp_delivery.py`；更新 `test_loader.py`, `test_executor.py`

**Interfaces:**
- `HandlerDef(id: str, type: Literal['acp'], command: tuple[str, ...], session: str, instruction: str)`；`ObserverDef.version: Literal[2]`。
- `AcpHandler(definition: HandlerDef, *, cwd: Path)`；`connect() -> None`, `deliver(delivery: Delivery) -> DeliveryResult`, `resume() -> bool`, `close() -> None`（async）。
- `DeliveryResult(status: Literal['succeeded','failed','cancelled','not-sent','outcome-unknown','paused'], error: str = '')`。
- `connect()` 用官方 SDK spawn_agent_process，initialize/load，只有具名扩展协商成功才查询 owner generation/resume；第三方无扩展时不能伪称验证过 generation，绑定同一个 ACP process/connection并在断连后停止，不自动重新 attach。
- `deliver` 使用标准 session/prompt：instruction 与 untrusted evidence 文本分段；Eyes provenance 放 `_meta` 的 `xdog` namespace，仅作为数据；不强制第三方 client 理解专用 message type。
- 现有 `observe()` 的 `deliver` 参数类型改为 `Callable[[Delivery], Awaitable[DeliveryResult]]`，其他 scanner/clock/sleep/cancel/resume 参数沿用；不再解释旧 accepted/duplicate/busy ACK。

- [ ] **Step 1: 写 version 2 的 RED 测试。**

```python
import pytest
from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.loader import parse


def test_v2_acp_target_is_explicit(tmp_path, observer_data):
    observer_data["version"] = 2
    observer_data["handlers"] = [{"id": "notify", "type": "acp",
        "command": ["xdog-coding", "--acp"], "session": "s1", "instruction": "Review tests"}]
    definition = parse(observer_data, base_dir=tmp_path)
    assert definition.handlers[0].session == "s1"
    assert definition.handlers[0].command == ("xdog-coding", "--acp")
    observer_data["handlers"][0].pop("session")
    with pytest.raises(ObserverValidationError):
        parse(observer_data, base_dir=tmp_path)
```

- [ ] **Step 2: RED。** `uv run pytest packages/eyes/tests/test_loader.py -q`。Expected: loader 拒绝 version 2。
- [ ] **Step 3: 添加与 Task 1 相同 Python marker 的官方 SDK 依赖并实现 v2 loader/handler。** command 必须非空 argv 数组，每项合法 UTF-8/无 NUL，拒绝 shell string；保留 unknown field、unique ID、reference、glob 与 roots validation。v1 报含迁移提示的 unknown_version；更新 fixture，不改变 file source/detector 行为。
- [ ] **Step 4: 实现 ACP client。** spawn 用 argv，不 shell=True；target session/cwd load；反向权限默认 cancelled；client source label 明确自报。每个 handler 一条连接串行 prompt，多个 route 共享 handler不能并发给同一 session 乱序；connect 前不采集，失败 close 所有已连接 handler。
- [ ] **Step 5: 测 outcome-unknown 再修改 executor。** 用 fake SDK peer 在收到 prompt 后断开，断言只收到一次请求、状态 outcome-unknown、暂停且不指数重试；连接失败且尚未发送可返回 not-sent 重试。failed 是执行失败不是 not-sent，不重复执行；只有 PromptResponse 终态标完成，收到 update 不能 report succeeded。
- [ ] **Step 6: 加入噪音、安全与容量测试。** source 不重复扫描、baseline/净变化原测试继续通过；事件第一次尝试后内容不变；恶意 argv/path/metadata 不执行 shell、不 grant permissions；256 path/128 pending 保持溢出暂停。暂停不消费后续证据，resume 不无条件重发 outcome-unknown，要求显式 stop/rebaseline。
- [ ] **Step 7: GREEN 和提交。**

```bash
uv run pytest packages/eyes/tests/test_loader.py packages/eyes/tests/test_executor.py packages/eyes/tests/test_acp_handler.py packages/eyes/tests/test_acp_delivery.py -q
uv run mypy --strict -p xdog.eyes
# Expected: v2/ACP 完成语义通过，未发送失败与不确定结果被严格区分。
git add packages/eyes uv.lock
git commit -m "feat(eyes): deliver versioned observer tasks through ACP"
```

## Task 9: Eyes-owned local control and live-target lifecycle

**Files:**
- Create: `packages/eyes/src/xdog/eyes/local_control.py`
- Modify: `packages/eyes/src/xdog/eyes/runtime.py`, `process.py`, `control.py`, `cli.py`
- Modify: `packages/eyes/src/xdog/eyes/skills/eyes-observers/SKILL.md`, `examples/source-test-review.json`
- Test: `packages/eyes/tests/test_runtime.py`, `test_cli.py`, `test_end_to_end.py`, `test_local_control.py`

**Interfaces:**
- `RuntimeLocation(directory: Path, socket: Path, generation: str)`：Eyes 自己的 runtime control，不携带 harness token。
- `runtime_location(definition: Path | None = None) -> RuntimeLocation`：用户私有目录的 observer runtime；只管理本 UID 注册，不以 definition 名称推断目标 session。
- `ensure_runtime(location: RuntimeLocation) -> None`；`request(location, action: str, body: dict[str, JsonValue]) -> dict[str, JsonValue]`（async）。只提供 Eyes start/status/stop/resume 控制，不接受 agent events，不是旧 deliver 协议的别名。
- `ObserverRuntime.start(definition: ObserverDef) -> registration_id`：全部 handler connect/target check 成功后启动 source；对相同 normalized definition/name 幂等，不同定义要求先 stop。
- status 返回 source health、ACP processing、target ID、owner/connection 状态、paused/outcome-unknown；不可将 prompt 发送成功等同执行成功。

- [ ] **Step 1: 写不依赖旧 capability 的 RED 测试。**

```python
import json
from xdog.eyes.cli import main


def test_validate_v2_needs_no_host_environment(tmp_path, monkeypatch, capsys, observer_data):
    monkeypatch.delenv("XDOG_EVENT_CONTEXT", raising=False)
    observer_data["version"] = 2
    observer_data["handlers"] = [{"id": "notify", "type": "acp",
        "command": ["xdog-coding", "--acp"], "session": "s1", "instruction": "Review tests"}]
    path = tmp_path / "eyes.json"
    path.write_text(json.dumps(observer_data))
    assert main(["validate", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
```

再写 `test_start_without_event_context_uses_acp_target`：测试脚本启动真实 SDK ACP peer，传入明确 session，运行 start/status/stop；整个生命周期不设置旧环境变量、不创建 descriptor。

- [ ] **Step 2: RED。** `uv run pytest packages/eyes/tests/test_local_control.py packages/eyes/tests/test_cli.py -q`。Expected: start 仍要求旧 event context 或新 control 模块缺失。
- [ ] **Step 3: 重写控制面生命周期。** 私有目录/lock/socket UID/type/mode checks、并发 start、pid reuse 保守处理、startup failure rollback、最后一个 registration stop 后进程退出。控制记录不包含模型凭据；定义文件路径不能决定不受限 socket 地址。
- [ ] **Step 4: 接入 handler 与 target 生命周期。** owner generation/branch 变化或 ACP disconnect 停止注册，不重路由；多个 handlers 初始化部分失败关闭所有连接且没有 scan；stop 撤销 queued request，只按请求所有权 cancel，不取消人类 turn；正在执行任务按 controller cancellation semantics 结算。最后一个 observer 停止清理 bridge 子进程，不能关闭 TUI owner。
- [ ] **Step 5: resume 安全与 end-to-end。** 只在目标协商 `_xdog/resume` 后申请；无人审批失败，不清除 controller budget；第三方不支持则清楚提示不支持。批准后一个新 automatic turn，之后再次暂停。真实文件变化到活跃 session、TUI history 的闭环使用确定性模型桩和真实 ACP 连接。
- [ ] **Step 6: 更新 skill/example。** 明确 version 2、显式 session ID、argv、root 相对定义文件、审批启动、不可信证据、真实完成与 outcome-unknown、无 crash replay。运行所有 Eyes 测试验证旧 source 行为保留。
- [ ] **Step 7: GREEN 和提交。**

```bash
uv run pytest packages/eyes/tests -q
# Expected: 无 XDOG_EVENT_CONTEXT 也能启动明确目标，关闭后无残留子进程，owner TUI 仍活着。
git add packages/eyes
git commit -m "refactor(eyes): own observer lifecycle independently of coding host"
```

## Task 10: Delete legacy host, documentation migration, and release verification

**Files:**
- Delete: `packages/coding/src/xdog/coding/core/external_events/`（全部文件）
- Delete: `packages/eyes/src/xdog/eyes/handlers/session_notify.py`, `packages/eyes/src/xdog/eyes/transport/`（旧 client/protocol）
- Delete: `docs/eyes-host-protocol.md`, `docs/fixtures/eyes-host-v1.json`
- Delete/replace: `packages/coding/tests/test_external_event_{host,inbox,interactive,rpc,session}.py`，把可复用历史/权限断言迁到新测试
- Delete/replace: `packages/eyes/tests/test_protocol.py`（实际旧 host protocol/client 测试文件）
- Modify: `packages/coding/src/xdog/coding/core/agent_session.py`、`agent`/`eyes` 中剩余旧接线；保留 `agent/external_event.py` 作为历史兼容
- Create: `docs/acp-session-routing.md`, `packages/coding/tests/test_no_legacy_event_host.py`
- Modify: `AGENTS.md`, `packages/agent/README.md`, `packages/coding/README.md`, `packages/eyes/README.md`, `docs/eyes-verification.md`
- Modify: `docs/superpowers/specs/2026-10-14-eyes-design.md`, `docs/superpowers/plans/2026-10-14-eyes.md`（仅加 superseded 标记与新设计引用）
- Test: `packages/agent/tests/test_acp_packaging.py`, `packages/eyes/tests/test_acp_packaging.py`（artifact assertions），保留现有打包测试

**Interfaces:**
- 最终公开接口仅为 ACP adapter/backend、session routing/controller、Eyes v2 CLI/skill；旧 register/deliver/heartbeat/capability 接口不存在。
- 旧 external_event 数据仍可 messages_to_dicts/dicts_to_messages 和 LLM 投影；不允许用兼容读取为理由重建 host。

- [ ] **Step 1: 写旧 host 删除的 RED 审计测试。**

```python
from pathlib import Path


def test_previous_event_host_implementation_is_removed():
    repo = Path(__file__).resolve().parents[3]
    assert not (repo / "packages/coding/src/xdog/coding/core/external_events").exists()
    assert not (repo / "packages/eyes/src/xdog/eyes/handlers/session_notify.py").exists()
    for package in ("coding", "eyes"):
        for path in (repo / "packages" / package / "src").rglob("*.py"):
            text = path.read_text()
            assert "XDOG_EVENT_CONTEXT" not in text, path
            assert "enable_external_events" not in text, path
```

- [ ] **Step 2: RED。** `uv run pytest packages/coding/tests/test_no_legacy_event_host.py -q`。Expected: 旧 core host 目录仍在，审计失败。
- [ ] **Step 3: 删除旧服务器与测试，迁移剩余断言。** 不保留兼容 alias；删除 import/env masking/descriptor rotation/旧 resume hook；将安全的 subprocess_env 合并和非秘密 XDOG_SESSION_ID 注入保留。旧 fixture 和协议文档删除，测试引用移除。先 `git ls-files` 核对确切路径，避免删除不相关测试。
- [ ] **Step 4: 更新所有公开文档与依赖声明。** README 给出 new ACP / fixed --session / Eyes v2 完整例子和 inactive/permission/outcome-unknown 行为；说明 stdio 是标准入口、Unix socket 是 XDOG local transport、当前无 TCP/Windows/daemon；agent/eyes 不再错误声称 stdlib-only。历史设计加 superseded，不批量改写历史内容。
- [ ] **Step 5: 打包与隔离安装。**

```bash
uv build --package xdog-ai --out-dir /tmp/xdog-acp-dist
uv build --package xdog-agent --out-dir /tmp/xdog-acp-dist
uv build --package xdog-coding --out-dir /tmp/xdog-acp-dist
uv build --package xdog-eyes --out-dir /tmp/xdog-acp-dist
uvx twine check /tmp/xdog-acp-dist/*
uv venv /tmp/xdog-acp-isolated
uv pip install --python /tmp/xdog-acp-isolated/bin/python /tmp/xdog-acp-dist/xdog_eyes-*.whl
/tmp/xdog-acp-isolated/bin/xdog-eyes --help
```

Expected: wheel/sdist 均有效；Eyes 隔离环境只安装 SDK 与其依赖，不安装 ai/agent/coding/claw/flow；skill/example/py.typed/license 存在。另建 agent-only 环境安装 ai+agent wheel，用确定性测试脚本跑 SDK stdio，不借用 workspace coding。Python SDK 的 Requires-Python 上限在测试记录中列明。

- [ ] **Step 6: 全量验证。**

```bash
git diff --check
uv run python scripts/test.py
uv run ruff check packages
uv run mypy --strict -p xdog.ai -p xdog.agent -p xdog.tui -p xdog.flow -p xdog.site -p xdog.claw -p xdog.coding -p xdog.eyes
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
```

Expected: 八包测试、lint、strict types、Flow 示例通过。记录实际数量/耗时/跳过原因，不复制上一版统计。检查没有残留 Eyes/ACP bridge 子进程和 owner registry；不能用 kill-all 清理别人的进程。

- [ ] **Step 7: 整分支独立 review 和修复。** review 范围从本次计划开始前的 commit 到最终 HEAD，关注 Review Focus 及“旧 host 真删除、TUI 不负责路由、ACP 不绕过 session”。执行方式若授权 reviewer，遵循对应 skill；不擅自指定不同模型。不通过 review 的重要问题用 RED→GREEN 修复后重跑全套；未做真实模型或外部 IDE 验证明确说明。
- [ ] **Step 8: GREEN 后提交和交付。**

```bash
git add AGENTS.md docs packages uv.lock
git diff --cached --check
git commit -m "refactor: remove legacy eyes host and document ACP session routing"
git status --short --branch
```

只提交本任务改动，不包含不相关用户变更。向用户报告命令、功能边界、实际验证结果、未验证项及分支状态；不自动 push/开 PR/发布。

## Spec coverage and plan self-review

| Approved spec | Implementation tasks |
| --- | --- |
| §1 意图 / §3 架构 / §4 包边界 | 1, 3, 5, 6, 8 |
| §2 现有 duplicate turn 和 SDK 先建 session 问题 | 3, 5, 6 |
| §5 单 owner、队列、控制结算 | 2, 3, 6 |
| §6 lease、registry、安全、generation | 4, 5, 9 |
| §7 stdio/--session/ACP semantics | 1, 5 |
| §8 provenance、持久状态、TUI live/replay | 2, 7 |
| §9 权限、cancel、预算/resume | 1, 3, 5, 9 |
| §10 Eyes v2、handler、结果不确定性 | 8, 9 |
| §11 删除旧 host，历史兼容 | 2, 6, 10 |
| §12 验收与 release | 所有任务的 RED/GREEN，10 的全量与 artifact 验证 |
| §13 标准/自定义 transport 与审核边界 | 1, 5, 10，以下执行交接 |

执行前必须核对共享接口：Task 1 Backend → Task 5 CodingBackend；Task 2 SessionInput/InputRecord → Tasks 3/7/8；Task 3 Controller/Future → Tasks 4/5/6；Task 4 OwnerRecord → Task 5 bridge → Task 9 target lifetime；Task 8 DeliveryResult → Task 9 runtime。接口冲突先记录裁决，不能在各任务内部隐式改名。

### Self-review decisions

- 用独立、原子写入的 request sidecar 记录排队状态，避免 controller 正在导航/压缩时接纳 queued 输入覆盖 staged session history；sidecar 不具备自动重放语义。
- 官方 SDK 0.12.1 的 ext_method 自动加 `_`，wire 名与 Python 调用名分别固定，避免重复下划线；更新类型使用本地明确 union，不引用不存在的 SessionUpdate alias。
- Python 3.15+ 不强装当前不兼容 SDK；用依赖 marker 保持 workspace 可解析，并对 ACP 入口明确报不支持。第一版 ACP 验证范围为 Python 3.12–3.14。
- 删除 dispatcher 的任务同时迁移 native/RPC 旧测试，避免中间提交因已删除模块 collection 失败。旧 core host 在最终迁移任务删除，产品模式不会同时启用它。
- 所有跨 loop 返回值用 concurrent future；测试同步使用 threading.Event；共享 turn 不通过 public send_message 递归进入 controller。

## Execution handoff

计划写完和自审不等于已执行。用户先审核本计划并选择执行方式：

- **Native**：当前会话逐任务实现，最后独立整分支 review；接口耦合较多，建议采用，减少反复交接。
- **Subagent-driven**：每任务独立实现和审核，最后整分支 review；审核更密集，但上下文和协调成本更高。

此前“直接在原目录而不是 worktree”约束继续有效。本文不创建新的目标、不扩大自动提交/推送授权。选择执行方式后使用相应 skill、建立仅本计划的执行 ledger，先 TDD，再实施。
