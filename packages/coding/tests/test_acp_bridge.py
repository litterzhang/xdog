import asyncio


async def test_official_acp_client_attaches_over_unix_socket(coding_session):
    from acp import PROTOCOL_VERSION, connect_to_agent
    from acp.schema import TextContentBlock
    from xdog.coding.acp.server import serve_local
    from xdog.coding.core.sessions.runtime import SessionRuntime

    runtime = SessionRuntime(coding_session, bound=False)
    runtime.start()
    server = await serve_local(runtime)

    class Client:
        async def session_update(self, session_id, update, **kwargs):
            pass

    reader, writer = await asyncio.open_unix_connection(runtime.record.address)
    conn = connect_to_agent(Client(), writer, reader)
    try:
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        await conn.load_session(
            session_id=coding_session.session_id, cwd=str(coding_session.working_dir), mcp_servers=[]
        )
        info = await conn.ext_method("xdog/session_info", {"session_id": coding_session.session_id})
        assert info["generation"] == runtime.record.generation
        result = await conn.prompt(
            session_id=coding_session.session_id,
            prompt=[TextContentBlock(type="text", text="Review")],
            xdog={"source": "Eyes"},
        )
        assert result.stop_reason == "end_turn"
        assert runtime.controller.store.records()[0].input.source == "Eyes"
    finally:
        await conn.close()
        writer.close()
        await writer.wait_closed()
        await server.close()
        await asyncio.wrap_future(runtime.close())


async def test_stdio_bridge_uses_existing_process_and_leaves_owner_alive(tmp_path):
    import json
    import os
    import sys
    from pathlib import Path

    from acp import PROTOCOL_VERSION, spawn_agent_process
    from acp.schema import TextContentBlock

    env = {**os.environ, "CODING_DIR": str(tmp_path / "coding")}
    owner = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("acp_owner_agent.py")),
        str(tmp_path),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )

    class Client:
        def __init__(self):
            self.updates = []

        async def session_update(self, session_id, update, **kwargs):
            self.updates.append(update)

    client = Client()
    try:
        async with asyncio.timeout(10):
            ready = json.loads(await owner.stdout.readline())
            sid = ready["session_id"]
            async with spawn_agent_process(
                client,
                sys.executable,
                "-c",
                "from xdog.coding.main import main; main()",
                "--acp",
                "--session",
                sid,
                env=env,
            ) as (conn, bridge):
                await conn.initialize(protocol_version=PROTOCOL_VERSION)
                await conn.load_session(cwd=str(tmp_path), session_id=sid, mcp_servers=[])
                outcome = await conn.prompt(session_id=sid, prompt=[TextContentBlock(type="text", text="Review")])
                assert outcome.stop_reason == "end_turn"
                assert (
                    "".join(row.content.text for row in client.updates if hasattr(row, "content")) == "owner reviewed"
                )
            assert owner.returncode is None
    finally:
        owner.stdin.close()
        await asyncio.wait_for(owner.wait(), 5)
