"""Compatibility exports for the shared Python extension hook types."""
from xdog.agent.extensions.types import (
    Extension,
    ExtensionHook,
    ExtensionManifest,
    PostToolUseHook,
    PreToolUseHook,
    StopHook,
)

__all__ = ["Extension", "ExtensionHook", "ExtensionManifest", "PostToolUseHook", "PreToolUseHook", "StopHook"]
