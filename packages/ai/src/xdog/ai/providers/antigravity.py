"""Native Google Antigravity provider; no CLIProxyAPI server dependency."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

import httpx
from xdog.ai.core import BaseProvider
from xdog.ai.native import NativeEventStream, NativeOperation, NativeResponse, ProtocolRequest
from xdog.ai.protocols.google_generative import GeminiDecoder, encode_context
from xdog.ai.protocols.google_images import decode_image_response, encode_image_request
from xdog.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    DoneEvent,
    EmbeddingRequest,
    EmbeddingResponse,
    ErrorEvent,
    ImageGenerationRequest,
    ImageGenerationResponse,
    Model,
    StartEvent,
    StreamOptions,
    UserMessage,
)
from xdog.ai.utils.event_stream import EventStream
from xdog.ai.vendors.antigravity import AntigravityVendor, _transport
from xdog.ai.vendors.antigravity._model_sync import PROTOCOL


class AntigravityProvider(BaseProvider):
    def __init__(self, vendor: AntigravityVendor | None = None) -> None:
        self._vendor = vendor or AntigravityVendor()

    @property
    def id(self) -> str:
        return "antigravity"

    @property
    def name(self) -> str:
        return "Google Antigravity"

    def models(self) -> tuple[Model, ...]:
        return self._vendor.models()

    def model(self, name: str) -> Model | None:
        full = name if name.startswith("antigravity/") else f"antigravity/{name}"
        return next((model for model in self.models() if model.id == full), None)

    def _resolve(self, name: str) -> Model:
        model = self.model(name)
        if model is None:
            raise ValueError(f"Unknown Antigravity model {name!r}; run `xdog-ai models antigravity --sync`.")
        return model

    async def login(self) -> str:
        token = await self._vendor.login()
        await self.sync_models(force=True)
        return token

    async def sync_models(self, *, ttl: float = 86400, force: bool = False) -> tuple[Model, ...]:
        return await self._vendor.sync_models(ttl, force)

    def supports_native_request(self, model: str, request: ProtocolRequest) -> bool:
        info = self.model(model)
        if info is None or request.protocol != PROTOCOL or request.operation is not NativeOperation.GENERATE:
            return False
        body = request.json()
        generation = body.get("generationConfig", {})
        if not isinstance(generation, dict):
            return False
        modalities = generation.get("responseModalities", [])
        return not ("IMAGE" in modalities and info.supports_image_output is False)

    def _native_model(self, model: str, request: ProtocolRequest) -> Model:
        if not self.supports_native_request(model, request):
            raise NotImplementedError("Model does not support the requested protocol/operation/output modality.")
        return self._resolve(model)

    async def request_complete(self, model: str, request: ProtocolRequest) -> NativeResponse:
        info = self._native_model(model, request)
        creds = await self._vendor.tokens.get()
        return await _transport.complete(creds, info.id.removeprefix("antigravity/"), request.json())

    async def request_stream(self, model: str, request: ProtocolRequest) -> NativeEventStream:
        info = self._native_model(model, request)
        if not info.supports_streaming:
            raise NotImplementedError("Model does not support streaming.")
        creds = await self._vendor.tokens.get()
        return await _transport.stream(creds, info.id.removeprefix("antigravity/"), request.json())

    def stream(
        self, model: str, context: Context, options: StreamOptions | None = None,
    ) -> EventStream[AssistantMessage]:
        info = self._resolve(model)
        opts = options or StreamOptions()
        result: asyncio.Future[AssistantMessage] = asyncio.get_running_loop().create_future()
        decoder = GeminiDecoder(replace(info, id=info.id.removeprefix("antigravity/")))

        async def generate() -> AsyncIterator[AssistantMessageEvent]:
            native: NativeEventStream | None = None
            builder = decoder.builder
            try:
                if opts.cancel and opts.cancel.is_set():
                    raise asyncio.CancelledError
                body = encode_context(context, info, opts)
                creds = await _transport.cancellable(self._vendor.tokens.get(), opts.cancel)
                yield StartEvent(partial=builder.snapshot())
                if info.supports_streaming:
                    native = await _transport.stream(creds, builder.model_id, body, opts.cancel)
                    async for frame in native:
                        for event in decoder.feed(frame.json()):
                            yield event
                else:
                    response = await _transport.cancellable(
                        _transport.complete(creds, builder.model_id, body), opts.cancel,
                    )
                    for event in decoder.feed(response.json()):
                        yield event
            except asyncio.CancelledError:
                if not opts.cancel or not opts.cancel.is_set():
                    result.cancel()
                    raise
                builder.stop_reason, builder.error_message = "aborted", "Generation cancelled."
            except Exception as exc:
                builder.stop_reason = "error"
                builder.error_message = (
                    "Antigravity network request failed." if isinstance(exc, httpx.HTTPError) else str(exc)
                )
            finally:
                if native is not None:
                    await native.aclose()
            builder.mark_dirty()
            message = builder.snapshot()
            if not result.done():
                result.set_result(message)
            if message.stop_reason in ("error", "aborted"):
                yield ErrorEvent(error=message.error_message or "Generation failed.", message=message)
            else:
                yield DoneEvent(stop_reason=message.stop_reason, message=message)

        return EventStream.from_async_generator(generate(), result)

    async def complete(self, model: str, context: Context, options: StreamOptions | None = None) -> AssistantMessage:
        return await self.stream(model, context, options).result()

    async def embed(
        self, model: str, input: str | tuple[str, ...] | EmbeddingRequest,
    ) -> EmbeddingResponse:
        raise NotImplementedError("Antigravity does not advertise an embedding endpoint.")

    async def web_search(self, model: str, query: str) -> AssistantMessage:
        return await self.complete(
            model, Context(messages=(UserMessage(content=query),)), StreamOptions(web_search=True),
        )

    async def image_generation(
        self, model: str, prompt: str | ImageGenerationRequest,
    ) -> ImageGenerationResponse:
        info = self._resolve(model)
        request = ImageGenerationRequest(prompt) if isinstance(prompt, str) else prompt
        body = await asyncio.to_thread(encode_image_request, request, info)
        async with asyncio.timeout(180):
            response = await self.request_complete(model, ProtocolRequest.from_json(PROTOCOL, body))
        return await asyncio.to_thread(decode_image_response, response.json(), info)
