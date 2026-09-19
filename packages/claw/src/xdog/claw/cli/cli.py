"""CLI commands for claw.

Commands:
    xdog-claw onboard                                       — Interactive setup wizard
    xdog-claw gateway start [--config PATH] [--foreground]  — Start the gateway daemon
    xdog-claw gateway stop                                  — Stop the running gateway
    xdog-claw gateway status                                — Check if gateway is running
    xdog-claw tui [--group GROUP]                           — Connect to gateway with interactive chat
    xdog-claw channel login --weixin                        — Log in to a channel
"""
from __future__ import annotations

import os
import signal
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import click
from xdog.claw.config import ClawConfig, ToolConfigError, get_config_path, get_state_dir, load_config


def _resolve_config(config_path: str | None) -> ClawConfig:
    """Load config from the given path or default location."""
    if config_path:
        path = Path(config_path).expanduser()
        if not path.exists():
            click.echo(f"Error: Config file not found: {path}", err=True)
            sys.exit(1)
        return load_config(path)
    return load_config()


def _update_config_weixin(config_file: Path, account_id: str) -> None:
    """Auto-update config.yaml with WeChat login credentials."""
    try:
        import yaml
    except ImportError:
        click.echo("Warning: pyyaml not installed, cannot auto-update config", err=True)
        return

    config_file.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if config_file.exists():
        try:
            raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw
        except Exception:
            pass

    updated = {**existing, "weixin_enabled": True, "weixin_account_id": account_id}
    config_file.write_text(
        yaml.dump(updated, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _get_version() -> str:
    try:
        from importlib.metadata import version
        return version("claw")
    except Exception:
        return "0.1.0"


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version=_get_version(), prog_name="xdog-claw")
def cli() -> None:
    """claw — AI agent orchestration runtime."""


@cli.command("generate-image")
@click.argument("prompt")
@click.option("--workspace", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".",
              help="Workspace containing references and generated files (default: current directory).")
@click.option("--output-dir", default="generated-images", show_default=True,
              help="Output directory inside the workspace.")
@click.option("--reference", "references", multiple=True,
              help="Reference image path inside the workspace; repeat up to four times.")
@click.option("--aspect-ratio", default="9:16", show_default=True, help="Image aspect ratio, e.g. 1:1 or 9:16.")
@click.option("--image-size", type=click.Choice(["1K", "2K", "4K"]), default="1K", show_default=True)
@click.option("--config", "config_path", type=click.Path(), default=None,
              help="Claw config containing the enabled tool and selected image model.")
def generate_image(
    prompt: str, workspace: Path, output_dir: str, references: tuple[str, ...],
    aspect_ratio: str, image_size: str, config_path: str | None,
) -> None:
    """Generate image assets via xdog.ai; print JSON containing saved paths.

    Enable generate_image and choose its model with xdog-claw onboard first.
    No gateway or proxy is required.
    """
    import asyncio

    from xdog.claw.core.tools.tool_generate_image import ImageGenerationError, generate_images

    try:
        config = load_config(Path(config_path).expanduser() if config_path else get_config_path())
        if config.enabled_tools is None or "generate_image" not in config.enabled_tools:
            raise ImageGenerationError("generate_image is disabled. Enable it with `xdog-claw onboard` first.")
        if not config.image_model:
            raise ImageGenerationError("No image model configured. Select one with `xdog-claw onboard` first.")
        result = asyncio.run(generate_images(
            prompt,
            workspace=workspace,
            output_dir=output_dir,
            references=references,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            model=config.image_model,
        ))
    except (ImageGenerationError, ToolConfigError) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(result.to_json())


# ---------------------------------------------------------------------------
# xdog-claw onboard
# ---------------------------------------------------------------------------

def _configure_tools(models: list[Any], existing: ClawConfig) -> tuple[tuple[str, ...], str]:
    """Choose an explicit tool set and, if needed, a confirmed image model."""
    from xdog.claw.core.tools import default_enabled_tools, registered_names

    names = sorted(registered_names())
    image_models = [model for model in models if model.supports_image_output is True]
    selected = default_enabled_tools() if existing.enabled_tools is None else existing.enabled_tools
    default = ",".join(str(i) for i, name in enumerate(names, 1) if name in selected) or "none"
    click.echo("  Available tools:")
    for i, name in enumerate(names, 1):
        marker = "x" if name in selected else " "
        note = " (requires an image_generation model)" if name == "generate_image" else ""
        click.echo(f"    {i}. [{marker}] {name}{note}")
    click.echo("  Enter comma-separated numbers or names, 'all', or 'none'.")
    while True:
        value = click.prompt("  Enabled tools", default=default).strip()
        if value.lower() == "none":
            enabled: tuple[str, ...] = ()
        elif value.lower() == "all":
            enabled = tuple(names)
        else:
            resolved: set[str] = set()
            invalid = []
            for item in value.split(","):
                item = item.strip()
                if item.isdecimal() and 1 <= int(item) <= len(names):
                    resolved.add(names[int(item) - 1])
                elif item in names:
                    resolved.add(item)
                else:
                    invalid.append(item or "(empty)")
            if invalid:
                click.echo(f"  Error: Unknown tool selection: {', '.join(invalid)}")
                continue
            enabled = tuple(name for name in names if name in resolved)
        if "generate_image" not in enabled:
            return enabled, ""
        if not image_models:
            click.echo(
                "  Error: No model has confirmed image_generation support. "
                "Deselect generate_image, or log in to an image provider and rerun onboarding."
            )
            continue
        click.echo("  Image-generation models:")
        for i, model in enumerate(image_models, 1):
            click.echo(f"    {i}. {model.id} — {model.name}")
        saved_index = next(
            (i for i, model in enumerate(image_models, 1) if model.id == existing.image_model), None,
        )
        choice = click.prompt("  Select image model", type=click.IntRange(1, len(image_models)), default=saved_index)
        click.echo(f"  Image model: {image_models[choice - 1].id}")
        return enabled, image_models[choice - 1].id


@cli.command()
@click.option("--config", "config_path", type=click.Path(), default=None,
              help=f"Path to config.yaml (default: {get_config_path()})")
def onboard(config_path: str | None) -> None:
    """Interactive setup wizard — configure providers and models."""
    import asyncio

    config_file = Path(config_path).expanduser() if config_path else get_config_path()
    try:
        existing = load_config(config_file) if config_file.exists() else ClawConfig()
    except ToolConfigError as exc:
        raise click.ClickException(str(exc)) from None

    click.echo("=" * 50)
    click.echo("  claw — Setup Wizard")
    click.echo("=" * 50)
    click.echo()

    # Step 1: Provider login
    click.echo("Step 1: LLM Provider")
    click.echo("-" * 30)

    try:
        import xdog.ai as ai
        runtime = ai.load()
        active = runtime.active_providers()
    except Exception:
        active = []

    if active:
        click.echo(f"  Active providers: {', '.join(active)}")
        do_login = click.confirm("  Log in to another provider?", default=False)
    else:
        click.echo("  No providers configured.")
        do_login = click.confirm("  Log in to a provider now?", default=True)

    if do_login:
        click.echo()
        click.echo("  Available providers:")
        click.echo("    1. copilot (GitHub Copilot — recommended)")
        click.echo("    2. antigravity (Google Antigravity)")
        choice = click.prompt("  Select provider", type=click.IntRange(1, 2), default=1)

        if choice in (1, 2):
            provider_id = "copilot" if choice == 1 else "antigravity"
            click.echo()
            click.echo(f"  Logging in to {provider_id}...")
            try:
                asyncio.run(ai.login(provider_id))
                click.echo("  Logged in successfully.")
                # Reload runtime with new provider
                runtime = ai.load()
                active = runtime.active_providers()
            except Exception as exc:
                click.echo(f"  Login failed: {exc}", err=True)
                click.echo("  You can retry later with: xdog-claw onboard")

    # Step 2: Primary model
    click.echo()
    click.echo("Step 2: Primary Model")
    click.echo("-" * 30)

    models: list[Any] = []
    try:
        import xdog.ai as ai
        runtime = ai.load()
        if runtime.active_providers():
            # Refresh from the provider first so the list reflects newly available
            # models, not just whatever was cached; fall back to the cache on error.
            click.echo("  Syncing available models...")
            try:
                models = list(asyncio.run(runtime.sync_models(force=True)))
            except Exception:
                models = list(runtime.models())
    except Exception:
        pass

    # Model names and catalogue order are not capability rankings. Keep every
    # chat model selectable, including newly discovered families.
    chat_models = [
        m for m in models
        if m.model_type == "chat" and (m.output is None or "text" in m.output)
    ]

    if chat_models:
        click.echo("  Available models:")
        for i, m in enumerate(chat_models, 1):
            ctx = f"{m.context_window // 1000}k" if m.context_window else "?"
            click.echo(f"    {i}. {m.id} ({ctx} context)")

        default_idx = next((i for i, m in enumerate(chat_models, 1) if m.id == existing.model), None)
        choice = click.prompt("  Select primary model", type=click.IntRange(1, len(chat_models)), default=default_idx)
        primary_model = chat_models[choice - 1].id
        click.echo(f"  Selected: {primary_model}")
    else:
        primary_model = click.prompt("  Enter model name", default=existing.model or None)

    # Step 3: Tool set and tool-specific model
    click.echo()
    click.echo("Step 3: Tool Setup")
    click.echo("-" * 30)
    enabled_tools, image_model = _configure_tools(models, existing)

    # Step 4: Agent name
    click.echo()
    click.echo("Step 4: Agent Identity")
    click.echo("-" * 30)
    from xdog.claw.config import GroupDef, save_config
    main_group = next(
        (group for group in existing.groups if group.is_main),
        next((group for group in existing.groups if group.id == "main"), GroupDef(id="main", is_main=True)),
    )
    agent_name = click.prompt("  Agent name", default=main_group.name or "Claw")

    # Step 5: Write config, preserving channels, paths, and unrelated groups.
    click.echo()
    click.echo("Step 5: Save Configuration")
    click.echo("-" * 30)

    main_group = replace(main_group, name=agent_name, is_main=True, model_id="")
    groups = tuple(main_group if group.id == main_group.id else group for group in existing.groups)
    if not any(group.id == main_group.id for group in groups):
        groups = (*groups, main_group)
    config = replace(
        existing,
        model=primary_model,
        enabled_tools=enabled_tools, image_model=image_model, groups=groups,
    )

    config_file.parent.mkdir(parents=True, exist_ok=True)
    save_config(config, config_file)
    click.echo(f"  Config saved: {config_file}")

    # Step 6: Initialize workspace
    click.echo()
    click.echo("Step 6: Initialize Workspace")
    click.echo("-" * 30)

    from xdog.claw.core.prompt import init_workspace, set_identity_name, workspace_path
    data_dir = Path(config.data_dir)
    ws = Path(main_group.workspace) if main_group.workspace else workspace_path(data_dir / "groups" / main_group.id)
    init_workspace(ws, agent_name=agent_name)
    # init_workspace only writes IDENTITY.md when absent; force the chosen name so
    # re-running onboard to rename the agent actually updates an existing workspace.
    set_identity_name(ws, agent_name)
    click.echo(f"  Workspace: {ws}")

    # Done
    click.echo()
    click.echo("=" * 50)
    click.echo("  Setup complete!")
    click.echo()
    click.echo("  Start the gateway:")
    click.echo("    xdog-claw gateway start")
    click.echo()
    click.echo("  Then connect:")
    click.echo("    xdog-claw tui")
    click.echo("=" * 50)


# ---------------------------------------------------------------------------
# xdog-claw gateway {start,stop,status}
# ---------------------------------------------------------------------------

@cli.group()
def gateway() -> None:
    """Manage the gateway daemon."""


@gateway.command()
@click.option("--config", "config_path", type=click.Path(), default=None,
              help=f"Path to config.yaml (default: {get_config_path()})")
@click.option("--foreground", is_flag=True, default=False,
              help="Run in foreground (don't daemonize)")
def start(config_path: str | None, foreground: bool) -> None:
    """Start the gateway daemon."""
    config = _resolve_config(config_path)
    if not config.model and (not config.groups or any(not group.model_id for group in config.groups)):
        raise click.ClickException("No primary model configured. Run `xdog-claw onboard` first.")
    pid_path = Path(config.pid_file)

    from xdog.claw.core.runtime.gateway import read_pid
    existing_pid = read_pid(pid_path)
    if existing_pid is not None:
        click.echo(f"Gateway already running (PID: {existing_pid})")
        click.echo("Use 'xdog-claw gateway stop' to stop it first.")
        sys.exit(1)

    if foreground:
        click.echo("Starting gateway in foreground...")
        from xdog.claw.core.runtime.gateway import run_gateway
        run_gateway(config)
    else:
        _daemonize(config)


@gateway.command()
@click.option("--config", "config_path", type=click.Path(), default=None,
              help=f"Path to config.yaml (default: {get_config_path()})")
def stop(config_path: str | None) -> None:
    """Stop the running gateway."""
    config = _resolve_config(config_path)
    pid_path = Path(config.pid_file)

    from xdog.claw.core.runtime.gateway import read_pid
    pid = read_pid(pid_path)
    if pid is None:
        click.echo("Gateway is not running.")
        sys.exit(0)

    try:
        os.kill(pid, signal.SIGTERM)
        click.echo(f"Sent shutdown signal to gateway (PID: {pid})")

        import time
        for _ in range(30):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except OSError:
                click.echo("Gateway stopped.")
                if pid_path.exists():
                    pid_path.unlink()
                return

        click.echo("Warning: Gateway did not stop within 3 seconds")
        click.echo(f"You may need to kill it manually: kill -9 {pid}")
    except ProcessLookupError:
        click.echo("Gateway process not found (stale PID file)")
        if pid_path.exists():
            pid_path.unlink()
    except PermissionError:
        click.echo(f"Error: Permission denied sending signal to PID {pid}", err=True)
        sys.exit(1)


@gateway.command()
@click.option("--config", "config_path", type=click.Path(), default=None,
              help=f"Path to config.yaml (default: {get_config_path()})")
def status(config_path: str | None) -> None:
    """Show whether the gateway is running."""
    config = _resolve_config(config_path)
    pid_path = Path(config.pid_file)

    from xdog.claw.core.runtime.gateway import read_pid
    pid = read_pid(pid_path)
    if pid is not None:
        click.echo(f"Gateway running (PID: {pid})")
        click.echo(f"  Socket: {Path(config.socket_path)}")
        click.echo(f"  PID file: {pid_path}")
        for line in _channel_lines(config):
            click.echo(line)
        stale = _config_newer_than_process(config_path, pid_path)
        if stale:
            click.echo(f"  ! {stale}")
    else:
        click.echo("Gateway not running")
        if pid_path.exists():
            click.echo("  (stale PID file detected — cleaning up)")
            pid_path.unlink()


def _channel_lines(config: ClawConfig) -> list[str]:
    """What is actually carrying messages, not just that a process exists.

    A channel enabled in the file and absent from the process looks identical
    from outside: the gateway says "running", the user sends a message, and
    nothing happens. Naming the channels is the difference between that and a
    diagnosis.
    """
    lines = ["  Channels:"]
    if config.weixin_enabled:
        account = config.weixin_account_id or "(no account)"
        lines.append(f"    weixin: enabled (account={account})")
    if len(lines) == 1:
        lines.append("    (none enabled)")
    return lines


def _config_newer_than_process(config_path: str | None, pid_path: Path) -> str:
    """A warning when the config changed after the gateway read it.

    The gateway loads its config once, at startup. Editing the file afterwards —
    enabling a channel, say — changes nothing until a restart, and nothing in
    the system says so: the edit succeeds, the status stays "running", and the
    only symptom is a message that never gets answered.

    Compared by mtime rather than by content because the question is not "is the
    config different" but "did this process read this version".
    """
    from xdog.claw.config import get_config_path

    try:
        cfg = Path(config_path).expanduser() if config_path else get_config_path()
        if not cfg.exists() or not pid_path.exists():
            return ""
        drift = cfg.stat().st_mtime - pid_path.stat().st_mtime
        if drift <= 1:
            return ""
    except OSError:
        return ""
    mins = int(drift // 60)
    when = f"{mins}m" if mins else f"{int(drift)}s"
    return (
        f"config.yaml was modified {when} after the gateway started; "
        f"restart to apply it"
    )


def _daemonize(config: ClawConfig) -> None:
    """Fork into a background daemon process."""
    import time

    try:
        pid = os.fork()
        if pid > 0:
            click.echo("Starting gateway in background...")
            pid_path = Path(config.pid_file)
            from xdog.claw.core.runtime.gateway import read_pid

            actual_pid = None
            for _ in range(30):
                time.sleep(0.1)
                actual_pid = read_pid(pid_path)
                if actual_pid is not None:
                    break

            if actual_pid is not None:
                click.echo(f"Gateway running (PID: {actual_pid})")
            else:
                click.echo("Gateway may still be starting — check: xdog-claw gateway status")
            sys.exit(0)
    except OSError as exc:
        click.echo(f"Error: fork failed: {exc}", err=True)
        sys.exit(1)

    os.setsid()

    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as exc:
        click.echo(f"Error: second fork failed: {exc}", err=True)
        sys.exit(1)

    sys.stdin.close()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)

    log_path = get_state_dir() / "gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(devnull)
    if log_fd > 2:
        os.close(log_fd)

    from xdog.claw.core.runtime.gateway import run_gateway
    run_gateway(config)


# ---------------------------------------------------------------------------
# xdog-claw tui
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--config", "config_path", type=click.Path(), default=None,
              help=f"Path to config.yaml (default: {get_config_path()})")
