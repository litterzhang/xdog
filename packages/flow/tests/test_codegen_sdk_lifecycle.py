"""Execute generated SDK nodes offline: lifetime, inheritance and interpreter parity."""
from __future__ import annotations

import asyncio
import copy
import os
from pathlib import Path
from typing import Any, cast

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, ToolRepo
from xdog.agent.core import StreamFn
from xdog.agent.skills import SkillRepo
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
from xdog.flow.codegen import generate
from xdog.flow.executor import execute
from xdog.flow.models import OUT_NODE_ID, EdgeDef, InheritSpec, NodeDef, Port, WorkflowDef
from xdog.flow.runners import SdkRunner


class Adapter:
    def __init__(self, models: tuple[str, ...] = ("node/new",), *responses: AssistantMessage) -> None:
        self.models = models
        self.responses = list(responses)
        self.calls: list[tuple[str, Context]] = []
        self.resolved: list[str] = []
        self.close_calls = 0

    def resolve_model(self, model: str) -> Model | None:
        self.resolved.append(model)
        return Model(id=model) if model in self.models else None

    def __call__(self, model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
        assert not self.close_calls
        self.calls.append((model, context))
        return EventStream.empty(self.responses.pop(0) if self.responses else AssistantMessage(
            content=(TextContent(text="offline answer"),), usage=Usage(input=2, output=3), timestamp=0,
        ))

    async def aclose(self) -> None:
        self.close_calls += 1


class TrackedAgent(Agent):
    created: list[TrackedAgent] = []

    def __init__(self, stream_fn: StreamFn, **kwargs: Any) -> None:
        super().__init__(stream_fn, **kwargs)
        self.operations: list[str] = []
        self.created.append(self)

    def dump(self, *, recovery: bool = False) -> dict[str, Any]:
        assert not self.closed, "export must precede node cleanup"
        self.operations.append("dump")
        return super().dump(recovery=recovery)

    async def aclose(self) -> None:
        self.operations.append("close")
        await super().aclose()


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # No ambient checkpoint files, input overrides, grants or real providers.
    for name in tuple(os.environ):
        if name.startswith("FLOW_"):
            monkeypatch.delenv(name)
    TrackedAgent.created = []
    monkeypatch.setattr("xdog.agent.Agent", TrackedAgent)
    monkeypatch.setattr("xdog.agent.agent.Agent", TrackedAgent)
    monkeypatch.setattr("xdog.agent.helpers.stream_fn_from_provider", lambda provider: cast(StreamFn, provider))
    # The legacy helper probes the global provider catalog; keep that lookup
    # offline too. Restore still validates the real bound adapter below.
    monkeypatch.setattr("xdog.agent.helpers.model_supports_tool_calls", lambda model: None)

    def no_live_provider(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Generated tests must bind an offline provider explicitly")

    monkeypatch.setattr("xdog.ai.provider", no_live_provider)


def write_skill(root: Path, name: str, scope: str, body: str) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: procedure\nmetadata:\n  scope: {scope}\n---\n{body}\n", encoding="utf-8",
    )


def generated(wf: WorkflowDef, root: Path) -> dict[str, Any]:
    path = root / "generated.py"
    source = generate(wf)
    path.write_text(source, encoding="utf-8")
    namespace: dict[str, Any] = {"__name__": "_offline_generated", "__file__": str(path)}
    exec(compile(source, str(path), "exec"), namespace)  # noqa: S102
    return namespace


def workflow(node: NodeDef) -> WorkflowDef:
    return WorkflowDef(
        name="generated-sdk-parity", provider="offline", default_model="workflow/default", entry="ancestor",
        nodes=(NodeDef(id="ancestor", model="ancestor/old", prompt="research", output_ports=(Port("out"),)), node),
        edges=(EdgeDef(src="ancestor", dst=node.id, mapping=()),
               EdgeDef(src=node.id, dst=OUT_NODE_ID, mapping=(("out", "result"),))),
    )


async def snapshot(root: Path) -> dict[str, Any]:
    write_skill(root / "ancestor-skills", "old-guard", "session", "ANCESTOR_SKILL_BODY")
    write_skill(root / "ancestor-skills", "old-once", "turn", "ANCESTOR_TURN_BODY")
    source = Agent(
        Adapter(("ancestor/old",)), config=AgentConfig(model="ancestor/old", system_prompt="ANCESTOR_BASE"),
        skills=SkillRepo(builtin=False).register(directory=root / "ancestor-skills", name="shared"),
        active_skills=("old-guard", "old-once"),
    )
    source.append_message(UserMessage(content="ancestor question", timestamp=0))
    source.append_message(AssistantMessage(content=(TextContent(text="ancestor answer"),), timestamp=0))
    try:
        return source.dump()
    finally:
        await source.aclose()


def normalized(value: Any) -> Any:
    """Only new run IDs and clock timestamps differ between otherwise identical runs."""
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items() if key not in {"run_id", "timestamp"}}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


