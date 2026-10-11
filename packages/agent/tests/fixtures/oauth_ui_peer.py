"""Independent offline issuer/resource peer used by real client UI/terminal tests."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, unquote_plus, urlsplit

import httpx

RESOURCE = "https://resource.test/mcp"
ISSUER = "https://issuer.test/tenant"
PROFILE = {"issuer": ISSUER, "client_id": "registered-client", "scopes": ["tools:read"]}


class Peer:
    def __init__(self, mode="pre-registered"):
        self.requests = []
        self.mode = mode
        self.method = "none"
        self.secret = ""
        self.client_id = "registered-client"
        self.registration_posts = []
        self.challenge = ""
        self.transport = httpx.MockTransport(self.handle)
        self.token_posts = 0
        self.issue_refresh = False
        self.refresh_posts = []
        self.assertions = []
        self.public_rsa: tuple[int, int] | None = None

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
                "response_types_supported": ["code"], "token_endpoint_auth_methods_supported": [self.method],
                "token_endpoint_auth_signing_alg_values_supported": [
                    "RS256" if self.method == "private_key_jwt" else "HS256"],
                "authorization_response_iss_parameter_supported": True,
                "registration_endpoint": "https://issuer.test/register",
                "client_id_metadata_document_supported": True})
        if path == "/register":
            body = json.loads(request.content)
            assert body["token_endpoint_auth_method"] == "none" and body["grant_types"] == ["authorization_code"]
            self.registration_posts.append(body)
            self.client_id = "issued-native-client"
            return httpx.Response(201, json={"client_id": self.client_id, "token_endpoint_auth_method": "none",
                                           "client_secret": "IGNORED-REGISTRATION-SECRET"})
        if path == "/token":
            body = parse_qs(request.content.decode())
            if self.method == "client_secret_basic":
                raw = base64.b64decode(request.headers["authorization"].removeprefix("Basic ")).decode()
                identifier, secret = raw.split(":", 1)
                assert unquote_plus(identifier) == self.client_id and unquote_plus(secret) == self.secret
                assert "client_id" not in body and "client_secret" not in body
            elif self.method == "client_secret_post":
                assert "authorization" not in request.headers and body.pop("client_secret") == [self.secret]
            elif self.method in {"client_secret_jwt", "private_key_jwt"}:
                assert "authorization" not in request.headers and "client_secret" not in body
                assert body.pop("client_assertion_type") == ["urn:ietf:params:oauth:client-assertion-type:jwt-bearer"]
                assertion = body.pop("client_assertion")[0]
                header, claims, signature = assertion.split(".")
                def decode(value):
                    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
                if self.method == "client_secret_jwt":
                    assert json.loads(decode(header)) == {"alg": "HS256", "typ": "JWT"}
                    assert hmac.compare_digest(decode(signature), hmac.digest(
                        self.secret.encode(), (header + "." + claims).encode(), "sha256"))
                else:
                    assert json.loads(decode(header)) == {"alg": "RS256", "typ": "JWT"}
                    assert self.public_rsa is not None
                    modulus, exponent = self.public_rsa
                    size = (modulus.bit_length() + 7) // 8
                    encoded = pow(int.from_bytes(decode(signature)), exponent, modulus).to_bytes(size)
                    digest = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(
                        (header + "." + claims).encode()).digest()
                    assert encoded == b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
                claim = json.loads(decode(claims))
                assert claim["iss"] == claim["sub"] == self.client_id and claim["aud"] == "https://issuer.test/token"
                assert claim["exp"] - claim["iat"] == 60
                self.assertions.append(assertion)
            else:
                assert "authorization" not in request.headers

            assert body["resource"] == [RESOURCE]
            if self.method != "client_secret_basic":
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
