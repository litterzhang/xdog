"""Context token cache for WeChat messages.

Each outbound message must echo the most recent context_token received
from that user. This module provides an in-memory cache backed by
disk persistence for restart survival.

Ported from openclaw-weixin src/messaging/inbound.ts context token store.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# In-memory store: {(account_id, user_id) → token}
_token_store: dict[tuple[str, str], str] = {}


def set_context_token(account_id: str, user_id: str, token: str) -> None:
    """Store a context token for a given account+user pair (memory + disk)."""
    _token_store[(account_id, user_id)] = token
    logger.debug("setContextToken: account=%s user=%s", account_id, user_id)


def get_context_token(account_id: str, user_id: str) -> str | None:
    """Retrieve the cached context token for a given account+user pair."""
    val = _token_store.get((account_id, user_id))
    logger.debug(
        "getContextToken: account=%s user=%s found=%s",
        account_id,
        user_id,
        val is not None,
    )
    return val


def read_context_tokens(state_dir: Path, account_id: str) -> dict[str, str]:
    """Read detached cache data; never mutate the owning loop's token map."""
    file_path = _resolve_file_path(state_dir, account_id)
    try:
        if not file_path.exists():
            return {}
        value = json.loads(file_path.read_text(encoding="utf-8"))
        return {key: token for key, token in value.items() if isinstance(key, str) and isinstance(token, str) and token}
    except Exception as exc:
        logger.warning("restoreContextTokens: failed to read %s: %s", file_path, exc)
        return {}


def context_token_snapshot(account_id: str) -> dict[str, str]:
    return {user: token for (account, user), token in _token_store.items() if account == account_id}


def restore_context_tokens(state_dir: Path, account_id: str) -> None:
    """Explicit synchronous setup helper; production adopts detached reads on its owner."""
    for user, token in read_context_tokens(state_dir, account_id).items():
        set_context_token(account_id, user, token)


def write_context_tokens(state_dir: Path, account_id: str, tokens: dict[str, str]) -> None:
    file_path = _resolve_file_path(state_dir, account_id)
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(tokens), encoding="utf-8")
        try:
            os.chmod(str(file_path), 0o600)
        except OSError:
            pass
    except Exception as exc:
        logger.warning("persistContextTokens: failed to write %s: %s", file_path, exc)


def persist_context_tokens(state_dir: Path, account_id: str) -> None:
    write_context_tokens(state_dir, account_id, context_token_snapshot(account_id))


def clear_context_tokens(account_id: str) -> None:
    """Remove all context tokens for a given account from memory."""
    keys_to_remove = [k for k in _token_store if k[0] == account_id]
    for k in keys_to_remove:
        del _token_store[k]


def _resolve_file_path(state_dir: Path, account_id: str) -> Path:
    return state_dir / "weixin" / "accounts" / f"{account_id}.context-tokens.json"
