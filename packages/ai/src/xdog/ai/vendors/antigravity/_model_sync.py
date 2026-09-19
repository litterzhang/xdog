"""Antigravity model metadata; never infer image capabilities from model names."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, cast

from xdog.ai.paths import config_dir, data_dir
from xdog.ai.types import InputModality, Model, ModelEndpoint

PROTOCOL = "google-generative-ai"


def cache_path() -> Path:
    return data_dir() / "antigravity_models_cache.json"


def _modalities(raw: Any) -> tuple[InputModality, ...] | None:
    if not isinstance(raw, list) or any(not isinstance(v, str) for v in raw):
        return None
    # Unknown modality names are not represented as text or image.
    return tuple(dict.fromkeys(cast(InputModality, v.lower()) for v in raw if v.lower() in ("text", "image")))


def _tokens(raw: Any) -> int:
    return raw if type(raw) is int and raw >= 0 else 0


def parse_models(
    payload: dict[str, Any], base_url: str, overrides: dict[str, Any] | None = None,
) -> tuple[Model, ...]:
    entries = payload.get("models", {})
    if not isinstance(entries, dict):
        raise ValueError("Antigravity model catalogue must contain a models object.")
    image_ids = payload.get("imageGenerationModelIds", [])
    image_models = {value for value in image_ids if isinstance(value, str)} if isinstance(image_ids, list) else set()
    search_ids = payload.get("webSearchModelIds", [])
    search_models = {value for value in search_ids if isinstance(value, str)} if isinstance(search_ids, list) else set()
    models: list[Model] = []
    for model_id, raw in entries.items():
        if not isinstance(model_id, str) or not isinstance(raw, dict):
            continue
        input_types = _modalities(raw.get("inputModalities", raw.get("supportedInputModalities")))
        output_types = _modalities(raw.get("outputModalities", raw.get("supportedOutputModalities")))
        if input_types is None and type(raw.get("supportsImages")) is bool:
            input_types = ("text", "image") if raw["supportsImages"] else ("text",)
        if output_types is None and type(raw.get("supportsImageGeneration")) is bool:
            output_types = ("text", "image") if raw["supportsImageGeneration"] else ("text",)
        if output_types is None and model_id in image_models:
            # Positive catalogue evidence even when the per-model record has
            # no modalities. Do not infer image input, text output, or a negative
            # image capability for models absent from this list.
            output_types = ("image",)
        override = (overrides or {}).get(model_id, {})
        if not isinstance(override, dict):
            raise ValueError(f"Invalid capability override for {model_id}")
        if "input" in override:
            input_types = _modalities(override["input"])
        if "output" in override:
            output_types = _modalities(override["output"])
        streaming = raw.get("supportsStreaming") is not False
        web_search: bool | None = raw["supportsWebSearch"] if type(raw.get("supportsWebSearch")) is bool else None
        if web_search is None and model_id in search_models:
            web_search = True
        endpoints = [ModelEndpoint(PROTOCOL, "generate", "/v1internal:generateContent")]
        if streaming:
            endpoints.append(ModelEndpoint(PROTOCOL, "stream_generate", "/v1internal:streamGenerateContent"))
        models.append(Model(
            id=f"antigravity/{model_id}", name=str(raw.get("displayName") or model_id),
            provider="antigravity", api=PROTOCOL, preferred_protocol=PROTOCOL, base_url=base_url,
            input=input_types, output=output_types, endpoints=tuple(endpoints),
            supported_protocols=(PROTOCOL,), supported_generation_protocols=(PROTOCOL,),
            # Antigravity's catalogue uses maxTokens for context capacity.
            # Keep a separately advertised input cap distinct from that limit.
            context_window=_tokens(raw.get("maxTokens")) or _tokens(raw.get("maxInputTokens")),
            max_prompt_tokens=_tokens(raw.get("maxInputTokens")), max_tokens=_tokens(raw.get("maxOutputTokens")),
            reasoning=raw.get("supportsThinking") is True,
            supports_tool_calls=override.get("tool_calls", raw.get("supportsToolCalls", True)) is True,
            supports_streaming=streaming,
            supports_web_search=web_search,
        ))
    return tuple(models)


def load_overrides() -> dict[str, Any]:
    path = config_dir() / "antigravity-models.json"
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("antigravity-models.json must be an object keyed by exact model ID.")
    return value


def read_cache() -> tuple[dict[str, Any], float] | None:
    try:
        raw = json.loads(cache_path().read_text())
        if raw.get("schema_version") != 1 or not isinstance(raw.get("catalogue"), dict):
            return None
        return raw["catalogue"], float(raw["timestamp"])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def write_cache(payload: dict[str, Any]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "timestamp": time.time(), "catalogue": payload}))
