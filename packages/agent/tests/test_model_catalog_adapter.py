from __future__ import annotations

import pytest
from xdog.agent.core import CatalogStreamFn
from xdog.agent.extensions.providers import ExtensionProviderStream
from xdog.agent.helpers import stream_fn_from_provider
from xdog.ai.types import Model


class Provider:
    def __init__(self):
        self.lookups = []
    def models(self):
        return (Model(id='configured/model', context_window=48000),)
    def model(self, name):
        self.lookups.append(name)
        return self.models()[0] if name in ('model', 'configured/model') else None
    def stream(self, *args):
        pytest.fail('Metadata cannot invoke model stream')
    def sync_models(self, *args):
        pytest.fail('Metadata cannot trigger a catalog sync')
    def login(self, *args):
        pytest.fail('Metadata cannot authenticate/activate a provider')


def test_cached_catalog_comes_from_the_same_adapter_and_unknown_names_never_fall_back():
    provider = Provider()
    stream = stream_fn_from_provider(provider)
    assert isinstance(stream, CatalogStreamFn)
    assert stream.models()[0].id == 'configured/model' and not provider.lookups
    assert stream.resolve_model('model').id == 'configured/model'
    assert stream.resolve_model('unknown/model') is None


def test_extension_catalog_composes_independent_owned_models_without_global_registry_or_reserved_shadowing():
    class Runtime:
        def provider_models(self):
            return (Model(id='ext-owned-local/model', context_window=32000),)
        def provider_model(self, name):
            return self.provider_models()[0] if name == 'ext-owned-local/model' else None
    stream = ExtensionProviderStream(Runtime(), stream_fn_from_provider(Provider()))
    assert [model.id for model in stream.models()] == ['configured/model', 'ext-owned-local/model']
    assert stream.resolve_model('ext-owned-local/model').context_window == 32000
    class Broken(Provider):
        def models(self):
            return (Model(id='ext-unowned-local/model'),)
    with pytest.raises(ValueError, match='reserved'):
        ExtensionProviderStream(Runtime(), stream_fn_from_provider(Broken())).models()
