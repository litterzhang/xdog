"""CLI entry point for xdog-agent.

Usage::

    xdog-agent login                   Login to provider (GitHub Copilot OAuth)
    xdog-agent chat [model] [msg]      Chat with an agent (interactive or one-shot)

Interactive slash commands::

    /model <name>    Switch model
    /thinking <lvl>  Set thinking level (off, minimal, low, medium, high, xhigh)
    /image <path>    Attach an image to the next message
    /tools           List available tools
    /clear           Clear conversation history
    /verbose         Toggle verbose output
    /help            Show help
    /exit            Exit
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from xdog.agent.agent import Agent
from xdog.agent.core import AgentConfig, AgentToolResult, CustomAgentMessage
from xdog.agent.events import (
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
)
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    StreamOptions,
    TextContent,
)

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_DIM = "\033[2m"
_RESET = "\033[0m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_GREEN = "\033[32m"


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _cyan(text: str) -> str:
    return f"{_CYAN}{text}{_RESET}"


def _yellow(text: str) -> str:
    return f"{_YELLOW}{text}{_RESET}"


def _red(text: str) -> str:
    return f"{_RED}{text}{_RESET}"


def _green(text: str) -> str:
    return f"{_GREEN}{text}{_RESET}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def _cmd_login() -> None:
    """Login to the AI provider."""
    import xdog.ai as ai
    await ai.login()
    print("Login successful.", file=sys.stderr)


def _parse_tool_ctx(raw: str | None) -> dict[str, Any]:
    """Parse the --tool-ctx argument into a dict.

    Accepts a JSON object, or ``@path`` to read the JSON from a file.
    Returns ``{}`` when *raw* is ``None``.  Exits with an error if the
    value is not valid JSON or does not decode to an object.
    """
    if raw is None:
        return {}
    text = raw
    if raw.startswith("@"):
        path = Path(raw[1:])
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Error reading --tool-ctx file {path}: {exc}", file=sys.stderr)
            sys.exit(1)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"Error: --tool-ctx is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(parsed, dict):
        print(f"Error: --tool-ctx must be a JSON object, got {type(parsed).__name__}", file=sys.stderr)
        sys.exit(1)
    return parsed


async def _cmd_chat(
    *,
    model_id: str | None,
    message: str | None,
    system_prompt: str | None,
    temperature: float | None,
    max_tokens: int | None,
    thinking: str | None,
    no_tools: bool,
    verbose: bool,
    tool_ctx: str | None = None,
) -> None:
    """Run an interactive agent chat session."""
    import xdog.ai as ai
    from xdog.agent.helpers import stream_fn_from_provider, web_search_fn_from_provider
    from xdog.agent.tools.registry import get_registered_tools

    parsed_tool_ctx = _parse_tool_ctx(tool_ctx)

    # Load runtime from auth.json
    runtime = ai.load()
    if not runtime.active_providers():
        print("No active providers. Run 'xdog-agent login' first.", file=sys.stderr)
        sys.exit(1)

    # Resolve model
    default_model = "claude-sonnet-4.5"
    model_name = model_id or default_model

    # Build agent
    stream_fn = stream_fn_from_provider(runtime)
    search_fn = web_search_fn_from_provider(runtime, model_name)
    opts = StreamOptions(
        thinking=thinking,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
    )
    config = AgentConfig(
        model=model_name,
        system_prompt=system_prompt or "You are a helpful assistant. Use the available tools when appropriate.",
        options=opts,
    )

    builtin_tools = get_registered_tools() if not no_tools else []

    agent = Agent(
        stream_fn,
        config=config,
        web_search_fn=search_fn,
        tools=builtin_tools,
        tool_ctx=parsed_tool_ctx,
    )

    # Session state for slash commands
    session = _Session(agent=agent, runtime=runtime, verbose=verbose)

    # One-shot or interactive
    messages: list[str] = []
    if message is not None:
        if not sys.stdin.isatty():
            stdin_text = sys.stdin.read().strip()
            messages.append(f"{message}\n\n{stdin_text}" if stdin_text else message)
        else:
            messages.append(message)
    elif not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()
        if not stdin_text:
            print("No input provided.", file=sys.stderr)
            sys.exit(1)
        messages.append(stdin_text)

    interactive = len(messages) == 0

    if interactive:
        tool_count = len(agent.state.tools)
        tool_info = f" with {tool_count} tools" if tool_count else ""
        print(
            f"Chatting with {_cyan(model_name)}{tool_info} "
            f"(type /help for commands, /exit to quit)\n",
            file=sys.stderr,
        )

    while True:
        if interactive:
            try:
                user_input = _read_multiline_input()
            except (EOFError, KeyboardInterrupt):
                print("\n", file=sys.stderr)
                break
            if not user_input.strip():
                continue

            # Handle slash commands
            handled = await session.handle_slash_command(user_input.strip())
            if handled == "exit":
                break
            if handled == "handled":
                continue
        else:
            if not messages:
                break
            user_input = messages.pop(0)

        try:
            # Build prompt with pending images
            if session.pending_images:
                content_parts: list[TextContent | ImageContent] = [TextContent(text=user_input)]
                content_parts.extend(session.pending_images)
                session.pending_images.clear()
                stream = await agent.prompt(
                    __import__("xdog.ai.types", fromlist=["UserMessage"]).UserMessage(
                        content=tuple(content_parts)
                    )
                )
            else:
                stream = await agent.prompt(user_input)

            await _consume_agent_stream(stream, session=session)
            if parsed_tool_ctx:
                _print_tool_ctx(parsed_tool_ctx, verbose=verbose)
        except KeyboardInterrupt:
            print(f"\n{_dim('[interrupted]')}", file=sys.stderr)
            agent.abort()
            agent.reset_abort()
            if not interactive:
                sys.exit(130)
        except Exception as exc:
            print(f"\n{_red(f'Error: {exc}')}", file=sys.stderr)
            if not interactive:
                sys.exit(1)


# ---------------------------------------------------------------------------
# Session state for slash commands
# ---------------------------------------------------------------------------


class _Session:
    """Mutable session state for interactive slash commands."""

    def __init__(self, agent: Agent, runtime: Any, verbose: bool) -> None:
        self.agent = agent
        self.runtime = runtime
        self.verbose = verbose
        self.pending_images: list[ImageContent] = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.turn_count = 0

    async def handle_slash_command(self, text: str) -> str:
        """Handle a slash command. Returns 'exit', 'handled', or 'not_command'."""
        if not text.startswith("/") and text.lower() not in ("exit", "quit"):
            return "not_command"

        cmd = text.lower()

        if cmd in ("/exit", "/quit", "exit", "quit"):
            return "exit"

        if cmd == "/help":
            self._print_help()
            return "handled"

        if cmd == "/tools":
            self._print_tools()
            return "handled"

        if cmd == "/clear":
            self.agent.reset()
            print(_dim("[conversation cleared]"), file=sys.stderr)
            return "handled"

        if cmd == "/verbose":
            self.verbose = not self.verbose
            print(_dim(f"[verbose {'on' if self.verbose else 'off'}]"), file=sys.stderr)
            return "handled"

        if cmd == "/status":
            self._print_status()
            return "handled"

        if cmd.startswith("/model"):
            parts = text.split(None, 1)
            if len(parts) < 2:
                print(f"  Current model: {_cyan(self.agent.state.model)}", file=sys.stderr)
                models = self.runtime.models()
                if models:
                    print(f"  Available ({len(models)}):", file=sys.stderr)
                    for m in sorted(models, key=lambda x: x.id):
                        short = m.id.split("/", 1)[-1] if "/" in m.id else m.id
                        print(f"    {short}", file=sys.stderr)
            else:
                new_model = parts[1].strip()
                self.agent.set_model(new_model)
                print(_dim(f"[model → {new_model}]"), file=sys.stderr)
            return "handled"

        if cmd.startswith("/thinking"):
            parts = text.split(None, 1)
            if len(parts) < 2:
                current = self.agent.options.thinking or "off"
                print(f"  Current thinking: {_cyan(current)}", file=sys.stderr)
                print("  Levels: off, minimal, low, medium, high, xhigh", file=sys.stderr)
            else:
                level = parts[1].strip().lower()
                if level == "off":
                    self.agent.set_options(replace(self.agent.options, thinking=None))
                else:
                    self.agent.set_options(replace(self.agent.options, thinking=level))  # type: ignore[arg-type]
                print(_dim(f"[thinking → {level}]"), file=sys.stderr)
            return "handled"

        if cmd.startswith("/image"):
            parts = text.split(None, 1)
            if len(parts) < 2:
                print("  Usage: /image <path>", file=sys.stderr)
            else:
                img_path = Path(parts[1].strip())
                if not img_path.exists():
                    print(f"  {_red('File not found')}: {img_path}", file=sys.stderr)
                else:
                    mime, _ = mimetypes.guess_type(str(img_path))
                    if not mime or not mime.startswith("image/"):
                        print(f"  {_red('Not an image')}: {img_path}", file=sys.stderr)
                    else:
                        data = base64.b64encode(img_path.read_bytes()).decode("ascii")
                        self.pending_images.append(ImageContent(data=data, mime_type=mime))
                        print(_dim(f"[image attached: {img_path.name}]"), file=sys.stderr)
            return "handled"

        print(f"  Unknown command: {text}. Type /help for available commands.", file=sys.stderr)
        return "handled"

    def _print_help(self) -> None:
        print("  /model [name]      Show or switch model", file=sys.stderr)
        print("  /thinking [level]  Show or set thinking (off/minimal/low/medium/high/xhigh)", file=sys.stderr)
        print("  /image <path>      Attach image to next message", file=sys.stderr)
        print("  /tools             List available tools", file=sys.stderr)
        print("  /status            Show session status", file=sys.stderr)
        print("  /clear             Clear conversation history", file=sys.stderr)
        print("  /verbose           Toggle verbose output", file=sys.stderr)
        print("  /exit              Exit", file=sys.stderr)

    def _print_status(self) -> None:
        cfg = self.agent._config
        opts = cfg.options
        msg_count = len(self.agent.state.messages)
        tool_count = len(self.agent.state.tools)

        # Estimate token usage from context
        from xdog.ai.types import Context
        from xdog.ai.utils.overflow import estimate_context_tokens
        ctx = Context(
            system_prompt=self.agent.state.system_prompt,
            # `Context` carries only the wire message kinds; CustomAgentMessage
            # is agent-side bookkeeping with nothing to send.
            messages=tuple(
                m for m in self.agent.state.messages
                if not isinstance(m, CustomAgentMessage)
            ),
        )
        est_tokens = estimate_context_tokens(ctx)
        ctx_window = cfg.context_window
        pct = (est_tokens / ctx_window * 100) if ctx_window > 0 else 0

        print(file=sys.stderr)
        print(f"  Model:          {_cyan(self.agent.state.model)}", file=sys.stderr)
        print(f"  Thinking:       {opts.thinking or 'off'}", file=sys.stderr)
        print(f"  Temperature:    {opts.temperature or 'default'}", file=sys.stderr)
        print(f"  Max tokens:     {opts.max_tokens or 'default'}", file=sys.stderr)
        print(f"  Context window: {ctx_window:,}", file=sys.stderr)
        print(f"  Messages:       {msg_count}", file=sys.stderr)
        print(f"  Est. tokens:    ~{est_tokens:,} / {ctx_window:,} ({pct:.1f}%)", file=sys.stderr)
        print(f"  Tools:          {tool_count}", file=sys.stderr)
        print(f"  Turns:          {self.turn_count}", file=sys.stderr)
        print(f"  Total tokens:   {self.total_input_tokens:,} in / {self.total_output_tokens:,} out", file=sys.stderr)
        cost_str = f"{self.total_cost}x" if self.total_cost > 0 else "free"
        print(f"  Total cost:     {cost_str}", file=sys.stderr)
        print(f"  Verbose:        {'on' if self.verbose else 'off'}", file=sys.stderr)
        print(file=sys.stderr)

    def _print_tools(self) -> None:
        tools = self.agent.state.tools
        print(f"\n  Tools ({len(tools)}):", file=sys.stderr)
        for tool in tools:
            print(f"    {_cyan(tool.name):<25} {tool.description[:60]}", file=sys.stderr)
        print(file=sys.stderr)


# ---------------------------------------------------------------------------
# Stream consumer
# ---------------------------------------------------------------------------


async def _consume_agent_stream(stream: Any, *, session: _Session) -> None:
    """Consume an AgentEventStream, printing text and tool activity."""
    response_parts: list[str] = []
    in_thinking = False
    verbose = session.verbose

    async for event in stream:
        if isinstance(event, MessageUpdateEvent):
            ame = event.assistant_message_event
            if ame is None:
                continue

            if ame.type == "text_delta":
                delta = getattr(ame, "delta", "")
                print(delta, end="", flush=True)
                response_parts.append(delta)

            elif ame.type == "thinking_start" and verbose:
                in_thinking = True
                print(f"{_DIM}[thinking] ", end="", flush=True)

            elif ame.type == "thinking_delta" and verbose and in_thinking:
                delta = getattr(ame, "delta", "")
                print(delta, end="", flush=True)

            elif ame.type == "thinking_done" and verbose and in_thinking:
                in_thinking = False
                print(_RESET, end="", flush=True)
                print()

        elif isinstance(event, ToolExecutionStartEvent):
            args_str = json.dumps(event.args, ensure_ascii=False) if event.args else ""
            print(
                f"\n{_dim('[')}calling {_yellow(event.tool_name)}"
                f"{_dim('(' + args_str[:100] + ')')}{_dim(']')}",
                file=sys.stderr, flush=True,
            )

        elif isinstance(event, ToolExecutionEndEvent):
            preview = _extract_result_text(event.result)[:200]
            if event.is_error:
                print(f"{_dim('[')}result {_red('error')}: {_dim(preview)}{_dim(']')}", file=sys.stderr, flush=True)
            else:
                print(f"{_dim('[')}result {_green('ok')}: {_dim(preview)}{_dim(']')}", file=sys.stderr, flush=True)

        elif isinstance(event, TurnEndEvent):
            msg = event.message
            if isinstance(msg, AssistantMessage) and msg.usage:
                session.total_input_tokens += msg.usage.input
                session.total_output_tokens += msg.usage.output
                session.total_cost += msg.usage.cost.total
                session.turn_count += 1
                if verbose:
                    cost = msg.usage.cost.total
                    cost_str = f" cost={cost}x" if cost > 0 else " cost=free"
                    print(
                        _dim(f"[in={msg.usage.input} out={msg.usage.output}{cost_str} stop={msg.stop_reason}]"),
                        file=sys.stderr,
                    )

    if response_parts and not response_parts[-1].endswith("\n"):
        print()


def _extract_result_text(result: AgentToolResult | None) -> str:
    """Extract a plain-text preview from an AgentToolResult."""
    if result is None:
        return "(no result)"
    parts: list[str] = []
    for part in result.content:
        if isinstance(part, TextContent):
            parts.append(part.text)
    return " ".join(parts) if parts else "(no text)"


def _print_tool_ctx(tool_ctx: dict[str, Any], *, verbose: bool) -> None:
    """Print the shared tool context after a turn.

    Tools mutate values inside ``tool_ctx`` (e.g. a ``flow_result_sink`` dict
    populated by ``submit_result``).  Since ToolDef passes handlers a shallow
    copy of the context, only mutations to shared *values* are visible here —
    which is exactly the sink-handoff pattern.
    """
    try:
        rendered = json.dumps(tool_ctx, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        rendered = repr(tool_ctx)
    print(f"\n{_dim('[tool-ctx]')} {rendered}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------


def _read_multiline_input() -> str:
    """Read user input, supporting multi-line with trailing backslash."""
    lines: list[str] = []
    prompt = ">>> "
    while True:
        line = input(prompt)
        if line.endswith("\\"):
            lines.append(line[:-1])
            prompt = "... "
        else:
            lines.append(line)
            break
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the ``xdog-agent`` console script."""
    parser = argparse.ArgumentParser(
        prog="xdog-agent",
        description="xdog-agent: Interactive agent with tool calling.",
    )
    sub = parser.add_subparsers(dest="command")

    # -- login ---------------------------------------------------------------
    sub.add_parser("login", help="Login to AI provider (GitHub Copilot OAuth)")

    acp_p = sub.add_parser("acp", help="Run standard stdio Agent Client Protocol")
    acp_p.add_argument("--model", default=None)

    # -- chat ----------------------------------------------------------------
    chat_p = sub.add_parser("chat", help="Chat with an agent")
    chat_p.add_argument("model", nargs="?", help="Model name (e.g. claude-sonnet-4.5)")
    chat_p.add_argument("message", nargs="?", help="Message (interactive if omitted)")
    chat_p.add_argument("-s", "--system", help="System prompt")
    chat_p.add_argument("-t", "--temperature", type=float, help="Sampling temperature")
    chat_p.add_argument("--max-tokens", type=int, help="Maximum output tokens")
    chat_p.add_argument(
        "--thinking", choices=["minimal", "low", "medium", "high", "xhigh"],
        help="Thinking level",
    )
    chat_p.add_argument("--no-tools", action="store_true", help="Disable built-in tools")
    chat_p.add_argument("--verbose", action="store_true", help="Show thinking, usage, and cost")
    chat_p.add_argument(
        "--tool-ctx",
        metavar="JSON",
        help=(
            "Shared tool context passed to every tool's execute(ctx=...). "
            "A JSON object, or '@path' to read the JSON from a file. "
            "Example: --tool-ctx '{\"flow_output_schema\": {\"summary\": \"string\"}}'"
        ),
    )

    args = parser.parse_args()

    if args.command == "login":
        asyncio.run(_cmd_login())
    elif args.command == "acp":
        if sys.version_info >= (3, 15):
            parser.error("ACP currently requires Python 3.12–3.14")
        from xdog.agent.acp.cli import run
        asyncio.run(run(args.model))
    elif args.command == "chat":
        asyncio.run(_cmd_chat(
            model_id=args.model,
            message=args.message,
            system_prompt=args.system,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            thinking=args.thinking,
            no_tools=args.no_tools,
            verbose=args.verbose,
            tool_ctx=args.tool_ctx,
        ))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
