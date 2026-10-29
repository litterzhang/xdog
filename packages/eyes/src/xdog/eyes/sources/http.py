"""Bounded HTTP collection and conditional document projection; no background loop."""

from __future__ import annotations

import asyncio
import math
import os
import re
from dataclasses import dataclass
from email.message import Message
from email.utils import parsedate_to_datetime
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from xdog.eyes.errors import SourceError
from xdog.eyes.models import FeedSourceDef, HttpSourceDef, RemoteSourceDef
from xdog.eyes.sources.documents import parse_owned
from xdog.eyes.sources.records import PollResult, Record, RecordSnapshot, decode, pointer, record, unique


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str = ""


def origin(url: str) -> tuple[str, str | None, int]:
    value = urlsplit(url)
    return value.scheme, value.hostname, value.port or (443 if value.scheme == "https" else 80)


def within_path(url: str, prefix: str) -> bool:
    path = unquote(urlsplit(url).path)
    return path.startswith(prefix) and not any(part in {".", ".."} for part in path.split("/")) and "\\" not in path


def credential(source: RemoteSourceDef) -> str | None:
    if source.bearer_env is None:
        return None
    token = os.environ.get(source.bearer_env)
    if not token or len(token) > 16384 or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
        raise SourceError("Configured bearer environment variable is missing or invalid")
    # Plaintext credentials must not travel over an insecure connection.
    url = source.url if isinstance(source, (HttpSourceDef, FeedSourceDef)) else source.api_url
    if urlsplit(url).scheme != "https":
        raise SourceError("Bearer credentials require HTTPS")
    return token


def redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text


def backoff(response: Response, *, now: float, attempt: int) -> float:
    retry = response.headers.get("retry-after", "")
    try:
        delay = float(retry) if retry.isdecimal() else parsedate_to_datetime(retry).timestamp() - now
        if delay >= 0:
            return max(1.0, delay) if math.isfinite(delay) else 3600.0
    except (ValueError, TypeError, OverflowError):
        pass
    if response.headers.get("x-ratelimit-remaining") == "0":
        try:
            delay = float(response.headers["x-ratelimit-reset"]) - now
            return max(1.0, delay) if math.isfinite(delay) else 3600.0
        except (ValueError, KeyError, OverflowError):
            pass
    return float(min(3600.0, 5.0 * 2 ** min(attempt, 10)))


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie: Cookie, request: object) -> bool:
        return False  # Sources never borrow another source's implicit login session.


