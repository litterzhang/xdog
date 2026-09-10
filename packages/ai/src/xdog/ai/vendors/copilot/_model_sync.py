"""Dynamic model synchronisation from GitHub Copilot's ``/models`` API.

Fetches the live model catalogue, maps each entry to a :class:`~ai.types.Model`
definition, and caches results locally so that subsequent look-ups are cheap.

Three public operations:

* :func:`sync_models` -- hit the API, refresh cache, return the model list.
* :func:`list_models` -- return cached models (or fallback) without network.
* :func:`get_synced_model` -- look up a single model by id.

The local cache lives at ``~/.local/xdog/models_cache.json`` with a
configurable TTL (default 24 h).  When the API is unreachable or the token is
not available the module falls back to :data:`_FALLBACK_MODELS`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal, cast

import httpx
from xdog.ai.paths import data_dir, models_cache_file
from xdog.ai.types import (
    Model,
    ModelCost,
    OpenAICompletionsCompat,
    ThinkingBudgetRange,
    VisionLimits,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_FILE = models_cache_file()
_CACHE_SCHEMA_VERSION = 2
_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# Mapping from Copilot API endpoint paths to internal adapter protocols.
_ENDPOINT_TO_PROTOCOL: dict[str, str] = {
    "/chat/completions": "openai-completions",
    "/v1/chat/completions": "openai-completions",
    "/v1/messages": "anthropic-messages",
    "/responses": "openai-responses",
    "/v1/responses": "openai-responses",
    "/embeddings": "openai-completions",
    "/v1/embeddings": "openai-completions",
}

_GENERATION_ENDPOINT_TO_PROTOCOL: dict[str, str] = {
    endpoint: protocol
    for endpoint, protocol in _ENDPOINT_TO_PROTOCOL.items()
    if endpoint not in ("/embeddings", "/v1/embeddings")
}


def _protocols_for_endpoints(
    endpoints: list[str],
    mapping: dict[str, str],
) -> tuple[str, ...]:
    """Return mapped protocol IDs once, in deterministic mapping order."""
    endpoint_set = frozenset(endpoints)
    return tuple(dict.fromkeys(
        protocol for endpoint, protocol in mapping.items() if endpoint in endpoint_set
    ))

# Fallback models when sync cache is empty and API unreachable.
_FALLBACK_MODELS: tuple[Model, ...] = (
    Model(id="copilot/gpt-4o", name="GPT-4o (Copilot)", api="openai-completions", provider="copilot",
          base_url="https://api.githubcopilot.com", input=("text", "image"), context_window=128_000, max_tokens=16_384,
          cost=ModelCost(input=0), compat=OpenAICompletionsCompat(supports_store=True, supports_developer_role=True, supports_usage_in_streaming=True, max_tokens_field="max_completion_tokens"),
          supported_generation_protocols=("openai-completions",)),
    Model(id="copilot/claude-sonnet-4.5", name="Claude Sonnet 4.5 (Copilot)", api="openai-completions", provider="copilot",
          base_url="https://api.githubcopilot.com", reasoning=True, input=("text", "image"), context_window=200_000, max_tokens=16_384,
          cost=ModelCost(input=1), compat=OpenAICompletionsCompat(supports_store=True, supports_developer_role=True, supports_usage_in_streaming=True),
          supported_protocols=("openai-completions", "anthropic-messages"),
          supported_generation_protocols=("openai-completions", "anthropic-messages"),
          preferred_protocol="anthropic-messages"),
    Model(id="copilot/claude-opus-4.6-1m", name="Claude Opus 4.6 1M (Copilot)", api="openai-completions", provider="copilot",
          base_url="https://api.githubcopilot.com", reasoning=True, input=("text", "image"), context_window=1_000_000, max_tokens=32_000,
          cost=ModelCost(input=3), compat=OpenAICompletionsCompat(supports_store=True, supports_developer_role=True, supports_usage_in_streaming=True),
          supported_protocols=("openai-completions", "anthropic-messages"),
          supported_generation_protocols=("openai-completions", "anthropic-messages"),
          preferred_protocol="anthropic-messages"),
    Model(id="copilot/o3-mini", name="o3-mini (Copilot)", api="openai-completions", provider="copilot",
          base_url="https://api.githubcopilot.com", reasoning=True, context_window=200_000, max_tokens=100_000,
          cost=ModelCost(input=1), compat=OpenAICompletionsCompat(supports_store=True, supports_developer_role=True, supports_reasoning_effort=True,
          reasoning_effort_map={"minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "high"}, supports_usage_in_streaming=True, max_tokens_field="max_completion_tokens"),
          supported_generation_protocols=("openai-completions",)),
    Model(id="copilot/gemini-2.5-pro", name="Gemini 2.5 Pro (Copilot)", api="openai-completions", provider="copilot",
          base_url="https://api.githubcopilot.com", reasoning=True, input=("text", "image"), context_window=1_000_000, max_tokens=65_536,
          cost=ModelCost(input=1), compat=OpenAICompletionsCompat(supports_store=True, supports_developer_role=True, supports_usage_in_streaming=True),
          supported_generation_protocols=("openai-completions",)),
)

# ---------------------------------------------------------------------------
# Compat overrides -- fields the API does *not* return
# ---------------------------------------------------------------------------

# Models whose reasoning_effort list is present need the ``supports_reasoning_effort``
# flag *and* a mapping from our internal ThinkingLevel names to provider strings.
_REASONING_EFFORT_MAP: dict[str, str] = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
}

# Per-family compat tweaks that cannot be derived from the API response.
_FAMILY_COMPAT_OVERRIDES: dict[str, dict[str, Any]] = {
    # OpenAI models use ``max_completion_tokens`` instead of ``max_tokens``.
    "gpt-4o": {"max_tokens_field": "max_completion_tokens", "supports_store": True, "supports_developer_role": True},
    "gpt-4o-mini": {"max_tokens_field": "max_completion_tokens", "supports_store": True, "supports_developer_role": True},
    "gpt-4.1": {"max_tokens_field": "max_completion_tokens", "supports_store": True, "supports_developer_role": True},
    "gpt-5.1": {"max_tokens_field": "max_completion_tokens", "supports_store": True, "supports_developer_role": True},
    "gpt-5.2": {"max_tokens_field": "max_completion_tokens", "supports_store": True, "supports_developer_role": True},
    "gpt-5-mini": {"max_tokens_field": "max_completion_tokens", "supports_store": True, "supports_developer_role": True},
}

# ---------------------------------------------------------------------------
# Premium request multiplier (from GitHub docs, keyed by model family)
#
# The Copilot API does not return pricing.  VS Code uses a hardcoded table.
# A multiplier of 0 means the model is included with paid plans at no extra
# cost.  1 = one premium request per call.  Higher = more expensive.
# Source: https://docs.github.com/en/copilot/managing-copilot/monitoring-usage-and-entitlements/about-premium-requests
# ---------------------------------------------------------------------------

_PREMIUM_MULTIPLIERS: dict[str, float] = {
    # Anthropic
    "claude-haiku-4.5": 0.33,
    "claude-sonnet-4": 1,
    "claude-sonnet-4.5": 1,
    "claude-sonnet-4.6": 1,
    "claude-opus-4.5": 3,
    "claude-opus-4.6": 3,
    "claude-opus-4.6-1m": 3,
    # Google
    "gemini-2.5-pro": 1,
    "gemini-3-flash": 0.33,
    "gemini-3-flash-preview": 0.33,
    "gemini-3-pro": 1,
    "gemini-3.1-pro": 1,
    "gemini-3.1-pro-preview": 1,
    # OpenAI
    "gpt-4.1": 0,
    "gpt-4o": 0,
    "gpt-4o-mini": 0,
    "gpt-5-mini": 0,
    "gpt-5.1": 1,
    "gpt-5.2": 1,
    "gpt-5.2-codex": 1,
    "gpt-5.3-codex": 1,
    "gpt-5.4": 1,
    "gpt-5.4-mini": 0.33,
    "goldeneye": 0,
}


# ---------------------------------------------------------------------------
# Conversion: API JSON -> Model
# ---------------------------------------------------------------------------


def _parse_api_model(raw: dict[str, Any]) -> Model | None:
    """Convert a single ``/models`` API entry to a :class:`Model`.

    Returns ``None`` when:
    - The entry has no ``id``.
    - The model has no supported endpoints and is not an embedding model
      (deprecated/legacy models the API still returns but VS Code hides).
    """
    model_id: str = raw.get("id", "")
    if not model_id:
        return None

    # Derive broad adapter support and exact generation support separately.
    endpoints_raw = raw.get("supported_endpoints", [])
    if not isinstance(endpoints_raw, list) or any(not isinstance(endpoint, str) for endpoint in endpoints_raw):
        return None
    endpoints: list[str] = endpoints_raw
    supported_protocols = _protocols_for_endpoints(endpoints, _ENDPOINT_TO_PROTOCOL)
    supported_generation_protocols = _protocols_for_endpoints(
        endpoints,
        _GENERATION_ENDPOINT_TO_PROTOCOL,
    )

    caps: dict[str, Any] = raw.get("capabilities", {})
    model_type: str = caps.get("type", "chat")

    # Filter: skip models that are not picker-enabled and have no endpoints,
    # unless they are embeddings.  Picker-enabled models without endpoints
    # (e.g. Gemini) are routed server-side and still usable.
    picker_enabled = raw.get("model_picker_enabled", False)
    if not endpoints and not picker_enabled and model_type != "embeddings":
        return None

    # Prefer anthropic-messages for Anthropic-vendor models when available.
    vendor: str = raw.get("vendor", "")
    preferred_protocol: str | None = None
    if "anthropic-messages" in supported_protocols and vendor.lower() == "anthropic":
        preferred_protocol = "anthropic-messages"

    limits: dict[str, Any] = caps.get("limits", {})
    supports: dict[str, Any] = caps.get("supports", {})
    family: str = caps.get("family", model_id)

    # -- Input modalities --------------------------------------------------
    input_modalities: tuple[str, ...] = ("text",)
    if supports.get("vision"):
        input_modalities = ("text", "image")

    # -- Reasoning ---------------------------------------------------------
    reasoning_effort_list = supports.get("reasoning_effort")
    has_reasoning = bool(
        supports.get("adaptive_thinking")
        or (isinstance(reasoning_effort_list, list) and len(reasoning_effort_list) > 0)
    )

    # -- Adaptive thinking flags -------------------------------------------
    adaptive_thinking_flag = supports.get("adaptive_thinking")
    adaptive_thinking: bool | None = bool(adaptive_thinking_flag) if adaptive_thinking_flag is not None else None
    supported_efforts: tuple[str, ...] | None = None
    if isinstance(reasoning_effort_list, list) and len(reasoning_effort_list) > 0:
        supported_efforts = tuple(reasoning_effort_list)

    # -- Capability flags --------------------------------------------------
    supports_tool_calls = bool(supports.get("tool_calls", False))
    supports_parallel_tool_calls = bool(supports.get("parallel_tool_calls", False))
    supports_streaming = bool(supports.get("streaming", False))
    supports_structured_outputs = bool(supports.get("structured_outputs", False))

    # -- Thinking budget range ---------------------------------------------
    thinking_budget_range: ThinkingBudgetRange | None = None
    max_thinking = supports.get("max_thinking_budget")
    min_thinking = supports.get("min_thinking_budget")
    if max_thinking is not None or min_thinking is not None:
        thinking_budget_range = ThinkingBudgetRange(
            min_budget=int(min_thinking) if min_thinking is not None else 1024,
            max_budget=int(max_thinking) if max_thinking is not None else 32768,
        )

    # -- Vision limits -----------------------------------------------------
    vision_limits_raw: dict[str, Any] = limits.get("vision", {})
    vision_limits: VisionLimits | None = None
    if vision_limits_raw and supports.get("vision"):
        vision_limits = VisionLimits(
            max_prompt_images=vision_limits_raw.get("max_prompt_images", 0),
            max_prompt_image_size=vision_limits_raw.get("max_prompt_image_size", 0),
            supported_media_types=tuple(vision_limits_raw.get("supported_media_types", ())),
        )

    # -- Model metadata ----------------------------------------------------
    model_version: str = raw.get("version", "")
    model_preview: bool = raw.get("preview", False)

    # -- Embedding dimensions ----------------------------------------------
    # The API returns dimensions as either a bool (supports custom dims)
    # or an int (fixed dimension count).
    dimensions_raw = supports.get("dimensions")
    supports_dimensions: bool = False
    dimensions: int | None = None
    if isinstance(dimensions_raw, bool):
        supports_dimensions = dimensions_raw
    elif isinstance(dimensions_raw, int) and dimensions_raw > 0:
        dimensions = dimensions_raw

    # -- Build OpenAICompletionsCompat ------------------------------------
    overrides = _FAMILY_COMPAT_OVERRIDES.get(family, {})

    supports_reasoning_effort = isinstance(reasoning_effort_list, list) and len(reasoning_effort_list) > 0
    reasoning_effort_map = _REASONING_EFFORT_MAP if supports_reasoning_effort else None

    compat = OpenAICompletionsCompat(
        supports_store=overrides.get("supports_store", True),
        supports_developer_role=overrides.get("supports_developer_role", True),
        supports_reasoning_effort=supports_reasoning_effort,
        reasoning_effort_map=reasoning_effort_map,
        supports_usage_in_streaming=True,
        max_tokens_field=overrides.get("max_tokens_field", "max_tokens"),
        supports_strict_mode=supports_structured_outputs,
    )

    # -- Name --------------------------------------------------------------
    name = raw.get("name", model_id)
    display_name = f"{name} (Copilot)"

    # -- Default API protocol ----------------------------------------------
    # Use the first supported protocol as the default api.  Embedding models
    # often lack ``supported_endpoints`` entirely, so infer from model_type.
    if supported_protocols:
        default_api = supported_protocols[0]
    elif model_type == "embeddings":
        default_api = "openai-completions"
        supported_protocols = ("openai-completions",)
    else:
        default_api = "openai-completions"

    # -- Premium request multiplier -> ModelCost ----------------------------
    multiplier = _PREMIUM_MULTIPLIERS.get(model_id)
    if multiplier is None:
        multiplier = _PREMIUM_MULTIPLIERS.get(family, 0.0)

    return Model(
        id=f"copilot/{model_id}",
        name=display_name,
        api=default_api,
        provider="copilot",
        base_url="https://api.githubcopilot.com",
        reasoning=has_reasoning,
        # Narrow to the literal pair Model declares; the upstream list is
        # free-form strings and may name a modality we do not model.
        input=tuple(
            cast(Literal["text", "image"], m)
            for m in input_modalities if m in ("text", "image")
        ),
        cost=ModelCost(input=multiplier),
        context_window=limits.get("max_context_window_tokens", 0) or 0,
        max_prompt_tokens=limits.get("max_prompt_tokens", 0) or 0,
        max_tokens=limits.get("max_output_tokens", 0) or 0,
        compat=compat,
        supported_protocols=supported_protocols if supported_protocols else None,
        supported_generation_protocols=supported_generation_protocols,
        preferred_protocol=preferred_protocol,
        supports_tool_calls=supports_tool_calls,
        supports_parallel_tool_calls=supports_parallel_tool_calls,
        supports_streaming=supports_streaming,
        supports_structured_outputs=supports_structured_outputs,
        thinking_budget_range=thinking_budget_range,
        adaptive_thinking=adaptive_thinking,
        supported_efforts=supported_efforts,
        vision_limits=vision_limits,
        model_type=model_type,
        vendor=vendor,
        version=model_version,
        preview=model_preview,
        dimensions=dimensions,
        supports_dimensions=supports_dimensions,
    )


def _parse_api_response(data: dict[str, Any]) -> tuple[Model, ...]:
    """Parse the full ``GET /models`` response into a tuple of :class:`Model`."""
    models: list[Model] = []
    for raw in data.get("data", []):
        model = _parse_api_model(raw)
        if model is not None:
            models.append(model)
    return tuple(models)


# ---------------------------------------------------------------------------
# Cache read/write
# ---------------------------------------------------------------------------


def _model_to_dict(m: Model) -> dict[str, Any]:
    """Serialise a :class:`Model` to a JSON-safe dict for caching."""
    compat_dict: dict[str, Any] | None = None
    if isinstance(m.compat, OpenAICompletionsCompat):
        compat_dict = {
            "supports_store": m.compat.supports_store,
            "supports_developer_role": m.compat.supports_developer_role,
            "supports_reasoning_effort": m.compat.supports_reasoning_effort,
            "reasoning_effort_map": m.compat.reasoning_effort_map,
            "supports_usage_in_streaming": m.compat.supports_usage_in_streaming,
            "max_tokens_field": m.compat.max_tokens_field,
            "requires_tool_result_name": m.compat.requires_tool_result_name,
            "requires_assistant_after_tool_result": m.compat.requires_assistant_after_tool_result,
            "requires_thinking_as_text": m.compat.requires_thinking_as_text,
            "supports_strict_mode": m.compat.supports_strict_mode,
        }
    return {
        "id": m.id,
        "name": m.name,
        "api": m.api,
        "provider": m.provider,
        "base_url": m.base_url,
        "reasoning": m.reasoning,
        "input": list(m.input),
        "cost": {"input": m.cost.input, "output": m.cost.output, "cache_read": m.cost.cache_read, "cache_write": m.cost.cache_write},
        "context_window": m.context_window,
        "max_prompt_tokens": m.max_prompt_tokens,
        "max_tokens": m.max_tokens,
        "headers": dict(m.headers),
        "compat": compat_dict,
        "supported_protocols": list(m.supported_protocols) if m.supported_protocols else None,
        "supported_generation_protocols": (
            list(m.supported_generation_protocols)
            if m.supported_generation_protocols is not None else None
        ),
        "preferred_protocol": m.preferred_protocol,
        "supports_tool_calls": m.supports_tool_calls,
        "supports_parallel_tool_calls": m.supports_parallel_tool_calls,
        "supports_streaming": m.supports_streaming,
        "supports_structured_outputs": m.supports_structured_outputs,
        "thinking_budget_range": (
            {"min_budget": m.thinking_budget_range.min_budget, "max_budget": m.thinking_budget_range.max_budget}
            if m.thinking_budget_range else None
        ),
        "adaptive_thinking": m.adaptive_thinking,
        "supported_efforts": list(m.supported_efforts) if m.supported_efforts else None,
        "vision_limits": (
            {
                "max_prompt_images": m.vision_limits.max_prompt_images,
                "max_prompt_image_size": m.vision_limits.max_prompt_image_size,
                "supported_media_types": list(m.vision_limits.supported_media_types),
            }
            if m.vision_limits else None
        ),
        "model_type": m.model_type,
        "vendor": m.vendor,
        "version": m.version,
        "preview": m.preview,
        "dimensions": m.dimensions,
        "supports_dimensions": m.supports_dimensions,
    }


def _model_from_dict(d: dict[str, Any]) -> Model:
    """Deserialise a cached dict back into a :class:`Model`."""
    compat_raw = d.get("compat")
    compat: OpenAICompletionsCompat | None = None
    if isinstance(compat_raw, dict):
        compat = OpenAICompletionsCompat(
            supports_store=compat_raw.get("supports_store", False),
            supports_developer_role=compat_raw.get("supports_developer_role", False),
            supports_reasoning_effort=compat_raw.get("supports_reasoning_effort", False),
            reasoning_effort_map=compat_raw.get("reasoning_effort_map"),
            supports_usage_in_streaming=compat_raw.get("supports_usage_in_streaming", True),
            max_tokens_field=compat_raw.get("max_tokens_field", "max_tokens"),
            requires_tool_result_name=compat_raw.get("requires_tool_result_name", False),
            requires_assistant_after_tool_result=compat_raw.get("requires_assistant_after_tool_result", False),
            requires_thinking_as_text=compat_raw.get("requires_thinking_as_text", False),
            supports_strict_mode=compat_raw.get("supports_strict_mode", False),
        )

    cost_raw = d.get("cost", {})
    sp_raw = d.get("supported_protocols")
    sgp_raw = d.get("supported_generation_protocols")

    # Thinking budget range
    tbr_raw = d.get("thinking_budget_range")
    thinking_budget_range: ThinkingBudgetRange | None = None
    if isinstance(tbr_raw, dict):
        thinking_budget_range = ThinkingBudgetRange(
            min_budget=tbr_raw.get("min_budget", 1024),
            max_budget=tbr_raw.get("max_budget", 32768),
        )

    # Vision limits
    vl_raw = d.get("vision_limits")
    vision_limits: VisionLimits | None = None
    if isinstance(vl_raw, dict):
        vision_limits = VisionLimits(
            max_prompt_images=vl_raw.get("max_prompt_images", 0),
            max_prompt_image_size=vl_raw.get("max_prompt_image_size", 0),
            supported_media_types=tuple(vl_raw.get("supported_media_types", ())),
        )

    return Model(
        id=d.get("id", ""),
        name=d.get("name", ""),
        api=d.get("api", ""),
        provider=d.get("provider", ""),
        base_url=d.get("base_url", ""),
        reasoning=d.get("reasoning", False),
        input=tuple(d.get("input", ("text",))),
        cost=ModelCost(
            input=cost_raw.get("input", 0.0),
            output=cost_raw.get("output", 0.0),
            cache_read=cost_raw.get("cache_read", 0.0),
            cache_write=cost_raw.get("cache_write", 0.0),
        ),
        context_window=d.get("context_window", 0),
        max_prompt_tokens=d.get("max_prompt_tokens", 0),
        max_tokens=d.get("max_tokens", 0),
        headers=d.get("headers", {}) if isinstance(d.get("headers"), dict) else {},
        compat=compat,
        supported_protocols=tuple(sp_raw) if sp_raw else None,
        supported_generation_protocols=(
            tuple(sgp_raw) if isinstance(sgp_raw, list) else None
        ),
        preferred_protocol=d.get("preferred_protocol"),
        supports_tool_calls=d.get("supports_tool_calls", True),
        supports_parallel_tool_calls=d.get("supports_parallel_tool_calls", False),
        supports_streaming=d.get("supports_streaming", True),
        supports_structured_outputs=d.get("supports_structured_outputs", False),
        thinking_budget_range=thinking_budget_range,
        adaptive_thinking=d.get("adaptive_thinking"),
        supported_efforts=tuple(d["supported_efforts"]) if d.get("supported_efforts") else None,
        vision_limits=vision_limits,
        model_type=d.get("model_type", "chat"),
        vendor=d.get("vendor", ""),
        version=d.get("version", ""),
        preview=d.get("preview", False),
        dimensions=d.get("dimensions"),
        supports_dimensions=d.get("supports_dimensions", False),
    )


def _read_cache() -> tuple[tuple[Model, ...], float] | None:
    """Read cached models + timestamp.  Returns ``None`` on any error."""
    if not _CACHE_FILE.exists():
        return None
    try:
        raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if type(raw.get("schema_version")) is not int or raw["schema_version"] != _CACHE_SCHEMA_VERSION:
            return None
        ts: float = raw.get("timestamp", 0.0)
        models = tuple(_model_from_dict(d) for d in raw.get("models", []))
        return (models, ts)
    except (json.JSONDecodeError, OSError, KeyError, TypeError) as exc:
        logger.debug("Failed to read models cache: %s", exc)
        return None


def _write_cache(models: tuple[Model, ...]) -> None:
    """Persist models to the local cache file."""
    data_dir().mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "timestamp": time.time(),
        "models": [_model_to_dict(m) for m in models],
    }
    _CACHE_FILE.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.debug("Wrote %d models to cache %s", len(models), _CACHE_FILE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def sync_models(
    ttl: float = _DEFAULT_TTL_SECONDS,
    force: bool = False,
) -> tuple[Model, ...]:
    """Fetch the model list from the Copilot API and update the local cache.

    Parameters
    ----------
    ttl:
        Cache time-to-live in seconds.  If the cache is fresher than *ttl*
        and *force* is ``False``, returns the cached data without hitting the
        network.
    force:
        Bypass the TTL and always fetch from the API.

    Returns
    -------
    tuple[Model, ...]
        The parsed model list.  On network failure, falls back to the cache
        or to :data:`pi__FALLBACK_MODELS`.
    """
    # Check cache freshness
    if not force:
        cached = _read_cache()
        if cached is not None:
            models, ts = cached
            if time.time() - ts < ttl and len(models) > 0:
                logger.debug("Using cached models (%d entries, age %.0fs)", len(models), time.time() - ts)
                return models

    # Fetch from API
    try:
        from xdog.ai.vendors.copilot import COPILOT_HEADERS, _get_token_manager

        manager = _get_token_manager()
        jwt, base_url = await manager.get_token()

        url = f"{base_url or 'https://api.githubcopilot.com'}/models"
        headers = {
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/json",
            **COPILOT_HEADERS,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        models = _parse_api_response(data)
        if models:
            _write_cache(models)
            logger.info("Synced %d models from Copilot API", len(models))
            return models

        logger.warning("API returned zero usable models, falling back to cache/generated")

    except Exception as exc:
        logger.warning("Failed to sync models from Copilot API: %s", exc)

    # Fallback: cache, then generated
    cached = _read_cache()
    if cached is not None:
        models, _ = cached
        if models:
            return models

    return _FALLBACK_MODELS


def list_models(ttl: float = _DEFAULT_TTL_SECONDS) -> tuple[Model, ...]:
    """Return the model list from cache (synchronous, no network).

    A *fresh* cache (younger than *ttl*) is returned directly.  A *stale* cache
    is still returned rather than discarded — a stale-but-real model list is far
    more useful than the tiny hard-coded :data:`_FALLBACK_MODELS`, and freshness
    is refreshed out-of-band by :func:`sync_models`.  This "stale-while-error"
    behaviour means a transient sync failure (e.g. the upstream token endpoint
    returning 502) never collapses a long-running consumer down to the fallback
    set.  Only a missing or unreadable cache falls back.
    """
    cached = _read_cache()
    if cached is not None:
        models, ts = cached
        if len(models) > 0:
            if time.time() - ts >= ttl:
                logger.debug(
                    "Copilot model cache is stale (age %.0fs >= ttl %.0fs); using it anyway",
                    time.time() - ts,
                    ttl,
                )
            return models

    return _FALLBACK_MODELS


def get_synced_model(model_id: str) -> Model | None:
    """Look up a single model by id from the cache."""
    for m in list_models():
        if m.id == model_id:
            return m
    return None
