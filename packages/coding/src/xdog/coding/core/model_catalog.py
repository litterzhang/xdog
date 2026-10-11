"""Model metadata on the same adapter that executes the owning session's requests."""
from __future__ import annotations

from xdog.agent.core import CatalogStreamFn, ModelStreamFn, StreamFn
from xdog.agent.extensions.providers import ExtensionProviderStream
from xdog.ai.types import Model


def validated(model: Model) -> Model:
    if (not isinstance(model, Model) or not model.id or len(model.id.encode()) > 256
            or any(ord(char) < 33 or ord(char) == 127 for char in model.id)
            or model.model_type != 'chat' or not model.supports_streaming
            or type(model.context_window) is not int or model.context_window <= 0
            or type(model.max_prompt_tokens) is not int or not 0 <= model.max_prompt_tokens <= model.context_window):
        raise ValueError('Model is not an available streaming chat profile on the configured adapter; no fallback')
    return model


def metadata_available(stream: StreamFn, name: str) -> bool:
    if name.startswith('ext-'):
        return True
    base = stream.base if isinstance(stream, ExtensionProviderStream) else stream
    return isinstance(base, ModelStreamFn)


def resolve(stream: StreamFn, name: str) -> Model | None:
    if (not isinstance(name, str) or not name or len(name.encode()) > 256
            or any(ord(char) < 33 or ord(char) == 127 for char in name)):
        raise ValueError('Invalid model identity')
    if not metadata_available(stream, name):
        return None  # Explicit opaque SDK adapters own their names, but supply no capability claims.
    model = stream.resolve_model(name) if isinstance(stream, ModelStreamFn) else None
    if model is None:
        raise ValueError('Model unavailable on the configured adapter; no fallback')
    return validated(model)


def catalog(stream: StreamFn) -> tuple[Model, ...]:
    if not isinstance(stream, CatalogStreamFn):
        return ()
    models = stream.models()
    if (len(models) > 576 or any(not isinstance(model, Model) for model in models)
            or len({model.id for model in models}) != len(models)):
        raise ValueError('Invalid or ambiguous configured model catalog')
    return tuple(validated(model) for model in models if model.model_type == 'chat' and model.supports_streaming)
