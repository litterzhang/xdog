"""Antigravity authentication and model discovery, independent of message format."""
from __future__ import annotations

import asyncio
import logging
import time

import httpx
from xdog.ai.core import AuthResult, BaseVendor
from xdog.ai.types import Context, Model
from xdog.ai.vendors.antigravity import _auth, _model_sync, _transport

logger = logging.getLogger(__name__)


class AntigravityVendor(BaseVendor):
    def __init__(self) -> None:
        self.tokens = _auth.TokenManager()

    @property
    def id(self) -> str:
        return "antigravity"

    @property
    def name(self) -> str:
        return "Google Antigravity"

    async def resolve_auth(self, model: Model, context: Context | None = None) -> AuthResult:
        creds = await self.tokens.get()
        return AuthResult(api_key=creds.access_token, base_url=_auth.base_url())

    async def login(self) -> str:
        return await _auth.login()

    def models(self) -> tuple[Model, ...]:
        cached = _model_sync.read_cache()
        return (
            _model_sync.parse_models(cached[0], _auth.base_url(), _model_sync.load_overrides())
            if cached else ()
        )

    async def sync_models(self, ttl: float = 86400, force: bool = False) -> tuple[Model, ...]:
        cached = await asyncio.to_thread(_model_sync.read_cache)
        overrides = await asyncio.to_thread(_model_sync.load_overrides)
        if not force and cached and time.time() - cached[1] < ttl:
            return _model_sync.parse_models(cached[0], _auth.base_url(), overrides)
        creds = await self.tokens.get()
        try:
            payload = await _transport.post_json(creds, "fetchAvailableModels", {"project": creds.project_id})
        except (httpx.HTTPError, _transport.AntigravityHTTPError):
            if not cached:
                raise
            logger.warning("Antigravity model sync failed; using cached catalogue.")
            return _model_sync.parse_models(cached[0], _auth.base_url(), overrides)
        models = _model_sync.parse_models(payload, _auth.base_url(), overrides)
        await asyncio.to_thread(_model_sync.write_cache, payload)
        return models
