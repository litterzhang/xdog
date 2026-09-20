"""Global configuration and paths for the coding agent.

Follows XDG Base Directory Specification:
- Config:  $XDG_CONFIG_HOME/xdog/coding/  (settings, models, keybindings)
- Data:    $XDG_DATA_HOME/xdog/coding/    (sessions, auth, extensions)
- State:   $XDG_STATE_HOME/xdog/coding/   (logs, cache)
- Project: <cwd>/.coding/                        (project-local config)

Override all XDG paths with a single directory via CODING_DIR env var.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MONOREPO_NAME = "xdog"
APP_NAME = "coding"
VERSION = "2.3.0"
PROJECT_DIR_NAME = ".coding"
ENV_OVERRIDE = "CODING_DIR"


# ---------------------------------------------------------------------------
# XDG directory resolution
# ---------------------------------------------------------------------------

def _env_override_dir() -> Path | None:
    """Return the single-dir override, or None."""
    raw = os.environ.get(ENV_OVERRIDE)
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p


def get_config_dir() -> Path:
    """$XDG_CONFIG_HOME/xdog/coding/"""
    override = _env_override_dir()
    if override is not None:
        return override
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / MONOREPO_NAME / APP_NAME


def get_data_dir() -> Path:
    """$XDG_DATA_HOME/xdog/coding/"""
    override = _env_override_dir()
    if override is not None:
        return override
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / MONOREPO_NAME / APP_NAME


def get_state_dir() -> Path:
    """$XDG_STATE_HOME/xdog/coding/"""
    override = _env_override_dir()
    if override is not None:
        return override
    base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return base / MONOREPO_NAME / APP_NAME


def get_project_dir(cwd: Path | None = None) -> Path:
    """Project-local config directory: <cwd>/.coding/"""
    return (cwd or Path.cwd()) / PROJECT_DIR_NAME


# ---------------------------------------------------------------------------
# Convenience path helpers
# ---------------------------------------------------------------------------

def get_settings_path() -> Path:
    """Global settings JSON inside config dir."""
    return get_config_dir() / "settings.json"


def get_models_path() -> Path:
    """Custom models JSON inside config dir."""
    return get_config_dir() / "models.json"


def get_sessions_dir() -> Path:
    """Session storage directory inside data dir."""
    return get_data_dir() / "sessions"


def get_extensions_dir() -> Path:
    """Extensions directory inside data dir."""
    return get_data_dir() / "extensions"


def get_skills_dir() -> Path:
    """Skills directory inside config dir."""
    return get_config_dir() / "skills"


def get_prompts_dir() -> Path:
    """Prompt templates directory inside config dir."""
    return get_config_dir() / "prompts"


def get_themes_dir() -> Path:
    """Custom themes directory inside config dir."""
    return get_config_dir() / "themes"


def get_debug_log_path() -> Path:
    """Debug log file inside state dir."""
    return get_state_dir() / "debug.log"


# Legacy aliases for callers that referenced get_global_settings_path
get_global_settings_path = get_settings_path


def get_project_settings_path(project_dir: Path) -> Path:
    """Return path to a project-level settings JSON."""
    return get_project_dir(project_dir) / "settings.json"


# ---------------------------------------------------------------------------
# Home dir helper (kept for backward compat)
# ---------------------------------------------------------------------------

def get_home_dir() -> Path:
    """Return the user's home directory."""
    return Path.home()


# ---------------------------------------------------------------------------
# Platform info
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlatformInfo:
    """Immutable snapshot of the current platform."""

    os_name: str
    os_version: str
    python_version: str
    home_dir: str
    shell: str

    @classmethod
    def detect(cls) -> PlatformInfo:
        return cls(
            os_name=platform.system(),
            os_version=platform.version(),
            python_version=platform.python_version(),
            home_dir=str(get_home_dir()),
            shell=os.environ.get("SHELL", "/bin/bash"),
        )


# ---------------------------------------------------------------------------
# Global config model
# ---------------------------------------------------------------------------

class GlobalConfig(BaseModel):
    """Top-level configuration loaded from settings.json."""

    default_model: str = ""
    thinking_level: str = "normal"
    permission_mode: str = "ask"
    extensions: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    custom_instructions: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> GlobalConfig:
        """Load config from a JSON file, falling back to defaults."""
        target = path or get_settings_path()
        if not target.exists():
            return cls()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            return cls.model_validate(raw)
        except Exception:
            return cls()

    def save(self, path: Path | None = None) -> None:
        """Persist config to disk as JSON."""
        target = path or get_settings_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.model_dump(), indent=2),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Project config
# ---------------------------------------------------------------------------

class ProjectConfig(BaseModel):
    """Project-level overrides."""

    model: str | None = None
    thinking_level: str | None = None
    permission_mode: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    custom_instructions: str = ""
    extensions: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, project_dir: Path) -> ProjectConfig:
        target = get_project_settings_path(project_dir)
        if not target.exists():
            return cls()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            return cls.model_validate(raw)
        except Exception:
            return cls()


# ---------------------------------------------------------------------------
# Resolved runtime config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuntimeConfig:
    """Fully resolved configuration for a session."""

    model: str
    thinking_level: str
    permission_mode: str
    allowed_tools: tuple[str, ...]
    custom_instructions: str
    extensions: tuple[str, ...]
    working_dir: str
    platform_info: PlatformInfo

    @classmethod
    def resolve(
        cls,
        *,
        global_cfg: GlobalConfig,
        project_cfg: ProjectConfig | None = None,
        working_dir: Path | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> RuntimeConfig:
        """Merge global, project and CLI overrides into a single config."""
        ov = overrides or {}
        pcfg = project_cfg or ProjectConfig()
        model = ov.get("model") or pcfg.model or global_cfg.default_model
        thinking = ov.get("thinking_level") or pcfg.thinking_level or global_cfg.thinking_level
        permission_mode = (
            ov.get("permission_mode")
            or pcfg.permission_mode
            or global_cfg.permission_mode
        )

        merged_tools = list(global_cfg.allowed_tools) + list(pcfg.allowed_tools)
        merged_ext = list(global_cfg.extensions) + list(pcfg.extensions)

        instructions_parts: list[str] = []
        if global_cfg.custom_instructions:
            instructions_parts.append(global_cfg.custom_instructions)
        if pcfg.custom_instructions:
            instructions_parts.append(pcfg.custom_instructions)

        return cls(
            model=model,
            thinking_level=thinking,
            permission_mode=permission_mode,
            allowed_tools=tuple(merged_tools),
            custom_instructions="\n\n".join(instructions_parts),
            extensions=tuple(merged_ext),
            working_dir=str(working_dir or Path.cwd()),
            platform_info=PlatformInfo.detect(),
        )
