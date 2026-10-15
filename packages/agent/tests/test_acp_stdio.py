"""Exercise official SDK framing/initialization against a real child process."""

import asyncio
import sys
from pathlib import Path


async def test_official_client_over_stdio(tmp_path):
    from acp import PROTOCOL_VERSION, spawn_agent_process
    from acp.schema import TextContentBlock

    class Client:
        def __init__(self):
            self.updates = []

        async def session_update(self, session_id, update, **kwargs):
            self.updates.append(update)

    client = Client()
    script = Path(__file__).with_name("acp_stdio_agent.py")
    async with asyncio.timeout(10):
        async with spawn_agent_process(client, sys.executable, str(script)) as (conn, process):
            initialized = await conn.initialize(protocol_version=PROTOCOL_VERSION)
            assert initialized.protocol_version == PROTOCOL_VERSION
            created = await conn.new_session(cwd=str(tmp_path), mcp_servers=[])
            result = await conn.prompt(
                session_id=created.session_id, prompt=[TextContentBlock(type="text", text="review")]
            )
            assert result.stop_reason == "end_turn"
            assert "".join(update.content.text for update in client.updates) == "stdio reviewed"
            await conn.load_session(session_id=created.session_id, cwd=str(tmp_path), mcp_servers=[])