async def interpreted(
    node: NodeDef, root: Path, adapter: StreamFn, saved: dict[str, Any], *, system_prompt: str,
    registry: ToolRepo | None = None, confined: bool = False,
) -> tuple[object, int, dict[str, Any]]:
    runner = SdkRunner(
        stream_fn_factory=lambda model: adapter, tool_repo=registry or ToolRepo(builtin=False),
        web_search_fn_factory=None,
        skill_dirs=[root / "skills"], workspace=root / "runtime",
        confine_to=[root / "runtime"] if confined else None,
    )
    sink: dict[str, object] = {}
    value, tokens = await runner.run(
        node, system_prompt=system_prompt, user_prompt="child question", model=node.model or "workflow/default",
        timeout=2, inputs={}, step=1, fan_index=None, session=saved, session_sink=sink,
    )
    return value, tokens, cast(dict[str, Any], sink["session"])


@pytest.mark.parametrize("selected", [(), ("guard",), ("once",)])
@pytest.mark.parametrize("explicit_model", [False, True])
@pytest.mark.parametrize("explicit_system", [False, True])
async def test_generated_node_matches_versioned_restore_and_skill_selection(
    tmp_path: Path, selected: tuple[str, ...], explicit_model: bool, explicit_system: bool,
) -> None:
    saved = await snapshot(tmp_path)
    original = copy.deepcopy(saved)
    write_skill(tmp_path / "skills", "guard", "session", "CHILD_SESSION_BODY")
    write_skill(tmp_path / "skills", "once", "turn", "CHILD_TURN_BODY")
    node = NodeDef(
        id="child", model="node/new" if explicit_model else None,
        system_prompt="NODE_BASE" if explicit_system else "", prompt="child question", skills=selected,
        inherit=InheritSpec(from_node="ancestor"), output_ports=(Port("out"),),
    )
    models = ("node/new",) if explicit_model else ("workflow/default", "ancestor/old")
    compiled_adapter, sdk_adapter = Adapter(models), Adapter(models)
    module = generated(workflow(node), tmp_path)
    module["_SESSIONS"]["ancestor"] = saved
    outputs, tokens = await module["node_child"](compiled_adapter, module["RuntimeContext"](1, "child", "test"))
    value, sdk_tokens, sdk_snapshot = await interpreted(
        node, tmp_path, sdk_adapter, saved, system_prompt=node.system_prompt,
    )
    assert outputs == {"out": value} == {"out": "offline answer"} and tokens == sdk_tokens == 5
    assert normalized(module["_SESSIONS"]["child"]) == normalized(sdk_snapshot)
    assert saved == original and module["_SESSIONS"]["ancestor"] is saved
    for adapter in (compiled_adapter, sdk_adapter):
        model, context = adapter.calls[0]
        assert model == ("node/new" if explicit_model else "ancestor/old")
        if explicit_model:
            assert "ancestor/old" not in adapter.resolved, "explicit override must precede validation"
        assert "ancestor question" in str(context.messages) and "ancestor answer" in str(context.messages)
        assert "ANCESTOR_SKILL_BODY" not in str(context.system_prompt)
        assert "ANCESTOR_TURN_BODY" not in str(context.messages)
        assert str(context.messages).count("CHILD_TURN_BODY") == (1 if "once" in selected else 0)
        assert str(context.system_prompt).count("CHILD_SESSION_BODY") == (1 if "guard" in selected else 0)
        assert ("ANCESTOR_BASE" in str(context.system_prompt)) is not explicit_system
        assert ("NODE_BASE" in str(context.system_prompt)) is explicit_system
        assert not adapter.close_calls
    assert len(TrackedAgent.created) == 2 and all(agent.closed for agent in TrackedAgent.created)
    assert all(agent.operations[-2:] == ["dump", "close"] for agent in TrackedAgent.created)
    assert all("CHILD_TURN_BODY" not in str(agent.messages) for agent in TrackedAgent.created)


