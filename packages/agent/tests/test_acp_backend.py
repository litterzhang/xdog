"""Concurrency/cancellation and incremental ACP projection."""

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, TextDeltaEvent
from xdog.ai.utils.event_stream import EventStream


async def test_cancel_only_affects_requesting_peer_and_settles_agent(tmp_path):
    from acp.schema import TextContentBlock
    from xdog.agent.acp.adapter import AcpAdapter
    from xdog.agent.acp.backend import AgentBackend

    started, release = asyncio.Event(), asyncio.Event()
    producers = []

    def stream(*args):
        events = EventStream()

        async def produce():
            started.set()
            await release.wait()
            message = AssistantMessage(content=(TextContent(text="done"),))
            await events.send(DoneEvent(stop_reason="stop", message=message))
            events.set_result(message)
            await events.close()

        producers.append(asyncio.create_task(produce()))
        return events

    class Client:
        async def session_update(self, *args, **kwargs):
            pass

    backend = AgentBackend(lambda cwd: Agent(stream, config=AgentConfig(model="test")))
    first, second = AcpAdapter(backend), AcpAdapter(backend)
    first.on_connect(Client())
    second.on_connect(Client())
    task = None
    try:
        created = await first.new_session(cwd=str(tmp_path))
        await second.load_session(cwd=str(tmp_path), session_id=created.session_id)
        task = asyncio.create_task(first.prompt(created.session_id, [TextContentBlock(type="text", text="wait")]))
        await asyncio.wait_for(started.wait(), 2)
        with pytest.raises(Exception, match="already running"):
            await second.prompt(created.session_id, [TextContentBlock(type="text", text="another")])
        await second.cancel(created.session_id)
        assert not task.done()
        await first.cancel(created.session_id)
        assert (await asyncio.wait_for(task, 2)).stop_reason == "cancelled"
        assert backend.agents[created.session_id][1].is_idle
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await first.aclose()
        await second.aclose()
        await asyncio.gather(*producers, return_exceptions=True)


def test_projector_never_repeats_text_at_message_end():
    from xdog.agent.acp.events import EventProjector
    from xdog.agent.events import MessageEndEvent, MessageUpdateEvent

    projector = EventProjector()
    message = AssistantMessage(content=(TextContent(text="hello"),))
    delta = TextDeltaEvent(index=0, delta="hel", partial=message)
    first = projector.project(MessageUpdateEvent(message=message, assistant_message_event=delta))
    last = projector.project(MessageEndEvent(message=message))
    assert "".join(item.content.text for item in (*first, *last)) == "hello"
    assert projector.project(MessageEndEvent(message=message)) == ()


async def test_broken_output_settles_producer_without_waiting_for_model(tmp_path):
    from xdog.agent.acp.backend import AgentBackend
    from xdog.agent.acp.contracts import Peer
    from xdog.ai.types import StartEvent

    release, observed = asyncio.Event(), asyncio.Event()
    producers = []

    def stream(*args):
        result = EventStream()

        async def produce():
            message = AssistantMessage(content=(TextContent(text="x"),))
            await result.send(StartEvent(partial=message))
            await result.send(TextDeltaEvent(index=0, delta="x", partial=message))
            await release.wait()
            await result.send(DoneEvent(stop_reason="stop", message=message))
            result.set_result(message)
            await result.close()

        producers.append(asyncio.create_task(produce()))
        return result

    async def broken(update):
        observed.set()
        raise OSError("client output closed")

    backend = AgentBackend(lambda cwd: Agent(stream, config=AgentConfig(model="test")))
    peer = Peer("p1", None)
    ref = await backend.new_session(tmp_path, peer)
    task = asyncio.create_task(backend.prompt(ref, peer, "review", {}, broken))
    try:
        await asyncio.wait_for(observed.wait(), 2)
        done, _ = await asyncio.wait({task}, timeout=0.1)
        finished_before_model = bool(done)
        release.set()
        with pytest.raises(OSError, match="output closed"):
            await asyncio.wait_for(task, 2)
        assert finished_before_model, "output failure left the model producer running"
        assert backend.agents[ref.id][1].is_idle
    finally:
        release.set()
        await asyncio.gather(*producers, return_exceptions=True)
        await backend.disconnect(peer)
