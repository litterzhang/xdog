"""Bounded token-response codec; secrets have no diagnostic representation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from xdog.agent.mcp.oauth import OAuthError, scopes


@dataclass(frozen=True, slots=True)
class TokenResult:
    access: str = field(repr=False)
    refresh: str = field(repr=False)
    lifetime: int
    scopes: tuple[str, ...]


def _token(value: Any, *, required: bool) -> str:
    if value is None and not required:
        return ""
    if (not isinstance(value, str) or not 0 < len(value.encode()) <= 8192
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise OAuthError("OAuth token response is unsafe; credentials discarded")
    return value


def decode(value: dict[str, Any], allowed: tuple[str, ...], *, previous_refresh: str = "") -> TokenResult:
    access = _token(value.get("access_token"), required=True)
    kind, lifetime = value.get("token_type"), value.get("expires_in")
    if (not isinstance(kind, str) or kind.lower() != "bearer" or type(lifetime) is not int
            or not 1 <= lifetime <= 31536000):
        raise OAuthError("OAuth token type/lifetime is invalid")
    granted = value.get("scope", " ".join(allowed))
    if not isinstance(granted, str) or any(char.isspace() and char != " " for char in granted):
        raise OAuthError("OAuth token scope encoding is invalid")
    chosen = scopes(granted.split(), allow_empty=True)
    if not set(chosen) <= set(allowed):
        raise OAuthError("OAuth token scopes exceed the reviewed grant")
    refresh = _token(value.get("refresh_token"), required=False) if "refresh_token" in value else previous_refresh
    return TokenResult(access, refresh, min(lifetime, 3600), chosen)
