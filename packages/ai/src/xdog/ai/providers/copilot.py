"""Copilot provider — thin user-facing layer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xdog.ai.vendors.copilot import CopilotVendor

from xdog.ai.core import AuthResult, BaseProtocol, BaseProvider
from xdog.ai.native import NativeEventStream, NativeOperation, NativeResponse, ProtocolRequest
from xdog.ai.types import (
    AssistantMessage,
    Context,
    EmbeddingRequest,
    StreamOptions,
    UserMessage,
)

if TYPE_CHECKING:
    from xdog.ai.types import (
        AssistantMessageEvent,
        EmbeddingResponse,
        Model,
    )
    from xdog.ai.utils.event_stream import EventStream


class CopilotProvider(BaseProvider):
    """GitHub Copilot provider."""

    def __init__(self) -> None:
        self._vendor: CopilotVendor | None = None
        self._model_cache: dict[str, Model] = {}
        self._protocols: dict[str, BaseProtocol] = {}

    @property
    def id(self) -> str:
        return "copilot"

    @property
    def name(self) -> str:
        return "GitHub Copilot"

    # -- Internals ------------------------------------------------------------

    def _get_vendor(self) -> CopilotVendor:
        if self._vendor is None:
            from xdog.ai.vendors.copilot import CopilotVendor
            self._vendor = CopilotVendor()
        return self._vendor

    def _get_protocol(self, protocol_id: str) -> BaseProtocol:
        if protocol_id not in self._protocols:
            if protocol_id == "openai-completions":
                from xdog.ai.protocols.openai_completions import OpenAICompletionsProtocol
                self._protocols[protocol_id] = OpenAICompletionsProtocol()
            elif protocol_id == "anthropic-messages":
                from xdog.ai.protocols.anthropic_messages import AnthropicMessagesProtocol
                self._protocols[protocol_id] = AnthropicMessagesProtocol()
            elif protocol_id == "openai-responses":
                from xdog.ai.protocols.openai_responses import OpenAIResponsesProtocol
                self._protocols[protocol_id] = OpenAIResponsesProtocol()
            else:
                raise ValueError(f"Unknown protocol: {protocol_id!r}")
        return self._protocols[protocol_id]

    def _resolve(self, name: str) -> Model:
        full = name if "/" in name else f"copilot/{name}"
        m = self._model_cache.get(full)
        if m is not None:
            return m
        from xdog.ai.vendors.copilot._model_sync import get_synced_model
        m = get_synced_model(full)
        if m is not None:
            self._model_cache[full] = m
            return m
        raise ValueError(f"Unknown model: {name!r}")

    def _wire_model(self, model: Model, auth: AuthResult) -> Model:
        """Prepare model for the wire: strip prefix, apply base_url."""
        return replace(
            model,
            id=model.id.removeprefix("copilot/"),
            base_url=auth.base_url or model.base_url,
        )

    # -- Models ---------------------------------------------------------------

    def models(self) -> tuple[Model, ...]:
        from xdog.ai.vendors.copilot._model_sync import list_models
        return list_models()

    def model(self, name: str) -> Model | None:
        try:
            return self._resolve(name)
        except ValueError:
            return None

    # -- Stream / Complete ----------------------------------------------------

    def stream(self, model_name: str, context: Context, options: StreamOptions | None = None, cancel: asyncio.Event | None = None) -> EventStream[AssistantMessage]:
        from xdog.ai.utils.event_stream import EventStream

        resolved = self._resolve(model_name)
        opts = options or StreamOptions()
        result_future: asyncio.Future[AssistantMessage] = asyncio.get_event_loop().create_future()

        async def _generate() -> AsyncIterator[AssistantMessageEvent]:
            auth = await self._get_vendor().resolve_auth(resolved, context)
            protocol = self._get_protocol(resolved.preferred_protocol or resolved.api)
            wire = self._wire_model(resolved, auth)
            inner = protocol.stream(wire, context, opts, auth)

            async for event in inner:
                yield event

            if hasattr(inner, "result"):
                result_future.set_result(await inner.result())

        return EventStream.from_async_generator(_generate(), result_future)

    async def complete(self, model_name: str, context: Context, options: StreamOptions | None = None, cancel: asyncio.Event | None = None) -> AssistantMessage:
        return await self.stream(model_name, context, options, cancel).result()

    @staticmethod
    def _native_protocol_ids(model: Model) -> tuple[str, ...]:
        supported = model.supported_generation_protocols
        if supported is not None:
            return supported
        return () if model.model_type == "embeddings" else (model.supported_protocols or (model.api,))

    def _native_protocol(self, model: Model, request: ProtocolRequest) -> BaseProtocol:
        if request.protocol not in self._native_protocol_ids(model):
            raise NotImplementedError(
                f"Model {model.id!r} does not support protocol {request.protocol!r}",
            )
        protocol = self._get_protocol(request.protocol)
        if not protocol.supports_native_operation(request.operation):
            raise NotImplementedError(
                f"Protocol {request.protocol!r} does not support operation {request.operation.value!r}",
            )
        return protocol

    def supports_native_request(self, model_name: str, request: ProtocolRequest) -> bool:
        try:
            model = self._resolve(model_name)
            return (
                request.protocol in self._native_protocol_ids(model)
                and self._get_protocol(request.protocol).supports_native_operation(request.operation)
            )
        except (NotImplementedError, ValueError):
            return False

    async def request_complete(
        self,
        model_name: str,
        request: ProtocolRequest,
    ) -> NativeResponse:
        resolved = self._resolve(model_name)
        protocol = self._native_protocol(resolved, request)
        auth = await self._get_vendor().resolve_auth(
            resolved,
            protocol.native_auth_context(request),
        )
        response: NativeResponse = await protocol.request_complete(self._wire_model(resolved, auth), request, auth)
        return response

    async def request_stream(
        self,
        model_name: str,
        request: ProtocolRequest,
    ) -> NativeEventStream:
        resolved = self._resolve(model_name)
        protocol = self._native_protocol(resolved, request)
        if request.operation is not NativeOperation.GENERATE:
            raise NotImplementedError(
                f"Native operation {request.operation.value!r} cannot be streamed",
            )
        auth = await self._get_vendor().resolve_auth(
            resolved,
            protocol.native_auth_context(request),
        )
        stream: NativeEventStream = await protocol.request_stream(self._wire_model(resolved, auth), request, auth)
        return stream

    # -- Embed ----------------------------------------------------------------

    async def embed(
        self, model_name: str, input: str | tuple[str, ...] | EmbeddingRequest,
    ) -> EmbeddingResponse:
        resolved = self._resolve(model_name)
        request = input if isinstance(input, EmbeddingRequest) else EmbeddingRequest(input=input)

        auth = await self._get_vendor().resolve_auth(resolved)
        protocol = self._get_protocol(resolved.preferred_protocol or resolved.api)
        wire = self._wire_model(resolved, auth)
        response: EmbeddingResponse = await protocol.embed(wire, request, auth)
        return response

    # -- Web search -----------------------------------------------------------

    async def web_search(self, model_name: str, query: str) -> AssistantMessage:
        context = Context(
            system_prompt="You are a web search assistant. Search the web and return a concise, factual summary with source URLs.",
            messages=(UserMessage(content=query),),
        )
        return await self.stream(model_name, context, StreamOptions(web_search=True)).result()

    # -- Auth & sync ----------------------------------------------------------

    async def login(self) -> str:
        return await self._get_vendor().login()

    async def sync_models(self, *, ttl: float = 86400, force: bool = False) -> tuple[Model, ...]:
        models = await self._get_vendor().sync_models(ttl, force)
        self._model_cache = {model.id: model for model in models}
        return models

    def __repr__(self) -> str:
        return "CopilotProvider()"
