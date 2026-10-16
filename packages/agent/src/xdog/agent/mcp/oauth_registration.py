"""Display-name validation for pre-registered public clients; no registration protocol."""
from __future__ import annotations

from typing import Any

from xdog.agent.mcp.oauth import OAuthError


def client_name(value: Any) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value.encode()) > 120
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise OAuthError("Invalid OAuth client display name")
    return value
