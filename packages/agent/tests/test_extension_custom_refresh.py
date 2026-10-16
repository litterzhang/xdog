from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import DialogBroker
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.stdio import StdioClient


def request(kind='resize', *, width=40, height=5, painted=-1):
    result = {'kind': kind, 'width': width, 'height': height, 'painted': painted}
    if kind == 'key':
        result.update(key='enter', ctrl=False, alt=False, shift=False)
    return result


def dialog():
    return {'id': 'worker', 'view_id': 'view-1', 'kind': 'custom', 'title': 'Custom view'}


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_ordinary_current_mcp_rejects_private_extension_repaint_notification(tmp_path):
    client = StdioClient(McpServerConfig('peer', ('unused',)), tmp_path)
    client.modern = True
    client.state = 'connected'
    stdout = asyncio.StreamReader()
    client.process = SimpleNamespace(stdout=stdout)
    stdout.feed_data((json.dumps({'jsonrpc': '2.0', 'method': 'xdog/custom_refresh', 'params': {
        'call_id': 'one', 'dialog_id': 'two', 'view_id': 'three'}}) + '\n').encode())
    await client._read()
    assert client.state == 'disconnected' and client.error == 'McpError'


def test_repaint_api_and_broker_transport_are_removed():
    import importlib.util
    assert not hasattr(ExtensionAPI({}), 'request_render')
    assert not hasattr(DialogBroker(), 'refresh_custom')
    assert not hasattr(DialogBroker(), 'request_custom')
    assert importlib.util.find_spec('xdog.agent.extensions.custom_broker') is None
