"""Tests for claw gateway server."""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer, _groups_from_config, read_pid


def _make_test_stream_fn(text="test response"):
    def stream_fn(model_id, context, options=None):
        msg = AssistantMessage(content=(TextContent(text=text),))

        async def _gen():
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    return stream_fn

# _build_model_and_options returns (model_name: str, stream_fn), not (Model, stream_fn).
_TEST_MODEL_NAME = "test/dummy"

@pytest.fixture
def gateway_config(tmp_path):
    return ClawConfig(
        model="test-model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "gateway.sock"),
        pid_file=str(tmp_path / "gateway.pid"),
        groups=(GroupDef(id="main", name="TestClaw", is_main=True),),
    )


class TestReadPid:
    def test_missing_file(self, tmp_path):
        assert read_pid(tmp_path / "nonexistent.pid") is None

    def test_invalid_content(self, tmp_path):
        pid_file = tmp_path / "gateway.pid"
        pid_file.write_text("not-a-number", encoding="utf-8")
        assert read_pid(pid_file) is None

    def test_stale_pid(self, tmp_path):
        pid_file = tmp_path / "gateway.pid"
        pid_file.write_text("999999999", encoding="utf-8")
        assert read_pid(pid_file) is None

    def test_current_pid(self, tmp_path):
        pid_file = tmp_path / "gateway.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        assert read_pid(pid_file) == os.getpid()


class TestGroupsFromConfig:
    def test_default_group(self):
        config = ClawConfig()
        groups = _groups_from_config(config)
        assert len(groups) == 1
        assert groups[0].id == "main"
        assert groups[0].name == "Claw"
        assert groups[0].is_main is True

    def test_custom_groups(self):
        config = ClawConfig(
            groups=(
                GroupDef(id="alpha", name="Alpha", is_main=True),
                GroupDef(id="beta", name="Beta", is_main=False),
            )
        )
        groups = _groups_from_config(config)
        assert len(groups) == 2
        assert groups[0].id == "alpha"
        assert groups[1].id == "beta"


