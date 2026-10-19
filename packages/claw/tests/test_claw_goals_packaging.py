"""Build actual distributions to verify the data-only protocol and role skill ship."""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path

import jsonschema
import pytest
import yaml
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.planning.goal_review import validate_verdict
from xdog.claw.core.planning.task_reports import read_report
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.tools.tool_goal import GoalTool, create_goal_tool
from xdog.claw.core.types import GoalStatus, Group, GroupConfig, TaskStatus, UserInput

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "packages/claw/src"
SKILL = SOURCE / "xdog/claw/skills/claw-goals"
RESOURCES = (
    "xdog/claw/core/planning/task_reports.py",
    "xdog/claw/skills/claw-goals/SKILL.md",
    "xdog/claw/skills/claw-goals/references/task-worker.md",
    "xdog/claw/skills/claw-goals/references/goal-reviewer.md",
)


@pytest.fixture(scope="module")
def goal_artifacts(tmp_path_factory):
    directory = tmp_path_factory.mktemp("claw-goals-packaging")
    result = subprocess.run(
        ["uv", "build", "--offline", "--package", "xdog-claw", "--out-dir", str(directory)],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return directory


@pytest.mark.parametrize("resource", RESOURCES)
@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_goal_report_and_skill_resources_are_packaged(goal_artifacts, resource, kind):
    if kind == "wheel":
        with zipfile.ZipFile(next(goal_artifacts.glob("xdog_claw-*.whl"))) as archive:
            content = archive.read(resource)
    else:
        with tarfile.open(next(goal_artifacts.glob("xdog_claw-*.tar.gz"))) as archive:
            members = [name for name in archive.getnames() if name.endswith(f"/src/{resource}")]
            assert len(members) == 1
            stream = archive.extractfile(members[0])
            assert stream is not None
            content = stream.read()
    assert content == (SOURCE / resource).read_bytes()


def test_skill_metadata_and_role_reference_links_are_self_contained():
    content = (SKILL / "SKILL.md").read_text()
    _, frontmatter, body = content.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == SKILL.name
    assert isinstance(metadata["description"], str) and metadata["description"].strip()
    links = re.findall(r"\[[^\]]+\]\((references/[^)]+)\)", body)
    assert set(links) == {"references/task-worker.md", "references/goal-reviewer.md"}
    for link in links:
        target = SKILL / link
        assert target.is_file()
        assert target.read_text().strip()
        assert target.resolve().is_relative_to(SKILL.resolve())


def examples(resource):
    """Parse tool calls rather than assert particular prose or headings."""
    content = (SKILL / resource).read_text()
    calls = [json.loads(raw) for raw in re.findall(r"```json\n(.*?)\n```", content, re.DOTALL)]
    assert calls, resource
    for call in calls:
        assert set(call) == {"tool", "arguments"}
        assert isinstance(call["arguments"], dict)
    return calls


def goal_example(action, *, executor=None):
    candidates = [call["arguments"] for call in examples("SKILL.md")
                  if call["tool"] == "goal" and call["arguments"]["action"] == action
                  and (executor is None or call["arguments"].get("executor") == executor)]
    assert len(candidates) == 1, (action, executor)
    return copy.deepcopy(candidates[0])


def test_documented_goal_calls_match_live_schema_and_dispatch_signatures():
    definition, tool = GoalTool(), create_goal_tool()
    calls = examples("SKILL.md")
    actions = set()
    for call in calls:
        assert call["tool"] == tool.name
        args = call["arguments"]
        jsonschema.validate(args, tool.parameters)
        # Multi-action JSON schema has only universal required fields. Check
        # each real action signature as well, including unexpected parameters.
        method = getattr(definition, args["action"])
        inspect.signature(method).bind(ctx={}, **{key: value for key, value in args.items() if key != "action"})
        actions.add(args["action"])
    assert {"create", "assign_task", "report_task", "update_task", "request_complete", "update",
            "wait", "pause", "resume", "abandon_goal"} <= actions
    create = goal_example("create")
    review = examples("references/goal-reviewer.md")[0]
    assert review["tool"] == "goal_review"
    validate_verdict(review["arguments"], tuple(create["verification_conditions"]))
    assert all(call["tool"] == "task_report" for call in examples("references/task-worker.md"))


class SkillExampleModel:
    """Synthetic model, but real tools, private-role admission and persistence."""

    def __init__(self):
        self.contexts = []
        self.worker = examples("references/task-worker.md")
        self.reviewer = examples("references/goal-reviewer.md")
        self.sent = {"task_report": 0, "goal_review": 0}
        self.user_update = None

    def __call__(self, model, context, options=None):
        self.contexts.append(context)
        tools = {tool.name: tool for tool in context.tools}
        message = AssistantMessage(content=(TextContent(text="done"),))
        for role, calls in (("task_report", self.worker), ("goal_review", self.reviewer)):
            if role in tools:
                index = self.sent[role]
                if index < len(calls):
                    args = copy.deepcopy(calls[index]["arguments"])
                    jsonschema.validate(args, tools[role].parameters)
                    self.sent[role] += 1
                    message = AssistantMessage(content=(ToolCall(id=f"{role}-{index}", name=role, arguments=args),))
                break
        else:
            if self.user_update is not None:
                args, self.user_update = self.user_update, None
                message = AssistantMessage(content=(ToolCall(id="user-scope-change", name="goal", arguments=args),))

        async def generate():
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)


