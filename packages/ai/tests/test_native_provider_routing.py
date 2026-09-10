from __future__ import annotations

from typing import Any

import pytest
from xdog.ai.core import AuthResult, BaseProvider
from xdog.ai.native import NativeEventStream, NativeOperation, NativeResponse, NativeResponseStart, ProtocolRequest
from xdog.ai.protocols.anthropic_messages import AnthropicMessagesProtocol
from xdog.ai.protocols.openai_completions import OpenAICompletionsProtocol
from xdog.ai.protocols.openai_responses import OpenAIResponsesProtocol
from xdog.ai.providers.copilot import CopilotProvider
from xdog.ai.providers.runtime import Runtime
from xdog.ai.types import ImageContent, Model, UserMessage
from xdog.ai.vendors.copilot import _build_dynamic_headers


async def test_copilot_native_complete_uses_requested_supported_protocol_and_wire_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CopilotProvider()
    resolved = Model(
        id="copilot/claude-test",
        api="openai-completions",
        provider="copilot",
        base_url="https://catalog.invalid",
        supported_protocols=("openai-completions", "anthropic-messages"),
    )
    provider._model_cache[resolved.id] = resolved
    captured: dict[str, Any] = {}

    class Protocol:
        def supports_native_operation(self, operation: NativeOperation) -> bool:
            return AnthropicMessagesProtocol().supports_native_operation(operation)

        def native_auth_context(self, request: ProtocolRequest) -> Any:
            return AnthropicMessagesProtocol().native_auth_context(request)

        async def request_complete(self, model: Model, request: ProtocolRequest, auth: AuthResult) -> NativeResponse:
            captured.update(model=model, request=request, auth=auth)
            return NativeResponse(status=200, body=b"{}")

    provider._protocols["anthropic-messages"] = Protocol()

    async def resolve_auth(model: Model, context: Any = None) -> AuthResult:
        assert model is resolved
        assert context is not None
        assert context.messages == ()
        return AuthResult(api_key="vendor", base_url="https://token.invalid")

    monkeypatch.setattr(provider._get_vendor(), "resolve_auth", resolve_auth)
    request = ProtocolRequest.from_json("anthropic-messages", {"model": "claude-test", "messages": [], "max_tokens": 1})

    response = await provider.request_complete("claude-test", request)

    assert response.status == 200
    assert captured["model"].id == "claude-test"
    assert captured["model"].base_url == "https://token.invalid"
    assert captured["request"] is request
    assert captured["auth"].api_key == "vendor"


async def test_copilot_native_auth_context_preserves_last_role_and_base64_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CopilotProvider()
    resolved = Model(
        id="copilot/claude-test",
        api="anthropic-messages",
        provider="copilot",
        supported_protocols=("anthropic-messages",),
    )
    provider._model_cache[resolved.id] = resolved
    captured_contexts: list[Any] = []

    class Protocol:
        def supports_native_operation(self, operation: NativeOperation) -> bool:
            return AnthropicMessagesProtocol().supports_native_operation(operation)

        def native_auth_context(self, request: ProtocolRequest) -> Any:
            return AnthropicMessagesProtocol().native_auth_context(request)

        async def request_complete(
            self,
            model: Model,
            request: ProtocolRequest,
            auth: AuthResult,
        ) -> NativeResponse:
            return NativeResponse(status=200, body=b"{}")

    provider._protocols["anthropic-messages"] = Protocol()

    async def resolve_auth(model: Model, context: Any = None) -> AuthResult:
        captured_contexts.append(context)
        return AuthResult(api_key="vendor")

    monkeypatch.setattr(provider._get_vendor(), "resolve_auth", resolve_auth)
    request = ProtocolRequest.from_json("anthropic-messages", {
        "model": "claude-test",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect this"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "aW1hZ2U=",
                    },
                },
            ],
        }],
        "max_tokens": 1,
    })

    await provider.request_complete("claude-test", request)

    context = captured_contexts[0]
    assert isinstance(context.messages[-1], UserMessage)
    assert context.messages[-1].content == (
        ImageContent(data="aW1hZ2U=", mime_type="image/png"),
    )


