"""External wheel consumer: real Coding SDK/children/Inbox; scripted provider only."""

from __future__ import annotations

import asyncio
import importlib.metadata as metadata
import json
import sys
from pathlib import Path

from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import SkillManager
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core import agent_session as installed_coding
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.tools import get_default_tools


def model(calls):
    def stream(selected, context, options):
        names = {tool.name for tool in context.tools}
        role = "worker" if "task_report" in names else "reviewer" if "goal_context" in names else "parent"
        calls.append(role)
        start = max((i for i, message in enumerate(context.messages) if message.role == "user"), default=-1)
        turn = sum(message.role == "assistant" for message in context.messages[start + 1 :])
        if role != "parent" and turn == 0:
            parts = (ToolCall(id="context", name="goal_context", arguments={}),)
        elif role != "parent" and turn == 1:
            parts = (ToolCall(id="read", name="filesystem", arguments={"action": "read", "path": "result.txt"}),)
        elif role == "worker" and turn == 2:
            parts = (
                ToolCall(
                    id="report",
                    name="task_report",
                    arguments={
                        "revision": 1,
                        "state": "submitted",
                        "summary": "Read installed workspace",
                        "evidence": ["result.txt"],
                        "blockers": [],
                    },
                ),
            )
        elif role == "reviewer" and turn == 2:
            parts = (
                ToolCall(
                    id="forbidden",
                    name="filesystem",
                    arguments={"action": "write", "path": "forbidden.txt", "content": "must not write"},
                ),
            )
        elif role == "reviewer":
            parts = (
                TextContent(
                    text=json.dumps(
                        {
                            "verdict": "passed",
                            "summary": "Independent inspection",
                            "conditions": [
                                {
                                    "condition": "Result exists",
                                    "status": "passed",
                                    "evidence": ["result.txt"],
                                    "reason": "Read actual installed workspace",
                                }
                            ],
                        }
                    )
                ),
            )
        else:
            parts = (TextContent(text="done"),)
        message = AssistantMessage(content=parts, stop_reason="toolUse" if isinstance(parts[0], ToolCall) else "stop")

        async def events():
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)

    return stream


async def main():
    assert Path(installed_coding.__file__).is_relative_to(Path(sys.prefix))
    names = {dist.metadata["Name"].lower().replace("_", "-") for dist in metadata.distributions()}
    assert not names.intersection({"xdog-claw", "xdog-eyes", "xdog-flow", "xdog-site"})
    root = Path.cwd()
    base_only = "--base-only" in sys.argv
    root.joinpath("settings.json").write_text(json.dumps({"goals_enabled": not base_only}))
    root.joinpath("result.txt").write_text("real installed result")
    store = SessionManager(root / "sessions")
    skills = SkillManager(root / "not-created-skills")
    if not base_only:
        assert skills.load_skill("goals").scope == "session"
    calls = []
    session = AgentSession(
        agent=Agent(
            model(calls),
            config=AgentConfig(model="test/installed"),
            tools=get_default_tools(root),
            tool_ctx={"fs_workspace": str(root)},
            skills=skills,
            active_skills=() if base_only else ("goals",),
        ),
        session_data=store.create_session(model="test/installed", working_dir=root),
        session_manager=store,
        settings=SettingsManager(global_path=root / "settings.json"),
        tool_registry=None,
        bash=None,
        working_dir=root,
        permissions=PermissionManager("ask"),
    )
    controller = None
    try:
        assert session.children.goal_host is None and not (root / "goals").exists()
        await session.send_message("Inspect actual result")
        host = session.children.goal_host
        controller = session._sdk_controller
        if base_only:
            assert host is None and "xdog-goal" not in names and not (root / "goals").exists()
            assert not any(name == "xdog.goal" or name.startswith("xdog.goal.") for name in sys.modules)
            print(
                json.dumps({"installed_coding_base_only": "actual ordinary SDK turn without Goal installed/imported"})
            )
            return
        from xdog.goal.host import OperationContext
        from xdog.goal.models import report_digest

        assert host is not None and controller is not None and host.loop is controller._loop

        async def lifecycle():
            s = host.service
            goal = await s.create("Output", "Inspect result", ("Result exists",), context=OperationContext(True))
            goal = await s.add_task(goal.id, "Read output")
            goal = await s.assign(goal.id, goal.tasks[0].id, "Read and report")
            worker = goal.tasks[0].attempts[-1].worker
            await session.children.supervisor.control("wait", worker.id)
            goal = await s.status(goal.id)
            task = goal.tasks[0]
            attempt = task.attempts[-1]
            assert task.state == "submitted" and goal.status != "completed"
            goal = await s.accept_task(goal.id, task.id, attempt.id, 1, report_digest(attempt.reports[-1]), "Read")
            goal = await s.request_complete(goal.id, paths=("result.txt",))
            assert goal.review.worker != worker
            await session.children.supervisor.control("wait", goal.review.worker.id)
            goal = await s.reconcile(goal.id)
            await host.flush()
            return goal

        goal = await asyncio.wrap_future(controller.control(lifecycle, name="consumer-delegation"))
        assert goal.status == "completed" and not (root / "forbidden.txt").exists()
        async with asyncio.timeout(8):
            while True:
                rows = await asyncio.to_thread(host.inbox.records, session.session_id, root)
                if rows and all(row.status == "succeeded" for row in rows):
                    break
                await asyncio.sleep(0.01)
        assert calls.count("worker") == 4 and calls.count("reviewer") == 4
        assert not root.joinpath("not-created-skills").exists()
        assert not any(name.startswith(("xdog.claw", "xdog.eyes", "xdog.flow", "xdog.site")) for name in sys.modules)
        print(
            json.dumps(
                {
                    "installed_coding_lifecycle": "explicit worker/acceptance/independent review/inbox consumption",
                    "goal_status": goal.status,
                    "worker_model_calls": 4,
                    "reviewer_model_calls": 4,
                    "parent_model_calls": calls.count("parent"),
                    "permission_mode": "ask",
                    "forbidden_write": False,
                    "durable_notices_consumed": len(rows),
                    "no_tui_receiver_or_provider_started": True,
                }
            )
        )
    finally:
        session.dispose()
        if controller is not None:
            await asyncio.wrap_future(controller.close())


if __name__ == "__main__":
    asyncio.run(main())
