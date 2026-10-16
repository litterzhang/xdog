"""Independent offline issuer/resource peer used by real client UI/terminal tests."""
from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx

RESOURCE = "https://resource.test/mcp"
ISSUER = "https://issuer.test/tenant"
PROFILE = {"issuer": ISSUER, "client_id": "registered-client", "scopes": ["tools:read"]}


class Peer:
    def __init__(self):
        self.requests = []
        self.client_id = "registered-client"
        self.registration_posts = []
        self.challenge = ""
        self.transport = httpx.MockTransport(self.handle)
        self.token_posts = 0
        self.issue_refresh = False
        self.refresh_posts = []

    def launch(self, url):
        self.challenge = parse_qs(urlsplit(url).query)["code_challenge"][0]

    async def handle(self, request):
        self.requests.append((request.method, request.url.path))
        path = request.url.path
        if path == "/.well-known/oauth-protected-resource/mcp":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"resource": RESOURCE, "authorization_servers": [ISSUER],
                                           "scopes_supported": ["tools:read"]})
        if path == "/.well-known/oauth-authorization-server/tenant":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": "https://issuer.test/authorize",
                "token_endpoint": "https://issuer.test/token", "code_challenge_methods_supported": ["S256"],
                "response_types_supported": ["code"], "token_endpoint_auth_methods_supported": ["none"],
                "authorization_response_iss_parameter_supported": True,
                "registration_endpoint": "https://issuer.test/register",
                "client_id_metadata_document_supported": True})
        if path == "/register":
            self.registration_posts.append(request)
            raise AssertionError("Public pre-registered clients must never attempt registration")
        if path == "/token":
            body = parse_qs(request.content.decode())
            assert "authorization" not in request.headers
            assert not {"client_secret", "client_assertion", "client_assertion_type"} & body.keys()
            assert body["resource"] == [RESOURCE]
            assert body["client_id"] == [self.client_id]
            if body.get("grant_type") == ["refresh_token"]:
                assert body["refresh_token"] == ["FIXTURE-REFRESH-ONE-SECRET"]
                assert body["scope"] == ["tools:read"]
                self.refresh_posts.append(body)
                return httpx.Response(200, json={"access_token": "FIXTURE-OAUTH-SECRET", "token_type": "Bearer",
                    "expires_in": 3600, "scope": "tools:read", "refresh_token": "FIXTURE-REFRESH-TWO-SECRET"})
            assert body["code"] == ["fixture-code"]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"][0].encode()).digest()).decode()
            assert challenge.rstrip("=") == self.challenge
            self.token_posts += 1
            reply = {"access_token": "FIXTURE-OAUTH-SECRET", "token_type": "Bearer",
                     "expires_in": 3600, "scope": "tools:read"}
            if self.issue_refresh:
                reply["refresh_token"] = "FIXTURE-REFRESH-ONE-SECRET"
            return httpx.Response(200, json=reply)
        if path == "/mcp":
            if request.headers.get("Authorization") != "Bearer FIXTURE-OAUTH-SECRET":
                return httpx.Response(401)
            message = json.loads(request.content)
            if message["method"] == "server/discover":
                result = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}},
                          "serverInfo": {"name": "OAuth fixture", "version": "1"}}
            else:
                result = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})
        raise AssertionError("Unexpected network endpoint")
