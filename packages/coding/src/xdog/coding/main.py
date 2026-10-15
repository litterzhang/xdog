"""Main entry point for the coding agent CLI."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from xdog.ai.types import ImageContent


def main() -> None:
    """CLI entry point (referenced by pyproject.toml ``[project.scripts]``)."""
    from xdog.coding.cli.args import parse_args
    parse_args()


def run_agent(
    *,
    overrides: dict[str, Any] | None = None,
    resume: bool = False,
    resume_id: str | None = None,
    prompt: str | None = None,
    print_mode: bool = False,
    output_format: str = "text",
    working_dir: Path | None = None,
    config_path: Path | None = None,
    rpc: bool = False,
    acp: bool = False,
    session_id: str | None = None,
    enqueue: bool = False,
    message_id: str | None = None,
    verbose: bool = False,
    files: tuple[Path, ...] = (),
) -> None:
    """Bootstrap and run the coding agent.

    Called by :func:`coding.cli.args.cli` after argument parsing.
    """
    if enqueue:
        if (not session_id or not message_id or not prompt or any((acp, rpc, resume, resume_id, print_mode,
                                                                 working_dir, config_path, overrides, files))):
            raise ValueError("Enqueue requires exact session/message/instruction without runtime overrides")
        import json

        from xdog.coding.cli.enqueue import enqueue as persist
        try:
            print(json.dumps(persist(session_id, message_id, prompt), ensure_ascii=True, allow_nan=False))
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        return
    if message_id is not None:
        raise ValueError("message_id requires enqueue")
    if acp or session_id is not None:
        if sys.version_info >= (3, 15):
            raise RuntimeError("ACP currently requires Python 3.12–3.14")
        if acp:
            from xdog.coding.acp.cli import run_acp
            asyncio.run(run_acp(session_id))
        else:
            from xdog.coding.acp.client import send_prompt
            from xdog.coding.modes.print_mode import PrintModeRenderer
            if not prompt:
                raise ValueError("--session requires a prompt")
            assert session_id is not None
            outcome = asyncio.run(send_prompt(session_id, prompt))
            if outcome.response is not None:
                PrintModeRenderer(output_format=output_format).render_message(outcome.response)
        return

    from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session

    try:
        result = create_agent_session(CreateSessionOptions(
            working_dir=working_dir,
            overrides=overrides,
            resume=resume,
            resume_id=resume_id,
            verbose=verbose,
            config_path=config_path,
        ))
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    session = result.session

    if result.model_fallback_message:
        print(result.model_fallback_message, file=sys.stderr)

    # Load extensions
    _load_extensions(session, verbose)

    if rpc:
        # stdin is the bidirectional JSON-lines control stream, not a one-shot
        # piped user prompt. Its reader must be ready before a client sends input.
        _run_rpc(session)
        return

    # Build initial message from CLI args + piped stdin + files
    initial_images: list[ImageContent] = []
    try:
        initial_message = _build_initial_message(prompt, files, images=initial_images)
    except (OSError, ValueError) as exc:
        print(f"Error loading initial input: {exc}", file=sys.stderr)
        sys.exit(1)

    # Route to the appropriate mode
    if print_mode and (initial_message or initial_images):
        _run_print(session, initial_message or "", output_format, verbose, images=initial_images)
    elif initial_message or initial_images:
        # Non-interactive with prompt
        _run_print(session, initial_message or "", "text", verbose, images=initial_images)
    else:
        _run_interactive(session, verbose)


def _build_initial_message(
    prompt: str | None,
    files: tuple[Path, ...], *, images: list[ImageContent] | None = None,
) -> str | None:
    """Build initial message from CLI arguments."""
    from xdog.coding.cli.initial_message import build_initial_message
    return build_initial_message(
        prompt=prompt,
        files=files,
        read_stdin=True, images=images,
    )


def _load_extensions(session: Any, verbose: bool) -> None:
    """Discover and load configured extensions."""
    try:
        from xdog.coding.core.extensions.loader import discover_extensions

        extensions = discover_extensions()
        if verbose and extensions:
            print(f"Loaded {len(extensions)} extension(s):", file=sys.stderr)
            for ext in extensions:
                print(f"  - {ext.name} v{ext.manifest.version}", file=sys.stderr)
    except Exception as exc:
        if verbose:
            print(f"Warning: failed to load extensions: {exc}", file=sys.stderr)


def _run_print(
    session: Any,
    message: str,
    output_format: str,
    verbose: bool,
    *, images: list[ImageContent] | None = None,
) -> None:
    """Run in non-interactive print mode."""
    from xdog.coding.modes.print_mode import run_print_mode

    async def owned_print() -> int:
        try:
            return await run_print_mode(session, message, output_format=output_format,
                                        verbose=verbose, images=tuple(images or ()))
        finally:
            await session.children.supervisor.aclose()
            session.dispose()
    exit_code = asyncio.run(owned_print())
    sys.exit(exit_code)


def _run_rpc(session: Any) -> None:
    """Run in headless RPC mode."""
    from xdog.coding.modes.rpc.rpc_mode import run_rpc_mode

    async def owned_rpc() -> int:
        try:
            return await run_rpc_mode(session)
        finally:
            try:
                await session.children.supervisor.aclose()
            finally:
                try:
                    await session.extensions.aclose()
                finally:
                    session.dispose()
    exit_code = asyncio.run(owned_rpc())
    sys.exit(exit_code)


def _run_interactive(session: Any, verbose: bool) -> None:
    """Run the interactive TUI mode."""
    try:
        from xdog.coding.modes.interactive.interactive_mode import run_interactive_mode
        run_interactive_mode(session, verbose=verbose)
    except ImportError:
        # Fallback to simple REPL if TUI is not available
        _run_simple_repl(session, verbose)


def _run_simple_repl(session: Any, verbose: bool) -> None:
    """Simple REPL fallback when the TUI is not available."""
    from xdog.coding.modes.print_mode import _terminal_permission_handler

    session.permissions.set_request_handler(_terminal_permission_handler(session))
    model_name = session.model or "unknown"
    thinking = session.agent.options.thinking or "off"

    print("Pi Coding Agent (interactive mode)")
    print(f"Model: {model_name} | Thinking: {thinking}")
    print(f"Working directory: {session.working_dir}")
    print("Type 'exit' or Ctrl+D to quit.\n")

    try:
        while True:
            try:
                user_input = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
                print("Goodbye.")
                break

            # Handle slash commands
            if user_input.startswith("/"):
                from xdog.coding.core.slash_commands import execute_command, parse_slash_command
                parsed = parse_slash_command(user_input)
                if parsed:
                    cmd, args = parsed
                    result = asyncio.run(execute_command(cmd, args, session))
                    if result.output:
                        print(f"\n{result.output}\n")
                    if result.exit_requested:
                        break
                    if result.prompt:
                        # A skill command: fall through to a normal turn carrying
                        # the skill's instructions instead of returning to the
                        # prompt, which would leave the model unaware of them.
                        user_input = result.prompt
                    else:
                        continue
                else:
                    continue

            # Send message
            try:
                response = asyncio.run(session.send_message(user_input))
                if response:
                    from xdog.ai.types import TextContent
                    text_parts = [
                        p.text for p in response.content
                        if isinstance(p, TextContent)
                    ]
                    print(f"\n{''.join(text_parts)}\n")
                else:
                    print("\n(no response)\n")
            except KeyboardInterrupt:
                print("\nCancelled.")
                session.abort()
            except Exception as exc:
                print(f"\nError: {exc}\n", file=sys.stderr)

        session.permissions.set_request_handler(None)
        session.permissions.deny_all()
    finally:
        session.permissions.set_request_handler(None)
        session.cancel()
        asyncio.run(session.children.supervisor.aclose())
        session.dispose()
