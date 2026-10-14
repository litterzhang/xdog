from __future__ import annotations

from dataclasses import replace

import pytest
from xdog.agent.helpers import stream_fn_from_provider
from xdog.agent.supervision_config import ChildConfig, parse_child_start
from xdog.agent.supervision_model import resolve_child_model
from xdog.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from xdog.ai.utils.event_stream import EventStream


class CatalogStream:
    def __init__(self):
        self.models = {
            "first/parent": Model(id="first/parent", reasoning=True, supported_efforts=("low", "high"),
                                  context_window=128000, max_tokens=4000, input=("text", "image")),
            "second/reviewer": Model(id="second/reviewer", reasoning=True, supported_efforts=("low", "medium"),
                                     context_window=64000, max_prompt_tokens=32000, max_tokens=1500,
                                     supports_tool_calls=False),
            "second/plain": Model(id="second/plain", context_window=64000, max_tokens=2000),
        }
        self.calls = []
        self.lookups = []

    def resolve_model(self, name):
        self.lookups.append(name)
        return self.models.get(name)

    def __call__(self, model, context, options):
        self.calls.append((model, context, options))
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))


def select(stream, config, options=None):
    return resolve_child_model(stream, "first/parent", options or StreamOptions(thinking="high"),
                               ChildConfig.from_dict(config), 128000, True)


def test_defaults_preserve_host_options_and_explicit_tool_support_without_resolver_io():
    stream = CatalogStream()
    options = StreamOptions(thinking="high", max_tokens=3000, prompt_cache_key="PARENT-CACHE")
    selected = resolve_child_model(stream, "first/parent", options, ChildConfig(), 90000, False)
    assert selected.options.thinking == "high" and selected.options.max_tokens == 3000
    assert not selected.supports_tool_calls and selected.context_window == 90000 and not stream.lookups
    assert select(stream, {"thinking": "off"}).options.thinking is None and not stream.lookups


async def test_model_reasoning_routing_is_canonical_and_output_cap_comes_from_same_adapter():
    stream = CatalogStream()
    chosen = select(stream, {"model": "second/reviewer", "thinking": "low"},
                    StreamOptions(thinking="high", max_tokens=5000, prompt_cache_key="PARENT-CACHE"))
    assert chosen.id == "second/reviewer" and chosen.options.thinking == "low"
    assert chosen.options.max_tokens == 1500 and chosen.context_window == 64000 and not chosen.supports_tool_calls
    assert chosen.options.prompt_cache_key is None and not stream.calls
    context = Context(messages=[UserMessage(content="task")])
    await chosen.stream(stream)(chosen.id, context, chosen.options).collect()
    assert stream.calls[0][0] == "second/reviewer" and stream.calls[0][2].thinking == "low"
    assert "Effective child model: second/reviewer" in chosen.prompt("PARENT-RESTRICTIONS")


@pytest.mark.parametrize("config", [
    {"model": "inactive/model"}, {"model": "second/plain", "thinking": "high"},
    {"model": "second/reviewer", "thinking": "xhigh"}, {"model": "second/reviewer"},
])
def test_unavailable_or_unsupported_selection_fails_before_provider_calls(config):
    stream = CatalogStream()
    with pytest.raises(ValueError):
        select(stream, config)
    assert stream.calls == []


def test_custom_adapters_cannot_silently_route_unverified_model_or_effort():
    def no_metadata(*args):
        raise AssertionError("Must fail before request")
    for config in ({"model": "second/reviewer"}, {"thinking": "medium"}):
        with pytest.raises(ValueError):
            select(no_metadata, config)
    assert select(no_metadata, {"thinking": "off"}).options.thinking is None


@pytest.mark.parametrize("replacement", [
    {"model_type": "embeddings"}, {"supports_streaming": False}, {"context_window": 0}, {"id": "bad identity"},
])
def test_invalid_catalog_capabilities_never_send_chat_to_incompatible_model(replacement):
    stream = CatalogStream()
    stream.models["second/plain"] = replace(stream.models["second/plain"], **replacement)
    with pytest.raises(ValueError):
        select(stream, {"model": "second/plain"})
    assert not stream.calls


