"""Interactive config selector: set default model, thinking level, etc."""

from __future__ import annotations

from pathlib import Path

from xdog.coding.config import GlobalConfig, get_settings_path


def _prompt_provider() -> str:
    """Ask the user which provider to configure."""
    providers = ["anthropic", "openai", "google"]
    print("\nAvailable providers:")
    for idx, name in enumerate(providers, 1):
        print(f"  {idx}. {name}")
    while True:
        choice = input("\nSelect provider [1-3]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(providers):
            return providers[int(choice) - 1]
        print("Invalid selection, try again.")


def _prompt_api_key(provider: str) -> str:
    """Show environment variable instructions for a provider.

    API keys are managed by the ai package via environment variables,
    not stored in the coding agent's config file.
    """
    env_vars = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
    }
    var = env_vars.get(provider, f"{provider.upper()}_API_KEY")
    print(f"\n  Set the {var} environment variable to use {provider}.")
    print(f"  Example: export {var}=your-key-here")
    return provider


def config_selector(config_path: Path | None = None) -> GlobalConfig:
    """Interactive configuration wizard.

    Lets the user set the default model and other preferences.
    API keys are managed by the ai package via environment variables.

    Returns the updated :class:`GlobalConfig`.
    """
    cfg = GlobalConfig.load(config_path)

    print("=== Pi Coding Agent - Configuration ===\n")
    print("Note: API keys are set via environment variables")
    print("  (ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY)\n")

    # Default model
    new_model = input(f"Default model [{cfg.default_model}]: ").strip()
    if new_model:
        cfg = cfg.model_copy(update={"default_model": new_model})

    # Thinking level
    new_thinking = input(
        f"Thinking level [{cfg.thinking_level}] (off/minimal/low/medium/high/xhigh): "
    ).strip()
    if new_thinking:
        cfg = cfg.model_copy(update={"thinking_level": new_thinking})

    # Tool permissions
    new_permissions = input(
        f"Permission mode [{cfg.permission_mode}] (ask/ask-all/allow-all/deny): "
    ).strip()
    if new_permissions:
        cfg = cfg.model_copy(update={"permission_mode": new_permissions})

    # Custom instructions
    if cfg.custom_instructions:
        print(f"\nCurrent custom instructions: {cfg.custom_instructions[:80]}...")
    new_instructions = input("Custom instructions (leave empty to keep): ").strip()
    if new_instructions:
        cfg = cfg.model_copy(update={"custom_instructions": new_instructions})

    save_path = config_path or get_settings_path()
    cfg.save(save_path)
    print(f"\nConfiguration saved to {save_path}")
    return cfg
