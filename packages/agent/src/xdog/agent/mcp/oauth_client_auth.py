"""Public-client token requests only; no secret sources, signing helpers or fallback."""
from __future__ import annotations

from typing import Any

from xdog.agent.mcp.oauth import OAuthError, client_identifier

METHODS = frozenset({"none"})


def validate(method: Any, variable: Any, registration: str) -> tuple[str, str]:
    if registration != "pre-registered" or method != "none" or variable != "":
        raise OAuthError("Only pre-registered public OAuth clients with token authentication none are supported")
    return "none", ""


def token_request(method: str, variable: str, client_id: str,
                  payload: dict[str, str], *, token_endpoint: str = "") -> tuple[dict[str, str], dict[str, str]]:
    validate(method, variable, "pre-registered")
    client_identifier(client_id)
    if any(key in payload for key in ("client_secret", "client_assertion", "client_assertion_type")):
        raise OAuthError("Public OAuth requests cannot contain client authentication fields")
    if payload.get("client_id", client_id) != client_id:
        raise OAuthError("OAuth token client ID differs from reviewed client")
    return {**payload, "client_id": client_id}, {}


async def validate_source(method: str, variable: str) -> None:
    """Compatibility validator; never reads environment credentials or starts helpers."""
    validate(method, variable, "pre-registered")


async def async_token_request(method: str, variable: str, client_id: str, payload: dict[str, str], *,
                              token_endpoint: str = "") -> tuple[dict[str, str], dict[str, str]]:
    return token_request(method, variable, client_id, payload, token_endpoint=token_endpoint)
