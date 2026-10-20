"""External example/test host using the real Agent ChildSupervisor, not a new loop.

Only model streaming is scripted. Goal callbacks cross back to the service loop;
filesystem tools, tool loop, child admission/result/cleanup and storage are real.
The same file is copied into the cold wheel-only consumer environment.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace

from xdog.agent import Agent, AgentConfig
from xdog.agent.supervision import ChildSupervisor, settle_on
from xdog.agent.tools import create_filesystem_tool
from xdog.agent.types import BeforeToolCallResult
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.skills.goal.host import ChildStatus, EnqueueReceipt
from xdog.skills.goal.models import WorkerRef
from xdog.skills.goal.tools import create_context_tool, create_report_tool


class AgentHost:
    def __init__(self, owner):
        self.owner = owner
        self.loop = asyncio.get_running_loop()
        self.service = None
        self.roles = {}
        self.accepted = {}
        self.execution = uuid.uuid4().hex
        self.model_calls = []
        self.agent_ids = []
        self.supervisor = ChildSupervisor(self.factory)

    def ref(self, identifier):
        return WorkerRef(identifier, self.execution + ":" + identifier)

    async def factory(self, spec, publish):
        role, data = self.roles[spec.label]
        reference = self.ref(spec.id)
        if role == "worker":
            await settle_on(self.loop, self.service.bind_worker(data.goal_id, data.task_id, data.attempt_id, reference))
            originals = (
                create_context_tool(self.service, data.goal_id, task_id=data.task_id, attempt_id=data.attempt_id),
                create_report_tool(self.service, data.goal_id, data.task_id, data.attempt_id),
            )
            context = await settle_on(
                self.loop, self.service.worker_context(data.goal_id, data.task_id, data.attempt_id)
            )
        else:
            await settle_on(self.loop, self.service.bind_reviewer(data.goal_id, data.review_id, reference))
            originals = (create_context_tool(self.service, data.goal_id, review_id=data.review_id),)
            context = await settle_on(self.loop, self.service.review_context(data.goal_id, data.review_id))
        tools = []
        for original in originals:

            async def execute(call, params, cancel=None, on_update=None, ctx=None, original=original):
                return await settle_on(self.loop, original.execute(call, params, cancel, on_update, ctx))

            tools.append(replace(original, execute=execute))
        tools.append(create_filesystem_tool())
        round_number = 0

        def model(_model, _context, options=None):
            nonlocal round_number
            self.model_calls.append((role, round_number))
            if round_number == 0:
                message = AssistantMessage(
                    content=(
                        ToolCall(id="read", name="filesystem", arguments={"action": "read", "path": "result.txt"}),
                    ),
                    stop_reason="toolUse",
                )
            elif role == "worker" and round_number == 1:
                message = AssistantMessage(
                    content=(
                        ToolCall(
                            id="report",
                            name="task_report",
                            arguments={
                                "revision": 1,
                                "state": "submitted",
                                "summary": "Read actual result",
                                "evidence": ["result.txt"],
                                "blockers": [],
                            },
                        ),
                    ),
                    stop_reason="toolUse",
                )
            elif role == "worker":
                message = AssistantMessage(content=(TextContent(text="Worker complete"),))
            else:
                verdict = {
                    "verdict": "passed",
                    "summary": "Read actual result independently",
                    "conditions": [
                        {
                            "condition": item,
                            "status": "passed",
                            "evidence": ["result.txt"],
                            "reason": "Actual filesystem read in an independent fresh Agent",
                        }
                        for item in context.criteria
                    ],
                }
                message = AssistantMessage(content=(TextContent(text=json.dumps(verdict)),))
            round_number += 1

            async def events():
                yield StartEvent(partial=message)
                yield DoneEvent(message=message)

            future = asyncio.get_running_loop().create_future()
            future.set_result(message)
            return EventStream.from_async_generator(events(), result_future=future)

        async def before(call, cancel=None):
            if call.tool_call.name == "filesystem" and call.args.get("action") not in {"read", "ls", "grep", "find"}:
                return BeforeToolCallResult(block=True, reason="Actual role read-only boundary")
            if role == "reviewer" and call.tool_call.name == "task_report":
                return BeforeToolCallResult(block=True, reason="Reviewer cannot report/accept worker tasks")
            return None

        agent = Agent(
            model,
            config=AgentConfig(model="test/goal-only"),
            tools=tools,
            tool_ctx={"fs_workspace": str(self.owner.workspace)},
            before_tool_call=before,
        )
        self.agent_ids.append(agent._session_id)
        return agent

    async def start_worker(self, data):
        key = "worker-" + data.attempt_id
        self.roles[key] = "worker", data
        identifier = await self.supervisor.start(
            data.prompt, access=data.access, inherit_context=False, config={"label": key}
        )
        return self.ref(identifier)

    async def start_reviewer(self, data):
        key = "reviewer-" + data.review_id
        self.roles[key] = "reviewer", data
        identifier = await self.supervisor.start(
            data.prompt, access="read-only", inherit_context=False, config={"label": key}
        )
        return self.ref(identifier)

    async def child_status(self, reference):
        if self.ref(reference.id) != reference:
            return ChildStatus(reference, "unknown")
        captured = self.supervisor.capture_source(reference.id)
        row = await self.supervisor.control("inspect", reference.id)
        state = (
            "settling"
            if captured.settling
            else {"completed": "succeeded", "failed": "failed", "cancelled": "cancelled", "interrupted": "unknown"}.get(
                captured.status, "running"
            )
        )
        return ChildStatus(reference, state, row.get("result", ""), row.get("error", ""))

    async def cancel_child(self, reference):
        if self.ref(reference.id) != reference:
            raise ValueError("Child execution owner changed")
        await self.supervisor.control("cancel", reference.id)

    async def enqueue(self, notice):
        previous = self.accepted.get(notice.id)
        if previous is not None and previous != notice:
            raise ValueError("Canonical notice conflict")
        self.accepted[notice.id] = notice
        return EnqueueReceipt(self.owner.key, notice.id, "duplicate" if previous else "accepted")

    async def receipt(self, notice):
        if self.accepted.get(notice.id) == notice:
            return EnqueueReceipt(self.owner.key, notice.id, "accepted")
        return None

    async def wait(self, reference):
        await self.supervisor.control("wait", reference.id)

    async def close(self):
        await self.supervisor.aclose()
