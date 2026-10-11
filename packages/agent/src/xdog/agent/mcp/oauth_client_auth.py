"""Explicit environment-backed token endpoint authentication; never discovery headers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import quote_plus

from xdog.agent.mcp.oauth import OAuthError

METHODS = frozenset({"none", "client_secret_basic", "client_secret_post", "client_secret_jwt", "private_key_jwt"})


def validate(method: Any, variable: Any, registration: str) -> tuple[str, str]:
    if not isinstance(method, str) or method not in METHODS or not isinstance(variable, str):
        raise OAuthError("Unsupported OAuth token client authentication")
    if method == "none":
        if variable:
            raise OAuthError("Public OAuth clients cannot implicitly use configured secrets")
    elif (registration != "pre-registered" or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", variable)):
        raise OAuthError("Confidential token methods need pre-registered credentials and explicit environment source")
    return method, variable


def secret(variable: str, *, method: str = "") -> str:
    value = os.environ.get(variable)
    if (not value or len(value.encode()) > 8192
            or any(ord(char) < 32 or ord(char) > 126 for char in value)):
        raise OAuthError("Configured OAuth client secret environment value is missing/unsafe")
    if method == "client_secret_jwt" and len(value.encode()) < 32:
        raise OAuthError("HS256 client authentication requires at least 32 secret bytes")
    return value


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def token_request(method: str, variable: str, client_id: str,
                  payload: dict[str, str], *, token_endpoint: str = "") -> tuple[dict[str, str], dict[str, str]]:
    body = dict(payload)
    if any(key in body for key in ("client_secret", "client_assertion", "client_assertion_type")):
        raise OAuthError("Client authentication fields are owned by the explicit token operation")
    if method == "none":
        return body, {}
    if method == 'private_key_jwt':
        raise OAuthError('Private-key JWT requires the asynchronous owned signer; no method fallback')
    value = secret(variable, method=method)  # Resolve only for this request; do not retain it on an owner/review.
    if method == "client_secret_jwt":
        from xdog.agent.mcp.oauth import client_identifier, endpoint
        client_identifier(client_id)
        audience = endpoint(token_endpoint)
        now = int(time.time())
        if not 0 <= now <= 2**53 - 61:
            raise OAuthError("Invalid client assertion clock")
        header = _b64(b'{"alg":"HS256","typ":"JWT"}')
        claims = _b64(json.dumps({"iss": client_id, "sub": client_id, "aud": audience,
            "iat": now, "exp": now + 60, "jti": secrets.token_urlsafe(24)}, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False).encode("ascii"))
        signing = (header + "." + claims).encode("ascii")
        assertion = signing.decode("ascii") + "." + _b64(hmac.new(value.encode(), signing, hashlib.sha256).digest())
        body.update(client_id=client_id, client_assertion=assertion,
                    client_assertion_type="urn:ietf:params:oauth:client-assertion-type:jwt-bearer")
        return body, {}  # Secret is neither posted nor placed in discovery/authentication headers.
    if method == "client_secret_post":
        body["client_secret"] = value
        return body, {}
    if method == "client_secret_basic":
        # OAuth Basic credentials are form-encoded individually BEFORE joining
        # with ':' and base64, not ordinary raw HTTP username/password encoding.
        raw = quote_plus(client_id) + ":" + quote_plus(value)
        encoded = base64.b64encode(raw.encode("ascii")).decode("ascii")
        body.pop("client_id", None)
        return body, {"Authorization": "Basic " + encoded}
    raise OAuthError("Unsupported OAuth client authentication; no method fallback")


async def validate_source(method: str, variable: str) -> None:
    if method == 'private_key_jwt':
        from xdog.agent.mcp.oauth_private_jwt import validate_key
        await validate_key(variable)
    elif method != 'none':
        secret(variable, method=method)


async def async_token_request(method: str, variable: str, client_id: str, payload: dict[str, str], *,
                              token_endpoint: str = '') -> tuple[dict[str, str], dict[str, str]]:
    if method != 'private_key_jwt':
        return token_request(method, variable, client_id, payload, token_endpoint=token_endpoint)
    if any(key in payload for key in ('client_secret', 'client_assertion', 'client_assertion_type')):
        raise OAuthError('Client authentication fields are owned by the explicit token operation')
    from xdog.agent.mcp.oauth_private_jwt import assertion
    signed = await assertion(variable, client_id, token_endpoint)
    return {**payload, 'client_id': client_id, 'client_assertion': signed,
            'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer'}, {}