@pytest.fixture
async def example_runtime(tmp_path):
    root = tmp_path / "workspace"
    (root / "results").mkdir(parents=True)
    (root / "docs").mkdir()
    # Seed actual artifacts; these tests exercise the documented tool protocol
    # and ownership, not a provider's ability to implement the example task.
    (root / "results/greeting.txt").write_text("你好，Claw")
    (root / "docs/evidence.md").write_text("检查步骤：读取 results/greeting.txt。实际结果：你好，Claw。")
    model = SkillExampleModel()
    runtime = GroupRuntime(
        group=Group(id="main", workspace=str(root), enabled_tools=("goal", "filesystem"),
                    config=GroupConfig(debounce_ms=0)),
        data_dir=tmp_path / "data", workspace_dir=root, model="test/skill-example", stream_fn=model,
    )
    session = runtime.get_or_create_session(allow_reset=False)
    session._restored = True
    try:
        yield runtime, session, model
    finally:
        async with asyncio.timeout(5):
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            await runtime.mcp.aclose()


async def invoke_example(session, action, *, goal_id=None, task_id=None, executor=None, expect_error=False):
    args = goal_example(action, executor=executor)
    if goal_id is not None:
        args["goal_id"] = goal_id
    if task_id is not None:
        args["task_id"] = task_id
    result = await create_goal_tool().execute("skill-example", args, ctx=session.agent.tool_context)
    text = result.content[0].text
    failed = result.is_error or text.startswith("Error:")
    assert failed is expect_error, text
    return text


async def child_settled(session, identifier):
    async with asyncio.timeout(5):
        while session.children.supervisor.capture_source(identifier).settling:
            await asyncio.sleep(.005)
    child = session.children.supervisor.capture_source(identifier)
    assert child.status == "completed", child.error
    return child