@click.option("--group", "group_id", default="main",
              help="Group ID to connect to (default: main)")
def tui(config_path: str | None, group_id: str) -> None:
    """Open interactive chat with the running gateway."""
    config = _resolve_config(config_path)
    from xdog.claw.channels.tui.tui_client import run_tui
    run_tui(config.socket_path, group_id, model=config.model)


# ---------------------------------------------------------------------------
# xdog-claw channel login --weixin
# ---------------------------------------------------------------------------

@cli.group()
def channel() -> None:
    """Channel management commands."""


@channel.command()
@click.option("--config", "config_path", type=click.Path(), default=None,
              help=f"Path to config.yaml (default: {get_config_path()})")
@click.option("--group", "group_id", default="main", show_default=True,
              help=(
                  "The conversation this channel delivers into. A channel is a way to "
                  "reach an agent, not an agent of its own: messages arriving here join "
                  "that group's session, memory and persona instead of starting a new one."
              ))
@click.option("--weixin", "use_weixin", is_flag=True, default=False,
              help="Log in to WeChat channel")
@click.option("--base-url", default="", help="API base URL (WeChat only)")
def login(config_path: str | None, group_id: str, use_weixin: bool, base_url: str) -> None:
    """Log in to a channel."""
    if not use_weixin:
        click.echo("Error: specify a channel, e.g. --weixin", err=True)
        sys.exit(1)

    import asyncio

    config_file = Path(config_path).expanduser() if config_path else get_config_path()
    config = _resolve_config(config_path)

    from xdog.claw.channels.weixin.auth import (
        DEFAULT_BASE_URL,
        QrStartResult,
        QrWaitResult,
        WeixinAccountData,
        normalize_account_id,
        register_account_id,
        save_account,
        start_qr_login,
        wait_qr_login,
    )

    api_base = base_url or config.weixin_base_url or DEFAULT_BASE_URL
    state_dir = Path(config.data_dir)

    async def _run_login() -> None:
        start_result: QrStartResult = await start_qr_login(api_base_url=api_base)
        if not start_result.qrcode_url:
            click.echo(f"Error: {start_result.message}", err=True)
            sys.exit(1)

        click.echo("\nScan this QR code with WeChat:\n")
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(start_result.qrcode_url)
            qr.print_ascii(invert=True)
        except ImportError:
            pass
        click.echo(f"\nOr open: {start_result.qrcode_url}")
        click.echo("\nWaiting...\n")

        wait_result: QrWaitResult = await wait_qr_login(
            api_base_url=api_base, qrcode=start_result.qrcode, timeout_s=480,
        )

        if wait_result.connected and wait_result.bot_token and wait_result.account_id:
            normalized_id = normalize_account_id(wait_result.account_id)
            from datetime import datetime, timezone
            account_data = WeixinAccountData(
                token=wait_result.bot_token,
                base_url=wait_result.base_url or api_base,
                user_id=wait_result.user_id,
                saved_at=datetime.now(timezone.utc).isoformat(),
                group_id=group_id or "main",
            )
            save_account(state_dir, normalized_id, account_data)
            register_account_id(state_dir, normalized_id)
            _update_config_weixin(config_file, normalized_id)
            click.echo(f"\nWeChat connected: {normalized_id} → group {group_id or 'main'}")
            click.echo("Restart the gateway to apply: xdog-claw gateway stop && ... start")
        else:
            click.echo(f"\n{wait_result.message}", err=True)
            sys.exit(1)

    asyncio.run(_run_login())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the xdog-claw command."""
    cli()