async def test_explicit_system_template_that_renders_empty_still_overrides(tmp_path: Path) -> None:
    saved = await snapshot(tmp_path)
    node = NodeDef(id="child", model="node/new", system_prompt="{{base}}", prompt="child question",
                   input_ports=(Port("base"),), output_ports=(Port("out"),), inherit=InheritSpec("ancestor"))
    module = generated(workflow(node), tmp_path)
    module["_SESSIONS"]["ancestor"] = saved
    adapter = Adapter()
    await module["node_child"](adapter, module["RuntimeContext"](1, "child", "test"), base="")
    _, _, sdk_saved = await interpreted(node, tmp_path, Adapter(), saved, system_prompt="")
    assert "ANCESTOR_BASE" not in str(adapter.calls[0][1].system_prompt)
    assert normalized(module["_SESSIONS"]["child"]) == normalized(sdk_saved)
    assert all(agent.closed for agent in TrackedAgent.created)


@pytest.mark.parametrize("invalid", ["model", "version", "interrupted"])
async def test_generated_restore_rejection_closes_without_request_or_session_publication(
    tmp_path: Path, invalid: str,
) -> None:
    saved = await snapshot(tmp_path)
    if invalid == "version":
        saved["version"] = 999
    elif invalid == "interrupted":
        saved["interrupted"] = True
    original = copy.deepcopy(saved)
    node = NodeDef(id="child", model=None if invalid == "model" else "node/new", prompt="child question",
                   output_ports=(Port("out"),), inherit=InheritSpec("ancestor"))
    module = generated(workflow(node), tmp_path)
    module["_SESSIONS"]["ancestor"] = saved
    adapter = Adapter()
    with pytest.raises(ValueError):
        await module["node_child"](adapter, module["RuntimeContext"](1, "child", "test"))
    assert not adapter.calls and not adapter.close_calls and "child" not in module["_SESSIONS"]
    assert saved == original and TrackedAgent.created[-1].closed


