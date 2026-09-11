"""CLI argument parsing via click."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
from xdog.coding.cli.list_models import list_models_command
from xdog.coding.cli.session_picker import pick_session_command


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "-m", "--model",
    default=None,
    help="Model to use (e.g. sonnet, opus, haiku, or a full model id).",
)
@click.option(
    "-r", "--resume",
    is_flag=True,
    default=False,
    help="Select a session from the current working directory.",
)
@click.option(
    "--resume-id",
    default=None,
    help="Resume a specific session by ID.",
)
@click.option(
    "-p", "--prompt",
    default=None,
    help="Initial prompt to send (non-interactive).",
)
@click.option(
    "--print",
    "print_mode",
    is_flag=True,
    default=False,
    help="Run in non-interactive print mode.",
)
@click.option(
    "--output-format",
    type=click.Choice(["text", "json", "markdown"]),
    default="text",
    help="Output format for print mode.",
)
@click.option(
    "--working-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Working directory for the agent.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a config file.",
)
@click.option(
    "--thinking-level",
    type=click.Choice(["off", "minimal", "low", "medium", "high", "xhigh"]),
    default=None,
    help="Thinking / reasoning level.",
)
@click.option(
    "--permission-mode",
    type=click.Choice(["ask", "ask-all", "allow-all", "deny"]),
    default=None,
    help="Tool permission policy.",
)
@click.option(
    "--list-models",
    is_flag=True,
    default=False,
    help="List available models and exit.",
)
@click.option(
    "--pick-session",
    is_flag=True,
    default=False,
    help="Interactively pick a session to resume.",
)
@click.option(
    "--rpc",
    is_flag=True,
    default=False,
    help="Run in RPC mode for IDE integration.",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable verbose logging.",
)
@click.argument("files", nargs=-1, type=click.Path(exists=True, path_type=Path))
def cli(
    model: str | None,
    resume: bool,
    resume_id: str | None,
    prompt: str | None,
    print_mode: bool,
    output_format: str,
    working_dir: Path | None,
    config_path: Path | None,
    thinking_level: str | None,
    permission_mode: str | None,
    list_models: bool,
    pick_session: bool,
    rpc: bool,
    verbose: bool,
    files: tuple[Path, ...],
) -> None:
    """pi - Interactive coding agent CLI.

    Optionally pass FILES to include their contents in the initial context.
    """
    # Early-exit commands
    if list_models:
        list_models_command()
        return

    if (resume or pick_session) and resume_id:
        raise click.UsageError("--resume/--pick-session cannot be combined with --resume-id.")
    if resume or pick_session:
        if rpc or print_mode:
            raise click.UsageError("Use --resume-id with --rpc or --print.")
        resume_id = pick_session_command(working_dir=working_dir)
        if resume_id is None:
            return
        resume = False

    # Build overrides dict from CLI flags
    overrides: dict[str, Any] = {}
    if model is not None:
        overrides["model"] = model
    if thinking_level is not None:
        overrides["thinking_level"] = thinking_level
    if permission_mode is not None:
        overrides["permission_mode"] = permission_mode

    # Defer to the main entry-point for actual execution
    from xdog.coding.main import run_agent

    run_agent(
        overrides=overrides,
        resume=resume,
        resume_id=resume_id,
        prompt=prompt,
        print_mode=print_mode,
        output_format=output_format,
        working_dir=working_dir,
        config_path=config_path,
        rpc=rpc,
        verbose=verbose,
        files=files,
    )


def parse_args() -> None:
    """Parse CLI arguments and run the application."""
    cli(standalone_mode=True)
