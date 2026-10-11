"""Data-only public-client registration validation; no implicit registration attempts."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from xdog.agent.mcp.oauth import OAuthError, client_identifier, endpoint


def client_name(value: Any) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value.encode()) > 120
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise OAuthError("Invalid OAuth client display name")
    return value


def metadata_id(value: Any) -> str:
    url = endpoint(value)
    if urlsplit(url).path in {"", "/"}:
        raise OAuthError("Client metadata identifiers require an HTTPS document path")
    return url


def validate_document(value: Any, url: str, redirect: str) -> str:
    if not isinstance(value, dict) or value.get("client_id") != url:
        raise OAuthError("Client metadata document identity does not match its exact HTTPS URL")
    name = client_name(value.get("client_name"))
    redirects = value.get("redirect_uris")
    if (not isinstance(redirects, list) or not 1 <= len(redirects) <= 16
            or any(not isinstance(item, str) or len(item) > 8192 for item in redirects)
            or redirect not in redirects):
        raise OAuthError("Client metadata must authorize the exact configured loopback callback")
    if value.get("token_endpoint_auth_method", "none") != "none":
        raise OAuthError("Only public client metadata is supported by this profile")
    for field, required, default in (("grant_types", "authorization_code", ["authorization_code"]),
                                      ("response_types", "code", ["code"])):
        choices = value.get(field, default)
        if (not isinstance(choices, list) or required not in choices
                or any(not isinstance(item, str) for item in choices)):
            raise OAuthError("Client metadata does not advertise authorization-code support")
    return name


def registration_request(name: str, redirect: str, scope: tuple[str, ...]) -> dict[str, Any]:
    return {"client_name": client_name(name), "application_type": "native", "redirect_uris": [redirect],
            "grant_types": ["authorization_code"], "response_types": ["code"],
            "token_endpoint_auth_method": "none", "scope": " ".join(scope)}


def registered_id(value: dict[str, Any], redirect: str) -> str:
    identifier = client_identifier(value.get("client_id"))
    if value.get("token_endpoint_auth_method", "none") != "none":
        raise OAuthError("Issuer returned incompatible confidential-client registration")
    redirects = value.get("redirect_uris", [redirect])
    if not isinstance(redirects, list) or redirect not in redirects:
        raise OAuthError("Issuer registration did not retain the requested loopback callback")
    # client_secret/registration_access_token are neither retained nor sent. The
    # requested public method is authoritative, not unexpected private credentials.
    return identifier