@pytest.mark.parametrize(
    "schema,payload,invalid",
    [
        ({"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}},
         {"answer": "ok"}, {"answer": 1}),
        ({"type": "array", "items": {"type": "integer"}}, [1, 2], ["bad"]),
        ({"type": "integer"}, 3, "bad"),
    ],
    ids=["object", "array", "scalar"],
)
async def test_generated_structured_tool_sink_context_and_tokens_match_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: dict[str, Any], payload: Any, invalid: Any,
) -> None:
    monkeypatch.setenv("FLOW_CONFINED", "1")
    saved = await snapshot(tmp_path)
    node = NodeDef(id="child", model="node/new", system_prompt="NODE_BASE", prompt="child question", tools=("probe",),
                   output_ports=(Port("out", schema=schema),), inherit=InheritSpec("ancestor"))
    contexts: list[dict[str, Any]] = []

    async def probe(call_id: str, args: Any, cancel: Any, on_update: Any, **kwargs: Any) -> AgentToolResult:
        contexts.append(kwargs["ctx"])
        return AgentToolResult(content=(TextContent(text="probe result"),))

    tool = AgentTool(name="probe", parameters={"type": "object"}, execute=probe)
    responses = (
        AssistantMessage(content=(ToolCall(id="probe", name="probe", arguments={}),), stop_reason="toolUse",
                         usage=Usage(input=4, output=2), timestamp=0),
        AssistantMessage(content=(ToolCall(id="invalid", name="submit_result", arguments={"result": invalid}),),
                         stop_reason="toolUse", timestamp=0),
        AssistantMessage(content=(ToolCall(id="submit", name="submit_result", arguments={"result": payload}),),
                         stop_reason="toolUse", usage=Usage(input=2, output=3, total_tokens=11), timestamp=0),
        AssistantMessage(content=(TextContent(text="not the output"),), usage=Usage(input=1, output=1), timestamp=0),
    )
    generated_adapter, sdk_adapter = Adapter(("node/new",), *responses), Adapter(("node/new",), *responses)
    module = generated(workflow(node), tmp_path)
    module["_SESSIONS"]["ancestor"] = saved
    module["_REGISTRY"].register(tool)
    output, tokens = await module["node_child"](generated_adapter, module["RuntimeContext"](1, "child", "test"))
    registry = ToolRepo(builtin=False)
    registry.register(tool)
    value, sdk_tokens, sdk_saved = await interpreted(
        node, tmp_path, sdk_adapter, saved, system_prompt="NODE_BASE", registry=registry, confined=True,
    )
    assert output == {"out": value} == {"out": payload} and tokens == sdk_tokens == 19
    assert normalized(module["_SESSIONS"]["child"]) == normalized(sdk_saved)
    assert len(contexts) == 2
    for ctx in contexts:
        assert ctx["fs_workspace"] == str(tmp_path / "runtime")
        assert ctx["fs_confine_to"] == [str(tmp_path / "runtime")]
        assert "flow_output_schema" not in ctx and "flow_result_sink" not in ctx
        assert ctx["result_schema"] == schema
        assert ctx["result_sink"] == {"result": payload}
    for adapter in (generated_adapter, sdk_adapter):
        assert all({tool.name for tool in context.tools or ()} == {"probe", "submit_result"}
                   for _, context in adapter.calls)
        assert "submit_result" in str(adapter.calls[0][1].system_prompt)
        rejected = next(message for message in adapter.calls[-1][1].messages
                        if isinstance(message, ToolResultMessage) and message.tool_call_id == "invalid")
        assert any(isinstance(part, TextContent) and part.text.startswith("Error:") for part in rejected.content)
        assert not adapter.close_calls
    assert all(agent.closed for agent in TrackedAgent.created)


