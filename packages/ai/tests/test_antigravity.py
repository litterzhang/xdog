"""Native Antigravity and model-capability tests; all Google requests are mocked."""
from __future__ import annotations

import asyncio
import json
import stat
import time
from dataclasses import asdict, replace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import xdog.ai as ai
from xdog.ai.cli import _cmd_models, main
from xdog.ai.native import NativeOperation, ProtocolRequest
from xdog.ai.protocols.google_generative import GeminiDecoder, encode_context
from xdog.ai.providers.antigravity import AntigravityProvider
from xdog.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    ModelEndpoint,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from xdog.ai.vendors.antigravity import AntigravityVendor, _auth, _model_sync
from xdog.ai.vendors.copilot._model_sync import _model_from_dict, _model_to_dict

CATALOGUE = {"models": {
    "vision": {
        "displayName": "Vision", "supportsImages": True, "supportsImageGeneration": False,
        "maxTokens": 250000, "maxOutputTokens": 8192, "supportsThinking": True,
    },
    "picture": {"inputModalities": ["TEXT", "IMAGE"], "outputModalities": ["TEXT", "IMAGE"]},
    "unknown-image": {"displayName": "Name is not evidence of capabilities"},
    "no-stream": {"supportsStreaming": False, "supportsImages": False, "supportsImageGeneration": False},
}}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(_auth, "auth_file", lambda: tmp_path / "auth.json")
    monkeypatch.setattr(_model_sync, "cache_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(_model_sync, "config_dir", lambda: tmp_path)
    _auth.save_credentials(_auth.Credentials("access-secret", "refresh-secret", time.time() + 3600, "project"))
    _model_sync.write_cache(CATALOGUE)
    return tmp_path


@pytest.fixture
def mock_http(monkeypatch):
    original = httpx.AsyncClient

    def install(handler):
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=transport, **kw))

    return install


def test_provider_registration_and_discovery(isolated, monkeypatch):
    monkeypatch.setattr("xdog.ai.api.auth_file", lambda: isolated / "auth.json")
    provider = ai.provider("antigravity")
    assert provider.id == "antigravity"
    assert ai.load().active_providers() == ["antigravity"]
    assert provider.model("vision").id == "antigravity/vision"
    assert provider.model("antigravity/vision").name == "Vision"


def test_image_input_output_and_protocol_endpoints_are_independent(isolated):
    provider = AntigravityProvider()
    vision = provider.model("vision")
    assert vision.supports_image_input is True
    assert vision.supports_image_output is False
    image = provider.model("picture")
    assert image.supports_image_input is True
    assert image.supports_image_output is True
    unknown = provider.model("unknown-image")
    assert unknown.supports_image_input is None
    assert unknown.supports_image_output is None
    assert unknown.input is None and unknown.output is None
    assert image.supported_generation_protocols == ("google-generative-ai",)
    assert image.endpoints == (
        ModelEndpoint("google-generative-ai", "generate", "/v1internal:generateContent"),
        ModelEndpoint("google-generative-ai", "stream_generate", "/v1internal:streamGenerateContent"),
    )
    assert len(provider.model("no-stream").endpoints) == 1


@pytest.mark.parametrize("limits,context,input_limit,output_limit", [
    ({"maxTokens": 250000, "maxOutputTokens": 64000}, 250000, 0, 64000),
    ({"maxTokens": 1048576, "maxOutputTokens": 65535}, 1048576, 0, 65535),
    ({"maxTokens": 250000, "maxInputTokens": 200000}, 250000, 200000, 0),
    ({"maxInputTokens": 200000}, 200000, 200000, 0),
    ({"maxTokens": True, "maxInputTokens": "200000", "maxOutputTokens": -1}, 0, 0, 0),
    ({}, 0, 0, 0),
])
def test_context_capacity_is_distinct_from_input_and_output_caps(limits, context, input_limit, output_limit):
    model = _model_sync.parse_models({"models": {"test": limits}}, "https://example.invalid")[0]
    assert model.context_window == context
    assert model.max_prompt_tokens == input_limit
    assert model.max_tokens == output_limit


