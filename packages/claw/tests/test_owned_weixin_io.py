"""Actual account/channel/polling caches use owned IO, never another agent loop."""
from __future__ import annotations

import builtins
import io
import os
import threading
from pathlib import Path
from unittest.mock import patch

from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.weixin.channel import WeixinChannel, _save_user_id_map, _TypingTicketCache
from xdog.claw.channels.weixin.context_tokens import clear_context_tokens, persist_context_tokens, set_context_token
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.claw.tests.test_weixin_application_intake import ChannelApi, text


def trace_opens(monkeypatch, root):
    calls = []
    actual_open, actual_io_open = builtins.open, io.open

    def observe(actual, file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).is_relative_to(root):
            calls.append((Path(file), threading.get_ident()))
        return actual(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", lambda file, *a, **kw: observe(actual_open, file, *a, **kw))
    monkeypatch.setattr(io, "open", lambda file, *a, **kw: observe(actual_io_open, file, *a, **kw))
    return calls


async def test_configured_account_lookup_is_off_loop_even_when_channel_is_unavailable(tmp_path, monkeypatch):
    import json

    from xdog.claw.channels.weixin.auth import _account_file

    path = _account_file(tmp_path / "data", "owned-fixture")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"token": "", "group_id": "main"}))  # No account/network/credentials activated.
    config = ClawConfig(data_dir=str(tmp_path / "data"), model="test/io", socket_path=str(tmp_path / "io.sock"),
                        pid_file=str(tmp_path / "io.pid"), weixin_enabled=True, weixin_account_id="owned-fixture",
                        groups=(GroupDef(id="main", is_main=True),))
    server = GatewayServer(config)
    owner = threading.get_ident()
    observed = trace_opens(monkeypatch, path.parent)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=(
        "test/io", lambda *args: EventStream.empty(AssistantMessage())
    )):
        try:
            await server.start()
            assert observed and all(thread != owner for _path, thread in observed)
            assert not server._orchestrator._channels and server._orchestrator._runtimes["main"]._active_session is None
        finally:
            await server.stop()


async def test_channel_connect_inbound_and_disconnect_persistence_is_off_loop(tmp_path, monkeypatch):
    account = "owned-io-fixture"
    set_context_token(account, "seed", "original-token")
    persist_context_tokens(tmp_path, account)
    clear_context_tokens(account)
    _save_user_id_map(tmp_path, account, {"main": "seed"})
    channel = WeixinChannel(tmp_path, account, "https://example.invalid", "")
    await channel._api_client.close()
    api = ChannelApi()
    channel._api_client = api
    channel._typing_tickets = _TypingTicketCache(api)
    owner = threading.get_ident()
    inputs = []

    async def receive(value):
        assert threading.get_ident() == owner
        inputs.append(value)

    channel.set_on_message(receive)
    observed = trace_opens(monkeypatch, tmp_path)
    try:
        await channel.connect()
        await channel._on_inbound(text(1, "alice", "actual channel input"))
        await channel.disconnect()
        assert len(inputs) == 1 and inputs[0].content == "actual channel input"
        assert observed and all(thread != owner for _path, thread in observed), (
            "Actual channel/polling cache IO ran on its owner loop", observed
        )
    finally:
        await channel.disconnect()
        clear_context_tokens(account)
