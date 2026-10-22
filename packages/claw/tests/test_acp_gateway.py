"""Gateway default ACP lifecycle does not depend on a connected TUI."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from acp import PROTOCOL_VERSION, connect_to_agent
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef, load_config, save_config
from xdog.claw.core.runtime.gateway import GatewayServer


def make_config(tmp_path, acp_path=""):
    return ClawConfig(model="test/dummy", data_dir=str(tmp_path / "data"),
                      socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid"),
                      acp_socket_path=acp_path, enabled_tools=(),
                      groups=(GroupDef(id="main", workspace=str(tmp_path)),))


def test_acp_address_override_round_trips_without_an_enable_switch(tmp_path):
    config = make_config(tmp_path)
    assert not config.acp_socket_path
    path = tmp_path / "config.yaml"
    save_config(config, path)
    assert not load_config(path).acp_socket_path
    config = make_config(tmp_path, str(tmp_path / "private" / "acp.sock"))
    save_config(config, path)
    assert load_config(path).acp_socket_path == config.acp_socket_path
    with pytest.raises(ValueError, match="absolute"):
        make_config(tmp_path, "relative.sock")


@pytest.mark.parametrize("updates", [0, 1])
async def test_gateway_acp_completion_notice_requires_explicit_subscription(tmp_path, monkeypatch, updates):
    monkeypatch.setattr("xdog.claw.core.runtime.gateway._build_model_and_options", lambda config: (
        "test/dummy", lambda *args: EventStream.empty(AssistantMessage(content=(TextContent(text="reviewed"),)))))

    class Client:
        async def session_update(self, session_id, update, **kwargs):
            pass

    with tempfile.TemporaryDirectory(prefix="claw-gw-") as private:
        endpoint = Path(private) / "acp.sock"
        config = make_config(tmp_path, str(endpoint))
        server = GatewayServer(config)
        writer = tui = None
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(endpoint))
            conn = connect_to_agent(Client(), writer, reader)
            await conn.initialize(protocol_version=PROTOCOL_VERSION)
            # Subscribe a real TUI socket to descriptive notifications, not execution ownership.
            tui_reader, tui = await asyncio.open_unix_connection(config.socket_path)
            tui.write((json.dumps({"type": "ping", "group_id": "main", "acp_updates": updates}) + "\n").encode())
            await tui.drain()
            assert json.loads(await asyncio.wait_for(tui_reader.readline(), 5))["type"] == "pong"
            receipt = await conn.ext_method("xdog/enqueue", {
                "session_id": "group:main", "cwd": str(tmp_path), "message_id": "gateway-event",
                "source": "Eyes", "instruction": "Review changes", "evidence": [], "provenance": {}})
            assert receipt["status"] == "queued"
            if updates:
                notification = json.loads(await asyncio.wait_for(tui_reader.readline(), 5))
                assert notification["type"] == "acp_input_completed"
                assert notification["message_id"] == "gateway-event"
                assert notification["response"] == "reviewed"
                assert notification["session_id"] == server._orchestrator.get_new_session_id("main")
            else:
                from xdog.claw.core.inputs.models import InputScope
                inputs = await server._orchestrator.input_service()
                record = await asyncio.wait_for(inputs.wait(InputScope("main", tmp_path), "gateway-event"), 5)
                assert record.response == "reviewed" and record.envelope.reply is None
                # A transport round trip after settlement observes no automatic chat push.
                tui.write(b'{"type":"ping"}\n')
                await tui.drain()
                assert json.loads(await asyncio.wait_for(tui_reader.readline(), 5))["type"] == "pong"
        finally:
            for stream in (writer, tui):
                if stream is not None:
                    stream.close()
                    await stream.wait_closed()
            await server.stop()
        assert not endpoint.exists() and not Path(config.pid_file).exists()


async def test_invalid_acp_start_cleans_gateway_and_does_not_replace_file(tmp_path, monkeypatch):
    monkeypatch.setattr("xdog.claw.core.runtime.gateway._build_model_and_options", lambda config: (
        "test/dummy", lambda *args: EventStream.empty(AssistantMessage())))
    with tempfile.TemporaryDirectory(prefix="claw-gw-bad-") as private:
        endpoint = Path(private) / "acp.sock"
        endpoint.write_text("keep")
        config = make_config(tmp_path, str(endpoint))
        server = GatewayServer(config)
        with pytest.raises(ValueError, match="exists"):
            await server.start()
        assert endpoint.read_text() == "keep"
        assert not Path(config.socket_path).exists() and not Path(config.pid_file).exists()
        await server.stop()


def test_acp_endpoint_cannot_alias_the_tui_socket(tmp_path):
    with pytest.raises(ValueError, match="different"):
        make_config(tmp_path, str(tmp_path / "gateway.sock"))


def test_disabled_gateway_import_and_cli_do_not_load_optional_acp_sdk(tmp_path):
    import subprocess
    import sys

    code = '''
import importlib.abc
import sys
class NoAcp(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'acp' or fullname.startswith('acp.'):
            raise AssertionError('Disabled Claw imported ACP SDK')
sys.meta_path.insert(0, NoAcp())
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.cli import main
assert not ClawConfig().acp_socket_path
assert GatewayServer(ClawConfig())._acp_server is None
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