def test_cross_model_history_omits_provider_continuity_without_mutating_original_tool_pairs():
    stream = CatalogStream()
    chosen = select(stream, {"model": "second/plain"})
    original = [UserMessage(content="old"), AssistantMessage(response_id="OLD-RESPONSE", content=(
        ThinkingContent(thinking="private old reasoning", thinking_signature="OLD-THINKING"),
        TextContent(text="old answer", text_signature="OLD-TEXT"),
        ToolCall(id="call", name="read", arguments={}, thought_signature="OLD-CALL"))),
        ToolResultMessage(tool_call_id="call", tool_name="read", content=(TextContent(text="result"),))]
    result = chosen.history(original)
    assert "OLD-THINKING" not in str(result) and "OLD-TEXT" not in str(result) and "OLD-CALL" not in str(result)
    assert "private old reasoning" not in str(result) and "old answer" in str(result) and "result" in str(result)
    assert "OLD-THINKING" in str(original) and original[1].response_id == "OLD-RESPONSE"
    assert len(result) == 3 and result[1].response_id is None


def test_changed_model_rechecks_transformed_images_and_conservative_context_before_request():
    stream = CatalogStream()
    chosen = select(stream, {"model": "second/plain"})
    request = chosen.stream(stream)
    with pytest.raises(ValueError, match="rendered image context"):
        request(chosen.id, Context(messages=[UserMessage(content=(ImageContent(data="binary"),))]), chosen.options)
    stream.models["second/plain"] = replace(stream.models["second/plain"], context_window=3000)
    chosen = select(stream, {"model": "second/plain"})
    with pytest.raises(ValueError, match="context too large"):
        chosen.stream(stream)(chosen.id, Context(messages=[UserMessage(content="x" * 5000)]), chosen.options)
    assert not stream.calls


def test_provider_adapter_resolves_only_its_actual_provider_without_activating_fallback():
    class Peer:
        def model(self, name):
            return Model(id="first/known", context_window=128000)
        def stream(self, model, context, options):
            raise AssertionError("No request during metadata resolution")
    adapter = stream_fn_from_provider(Peer())
    assert adapter.resolve_model("first/known").id == "first/known"
    assert adapter.resolve_model("inactive/known") is None


def test_model_reasoning_config_is_strict() -> None:

    def _check_case(config):
        with pytest.raises(ValueError):
            ChildConfig.from_dict(config)

    for config in [{"model": ""}, {"model": "bad\nmodel"}, {"thinking": "normal"}, {"thinking": []}, {"model": 123}]:
        _check_case(config)


def test_cli_model_reasoning_flags_retain_literal_task():
    task, access, config = parse_child_start('--model second/reviewer --thinking low -- --literal "task"')
    assert config.model == "second/reviewer" and config.thinking == "low" and task == '--literal "task"'


def test_same_model_selection_keeps_host_tool_override_and_parent_prompt_limits():
    stream = CatalogStream()
    selected = resolve_child_model(stream, "first/parent", StreamOptions(thinking="low"),
                                   ChildConfig.from_dict({"model": "first/parent"}), 128000, False)
    assert selected.supports_tool_calls is False and not selected.switched
    inherited = resolve_child_model(stream, "first/parent", StreamOptions(), ChildConfig(), 128000, True,
                                    parent_max_prompt_tokens=60000)
    assert inherited.max_prompt_tokens == 60000


def _image_context(count=1, *, tool=False):
    import base64
    # The neutral codec checks MIME/signature; real UI file/header checks belong
    # to the attachment reader. No remote URI or filesystem resolution here.
    image = ImageContent(data=base64.b64encode(b'\x89PNG\r\n\x1a\nchild-image').decode(), mime_type='image/png')
    parts = (image,) * count
    message = (ToolResultMessage(tool_call_id='image', tool_name='image', content=parts) if tool
               else UserMessage(content=parts))
    return Context(messages=[message])


@pytest.mark.parametrize('config', [{}, {'thinking': 'off'}, {'model': 'first/parent'}])
@pytest.mark.parametrize('tool', [False, True])
async def test_default_same_model_children_validate_actual_user_and_tool_images(config, tool):
    stream = CatalogStream()
    selected = select(stream, config)
    context = _image_context(tool=tool)
    await selected.stream(stream)(selected.id, context, selected.options).collect()
    assert stream.calls[-1][1] is context
    assert stream.lookups[-1] == selected.id


