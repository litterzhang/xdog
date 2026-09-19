"""Single-account Antigravity OAuth and explicit project onboarding.

No inference path starts an interactive login. Tokens are never printed.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from xdog.ai.paths import auth_file
from xdog.ai.types import AuthExpiredError
from xdog.ai.utils.auth import generate_code_challenge, generate_code_verifier

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_BASE_URL = "https://daily-cloudcode-pa.googleapis.com"
DISCOVERY_URL = "https://cloudcode-pa.googleapis.com"
LOGIN_COMMAND = "xdog-ai login antigravity"
# Public installed-app registration used by CLIProxyAPI's Antigravity flow.
# These identify the OAuth client, not the user's access/refresh credentials.
# Reference: CLIProxyAPI/internal/auth/antigravity/constants.go
_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
_CLIENT_SECRET = "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"
SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
)
# Compatibility metadata for the control-plane requests. Keep fixed internally,
# as with the client registration. Reference: CLIProxyAPI's
# internal/misc/antigravity_version.go and internal/auth/antigravity/auth.go.
_CLIENT_VERSION = "2.9.1"
_REQUEST_USER_AGENT = f"antigravity/hub/{_CLIENT_VERSION} darwin/arm64"
_ONBOARD_USER_AGENT = f"{_REQUEST_USER_AGENT} google-api-nodejs-client/10.3.0"
_GOOG_API_CLIENT = "gl-node/22.21.1"
_ONBOARD_ATTEMPTS = 5
_ONBOARD_INTERVAL = 2.0


def oauth_client() -> tuple[str, str]:
    """The provider owns its OAuth registration; it is not user configuration."""
    return _CLIENT_ID, _CLIENT_SECRET


def base_url() -> str:
    """Return the fixed provider backend, never an environment-supplied URL."""
    return _BASE_URL


def request_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "User-Agent": _REQUEST_USER_AGENT, "Accept": "*/*"}


@dataclass(frozen=True)
class Credentials:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    project_id: str


def load_credentials() -> Credentials:
    try:
        raw = json.loads(auth_file().read_text())["antigravity"]
        return Credentials(
            access_token=raw["access_token"], refresh_token=raw.get("refresh_token", ""),
            expires_at=float(raw.get("expires_at", 0)), project_id=raw.get("project_id", ""),
        )
    except (OSError, ValueError, TypeError, KeyError):
        raise AuthExpiredError("Antigravity", LOGIN_COMMAND) from None


def save_credentials(creds: Credentials) -> None:
    path = auth_file()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(existing, dict):
        raise ValueError("Invalid auth.json; refusing to overwrite other providers.")
    existing["antigravity"] = {
        "type": "oauth", "access_token": creds.access_token, "refresh_token": creds.refresh_token,
        "expires_at": creds.expires_at, "project_id": creds.project_id,
    }
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".antigravity-auth-")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(existing, output, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


async def exchange_token(data: dict[str, str]) -> dict[str, Any]:
    client_id, secret = oauth_client()
    data = {**data, "client_id": client_id}
    if secret:
        data["client_secret"] = secret
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(TOKEN_URL, data=data)
        if response.status_code != 200:
            raise AuthExpiredError("Antigravity", LOGIN_COMMAND, f"OAuth HTTP {response.status_code}")
        result = response.json()
    if not isinstance(result, dict) or not isinstance(result.get("access_token"), str):
        raise RuntimeError("Antigravity OAuth returned no access token.")
    return result


def _project_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return str(value["id"]).strip()
    return ""


def _extract_project(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    for key in ("cloudaicompanionProject", "projectId", "project"):
        project = _project_id(response.get(key))
        if project:
            return project
    return ""


def _onboarding_tier(response: dict[str, Any]) -> str:
    tiers = response.get("allowedTiers")
    if isinstance(tiers, list):
        for tier in tiers:
            if isinstance(tier, dict) and tier.get("isDefault") is True:
                tier_id = _project_id(tier.get("id"))
                if tier_id:
                    return tier_id
    current = _project_id(response.get("currentTier"))
    return current or "free-tier"


def _control_plane_response(response: httpx.Response, operation: str) -> dict[str, Any]:
    """Explain the failing stage without echoing tokens or raw account data."""
    if response.status_code != 200:
        raise RuntimeError(f"Antigravity {operation} failed (HTTP {response.status_code}).")
    try:
        raw = response.json()
    except ValueError:
        raise RuntimeError(f"Antigravity {operation} returned invalid JSON.") from None
    if not isinstance(raw, dict):
        raise RuntimeError(f"Antigravity {operation} returned a non-object response.")
    if "error" in raw:
        error = raw["error"]
        code = error.get("code") if isinstance(error, dict) else None
        suffix = f" (code {code})" if type(code) is int else ""
        raise RuntimeError(f"Antigravity {operation} returned an operation error{suffix}.")
    return raw


async def discover_project(token: str) -> str:
    """Discover project; onboarding is only called by explicit OAuth login."""
    headers = request_headers(token)
    async with httpx.AsyncClient(timeout=30) as client:
        async def load() -> dict[str, Any]:
            response = await client.post(
                DISCOVERY_URL + "/v1internal:loadCodeAssist",
                headers=headers, json={"metadata": {"ideType": "ANTIGRAVITY"}},
            )
            return _control_plane_response(response, "loadCodeAssist")

        raw = await load()
        project = _extract_project(raw)
        if project:
            return project
        # loadCodeAssist and onboardUser use different hosts and JSON schemas.
        # Reusing the load metadata/camelCase tierId can leave onboarding pending
        # or produce a completed operation without a usable project.
        body = {
            "tier_id": _onboarding_tier(raw),
            "metadata": {
                "ide_type": "ANTIGRAVITY", "ide_version": _CLIENT_VERSION, "ide_name": "antigravity",
            },
        }
        onboard_headers = {
            **headers, "User-Agent": _ONBOARD_USER_AGENT, "X-Goog-Api-Client": _GOOG_API_CLIENT,
        }
        for attempt in range(_ONBOARD_ATTEMPTS):
            response = await client.post(
                _BASE_URL + "/v1internal:onboardUser", headers=onboard_headers, json=body,
            )
            raw = _control_plane_response(response, "onboardUser")
            if raw.get("done") is True:
                project = _extract_project(raw.get("response")) or _extract_project(raw)
                if project:
                    return project
                # A completed operation may provision the project without
                # including it in the operation response. Re-read discovery.
                project = _extract_project(await load())
                if project:
                    return project
                raise RuntimeError(
                    "Antigravity onboardUser completed without a project; loadCodeAssist also returned none. "
                    "OAuth succeeded, but project discovery is incomplete."
                )
            if attempt + 1 < _ONBOARD_ATTEMPTS:
                await asyncio.sleep(_ONBOARD_INTERVAL)
    raise RuntimeError(
        f"Antigravity onboardUser is still pending after {_ONBOARD_ATTEMPTS} attempts. "
        "OAuth succeeded; retry login after onboarding completes."
    )


async def receive_code(state: str, authorize_url: str, *, port: int = 51121) -> str:
    """Print a login URL and receive its loopback callback, without opening a GUI."""
    code: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    handlers: set[asyncio.Task[None]] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status = "400 Bad Request"
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            method, target, _ = request.split(b"\r\n", 1)[0].decode("ascii").split(" ", 2)
            url = urlsplit(target)
            query = parse_qs(url.query)
            valid = (
                method == "GET" and url.path == "/oauth-callback"
                and secrets.compare_digest(query.get("state", [""])[0], state)
            )
            if valid and not code.done():
                if query.get("error"):
                    code.set_exception(RuntimeError("Antigravity authorization was declined."))
                elif query.get("code"):
                    code.set_result(query["code"][0])
                    status = "200 OK"
            body = b"You can return to the terminal."
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body,
            )
            await writer.drain()
        except (ValueError, OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(handle(reader, writer))
        handlers.add(task)
        task.add_done_callback(handlers.discard)

    server = await asyncio.start_server(accepted, "127.0.0.1", port, limit=8192)
    try:
        print(f"Open this URL to authorize Antigravity:\n{authorize_url}", file=sys.stderr)
        print(f"For remote login, forward localhost:{port} to this machine.", file=sys.stderr)
        return await asyncio.wait_for(code, 300)
    finally:
        server.close()
        await server.wait_closed()
        for task in handlers:
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)


async def login() -> str:
    client_id, _ = oauth_client()
    state = secrets.token_urlsafe(32)
    verifier = generate_code_verifier()
    redirect = "http://localhost:51121/oauth-callback"
    query = urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": generate_code_challenge(verifier), "code_challenge_method": "S256",
    })
    code = await receive_code(state, AUTH_URL + "?" + query)
    token = await exchange_token({
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect, "code_verifier": verifier,
    })
    project = await discover_project(token["access_token"])
    creds = Credentials(
        token["access_token"], token.get("refresh_token", ""),
        time.time() + float(token.get("expires_in", 3600)), project,
    )
    await asyncio.to_thread(save_credentials, creds)
    return creds.access_token


class TokenManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def get(self) -> Credentials:
        async with self._lock:
            creds = await asyncio.to_thread(load_credentials)
            if creds.access_token and creds.expires_at > time.time() + 60 and creds.project_id:
                return creds
            if not creds.refresh_token:
                raise AuthExpiredError("Antigravity", LOGIN_COMMAND)
            token = await exchange_token({"grant_type": "refresh_token", "refresh_token": creds.refresh_token})
            if not creds.project_id:
                raise RuntimeError("Missing Antigravity project; run explicit login again.")
            creds = Credentials(
                token["access_token"], token.get("refresh_token", creds.refresh_token),
                time.time() + float(token.get("expires_in", 3600)), creds.project_id,
            )
            await asyncio.to_thread(save_credentials, creds)
            return creds
