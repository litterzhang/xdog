"""External wheel consumer: actual Claw/Core Inbox/children; scripted model only."""
from __future__ import annotations

import asyncio
import importlib.metadata as metadata
import json
import sys
from pathlib import Path

from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime import orchestrator as installed_claw
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


class Model:
    def __init__(self, *, ordinary=False):
        self.calls = []
        self.ordinary = ordinary

    def __call__(self, model, context, options):
        names = {tool.name for tool in context.tools}
        role = "worker" if "task_report" in names else "reviewer" if "goal_context" in names else "parent"
        self.calls.append(role)
        start = max((i for i, message in enumerate(context.messages) if message.role == "user"), default=-1)
        turn = sum(message.role == "assistant" for message in context.messages[start + 1:])
        if role == "parent" and self.ordinary and turn == 0:
            parts = (ToolCall(id="ordinary-write", name="filesystem", arguments={
                "action": "write", "path": "ordinary.txt", "content": "ordinary tool result"}),)
        elif role == "parent" and self.ordinary and turn == 1:
            parts = (ToolCall(id="remember", name="memory", arguments={
                "action": "write", "target": "memory", "text": "installed-memory-sentinel"}),)
        elif role != "parent" and turn == 0:
            parts = (ToolCall(id="context", name="goal_context", arguments={}),)
        elif role != "parent" and turn == 1:
            parts = (ToolCall(id="read", name="filesystem", arguments={"action": "read", "path": "result.txt"}),)
        elif role == "worker" and turn == 2:
            parts = (ToolCall(id="report", name="task_report", arguments={
                "revision": 1, "state": "submitted", "summary": "Read installed output",
                "evidence": ["result.txt"], "blockers": []}),)
        elif role == "reviewer" and turn == 2:
            parts = (ToolCall(id="forbidden", name="filesystem", arguments={
                "action": "write", "path": "forbidden.txt", "content": "must not write"}),)
        elif role == "reviewer":
            parts = (TextContent(text=json.dumps({"verdict": "passed", "summary": "Independent real read",
                "conditions": [{"condition": "Result exists", "status": "passed", "evidence": ["result.txt"],
                                "reason": "Read the actual installed workspace"}]})),)
        else:
            parts = (TextContent(text="done"),)
        message = AssistantMessage(content=parts, stop_reason="toolUse" if isinstance(parts[0], ToolCall) else "stop")

        async def events():
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)


def application(root, model):
    app = Orchestrator(ClawConfig(data_dir=str(root / "data")),
                       stream_fn=model, data_dir=root / "data")
    app.register_group(Group(id="main", workspace=str(root / "workspace")))
    return app, app._runtimes["main"]


async def activate(app):
    result = await app.route_message(UserInput(group_id="main", content="Inspect actual output", channel="tui"))
    assert result is not None and not result.error


async def settled(app, runtime):
    service = await app.input_service()
    scope = InputScope("main", runtime.workspace_dir)
    async with asyncio.timeout(8):
        while True:
            rows = await service.store.records(scope)
            if all(row.status in {"succeeded", "failed", "cancelled", "interrupted"} for row in rows) and (
                    runtime._active_session is None or runtime._active_session.agent.is_idle):
                return rows
            await asyncio.sleep(.005)


async def base(root):
    names = {dist.metadata["Name"].lower().replace("_", "-") for dist in metadata.distributions()}
    assert not names.intersection({"xdog-goal", "xdog-eyes", "agent-client-protocol"}), names
    model = Model(ordinary=True)
    app, runtime = application(root, model)
    old = root / "data/groups/main/goals.json"
    old.write_bytes(b"unsupported legacy bytes; never open or migrate\n")
    try:
        await app.start()
        await activate(app)
        assert runtime.goal_host is None and not (old.parent / "goals").exists()
        assert runtime.workspace_dir.joinpath("ordinary.txt").read_text() == "ordinary tool result"
        assert "installed-memory-sentinel" in runtime.memory.long_term.read()
        history = runtime.transcript_store.load_transcript(runtime.get_new_session_id())
        assert any(row.get("role") == "assistant" for row in history)
        assert old.read_bytes() == b"unsupported legacy bytes; never open or migrate\n"
        assert not any(n.startswith(("xdog.goal", "xdog.eyes", "acp", "xdog.claw.acp")) for n in sys.modules)
    finally:
        await app.stop()
    from xdog.claw.core.runtime.gateway import GatewayServer
    for feature in ("eyes", "acp"):
        private = root / ("missing-" + feature)
        config = ClawConfig(data_dir=str(private), model="test/installed", socket_path=str(private / "native.sock"), **(
            {"eyes_enabled": True} if feature == "eyes" else {"acp_socket_path": str(private / "acp/socket")}))
        gateway = GatewayServer(config)
        try:
            await gateway.start()
        except RuntimeError as exc:
            assert "xdog-claw[" + feature + "]" in str(exc), str(exc)
        else:
            raise AssertionError("Explicit optional feature without distribution was silently accepted")
        assert not (private / "native.sock").exists()
    print(json.dumps({"installed_claw_base": "actual chat/filesystem/memory/history without Goal/Eyes/ACP",
                      "missing_features": "all explicitly rejected", "calls": model.calls}))


