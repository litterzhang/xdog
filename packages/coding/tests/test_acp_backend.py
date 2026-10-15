import asyncio

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream


async def test_acp_uses_target_session_and_persists_external_input(coding_session):
    from acp.schema import TextContentBlock
    from xdog.agent.acp.adapter import AcpAdapter
    from xdog.coding.acp.backend import CodingBackend
    from xdog.coding.core.sessions.runtime import SessionRuntime

    coding_session.agent.set_stream_fn(
        lambda *_: EventStream.empty(AssistantMessage(content=(TextContent(text="reviewed"),)))
    )
    runtime = SessionRuntime(coding_session, bound=False)
    runtime.start()

    class Client:
        def __init__(self):
            self.updates = []

        async def session_update(self, session_id, update, **kwargs):
            self.updates.append(update)

    client = Client()
    adapter = AcpAdapter(CodingBackend(runtime.router, runtime.registry, fixed_session=coding_session.session_id))
    adapter.on_connect(client)
    try:
        await adapter.load_session(cwd=str(coding_session.working_dir), session_id=coding_session.session_id)
        result = await adapter.prompt(coding_session.session_id, [TextContentBlock(type="text", text="Review tests")])
        assert result.stop_reason == "end_turn"
        assert "".join(row.content.text for row in client.updates if hasattr(row, "content")) == "reviewed"
        records = runtime.controller.store.records()
        assert records[0].input.origin == "acp"
        assert records[0].status == "succeeded"
        with pytest.raises(Exception, match="fixed|target"):
            await adapter.new_session(cwd=str(coding_session.working_dir))
    finally:
        await adapter.aclose()
        await asyncio.wrap_future(runtime.close())


async def test_untrusted_metadata_cannot_grant_human_wake_budget(coding_session):
    from acp.schema import TextContentBlock
    from xdog.agent.acp.adapter import AcpAdapter
    from xdog.coding.acp.backend import CodingBackend
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session, bound=True)
    runtime.start()

    class Client:
        async def session_update(self, *args, **kwargs):
            pass

    adapter = AcpAdapter(CodingBackend(runtime.router, runtime.registry, fixed_session=coding_session.session_id))
    adapter.on_connect(Client())
    try:
        await adapter.load_session(cwd=str(coding_session.working_dir), session_id=coding_session.session_id)
        with pytest.raises(Exception, match="metadata|origin"):
            await adapter.prompt(
                coding_session.session_id,
                [TextContentBlock(type="text", text="Review")],
                xdog={"origin": "human", "allow_all": True},
            )
        assert runtime.controller.store.records() == ()
    finally:
        await adapter.aclose()
        await asyncio.wrap_future(runtime.close())
