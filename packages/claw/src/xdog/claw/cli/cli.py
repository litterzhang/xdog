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
from pathlib import Path
from typing import Any

import click
from xdog.claw.config import ClawConfig, get_config_path, get_state_dir, load_config


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


# ---------------------------------------------------------------------------
# xdog-claw onboard
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--config", "config_path", type=click.Path(), default=None,
              help=f"Path to config.yaml (default: {get_config_path()})")
def onboard(config_path: str | None) -> None:
    """Interactive setup wizard — configure providers and models."""
    import asyncio

    config_file = Path(config_path).expanduser() if config_path else get_config_path()

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
        choice = click.prompt("  Select provider", type=int, default=1)

        if choice == 1:
            click.echo()
            click.echo("  Logging in to GitHub Copilot...")
            try:
                asyncio.run(ai.login("copilot"))
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
    models = [m for m in models if m.model_type == "chat"]

    if models:
        click.echo("  Available models:")
        for i, m in enumerate(models, 1):
            ctx = f"{m.context_window // 1000}k" if m.context_window else "?"
            click.echo(f"    {i}. {m.id} ({ctx} context)")

        default_idx = 1
        for i, m in enumerate(models, 1):
            if "sonnet" in m.id.lower() and "4" in m.id:
                default_idx = i
                break

        choice = click.prompt("  Select primary model", type=click.IntRange(1, len(models)), default=default_idx)
        primary_model = models[choice - 1].id
        click.echo(f"  Selected: {primary_model}")
    else:
        primary_model = click.prompt("  Enter model name", default="copilot/claude-sonnet-4.5")

    # Step 3: Agent name
    click.echo()
    click.echo("Step 3: Agent Identity")
    click.echo("-" * 30)
    agent_name = click.prompt("  Agent name", default="Claw")

    # Step 4: Write config
    click.echo()
    click.echo("Step 4: Save Configuration")
    click.echo("-" * 30)

    from xdog.claw.config import ClawConfig, GroupDef, save_config
    config = ClawConfig(
        model=primary_model,
        groups=(GroupDef(id="main", name=agent_name, is_main=True),),
    )

    config_file.parent.mkdir(parents=True, exist_ok=True)
    save_config(config, config_file)
    click.echo(f"  Config saved: {config_file}")

    # Step 5: Initialize workspace
    click.echo()
    click.echo("Step 5: Initialize Workspace")
    click.echo("-" * 30)

    from xdog.claw.core.prompt import init_workspace, set_identity_name, workspace_path
    data_dir = Path(config.data_dir)
    ws = workspace_path(data_dir / "groups" / "main")
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