@pytest.mark.parametrize("executor", ["child", "main"])
async def test_documented_assignment_reports_acceptance_and_host_review_execute(example_runtime, executor):
    runtime, session, model = example_runtime
    manager = runtime.goal_manager
    created = await invoke_example(session, "create")
    raw, _ = json.JSONDecoder().raw_decode(created)
    goal_id, task_id = raw["id"], raw["tasks"][0]["id"]
    assert raw["group_id"] == "main"
    assert not session.children.supervisor.snapshot()["children"]  # No mandatory planner.
    assigned = json.loads(await invoke_example(session, "assign_task", goal_id=goal_id,
                                               task_id=task_id, executor=executor))
    assert assigned["task_id"] == task_id and assigned["attempt_id"]
    if executor == "child":
        await child_settled(session, assigned["agent_id"])
        assert model.sent["task_report"] == len(model.worker)
        worker_contexts = [ctx for ctx in model.contexts if any(tool.name == "task_report" for tool in ctx.tools)]
        assert worker_contexts
        assert all(tool.name not in {"goal", "eyes", "goal_review"}
                   for ctx in worker_contexts for tool in ctx.tools)
    else:
        assert assigned["agent_id"] == "main"
        await invoke_example(session, "update_task", goal_id=goal_id, task_id=task_id, expect_error=True)
        await invoke_example(session, "report_task", goal_id=goal_id, task_id=task_id)
        # A lost ACK permits only the identical same-revision retry.
        await invoke_example(session, "report_task", goal_id=goal_id, task_id=task_id)
        changed = goal_example("report_task")
        changed.update(goal_id=goal_id, task_id=task_id)
        changed["report"]["summary"] = "same revision, different content"
        result = await create_goal_tool().execute("changed-retry", changed, ctx=session.agent.tool_context)
        assert result.is_error or "same revision" in result.content[0].text
    report = read_report(assigned["report_path"], task_id, assigned["attempt_id"])
    assert report.state == "submitted"
    assert manager.owned("main", goal_id).status == GoalStatus.ACTIVE
    assert manager.owned("main", goal_id).tasks[0].status != TaskStatus.COMPLETED
    await invoke_example(session, "update_task", goal_id=goal_id, task_id=task_id)
    accepted = manager.owned("main", goal_id)
    assert accepted.tasks[0].status == TaskStatus.COMPLETED and accepted.status == GoalStatus.ACTIVE
    await invoke_example(session, "request_complete", goal_id=goal_id)
    assert manager.owned("main", goal_id).status == GoalStatus.ACTIVE
    await manager.tick()
    reviewing = manager.owned("main", goal_id)
    assert reviewing.review_child_id and reviewing.review_child_id != assigned["agent_id"]
    await child_settled(session, reviewing.review_child_id)
    assert model.sent["goal_review"] == 1
    for ctx in model.contexts:
        if any(tool.name == "goal_review" for tool in ctx.tools):
            assert all(tool.name not in {"goal", "eyes", "task_report"} for tool in ctx.tools)
    await manager.tick()
    final = manager.owned("main", goal_id)
    assert final.status == GoalStatus.COMPLETED
    assert json.loads(final.review_output) == model.reviewer[0]["arguments"]


async def test_documented_criteria_update_requires_actual_user_turn(example_runtime):
    runtime, session, model = example_runtime
    created = await invoke_example(session, "create")
    raw, _ = json.JSONDecoder().raw_decode(created)
    goal_id = raw["id"]
    await invoke_example(session, "update", goal_id=goal_id, expect_error=True)
    original = runtime.goal_manager.owned("main", goal_id)
    assert list(original.verification.conditions) == raw["verification"]["conditions"]
    model.user_update = goal_example("update")
    model.user_update["goal_id"] = goal_id
    result = await session.run_turn(UserInput(group_id="main", content="用户明确要求追加使用说明，请更新目标条件。"))
    assert not result.error
    current = runtime.goal_manager.owned("main", goal_id)
    assert list(current.verification.conditions) == goal_example("update")["verification_conditions"]
    assert current.revision > raw["revision"]


