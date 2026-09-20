"""Export provider metadata in Codex's model_catalog_json format.

Catalogs are opt-in: provider sync only refreshes an existing generated file.
The extra _xdog key records configs managed by switch-cli; Codex ignores it.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from threading import Lock
from typing import Any

import tomlkit
from xdog.ai._config_io import read_json, write_json, write_text
from xdog.ai.paths import data_dir
from xdog.ai.types import Model

_LOCK = Lock()
_LOG = logging.getLogger(__name__)
_BASE_INSTRUCTIONS = (
    "You are a coding assistant working in the user's local workspace. "
    "Follow the user's instructions and applicable AGENTS.md files. "
    "Inspect relevant code before editing, preserve unrelated changes, "
    "use the available tools to complete the task, and verify your work. "
    "Report results and any remaining limitations clearly."
)


def catalog_path() -> Path:
    return data_dir() / "codex_models.json"


def compaction_limit(model: Model) -> int:
    # Leave room for the next response/tool results, respecting both limits.
    prompt = model.max_prompt_tokens or max(1, model.context_window - model.max_tokens)
    return max(1, min(prompt, model.context_window) * 9 // 10)


def model_entry(model: Model) -> dict[str, Any]:
    efforts = model.supported_efforts or (("medium",) if model.reasoning else ())
    # Codex's ReasoningEffort enum; provider-specific strings cannot be exported.
    efforts = tuple(e for e in efforts if e in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"})
    return {
        "slug": model.id,
        "display_name": model.name or model.id,
        "description": f"{model.name or model.id} via XDOG proxy",
        "context_window": model.context_window,
        "auto_compact_token_limit": compaction_limit(model),
        "effective_context_window_percent": 95,
        "supported_reasoning_levels": [{"effort": e, "description": f"{e} reasoning"} for e in efforts],
        "default_reasoning_level": "medium" if "medium" in efforts else next(iter(efforts), None),
        "shell_type": "unified_exec",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 0,
        "support_verbosity": False,
        "supports_reasoning_summaries": False,
        "supports_parallel_tool_calls": model.supports_parallel_tool_calls,
        "input_modalities": list(model.input or ("text",)),
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "experimental_supported_tools": [],
        "base_instructions": _BASE_INSTRUCTIONS,
    }


def _entries(models: Sequence[Model]) -> list[dict[str, Any]]:
    return [model_entry(m) for m in models if m.model_type == "chat" and m.supports_tool_calls and m.context_window > 0]


def generate_catalog(models: Sequence[Model], config_path: Path) -> Path:
    """Create a catalog and register the config that explicitly opted in."""
    path = catalog_path()
    with _LOCK:
        previous = read_json(path)
        configs = set(previous.get("_xdog", {}).get("config_paths", []))
        configs.add(str(config_path.resolve()))
        write_json(path, {"models": _entries(models), "_xdog": {"config_paths": sorted(configs)}})
    return path


def refresh_catalog(provider_id: str, models: Sequence[Model]) -> None:
    """Replace one provider's rows without dropping other providers' entries."""
    with _LOCK:
        path = catalog_path()
        if not path.exists():
            return
        catalog = read_json(path)
        # Never adopt a user-supplied catalog just because its filename matches.
        if "_xdog" not in catalog:
            return
        prefix = provider_id + "/"
        rows = [row for row in catalog["models"] if not row["slug"].startswith(prefix)]
        rows.extend(_entries(models))
        catalog["models"] = sorted(rows, key=lambda row: row["slug"])
        write_json(path, catalog)
        by_id = {m.id: m for m in models}
        for name in catalog["_xdog"].get("config_paths", []):
            config = Path(name)
            if not config.exists():
                continue
            settings = tomlkit.parse(config.read_text())
            if settings.get("model_provider") != "xdogproxy" or settings.get("model_catalog_json") != str(path):
                continue
            model = by_id.get(str(settings.get("model", "")))
            if model is not None and model.context_window > 0:
                settings["model_context_window"] = model.context_window
                settings["model_auto_compact_token_limit"] = compaction_limit(model)
                write_text(config, tomlkit.dumps(settings), backup=True)


async def refresh_after_sync(provider_id: str, models: Sequence[Model]) -> None:
    """Do file I/O off the event loop; an export failure must not break sync."""
    try:
        await asyncio.to_thread(refresh_catalog, provider_id, models)
    except (OSError, ValueError, KeyError, TypeError):
        _LOG.warning("Could not refresh the Codex catalog for %s; rerun xdog-ai switch-cli", provider_id)
