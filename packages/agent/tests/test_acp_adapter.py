"""Public ACP behavior, using deterministic model streams rather than a provider."""

from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream


class RecordingClient:
    def __init__(self):
        self.updates = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append((session_id, update))

    async def request_permission(self, **kwargs):
        from acp.schema import DeniedOutcome, RequestPermissionResponse

        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


def make_adapter():
    from xdog.agent.acp.adapter import AcpAdapter
    from xdog.agent.acp.backend import AgentBackend

    def factory(cwd: Path) -> Agent:
        return Agent(
            lambda *_: EventStream.empty(AssistantMessage(content=(TextContent(text="reviewed"),))),
            config=AgentConfig(model="test"),
        )

    adapter = AcpAdapter(AgentBackend(factory))
    client = RecordingClient()
    adapter.on_connect(client)
    return adapter, client


async def test_generic_acp_roundtrip_without_coding(tmp_path):
    adapter, client = make_adapter()
    from acp import PROTOCOL_VERSION
    from acp.schema import TextContentBlock

    try:
        initialized = await adapter.initialize(PROTOCOL_VERSION)
        assert initialized.protocol_version == PROTOCOL_VERSION
        assert initialized.agent_capabilities.load_session
        created = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
        result = await adapter.prompt(created.session_id, [TextContentBlock(type="text", text="review")])
        assert result.stop_reason == "end_turn"
        assert "".join(row.content.text for _, row in client.updates if hasattr(row, "content")) == "reviewed"
        await adapter.load_session(cwd=str(tmp_path), session_id=created.session_id, mcp_servers=[])
        with pytest.raises(Exception, match="session"):
            await adapter.prompt("missing-session", [TextContentBlock(type="text", text="review")])
    finally:
        await adapter.aclose()


@pytest.mark.parametrize("bad", ["relative/path", "/definitely-missing-acp-cwd"])
async def test_invalid_workspace_is_rejected(bad):
    adapter, _ = make_adapter()
    try:
        with pytest.raises(Exception, match="directory|absolute"):
            await adapter.new_session(cwd=bad, mcp_servers=[])
    finally:
        await adapter.aclose()


async def test_load_does_not_change_workspace(tmp_path):
    adapter, _ = make_adapter()
    other = tmp_path / "other"
    other.mkdir()
    try:
        created = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
        with pytest.raises(Exception, match="workspace"):
            await adapter.load_session(cwd=str(other), session_id=created.session_id, mcp_servers=[])
    finally:
        await adapter.aclose()


async def test_unsupported_content_and_mcp_fail_closed(tmp_path):
    adapter, _ = make_adapter()
    from acp.schema import ImageContentBlock

    try:
        with pytest.raises(Exception, match="MCP"):
            await adapter.new_session(cwd=str(tmp_path), mcp_servers=[object()])
        created = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
        with pytest.raises(Exception, match="text"):
            await adapter.prompt(
                created.session_id, [ImageContentBlock(type="image", data="AAAA", mime_type="image/png")]
            )
    finally:
        await adapter.aclose()


async def test_prompt_metadata_is_bounded_finite_json(tmp_path):
    adapter, _ = make_adapter()
    from acp.schema import TextContentBlock

    try:
        created = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
        for value in ({"xdog": {"number": float("nan")}}, {"xdog": {"payload": "x" * 1024 * 1024}}):
            with pytest.raises(Exception, match="JSON|capacity"):
                await adapter.prompt(
                    created.session_id, [TextContentBlock(type="text", text="review")], field_meta=value
                )
    finally:
        await adapter.aclose()


async def test_generic_adapter_does_not_advertise_unimplemented_extensions():
    from acp import PROTOCOL_VERSION

    adapter, _ = make_adapter()
    try:
        initialized = await adapter.initialize(PROTOCOL_VERSION)
        assert initialized.field_meta == {"xdog": {"extensions": {}}}
        with pytest.raises(Exception, match="integer"):
            await adapter.initialize(True)
    finally:
        await adapter.aclose()
