"""Helpers for building StreamFn, WebSearchFn, EmbedFn from ai providers.

Usage::

    import xdog.ai as ai
    from xdog.agent.helpers import stream_fn_from_provider, web_search_fn_from_provider

    provider = ai.provider("copilot")
    agent = Agent(
        stream_fn_from_provider(provider),
        config=AgentConfig(model="claude-sonnet-4.5"),
        web_search_fn=web_search_fn_from_provider(provider, "claude-sonnet-4.5"),
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from xdog.agent.core import CatalogStreamFn, EmbedFn
from xdog.ai.core import BaseProvider
from xdog.ai.types import Model

if TYPE_CHECKING:
    from xdog.agent.core import WebSearchFn
    from xdog.ai.types import AssistantMessage, Context, StreamOptions
    from xdog.ai.utils.event_stream import EventStream


class _ProviderStream:
    def __init__(self, provider: BaseProvider) -> None:
        self.provider = provider

    def __call__(self, model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
        return self.provider.stream(model, context, options)

    def models(self) -> tuple[Model, ...]:
        return self.provider.models()  # Actual active provider catalog, never sync/login/activation.

    def resolve_model(self, name: str) -> Model | None:
        # Provider.model() uses the local catalog. Never sync/login, activate a
        # new provider, or choose a fallback on behalf of a child override.
        model = self.provider.model(name)
        if model is not None and "/" in name and not model.id.startswith(name.split("/", 1)[0] + "/"):
            return None
        return model


def stream_fn_from_provider(provider: BaseProvider) -> CatalogStreamFn:
    """Build an adapter carrying cached metadata from its actual request owner."""
    return _ProviderStream(provider)


def web_search_fn_from_provider(provider: BaseProvider, model: str) -> WebSearchFn:
    """Build a :class:`~agent.core.WebSearchFn` from an ai Provider.

    The returned function calls ``provider.web_search(model, query)``
    and extracts the text content from the response.
    """

    async def _search(query: str) -> str:
        result = await provider.web_search(model, query)
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts) if parts else "(no results)"

    return _search


def embed_fn_from_provider(provider: BaseProvider, model: str) -> EmbedFn:
    """Build a :class:`~agent.core.EmbedFn` from an ai Provider.

    The returned function calls ``provider.embed(model, text)``
    and returns the first embedding vector.
    """

    async def _embed(text: str) -> list[float]:
        result = await provider.embed(model, text)
        if result.data:
            return list(result.data[0].embedding)
        return []

    return _embed


_TOOL_CALL_SUPPORT: dict[str, bool] = {}


def model_supports_tool_calls(model_id: str) -> bool | None:
    """Whether *model_id* accepts tool definitions through the API.

    ``None`` when it cannot be determined — an unknown provider, no network, an
    offline machine. Callers should treat that as "do both": send the API tools
    *and* describe them in the prompt. Doing both wastes a few hundred tokens;
    doing neither leaves the model unable to act, and picking the wrong one of
    the two is silent.

    Cached per process. Callers ask before every turn, and a provider round trip
    per turn would cost far more than the duplication it avoids.
    """
    # Not `if not model_id`: callers pass whatever their config holds, and a
    # non-string reaches the cache lookup as an unhashable key. Anything that is
    # not a model id is simply unknown.
    if not isinstance(model_id, str) or not model_id:
        return None
    if model_id in _TOOL_CALL_SUPPORT:
        return _TOOL_CALL_SUPPORT[model_id]
    try:
        import xdog.ai as ai

        for model in ai.provider(model_id.split("/", 1)[0]).models():
            if model.id == model_id:
                _TOOL_CALL_SUPPORT[model_id] = bool(model.supports_tool_calls)
                return _TOOL_CALL_SUPPORT[model_id]
    except Exception:
        logger.debug("could not resolve tool-call support for %r", model_id, exc_info=True)
    return None


logger = logging.getLogger(__name__)