def test_image_generation_catalogue_is_positive_output_evidence_only():
    models = _model_sync.parse_models({
        "models": {"generator": {}, "unknown-image": {}},
        "imageGenerationModelIds": ["generator"],
    }, "https://example.invalid")
    generator, unknown = models
    assert generator.supports_image_output is True
    assert generator.output == ("image",)
    assert generator.supports_image_input is None
    assert unknown.supports_image_output is None


def test_exact_model_overrides_do_not_match_other_names(isolated):
    (isolated / "antigravity-models.json").write_text(json.dumps({
        "unknown-image": {"input": ["text"], "output": ["image"], "tool_calls": False},
    }))
    model = AntigravityProvider().model("unknown-image")
    assert model.supports_image_input is False
    assert model.supports_image_output is True
    assert not model.supports_tool_calls
    assert AntigravityProvider().model("vision").supports_image_input is True


@pytest.mark.parametrize("input_types,output_types", [
    (None, None), (("text", "image"), ("text",)), (("text",), ("image",)), ((), ()),
])
def test_new_metadata_survives_existing_model_cache(input_types, output_types):
    model = Model(
        input=input_types, output=output_types,
        endpoints=(ModelEndpoint("google-generative-ai", "generate", "/v1internal:generateContent"),),
    )
    assert _model_from_dict(_model_to_dict(model)) == model


@pytest.mark.asyncio
async def test_cli_json_exposes_capabilities(isolated, capsys):
    await _cmd_models("antigravity", sync=False, output_json=True)
    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)}
    assert rows["antigravity/vision"]["supports_image_input"] is True
    assert rows["antigravity/vision"]["supports_image_output"] is False
    assert rows["antigravity/vision"]["context_window"] == 250000
    assert rows["antigravity/vision"]["max_prompt_tokens"] == 0
    assert rows["antigravity/unknown-image"]["output"] is None
    assert rows["antigravity/picture"]["endpoints"][0]["protocol"] == "google-generative-ai"


@pytest.mark.asyncio
async def test_cli_text_does_not_call_unknown_pricing_free(isolated, capsys):
    await _cmd_models("antigravity", sync=False)
    output = capsys.readouterr().out
    assert "CONTEXT" not in output and "MAKE IMAGE" not in output
    lines = {line.split()[0]: line.split()[1:] for line in output.splitlines() if line.strip()}
    assert lines["vision"] == ["250k", "in", "8k", "out", "google-generative-ai", "reasoning"]
    assert lines["unknown-image"] == ["?", "in", "?", "out", "google-generative-ai"]
    assert lines["picture"] == ["?", "in", "?", "out", "google-generative-ai", "image_generation"]
    assert "/v1internal:" not in output
    assert "free" not in output


@pytest.mark.asyncio
async def test_cli_details_retains_model_names_and_endpoint_modes(isolated, capsys):
    await _cmd_models("antigravity", sync=False, details=True)
    output = capsys.readouterr().out
    assert "antigravity/vision — Vision" in output
    assert "Non-streaming generation: POST /v1internal:generateContent" in output
    assert "Streaming generation: POST /v1internal:streamGenerateContent" in output
    assert "context: 250,000; max input: ?; max output: 8,192 tokens" in output
    assert "Read image: yes; generate image: no" in output


def test_cli_details_option_is_forwarded(monkeypatch):
    command = AsyncMock()
    monkeypatch.setattr("sys.argv", ["xdog-ai", "models", "antigravity", "--details"])
    monkeypatch.setattr("xdog.ai.cli._cmd_models", command)
    main()
    command.assert_awaited_once_with("antigravity", sync=False, output_json=False, details=True)


@pytest.mark.asyncio
async def test_sync_uses_project_and_separate_cache(isolated, mock_http):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/v1internal:fetchAvailableModels"
        assert json.loads(request.content) == {"project": "project"}
        assert request.headers["authorization"] == "Bearer access-secret"
        return httpx.Response(200, json=CATALOGUE)

    mock_http(handler)
    provider = AntigravityProvider()
    assert len(await provider.sync_models(force=True)) == 4
    await provider.sync_models()
    assert len(requests) == 1
    assert json.loads((isolated / "models.json").read_text())["catalogue"] == CATALOGUE