@pytest.mark.parametrize("failure", ["missing_result", "prompt", "dump"])
async def test_generated_errors_close_without_bypassing_real_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    node = NodeDef(id="child", model="node/new", prompt="child question",
                   output_ports=(Port("out", schema={"type": "object"}),))
    module = generated(workflow(node), tmp_path)
    adapter = Adapter()
    if failure == "prompt":
        async def fail_prompt(self: Agent, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("prompt failed")
        monkeypatch.setattr(TrackedAgent, "prompt", fail_prompt)
    elif failure == "dump":
        def fail_dump(self: Agent, **kwargs: Any) -> Any:
            raise RuntimeError("dump failed")
        monkeypatch.setattr(TrackedAgent, "dump", fail_dump)
    expected = module["WorkflowExecutionError"] if failure == "missing_result" else RuntimeError
    with pytest.raises(expected):
        await module["node_child"](adapter, module["RuntimeContext"](1, "child", "test"))
    assert TrackedAgent.created[-1].closed and not adapter.close_calls
    assert ("child" in module["_SESSIONS"]) is (failure == "missing_result")


@pytest.mark.parametrize("cancel_via", ["timeout", "repeated_cancel"])
async def test_generated_helper_settles_parallel_tools_on_timeout_or_repeated_cancel(
    tmp_path: Path, cancel_via: str,
) -> None:
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    entered: list[str] = []
    settled: list[str] = []
    signals: list[asyncio.Event] = []

    async def hold(call_id: str, args: Any, cancel: asyncio.Event, on_update: Any, **kwargs: Any) -> AgentToolResult:
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
        return AgentToolResult()

    adapter = Adapter(("node/new",), AssistantMessage(
        content=tuple(ToolCall(id=str(i), name="hold", arguments={}) for i in range(2)), stop_reason="toolUse",
    ))
    module = generated(workflow(NodeDef(id="child", model="node/new", output_ports=(Port("out"),))), tmp_path)
    tool = AgentTool(name="hold", parameters={"type": "object"}, execute=hold)
    task = asyncio.create_task(module["_run_agent"](
        adapter, "node/new", "", "hold", (tool,), node_id="child", timeout=0.2 if cancel_via == "timeout" else 5,
    ))
    try:
        await asyncio.wait_for(started.wait(), 2)
        if cancel_via == "repeated_cancel":
            task.cancel()
        await asyncio.wait_for(cleaning.wait(), 2)
        if cancel_via == "repeated_cancel":
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
        assert not task.done() and not TrackedAgent.created[-1].closed
        assert not settled and not adapter.close_calls and "child" not in module["_SESSIONS"]
        release.set()
        with pytest.raises(TimeoutError if cancel_via == "timeout" else asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert sorted(settled) == ["0", "1"] and all(signal.is_set() for signal in signals)
        assert TrackedAgent.created[-1].closed and TrackedAgent.created[-1].operations == ["close"]
        assert not adapter.close_calls and "child" not in module["_SESSIONS"]
        # A shared adapter is still usable by the next generated node.
        assert await module["_run_agent"](adapter, "node/new", "", "next", node_id="next") == ("offline answer", 5)
        assert TrackedAgent.created[-1].closed and not adapter.close_calls
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)


async def test_generated_main_real_snapshot_handoff_and_totals_match_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_skill(tmp_path / "skills", "guard", "session", "SESSION_MARKER")
    write_skill(tmp_path / "skills", "once", "turn", "TURN_MARKER")
    wf = WorkflowDef(
        name="actual-main", provider="offline", entry="first", default_model="node/new",
        nodes=(NodeDef(id="first", prompt="research", system_prompt="FIRST_BASE", skills=("guard", "once"),
                       output_ports=(Port("out"),)),
               NodeDef(id="second", prompt="critique", inherit=InheritSpec("first"), output_ports=(Port("out"),))),
        edges=(EdgeDef(src="first", dst="second", mapping=()),
               EdgeDef(src="second", dst=OUT_NODE_ID, mapping=(("out", "result"),))),
    )
    adapter, sdk_adapter = Adapter(), Adapter()
    monkeypatch.setattr("xdog.ai.provider", lambda name: adapter)
    module = generated(wf, tmp_path)
    await module["main"]()
    result = await execute(wf, base_dir=tmp_path, stream_fn_factory=lambda model: sdk_adapter)
    assert module["_OUTPUT"] == result.runtime["out"] == {"result": "offline answer"}
    assert module["_TOKENS_USED"] == result.runtime["tokens_used"] == 10
    assert set(module["_SESSIONS"]) == {"first", "second"}
    for active in (adapter, sdk_adapter):
        assert len(active.calls) == 2
        first, second = active.calls[0][1], active.calls[1][1]
        assert "research" in str(second.messages) and "critique" in str(second.messages)
        assert str(first.messages).count("TURN_MARKER") == 1 and "TURN_MARKER" not in str(second.messages)
        assert "SESSION_MARKER" in str(first.system_prompt) and "SESSION_MARKER" not in str(second.system_prompt)
        assert not active.close_calls
    assert len(TrackedAgent.created) == 4 and all(agent.closed for agent in TrackedAgent.created)
    assert all("TURN_MARKER" not in str(agent.messages) for agent in TrackedAgent.created)


def test_generated_sdk_keeps_exception_notice_and_no_flow_runtime_import(tmp_path: Path) -> None:
    import ast

    wf = workflow(NodeDef(id="child", model="node/new", output_ports=(Port("out"),)))
    source = generate(wf)
    assert "flow Generated Output Exception" in source and "LICENSE-EXCEPTION.md" in source
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(name == "xdog.flow" or name.startswith("xdog.flow.") for name in imports)
    # Explicit override intent must survive into node wrappers, not be guessed
    # from the resolved model (which may only be a workflow default).
    module = generated(wf, tmp_path)
    assert module["_run_agent"].__defaults__[-1] == 120.0


@pytest.mark.parametrize("finish", [False, True])
async def test_generated_main_closes_actual_request_stream_on_success_or_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: bool,
) -> None:
    from collections.abc import AsyncIterator

    from xdog.ai.types import AssistantMessageEvent, DoneEvent, StartEvent

    started, cleaned = asyncio.Event(), asyncio.Event()
    message = AssistantMessage(content=(TextContent(text="stream answer"),), usage=Usage(input=2, output=3))

    class HoldingAdapter(Adapter):
        def __call__(self, model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
            self.calls.append((model, context))
            result: asyncio.Future[AssistantMessage] = asyncio.get_running_loop().create_future()

            async def stream() -> AsyncIterator[AssistantMessageEvent]:
                try:
                    started.set()
                    yield StartEvent(partial=message)
                    if finish:
                        result.set_result(message)
                        yield DoneEvent(stop_reason="stop", message=message)
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()

            return EventStream.from_async_generator(stream(), result)

    adapter = HoldingAdapter()
    monkeypatch.setattr("xdog.ai.provider", lambda name: adapter)
    wf = WorkflowDef(name="stream-main", provider="offline", entry="child", default_model="node/new",
                     nodes=(NodeDef(id="child", prompt="stream", output_ports=(Port("out"),)),),
                     edges=(EdgeDef(src="child", dst=OUT_NODE_ID, mapping=(("out", "result"),)),))
    module = generated(wf, tmp_path)
    task = asyncio.create_task(module["main"]())
    try:
        await asyncio.wait_for(started.wait(), 2)
        if finish:
            await asyncio.wait_for(task, 2)
            assert module["_OUTPUT"] == {"result": "stream answer"}
            assert module["_TOKENS_USED"] == 5
        else:
            # The actual generated driver propagates cancellation to its SDK
            # node; it must not return on the outer deadline with a live Agent.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(task, 0.2)
        assert cleaned.is_set() and TrackedAgent.created[-1].closed and not adapter.close_calls
        assert ("child" in module["_SESSIONS"]) is finish
    finally:
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)


