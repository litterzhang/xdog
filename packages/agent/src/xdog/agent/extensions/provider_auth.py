"""Lazy worker-private provider credentials; no login, persistence, or transport policy.

Import ``ProviderAuth`` from this module and pass it to ``register_provider(auth=...)``.
Only that callback receives a fourth positional ``ProviderCredential`` argument.
Trusted extensions must not copy credentials into public events, logs, or metadata.
"""
from __future__ import annotations

import inspect
import os
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ProviderCredential:
    """One admission's credential, deliberately excluded from repr (not a sandbox)."""

    kind: Literal['bearer', 'api_key']
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if (self.kind not in ('bearer', 'api_key') or not isinstance(self.value, str)
                or not 1 <= len(self.value) <= 8192
                or any(not 33 <= ord(char) <= 126 for char in self.value)):
            raise ValueError('Invalid provider credential')


@dataclass(frozen=True)
class ProviderAuth:
    """Exactly one explicit environment source or sync/async zero-argument resolver.

    Sources are consulted afresh at stream admission, never during registration or
    catalog inspection. Resolvers run in the trusted worker and must not block its
    event loop. This layer neither retries nor refreshes/caches credentials itself.
    """

    kind: Literal['bearer', 'api_key'] = 'bearer'
    env: str | None = field(default=None, repr=False)
    resolver: Callable[[], Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.kind not in ('bearer', 'api_key') or (self.env is None) == (self.resolver is None)
                or self.env is not None and (not isinstance(self.env, str)
                    or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', self.env) is None)
                or self.resolver is not None and not callable(self.resolver)):
            raise ValueError('Provider auth requires one explicit environment source or resolver')

    async def resolve(self) -> ProviderCredential:
        value = os.environ.get(self.env) if self.env is not None else self.resolver() if self.resolver else None
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, str):
            raise ValueError('Invalid provider credential')
        return ProviderCredential(self.kind, value)


async def _failure() -> AsyncIterator[dict[str, Any]]:
    # Public by design: neither source names nor resolver exceptions cross the wire.
    yield {'type': 'error', 'message': 'Extension provider credentials unavailable'}


def bind_callback(callback: Callable[..., Any], auth: ProviderAuth,
                  is_current: Callable[[], bool]) -> Callable[..., Any]:
    """Keep auth in the private callback slot, never in serialized registration info."""
    if not isinstance(auth, ProviderAuth):
        raise ValueError('Provider auth must be an explicit ProviderAuth binding')

    async def bound(model: dict[str, Any], context: dict[str, Any], options: dict[str, Any]) -> Any:
        try:
            credential = await auth.resolve()
        except Exception:
            # An explicit subsequent attempt may resolve new credentials; never replay.
            return _failure()
        if not is_current():
            raise ValueError('Provider registration changed during credential resolution')
        result = callback(model, context, options, credential)
        return await result if inspect.isawaitable(result) else result

    return bound