@pytest.mark.asyncio
async def test_native_image_request_preserves_image_payload(isolated, mock_http):
    response = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": "image-base64"}},
    ]}}]}

    def handler(request):
        assert request.url.path == "/v1internal:generateContent"
        body = json.loads(request.content)
        assert body["model"] == "picture"
        assert body["project"] == "project"
        assert body["requestType"] == "image_gen"
        assert "model" not in body["request"]
        assert body["request"]["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]
        return httpx.Response(200, json={"response": response})

    mock_http(handler)
    request = ProtocolRequest.from_json("google-generative-ai", {
        "model": "cannot-override-model",
        "contents": [{"role": "user", "parts": [{"text": "Draw"}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    })
    result = await AntigravityProvider().request_complete("picture", request)
    assert result.json() == response


@pytest.mark.asyncio
async def test_native_capability_checks_never_authenticate(isolated):
    vendor = AntigravityVendor()
    vendor.tokens.get = AsyncMock(side_effect=AssertionError("No authentication in a capability query"))
    provider = AntigravityProvider(vendor)
    image = ProtocolRequest.from_json(
        "google-generative-ai", {"generationConfig": {"responseModalities": ["IMAGE"]}},
    )
    assert provider.supports_native_request("picture", image)
    assert not provider.supports_native_request("vision", image)
    assert not provider.supports_native_request("missing", image)
    assert not provider.supports_native_request("picture", replace(image, protocol="openai-responses"))
    assert not provider.supports_native_request("picture", replace(image, operation=NativeOperation.COUNT_TOKENS))
    with pytest.raises(NotImplementedError):
        await provider.request_complete("vision", image)
    vendor.tokens.get.assert_not_awaited()


def test_codec_preserves_tool_signatures_images_and_parallel_results():
    model = Model(input=("text", "image"))
    context = Context(
        system_prompt="System",
        messages=(
            UserMessage(content=(TextContent("Look"), ImageContent(data="pixels"))),
            AssistantMessage(content=(
                ThinkingContent("Reasoning", thinking_signature="thinking-sig"),
                TextContent("Checking", text_signature="text-sig"),
                ToolCall("a", "one", {"x": 1}, thought_signature="tool-sig"),
                ToolCall("b", "two", {}),
            )),
            ToolResultMessage(tool_call_id="a", tool_name="one", content=(TextContent("result one"),)),
            ToolResultMessage(tool_call_id="b", tool_name="two", content=(TextContent("result two"),)),
        ),
        tools=(Tool(name="one", parameters={"type": "object"}),),
    )
    body = encode_context(context, model, StreamOptions())
    assert body["contents"][0]["parts"][1]["inlineData"]["data"] == "pixels"
    parts = body["contents"][1]["parts"]
    assert [p.get("thoughtSignature") for p in parts] == ["thinking-sig", "text-sig", "tool-sig", None]
    assert len(body["contents"]) == 3
    assert [p["functionResponse"]["id"] for p in body["contents"][2]["parts"]] == ["a", "b"]
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "one"
    with pytest.raises(ValueError, match="does not accept image"):
        encode_context(context, replace(model, input=("text",)), StreamOptions())


def test_decoder_merges_text_but_preserves_signed_boundaries():
    decoder = GeminiDecoder(Model())
    decoder.feed({"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]})
    decoder.feed({"candidates": [{"content": {"parts": [{"text": "lo", "thoughtSignature": "signed"}]}}]})
    decoder.feed({"candidates": [{"content": {"parts": [{"text": "Second"}]}}]})
    assert decoder.builder.snapshot().content == (
        TextContent("Hello", text_signature="signed"), TextContent("Second"),
    )


@pytest.mark.asyncio
async def test_streaming_text_tools_usage_and_signatures(isolated, mock_http):
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "Checking "} ]}}]},
        {"candidates": [{"content": {"parts": [{"text": "now."}]}}]},
        {"candidates": [{"content": {"parts": [{
            "functionCall": {"id": "call1", "name": "read", "args": {"path": "x"}},
            "thoughtSignature": "opaque-signature",
        }]}, "finishReason": "STOP"}], "usageMetadata": {
            "promptTokenCount": 100, "cachedContentTokenCount": 20,
            "candidatesTokenCount": 8, "thoughtsTokenCount": 2, "totalTokenCount": 110,
        }},
    ]

    def handler(request):
        assert request.url.path == "/v1internal:streamGenerateContent"
        assert request.url.params["alt"] == "sse"
        return httpx.Response(200, text="".join(f"data: {json.dumps({'response': c})}\n\n" for c in chunks))

    mock_http(handler)
    stream = AntigravityProvider().stream("vision", Context(messages=(UserMessage(content="Read x"),)))
    events = [event async for event in stream]
    result = await stream.result()
    assert "".join(event.delta for event in events if isinstance(event, TextDeltaEvent)) == "Checking now."
    assert result.content[0] == TextContent("Checking now.")
    assert result.content[1].thought_signature == "opaque-signature"
    assert result.stop_reason == "toolUse"
    assert result.usage.input == 80 and result.usage.cache_read == 20 and result.usage.output == 10