class TestGatewayServer:
    @pytest.mark.asyncio
    async def test_start_and_stop(self, gateway_config, tmp_path):
        """Gateway starts, creates socket and PID file, then cleans up."""
        server = GatewayServer(gateway_config)

        # Mock the agent function to avoid pi_ai dependency
        with patch("xdog.claw.core.runtime.gateway._build_model_and_options") as mock_build:
            mock_build.return_value = (_TEST_MODEL_NAME, _make_test_stream_fn())

            await server.start()

            socket_path = Path(gateway_config.socket_path)
            pid_path = Path(gateway_config.pid_file)

            assert socket_path.exists()
            assert pid_path.exists()
            assert pid_path.read_text(encoding="utf-8") == str(os.getpid())

            await server.stop()

            assert not socket_path.exists()
            assert not pid_path.exists()

    @pytest.mark.asyncio
    async def test_ping_pong(self, gateway_config, tmp_path):
        """Client can ping and receive pong."""
        server = GatewayServer(gateway_config)

        with patch("xdog.claw.core.runtime.gateway._build_model_and_options") as mock_build:
            mock_build.return_value = (_TEST_MODEL_NAME, _make_test_stream_fn())

            await server.start()

            try:
                reader, writer = await asyncio.open_unix_connection(
                    gateway_config.socket_path
                )

                # Send ping
                writer.write(json.dumps({"type": "ping"}).encode() + b"\n")
                await writer.drain()

                # Read pong
                response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                response = json.loads(response_line)
                assert response["type"] == "pong"
                assert Path(response["workspace_dir"]).is_absolute()
                assert Path(response["workspace_dir"]).is_dir()

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()

    @pytest.mark.asyncio
    async def test_status_request(self, gateway_config, tmp_path):
        """Client can request gateway status."""
        server = GatewayServer(gateway_config)

        with patch("xdog.claw.core.runtime.gateway._build_model_and_options") as mock_build:
            mock_build.return_value = (_TEST_MODEL_NAME, _make_test_stream_fn())

            await server.start()

            try:
                reader, writer = await asyncio.open_unix_connection(
                    gateway_config.socket_path
                )

                writer.write(json.dumps({"type": "status"}).encode() + b"\n")
                await writer.drain()

                response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                response = json.loads(response_line)
                assert response["type"] == "status"
                assert response["running"] is True
                assert response["pid"] == os.getpid()
                assert "main" in response["groups"]

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()

    @pytest.mark.asyncio
    async def test_chat_message(self, gateway_config, tmp_path):
        """Client can send a chat message and receive a response."""
        server = GatewayServer(gateway_config)

        with patch("xdog.claw.core.runtime.gateway._build_model_and_options") as mock_build:
            mock_build.return_value = (_TEST_MODEL_NAME, _make_test_stream_fn())

            await server.start()

            try:
                reader, writer = await asyncio.open_unix_connection(
                    gateway_config.socket_path
                )

                request = {"type": "message", "group_id": "main", "content": "hi"}
                writer.write(json.dumps(request).encode() + b"\n")
                await writer.drain()

                ack_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                ack = json.loads(ack_line)
                assert ack["type"] == "run_ack"

                response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                response = json.loads(response_line)
                assert response["type"] == "response"
                assert response["group_id"] == "main"
                assert "test response" in response["content"]

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()

    @pytest.mark.asyncio
    async def test_unknown_group(self, gateway_config, tmp_path):
        """Message to unknown group returns error."""
        server = GatewayServer(gateway_config)

        with patch("xdog.claw.core.runtime.gateway._build_model_and_options") as mock_build:
            mock_build.return_value = (_TEST_MODEL_NAME, _make_test_stream_fn())

            await server.start()

            try:
                reader, writer = await asyncio.open_unix_connection(
                    gateway_config.socket_path
                )

                request = {"type": "message", "group_id": "nonexistent", "content": "hi"}
                writer.write(json.dumps(request).encode() + b"\n")
                await writer.drain()

                response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                response = json.loads(response_line)
                assert response["type"] == "error"

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()

    @pytest.mark.asyncio
    async def test_invalid_json(self, gateway_config, tmp_path):
        """Invalid JSON from client returns error."""
        server = GatewayServer(gateway_config)

        with patch("xdog.claw.core.runtime.gateway._build_model_and_options") as mock_build:
            mock_build.return_value = (_TEST_MODEL_NAME, _make_test_stream_fn())

            await server.start()

            try:
                reader, writer = await asyncio.open_unix_connection(
                    gateway_config.socket_path
                )

                writer.write(b"not valid json\n")
                await writer.drain()

                response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                response = json.loads(response_line)
                assert response["type"] == "error"

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()

    @pytest.mark.asyncio
    async def test_reset_session(self, gateway_config, tmp_path):
        """Client can reset a group session."""
        server = GatewayServer(gateway_config)

        with patch("xdog.claw.core.runtime.gateway._build_model_and_options") as mock_build:
            mock_build.return_value = (_TEST_MODEL_NAME, _make_test_stream_fn())

            await server.start()

            try:
                reader, writer = await asyncio.open_unix_connection(
                    gateway_config.socket_path
                )

                # First send a message to create a session
                request = {"type": "message", "group_id": "main", "content": "hello"}
                writer.write(json.dumps(request).encode() + b"\n")
                await writer.drain()
                await asyncio.wait_for(reader.readline(), timeout=5.0)  # run_ack
                await asyncio.wait_for(reader.readline(), timeout=5.0)  # response

                # Now reset
                writer.write(json.dumps({"type": "reset", "group_id": "main"}).encode() + b"\n")
                await writer.drain()

                response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                response = json.loads(response_line)
                assert response["type"] == "reset_ack"
                assert response["group_id"] == "main"

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()

    @pytest.mark.asyncio
    async def test_empty_message_content(self, gateway_config, tmp_path):
        """Empty message content returns error."""
        server = GatewayServer(gateway_config)

        with patch("xdog.claw.core.runtime.gateway._build_model_and_options") as mock_build:
            mock_build.return_value = (_TEST_MODEL_NAME, _make_test_stream_fn())

            await server.start()

            try:
                reader, writer = await asyncio.open_unix_connection(
                    gateway_config.socket_path
                )

                request = {"type": "message", "group_id": "main", "content": ""}
                writer.write(json.dumps(request).encode() + b"\n")
                await writer.drain()

                response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                response = json.loads(response_line)
                assert response["type"] == "error"

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()


async def test_validation_errors_keep_run_id_for_ui_command_interleaving(gateway_config):
    from unittest.mock import AsyncMock, Mock
    gateway = GatewayServer(gateway_config)
    gateway._orchestrator = Mock()
    gateway._orchestrator.get_group_ids.return_value = ["main"]
    reader = asyncio.StreamReader()
    request = {"type": "message", "group_id": "missing", "run_id": "rejected-run", "content": "request"}
    reader.feed_data((json.dumps(request) + "\n").encode())
    reader.feed_eof()
    writer = Mock()
    writer.wait_closed = AsyncMock()
    responses = []

    async def write(writer, response):
        responses.append(response)

    gateway._write_response = write
    await gateway._handle_client(reader, writer)
    assert responses == [{"type": "error", "message": "Unknown group: missing", "run_id": "rejected-run"}]
