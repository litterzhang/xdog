"""Retired private-key OAuth has no signing helper or environment-source fallback."""
from __future__ import annotations

import importlib.util
import os
from dataclasses import replace

import pytest
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError
from xdog.agent.mcp.oauth_client_auth import async_token_request, token_request, validate_source

from packages.agent.tests.test_mcp_oauth import CONFIG, ISSUER


@pytest.mark.parametrize("method", [
    "client_secret_basic", "client_secret_post", "client_secret_jwt", "private_key_jwt",
])
async def test_retired_auth_builders_refuse_before_environment_key_or_subprocess_io(method, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Retired authentication attempted helper I/O")

    monkeypatch.setattr("asyncio.create_subprocess_exec", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    original = type(os.environ).__getitem__

    def environment(mapping, key):
        if key == "XDOG_RETIRED_SOURCE":
            pytest.fail("Retired authentication read the environment source")
        return original(mapping, key)

    monkeypatch.setattr(type(os.environ), "__getitem__", environment)
    with pytest.raises(OAuthError):
        await validate_source(method, "XDOG_RETIRED_SOURCE")
    with pytest.raises(OAuthError):
        token_request(method, "XDOG_RETIRED_SOURCE", CONFIG["client_id"], {}, token_endpoint=ISSUER + "/token")
    with pytest.raises(OAuthError):
        await async_token_request(method, "XDOG_RETIRED_SOURCE", CONFIG["client_id"], {},
                                  token_endpoint=ISSUER + "/token")
    with pytest.raises(OAuthError):
        replace(OAuthConfig.parse(CONFIG), token_auth_method=method, client_secret_env="XDOG_RETIRED_SOURCE")


def test_private_jwt_signing_module_is_absent():
    assert importlib.util.find_spec("xdog.agent.mcp.oauth_private_jwt") is None
