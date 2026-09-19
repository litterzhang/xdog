"""Configuration loading and persistence for claw.

Follows XDG Base Directory Specification:
- Config:  $XDG_CONFIG_HOME/xdog/claw/  (config.yaml)
- Data:    $XDG_DATA_HOME/xdog/claw/    (sessions, groups, goals)
- State:   $XDG_STATE_HOME/xdog/claw/   (gateway.sock, gateway.pid, logs)

Override all XDG paths with a single directory via CLAW_DIR env var.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MONOREPO_NAME = "xdog"
APP_NAME = "claw"
ENV_OVERRIDE = "CLAW_DIR"


# ---------------------------------------------------------------------------
# XDG directory resolution
# ---------------------------------------------------------------------------

def _env_override_dir() -> Path | None:
    """Return the single-dir override, or None."""
    raw = os.environ.get(ENV_OVERRIDE)
    if not raw:
        return None
    return Path(raw).expanduser()


def get_config_dir() -> Path:
    """$XDG_CONFIG_HOME/xdog/claw/"""
    override = _env_override_dir()
    if override is not None:
        return override
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / MONOREPO_NAME / APP_NAME


def get_data_dir() -> Path:
    """$XDG_DATA_HOME/xdog/claw/"""
    override = _env_override_dir()
    if override is not None:
        return override
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / MONOREPO_NAME / APP_NAME


def get_state_dir() -> Path:
    """$XDG_STATE_HOME/xdog/claw/"""
    override = _env_override_dir()
    if override is not None:
        return override
    base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return base / MONOREPO_NAME / APP_NAME


def get_config_path() -> Path:
    """Default config file: $XDG_CONFIG_HOME/xdog/claw/config.yaml"""
    return get_config_dir() / "config.yaml"


@dataclass(frozen=True)
class GroupDef:
    """Definition of a single group from config.yaml."""

    id: str = ""
    name: str = ""
    is_main: bool = False
    workspace: str = ""
    # Agent config overrides (per-group; falls back to top-level ClawConfig.model)
    model_id: str = ""
    thinking_level: str = ""
    temperature: float | None = None
    max_tokens: int | None = None



@dataclass(frozen=True)
class ClawConfig:
    """Top-level claw configuration."""

    # Model settings
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    # None preserves the legacy tool set (without opt-in image generation).
    # An explicitly empty tuple disables all tools.
    enabled_tools: tuple[str, ...] | None = None
    image_model: str = ""

    # Paths (XDG defaults)
    data_dir: str = ""
    tasks_file: str = ""  # defaults to {data_dir}/scheduled_tasks.json
    socket_path: str = ""
    pid_file: str = ""

    # Gateway settings (flattened from gateway: section)
    max_concurrent_agents: int = 3
    daily_reset_hour: int = 4
    idle_reset_seconds: int = 0

    # WeChat channel settings
    weixin_enabled: bool = False
    weixin_account_id: str = ""
    weixin_token: str = ""
    weixin_base_url: str = ""

    # Groups defined in config
    groups: tuple[GroupDef, ...] = ()

    def __post_init__(self) -> None:
        """Fill in XDG defaults for empty path fields."""
        if self.enabled_tools is not None:
            if not isinstance(self.enabled_tools, (tuple, list)) or any(
                not isinstance(name, str) or not name.strip() for name in self.enabled_tools
            ):
                raise ToolConfigError("enabled_tools must be a list of tool names (or null for defaults).")
            object.__setattr__(self, "enabled_tools", tuple(dict.fromkeys(self.enabled_tools)))
        if not isinstance(self.image_model, str):
            raise ToolConfigError("image_model must be a provider/model string.")
        if self.image_model and ("/" not in self.image_model or not all(self.image_model.split("/", 1))):
            raise ToolConfigError("image_model must use provider/model format.")
        if not self.data_dir:
            object.__setattr__(self, "data_dir", str(get_data_dir()))
        if not self.tasks_file:
            object.__setattr__(self, "tasks_file", str(Path(self.data_dir) / "scheduled_tasks.json"))
        if not self.socket_path:
            object.__setattr__(self, "socket_path", str(get_state_dir() / "gateway.sock"))
        if not self.pid_file:
            object.__setattr__(self, "pid_file", str(get_state_dir() / "gateway.pid"))


class ToolConfigError(ValueError):
    """Invalid tool configuration must not silently enable the default tools."""


def _parse_groups(raw_groups: dict[str, Any] | None) -> tuple[GroupDef, ...]:
    """Parse groups section from config YAML."""
    if not raw_groups:
        return ()
    result = []
    for group_id, group_data in raw_groups.items():
        if isinstance(group_data, dict):
            workspace_raw = group_data.get("workspace", "")
            workspace = _expand_path(workspace_raw) if workspace_raw else ""
            result.append(
                GroupDef(
                    id=group_id,
                    name=group_data.get("name", group_id),
                    is_main=group_data.get("is_main", False),
                    workspace=workspace,
                    model_id=group_data.get("model_id", ""),
                    thinking_level=group_data.get("thinking_level", ""),
                    temperature=group_data.get("temperature"),
                    max_tokens=group_data.get("max_tokens"),
                )
            )
        else:
            result.append(GroupDef(id=group_id, name=str(group_data)))
    return tuple(result)


def _flatten_gateway(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten gateway: section into top-level config keys."""
    result = dict(raw)
    gateway_section = result.pop("gateway", None)
    if isinstance(gateway_section, dict):
        for key, value in gateway_section.items():
            if key not in result:
                result[key] = value
    return result