@pytest.mark.asyncio
async def test_non_streaming_model_uses_generate_content(isolated, mock_http):
    def handler(request):
        assert request.url.path == "/v1internal:generateContent"
        return httpx.Response(200, json={"response": {
            "candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}],
        }})

    mock_http(handler)
    message = await AntigravityProvider().complete("no-stream", Context())
    assert message.content == (TextContent("hello"),)


@pytest.mark.asyncio
async def test_chat_does_not_silently_discard_image_output(isolated, mock_http):
    mock_http(lambda _: httpx.Response(200, text="data: " + json.dumps({"response": {
        "candidates": [{"content": {"parts": [{"inlineData": {"data": "image"}}]}}],
    }}) + "\n\n"))
    message = await AntigravityProvider().complete("picture", Context())
    assert message.stop_reason == "error"
    assert "protocol-native" in message.error_message


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_failed_generation_is_sanitized_and_not_retried(isolated, mock_http, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="access-secret private server response")

    mock_http(handler)
    result = await AntigravityProvider().complete("vision", Context())
    assert result.stop_reason == "error"
    assert "access-secret" not in result.error_message
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_cancellation_closes_stream_and_returns_aborted(isolated, mock_http):
    started = asyncio.Event()
    closed = asyncio.Event()
    cancel = asyncio.Event()

    class Blocked(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            closed.set()

    mock_http(lambda _: httpx.Response(200, stream=Blocked()))
    task = asyncio.create_task(AntigravityProvider().complete("vision", Context(), StreamOptions(cancel=cancel)))
    await asyncio.wait_for(started.wait(), 2)
    cancel.set()
    result = await asyncio.wait_for(task, 2)
    assert result.stop_reason == "aborted"
    assert closed.is_set()


@pytest.mark.asyncio
async def test_refresh_is_serialized_and_preserves_other_provider_credentials(isolated, mock_http):
    path = isolated / "auth.json"
    raw = json.loads(path.read_text())
    raw["copilot"] = {"access_token": "copilot-secret"}
    raw["antigravity"]["expires_at"] = 0
    path.write_text(json.dumps(raw))
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == _auth.TOKEN_URL
        form = parse_qs(request.content.decode())
        assert form["refresh_token"] == ["refresh-secret"]
        assert form["client_id"] == [_auth._CLIENT_ID]
        return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})

    mock_http(handler)
    manager = _auth.TokenManager()
    creds = await asyncio.gather(*(manager.get() for _ in range(5)))
    assert all(value.access_token == "new-access" for value in creds)
    assert len(calls) == 1
    assert json.loads(path.read_text())["copilot"]["access_token"] == "copilot-secret"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "new-access" not in repr(creds[0])


