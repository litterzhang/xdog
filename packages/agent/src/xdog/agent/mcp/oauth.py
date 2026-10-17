"""Explicit pre-registered public-client PKCE; no browser launch, credential disk writes or POST replay.

Issuer/client/scopes are operator-selected. Discovery cannot activate arbitrary
issuers or forward existing MCP headers. Tokens belong to one exact resource URL.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx

_LIMIT = 64 * 1024


class OAuthError(ValueError):
    """Safe public error: remote bodies, auth codes, tokens and verifier never become errors."""


class MetadataAbsent(OAuthError):
    """Only an absent metadata GET route permits well-known fallback; never token POST replay."""


def origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise OAuthError("Invalid OAuth endpoint port") from exc
    return parsed.scheme, parsed.hostname or "", port


def endpoint(value: Any, *, https: bool = True) -> str:
    if (not isinstance(value, str) or not value or len(value.encode()) > 8192
            or any(ord(char) < 33 or ord(char) == 127 for char in value)):
        raise OAuthError("Invalid OAuth endpoint")
    parsed = urlsplit(value)
    if (parsed.scheme not in ({"https"} if https else {"https", "http"}) or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.query):
        raise OAuthError("OAuth endpoints must be explicit credential-free HTTPS URLs")
    origin(value)
    return value


def scopes(value: Any, *, allow_empty: bool = False) -> tuple[str, ...]:
    if (not isinstance(value, list) or not (0 if allow_empty else 1) <= len(value) <= 16
            or any(not isinstance(item, str) or not item or len(item) > 128
                   or any(ord(char) < 33 or ord(char) > 126 or char in {'"', '\\'} for char in item) for item in value)
            or len(set(value)) != len(value)):
        raise OAuthError("OAuth scopes must be 1–16 explicit distinct scope strings")
    return tuple(value)


def client_identifier(value: Any) -> str:
    if (not isinstance(value, str) or not 0 < len(value) <= 512
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise OAuthError("OAuth requires an explicit pre-registered client ID")
    return value


@dataclass(frozen=True, slots=True)
class OAuthConfig:
    issuer: str
    client_id: str
    scopes: tuple[str, ...]
    callback_port: int = 0
    ca_file: str = ""
    metadata_origins: tuple[str, ...] = ()
    registration: str = "pre-registered"
    client_name: str = "XDOG"
    token_auth_method: str = "none"
    client_secret_env: str = ""

    @classmethod
    def parse(cls, value: Any) -> OAuthConfig:
        if not isinstance(value, dict) or set(value) - {
                "issuer", "client_id", "scopes", "callback_port", "ca_file", "metadata_origins",
                "registration", "client_name", "token_auth_method", "client_secret_env"}:
            raise OAuthError("Invalid/unsupported OAuth configuration")
        issuer = endpoint(value.get("issuer"))
        from xdog.agent.mcp.oauth_registration import client_name
        registration = value.get("registration", "pre-registered")
        from xdog.agent.mcp.oauth_client_auth import validate
        method, variable = validate(value.get("token_auth_method", "none"),
                                    value.get("client_secret_env", ""), registration)
        client_id = client_identifier(value.get("client_id"))
        display_name = client_name(value.get("client_name", "XDOG"))
        port = value.get("callback_port", 0)
        if type(port) is not int or not 0 <= port <= 65535:
            raise OAuthError("Invalid OAuth callback port")
        ca = value.get("ca_file", "")
        if not isinstance(ca, str) or ca and (not Path(ca).expanduser().is_absolute() or '\x00' in ca):
            raise OAuthError("OAuth CA file must be an explicit absolute path")
        origins = value.get("metadata_origins", [])
        if not isinstance(origins, list) or len(origins) > 8:
            raise OAuthError("OAuth metadata_origins must be at most eight explicit HTTPS origins")
        approved = []
        for item in origins:
            url = endpoint(item)
            if urlsplit(url).path not in {"", "/"} or origin(url) in approved:
                raise OAuthError("OAuth metadata origins must be distinct origin-only HTTPS URLs")
            approved.append(origin(url))
        return cls(issuer, client_id, scopes(value.get("scopes")), port, ca, tuple(origins),
                   registration, display_name, method, variable)

    def __post_init__(self) -> None:
        self.validate_public()

    def validate_public(self) -> None:
        """Pure validation, also used before CA, discovery and owned-flow I/O."""
        from xdog.agent.mcp.oauth_client_auth import validate
        validate(self.token_auth_method, self.client_secret_env, self.registration)
        client_identifier(self.client_id)

    def verify(self) -> bool | ssl.SSLContext:
        self.validate_public()
        return ssl.create_default_context(cafile=str(Path(self.ca_file).expanduser())) if self.ca_file else True


@dataclass(frozen=True, slots=True)
class OAuthReview:
    id: str
    issuer: str
    resource: str
    client_id: str
    scopes: tuple[str, ...]
    authorize: str
    token_endpoint: str
    response_issuer: bool = False
    tls_trust: str = "system"
    registration: str = "pre-registered"
    registration_endpoint: str = ""
    client_name: str = "XDOG"
    callback_port: int = 0
    token_auth_method: str = "none"
    client_secret_env: str = ""
    created: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, Any]:
        return {"review_id": self.id, "issuer": self.issuer, "resource": self.resource,
                "client_id": self.client_id, "scopes": list(self.scopes),
                "authorization_endpoint": self.authorize, "token_endpoint": self.token_endpoint,
                "tls_trust": self.tls_trust, "registration": self.registration,
                "registration_endpoint": self.registration_endpoint, "client_name": self.client_name,
                "callback_port": self.callback_port, "client_assigned": bool(self.client_id),
                "token_auth_method": self.token_auth_method, "client_secret_env": self.client_secret_env,
                "notice": "Resource-bound credentials; NOT tool permission. No browser auto-launch."}


@dataclass(slots=True)
class _Flow:
    review: OAuthReview
    state: str
    verifier: str
    redirect: str
    code: asyncio.Future[str]
    listener: asyncio.AbstractServer
    task: asyncio.Task[None] | None = None
    handlers: set[asyncio.Task[Any]] = field(default_factory=set)
    status: str = "waiting"
    error: str = ""
    url: str = ""


class OAuthOwner:
    """Call on the manager's owned loop. Public snapshots never contain bearer credentials."""
    def __init__(self, resource: str, config: OAuthConfig) -> None:
        config.validate_public()
        self.resource = endpoint(resource, https=False)
        if origin(resource)[0] == "http" and origin(resource)[1] not in {"127.0.0.1", "localhost", "::1"}:
            raise OAuthError("OAuth credentials cannot be used over non-loopback plaintext HTTP")
        self.config = config
        self.review: OAuthReview | None = None
        self.flow: _Flow | None = None
        self._token = ""
        self._refresh = ""
        self._grant: OAuthReview | None = None
        self._refresh_review: OAuthReview | None = None
        self._refresh_state = "idle"
        self._refresh_task: asyncio.Task[Any] | None = None
        self._token_target = ""
        self._expires = 0.0
        self._closed = False
        self._operation = asyncio.Lock()
        self.guard: Callable[[], bool] = lambda: True

    def live(self) -> bool:
        try:
            return bool(self.guard())
        except Exception:
            return False

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(verify=self.config.verify(), trust_env=False, follow_redirects=False,
                                 timeout=15, limits=httpx.Limits(max_connections=2))

    async def _json(self, client: httpx.AsyncClient, method: str, url: str,
                    data: dict[str, str] | None = None, *, document: dict[str, Any] | None = None,
                    accepted: tuple[int, ...] = (200,), token_headers: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            client.cookies.clear()  # Discovery cookies cannot become cross-origin authorization credentials.
            headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
            self.config.validate_public()
            if token_headers or (data is not None and any(
                    key in data for key in ("client_secret", "client_assertion", "client_assertion_type"))):
                raise OAuthError("Only public OAuth clients are supported; confidential credentials refused")
            if document is not None:
                raise OAuthError("OAuth client registration is not supported")
            if method == "POST" and (self._closed or not self.live() or not self._token_target
                    or url != self._token_target or origin(url) != origin(self.config.issuer)):
                raise OAuthError("Token request cannot leave its exact pinned token operation")
            async with client.stream(method, url, data=data, json=document, headers=headers) as response:
                if method == "GET" and response.status_code in {404, 405}:
                    raise MetadataAbsent("OAuth metadata route not provided")
                if response.status_code not in accepted:
                    raise OAuthError("OAuth metadata/token endpoint refused request; no redirects or replay")
                if (response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
                        or response.headers.get("content-encoding", "identity").lower() != "identity"):
                    raise OAuthError("OAuth response must be uncompressed JSON")
                body = bytearray()
                async for block in response.aiter_bytes():
                    body.extend(block)
                    if len(body) > _LIMIT:
                        raise OAuthError("OAuth response exceeds retention limit")
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise OAuthError("Invalid OAuth response object")
                return value
        except (httpx.HTTPError, json.JSONDecodeError, UnicodeError) as exc:
            raise OAuthError("OAuth network/response failed; secrets were not logged or replayed") from exc

    async def prepare(self, hint: Any = None) -> dict[str, Any]:
        async with self._operation:
            return await self._prepare(hint)

    async def _discover(self, client: httpx.AsyncClient, candidates: tuple[str, ...]) -> tuple[dict[str, Any], str]:
        for url in candidates:
            if self._closed or not self.live():
                raise OAuthError("OAuth discovery owner changed; no fallback was attempted")
            try:
                return await self._json(client, "GET", url), url
            except MetadataAbsent:
                continue
        raise OAuthError("No supported OAuth metadata discovery route was provided")

    async def _prepare(self, hint: Any) -> dict[str, Any]:
        if self._closed:
            raise OAuthError("OAuth owner closed")
        if self.flow is not None and self.flow.status == "waiting":
            raise OAuthError("Finish/cancel pending OAuth before preparing another review")
        self.review = None
        self.config.validate_public()
        from xdog.agent.mcp.oauth_discovery import OAuthChallenge, issuer_candidates, resource_candidates
        if hint is not None and not isinstance(hint, OAuthChallenge):
            raise OAuthError("Invalid retained resource challenge")
        async with asyncio.timeout(30), self.client() as client:
            resource_metadata, metadata_url = await self._discover(client, resource_candidates(self.resource, hint,
                trusted_origins=(self.config.issuer, *self.config.metadata_origins)))
            if resource_metadata.get("resource") != self.resource:
                raise OAuthError("OAuth protected-resource identity mismatch")
            servers = resource_metadata.get("authorization_servers")
            if not isinstance(servers, list) or self.config.issuer not in servers:
                raise OAuthError("Configured OAuth issuer is not advertised by this resource")
            advertised = resource_metadata.get("scopes_supported")
            if advertised is not None and (not isinstance(advertised, list) or len(advertised) > 128
                                           or any(not isinstance(item, str) for item in advertised)):
                raise OAuthError("Invalid advertised OAuth scopes")
            # Challenges are operation-specific; do not require a subset relation
            # with scopes_supported. Operator scopes remain a hard request ceiling.
            requested = (hint.scopes if hint is not None and hint.scopes is not None else
                         scopes(advertised, allow_empty=True) if advertised is not None else self.config.scopes)
            if not set(requested) <= set(self.config.scopes):
                raise OAuthError("Required scopes exceed operator configuration; review/update the profile explicitly")
            metadata, authorization_url = await self._discover(client, issuer_candidates(self.config.issuer))
        if metadata.get("issuer") != self.config.issuer:
            raise OAuthError("OAuth issuer metadata identity mismatch")
        authorize, token = endpoint(metadata.get("authorization_endpoint")), endpoint(metadata.get("token_endpoint"))
        if origin(authorize) != origin(self.config.issuer) or origin(token) != origin(self.config.issuer):
            raise OAuthError("OAuth endpoints must remain on the operator-pinned issuer origin")
        def supports(key: str, choice: str) -> bool:
            values = metadata.get(key)
            return (isinstance(values, list) and len(values) <= 32
                    and all(isinstance(item, str) for item in values) and choice in values)
        if not supports("code_challenge_methods_supported", "S256"):
            raise OAuthError("OAuth issuer must explicitly support S256 PKCE")
        if not supports("response_types_supported", "code"):
            raise OAuthError("OAuth issuer must advertise authorization-code responses")
        methods = metadata.get("token_endpoint_auth_methods_supported")
        if (not isinstance(methods, list) or self.config.token_auth_method not in methods
                or any(not isinstance(item, str) for item in methods)):
            raise OAuthError("Configured OAuth token authentication is not advertised; no method fallback")
        response_issuer = metadata.get("authorization_response_iss_parameter_supported", False)
        if type(response_issuer) is not bool:
            raise OAuthError("Invalid OAuth response-issuer capability")
        if self._closed or not self.live():
            raise OAuthError("OAuth review owner changed/closed during discovery")
        self.review = OAuthReview(secrets.token_hex(16), self.config.issuer, self.resource, self.config.client_id,
                                  requested, authorize, token, response_issuer,
                                  self.config.ca_file or "system", self.config.registration,
                                  "", self.config.client_name, self.config.callback_port,
                                  self.config.token_auth_method, self.config.client_secret_env)
        return {**self.review.to_dict(), "resource_metadata": metadata_url, "issuer_metadata": authorization_url,
                "scope_source": "challenge" if hint is not None and hint.scopes is not None else
                                "metadata" if advertised is not None else "operator"}

    def status(self) -> dict[str, Any]:
        state = (self.flow.status if self.flow is not None else
                 "authorized" if self._token else "expired" if self._grant is not None else "idle")
        if self._token and time.monotonic() >= self._expires:
            self._token = ""
            if self.flow is not None and self.flow.status == "authorized":
                self.flow.status = "expired"
            state = self.flow.status if self.flow is not None else "expired"
        return {"state": state, "error": self.flow.error if self.flow is not None else "",
                "issuer": self.config.issuer, "resource": self.resource,
                "expires_in": max(0, int(self._expires - time.monotonic())) if self._token else 0,
                "refresh_available": bool(self._refresh and self._grant), "refresh_state": self._refresh_state}

    def credential(self) -> str | None:
        if self._closed or not self._token:
            return None
        if time.monotonic() >= self._expires:
            self._token = ""
            if self.flow is not None and self.flow.status == "authorized":
                self.flow.status = "expired"
            return None
        return self._token

    async def start(self, review_id: str) -> dict[str, Any]:
        async with self._operation:
            return await self._start(review_id)

    async def _start(self, review_id: str) -> dict[str, Any]:
        review = self.review
        if (self._closed or review is None or not isinstance(review_id, str)
                or len(review_id) != 32 or any(char not in "0123456789abcdef" for char in review_id)
                or time.monotonic() - review.created > 300 or not secrets.compare_digest(review.id, review_id)):
            raise OAuthError("OAuth review changed/unavailable; inspect and confirm a fresh review")
        if self.flow is not None and self.flow.status == "waiting":
            raise OAuthError("OAuth authorization already pending")
        if not self.live():
            raise OAuthError("OAuth consent owner changed; prepare a fresh review")
        await self._cancel()
        self.review = None  # A confirmation receipt is single-use, not a repeatable browser/code action.
        self.config.validate_public()
        validate_review(review.to_dict(), prepared=True)
        if self._closed or not self.live():
            raise OAuthError("OAuth consent owner changed; prepare fresh consent")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        code: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        flow_ref: list[_Flow] = []
        async def callback(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            if not flow_ref or len(flow_ref[0].handlers) >= 8:
                # start_server may accept a local connection before its await
                # returns. No unowned request may acquire a transaction/code.
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except (OSError, asyncio.TimeoutError):
                    pass
                return
            task = asyncio.current_task()
            assert task is not None
            flow = flow_ref[0]
            flow.handlers.add(task)
            accepted = False
            try:
                async with asyncio.timeout(3):
                    request = await reader.readuntil(b"\r\n\r\n")
                if len(request) > 8192:
                    raise OAuthError("OAuth callback exceeds input limit")
                line = request.split(b"\r\n", 1)[0].decode("ascii")
                method, target, protocol = line.split(" ")
                url = urlsplit(target)
                if (method != "GET" or protocol != "HTTP/1.1" or url.scheme or url.netloc
                        or url.path != "/callback" or url.fragment):
                    raise OAuthError("Invalid OAuth callback")
                args = parse_qs(url.query, keep_blank_values=True, max_num_fields=8)
                if any(len(values) != 1 for values in args.values()):
                    raise OAuthError("Duplicate OAuth callback fields")
                received_state = args.get("state", [""])[0]
                if (not received_state.isascii() or len(received_state) != len(flow.state)
                        or not secrets.compare_digest(received_state, flow.state) or flow.code.done()):
                    raise OAuthError("OAuth callback is stale or belongs to another request")
                issuer_value = args.get("iss", [""])[0]
                if issuer_value and issuer_value != review.issuer or review.response_issuer and not issuer_value:
                    raise OAuthError("OAuth callback issuer mismatch")
                value = args.get("code", [""])[0]
                if (args.get("error") or not value or len(value) > 4096
                        or any(ord(c) < 33 or ord(c) > 126 for c in value)):
                    if not flow.code.done():
                        flow.code.set_exception(OAuthError("OAuth authorization refused or invalid code"))
                else:
                    flow.code.set_result(value)
                    accepted = True
            except (ValueError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError,
                    asyncio.LimitOverrunError):
                pass  # A foreign/invalid request cannot consume the valid pending authorization.
            finally:
                body = b"Authorization received. Return to XDOG." if accepted else b"Authorization callback refused."
                try:
                    writer.write((b"HTTP/1.1 200 OK\r\n" if accepted else b"HTTP/1.1 400 Bad Request\r\n")
                                 + b"Content-Type: text/plain\r\nCache-Control: no-store\r\nConnection: close\r\n"
                                 + b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                    await asyncio.wait_for(writer.drain(), 1)
                except (OSError, asyncio.TimeoutError):
                    pass
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1)
                except (OSError, asyncio.TimeoutError):
                    pass
                finally:
                    flow.handlers.discard(task)
        listener = await asyncio.start_server(callback, "127.0.0.1", self.config.callback_port, limit=8192)
        if not self.live() or self._closed:
            listener.close()
            await listener.wait_closed()
            code.cancel()
            raise OAuthError("OAuth consent owner changed before listener admission")
        port = listener.sockets[0].getsockname()[1]
        redirect = f"http://127.0.0.1:{port}/callback"
        flow = _Flow(review, state, verifier, redirect, code, listener)
        flow_ref.append(flow)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        flow.url = review.authorize + "?" + urlencode({"response_type": "code", "client_id": review.client_id,
            "redirect_uri": redirect, "scope": " ".join(review.scopes), "resource": review.resource,
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
        self.flow = flow
        flow.task = asyncio.create_task(self._finish(flow))
        return {"authorization_url": flow.url, "review_id": review.id, "client_id": review.client_id,
                "registration": review.registration,
                "notice": "Open this operator-reviewed URL in your browser. Do not paste auth codes/tokens into chat."}

    async def _finish(self, flow: _Flow) -> None:
        try:
            async with asyncio.timeout(300):
                value = await flow.code
                from xdog.agent.mcp.oauth_client_auth import async_token_request
                body, headers = await async_token_request(flow.review.token_auth_method, flow.review.client_secret_env,
                    flow.review.client_id, {"grant_type": "authorization_code", "client_id": flow.review.client_id,
                        "code": value, "redirect_uri": flow.redirect, "code_verifier": flow.verifier,
                        "resource": flow.review.resource}, token_endpoint=flow.review.token_endpoint)
                self._token_target = flow.review.token_endpoint
                async with self.client() as client:
                    result = await self._json(client, "POST", flow.review.token_endpoint, body, token_headers=headers)
                from xdog.agent.mcp.oauth_tokens import decode
                tokens = decode(result, flow.review.scopes)
                if self._closed or self.flow is not flow or not self.live():
                    raise OAuthError("OAuth owner changed/closed before credential commit")
                self._token, self._refresh = tokens.access, tokens.refresh
                self._expires = time.monotonic() + tokens.lifetime
                self._grant = replace(flow.review, scopes=tokens.scopes)
                self._refresh_review, self._refresh_state = None, "idle"
                flow.status = "authorized"
        except asyncio.CancelledError:
            flow.status = "canceled"
        except Exception:
            # Unexpected transport/codec defects must fail the background owner,
            # not leave status waiting forever or log request secrets in a task traceback.
            flow.status, flow.error = "failed", "OAuth failed/expired; no credentials committed and no code replay"
        finally:
            self._token_target = ""
            flow.verifier, flow.url, flow.state = "", "", ""
            if not flow.code.done():
                flow.code.cancel()
            else:
                if not flow.code.cancelled():
                    flow.code.exception()  # Observe refused-code errors even if shutdown won the race.
                flow.code = asyncio.get_running_loop().create_future()
                flow.code.cancel()  # Release any retained authorization code/error payload.
            flow.listener.close()
            await flow.listener.wait_closed()

    async def prepare_refresh(self) -> dict[str, Any]:
        async with self._operation:
            if self._closed or not self.live() or not self._refresh or self._grant is None:
                raise OAuthError("No live resource-bound refresh grant; authorize explicitly again")
            if self.flow is not None and self.flow.status == "waiting":
                raise OAuthError("Finish/cancel code authorization before refreshing")
            self.config.validate_public()
            validate_review(self._grant.to_dict())
            self._refresh_review = replace(self._grant, id=secrets.token_hex(16), created=time.monotonic())
            return {**self._refresh_review.to_dict(), "operation": "refresh",
                    "notice": "Refresh only this resource's existing scopes; no MCP call replay or tool approval."}

    async def refresh(self, proof: str) -> dict[str, Any]:
        async with self._operation:
            review = self._refresh_review
            if (self._closed or not self.live() or review is None or not isinstance(proof, str)
                    or not proof.isascii() or proof != review.id or time.monotonic() - review.created > 300
                    or not self._refresh or self._grant is None):
                raise OAuthError("Refresh consent is stale/unavailable; prepare a fresh review")
            if self.flow is not None and self.flow.status == "waiting":
                raise OAuthError("Another authorization transaction owns this resource")
            self.config.validate_public()
            validate_review(review.to_dict())
            self._refresh_review = None
            previous, self._refresh = self._refresh, ""  # Unknown rotation outcome cannot replay the old token.
            self._refresh_state = "refreshing"
            task = asyncio.current_task()
            assert task is not None
            self._refresh_task = task
            try:
                from xdog.agent.mcp.oauth_client_auth import async_token_request
                body, headers = await async_token_request(review.token_auth_method, review.client_secret_env,
                    review.client_id,
                    {"grant_type": "refresh_token", "refresh_token": previous, "client_id": review.client_id,
                     "scope": " ".join(review.scopes), "resource": review.resource},
                    token_endpoint=review.token_endpoint)
                self._token_target = review.token_endpoint
                async with asyncio.timeout(30), self.client() as client:
                    result = await self._json(client, "POST", review.token_endpoint, body, token_headers=headers)
                from xdog.agent.mcp.oauth_tokens import decode
                tokens = decode(result, review.scopes, previous_refresh=previous)
                if self._closed or not self.live():
                    raise OAuthError("Refresh owner changed before credential commit")
                self._token, self._refresh = tokens.access, tokens.refresh
                self._expires = time.monotonic() + tokens.lifetime
                self._grant = replace(review, scopes=tokens.scopes)
                self._refresh_state = "refreshed"
                if self.flow is not None:
                    self.flow.status = "authorized"
                    self.flow.error = ""
                return {**self.status(), "review": self._grant.to_dict()}
            except asyncio.CancelledError:
                self._refresh_state = "canceled"
                raise
            except Exception:
                self._refresh_state = "failed"
                raise OAuthError("Refresh failed; rotation uncertain; authorize again, no token replay") from None
            finally:
                self._token_target = ""
                self._refresh_task = None

    async def cancel(self) -> None:
        task = self._refresh_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self._operation:
            await self._cancel()

    async def _cancel(self) -> None:
        self._refresh_review = None
        task = self._refresh_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        flow = self.flow
        if flow is None:
            return
        flow.listener.close()
        await flow.listener.wait_closed()
        for task in tuple(flow.handlers):
            task.cancel()
        if flow.handlers:
            await asyncio.gather(*flow.handlers, return_exceptions=True)
        if flow.task is not None and not flow.task.done():
            flow.task.cancel()
            await asyncio.gather(flow.task, return_exceptions=True)
        flow.verifier, flow.url, flow.state = "", "", ""
        if flow.status == "waiting":
            flow.status = "canceled"  # A task canceled before admission never enters _finish's finally.
        if not flow.code.done():
            flow.code.cancel()
        elif not flow.code.cancelled():
            flow.code.exception()
            flow.code = asyncio.get_running_loop().create_future()
            flow.code.cancel()

    async def close(self) -> None:
        self._closed = True
        self._token, self._refresh, self._expires, self.review = "", "", 0, None
        self._grant, self._refresh_review = None, None
        task = self._refresh_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self._operation:
            await self._cancel()


def validate_review(value: Any, *, prepared: bool = False) -> dict[str, Any]:
    """Bounded public consent codec shared by application hosts, not a permission grant."""
    if not isinstance(value, dict) or not isinstance(value.get("review_id"), str):
        raise OAuthError("Invalid OAuth review")
    identifier = value["review_id"]
    if len(identifier) != 32 or any(char not in "0123456789abcdef" for char in identifier):
        raise OAuthError("Invalid OAuth review receipt")
    for key in ("issuer", "authorization_endpoint", "token_endpoint"):
        endpoint(value.get(key))
    resource = endpoint(value.get("resource"), https=False)
    if origin(resource)[0] == "http" and origin(resource)[1] not in {"127.0.0.1", "localhost", "::1"}:
        raise OAuthError("Unsafe OAuth resource")
    if any(origin(value[key]) != origin(value["issuer"]) for key in ("authorization_endpoint", "token_endpoint")):
        raise OAuthError("OAuth review endpoint origin mismatch")
    trust = value.get("tls_trust", "system")
    if not isinstance(trust, str) or not trust or len(trust.encode()) > 4096 or "\x00" in trust:
        raise OAuthError("Invalid OAuth certificate trust review")
    chosen = scopes(value.get("scopes"), allow_empty=True)
    registration = value.get("registration", "pre-registered")
    from xdog.agent.mcp.oauth_registration import client_name
    client_name(value.get("client_name", "XDOG"))
    from xdog.agent.mcp.oauth_client_auth import validate
    validate(value.get("token_auth_method", "none"), value.get("client_secret_env", ""), registration)
    if any(key in value for key in ("client_private_key_env", "client_secret", "client_assertion",
                                    "client_assertion_type", "registration_access_token")):
        raise OAuthError("Unsupported OAuth credential fields")
    if value.get("registration_endpoint", "") != "" or value.get("client_assigned", True) is not True:
        raise OAuthError("Only an assigned pre-registered public OAuth client is supported")
    port = value.get("callback_port", 0)
    if type(port) is not int or not 0 <= port <= 65535:
        raise OAuthError("Invalid reviewed native callback port")
    client_identifier(value.get("client_id"))
    return {**value, "scopes": list(chosen), "tls_trust": trust}


def normalize_auth_reply(
    action: str, data: Any, *, review: dict[str, Any] | None, error: str = "",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validate a native consent reply without changing host ownership or credentials.

    The returned review replaces the host cache only after successful validation.
    Reported operation errors remain displayable even without a structured reply.
    """
    if not isinstance(data, dict) and not error:
        raise ValueError("Invalid authorization response")
    if error:
        return data if isinstance(data, dict) else {}, review
    if action.startswith("auth_credentials_") and action.endswith("_prepare"):
        expected = action.removeprefix("auth_").removesuffix("_prepare")
        if data.get("operation") != expected or not isinstance(data.get("storage_notice"), str):
            raise ValueError("Missing explicit credential storage consent")
        review = validate_review(data)
        data = review
    elif action == "auth_prepare":
        review = validate_review(data, prepared=True)
        data = review
    elif action == "auth_refresh_prepare":
        if data.get("operation") != "refresh":
            raise ValueError("Missing explicit refresh operation")
        review = validate_review(data)
        data = review
    elif action == "auth_status" and isinstance(data.get("review"), dict):
        data = {**data, "review": validate_review(data["review"])}
    elif action == "auth_start":
        if review is None:
            raise ValueError("Missing reviewed authorization scope")
        data = validate_launch(data, review)
    return data, review


def validate_launch(value: Any, review: dict[str, Any]) -> dict[str, Any]:
    review = validate_review(review, prepared=True)
    if not isinstance(value, dict) or value.get("review_id") != review["review_id"]:
        raise OAuthError("OAuth launch receipt changed")
    url = value.get("authorization_url")
    if not isinstance(url, str) or len(url.encode()) > 32768 or any(ord(char) < 33 or ord(char) > 126 for char in url):
        raise OAuthError("Unsafe OAuth authorization URI")
    parsed = urlsplit(url)
    base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if parsed.fragment or base != review["authorization_endpoint"]:
        raise OAuthError("OAuth authorization endpoint changed after review")
    args = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=16)
    if any(len(values) != 1 for values in args.values()):
        raise OAuthError("Duplicate OAuth URI fields")
    identifier = review["client_id"]
    if (value.get("registration", "pre-registered") != "pre-registered"
            or value.get("client_id", identifier) != identifier):
        raise OAuthError("Pre-registered client changed after consent")
    expected = {"response_type": "code", "client_id": identifier, "resource": review["resource"],
                "scope": " ".join(review["scopes"]), "code_challenge_method": "S256"}
    if any(args.get(key) != [text] for key, text in expected.items()):
        raise OAuthError("OAuth URI fields no longer match reviewed consent")
    if set(args) != {*expected, "state", "code_challenge", "redirect_uri"}:
        raise OAuthError("Unexpected OAuth authorization URI fields")
    redirect = urlsplit(args["redirect_uri"][0])
    if (redirect.scheme != "http" or redirect.hostname != "127.0.0.1" or redirect.path != "/callback"
            or redirect.username or redirect.password or redirect.query or redirect.fragment or not redirect.port):
        raise OAuthError("Invalid OAuth local callback")
    if review.get("callback_port", 0) and redirect.port != review["callback_port"]:
        raise OAuthError("OAuth callback port changed after explicit registration review")
    for key in ("state", "code_challenge"):
        if len(args[key][0]) != 43 or any(not (char.isascii() and (char.isalnum() or char in "-_"))
                                       for char in args[key][0]):
            raise OAuthError("Invalid OAuth state/challenge URI field")
    return dict(value)
