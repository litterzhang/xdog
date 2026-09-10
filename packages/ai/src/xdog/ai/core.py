"""Core interfaces — Provider, Protocol, Vendor, AuthResult."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xdog.ai.native import NativeEventStream, NativeOperation, NativeResponse, ProtocolRequest
    from xdog.ai.types import (
        AssistantMessage,
        Context,
        EmbeddingRequest,
        EmbeddingResponse,
        Model,
        StreamOptions,
    )
    from xdog.ai.utils.event_stream import EventStream


@dataclass(frozen=True)
class AuthResult:
    """Resolved authentication credentials."""

    api_key: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    base_url: str = ""


class BaseProvider(ABC):
    """User-facing provider interface."""

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def models(self) -> tuple[Model, ...]: ...

    @abstractmethod
    def model(self, name: str) -> Model | None: ...

    @abstractmethod
    def stream(self, model: str, context: Context, options: StreamOptions | None = None) -> EventStream[AssistantMessage]: ...

    @abstractmethod
    async def complete(self, model: str, context: Context, options: StreamOptions | None = None) -> AssistantMessage: ...

    @abstractmethod
    async def embed(
        self, model: str, input: str | tuple[str, ...] | EmbeddingRequest,
    ) -> EmbeddingResponse: ...

    @abstractmethod
    async def web_search(self, model: str, query: str) -> AssistantMessage: ...

    def supports_native_request(self, model: str, request: ProtocolRequest) -> bool:
        """Report native request support without authentication or I/O."""
        return False

    async def request_complete(self, model: str, request: ProtocolRequest) -> NativeResponse:
        """Execute a lossless protocol-native request when supported."""
        raise NotImplementedError(f"Provider {self.id!r} does not support protocol-native requests")

    async def request_stream(self, model: str, request: ProtocolRequest) -> NativeEventStream:
        """Open a lossless protocol-native event stream when supported."""
        raise NotImplementedError(f"Provider {self.id!r} does not support protocol-native requests")

    @abstractmethod
    async def login(self) -> str: ...

    @abstractmethod
    async def sync_models(self, *, ttl: float = 86400, force: bool = False) -> tuple[Model, ...]: ...


class BaseProtocol(ABC):
    """Wire-format protocol."""

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def stream(self, model: Model, context: Context, options: StreamOptions, auth: AuthResult) -> EventStream[AssistantMessage]: ...

    def supports_native_operation(self, operation: NativeOperation) -> bool:
        """Report which constrained native operations this protocol supports."""
        from xdog.ai.native import NativeOperation

        return operation is NativeOperation.GENERATE

    def native_auth_context(self, request: ProtocolRequest) -> Context:
        """Project a native request into the context needed for vendor auth."""
        from xdog.ai.types import Context

        return Context()

    async def request_complete(
        self, model: Model, request: ProtocolRequest, auth: AuthResult,
    ) -> NativeResponse:
        """Execute a protocol-native request when supported."""
        raise NotImplementedError(f"Protocol {self.id!r} does not support protocol-native requests")

    async def request_stream(
        self, model: Model, request: ProtocolRequest, auth: AuthResult,
    ) -> NativeEventStream:
        """Open a protocol-native event stream when supported."""
        raise NotImplementedError(f"Protocol {self.id!r} does not support protocol-native requests")

    async def embed(self, model: Model, request: EmbeddingRequest, auth: AuthResult) -> EmbeddingResponse:
        raise NotImplementedError(f"Protocol {self.id!r} does not support embeddings")


class BaseVendor(ABC):
    """Vendor — authentication and model sync."""

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def resolve_auth(self, model: Model, context: Context | None = None) -> AuthResult: ...

    @abstractmethod
    async def sync_models(self, ttl: float = 86400, force: bool = False) -> tuple[Model, ...]: ...

    async def login(self) -> str:
        raise NotImplementedError(f"Vendor {self.id!r} does not support login")