@pytest.mark.asyncio
async def test_missing_auth_never_starts_login(isolated, monkeypatch):
    (isolated / "auth.json").unlink()
    login = AsyncMock(side_effect=AssertionError("Interactive login must be explicit"))
    monkeypatch.setattr(_auth, "login", login)
    result = await AntigravityProvider().complete("vision", Context())
    assert result.stop_reason == "error"
    assert "xdog-ai login antigravity" in result.error_message
    login.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["cloudaicompanionProject", "projectId", "project"])
@pytest.mark.parametrize("value", ["existing-project", {"id": "existing-project"}])
async def test_discovery_accepts_project_response_variants(mock_http, field, value):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "cloudcode-pa.googleapis.com"
        assert request.url.path == "/v1internal:loadCodeAssist"
        assert json.loads(request.content) == {"metadata": {"ideType": "ANTIGRAVITY"}}
        assert request.headers["user-agent"] == _auth._REQUEST_USER_AGENT
        return httpx.Response(200, json={field: value})

    mock_http(handler)
    assert await _auth.discover_project("token") == "existing-project"
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["cloudaicompanionProject", "projectId", "project"])
@pytest.mark.parametrize("value", ["new-project", {"id": "new-project"}])
async def test_onboarding_uses_daily_host_snake_case_and_project_variants(mock_http, field, value):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1internal:loadCodeAssist":
            return httpx.Response(200, json={
                "allowedTiers": [
                    {"id": "other-tier", "isDefault": False},
                    {"id": "default-tier", "isDefault": True},
                ],
            })
        assert request.url.host == "daily-cloudcode-pa.googleapis.com"
        assert request.url.path == "/v1internal:onboardUser"
        assert request.headers["authorization"] == "Bearer token"
        assert request.headers["user-agent"] == _auth._ONBOARD_USER_AGENT
        assert request.headers["x-goog-api-client"] == _auth._GOOG_API_CLIENT
        assert json.loads(request.content) == {
            "tier_id": "default-tier",
            "metadata": {
                "ide_type": "ANTIGRAVITY",
                "ide_version": _auth._CLIENT_VERSION,
                "ide_name": "antigravity",
            },
        }
        return httpx.Response(200, json={"done": True, "response": {field: value}})

    mock_http(handler)
    assert await _auth.discover_project("token") == "new-project"
    assert len(requests) == 2


@pytest.mark.parametrize("raw,expected", [
    ({"currentTier": {"id": "current-tier"}}, "current-tier"),
    ({"allowedTiers": None, "currentTier": {"id": "current-tier"}}, "current-tier"),
    ({"allowedTiers": [{"id": "default", "isDefault": True}], "currentTier": {"id": "current"}}, "default"),
    ({}, "free-tier"),
    ({"currentTier": None, "allowedTiers": [None, {}]}, "free-tier"),
])
def test_onboarding_tier_fallbacks(raw, expected):
    assert _auth._onboarding_tier(raw) == expected


@pytest.mark.asyncio
async def test_pending_onboarding_is_polled_with_same_body(mock_http, monkeypatch):
    bodies = []

    def handler(request):
        if request.url.path == "/v1internal:loadCodeAssist":
            return httpx.Response(200, json={})
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(200, json={"done": False})
        return httpx.Response(200, json={"done": True, "response": {"projectId": "provisioned"}})

    monkeypatch.setattr(_auth, "_ONBOARD_INTERVAL", 0)
    mock_http(handler)
    assert await _auth.discover_project("token") == "provisioned"
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    assert bodies[0]["tier_id"] == "free-tier"


@pytest.mark.asyncio
async def test_completed_onboarding_without_project_reloads_discovery(mock_http):
    operations = []

    def handler(request):
        operations.append(request.url.path)
        if len(operations) == 1:
            return httpx.Response(200, json={})
        if len(operations) == 2:
            return httpx.Response(200, json={"done": True, "response": {}})
        return httpx.Response(200, json={"project": {"id": "visible-after-onboarding"}})

    mock_http(handler)
    assert await _auth.discover_project("token") == "visible-after-onboarding"
    assert operations == [
        "/v1internal:loadCodeAssist", "/v1internal:onboardUser", "/v1internal:loadCodeAssist",
    ]