def test_final_child_image_admission_fails_before_adapter_call() -> None:

    # Never fall back to the earlier selection metadata.
    # Never use old metadata for an unadvertised new route.
    def _check_case(scenario):
        from xdog.ai.types import VisionLimits

        stream = CatalogStream()
        selected = select(stream, {})
        context = _image_context()
        model = selected.id
        if scenario == "unknown":
            stream.models.clear()
        elif scenario == "text-only":
            stream.models[model] = replace(stream.models[model], input=("text",))
        elif scenario == "too-many":
            context = _image_context(5)
            stream.models[model] = replace(stream.models[model], vision_limits=VisionLimits(max_prompt_images=4))
        elif scenario == "too-large":
            stream.models[model] = replace(stream.models[model], vision_limits=VisionLimits(max_prompt_image_size=8))
        elif scenario == "media":
            stream.models[model] = replace(
                stream.models[model], vision_limits=VisionLimits(supported_media_types=("image/jpeg",))
            )
        elif scenario == "withdrawn":
            selected = select(stream, {"model": model})
            stream.models.clear()
        elif scenario == "model-type":
            stream.models[model] = replace(stream.models[model], model_type="embeddings")
        elif scenario == "streaming":
            stream.models[model] = replace(stream.models[model], supports_streaming=False)
        elif scenario == "malformed":
            context = Context(messages=[UserMessage(content=(ImageContent(data="not-base64", mime_type="image/png"),))])
        else:
            model = "inactive/other"
        with pytest.raises(ValueError):
            selected.stream(stream)(model, context, selected.options)
        assert not stream.calls

    for scenario in [
        "unknown",
        "text-only",
        "too-many",
        "too-large",
        "media",
        "withdrawn",
        "model-type",
        "streaming",
        "malformed",
        "changed-route",
    ]:
        _check_case(scenario)


def test_metadata_less_custom_child_adapter_is_text_compatible_but_cannot_send_images():
    calls = []
    def custom(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    selected = select(custom, {})
    with pytest.raises(ValueError, match='advertised model metadata'):
        selected.stream(custom)(selected.id, _image_context(), selected.options)
    assert not calls
    selected.stream(custom)(selected.id, Context(messages=[UserMessage(content='text')]), selected.options)
    assert len(calls) == 1


@pytest.mark.parametrize('maximum', [0, 6])
async def test_child_retained_image_count_is_not_the_four_image_composer_cap(maximum):
    from xdog.ai.types import VisionLimits
    stream = CatalogStream()
    stream.models['first/parent'] = replace(stream.models['first/parent'],
                                           vision_limits=VisionLimits(max_prompt_images=maximum))
    chosen = select(stream, {})
    await chosen.stream(stream)(chosen.id, _image_context(6), chosen.options).collect()
    assert len(stream.calls) == 1


def test_child_image_metadata_withdrawal_during_admission_never_reaches_adapter():
    stream = CatalogStream()
    chosen = select(stream, {})
    offered = stream.models['first/parent']
    lookups = []
    def changed(name):
        lookups.append(name)
        return offered if len(lookups) == 1 else replace(offered, input=('text',))
    stream.resolve_model = changed
    with pytest.raises(ValueError, match='changed during admission'):
        chosen.stream(stream)(chosen.id, _image_context(), chosen.options)
    assert len(lookups) == 2 and not stream.calls


async def test_switched_child_image_transport_bytes_are_not_charged_as_plain_text_tokens():
    import base64
    stream = CatalogStream()
    stream.models['second/reviewer'] = replace(stream.models['second/reviewer'], input=('text', 'image'))
    chosen = select(stream, {'model': 'second/reviewer', 'thinking': 'low'})
    image = ImageContent(data=base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'\0' * (2 * 1024 * 1024)).decode(),
                         mime_type='image/png')
    context = Context(messages=[UserMessage(content=(TextContent(text='inspect'), image))])
    await chosen.stream(stream)(chosen.id, context, chosen.options).collect()
    assert len(stream.calls) == 1 and stream.calls[0][0] == 'second/reviewer'