class HttpTransport:
    """One observation-owned connection pool, created lazily and explicitly closed."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def get(
        self, url: str, headers: dict[str, str], *, max_bytes: int, path_prefix: str | None = None
    ) -> Response:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                trust_env=True,
                cookies=CookieJar(policy=_NoCookies()),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                timeout=30,
            )
        original = url
        for _ in range(4):
            async with self._client.stream("GET", url, headers=headers) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    target = urljoin(url, response.headers.get("location", ""))
                    parsed = urlsplit(target)
                    if (
                        not response.headers.get("location")
                        or parsed.username
                        or parsed.password
                        or origin(target) != origin(original)
                        or path_prefix is not None
                        and not within_path(target, path_prefix)
                    ):
                        raise SourceError("Unsafe or cross-origin redirect refused")
                    # Validators belong to a representation, not every URL on
                    # the same origin. Never forward them to a redirect target.
                    headers = {key: value for key, value in headers.items()
                               if key.lower() not in {'if-none-match', 'if-modified-since'}}
                    url = target
                    continue
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise SourceError("HTTP decoded body exceeds max_bytes; no snapshot committed")
                    chunks.append(chunk)
                return Response(response.status_code, dict(response.headers), b"".join(chunks), url)
        raise SourceError("HTTP redirect limit exceeded")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _records(source: HttpSourceDef | FeedSourceDef, response: Response, secret: str | None) -> tuple[Record, ...]:
    if isinstance(source, FeedSourceDef):
        from xdog.eyes.sources.feed import records as feed_records

        return feed_records(response.body, source, response.url or source.url, secret)
    if source.mode == "html":
        from xdog.eyes.sources.html import records as html_records

        content_type = Message()
        content_type['content-type'] = response.headers.get('content-type', '')
        return html_records(response.body, source, response.url or source.url, secret,
                            content_type.get_content_charset())
    if source.mode == "status":
        return (
            record(
                "$", {"status_code": response.status}, source.url, secret=secret
            ),
        )
    value = pointer(decode(response.body), source.pointer)
    if source.key_pointer is None:
        return (record("$", value, source.url, secret=secret),)
    if not isinstance(value, list) or len(value) > source.max_records:
        raise ValueError("Expected bounded record array")
    result = []
    for item in value:
        key = pointer(item, source.key_pointer)
        if type(key) not in {str, int}:
            raise ValueError("Expected stable scalar key")
        result.append(record(str(key), item, source.url, secret=secret))
    return unique(tuple(result), source.max_records)


async def poll(
    source: HttpSourceDef | FeedSourceDef, state: dict[str, Any], transport: HttpTransport, *, now: float
) -> PollResult:
    secret = credential(source)
    mode = source.mode if isinstance(source, HttpSourceDef) else "feed"
    accept = {
        "json": "application/json",
        "html": "text/html",
        "feed": "application/atom+xml, application/rss+xml, application/xml",
    }
    headers = {"accept": accept.get(mode, "*/*")}
    if secret:
        headers["authorization"] = "Bearer " + secret
    if mode != "status":
        for field, name in [("etag", "if-none-match"), ("last_modified", "if-modified-since")]:
            if isinstance(state.get(field), str):
                headers[name] = state[field]
    async with asyncio.timeout(source.timeout_seconds):
        response = await transport.get(source.url, headers, max_bytes=source.max_bytes)
        healthy = 200 <= response.status < 300 or response.status == 304 and mode != "status"
        records: tuple[Record, ...] = ()
        if healthy and response.status != 304 or mode == "status":
            try:
                # Parsing never occupies the daemon control loop. Cancellation
                # joins the actual bounded parser, not a detached background job.
                records = await parse_owned(lambda: _records(source, response, secret))
            except SourceError:
                raise
            except (ValueError, KeyError, IndexError, TypeError, UnicodeError, RecursionError, SyntaxError) as exc:
                raise SourceError("HTTP document selection/shape is invalid; previous baseline retained") from exc
    next_state = {**state, "last_status": response.status}
    attempts = int(state.get("failures", 0))
    delay = source.poll_interval_seconds
    if not healthy:
        next_state["failures"] = attempts + 1
        delay = max(delay, backoff(response, now=now, attempt=attempts))
        return PollResult(
            RecordSnapshot(
                records,
                now,
                healthy=False,
                complete=mode == "status",
                status_code=response.status,
                error=f"HTTP status {response.status}",
                valid=mode == "status",
            ),
            next_state,
            delay,
        )
    if response.status == 304:
        if not state.get("baselined"):
            raise SourceError("HTTP 304 without a committed baseline")
        next_state.update(last_success=now, failures=0)
        return PollResult(RecordSnapshot((), now, unchanged=True, status_code=304), next_state, delay)
    if mode != "status":
        for field, header in [("etag", "etag"), ("last_modified", "last-modified")]:
            next_state.pop(field, None)
            if header in response.headers and (not response.url or response.url == source.url):
                validator = response.headers[header]
                if (
                    len(validator) <= 1024
                    and all(32 <= ord(char) < 127 for char in validator)
                    and not (secret and secret in validator)
                ):
                    next_state[field] = validator
    next_state.update(last_success=now, failures=0, baselined=True)
    return PollResult(
        RecordSnapshot(
            records, now, status_code=response.status, complete=mode != "feed", retain_current=mode == "feed"
        ),
        next_state,
        delay,
    )