@pytest.mark.asyncio
async def test_completed_onboarding_without_project_reports_stage(mock_http):
    def handler(request):
        if request.url.path == "/v1internal:loadCodeAssist":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"done": True})

    mock_http(handler)
    with pytest.raises(RuntimeError, match="onboardUser completed without a project"):
        await _auth.discover_project("token")


@pytest.mark.asyncio
async def test_pending_onboarding_reports_pending_not_invalid_account(mock_http, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"done": False})

    monkeypatch.setattr(_auth, "_ONBOARD_INTERVAL", 0)
    mock_http(handler)
    with pytest.raises(RuntimeError, match="still pending"):
        await _auth.discover_project("token")
    assert calls.count("/v1internal:onboardUser") == _auth._ONBOARD_ATTEMPTS


@pytest.mark.asyncio
@pytest.mark.parametrize("response,expected", [
    (httpx.Response(403, json={"message": "sensitive account details"}), "HTTP 403"),
    (httpx.Response(200, json={"error": {"code": 7, "message": "sensitive account details"}}), "operation error"),
    (httpx.Response(200, text="sensitive account details"), "invalid JSON"),
    (httpx.Response(200, json=[]), "non-object response"),
])
async def test_onboarding_errors_are_precise_and_sanitized(mock_http, response, expected):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(200, json={})
        return response

    mock_http(handler)
    with pytest.raises(RuntimeError) as error:
        await _auth.discover_project("token")
    assert "onboardUser" in str(error.value)
    assert expected in str(error.value)
    assert "sensitive account details" not in str(error.value)
    assert len(calls) == 2  # No repeated onboarding after an explicit failure.


@pytest.mark.asyncio
async def test_login_onboarding_failure_does_not_replace_stored_credentials(isolated, mock_http, monkeypatch):
    before = (isolated / "auth.json").read_bytes()
    monkeypatch.setattr(_auth, "receive_code", AsyncMock(return_value="code"))

    def handler(request):
        if str(request.url) == _auth.TOKEN_URL:
            return httpx.Response(200, json={"access_token": "new-token", "refresh_token": "new-refresh"})
        if request.url.path == "/v1internal:loadCodeAssist":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"done": True})

    mock_http(handler)
    with pytest.raises(RuntimeError, match="onboardUser completed"):
        await _auth.login()
    assert (isolated / "auth.json").read_bytes() == before


@pytest.mark.asyncio
async def test_explicit_login_uses_pkce_and_discovers_project(isolated, mock_http, monkeypatch):
    verifier_challenge = []

    async def receive(state, url):
        query = parse_qs(urlsplit(url).query)
        assert query["state"] == [state]
        assert query["code_challenge_method"] == ["S256"]
        verifier_challenge.append(query["code_challenge"][0])
        return "authorization-code"

    def handler(request):
        if str(request.url) == _auth.TOKEN_URL:
            form = parse_qs(request.content.decode())
            assert _auth.generate_code_challenge(form["code_verifier"][0]) == verifier_challenge[0]
            return httpx.Response(200, json={
                "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600,
            })
        assert request.url.path == "/v1internal:loadCodeAssist"
        return httpx.Response(200, json={"cloudaicompanionProject": {"id": "new-project"}})

    monkeypatch.setattr(_auth, "receive_code", receive)
    mock_http(handler)
    await _auth.login()
    creds = _auth.load_credentials()
    assert creds.project_id == "new-project"
    assert creds.refresh_token == "new-refresh"


