"""Select user-level models for XDOG clients and configure external CLIs for its proxy."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, cast

import httpx
import tomlkit
from xdog.ai._config_io import read_json, write_json, write_text
from xdog.ai.codex_catalog import compaction_limit, generate_catalog
from xdog.ai.paths import config_dir
from xdog.ai.types import InputModality, Model

_DEFAULTS = {"proxy_url": "http://127.0.0.1:8082", "api_key": "xdog-proxy-key"}
_NATIVE_AGENTS = ("coding", "claw")
_AGENTS = ("claude", "gemini", "codex", *_NATIVE_AGENTS)


def add_parser(sub: Any) -> None:
    parser = sub.add_parser(
        "switch-cli", help="Set coding/claw defaults or configure Claude, Gemini, and Codex for the proxy",
    )
    parser.add_argument("action", choices=(*_AGENTS, "set-key", "set-url", "config", "help"))
    parser.add_argument("target", nargs="?", help="Provider prefix, list, or value for set-key/set-url")
    parser.add_argument("--model", help="Select a model without prompting (Claude: primary/Opus role)")


def _config_path() -> Path:
    return config_dir() / "switch-cli.json"


def _load_config() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        path = Path.home() / ".config" / "switch-cli" / "config.json"
    return {**_DEFAULTS, **read_json(path)}


def _proxy_root(url: str) -> str:
    url = url.rstrip("/").removesuffix("/v1")
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise ValueError("Proxy URL must be an absolute HTTP(S) URL")
    return url


def fetch_models(proxy_url: str, api_key: str) -> list[Model]:
    response = httpx.get(
        f"{_proxy_root(proxy_url)}/v1/models",
        headers={"Authorization": f"Bearer {api_key}"}, timeout=20,
    )
    response.raise_for_status()
    rows = response.json()["data"]
    models = []
    for row in rows:
        limits = {key: row.get(key, 0) for key in ("context_window", "max_prompt_tokens", "max_output_tokens")}
        if any(type(value) is not int or value < 0 for value in limits.values()):
            raise ValueError("Proxy returned invalid model token limits")
        models.append(Model(
            id=row["id"], name=row.get("display_name", row["id"]),
            context_window=limits["context_window"], max_prompt_tokens=limits["max_prompt_tokens"],
            max_tokens=limits["max_output_tokens"], reasoning=row.get("reasoning", False),
            supported_efforts=tuple(row["supported_efforts"]) if row.get("supported_efforts") else None,
            input=tuple(cast(InputModality, m) for m in row.get("input", ["text"]) or ["text"]),
            supports_tool_calls=row.get("supports_tool_calls", True),
            supports_parallel_tool_calls=row.get("supports_parallel_tool_calls", False),
            supports_web_search=row.get("supports_web_search"),
            model_type=row.get("model_type", "chat"),
        ))
    return models


def _select_model(models: list[Model], role: str) -> Model | None:
    print(f"\nSelect model for {role}:")
    for index, model in enumerate(models, 1):
        print(f"  {index}. {model.id}")
    print("  0. Skip")
    while True:
        value = input(f"Choose (0-{len(models)}): ").strip()
        if value == "0":
            return None
        if value.isdigit() and 1 <= int(value) <= len(models):
            return models[int(value) - 1]
        print("Invalid choice, try again.")


def configure_codex(model: Model, models: list[Model], proxy_url: str, api_key: str) -> None:
    if model.context_window <= 0:
        raise ValueError("Model context window is unknown; sync models and restart the XDOG proxy first")
    if model.model_type != "chat" or not model.supports_tool_calls:
        raise ValueError("Codex requires a chat model with tool support")
    root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()
    config = root / "config.toml"
    settings = tomlkit.parse(config.read_text() if config.exists() else "")
    url = _proxy_root(proxy_url) + "/v1"
    providers = settings.setdefault("model_providers", {})
    providers["xdogproxy"] = {
        "name": "xdogproxy", "base_url": url, "wire_api": "responses",
        # Custom providers do not read OPENAI_API_KEY from auth.json by default.
        # Scope the proxy credential to this provider, preserving OpenAI login.
        "experimental_bearer_token": api_key,
    }
    settings["model"] = model.id
    settings["model_provider"] = "xdogproxy"
    settings["model_context_window"] = model.context_window
    settings["model_auto_compact_token_limit"] = compaction_limit(model)
    if model.supports_web_search is False:
        # Unknown support must preserve the user's setting and Codex defaults.
        settings["web_search"] = "disabled"
    settings["model_catalog_json"] = str(generate_catalog(models, config))
    write_text(config, tomlkit.dumps(settings), backup=True)
    print(f"Codex configured: {model.id} (context {model.context_window:,}). Restart Codex to apply changes.")


def _configure_claude(models: list[Model], primary: Model | None, url: str, key: str, direct: bool) -> None:
    opus = primary if direct else _select_model(models, "OPUS (primary)")
    sonnet = None if direct else _select_model(models, "SONNET (balanced)")
    haiku = None if direct else _select_model(models, "HAIKU (fast)")
    root = Path.home() / ".claude"
    settings = read_json(root / "settings.json")
    env = settings.setdefault("env", {})
    env.update(ANTHROPIC_BASE_URL=url, ANTHROPIC_AUTH_TOKEN=key, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    if opus:
        env.update(ANTHROPIC_DEFAULT_OPUS_MODEL=opus.id, ANTHROPIC_MODEL=opus.id)
        settings["model"] = opus.id
        if opus.context_window > 0:
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(opus.context_window)
            # Claude Code's auto-compaction window currently accepts up to 1M.
            env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(min(compaction_limit(opus), 1_000_000))
        else:
            # Never carry the previous model's larger window into an unknown one.
            env.pop("CLAUDE_CODE_MAX_CONTEXT_TOKENS", None)
            env.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
            print("Model context window is unknown; Claude Code will use its default limits.")
    if sonnet:
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = sonnet.id
    if haiku:
        env.update(ANTHROPIC_DEFAULT_HAIKU_MODEL=haiku.id, ANTHROPIC_SMALL_FAST_MODEL=haiku.id)
    config = read_json(root / "config.json")
    config["primaryApiKey"] = key
    onboarding_path = Path.home() / ".claude.json"
    onboarding = read_json(onboarding_path)
    onboarding["hasCompletedOnboarding"] = True
    write_json(root / "settings.json", settings, backup=True)
    write_json(root / "config.json", config, backup=True)
    write_json(onboarding_path, onboarding, backup=True)
    print("Claude Code configured. Restart Claude Code to apply changes.")


def _configure_gemini(model: Model | None, url: str, key: str) -> None:
    path = Path.home() / ".gemini" / ".env"
    text = path.read_text() if path.exists() else ""
    values = {"GOOGLE_GEMINI_BASE_URL": url, "GEMINI_API_KEY": key, "GEMINI_TELEMETRY_ENABLED": "false"}
    if model:
        values["GEMINI_MODEL"] = model.id
    for name, value in values.items():
        line = f"{name}={json.dumps(value)}"
        pattern = rf"(?m)^(?:export\s+)?{name}\s*=.*$"
        if re.search(pattern, text):
            text = re.sub(pattern, lambda _: line, text)
        else:
            text = text.rstrip("\n") + "\n" + line + "\n"
    write_text(path, text, backup=True)
    print("Gemini CLI environment configured. Restart Gemini CLI to apply changes.")
    print("Note: Gemini CLI needs a Gemini-native endpoint; XDOG's proxy currently serves Anthropic/OpenAI only.")


def _configure_native(agent: str, model: Model) -> None:
    # Keep the dependency direction ai <- coding/claw: mirror their small XDG
    # path rules without importing either application into the foundation.
    override = os.environ.get(f"{agent.upper()}_DIR")
    if override:
        root = Path(override).expanduser()
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        root = base / "xdog" / agent
    if agent == "coding":
        path = root / "settings.json"
        settings = read_json(path)
        settings["default_model"] = model.id
        write_json(path, settings, backup=True)
        print(f"xdog-coding default model: {model.id} ({path}). Applies to new sessions without overrides.")
    else:
        import yaml

        path = root / "config.yaml"
        try:
            raw = yaml.safe_load(path.read_text()) if path.exists() else None
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {path}; no changes made") from exc
        if raw is not None and not isinstance(raw, dict):
            raise ValueError(f"Expected a YAML mapping in {path}; no changes made")
        settings = raw if raw is not None else {}
        settings["model"] = model.id
        write_text(path, yaml.safe_dump(settings, sort_keys=False, allow_unicode=True), backup=True)
        print(f"xdog-claw default model: {model.id} ({path}). Restart the gateway to apply it.")


def run(args: argparse.Namespace) -> None:
    native = args.action in _NATIVE_AGENTS
    cfg = {} if native else _load_config()
    if args.action == "help":
        print("xdog-ai switch-cli <claude|gemini|codex|coding|claw> <provider|list> [--model provider/model]")
        print("xdog-ai switch-cli <set-url|set-key> <value>\nxdog-ai switch-cli config")
        return
    if args.action == "config":
        print(f"Proxy URL: {cfg['proxy_url']}\nAPI key: {'configured' if cfg['api_key'] else 'empty'}")
        return
    if args.action in ("set-key", "set-url"):
        if args.target is None:
            raise ValueError(f"{args.action} requires a value")
        key = "api_key" if args.action == "set-key" else "proxy_url"
        cfg[key] = args.target if key == "api_key" else _proxy_root(args.target)
        write_json(_config_path(), cfg, backup=True)
        print(f"Saved {key}.")
        return
    if not args.target:
        raise ValueError("Specify a provider prefix or list")
    url = ""
    if native:
        import xdog.ai as ai
        models = list(ai.load().models())
    else:
        url = _proxy_root(cfg["proxy_url"])
        models = fetch_models(url, cfg["api_key"])
    if args.target == "list":
        for prefix in sorted({m.id.split("/", 1)[0] for m in models if "/" in m.id}):
            print(f"  {prefix}")
        return
    filtered = [m for m in models if m.id.startswith(args.target + "/") and m.model_type == "chat"]
    if not filtered:
        raise ValueError(f"No chat models found for {args.target!r}")
    primary = None
    if args.model:
        primary = next((m for m in filtered if m.id in (args.model, f"{args.target}/{args.model}")), None)
        if primary is None:
            raise ValueError(f"Model {args.model!r} not found for {args.target!r}")
    if args.action == "claude":
        _configure_claude(filtered, primary, url, cfg["api_key"], bool(args.model))
    else:
        model = primary or _select_model(filtered, "MODEL (primary)")
        if args.action == "gemini":
            _configure_gemini(model, url, cfg["api_key"])
        elif model is None:
            print("No model selected; no changes made.")
        elif args.action == "codex":
            configure_codex(model, models, url, cfg["api_key"])
        else:
            _configure_native(args.action, model)