def _filter_known_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Filter raw dict to only include known ClawConfig fields."""
    known = {f.name for f in fields(ClawConfig)}
    return {k: v for k, v in raw.items() if k in known}


def _expand_path(path_str: str) -> str:
    """Expand ~ in path strings."""
    return str(Path(path_str).expanduser())


_cached_config: ClawConfig | None = None


def load_config(path: Path | None = None) -> ClawConfig:
    """Load configuration from a YAML file.

    Caches the result — config doesn't change during a session.
    Pass ``path`` explicitly to bypass the cache.
    """
    global _cached_config
    if path is None and _cached_config is not None:
        return _cached_config

    if path is None:
        path = get_config_path()

    if not path.exists():
        _cached_config = ClawConfig()
        return _cached_config

    try:
        import yaml
    except ImportError:
        logger.warning("pyyaml not installed; cannot load %s", path)
        return ClawConfig()

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return ClawConfig()

        # Parse groups separately before flattening
        groups = _parse_groups(raw.pop("groups", None))

        # Flatten gateway section
        flat = _flatten_gateway(raw)

        # Filter to known fields
        filtered = _filter_known_fields(flat)

        # Expand paths
        for path_field in ("data_dir", "socket_path", "pid_file"):
            if path_field in filtered and isinstance(filtered[path_field], str):
                filtered[path_field] = _expand_path(filtered[path_field])

        result = ClawConfig(**filtered, groups=groups)
        _cached_config = result
        return result

    except ToolConfigError:
        raise
    except Exception as exc:
        logger.warning("Failed to parse config at %s: %s", path, exc)
        return ClawConfig()


def save_config(config: ClawConfig, path: Path) -> None:
    """Save configuration to a YAML file.

    Note: api_key is excluded from serialization for security.
    Use environment variables or pass --api-key at runtime instead.
    """
    try:
        import yaml
    except ImportError:
        logger.warning("pyyaml not installed; cannot save config")
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(config)

    # Security: never write api_key to disk
    sensitive_keys = {"api_key", "weixin_token"}
    gateway_keys = {"max_concurrent_agents", "daily_reset_hour", "idle_reset_seconds"}

    # Build gateway section (immutable — no pop)
    gateway_section = {k: data[k] for k in gateway_keys if k in data}

    # Build groups section (immutable — no pop)
    groups_list = data.get("groups", ())
    groups_section = {
        g["id"]: {k: v for k, v in g.items() if k != "id"}
        for g in groups_list
        if g.get("id")
    }

    # Build top-level output excluding nested/sensitive keys
    excluded = sensitive_keys | gateway_keys | {"groups"}
    output = {k: v for k, v in data.items() if k not in excluded}

    if gateway_section:
        output["gateway"] = gateway_section
    if groups_section:
        output["groups"] = groups_section

    path.write_text(yaml.safe_dump(output, default_flow_style=False, sort_keys=False), encoding="utf-8")
