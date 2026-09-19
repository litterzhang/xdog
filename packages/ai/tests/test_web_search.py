"""Search discovery is tri-state; tool calling/Responses alone is not evidence."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from xdog.ai.cli import _cmd_models, _cmd_search
from xdog.ai.core import AuthResult
from xdog.ai.protocols.google_generative import encode_context
from xdog.ai.providers.antigravity import AntigravityProvider
from xdog.ai.providers.copilot import CopilotProvider
from xdog.ai.types import AssistantMessage, Context, DoneEvent, Model, StatusEvent, StreamOptions, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.ai.vendors.antigravity._model_sync import parse_models
from xdog.ai.vendors.copilot import _capabilities
from xdog.ai.vendors.copilot._model_sync import _model_from_dict, _model_to_dict, _parse_api_model


@pytest.mark.parametrize("flag", [True, False, None, "true", 1, 0, {}])
def test_copilot_reads_only_explicit_boolean_search_support(flag):
    supports = {"tool_calls": True, "vision": True}
    if flag is not None:
        supports["web_search"] = flag
    model = _parse_api_model({
        "id": "test", "supported_endpoints": ["/responses"],
        "capabilities": {"type": "chat", "supports": supports},
    })
    assert model.supports_web_search is (flag if type(flag) is bool else None)


@pytest.mark.parametrize("flag", [True, False, None])
def test_search_metadata_survives_cache_roundtrip(flag):
    model = Model(supports_web_search=flag)
    assert _model_from_dict(_model_to_dict(model)).supports_web_search is flag
    assert _model_from_dict({}).supports_web_search is None


def test_antigravity_search_catalogue_is_positive_evidence_only():
    models = {model.id: model for model in parse_models({
        "models": {
            "search": {}, "unknown": {"supportsToolCalls": True}, "disabled": {"supportsWebSearch": False},
        },
        "webSearchModelIds": ["search", "disabled"],
    }, "https://example.invalid")}
    assert models["antigravity/search"].supports_web_search is True
    assert models["antigravity/unknown"].supports_web_search is None
    assert models["antigravity/disabled"].supports_web_search is False


@pytest.mark.parametrize("catalogue", [None, "search", {"search": True}, [123]])
def test_invalid_search_catalogue_is_not_positive_evidence(catalogue):
    model = parse_models({
        "models": {"search": {}}, "webSearchModelIds": catalogue,
    }, "https://example.invalid")[0]
    assert model.supports_web_search is None


@pytest.mark.asyncio
async def test_search_tag_details_and_json(monkeypatch, capsys):
    models = (
        Model(
            id="test/yes", api="google-generative-ai", provider="test", reasoning=True,
            output=("image",), supports_web_search=True,
        ),
        Model(id="test/no", api="google-generative-ai", supports_web_search=False),
        Model(id="test/unknown", api="google-generative-ai"),
    )
    monkeypatch.setattr("xdog.ai.cli.ai.provider", lambda _: SimpleNamespace(models=lambda: models))
    await _cmd_models("test", sync=False)
    lines = capsys.readouterr().out.splitlines()
    assert next(line for line in lines if line.strip().startswith("yes ")).endswith(
        "reasoning image_generation web_search",
    )
    assert all("web_search" not in line for line in lines if line.strip().startswith(("no ", "unknown ")))
    await _cmd_models("test", sync=False, details=True)
    detailed = capsys.readouterr().out
    for label in ("yes", "no", "unknown"):
        assert f"web search: {label}" in detailed
    await _cmd_models("test", sync=False, output_json=True)
    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)}
    assert rows["test/yes"]["supports_web_search"] is True
    assert rows["test/no"]["supports_web_search"] is False
    assert rows["test/unknown"]["supports_web_search"] is None


def _copilot(monkeypatch, model, statuses=()):
    provider = CopilotProvider()
    vendor = SimpleNamespace(resolve_auth=AsyncMock(return_value=AuthResult()))
    monkeypatch.setattr(provider, "_resolve", lambda _: model)
    monkeypatch.setattr(provider, "_get_vendor", lambda: vendor)

    def stream(*args):
        message = AssistantMessage(content=(TextContent("Search results"),))

        async def events():
            for status in statuses:
                yield StatusEvent(status=status)
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), future)

    adapter = SimpleNamespace(stream=Mock(side_effect=stream))
    protocol = Mock(return_value=adapter)
    monkeypatch.setattr(provider, "_get_protocol", protocol)
    return provider, vendor, protocol, adapter


@pytest.fixture
def observations(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"copilot": {"access_token": "account-one-token"}}))
    monkeypatch.setattr(_capabilities, "auth_file", lambda: auth)
    monkeypatch.setattr(_capabilities, "data_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("statuses,confirmed", [
    ((), False), (("web_search_started",), False), (("web_search_searching",), False),
    (("web_search_completed",), True),
])
async def test_only_completed_search_tools_are_remembered(observations, monkeypatch, statuses, confirmed):
    model = Model(id="copilot/test", supported_generation_protocols=("openai-responses",))
    provider, _, _, _ = _copilot(monkeypatch, model, statuses)
    await provider.web_search("test", "query")
    assert _capabilities.apply_observed_search((model,))[0].supports_web_search is (True if confirmed else None)
    assert _capabilities._path().exists() is confirmed
    if confirmed:
        raw = _capabilities._path().read_text()
        assert "account-one-token" not in raw


def test_observed_search_is_account_scoped_expires_and_does_not_override_explicit_no(observations, monkeypatch):
    model = Model(id="copilot/test", supports_web_search=None)
    key = _capabilities.account_key()
    _capabilities.record_search(model.id, key)
    assert _capabilities.apply_observed_search((model,))[0].supports_web_search is True
    unsupported = Model(id=model.id, supports_web_search=False)
    assert _capabilities.apply_observed_search((unsupported,))[0].supports_web_search is False
    now = time.time()
    monkeypatch.setattr(_capabilities.time, "time", lambda: now + 86401)
    assert _capabilities.apply_observed_search((model,))[0].supports_web_search is None
    monkeypatch.setattr(_capabilities.time, "time", lambda: now)
    (observations / "auth.json").write_text(json.dumps({"copilot": {"access_token": "another-account"}}))
    assert _capabilities.apply_observed_search((model,))[0].supports_web_search is None
    _capabilities.record_search("copilot/not-recorded", key)
    assert "copilot/not-recorded" not in _capabilities._path().read_text()


@pytest.mark.asyncio
async def test_confirmed_search_survives_catalogue_refresh(observations, monkeypatch):
    model = Model(id="copilot/test")
    _capabilities.record_search(model.id, _capabilities.account_key())
    provider = CopilotProvider()
    vendor = SimpleNamespace(sync_models=AsyncMock(return_value=(model,)))
    monkeypatch.setattr(provider, "_get_vendor", lambda: vendor)
    monkeypatch.setattr("xdog.ai.vendors.copilot._model_sync.list_models", lambda: (model,))
    assert (await provider.sync_models(force=True))[0].supports_web_search is True
    assert provider.model(model.id).supports_web_search is True
    assert CopilotProvider().models()[0].supports_web_search is True


@pytest.mark.asyncio
async def test_capability_cache_write_failure_does_not_break_search(observations, monkeypatch):
    model = Model(id="copilot/test", supported_generation_protocols=("openai-responses",))
    provider, _, _, _ = _copilot(monkeypatch, model, ("web_search_completed",))
    monkeypatch.setattr(
        "xdog.ai.providers.copilot.record_search", Mock(side_effect=OSError("Read-only capability cache")),
    )
    result = await provider.web_search("test", "query")
    assert result.content == (TextContent("Search results"),)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,confirmed", [("completed", True), ("failed", False), ("in_progress", False)])
async def test_responses_adapter_exposes_completed_search_items(monkeypatch, status, confirmed):
    from xdog.ai.protocols.openai_responses import _stream_impl

    def handler(request):
        frames = [
            {"type": "response.output_item.done", "item": {"type": "web_search_call", "status": status}},
            {"type": "response.completed", "response": {"id": "response", "status": "completed"}},
        ]
        return httpx.Response(200, text="".join(f"data: {json.dumps(frame)}\n\n" for frame in frames))

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    events = [event async for event in _stream_impl(
        Model(id="test", base_url="https://example.invalid"), Context(), StreamOptions(web_search=True), AuthResult(),
    )]
    assert any(
        isinstance(event, StatusEvent) and event.status == "web_search_completed" for event in events
    ) is confirmed


@pytest.mark.asyncio
@pytest.mark.parametrize("support", [True, None])
async def test_copilot_search_chooses_responses_instead_of_silently_ignoring_search(monkeypatch, support):
    model = Model(
        id="copilot/test", api="openai-completions",
        supported_generation_protocols=("openai-completions", "openai-responses"),
        supports_web_search=support,
    )
    provider, vendor, protocol, adapter = _copilot(monkeypatch, model)
    result = await provider.web_search("test", "query")
    assert result.content == (TextContent("Search results"),)
    protocol.assert_called_once_with("openai-responses")
    assert adapter.stream.call_args.args[2].web_search is True
    assert model.supports_web_search is support  # Do not turn an attempted request into capability evidence.
    vendor.resolve_auth.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocols,support", [
    (("openai-completions",), None),
    (("anthropic-messages",), True),
    (("openai-responses",), False),
])
async def test_copilot_rejects_unavailable_search_before_auth(monkeypatch, protocols, support):
    model = Model(id="copilot/test", supported_generation_protocols=protocols, supports_web_search=support)
    provider, vendor, _, _ = _copilot(monkeypatch, model)
    with pytest.raises(NotImplementedError):
        await provider.web_search("test", "query")
    vendor.resolve_auth.assert_not_awaited()


@pytest.mark.asyncio
async def test_ordinary_chat_keeps_its_original_protocol(monkeypatch):
    model = Model(
        id="copilot/test", api="openai-completions",
        supported_generation_protocols=("openai-completions", "openai-responses"),
    )
    provider, _, protocol, _ = _copilot(monkeypatch, model)
    await provider.complete("test", Context())
    protocol.assert_called_once_with("openai-completions")


@pytest.mark.parametrize("support", [True, None])
def test_gemini_search_request_contains_grounding_tool(support):
    body = encode_context(Context(), Model(supports_web_search=support), StreamOptions(web_search=True))
    assert body["tools"] == [{"googleSearch": {}}]


@pytest.mark.asyncio
async def test_antigravity_explicit_no_search_is_rejected_without_auth(monkeypatch):
    provider = AntigravityProvider()
    monkeypatch.setattr(provider, "_resolve", lambda _: Model(id="antigravity/test", supports_web_search=False))
    auth = AsyncMock(side_effect=AssertionError("Should not authenticate"))
    monkeypatch.setattr(provider._vendor.tokens, "get", auth)
    result = await provider.web_search("test", "query")
    assert result.stop_reason == "error"
    assert "does not support web search" in result.error_message
    auth.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_cli_fails_on_provider_error(monkeypatch, capsys):
    provider = SimpleNamespace(web_search=AsyncMock(return_value=AssistantMessage(
        stop_reason="error", error_message="Search unavailable.",
    )))
    monkeypatch.setattr("xdog.ai.cli.ai.provider", lambda _: provider)
    with pytest.raises(SystemExit) as error:
        await _cmd_search(provider="test", model="test", query="query")
    assert error.value.code == 1
    assert "Search unavailable." in capsys.readouterr().err
