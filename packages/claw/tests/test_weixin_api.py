"""Tests for WeChat API client."""
from __future__ import annotations

import json

import httpx
import pytest
from xdog.claw.channels.weixin.api import (
    get_updates,
    send_message,
)
from xdog.claw.channels.weixin.types import (
    MessageItem,
    MessageItemType,
    MessageState,
    MessageType,
    SendMessageReq,
    TextItem,
    WeixinMessage,
)


@pytest.mark.asyncio
async def test_get_updates_with_mocked_response(httpx_mock):
    """Test getUpdates parses a response with messages."""
    mock_response = {
        "ret": 0,
        "errcode": 0,
        "msgs": [
            {
                "from_user_id": "user123@im.wechat",
                "to_user_id": "bot456@im.bot",
                "message_type": 1,
                "item_list": [
                    {
                        "type": 1,
                        "text_item": {"text": "Hello bot!"},
                    }
                ],
                "context_token": "ctx-abc",
            }
        ],
        "get_updates_buf": "new-buf-data",
        "longpolling_timeout_ms": 35000,
    }

    httpx_mock.add_response(
        url="https://ilinkai.weixin.qq.com/ilink/bot/getupdates",
        method="POST",
        json=mock_response,
    )

    resp = await get_updates(
        base_url="https://ilinkai.weixin.qq.com",
        token="test-token",
        get_updates_buf="old-buf",
    )

    assert resp.ret == 0
    assert len(resp.msgs) == 1
    msg = resp.msgs[0]
    assert msg.from_user_id == "user123@im.wechat"
    assert msg.context_token == "ctx-abc"
    assert len(msg.item_list) == 1
    assert msg.item_list[0].type == MessageItemType.TEXT
    assert msg.item_list[0].text_item.text == "Hello bot!"
    assert resp.get_updates_buf == "new-buf-data"

@pytest.mark.asyncio
async def test_get_updates_timeout_returns_empty(httpx_mock):
    """Client-side timeout should return an empty response, not raise."""
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"))

    resp = await get_updates(
        base_url="https://ilinkai.weixin.qq.com",
        token="test-token",
        get_updates_buf="old-buf",
        timeout_ms=100,
    )

    assert resp.ret == 0
    assert resp.get_updates_buf == "old-buf"
    assert len(resp.msgs) == 0

@pytest.mark.asyncio
async def test_send_message_with_mocked_httpx(httpx_mock):
    """Test sendMessage calls the correct endpoint."""
    httpx_mock.add_response(
        url="https://ilinkai.weixin.qq.com/ilink/bot/sendmessage",
        method="POST",
        json={},
    )

    req = SendMessageReq(
        msg=WeixinMessage(
            to_user_id="user123@im.wechat",
            client_id="test-client-1",
            message_type=MessageType.BOT,
            message_state=MessageState.FINISH,
            item_list=(
                MessageItem(
                    type=MessageItemType.TEXT,
                    text_item=TextItem(text="Hello from bot"),
                ),
            ),
            context_token="ctx-abc",
        )
    )

    await send_message(
        base_url="https://ilinkai.weixin.qq.com",
        token="test-token",
        body=req,
    )

    # Verify the request was made
    request = httpx_mock.get_request()
    assert request is not None
    body = json.loads(request.content)
    assert body["msg"]["to_user_id"] == "user123@im.wechat"
    assert body["msg"]["context_token"] == "ctx-abc"

# ---------------------------------------------------------------------------
# pytest-httpx fixture (if not available, tests are skipped)
# ---------------------------------------------------------------------------

@pytest.fixture
def httpx_mock():
    """Simple httpx mock using respx or manual mock."""
    import importlib.util
    if importlib.util.find_spec("pytest_httpx") is not None:
        # If pytest_httpx is available, it provides the fixture automatically.
        pass

    # Fallback: inline mock
    class _MockTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self._responses: list[dict] = []
            self._exceptions: list[Exception] = []
            self._requests: list[httpx.Request] = []

        def add_response(self, url: str, method: str = "POST", json: dict | None = None):
            self._responses.append({"url": url, "method": method, "json": json})

        def add_exception(self, exc: Exception):
            self._exceptions.append(exc)

        def get_request(self) -> httpx.Request | None:
            return self._requests[0] if self._requests else None

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self._requests.append(request)
            if self._exceptions:
                raise self._exceptions.pop(0)
            if self._responses:
                resp_data = self._responses.pop(0)
                body = json.dumps(resp_data.get("json", {})).encode()
                return httpx.Response(200, content=body, headers={"content-type": "application/json"})
            return httpx.Response(200, content=b"{}")

    transport = _MockTransport()

    # Monkey-patch httpx.AsyncClient to use our transport
    original_init = httpx.AsyncClient.__init__

    def patched_init(self_client, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("timeout", None)
        original_init(self_client, *args, timeout=httpx.Timeout(30), **kwargs)

    httpx.AsyncClient.__init__ = patched_init

    yield transport

    httpx.AsyncClient.__init__ = original_init


@pytest.mark.parametrize("payload", [{"ret": -1}, {"ret": 0, "errcode": 400}, []])
async def test_client_rejects_application_delivery_errors(payload):
    from xdog.claw.channels.weixin.api import WeixinApiClient

    client = WeixinApiClient(base_url="https://test.invalid", token="fake")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload),
    ))
    try:
        with pytest.raises(RuntimeError):
            await client.send_message(SendMessageReq(msg=WeixinMessage(to_user_id="fake")))
    finally:
        await client.close()