def test_login_rejects_auth_file_import(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["xdog-ai", "login", "antigravity", "--auth-file", "proxy-auth.json"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "unrecognized arguments: --auth-file" in capsys.readouterr().err


@pytest.mark.parametrize("client_id,secret", [
    (None, None),
    ("", ""),
    ("custom-client", "custom-secret"),
    ("custom-client", None),
    (None, "custom-secret"),
])
def test_internal_provider_settings_ignore_environment(monkeypatch, client_id, secret):
    for name, value in (("ANTIGRAVITY_CLIENT_ID", client_id), ("ANTIGRAVITY_CLIENT_SECRET", secret)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("ANTIGRAVITY_BASE_URL", "https://untrusted.example")
    assert _auth.oauth_client() == (_auth._CLIENT_ID, _auth._CLIENT_SECRET)
    assert _auth.base_url() == "https://daily-cloudcode-pa.googleapis.com"


@pytest.mark.asyncio
async def test_login_and_refresh_work_without_oauth_environment(isolated, mock_http, monkeypatch):
    monkeypatch.delenv("ANTIGRAVITY_CLIENT_ID", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_CLIENT_SECRET", raising=False)
    grants = []
    challenges = []

    async def receive(state, url):
        query = parse_qs(urlsplit(url).query)
        assert query["client_id"] == [_auth._CLIENT_ID]
        assert query["state"] == [state]
        assert query["code_challenge_method"] == ["S256"]
        assert set(query["scope"][0].split()) == set(_auth.SCOPES)
        challenges.append(query["code_challenge"][0])
        return "authorization-code"

    def handler(request):
        if str(request.url) == _auth.TOKEN_URL:
            form = parse_qs(request.content.decode())
            assert form["client_id"] == [_auth._CLIENT_ID]
            assert form["client_secret"] == [_auth._CLIENT_SECRET]
            grant = form["grant_type"][0]
            grants.append(grant)
            if grant == "authorization_code":
                assert _auth.generate_code_challenge(form["code_verifier"][0]) == challenges[0]
                return httpx.Response(200, json={
                    "access_token": "login-access", "refresh_token": "login-refresh", "expires_in": 3600,
                })
            assert form["refresh_token"] == ["login-refresh"]
            return httpx.Response(200, json={"access_token": "refreshed-access", "expires_in": 3600})
        assert request.url.path == "/v1internal:loadCodeAssist"
        return httpx.Response(200, json={"cloudaicompanionProject": "new-project"})

    monkeypatch.setattr(_auth, "receive_code", receive)
    mock_http(handler)
    await _auth.login()
    credentials = _auth.load_credentials()
    assert credentials.project_id == "new-project"
    _auth.save_credentials(replace(credentials, expires_at=0))
    refreshed = await _auth.TokenManager().get()
    assert refreshed.access_token == "refreshed-access"
    assert refreshed.refresh_token == "login-refresh"
    assert grants == ["authorization_code", "refresh_token"]


def test_cli_login_failure_is_readable(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["xdog-ai", "login", "antigravity"])
    monkeypatch.setattr("xdog.ai.cli._cmd_login", AsyncMock(side_effect=RuntimeError("Authorization declined.")))
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    output = capsys.readouterr().err
    assert output == "Login failed: Authorization declined.\n"
    assert "Traceback" not in output


def test_cli_login_network_failure_is_sanitized(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["xdog-ai", "login", "antigravity"])
    monkeypatch.setattr(
        "xdog.ai.cli._cmd_login", AsyncMock(side_effect=httpx.ConnectError("sensitive transport details")),
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    output = capsys.readouterr().err
    assert "network request failed" in output
    assert "sensitive transport details" not in output


@pytest.mark.asyncio
async def test_callback_rejects_wrong_state_and_accepts_correct_state(isolated, monkeypatch):
    original = asyncio.start_server
    listening = asyncio.get_running_loop().create_future()

    async def start(*args, **kwargs):
        server = await original(*args, **kwargs)
        listening.set_result(server.sockets[0].getsockname()[1])
        return server

    monkeypatch.setattr(asyncio, "start_server", start)
    task = asyncio.create_task(_auth.receive_code("expected", "https://example.invalid/authorize", port=0))
    port = await listening

    async def send(state):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"GET /oauth-callback?state={state}&code=code HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
        await writer.drain()
        result = await reader.read()
        writer.close()
        await writer.wait_closed()
        return result

    try:
        assert b"400" in await send("wrong")
        assert not task.done()
        assert b"200" in await send("expected")
        assert await asyncio.wait_for(task, 2) == "code"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_empty_metadata_json_keeps_null_distinct_from_false():
    model = Model(input=None, output=("text",))
    assert asdict(model)["input"] is None
    assert model.supports_image_input is None
    assert model.supports_image_output is False