async def lifecycle(root):
    from xdog.goal.host import OperationContext
    from xdog.goal.models import report_digest
    from xdog.goal.store import GoalStore

    model = Model()
    app, runtime = application(root, model)
    runtime.workspace_dir.joinpath("result.txt").write_text("actual installed result")
    try:
        reference = runtime.skill_manager.load_skill("goals")
        assert reference is not None and reference.packaged and reference.content
        await app.start()
        await activate(app)
        host = runtime.goal_host
        owner = host.owner
        goal = await host.service.create("Output", "Inspect installed result", ("Result exists",),
                                         context=OperationContext(True))
        goal = await host.service.add_task(goal.id, "Read output")
        goal = await host.service.assign(goal.id, goal.tasks[0].id, "Read original output and report")
        worker = goal.tasks[0].attempts[-1].worker
        await runtime._active_session.children.supervisor.control("wait", worker.id)
        await host.flush()
        await settled(app, runtime)
        goal = await host.service.status(goal.id)
        task = goal.tasks[0]
        assert task.state == "submitted" and goal.status == "active"
        old_session = runtime._active_session
        runtime.reset_session()
        await activate(app)
        assert runtime._active_session is not old_session and runtime.goal_host.owner == owner
        assert (await host.child_status(worker)).state == "succeeded"
        attempt = task.attempts[-1]
        goal = await host.service.accept_task(goal.id, task.id, attempt.id, 1, report_digest(attempt.reports[-1]),
                                              "Inspected actual settled report")
        goal = await host.service.request_complete(goal.id, paths=("result.txt",))
        reviewer = goal.review.worker
        await runtime._active_session.children.supervisor.control("wait", reviewer.id)
        goal = await host.service.reconcile(goal.id)
        assert goal.status == "completed" and not runtime.workspace_dir.joinpath("forbidden.txt").exists()
        await host.flush()
        rows = await settled(app, runtime)
        notices = [row for row in rows if row.envelope.message.source == "goal"]
        assert len(notices) == 3 and all(row.status == "succeeded" for row in notices)
        path = host.service.store.path
    finally:
        await app.stop()
    # Independently acquire the actual lease, then reopen the real application.
    store = await GoalStore.open(path, owner)
    await store.close()
    calls = len(model.calls)
    app, runtime = application(root, model)
    try:
        await app.start()
        host = runtime.goal_host
        assert host.owner == owner and (await host.service.status(goal.id)).status == "completed"
        assert (await host.child_status(worker)).state == "succeeded"
        assert (await host.child_status(reviewer)).state == "succeeded"
        assert runtime._active_session is None and len(model.calls) == calls
    finally:
        await app.stop()
    # A fresh owner automatically gets the installed capability without delegation.
    app, runtime = application(root / "disabled-with-package", model)
    try:
        await app.start()
        assert runtime.skill_manager.load_skill("goals").packaged
        assert runtime.goal_host is not None and await runtime.goal_host.service.list() == ()
    finally:
        await app.stop()
    print(json.dumps({"installed_claw_goal": "real worker/report/rotation/accept/review/Inbox/reopen",
                      "calls": model.calls, "notices": len(notices)}))


async def main():
    assert Path(installed_claw.__file__).is_relative_to(Path(sys.prefix))
    names = {dist.metadata["Name"].lower().replace("_", "-") for dist in metadata.distributions()}
    assert not names.intersection({"xdog-coding", "xdog-eyes", "xdog-flow", "xdog-site"})
    await (base(Path.cwd()) if "--base-only" in sys.argv else lifecycle(Path.cwd()))


if __name__ == "__main__":
    asyncio.run(main())
