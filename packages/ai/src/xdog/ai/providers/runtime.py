"""Runtime — aggregates all active providers.

Usage::

    runtime = ai.load()
    runtime.provider("copilot").stream("claude-sonnet-4.5", context)
    runtime.stream("copilot/claude-sonnet-4.5", context)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from xdog.ai.core import BaseProvider
from xdog.ai.providers import provider as _make_provider

if TYPE_CHECKING:
    from xdog.ai.native import NativeEventStream, NativeResponse, ProtocolRequest
    from xdog.ai.types import (
        AssistantMessage,
        Context,
        EmbeddingRequest,
        EmbeddingResponse,
        ImageGenerationRequest,
        ImageGenerationResponse,
        Model,
        StreamOptions,
    )
    from xdog.ai.utils.event_stream import EventStream

logger = logging.getLogger(__name__)


class Runtime(BaseProvider):
    """Aggregates all active providers discovered from auth storage.

    Implements the :class:`BaseProvider` interface. Routes calls to the
    appropriate underlying provider based on the ``provider`` parameter
    in ``stream``/``complete``/etc.
    """

    def __init__(self) -> None:
        self._active: dict[str, BaseProvider] = {}

    @property
    def id(self) -> str:
        return "_runtime"

    @property
    def name(self) -> str:
        return "Runtime"

    def add_provider(self, provider_id: str) -> None:
        """Activate a provider by ID."""
        try:
            self._active[provider_id] = _make_provider(provider_id)
        except KeyError:
            logger.warning("Provider %r not registered, skipping", provider_id)

    def active_providers(self) -> list[str]:
        """Return the list of active provider IDs."""
        return list(self._active.keys())

    def provider(self, provider_id: str) -> BaseProvider:
        """Get a specific active provider."""
        if provider_id not in self._active:
            raise KeyError(
                f"Provider {provider_id!r} not active. "
                f"Active: {', '.join(self._active) or '(none)'}"
            )
        return self._active[provider_id]

    # -- Model management (aggregated) ----------------------------------------

    def models(self) -> tuple[Model, ...]:
        """List all models from all active providers."""
        all_mods: list[Model] = []
        for p in self._active.values():
            all_mods.extend(p.models())
        return tuple(all_mods)

    def model(self, name: str) -> Model | None:
        """Get a model. Name must include provider prefix (e.g. 'copilot/claude-sonnet-4.5')."""
        if "/" in name:
            provider_id = name.split("/", 1)[0]
            p = self._active.get(provider_id)
            if p:
                return p.model(name)
        # Try each active provider
        for p in self._active.values():
            result = p.model(name)
            if result is not None:
                return result
        return None

    # -- Chat -----------------------------------------------------------------

    def _route(self, model: str) -> tuple[BaseProvider, str]:
        """Route a model name to (provider, short_name).

        Accepts:
        - ``"copilot/claude-sonnet-4.5"`` → provider copilot, model claude-sonnet-4.5
        - ``"claude-sonnet-4.5"`` → default provider (if only one active), model as-is
        """
        if "/" in model:
            pid, short = model.split("/", 1)
            p = self._active.get(pid)
            if p is None:
                raise KeyError(f"Provider {pid!r} not active. Active: {', '.join(self._active)}")
            return p, short

        if len(self._active) == 1:
            p = next(iter(self._active.values()))
            return p, model

        raise ValueError(
            f"Ambiguous model {model!r} — multiple providers active. "
            f"Use 'provider/model' format. Active: {', '.join(self._active)}"
        )

    def stream(
        self,
        model: str = "",
        context: Context | None = None,
        options: StreamOptions | None = None,
    ) -> EventStream[AssistantMessage]:
        """Stream a response. Model can be 'provider/model' or short name if one provider active."""
        p, short = self._route(model)
        # Providers declare `Context`, not `Context | None`: passing None
        # through reached them and failed on attribute access deeper in.
        return p.stream(short, context or Context(), options)

    async def complete(
        self,
        model: str = "",
        context: Context | None = None,
        options: StreamOptions | None = None,
    ) -> AssistantMessage:
        """Complete a response. Same routing as stream."""
        p, short = self._route(model)
        return await p.complete(short, context or Context(), options)

    def supports_native_request(self, model: str, request: ProtocolRequest) -> bool:
        """Route a side-effect-free protocol-native capability check."""
        provider, short = self._route(model)
        return provider.supports_native_request(short, request)

    async def request_complete(
        self,
        model: str,
        request: ProtocolRequest,
    ) -> NativeResponse:
        """Route a protocol-native non-streaming request."""
        provider, short = self._route(model)
        return await provider.request_complete(short, request)

    async def request_stream(
        self,
        model: str,
        request: ProtocolRequest,
    ) -> NativeEventStream:
        """Route a protocol-native streaming request."""
        provider, short = self._route(model)
        return await provider.request_stream(short, request)

    # -- Embedding / Web search -----------------------------------------------

    async def embed(
        self, model: str = "", input: str | tuple[str, ...] | EmbeddingRequest = "",
    ) -> EmbeddingResponse:
        """Generate embeddings. Same routing as stream."""
        p, short = self._route(model)
        return await p.embed(short, input)

    async def web_search(self, model: str = "", query: str = "") -> AssistantMessage:
        """Web search. Same routing as stream."""
        p, short = self._route(model)
        return await p.web_search(short, query)

    async def image_generation(
        self, model: str, prompt: str | ImageGenerationRequest,
    ) -> ImageGenerationResponse:
        """Generate images through the selected provider, like web_search."""
        p, short = self._route(model)
        return await p.image_generation(short, prompt)

    # -- Auth & sync ----------------------------------------------------------

    async def login(self, provider_id: str = "") -> str:
        """Login to a provider. Adds it to active providers."""
        from xdog.ai.api import login as _login
        token = await _login(provider_id)
        self.add_provider(provider_id)
        return token

    async def sync_models(self, *, ttl: float = 86400, force: bool = False) -> tuple[Model, ...]:
        """Sync models for all active providers."""
        all_synced: list[Model] = []
        for p in self._active.values():
            synced = await p.sync_models(ttl=ttl, force=force)
            all_synced.extend(synced)
        return tuple(all_synced)

    def __repr__(self) -> str:
        return f"Runtime(providers={list(self._active.keys())})"
