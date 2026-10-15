import asyncio


async def test_controller_uses_existing_session_preparation_and_persistence(coding_session):
    from xdog.agent.core import CustomAgentMessage
    from xdog.agent.session_input import SessionInput
    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.core.sessions.controller import SessionController

    seen = []

    def stream(model, context, options):
        seen.extend(context.messages)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="reviewed"),)))

    coding_session.agent.set_stream_fn(stream)
    controller = SessionController(coding_session, bound=False)
    controller.start()
    try:
        ticket = controller.submit(
            SessionInput("r1", "acp", "Eyes", "Review tests", ({"path": "a.py"},), {}), peer_id="p1"
        )
        outcome = await asyncio.wait_for(asyncio.wrap_future(ticket.result), 3)
        assert outcome.status == "succeeded"
        saved = coding_session.session_manager.load_session(coding_session.session_id)
        assert any(
            isinstance(row, CustomAgentMessage) and row.type == "xdog.session_input.v1" for row in saved.messages
        )
        assert any("not a human message" in part.text for row in seen for part in row.content if hasattr(part, "text"))
        assert controller.store.records()[0].status == "succeeded"
    finally:
        await asyncio.wrap_future(controller.close())
