"""Antigravity envelopes and HTTP transport; Gemini message conversion lives elsewhere."""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable
from typing import Any, TypeVar

import httpx
from xdog.ai.native import NativeEventStream, NativeResponse, NativeResponseStart, NativeSSEEvent
from xdog.ai.types import AuthExpiredError
from xdog.ai.vendors.antigravity._auth import Credentials, base_url, request_headers

T = TypeVar("T")
_MAX_JSON_RESPONSE_BYTES = 32 * 1024 * 1024


class AntigravityHTTPError(RuntimeError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"Antigravity returned HTTP {status}; check account access/quota. Request was not retried.")


async def cancellable(operation: Awaitable[T], cancel: asyncio.Event | None) -> T:
    task = asyncio.ensure_future(operation)
    stop = asyncio.create_task(cancel.wait()) if cancel is not None else None
    try:
        if stop is not None:
            done, _ = await asyncio.wait((task, stop), return_when=asyncio.FIRST_COMPLETED)
            if stop in done:
                raise asyncio.CancelledError
        return await task
    finally:
        tasks = [task] if stop is None else [task, stop]
        for pending in tasks:
            if not pending.done():
                pending.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def envelope(model: str, body: dict[str, Any], creds: Credentials) -> dict[str, Any]:
    request = dict(body)
    # Model selection belongs to the provider, not an untrusted request body.
    request.pop("model", None)
    modalities = request.get("generationConfig", {}).get("responseModalities", [])
    return {
        "model": model, "project": creds.project_id, "request": request,
        "requestId": str(uuid.uuid4()), "userAgent": "antigravity",
        "requestType": "image_gen" if "IMAGE" in modalities else "agent",
    }


def unwrap(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Invalid Antigravity response envelope.")
    result = raw.get("response", raw)
    if not isinstance(result, dict):
        raise ValueError("Invalid Antigravity generation response.")
    return result


def check_status(status: int) -> None:
    if status == 401:
        raise AuthExpiredError("Antigravity", "xdog-ai login antigravity")
    if status != 200:
        raise AntigravityHTTPError(status)


async def post_json(creds: Credentials, operation: str, body: dict[str, Any]) -> dict[str, Any]:
    if operation not in ("generateContent", "fetchAvailableModels"):
        raise ValueError("Unsupported Antigravity operation.")
    async with httpx.AsyncClient(timeout=180, follow_redirects=False) as client:
        async with client.stream(
            "POST",
            base_url() + "/v1internal:" + operation,
            headers=request_headers(creds.access_token),
            json=body,
        ) as response:
            check_status(response.status_code)
            raw = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(raw) + len(chunk) > _MAX_JSON_RESPONSE_BYTES:
                    raise ValueError("Antigravity JSON response exceeded 32 MiB.")
                raw.extend(chunk)
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Antigravity returned a non-object response.")
        return result


async def complete(creds: Credentials, model: str, body: dict[str, Any]) -> NativeResponse:
    result = unwrap(await post_json(creds, "generateContent", envelope(model, body, creds)))
    return NativeResponse(status=200, body=json.dumps(result).encode())


async def stream(
    creds: Credentials, model: str, body: dict[str, Any], cancel: asyncio.Event | None = None,
) -> NativeEventStream:
    client = httpx.AsyncClient(timeout=httpx.Timeout(180, connect=30), follow_redirects=False)
    response: httpx.Response | None = None
    try:
        request = client.build_request(
            "POST", base_url() + "/v1internal:streamGenerateContent?alt=sse",
            headers=request_headers(creds.access_token),
            json=envelope(model, body, creds),
        )
        response = await cancellable(client.send(request, stream=True), cancel)
        check_status(response.status_code)
    except BaseException:
        if response is not None:
            await response.aclose()
        await client.aclose()
        raise

    async def close() -> None:
        await response.aclose()
        await client.aclose()

    async def events() -> AsyncIterator[NativeSSEEvent]:
        lines = response.aiter_lines().__aiter__()
        data: list[str] = []
        try:
            while True:
                try:
                    line = await cancellable(anext(lines), cancel)
                except StopAsyncIteration:
                    line = ""
                    if not data:
                        break
                    # Flush an unterminated final SSE event.
                    value = "\n".join(data)
                    if value != "[DONE]":
                        yield NativeSSEEvent(None, json.dumps(unwrap(json.loads(value))).encode())
                    break
                if line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))
                elif not line and data:
                    value, data = "\n".join(data), []
                    if value == "[DONE]":
                        break
                    yield NativeSSEEvent(None, json.dumps(unwrap(json.loads(value))).encode())
        finally:
            await close()

    return NativeEventStream(NativeResponseStart(200), events(), close)