async def test_generated_legacy_model_override_matches_sdk_without_mutating_snapshot(tmp_path: Path) -> None:
    saved = {"model": "removed/old", "system_prompt": "LEGACY_BASE"}
    original = dict(saved)
    node = NodeDef(id="child", model="node/new", prompt="child question", inherit=InheritSpec("ancestor"),
                   output_ports=(Port("out"),))
    module = generated(workflow(node), tmp_path)
    module["_SESSIONS"]["ancestor"] = saved
    adapter = Adapter()
    outputs, tokens = await module["node_child"](adapter, module["RuntimeContext"](1, "child", "test"))
    value, sdk_tokens, sdk_saved = await interpreted(node, tmp_path, Adapter(), saved, system_prompt="")
    assert outputs == {"out": value} and tokens == sdk_tokens == 5
    assert saved == original and "removed/old" not in adapter.resolved
    compiled_saved = module["_SESSIONS"]["child"]
    # Legacy data carries no session identity; each fresh Agent correctly owns
    # a new one. Versioned parity tests above still compare that identity.
    assert compiled_saved["session_id"] and sdk_saved["session_id"]
    assert compiled_saved["session_id"] != sdk_saved["session_id"]
    assert normalized({k: v for k, v in compiled_saved.items() if k != "session_id"}) == normalized(
        {k: v for k, v in sdk_saved.items() if k != "session_id"}
    )
    assert all(agent.closed for agent in TrackedAgent.created) and not adapter.close_calls


async def test_generated_close_failure_is_not_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_close = TrackedAgent.aclose

    async def failed_close(self: TrackedAgent) -> None:
        await original_close(self)
        raise OSError("cleanup failure")

    monkeypatch.setattr(TrackedAgent, "aclose", failed_close)
    module = generated(workflow(NodeDef(id="child", model="node/new", output_ports=(Port("out"),))), tmp_path)
    adapter = Adapter()
    with pytest.raises(OSError, match="cleanup failure"):
        await module["node_child"](adapter, module["RuntimeContext"](1, "child", "test"))
    assert TrackedAgent.created[-1].closed and not adapter.close_calls
