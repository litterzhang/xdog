"""Bounded HTTP authentication-challenge and well-known discovery codecs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from xdog.agent.mcp.oauth import OAuthError, endpoint, origin, scopes

_TOKEN68 = re.compile(r"[A-Za-z0-9._~+/-]+=*(?=[ \t]*[,]|[ \t]*$)")
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


@dataclass(frozen=True, slots=True)
class OAuthChallenge:
    metadata: str | None = None
    scopes: tuple[str, ...] | None = None


def challenge(value: str) -> OAuthChallenge | None:
    """Parse HTTP auth-params without splitting quoted commas or combining schemes."""
    if (not isinstance(value, str) or len(value.encode()) > 16384
            or any((ord(c) < 32 and c != "\t") or ord(c) > 126 for c in value)):
        raise OAuthError("Invalid/oversized HTTP authentication challenge")
    position, scheme = 0, ""
    params: dict[str, str] = {}
    bearer: list[dict[str, str]] = []
    def finish() -> None:
        if scheme.lower() == "bearer":
            bearer.append(dict(params))
    while position < len(value):
        while position < len(value) and value[position] in " ,\t":
            position += 1
        if position == len(value):
            break
        if scheme and scheme.lower() != "bearer":
            opaque = _TOKEN68.match(value, position)
            if opaque is not None:
                position = opaque.end()
                continue  # Other schemes' opaque tickets are not Bearer auth-params.
        match = _TOKEN.match(value, position)
        if match is None:
            raise OAuthError("Malformed HTTP authentication challenge")
        word = match[0]
        position = match.end()
        while position < len(value) and value[position] in " \t":
            position += 1
        if position == len(value) or value[position] != "=":
            finish()
            scheme, params = word, {}
            if position < len(value) and value[position] not in " ,\t" and _TOKEN.match(value, position) is None:
                raise OAuthError("Unsupported HTTP authentication challenge syntax")
            continue
        if not scheme:
            raise OAuthError("Authentication parameters arrived without a scheme")
        position += 1
        while position < len(value) and value[position] in " \t":
            position += 1
        if position < len(value) and value[position] == '"':
            position += 1
            decoded = []
            while position < len(value) and value[position] != '"':
                char = value[position]
                position += 1
                if char == "\\":
                    if position == len(value):
                        raise OAuthError("Incomplete quoted authentication value")
                    char = value[position]
                    position += 1
                decoded.append(char)
            if position == len(value):
                raise OAuthError("Unterminated authentication value")
            position += 1
            text = "".join(decoded)
        else:
            match = _TOKEN.match(value, position)
            if match is None:
                raise OAuthError("Invalid authentication parameter value")
            text, position = match[0], match.end()
        key = word.lower()
        if key in params:
            raise OAuthError("Duplicate HTTP authentication parameter")
        params[key] = text
        if len(params) > 32:
            raise OAuthError("Authentication parameter capacity exceeded")
        while position < len(value) and value[position] in " \t":
            position += 1
        if position < len(value) and value[position] != ",":
            raise OAuthError("Malformed authentication parameter boundary")
    finish()
    if not bearer:
        return None
    if len(bearer) != 1:
        raise OAuthError("Multiple Bearer challenges require explicit issuer/scope selection")
    params = bearer[0]
    pointer = params.get("resource_metadata")
    if pointer is not None:
        if not pointer or len(pointer) > 8192:
            raise OAuthError("Invalid protected-resource metadata pointer")
        # Pointer queries are permitted as inert discovery data, never credentials
        # imported from the MCP config or another URI. Origins are validated at use.
        parsed = urlsplit(pointer)
        endpoint(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment)), https=False)
    if "scope" in params and any(char.isspace() and char != " " for char in params["scope"]):
        raise OAuthError("OAuth scope challenge uses non-space delimiters")
    selected = scopes(params["scope"].split(), allow_empty=True) if "scope" in params else None
    return OAuthChallenge(pointer, selected)


def resource_candidates(resource: str, hint: OAuthChallenge | None = None,
                        *, trusted_origins: tuple[str, ...] = ()) -> tuple[str, ...]:
    parsed = urlsplit(resource)
    if hint is not None and hint.metadata is not None:
        approved = {origin(resource), *(origin(value) for value in trusted_origins)}
        if origin(hint.metadata) not in approved:
            raise OAuthError("Metadata pointer origin differs from operator pins; review metadata_origins explicitly")
        return (hint.metadata,)
    suffix = parsed.path if parsed.path not in {"", "/"} else ""
    paths = ("/.well-known/oauth-protected-resource" + suffix, "/.well-known/oauth-protected-resource")
    return tuple(dict.fromkeys(urlunsplit((parsed.scheme, parsed.netloc, path, "", "")) for path in paths))


def issuer_candidates(issuer: str) -> tuple[str, ...]:
    parsed = urlsplit(issuer)
    path = parsed.path.rstrip("/")
    paths = ["/.well-known/oauth-authorization-server" + path, "/.well-known/openid-configuration" + path]
    if path:
        paths.append(path + "/.well-known/openid-configuration")
    return tuple(dict.fromkeys(urlunsplit((parsed.scheme, parsed.netloc, value, "", "")) for value in paths))