@pytest.mark.parametrize(
    ("protocol", "body", "expected_initiator"),
    [
        (
            AnthropicMessagesProtocol(),
            {
                "model": "claude-test",
                "messages": [{"role": "assistant", "content": "prior"}],
                "max_tokens": 1,
            },
            "agent",
        ),
        (
            OpenAIResponsesProtocol(),
            {
                "model": "gpt-test",
                "input": [
                    {"role": "assistant", "content": [{"type": "output_text", "text": "prior"}]},
                    {
                        "role": "user",
                        "content": [{"type": "input_image", "image_url": "https://example.invalid/image.png"}],
                    },
                ],
            },
            "user",
        ),
        (
            OpenAICompletionsProtocol(),
            {
                "model": "gpt-test",
                "messages": [
                    {"role": "user", "content": "question"},
                    {"role": "tool", "tool_call_id": "call_1", "content": "answer"},
                ],
            },
            "agent",
        ),
    ],
    ids=("anthropic", "responses", "chat"),
)
def test_native_auth_context_drives_copilot_headers(
    protocol: Any,
    body: dict[str, Any],
    expected_initiator: str,
) -> None:
    request = ProtocolRequest.from_json(protocol.id, body)

    context = protocol.native_auth_context(request)
    headers = _build_dynamic_headers(context)

    assert headers["X-Initiator"] == expected_initiator
    if protocol.id == "openai-responses":
        assert headers["Copilot-Vision-Request"] == "true"
    else:
        assert "Copilot-Vision-Request" not in headers


def test_native_auth_context_detects_chat_data_url_image() -> None:
    request = ProtocolRequest.from_json("openai-completions", {
        "model": "gpt-test",
        "messages": [{
            "role": "user",
            "content": [{
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,aW1hZ2U="},
            }],
        }],
    })

    context = OpenAICompletionsProtocol().native_auth_context(request)

    assert _build_dynamic_headers(context)["Copilot-Vision-Request"] == "true"


def test_protocol_native_operation_support_is_constrained() -> None:
    assert AnthropicMessagesProtocol().supports_native_operation(NativeOperation.GENERATE)
    assert AnthropicMessagesProtocol().supports_native_operation(NativeOperation.COUNT_TOKENS)
    assert OpenAIResponsesProtocol().supports_native_operation(NativeOperation.GENERATE)
    assert not OpenAIResponsesProtocol().supports_native_operation(NativeOperation.COUNT_TOKENS)
    assert OpenAICompletionsProtocol().supports_native_operation(NativeOperation.GENERATE)
    assert not OpenAICompletionsProtocol().supports_native_operation(NativeOperation.COUNT_TOKENS)


def test_base_provider_native_preflight_defaults_to_false() -> None:
    class Provider(BaseProvider):
        @property
        def id(self) -> str:
            return "fixture"

        @property
        def name(self) -> str:
            return "Fixture"

        def models(self) -> tuple[Model, ...]:
            return ()

        def model(self, name: str) -> Model | None:
            return None

        def stream(self, model: str, context: Any, options: Any = None) -> Any:
            raise NotImplementedError

        async def complete(self, model: str, context: Any, options: Any = None) -> Any:
            raise NotImplementedError

        async def embed(self, model: str, input: Any) -> Any:
            raise NotImplementedError

        async def web_search(self, model: str, query: str) -> Any:
            raise NotImplementedError

        async def login(self) -> str:
            return ""

        async def sync_models(self, *, ttl: float = 86400, force: bool = False) -> tuple[Model, ...]:
            return ()

    request = ProtocolRequest.from_json(
        "anthropic-messages",
        {"model": "model", "messages": []},
        operation=NativeOperation.COUNT_TOKENS,
    )

    assert not Provider().supports_native_request("model", request)


