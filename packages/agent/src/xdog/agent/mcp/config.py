"""Explicit MCP server configuration; declaring a server never launches it."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from xdog.agent.mcp.oauth import OAuthConfig, endpoint


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    name: str
    command: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()
    timeout: float = 30
    allow_tools: bool = False
    transport: str = "stdio"
    url: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    headers_env: tuple[tuple[str, str], ...] = ()
    oauth: OAuthConfig | None = None
    roots: tuple[tuple[str, str], ...] = ()

    @classmethod
    def parse(cls, name: str, value: object) -> McpServerConfig:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
            raise ValueError("MCP server IDs must be 1-32 ASCII letters/digits/underscore/hyphen")
        if not isinstance(value, dict) or value.get("transport", "stdio") not in {"stdio", "http"}:
            raise ValueError(f"MCP {name}: unsupported transport")
        from xdog.agent.mcp.client_capabilities import roots_config
        roots = roots_config(value.get("roots", []))
        transport = value.get("transport", "stdio")
        timeout = value.get("timeout", 30)
        if (type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0.1 <= timeout <= 600):
            raise ValueError(f"MCP {name}: timeout must be between 0.1 and 600 seconds")
        allow = value.get("allow_tools", False)
        if type(allow) is not bool:
            raise ValueError(f"MCP {name}: allow_tools must be an explicit boolean")
        if transport == "http":
            url = value.get("url")
            if not isinstance(url, str) or any(ord(char) < 0x21 for char in url):
                raise ValueError(f"MCP {name}: url must be an explicit HTTP endpoint")
            parsed = urlsplit(url)
            if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.fragment
                    or parsed.username is not None or parsed.password is not None):
                raise ValueError(f"MCP {name}: invalid endpoint URL")
            insecure = value.get("allow_insecure_http", False)
            if type(insecure) is not bool:
                raise ValueError(f"MCP {name}: allow_insecure_http must be an explicit boolean")
            if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"} and not insecure:
                raise ValueError(f"MCP {name}: non-loopback plaintext HTTP requires explicit opt-in")
            from xdog.agent.mcp.protocol import TOKEN
            def headers(key: str) -> tuple[tuple[str, str], ...]:
                values = value.get(key, {})
                if not isinstance(values, dict):
                    raise ValueError(f"MCP {name}: {key} must map header names to strings")
                seen = set()
                result = []
                for header, content in values.items():
                    if (not isinstance(header, str) or not TOKEN.fullmatch(header) or header.lower() in seen
                            or header.lower().startswith("mcp-") or header.lower().startswith("mcp-param-")
                            or header.lower() in {"accept", "content-type", "content-length", "host", "connection",
                                                  "transfer-encoding", "last-event-id"}
                            or not isinstance(content, str) or not content
                            or any(ord(char) < 0x20 or ord(char) > 0x7e for char in content)):
                        raise ValueError(f"MCP {name}: invalid/reserved HTTP header")
                    seen.add(header.lower())
                    result.append((header, content))
                return tuple(result)
            static, environment = headers("headers"), headers("headers_env")
            if {key.lower() for key, _value in static} & {key.lower() for key, _value in environment}:
                raise ValueError(f"MCP {name}: duplicate static/environment credential header")
            oauth = OAuthConfig.parse(value["oauth"]) if "oauth" in value else None
            if oauth is not None:
                endpoint(url, https=False)
                if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                    raise ValueError("OAuth requires HTTPS or explicit loopback resource endpoints")
                if any(key.lower() == "authorization" for key, _value in (*static, *environment)):
                    raise ValueError("OAuth cannot be mixed with a configured Authorization header")
            return cls(name, (), timeout=float(timeout), allow_tools=allow, transport="http", url=url,
                       headers=static, headers_env=environment, oauth=oauth, roots=roots)
        if "oauth" in value:
            raise ValueError("OAuth is available only for HTTP MCP resources")
        command = value.get("command")
        args = value.get("args", [])
        if not isinstance(command, str) or not command or "\x00" in command:
            raise ValueError(f"MCP {name}: command must be a nonempty executable string")
        if not isinstance(args, list) or any(not isinstance(arg, str) or "\x00" in arg for arg in args):
            raise ValueError(f"MCP {name}: args must be an array of strings")
        env = value.get("env", {})
        if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                           or not k or "=" in k or "\x00" in k + v for k, v in env.items()):
            raise ValueError(f"MCP {name}: env must map valid environment names to strings")
        return cls(name, (command, *args), tuple(env.items()), float(timeout), allow, roots=roots)


def parse_servers(raw: Any) -> tuple[McpServerConfig, ...]:
    if not isinstance(raw, dict) or any(not isinstance(name, str) for name in raw):
        raise ValueError("mcp_servers must be an object keyed by server ID")
    return tuple(McpServerConfig.parse(name, value) for name, value in raw.items())
