"""HTTP API client for the WeChat ilink bot API.

Ported from openclaw-weixin src/api/api.ts.
Uses httpx.AsyncClient for async HTTP.
"""
from __future__ import annotations

import base64
import logging
import os
import struct
from typing import Any

import httpx
from xdog.claw.channels.weixin.types import (
    GetUpdatesResp,
    SendMessageReq,
    parse_get_updates_resp,
    send_message_req_to_dict,
)

logger = logging.getLogger(__name__)

CHANNEL_VERSION = "claw-0.1.0"
DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
DEFAULT_API_TIMEOUT_MS = 15_000


def _ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else f"{url}/"


def _random_wechat_uin() -> str:
    """X-WECHAT-UIN header: random uint32 → decimal string → base64."""
    raw = os.urandom(4)
    uint32 = struct.unpack(">I", raw)[0]
    return base64.b64encode(str(uint32).encode("utf-8")).decode("ascii")


def build_headers(token: str) -> dict[str, str]:
    """Build authentication headers for the ilink bot API."""
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
    }
    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def _build_base_info() -> dict[str, Any]:
    return {"channel_version": CHANNEL_VERSION}


class WeixinApiClient:
    """Shared HTTP client for the ilink bot API.

    Reuses a single ``httpx.AsyncClient`` for connection pooling instead
    of creating one per request.
    """

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url
        self._token = token
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self, timeout_s: float) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=timeout_s)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            # `aclose`, not `close`: httpx names the async one differently, and
            # the sync spelling is simply absent from AsyncClient.
            await self._client.aclose()
            self._client = None

    async def get_updates(
        self,
        get_updates_buf: str = "",
        timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS,
    ) -> GetUpdatesResp:
        """Long-poll getUpdates. Returns empty response on client-side timeout."""
        url = f"{_ensure_trailing_slash(self._base_url)}ilink/bot/getupdates"
        headers = build_headers(self._token)
        body = {
            "get_updates_buf": get_updates_buf,
            "base_info": _build_base_info(),
        }
        timeout_s = timeout_ms / 1000.0

        try:
            client = await self._get_client(timeout_s)
            resp = await client.post(url, json=body, headers=headers, timeout=timeout_s)
            resp.raise_for_status()
            return parse_get_updates_resp(resp.json())
        except httpx.TimeoutException:
            logger.debug(
                "getUpdates: client-side timeout after %dms, returning empty response",
                timeout_ms,
            )
            return GetUpdatesResp(ret=0, get_updates_buf=get_updates_buf)
        except Exception:
            logger.exception("getUpdates: request failed")
            raise

    async def send_message(
        self,
        body: SendMessageReq,
        timeout_ms: int = DEFAULT_API_TIMEOUT_MS,
    ) -> None:
        """Send a single message downstream."""
        url = f"{_ensure_trailing_slash(self._base_url)}ilink/bot/sendmessage"
        headers = build_headers(self._token)
        payload = send_message_req_to_dict(body)
        payload["base_info"] = _build_base_info()
        timeout_s = timeout_ms / 1000.0

        client = await self._get_client(timeout_s)
        resp = await client.post(url, json=payload, headers=headers, timeout=timeout_s)
        resp.raise_for_status()
        _check_send_response(resp)

    async def send_typing(
        self,
        ilink_user_id: str,
        typing_ticket: str = "",
        status: int = 1,
        timeout_ms: int = DEFAULT_API_TIMEOUT_MS,
    ) -> None:
        """Send a typing indicator to a user."""
        url = f"{_ensure_trailing_slash(self._base_url)}ilink/bot/sendtyping"
        headers = build_headers(self._token)
        body = {
            "ilink_user_id": ilink_user_id,
            "typing_ticket": typing_ticket,
            "status": status,
            "base_info": _build_base_info(),
        }
        timeout_s = timeout_ms / 1000.0

        try:
            client = await self._get_client(timeout_s)
            resp = await client.post(url, json=body, headers=headers, timeout=timeout_s)
            resp.raise_for_status()
        except Exception:
            logger.debug("sendTyping: request failed (non-critical)")

    async def get_config(
        self,
        ilink_user_id: str,
        context_token: str = "",
        timeout_ms: int = DEFAULT_API_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Fetch bot config for a user (includes typing_ticket)."""
        url = f"{_ensure_trailing_slash(self._base_url)}ilink/bot/getconfig"
        headers = build_headers(self._token)
        body = {
            "ilink_user_id": ilink_user_id,
            "context_token": context_token,
            "base_info": _build_base_info(),
        }
        timeout_s = timeout_ms / 1000.0

        try:
            client = await self._get_client(timeout_s)
            resp = await client.post(url, json=body, headers=headers, timeout=timeout_s)
            resp.raise_for_status()
            config: dict[str, Any] = resp.json()
            return config
        except Exception:
            logger.debug("getConfig: request failed (non-critical)")
            return {}


# ---------------------------------------------------------------------------
# Module-level convenience functions (create one-shot clients for callers
# that don't manage a WeixinApiClient — e.g. tests, CLI commands).
# ---------------------------------------------------------------------------


async def get_updates(
    base_url: str,
    token: str,
    get_updates_buf: str = "",
    timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS,
) -> GetUpdatesResp:
    """Long-poll getUpdates. Returns empty response on client-side timeout."""
    url = f"{_ensure_trailing_slash(base_url)}ilink/bot/getupdates"
    headers = build_headers(token)
    body = {
        "get_updates_buf": get_updates_buf,
        "base_info": _build_base_info(),
    }
    timeout_s = timeout_ms / 1000.0

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            return parse_get_updates_resp(resp.json())
    except httpx.TimeoutException:
        logger.debug(
            "getUpdates: client-side timeout after %dms, returning empty response",
            timeout_ms,
        )
        return GetUpdatesResp(ret=0, get_updates_buf=get_updates_buf)
    except Exception:
        logger.exception("getUpdates: request failed")
        raise


async def send_message(
    base_url: str,
    token: str,
    body: SendMessageReq,
    timeout_ms: int = DEFAULT_API_TIMEOUT_MS,
) -> None:
    """Send a single message downstream."""
    url = f"{_ensure_trailing_slash(base_url)}ilink/bot/sendmessage"
    headers = build_headers(token)
    payload = send_message_req_to_dict(body)
    payload["base_info"] = _build_base_info()
    timeout_s = timeout_ms / 1000.0

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        _check_send_response(resp)


def _check_send_response(response: httpx.Response) -> None:
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid Weixin send response")
    if payload.get("ret", 0) != 0 or payload.get("errcode", 0) != 0:
        raise RuntimeError(f"Weixin delivery rejected: {payload}")