def test_copilot_count_tokens_preflight_is_static_and_operation_aware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CopilotProvider()
    native = Model(
        id="copilot/claude-test",
        api="openai-completions",
        provider="copilot",
        supported_generation_protocols=("anthropic-messages",),
    )
    openai_only = Model(
        id="copilot/gpt-test",
        api="openai-responses",
        provider="copilot",
        supported_generation_protocols=("openai-responses",),
    )
    embedding = Model(
        id="copilot/embed-test",
        api="openai-completions",
        provider="copilot",
        model_type="embeddings",
        supported_generation_protocols=(),
    )
    provider._model_cache = {model.id: model for model in (native, openai_only, embedding)}

    def fail_vendor() -> Any:
        raise AssertionError("native preflight must not resolve vendor authentication")

    monkeypatch.setattr(provider, "_get_vendor", fail_vendor)

    def count_request(model: str) -> ProtocolRequest:
        return ProtocolRequest.from_json(
            "anthropic-messages",
            {"model": model, "messages": []},
            operation=NativeOperation.COUNT_TOKENS,
        )

    assert provider.supports_native_request("claude-test", count_request("claude-test"))
    assert not provider.supports_native_request("gpt-test", count_request("gpt-test"))
    assert not provider.supports_native_request("embed-test", count_request("embed-test"))


async def test_copilot_count_tokens_rejects_stream_before_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CopilotProvider()
    model = Model(
        id="copilot/claude-test",
        api="anthropic-messages",
        provider="copilot",
        supported_generation_protocols=("anthropic-messages",),
    )
    provider._model_cache[model.id] = model

    def fail_vendor() -> Any:
        raise AssertionError("count streaming must fail before authentication")

    monkeypatch.setattr(provider, "_get_vendor", fail_vendor)
    request = ProtocolRequest.from_json(
        "anthropic-messages",
        {"model": "claude-test", "messages": []},
        operation=NativeOperation.COUNT_TOKENS,
    )

    with pytest.raises(NotImplementedError, match="cannot be streamed"):
        await provider.request_stream("claude-test", request)


async def test_copilot_count_tokens_rejects_unsupported_model_before_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CopilotProvider()
    model = Model(
        id="copilot/gpt-test",
        api="openai-responses",
        provider="copilot",
        supported_generation_protocols=("openai-responses",),
    )
    provider._model_cache[model.id] = model

    def fail_vendor() -> Any:
        raise AssertionError("unsupported count request must fail before authentication")

    monkeypatch.setattr(provider, "_get_vendor", fail_vendor)
    request = ProtocolRequest.from_json(
        "anthropic-messages",
        {"model": "gpt-test", "messages": []},
        operation=NativeOperation.COUNT_TOKENS,
    )

    with pytest.raises(NotImplementedError, match="does not support protocol"):
        await provider.request_complete("gpt-test", request)


async def test_copilot_native_rejects_embedding_before_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CopilotProvider()
    model = Model(
        id="copilot/embed-test",
        api="openai-completions",
        provider="copilot",
        model_type="embeddings",
        supported_protocols=("openai-completions",),
        supported_generation_protocols=(),
    )
    provider._model_cache[model.id] = model
    auth_called = False

    async def resolve_auth(model: Model, context: Any = None) -> AuthResult:
        nonlocal auth_called
        auth_called = True
        return AuthResult(api_key="vendor")

    monkeypatch.setattr(provider._get_vendor(), "resolve_auth", resolve_auth)
    request = ProtocolRequest.from_json("openai-completions", {
        "model": "embed-test",
        "messages": [],
    })

    with pytest.raises(NotImplementedError, match="does not support protocol"):
        await provider.request_complete("embed-test", request)

    assert auth_called is False


async def test_copilot_native_uses_exact_generation_protocol_and_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CopilotProvider()
    model = Model(
        id="copilot/gpt-test",
        api="openai-completions",
        preferred_protocol="openai-completions",
        provider="copilot",
        supported_protocols=("openai-completions", "openai-responses"),
        supported_generation_protocols=("openai-responses",),
    )
    provider._model_cache[model.id] = model
    captured: dict[str, Any] = {}

    class Protocol:
        def supports_native_operation(self, operation: NativeOperation) -> bool:
            return OpenAIResponsesProtocol().supports_native_operation(operation)

        def native_auth_context(self, request: ProtocolRequest) -> Any:
            captured["context_request"] = request
            return OpenAIResponsesProtocol().native_auth_context(request)

        async def request_complete(
            self,
            wire_model: Model,
            request: ProtocolRequest,
            auth: AuthResult,
        ) -> NativeResponse:
            captured.update(model=wire_model, request=request, auth=auth)
            return NativeResponse(status=200, body=b"{}")

    provider._protocols["openai-responses"] = Protocol()

    async def resolve_auth(resolved: Model, context: Any = None) -> AuthResult:
        captured["auth_model"] = resolved
        captured["auth_context"] = context
        return AuthResult(api_key="vendor")

    monkeypatch.setattr(provider._get_vendor(), "resolve_auth", resolve_auth)
    request = ProtocolRequest.from_json("openai-responses", {
        "model": "gpt-test",
        "input": "hello",
    })

    await provider.request_complete("gpt-test", request)

    assert captured["context_request"] is request
    assert captured["auth_model"] is model
    assert isinstance(captured["auth_context"].messages[-1], UserMessage)
    assert captured["request"] is request


