"""Settings manager: hierarchical config (global > project > session)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from xdog.coding.config import validate_max_messages_per_turn


class SessionSettings(BaseModel):
    """Per-session overrides that live inside the session JSON."""

    model_config = ConfigDict(extra="ignore")

    max_messages_per_turn: int | None = None

    @field_validator('max_messages_per_turn', mode='before')
    @classmethod
    def _batch_limit(cls, value: object) -> int:
        return validate_max_messages_per_turn(value)

    model: str | None = None
    thinking_level: str | None = None
    custom_instructions: str = ""


class SettingsManager:
    """Manages the three-tier settings hierarchy.

    Precedence (highest to lowest):
      1. Session settings
      2. Project settings
      3. Global settings
    """

    def __init__(
        self,
        *,
        global_path: Path | None = None,
        project_dir: Path | None = None,
    ) -> None:
        from xdog.coding.config import (
            GlobalConfig,
            ProjectConfig,
            get_global_settings_path,
        )

        self._global_path = global_path or get_global_settings_path()
        self._global = GlobalConfig.load(self._global_path)
        self._project = ProjectConfig.load(project_dir) if project_dir else ProjectConfig()
        self._session = SessionSettings()

    # --- Getters (resolved) ---

    @property
    def max_messages_per_turn(self) -> int:
        for value in (self._session.max_messages_per_turn, self._project.max_messages_per_turn,
                      self._global.max_messages_per_turn):
            if value is not None:
                return validate_max_messages_per_turn(value)
        return 10

    def set_session_max_messages_per_turn(self, value: int) -> None:
        self._session = self._session.model_copy(update={
            'max_messages_per_turn': validate_max_messages_per_turn(value)})


    @property
    def model(self) -> str:
        return (
            self._session.model
            or self._project.model
            or self._global.default_model
        )

    @property
    def thinking_level(self) -> str:
        return (
            self._session.thinking_level
            or self._project.thinking_level
            or self._global.thinking_level
        )

    @property
    def custom_instructions(self) -> str:
        parts: list[str] = []
        if self._global.custom_instructions:
            parts.append(self._global.custom_instructions)
        if self._project.custom_instructions:
            parts.append(self._project.custom_instructions)
        if self._session.custom_instructions:
            parts.append(self._session.custom_instructions)
        return "\n\n".join(parts)

    @property
    def goals_enabled(self) -> bool:
        # Only trusted global config enables an optional in-process capability;
        # a project/session snapshot cannot grant it.
        from xdog.coding.config import validate_goals_enabled
        return validate_goals_enabled(self._global.goals_enabled)

    @property
    def child_presets(self) -> Any:
        return self._global.child_presets  # Explicit global templates; project/session data cannot replace them.

    @property
    def mcp_servers(self) -> dict[str, Any]:
        return {**self._global.mcp_servers, **self._project.mcp_servers}

    @property
    def mcp_credential_backend(self) -> str:
        from xdog.agent.mcp.oauth_native_store import storage_backend
        return storage_backend(self._global.mcp_credential_backend)  # Project/session cannot select weaker storage.

    @property
    def trusted_extensions(self) -> tuple[str, ...]:
        from xdog.agent.extensions.runtime import names
        return names(self._global.extensions)  # Project/session metadata cannot authorize Python imports.

    @property
    def extension_root(self) -> Path:
        from xdog.coding.config import get_extensions_dir, get_global_settings_path
        if not self._global.extension_dir:
            return get_extensions_dir()
        root = Path(self._global.extension_dir).expanduser()
        return root if root.is_absolute() else (self._global_path or get_global_settings_path()).parent / root

    @property
    def extension_settings(self) -> dict[str, dict[str, Any]]:
        return self._global.extension_settings

    # --- Session-level mutators ---

    def set_session_model(self, model: str) -> None:
        self._session = self._session.model_copy(update={"model": model})

    def set_session_thinking(self, level: str) -> None:
        self._session = self._session.model_copy(update={"thinking_level": level})

    def set_session_instructions(self, text: str) -> None:
        self._session = self._session.model_copy(update={"custom_instructions": text})

    # --- Serialization helpers ---

    def session_to_dict(self) -> dict[str, Any]:
        return self._session.model_dump(exclude_none=True)

    def load_session_settings(self, data: dict[str, Any]) -> None:
        self._session = SessionSettings.model_validate(data)
