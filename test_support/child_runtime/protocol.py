"""Independent child knowledge/configuration and canonical directional message facts."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, HandledInput
from xdog.agent.events import MessageEndEvent
from xdog.agent.supervision import ChildSpec, child_prompt
from xdog.agent.supervision_config import ChildPresets
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream


def facts(events):
    return [event for event in events if event["type"] == "child_message"]


class TestChildProtocol:
    async def test_old_true_is_inert_history_and_cannot_admit_new_inherited_work(
        self, tmp_path, *, runtime_factory, child_store_type, execution_owner
    ):
        store = child_store_type(tmp_path)
        store.begin(ChildSpec("0123456789ab", "old task"))
        old = store.load()[0]
        old["config"]["inherit_context"] = True
        store.save(old)
        restored = runtime_factory(lambda *args: pytest.fail("No historical replay"), records=store.load())
        try:
            record = await restored.control("inspect", old["id"])
            assert record["config"]["inherit_context"] is True and not restored.active
            execution_owner.assert_running(restored)  # Metadata owner only; never an Agent/model.
        finally:
            await restored.aclose()
        owner = runtime_factory(lambda *args: pytest.fail("Invalid admission"))
        try:
            with pytest.raises(ValueError):
                await owner.start("task", inherit_context=True, config={"inherit_context": False})
            with pytest.raises(ValueError):
                await owner.start("task", config={"inherit_context": True})
            with pytest.raises(ValueError):
                await owner.tools()[0].execute("call", {"action": "spawn", "task": "task", "inherit_context": True})
            execution_owner.assert_unstarted(owner)
        finally:
            await owner.aclose()
        # Historical preset templates may be reused only as fresh independent children.
        presets = ChildPresets.from_snapshot([{"name": "old", "description": "", "config": old["config"]}])
        assert presets.resolve({"preset": "old"}).inherit_context is False

    async def test_prompt_update_before_first_request_busy_refusal_and_stopped_metadata_only(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        building, release_factory, entered, release_model = (threading.Event() for _ in range(4))
        contexts, agents, events = [], [], []

        async def factory(spec, publish):
            # Capture the original spec, as an asynchronous real host factory does.
            building.set()
            while not release_factory.is_set():
                await asyncio.sleep(0.002)

            def stream(model, context, options):
                contexts.append(context)
                message = AssistantMessage(content=(TextContent(text="answer"),))
                completed = asyncio.get_running_loop().create_future()
                completed.set_result(message)

                async def generate():
                    entered.set()
                    while not release_model.is_set():
                        await asyncio.sleep(0.002)
                    yield DoneEvent(message=message)

                return EventStream.from_async_generator(generate(), result_future=completed)

            agent = Agent(stream, config=AgentConfig(model="test/model", system_prompt=child_prompt("", spec)))
            agents.append(agent)
            return agent

        store = child_store_type(tmp_path)
        owner = runtime_factory(factory, store.save, begin=store.begin, persist_admission=store.save_admission)
        owner.subscribe(events.append)
        try:
            identifier = await owner.start("FIRST USER KNOWLEDGE", config={"system_prompt": "ORIGINAL"})
            assert await asyncio.to_thread(building.wait, 3)
            tool = owner.tools()[0]
            result = await tool.execute("call", {"action": "set_system_prompt", "id": identifier, "text": "NEW ROLE"})
            assert json.loads(result.content[0].text)["config"]["system_prompt"] == "NEW ROLE"
            assert not contexts and not facts(events)
            release_factory.set()
            assert await asyncio.to_thread(entered.wait, 3)
            assert "NEW ROLE" in str(contexts[0].system_prompt) and "ORIGINAL" not in str(contexts[0].system_prompt)
            assert len(contexts[0].messages) == 1
            assert contexts[0].messages[0].content[0].text == "FIRST USER KNOWLEDGE"
            before = agents[0].messages
            with pytest.raises(RuntimeError, match="idle-only"):
                await owner.control("set_system_prompt", identifier, "NOT APPLIED")
            assert (await owner.control("inspect", identifier))["config"]["system_prompt"] == "NEW ROLE"
            assert agents[0].messages == before and len(contexts) == 1
            release_model.set()
            result = await owner.control("wait", identifier)
            history = result["messages"]
            previous_facts = facts(events)
            result = await owner.control("set_system_prompt", identifier, "STOPPED CONFIG ONLY")
            assert result["messages"] == history and result["status"] == "completed"
            assert store.load()[0]["config"]["system_prompt"] == "STOPPED CONFIG ONLY"
            assert len(contexts) == 1 and facts(events) == previous_facts
            assert result["config"]["skills"] == [] and result["config"]["tool_actions"] == {}
        finally:
            release_factory.set()
            release_model.set()
            await owner.aclose()

    async def test_canonical_messages_keep_original_ids_and_never_present_tools_or_queueing_as_replies(
        self, *, runtime_factory
    ):
        entered, release = threading.Event(), threading.Event()
        requests, received, ui = [], [], []

        async def execute(*args, **kwargs):
            return AgentToolResult(content=(TextContent(text="INTERNAL TOOL RESULT"),))

        def factory(spec, publish):
            def stream(model, context, options):
                requests.append(context)
                if len(requests) == 1:
                    message = AssistantMessage(
                        content=(TextContent(text="TOOL PHASE BODY"), ToolCall(id="read1", name="read", arguments={}))
                    )
                    completed = asyncio.get_running_loop().create_future()
                    completed.set_result(message)

                    async def generate():
                        entered.set()
                        while not release.is_set():
                            await asyncio.sleep(0.002)
                        yield DoneEvent(message=message)

                    return EventStream.from_async_generator(generate(), result_future=completed)
                return EventStream.empty(AssistantMessage(content=(TextContent(text=f"answer {len(requests)}"),)))

            return Agent(
                stream,
                config=AgentConfig(model="test/model"),
                tools=[AgentTool(name="read", parameters={"type": "object"}, execute=execute)],
            )

        owner = runtime_factory(factory)
        # A listener's mutations/failures must not corrupt another listener or the UI.
        owner.subscribe(lambda event: event.update(text="MUTATED"))
        owner.subscribe(received.append)
        owner.publish = ui.append
        try:
            identifier = await owner.start("FIRST TASK")
            assert await asyncio.to_thread(entered.wait, 3)
            initial = facts(received)
            assert len(initial) == 1 and initial[0]["message_id"] == "initial-task:" + identifier
            assert initial[0]["status"] == "admitted" and initial[0]["text"] == "FIRST TASK"
            await owner.control("steer", identifier, "STEER KNOWLEDGE")
            await owner.control("follow-up", identifier, "FOLLOW KNOWLEDGE")
            queue = (await owner.control("queue", identifier, '{"action":"list"}'))["queue"]
            ids = {row["content"]: row["id"] for row in queue["inputs"]}
            assert facts(received) == initial  # Merely queued is not canonical admission.
            release.set()
            result = await owner.control("wait", identifier)
            assert result["status"] == "completed" and len(requests) == 3
            incoming = [row for row in facts(received) if row["direction"] == "parent_to_child"]
            assert [(row["message_id"], row["text"]) for row in incoming] == [
                ("initial-task:" + identifier, "FIRST TASK"),
                (ids["STEER KNOWLEDGE"], "STEER KNOWLEDGE"),
                (ids["FOLLOW KNOWLEDGE"], "FOLLOW KNOWLEDGE"),
            ]
            outgoing = [row for row in facts(received) if row["direction"] == "child_to_parent"]
            assert [(row["text"], row["status"]) for row in outgoing] == [
                ("TOOL PHASE BODY", "received"),
                ("answer 2", "completed"),
                ("answer 3", "completed"),
            ]
            assert all(row["sequence"] > 0 and row["run_id"] for row in outgoing)
            assert all(row["message_id"] == f"assistant:{row['run_id']}:{row['sequence']}" for row in outgoing)
            assert len({row["message_id"] for row in facts(received)}) == len(facts(received))
            assert facts(received) == facts(ui)
            assert "INTERNAL TOOL RESULT" not in str(facts(received))
            before = list(facts(received))
            await owner.control("inspect", identifier)
            owner.snapshot()
            # Re-publishing the same original completion is idempotent and requires no model.
            row = outgoing[-1]
            event = MessageEndEvent(
                message=AssistantMessage(content=(TextContent(text=row["text"]),)),
                run_id=row["run_id"],
                sequence=row["sequence"],
            )
            owner._message_fact(
                owner._children[identifier],
                event,
                message_id=row["message_id"],
                direction=row["direction"],
                message=event.message,
                source="assistant",
                status="completed",
            )
            assert facts(received) == before and len(requests) == 3
        finally:
            release.set()
            await owner.aclose()

    async def test_handled_initial_input_does_not_fabricate_admission_or_assistant_reply(self, *, runtime_factory):
        events, calls = [], []

        async def handle(message, source, cancel):
            return HandledInput("Handler feedback is not an assistant message")

        def factory(spec, publish):
            return Agent(
                lambda *args: calls.append(args) or EventStream.empty(AssistantMessage()),
                config=AgentConfig(model="test/model"),
                input_transform=handle,
            )

        owner = runtime_factory(factory)
        owner.subscribe(events.append)
        try:
            result = await owner.control("wait", await owner.start("handled"))
            assert result["status"] == "completed" and result["messages"] == []
            assert not calls and not facts(events)
        finally:
            await owner.aclose()

    async def test_canceled_queued_input_has_no_admission_or_complete_partial_reply(self, *, runtime_factory):
        from test_support.child_runtime.queue import running

        owner, identifier = await running(runtime_factory=runtime_factory)
        events = []
        owner.subscribe(events.append)
        try:
            await owner.control("steer", identifier, "QUEUED BUT NOT ADMITTED")
            await owner.control("cancel", identifier)
            assert not facts(events)  # HELD partial and unconsumed steer are not replies/admissions.
        finally:
            await owner.aclose()
