"""Short-lived, account-scoped evidence from completed provider search tools.

Only an actual search-tool completion is evidence, never answer prose or URLs.
This does not replace explicit model-catalogue capability declarations.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

from xdog.ai.paths import auth_file, data_dir
from xdog.ai.types import Model

_TTL_SECONDS = 24 * 60 * 60
_LOCK = threading.Lock()


def _path() -> Path:
    return data_dir() / "copilot_search_capabilities.json"


def account_key() -> str | None:
    try:
        raw = json.loads(auth_file().read_text())
        token = raw.get("copilot", {}).get("access_token")
        if isinstance(token, str) and token:
            return hashlib.sha256(token.encode()).hexdigest()
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _observations(key: str) -> dict[str, float]:
    try:
        raw = json.loads(_path().read_text())
        if raw.get("account") != key or raw.get("version") != 1:
            return {}
        values = raw.get("models", {})
        if not isinstance(values, dict):
            return {}
        now = time.time()
        return {
            model: float(timestamp) for model, timestamp in values.items()
            if isinstance(model, str) and type(timestamp) in (int, float)
            and 0 <= now - timestamp < _TTL_SECONDS
        }
    except (OSError, ValueError, AttributeError):
        return {}


def apply_observed_search(models: tuple[Model, ...]) -> tuple[Model, ...]:
    if not _path().exists():
        return models
    key = account_key()
    observed = _observations(key) if key else {}
    return tuple(
        replace(model, supports_web_search=True)
        if model.supports_web_search is None and model.id in observed else model
        for model in models
    )


def record_search(model_id: str, key: str | None) -> None:
    if key is None:
        return
    with _LOCK:
        if account_key() != key:
            return  # The login changed while the request was running.
        models = _observations(key)
        models[model_id] = time.time()
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".search-capabilities-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"version": 1, "account": key, "models": models}, stream)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
