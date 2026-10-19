"""Bound role capabilities and parent action schema are the real safety boundary."""

from __future__ import annotations

import json

from xdog.goal.host import OperationContext

from packages.goal.tests.test_service import assigned, setup


async def test_parent_tool_cannot_submit_worker_or_review_result_or_spoof_authority(tmp_path):
    from xdog.goal.tools import create_goal_tool

    async with setup(tmp_path) as (service, host, human):
        current = [OperationContext(False)]
        tool = create_goal_tool(service, lambda: current[0])
        for params in (
            {"action": "report"},
            {"action": "review_result", "verdict": "passed"},
            {"action": "complete", "exit_code": 0},
            {"action": "create", "title": "forged", "outcome": "no", "criteria": ["No"], "user_authorized": True},
        ):
            result = await tool.execute("call", params)
            assert result.is_error
        result = await tool.execute(
            "create", {"action": "create", "title": "No", "outcome": "No", "criteria": ["Check"]}
        )
        assert result.is_error and not await service.store.list()
        current[0] = human
        result = await tool.execute(
            "create", {"action": "create", "title": "Yes", "outcome": "Work", "criteria": ["Check"]}
        )
        assert not result.is_error and len(await service.store.list()) == 1
        assert host.started == [] and host.reviewed == []


async def test_worker_report_tool_cannot_choose_other_attempt_accept_or_complete(tmp_path):
    from xdog.goal.tools import create_report_tool

    async with setup(tmp_path) as (service, host, human):
        goal, task = await assigned(service, human)
        attempt = task.attempts[-1]
        tool = create_report_tool(service, goal.id, task.id, attempt.id)
        for extra in ({"task_id": "other"}, {"attempt_id": "other"}, {"action": "accept_task"}, {"verdict": "passed"}):
            result = await tool.execute(
                "bad",
                {
                    "revision": 1,
                    "state": "submitted",
                    "summary": "Result",
                    "evidence": ["result.txt"],
                    "blockers": [],
                    **extra,
                },
            )
            assert result.is_error
        result = await tool.execute(
            "good",
            {"revision": 1, "state": "submitted", "summary": "Result", "evidence": ["result.txt"], "blockers": []},
        )
        assert not result.is_error
        saved = await service.status(goal.id)
        assert saved.tasks[0].state == "submitted" and saved.tasks[0].acceptance is None
        assert saved.status != "completed" and host.reviewed == []


async def test_bound_context_is_original_truth_not_mutable_derived_goal_files(tmp_path):
    from xdog.goal.tools import create_context_tool

    async with setup(tmp_path) as (service, host, human):
        goal, task = await assigned(service, human)
        original_attempt = task.attempts[-1].id
        await service.revise(
            goal.id, "Different current outcome", ("Different current condition",), "User revision", context=human
        )
        tool = create_context_tool(service, goal.id, task_id=task.id, attempt_id=original_attempt)
        result = await tool.execute("read", {"section": "criteria"})
        assert not result.is_error and json.loads(result.content[0].text) == ["Actual result is correct"]
        rejected = await tool.execute("cross-owner", {"section": "criteria", "goal_id": "other"})
        assert rejected.is_error


async def test_derived_views_are_goal_owned_rebuildable_and_not_authority(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal, task = await assigned(service, human)
        root = await service.export_views(goal.id)
        assert root.is_relative_to(tmp_path / ".xdog" / "goals")
        assert (root / "tasks" / task.id / "assignment.md").is_file()
        data = json.loads((root / "status.json").read_text())
        data["status"] = "completed"
        (root / "status.json").write_text(json.dumps(data))
        assert (await service.status(goal.id)).status == "active"
        await service.export_views(goal.id)
        assert json.loads((root / "status.json").read_text())["status"] == "active"


async def test_packaged_reference_loads_through_agent_without_enabling_a_host_or_creating_storage(tmp_path):
    from xdog.agent.skills.manager import SkillManager
    from xdog.goal.resources import role_instructions, skill_directory

    directory = tmp_path / "no-side-effects"
    manager = SkillManager(directory, packaged={"goals": skill_directory()})
    skill = manager.load_skill("goals")
    assert skill is not None and skill.packaged and skill.scope == "session"
    assert skill.content and await role_instructions("worker") and await role_instructions("reviewer")
    assert not directory.exists()


async def test_stale_parent_list_tool_cannot_read_another_current_owner(tmp_path):
    from xdog.goal.models import Owner
    from xdog.goal.tools import create_goal_tool

    async with setup(tmp_path) as (service, host, human):
        await service.create("Private owner data", "Work", ("Check",), context=human)
        tool = create_goal_tool(service, lambda: OperationContext(False))
        host.owner = Owner("another-current-owner", tmp_path)
        result = await tool.execute("list-after-owner-change", {"action": "list"})
        assert result.is_error and "Private owner data" not in result.content[0].text