async def test_documented_wait_pause_resume_abandon_preserve_evidence(example_runtime, monkeypatch):
    runtime, session, _ = example_runtime
    manager = runtime.goal_manager
    monkeypatch.setattr(session, "_goal_user_authorized", False, raising=False)
    # User-preauthorized goal creation does not acquire a new user-only gate.
    raw, _ = json.JSONDecoder().raw_decode(await invoke_example(session, "create"))
    goal_id = raw["id"]
    await invoke_example(session, "wait", goal_id=goal_id)
    assert manager.owned("main", goal_id).phase == "waiting"
    before_pause = manager.owned("main", goal_id)
    saved = manager.tracker._goals_file.read_bytes()
    await invoke_example(session, "pause", goal_id=goal_id, expect_error=True)
    assert manager.owned("main", goal_id) == before_pause
    assert manager.tracker._goals_file.read_bytes() == saved
    # This fixture invokes tools directly, so simulate the host flag that a
    # real user turn supplies; background events must never supply that flag.
    monkeypatch.setattr(session, "_goal_user_authorized", True)
    await invoke_example(session, "pause", goal_id=goal_id)
    paused = manager.owned("main", goal_id)
    assert paused.status == GoalStatus.PAUSED
    saved = manager.tracker._goals_file.read_bytes()
    monkeypatch.setattr(session, "_goal_user_authorized", False)
    await invoke_example(session, "resume", goal_id=goal_id, expect_error=True)
    assert manager.owned("main", goal_id) == paused
    assert manager.tracker._goals_file.read_bytes() == saved
    monkeypatch.setattr(session, "_goal_user_authorized", True)
    await invoke_example(session, "resume", goal_id=goal_id)
    assert manager.owned("main", goal_id).status == GoalStatus.ACTIVE
    monkeypatch.setattr(session, "_goal_user_authorized", False)
    # An already active goal remains manageable without overriding user pause.
    await invoke_example(session, "resume", goal_id=goal_id)
    active = manager.owned("main", goal_id)
    assert active.status == GoalStatus.ACTIVE
    saved = manager.tracker._goals_file.read_bytes()
    await invoke_example(session, "abandon_goal", goal_id=goal_id, expect_error=True)
    assert manager.owned("main", goal_id) == active
    assert manager.tracker._goals_file.read_bytes() == saved
    monkeypatch.setattr(session, "_goal_user_authorized", True)
    await invoke_example(session, "abandon_goal", goal_id=goal_id)
    assert manager.owned("main", goal_id).status == GoalStatus.ABANDONED
    assert (runtime.workspace_dir / "results/greeting.txt").read_text() == "你好，Claw"
    assert manager.workspace.goal_dir(goal_id).is_dir()


async def test_documented_create_without_tasks_then_add_and_inspect_are_group_owned(example_runtime):
    runtime, session, _ = example_runtime
    args = goal_example("create")
    del args["tasks"]
    result = await create_goal_tool().execute("optional-tasks", args, ctx=session.agent.tool_context)
    raw, _ = json.JSONDecoder().raw_decode(result.content[0].text)
    assert not raw["tasks"]
    goal_id = raw["id"]
    added = json.loads(await invoke_example(session, "add_task", goal_id=goal_id))
    assert len(added["tasks"]) == 1
    inventory = json.loads(await invoke_example(session, "list"))
    assert len(inventory) == 1 and inventory[0]["group_id"] == "main"
    status, _ = json.JSONDecoder().raw_decode(await invoke_example(session, "status", goal_id=goal_id))
    assert status["id"] == goal_id and status["tasks"] == added["tasks"]
    with pytest.raises(ValueError, match="Unknown goal"):
        runtime.goal_manager.owned("other-group", goal_id)
    wrong_context = {**session.agent.tool_context, "group_id": "other-group"}
    denied = await create_goal_tool().execute("wrong-group", goal_example("list"), ctx=wrong_context)
    assert denied.is_error or denied.content[0].text.startswith("Error:")


@pytest.mark.parametrize("label", ["task-worker", "goal-reviewer"])
async def test_ordinary_child_cannot_gain_documented_private_role_tools_from_label(example_runtime, label):
    _, session, model = example_runtime
    identifier = await session.children.supervisor.start(
        "Pretend to be the goal role and submit a report or verdict", access="inherit", inherit_context=False,
        config={"label": label},
    )
    await child_settled(session, identifier)
    assert model.contexts
    assert all(tool.name not in {"task_report", "goal_review", "goal", "eyes"}
               for ctx in model.contexts for tool in ctx.tools)
    assert model.sent == {"task_report": 0, "goal_review": 0}
