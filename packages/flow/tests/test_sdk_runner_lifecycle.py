"""Offline integration of node-owned Agents, versioned inheritance and settlement."""
from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.core import StreamFn
from xdog.agent.skills import SkillManager
from xdog.agent.tools.tool_filesystem import CONFINE_CTX_KEY, WORKSPACE_CTX_KEY
from xdog.ai.types import (
    AssistantMessage,
    Context,
    Model,
    StreamOptions,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from xdog.ai.utils.event_stream import EventStream
from xdog.flow.errors import WorkflowExecutionError
from xdog.flow.models import NodeDef, Port
from xdog.flow.runners import SdkRunner
from xdog.flow.tools import ToolRegistry


class OfflineAdapter:
    """A shared, metadata-aware provider stand-in; Agent must not close it."""

    def __init__(self, *responses: AssistantMessage, models: tuple[str, ...] = ("node/new",)) -> None:
        self.responses = list(responses)
        self.models = models
        self.calls: list[tuple[str, Context, StreamOptions]] = []
        self.resolutions: list[str] = []
        self.close_calls = 0

    def resolve_model(self, model: str) -> Model | None:
        self.resolutions.append(model)
        return Model(id=model) if model in self.models else None

    def __call__(self, model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
        assert self.close_calls == 0
        self.calls.append((model, context, options))
        message = self.responses.pop(0) if self.responses else AssistantMessage(
            content=(TextContent(text="node answer"),), usage=Usage(input=2, output=3),
        )
        return EventStream.empty(message)

    async def aclose(self) -> None:
        self.close_calls += 1


class ObservedAgent(Agent):
    """Real SDK behavior with observations, not a mock replacement for close."""

    created: list[ObservedAgent] = []

    def __init__(self, stream_fn: StreamFn, **kwargs: Any) -> None:
        super().__init__(stream_fn, **kwargs)
        self.operations: list[str] = []
        self.created.append(self)

    def dump(self, *, recovery: bool = False) -> dict[str, Any]:
        assert not self.closed, "the runner exports before releasing its node Agent"
        self.operations.append("dump")
        return super().dump(recovery=recovery)

    async def aclose(self) -> None:
        self.operations.append("close")
        await super().aclose()


@pytest.fixture
def owned_agents(monkeypatch: pytest.MonkeyPatch) -> list[ObservedAgent]:
    ObservedAgent.created = []
    monkeypatch.setattr("xdog.agent.agent.Agent", ObservedAgent)
    return ObservedAgent.created


def write_skill(root: Path, slug: str, scope: str, body: str) -> None:
    directory = root / slug
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: procedure\nmetadata:\n  scope: {scope}\n---\n{body}\n",
        encoding="utf-8",
    )


async def seed_snapshot(tmp_path: Path, *, skills: bool = False) -> dict[str, Any]:
    root = tmp_path / "ancestor-skills"
    selected = []
    if skills:
        write_skill(root, "ancestor-session", "session", "ANCESTOR_SESSION_BODY")
        write_skill(root, "ancestor-turn", "turn", "ANCESTOR_TURN_BODY")
        selected = ["ancestor-session", "ancestor-turn"]
    source = Agent(
        OfflineAdapter(models=("ancestor/old",)),
        config=AgentConfig(model="ancestor/old", system_prompt="ANCESTOR_BASE"),
        skills=SkillManager(root, packaged={}), active_skills=selected,
    )
    # An idle history plus a pending turn skill is a legitimate host snapshot.
    source.append_message(UserMessage(content="ancestor question"))
    source.append_message(AssistantMessage(
        content=(TextContent(text="ancestor answer"),), usage=Usage(total_tokens=100),
    ))
    try:
        return source.dump()
    finally:
        await source.aclose()


def runner_for(adapter: StreamFn, *, registry: ToolRegistry | None = None, **kwargs: Any) -> SdkRunner:
    return SdkRunner(
        stream_fn_factory=lambda model: adapter, tool_registry=registry or ToolRegistry(),
        web_search_fn_factory=None, **kwargs,
    )


async def run_node(
    runner: SdkRunner, node: NodeDef | None = None, *, session: Mapping[str, object] | None = None,
    sink: dict[str, object] | None = None, timeout: float = 2,
) -> tuple[object, int]:
    return await runner.run(
        node or NodeDef(id="node", model="node/new"), system_prompt="NODE_BASE", user_prompt="child question",
        model="node/new", timeout=timeout, inputs={}, step=0, fan_index=None, session=session, session_sink=sink,
    )


@pytest.mark.parametrize("selected", [(), ("child-session",), ("child-turn",)])
@pytest.mark.parametrize("explicit_model", [False, True])
@pytest.mark.parametrize("explicit_system", [False, True])
async def test_versioned_inheritance_keeps_node_selection_and_explicit_overrides(
    tmp_path: Path, owned_agents: list[ObservedAgent], selected: tuple[str, ...],
    explicit_model: bool, explicit_system: bool,
) -> None:
    snapshot = await seed_snapshot(tmp_path, skills=True)
    original = copy.deepcopy(snapshot)
    root = tmp_path / "child-skills"
    write_skill(root, "child-session", "session", "CHILD_SESSION_BODY")
    write_skill(root, "child-turn", "turn", "CHILD_TURN_BODY")
    # The child's manager deliberately cannot resolve ancestor skill identities.
    # Its explicit selection must win BEFORE restore attempts those bindings.
    models = ("node/new",) if explicit_model else ("node/new", "ancestor/old")
    adapter = OfflineAdapter(models=models)
    runner = runner_for(adapter, skill_dirs=[root])
    node = NodeDef(
        id="child", model="node/new" if explicit_model else None,
        system_prompt="explicit" if explicit_system else "", skills=selected,
    )
    sink: dict[str, object] = {}
    assert await run_node(runner, node, session=snapshot, sink=sink) == ("node answer", 5)
    assert snapshot == original, "per-node overrides must not mutate a shared upstream snapshot"
    agent = owned_agents[-1]
    assert agent.closed and agent.operations[-2:] == ["dump", "close"]
    assert adapter.close_calls == 0 and len(adapter.calls) == 1
    requested_model, context, _ = adapter.calls[0]
    assert requested_model == ("node/new" if explicit_model else "ancestor/old")
    if explicit_model:
        assert "ancestor/old" not in adapter.resolutions, "override must precede adapter validation"
    prefix = str(context.system_prompt)
    assert ("NODE_BASE" if explicit_system else "ANCESTOR_BASE") in prefix
    assert ("ANCESTOR_BASE" if explicit_system else "NODE_BASE") not in prefix
    assert "ANCESTOR_SESSION_BODY" not in prefix and "ANCESTOR_TURN_BODY" not in str(context.messages)
    assert "ancestor question" in str(context.messages) and "ancestor answer" in str(context.messages)
    assert "child question" in str(context.messages)
    assert ("CHILD_SESSION_BODY" in prefix) is (selected == ("child-session",))
    assert str(context.messages).count("CHILD_TURN_BODY") == (1 if selected == ("child-turn",) else 0)
    saved = sink["session"]
    assert isinstance(saved, dict) and saved["format"] == "xdog.agent.snapshot" and saved["version"] == 1
    assert [record["slug"] for record in saved["skills"]] == (["child-session"] if "child-session" in selected else [])
    assert "CHILD_TURN_BODY" not in str(agent.messages) and "CHILD_TURN_BODY" not in str(saved["messages"])
    assert saved["base_system_prompt"] == ("NODE_BASE" if explicit_system else "ANCESTOR_BASE")


async def test_legacy_model_override_is_copied_before_restore_validation(owned_agents: list[ObservedAgent]) -> None:
    adapter = OfflineAdapter()
    snapshot = {"model": "removed/model", "system_prompt": "OLD_BASE"}
    sink: dict[str, object] = {}
    assert await run_node(runner_for(adapter), session=snapshot, sink=sink) == ("node answer", 5)
    assert snapshot == {"model": "removed/model", "system_prompt": "OLD_BASE"}
    assert adapter.calls[0][0] == "node/new"
    assert str(adapter.calls[0][1].system_prompt).endswith("OLD_BASE")
    saved = sink["session"]
    assert isinstance(saved, dict) and saved["base_system_prompt"] == "OLD_BASE"
    assert owned_agents[-1].closed and adapter.close_calls == 0


@pytest.mark.parametrize("invalid", ["model", "version", "interrupted"])
async def test_restore_rejection_closes_without_model_call_or_sink_publication(
    tmp_path: Path, owned_agents: list[ObservedAgent], invalid: str,
) -> None:
    snapshot = await seed_snapshot(tmp_path)
    if invalid == "version":
        snapshot["version"] = 999
    elif invalid == "interrupted":
        snapshot["interrupted"] = True
    adapter = OfflineAdapter()
    sink: dict[str, object] = {"existing": "untouched"}
    # Without an explicit model override, the saved model is genuinely unavailable.
    node = NodeDef(id="child", model=None if invalid == "model" else "node/new")
    with pytest.raises(ValueError):
        await run_node(runner_for(adapter), node, session=snapshot, sink=sink)
    assert len(owned_agents) == 1 and owned_agents[0].closed
    assert not adapter.calls and adapter.close_calls == 0 and sink == {"existing": "untouched"}


async def test_inherited_structured_run_preserves_sink_tool_inventory_context_and_tokens(
    tmp_path: Path, owned_agents: list[ObservedAgent],
) -> None:
    snapshot = await seed_snapshot(tmp_path)
    adapter = OfflineAdapter(
        AssistantMessage(content=(ToolCall(id="denied", name="not_granted", arguments={}),),
                         stop_reason="toolUse", usage=Usage(input=2, output=3)),
        AssistantMessage(content=(ToolCall(id="probe", name="probe", arguments={}),),
                         stop_reason="toolUse", usage=Usage(input=4, output=2)),
        AssistantMessage(content=(ToolCall(id="submit", name="submit_result", arguments={"result": {"answer": "ok"}}),),
                         stop_reason="toolUse", usage=Usage(input=2, output=3, total_tokens=11)),
        AssistantMessage(content=(TextContent(text="finished"),), usage=Usage(input=1, output=1)),
    )
    seen_ctx = []

    async def execute(
        call_id: str, args: dict[str, Any], cancel: asyncio.Event | None = None,
        on_update: Any = None, **kwargs: Any,
    ) -> AgentToolResult:
        seen_ctx.append(kwargs["ctx"])
        return AgentToolResult(content=(TextContent(text="probed"),))

    async def denied(*args: Any, **kwargs: Any) -> AgentToolResult:
        pytest.fail("A registered but unselected tool must not run")

    registry = ToolRegistry()
    registry.register(AgentTool(name="probe", parameters={"type": "object"}, execute=execute))
    registry.register(AgentTool(name="not_granted", parameters={"type": "object"}, execute=denied))
    runner = runner_for(adapter, registry=registry, workspace=tmp_path, confine_to=[tmp_path])
    node = NodeDef(id="structured", model="node/new", system_prompt="explicit", tools=("probe",),
                   output_ports=(Port("out", schema={"type": "object"}),))
    sink: dict[str, object] = {}
    assert await run_node(runner, node, session=snapshot, sink=sink) == ({"answer": "ok"}, 24)
    assert len(seen_ctx) == 1
    assert seen_ctx[0][WORKSPACE_CTX_KEY] == str(tmp_path)
    assert seen_ctx[0][CONFINE_CTX_KEY] == [str(tmp_path)]
    assert seen_ctx[0]["flow_output_schema"] == {"type": "object"}
    assert seen_ctx[0]["flow_result_sink"]["result"] == {"answer": "ok"}
    assert all({tool.name for tool in context.tools or ()} == {"probe", "submit_result"}
               for _, context, _ in adapter.calls)
    assert "submit_result" in str(adapter.calls[0][1].system_prompt)
    assert str(tmp_path) in str(adapter.calls[0][1].system_prompt)
    result = next(message for message in adapter.calls[1][1].messages if isinstance(message, ToolResultMessage))
    assert result.tool_call_id == "denied" and result.is_error
    assert owned_agents[-1].closed and owned_agents[-1].operations[-2:] == ["dump", "close"]
    assert adapter.close_calls == 0


@pytest.mark.parametrize("failure", ["prompt", "dump", "missing_result", "provider"])
async def test_errors_still_close_owned_agent(
    monkeypatch: pytest.MonkeyPatch, owned_agents: list[ObservedAgent], failure: str,
) -> None:
    adapter = OfflineAdapter()
    node = NodeDef(id="node", model="node/new")
    sink: dict[str, object] = {}
    if failure == "prompt":
        async def fail_prompt(self: Agent, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("prompt failure")
        monkeypatch.setattr(ObservedAgent, "prompt", fail_prompt)
    elif failure == "dump":
        def fail_dump(self: Agent, **kwargs: Any) -> Any:
            raise RuntimeError("dump failure")
        monkeypatch.setattr(ObservedAgent, "dump", fail_dump)
    else:
        node = NodeDef(id="structured", model="node/new", output_ports=(Port("out", schema={"type": "object"}),))
        if failure == "provider":
            adapter.responses.append(AssistantMessage(stop_reason="error", error_message="offline provider failed"))
    expected = RuntimeError if failure in ("prompt", "dump") else WorkflowExecutionError
    with pytest.raises(expected):
        await run_node(runner_for(adapter), node, sink=sink)
    assert len(owned_agents) == 1 and owned_agents[0].closed and adapter.close_calls == 0
    assert ("session" in sink) is (failure in ("missing_result", "provider"))


@pytest.mark.parametrize("cancel_via", ["timeout", "task", "repeated_task"])
async def test_node_cancellation_settles_parallel_tools_before_return_without_closing_provider(
    owned_agents: list[ObservedAgent], cancel_via: str,
) -> None:
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    entered: list[str] = []
    settled: list[str] = []
    signals: list[asyncio.Event] = []

    async def hold(
        call_id: str, args: dict[str, Any], cancel: asyncio.Event, on_update: Any, **kwargs: Any,
    ) -> AgentToolResult:
        entered.append(call_id)
        signals.append(cancel)
        if len(entered) == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            settled.append(call_id)
        return AgentToolResult(content=())

    registry = ToolRegistry()
    registry.register(AgentTool(name="hold", parameters={"type": "object"}, execute=hold))
    adapter = OfflineAdapter(AssistantMessage(
        content=tuple(ToolCall(id=str(i), name="hold", arguments={}) for i in range(2)), stop_reason="toolUse",
    ))
    sink: dict[str, object] = {}
    task = asyncio.create_task(run_node(
        runner_for(adapter, registry=registry), NodeDef(id="held", tools=("hold",)),
        timeout=0.2 if cancel_via == "timeout" else 5, sink=sink,
    ))
    try:
        await asyncio.wait_for(started.wait(), 2)
        if cancel_via != "timeout":
            task.cancel()
        await asyncio.wait_for(cleaning.wait(), 2)
        if cancel_via == "repeated_task":
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
        assert not task.done() and not owned_agents[-1].closed
        assert not settled and not sink and adapter.close_calls == 0
    finally:
        release.set()
    with pytest.raises(TimeoutError if cancel_via == "timeout" else asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert sorted(settled) == ["0", "1"] and all(signal.is_set() for signal in signals)
    assert owned_agents[-1].closed and owned_agents[-1].operations == ["close"]
    assert not sink and len(adapter.calls) == 1 and adapter.close_calls == 0
    # Closing a node must not render a shared adapter unusable for a later node.
    assert await run_node(runner_for(adapter)) == ("node answer", 5)
    assert owned_agents[-1].closed and adapter.close_calls == 0


@pytest.mark.parametrize("selected", [(), ("guard", "once")])
async def test_executor_inherits_real_runner_dump_but_rearms_only_declared_node_skills(
    tmp_path: Path, owned_agents: list[ObservedAgent], selected: tuple[str, ...],
) -> None:
    from xdog.flow.executor import execute
    from xdog.flow.models import OUT_NODE_ID, EdgeDef, InheritSpec, WorkflowDef

    write_skill(tmp_path / "skills", "guard", "session", "SESSION_GUARD_MARKER")
    write_skill(tmp_path / "skills", "once", "turn", "TURN_MARKER")
    adapters = {
        "ancestor/old": OfflineAdapter(models=("ancestor/old",)),
        "node/new": OfflineAdapter(models=("node/new",)),
    }
    workflow = WorkflowDef(
        name="sdk-lifetime", provider="offline", entry="first", default_model="ancestor/old",
        nodes=(
            NodeDef(id="first", prompt="research", skills=("guard", "once"), output_ports=(Port("out"),)),
            NodeDef(id="next", model="node/new", prompt="critique", skills=selected,
                    inherit=InheritSpec(from_node="first"), output_ports=(Port("out"),)),
        ),
        edges=(EdgeDef(src="first", dst="next", mapping=()),
               EdgeDef(src="next", dst=OUT_NODE_ID, mapping=(("out", "result"),))),
    )
    result = await execute(workflow, base_dir=tmp_path, stream_fn_factory=lambda model: adapters[model])
    assert not result.runtime["failed"]
    assert result.runtime["out"]["result"] == "node answer"
    first = adapters["ancestor/old"].calls[0][1]
    second = adapters["node/new"].calls[0][1]
    assert "research" in str(second.messages) and "critique" in str(second.messages)
    assert str(first.messages).count("TURN_MARKER") == 1
    assert str(second.messages).count("TURN_MARKER") == (1 if selected else 0)
    assert str(second.system_prompt).count("SESSION_GUARD_MARKER") == (1 if selected else 0)
    assert len(owned_agents) == 2 and all(agent.closed for agent in owned_agents)
    assert all("TURN_MARKER" not in str(agent.messages) for agent in owned_agents)
    assert all(adapter.close_calls == 0 for adapter in adapters.values())


@pytest.mark.parametrize("finish", [False, True])
async def test_node_settles_provider_request_stream_not_shared_adapter(
    owned_agents: list[ObservedAgent], finish: bool,
) -> None:
    from collections.abc import AsyncIterator

    from xdog.ai.types import AssistantMessageEvent, DoneEvent, StartEvent

    started, cleaned = asyncio.Event(), asyncio.Event()
    message = AssistantMessage(content=(TextContent(text="stream answer"),), usage=Usage(input=2, output=3))

    class HoldingAdapter(OfflineAdapter):
        def __call__(self, model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
            self.calls.append((model, context, options))
            result: asyncio.Future[AssistantMessage] = asyncio.get_running_loop().create_future()

            async def generate() -> AsyncIterator[AssistantMessageEvent]:
                try:
                    started.set()
                    yield StartEvent(partial=message)
                    if finish:
                        result.set_result(message)
                        yield DoneEvent(stop_reason="stop", message=message)
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()

            return EventStream.from_async_generator(generate(), result)

    adapter = HoldingAdapter()
    sink: dict[str, object] = {}
    task = asyncio.create_task(run_node(runner_for(adapter), timeout=0.2 if not finish else 2, sink=sink))
    try:
        await asyncio.wait_for(started.wait(), 2)
        if finish:
            assert await asyncio.wait_for(task, 2) == ("stream answer", 5)
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(task, 2)
        assert cleaned.is_set() and owned_agents[-1].closed and adapter.close_calls == 0
        assert ("session" in sink) is finish
    finally:
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)


async def test_cleanup_failure_is_reported_not_silently_treated_as_success(
    monkeypatch: pytest.MonkeyPatch, owned_agents: list[ObservedAgent],
) -> None:
    original_close = ObservedAgent.aclose

    async def failed_close(self: ObservedAgent) -> None:
        await original_close(self)
        raise OSError("node cleanup failed")

    monkeypatch.setattr(ObservedAgent, "aclose", failed_close)
    adapter = OfflineAdapter()
    with pytest.raises(OSError, match="node cleanup failed"):
        await run_node(runner_for(adapter))
    assert owned_agents[-1].closed and adapter.close_calls == 0