async def test_copilot_sync_replaces_stale_in_memory_models(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = CopilotProvider()
    stale = Model(
        id="copilot/retired",
        api="anthropic-messages",
        provider="copilot",
        supported_protocols=("anthropic-messages",),
    )
    changed = Model(
        id="copilot/changed",
        api="anthropic-messages",
        provider="copilot",
        supported_protocols=("anthropic-messages",),
    )
    refreshed = Model(
        id="copilot/changed",
        api="openai-responses",
        provider="copilot",
        supported_protocols=("openai-responses",),
    )
    provider._model_cache = {stale.id: stale, changed.id: changed}

    async def sync_models(ttl: float, force: bool) -> tuple[Model, ...]:
        return (refreshed,)

    monkeypatch.setattr(provider._get_vendor(), "sync_models", sync_models)

    result = await provider.sync_models()

    assert result == (refreshed,)
    assert provider.model("changed") is refreshed
    assert provider.model("retired") is None


async def test_copilot_native_request_rejects_unadvertised_protocol() -> None:
    provider = CopilotProvider()
    model = Model(
        id="copilot/gpt-test",
        api="openai-responses",
        provider="copilot",
        supported_protocols=("openai-responses",),
    )
    provider._model_cache[model.id] = model
    request = ProtocolRequest.from_json("anthropic-messages", {"model": "gpt-test", "messages": [], "max_tokens": 1})

    with pytest.raises(NotImplementedError, match="does not support protocol"):
        await provider.request_complete("gpt-test", request)


async def test_runtime_routes_native_complete_to_provider() -> None:
    calls: list[Any] = []
    support_calls: list[Any] = []

    class Provider(BaseProvider):
        @property
        def id(self) -> str:
            return "fixture"

        @property
        def name(self) -> str:
            return "Fixture"

        def models(self) -> tuple[Model, ...]:
            return ()

        def model(self, name: str) -> Model | None:
            return None

        def stream(self, model: str, context: Any, options: Any = None) -> Any:
            raise NotImplementedError

        async def complete(self, model: str, context: Any, options: Any = None) -> Any:
            raise NotImplementedError

        async def embed(self, model: str, input: Any) -> Any:
            raise NotImplementedError

        async def web_search(self, model: str, query: str) -> Any:
            raise NotImplementedError

        def supports_native_request(self, model: str, request: ProtocolRequest) -> bool:
            support_calls.append((model, request))
            return True

        async def request_complete(self, model: str, request: ProtocolRequest) -> NativeResponse:
            calls.append((model, request))
            return NativeResponse(status=200, body=b"{}")

        async def request_stream(self, model: str, request: ProtocolRequest) -> NativeEventStream:
            async def events():
                if False:
                    yield

            return NativeEventStream(NativeResponseStart(200), events(), _close)

        async def login(self) -> str:
            return ""

        async def sync_models(self, *, ttl: float = 86400, force: bool = False) -> tuple[Model, ...]:
            return ()

    async def _close() -> None:
        return None

    runtime = Runtime()
    runtime._active["fixture"] = Provider()
    request = ProtocolRequest.from_json("anthropic-messages", {"model": "m", "messages": [], "max_tokens": 1})

    assert runtime.supports_native_request("fixture/model", request)
    assert runtime.supports_native_request("model", request)
    response = await runtime.request_complete("fixture/model", request)

    assert response.status == 200
    assert support_calls == [("model", request), ("model", request)]
    assert calls == [("model", request)]
