"""Provider factory."""

from xdog.ai.core import BaseProvider
from xdog.ai.types import ProviderType


def provider(provider_id: str) -> BaseProvider:
    """Get a provider instance."""
    if provider_id == ProviderType.COPILOT:
        from xdog.ai.providers.copilot import CopilotProvider
        return CopilotProvider()
    if provider_id == ProviderType.ANTIGRAVITY:
        from xdog.ai.providers.antigravity import AntigravityProvider
        return AntigravityProvider()

    from xdog.ai.providers.testing import _test_providers
    if provider_id in _test_providers:
        return _test_providers[provider_id]

    raise KeyError(f"Unknown provider: {provider_id!r}. Available: {', '.join(ProviderType)}")
