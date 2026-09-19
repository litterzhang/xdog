"""CLI entry point for xdog-ai.

Usage::

    xdog-ai login [provider]              Login to a provider
    xdog-ai providers                     List available providers
    xdog-ai models <provider> [--sync]    List models for a provider
    xdog-ai chat <provider> <model> [msg] Chat with a model
    xdog-ai embed <provider> <model> <text>  Generate embeddings
    xdog-ai search <provider> <model> <query>  Web search
    xdog-ai image <provider> <model> <prompt>  Generate image files
    xdog-ai proxy [--port PORT]           Start Messages / token count / OpenAI proxy
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import sys
from pathlib import Path
from typing import Any

import httpx
import xdog.ai as ai
from xdog.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    EmbeddingRequest,
    ErrorEvent,
    ImageContent,
    ImageGenerationRequest,
    Model,
    ProviderType,
    StatusEvent,
    StreamOptions,
    TextContent,
    UserMessage,
)


def main() -> None:
    """Entry point for the ``xdog-ai`` console script."""
    parser = argparse.ArgumentParser(prog="xdog-ai", description="xdog-ai CLI")
    sub = parser.add_subparsers(dest="command")

    # --- login ---
    login_p = sub.add_parser("login", help="Login to a provider")
    login_p.add_argument("provider", nargs="?", help="Provider ID (e.g. copilot)")

    # --- providers ---
    sub.add_parser("providers", help="List available providers")

    # --- models ---
    models_p = sub.add_parser("models", help="List models for a provider")
    models_p.add_argument("provider", help="Provider ID (e.g. copilot)")
    models_p.add_argument("--sync", action="store_true", help="Fetch latest from API")
    models_p.add_argument("--json", action="store_true", dest="output_json",
                          help="Include input/output modalities, protocols, and transport endpoints as JSON")
    models_p.add_argument("--details", action="store_true",
                          help="Show model names, input limits, protocols, and transport endpoints")

    # --- chat ---
    chat_p = sub.add_parser("chat", help="Chat with a model")
    chat_p.add_argument("provider", help="Provider ID (e.g. copilot)")
    chat_p.add_argument("model", help="Model name (e.g. claude-sonnet-4.5)")
    chat_p.add_argument("message", nargs="?", help="Message (interactive if omitted)")
    chat_p.add_argument("-s", "--system", help="System prompt")
    chat_p.add_argument("-t", "--temperature", type=float, help="Sampling temperature")
    chat_p.add_argument("--max-tokens", type=int, help="Maximum output tokens")
    chat_p.add_argument("--thinking", choices=["minimal", "low", "medium", "high", "xhigh"],
                        help="Thinking / reasoning level")
    chat_p.add_argument("-i", "--image", action="append", default=[], help="Image file path")
    chat_p.add_argument("--no-stream", action="store_true", help="Disable streaming")
    chat_p.add_argument("--web-search", action="store_true", help="Enable web search")
    chat_p.add_argument("--verbose", action="store_true", help="Show thinking and usage stats")

    # --- embed ---
    embed_p = sub.add_parser("embed", help="Generate embeddings")
    embed_p.add_argument("provider", help="Provider ID")
    embed_p.add_argument("model", help="Embedding model name")
    embed_p.add_argument("text", nargs="?", help="Text to embed (reads stdin if omitted)")
    embed_p.add_argument("-d", "--dimensions", type=int, help="Number of dimensions")
    embed_p.add_argument("--json", action="store_true", dest="output_json", help="Output JSON")

    # --- search ---
    search_p = sub.add_parser("search", help="Web search via a model")
    search_p.add_argument("provider", help="Provider ID")
    search_p.add_argument("model", help="Model name")
    search_p.add_argument("query", help="Search query")

    # --- image generation ---
    image_p = sub.add_parser("image", aliases=["image_generation"], help="Generate image files via a provider")
    image_p.add_argument("provider", help="Provider ID (e.g. antigravity)")
    image_p.add_argument("model", help="Image-generation model ID")
    image_p.add_argument("prompt", help="Image prompt or editing instructions")
    image_p.add_argument("--output-dir", type=Path, default=Path("generated-images"),
                         help="Output directory (default: generated-images; existing files are never overwritten)")
    image_p.add_argument("--reference", type=Path, action="append", default=[],
                         help="Local reference image; repeat up to four times")
    image_p.add_argument("--aspect-ratio", help="Aspect ratio, e.g. 9:16 (default: provider setting)")
    image_p.add_argument("--image-size", choices=["1K", "2K", "4K"], help="Resolution (default: provider setting)")
    image_p.add_argument("--json", action="store_true", dest="output_json",
                         help="Print saved paths and metadata as JSON")

    # --- proxy ---
    proxy_p = sub.add_parser("proxy", help="Start Messages / token count / OpenAI proxy server")
    proxy_p.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    proxy_p.add_argument("--port", type=int, default=8082, help="Port (default: 8082)")
    proxy_p.add_argument("--api-key", default="", help="API key for authentication (default: none)")

    args = parser.parse_args()

    if args.command == "providers":
        _cmd_providers()
    elif args.command == "login":
        try:
            asyncio.run(_cmd_login(args.provider))
        except httpx.HTTPError:
            parser.exit(1, "Login failed: network request failed; check your connection and retry.\n")
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            parser.exit(1, f"Login failed: {exc}\n")
    elif args.command == "models":
        asyncio.run(_cmd_models(
            args.provider, sync=args.sync, output_json=args.output_json, details=args.details,
        ))
    elif args.command == "chat":
        asyncio.run(_cmd_chat(
            provider=args.provider, model=args.model, message=args.message,
            system_prompt=args.system, temperature=args.temperature,
            max_tokens=args.max_tokens, thinking=args.thinking,
            image_paths=args.image, no_stream=args.no_stream,
            web_search=args.web_search, verbose=args.verbose,
        ))
    elif args.command == "embed":
        asyncio.run(_cmd_embed(
            provider=args.provider, model=args.model,
            text=args.text, dimensions=args.dimensions,
            output_json=args.output_json,
        ))
    elif args.command == "search":
        asyncio.run(_cmd_search(
            provider=args.provider, model=args.model, query=args.query,
        ))
    elif args.command in ("image", "image_generation"):
        asyncio.run(_cmd_image(
            provider=args.provider, model=args.model, prompt=args.prompt,
            output_dir=args.output_dir, references=args.reference,
            aspect_ratio=args.aspect_ratio, image_size=args.image_size, output_json=args.output_json,
        ))
    elif args.command == "proxy":
        from xdog.ai.proxy import run_proxy
        run_proxy(host=args.host, port=args.port, api_key=args.api_key)
    else:
        parser.print_help()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_providers() -> None:
    """List available providers."""
    rt = ai.load()
    for pid in rt.active_providers():
        p = rt.provider(pid)
        print(f"  {pid:<20} {p.name}")

    if not rt.active_providers():
        print("No active providers. Run 'xdog-ai login copilot' first.", file=sys.stderr)


async def _cmd_login(provider_id: str | None) -> None:
    """Login to a provider."""
    pid = provider_id or ProviderType.COPILOT
    p = ai.provider(pid)
    await p.login()
    print(f"Logged in to {p.name}.", file=sys.stderr)


def _token_limit(value: int) -> str:
    return f"{value:,}" if value else "?"


def _compact_token_limit(value: int) -> str:
    if not value:
        return "?"
    return f"{value // 1000}k" if value >= 1000 else str(value)


def _generation_protocols(model: Model) -> str:
    if model.supported_generation_protocols is not None:
        return ", ".join(model.supported_generation_protocols) or "none"
    return model.api or "?"


async def _cmd_models(
    provider_id: str, *, sync: bool, output_json: bool = False, details: bool = False,
) -> None:
    """List models for a provider."""
    p = ai.provider(provider_id)

    if sync:
        print("Syncing models...", file=sys.stderr)
        await p.sync_models(force=True)

    provider_models = p.models()
    if not provider_models:
        print(f"No models for {provider_id}. Try 'xdog-ai models {provider_id} --sync'.", file=sys.stderr)
        sys.exit(1)

    if output_json:
        from dataclasses import asdict
        print(json.dumps([
            {
                **asdict(model),
                "supports_image_input": model.supports_image_input,
                "supports_image_output": model.supports_image_output,
            }
            for model in sorted(provider_models, key=lambda model: model.id)
        ], indent=2))
        return

    print(f"\n  {provider_id} ({len(provider_models)} models)")
    print(f"  {'─' * 70}")
    models = sorted(provider_models, key=lambda model: model.id)
    for model in models:
        short = model.id.removeprefix(f"{provider_id}/")
        prompt = _compact_token_limit(model.max_prompt_tokens or model.context_window)
        out = _compact_token_limit(model.max_tokens)
        # Embeddings still use a wire protocol even though they advertise no
        # generation operations. Show the selected adapter, not that operation filter.
        protocol = model.preferred_protocol or model.api or "?"
        tags = []
        if model.reasoning:
            tags.append("reasoning")
        if model.model_type == "embeddings":
            tags.append("embedding")
        if model.supports_image_output is True:
            tags.append("image_generation")
        if model.supports_web_search is True:
            tags.append("web_search")
        suffix = " " + " ".join(tags) if tags else ""
        if model.provider == "copilot":
            suffix += "  " + (f"{model.cost.input:g}x" if model.cost.input else "free")
        print(f"  {short:<40} {prompt:>6} in  {out:>5} out  {protocol}{suffix}")
    if details:
        labels = {None: "unknown", False: "no", True: "yes"}
        print("\n  Model details:")
        for model in models:
            print(f"\n  {model.id} — {model.name}")
            print(
                f"    Type: {model.model_type}; context: {_token_limit(model.context_window)}; "
                f"max input: {_token_limit(model.max_prompt_tokens)}; "
                f"max output: {_token_limit(model.max_tokens)} tokens"
            )
            print(
                f"    Read image: {labels[model.supports_image_input]}; "
                f"generate image: {labels[model.supports_image_output]}; "
                f"web search: {labels[model.supports_web_search]}"
            )
            print(f"    Wire protocol: {model.preferred_protocol or model.api or '?'}")
            supported = ", ".join(model.supported_protocols) if model.supported_protocols is not None else "unknown"
            print(f"    Supported protocols: {supported or 'none'}")
            print(f"    Generation protocols: {_generation_protocols(model)}")
            if model.endpoints is None:
                print("    Transport endpoints: not reported")
            elif not model.endpoints:
                print("    Transport endpoints: none")
            for endpoint in model.endpoints or ():
                label = {
                    "generate": "Non-streaming generation",
                    "stream_generate": "Streaming generation",
                    "embed": "Embeddings",
                    "count_tokens": "Token counting",
                }.get(endpoint.operation, endpoint.operation)
                print(f"    {label}: {endpoint.method} {endpoint.path} [{endpoint.protocol}]")
    print()


async def _cmd_chat(
    *,
    provider: str,
    model: str,
    message: str | None,
    system_prompt: str | None,
    temperature: float | None,
    max_tokens: int | None,
    thinking: str | None,
    image_paths: list[str],
    no_stream: bool,
    web_search: bool,
    verbose: bool,
) -> None:
    """Chat with a model."""
    p = ai.provider(provider)
    m = p.model(model)
    if m is None:
        print(f"Unknown model: {model!r} for provider {provider!r}", file=sys.stderr)
        sys.exit(1)

    # Load images
    images: list[ImageContent] = []
    for img_path in image_paths:
        path = Path(img_path)
        if not path.exists():
            print(f"Image not found: {img_path}", file=sys.stderr)
            sys.exit(1)
        mime, _ = mimetypes.guess_type(str(path))
        if not mime or not mime.startswith("image/"):
            print(f"Not an image: {img_path}", file=sys.stderr)
            sys.exit(1)
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        images.append(ImageContent(data=data, mime_type=mime))

    # Web search
    if web_search and message:
        try:
            result = await p.web_search(model, message)
            if result.stop_reason in ("error", "aborted"):
                raise RuntimeError(result.error_message or "Web search failed.")
            _print_result(result, verbose=verbose)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # Collect message
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
        print(f"Chatting with {provider}/{model} (type 'exit' or Ctrl+D to quit)\n", file=sys.stderr)

    opts = StreamOptions(
        thinking=thinking,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
    )

    conversation: list[tuple[str, str]] = []
    _MAX_HISTORY = 50

    while True:
        if interactive:
            try:
                user_input = _read_multiline_input()
            except (EOFError, KeyboardInterrupt):
                print("\n", file=sys.stderr)
                break
            if user_input.lower() in ("exit", "quit", "/exit"):
                break
            if not user_input.strip():
                continue
        else:
            if not messages:
                break
            user_input = messages.pop(0)

        # Build context
        history: list[Any] = []
        for role, content in conversation:
            if role == "user":
                history.append(UserMessage(content=content))
            else:
                history.append(AssistantMessage(content=(TextContent(text=content),)))

        if images:
            parts: list[TextContent | ImageContent] = [TextContent(text=user_input)]
            parts.extend(images)
            history.append(UserMessage(content=tuple(parts)))
            images = []
        else:
            history.append(UserMessage(content=user_input))

        ctx = Context(messages=tuple(history), system_prompt=system_prompt)

        try:
            if no_stream:
                result = await p.complete(model, ctx, opts)
                response_text = _print_result(result, verbose=verbose)
            else:
                event_stream = p.stream(model, ctx, opts)
                response_text = await _stream_response(event_stream, verbose=verbose)
        except KeyboardInterrupt:
            print("\n[interrupted]", file=sys.stderr)
            if not interactive:
                sys.exit(130)
            continue
        except Exception as exc:
            print(f"\nError: {exc}", file=sys.stderr)
            if not interactive:
                sys.exit(1)
            continue

        if interactive and response_text:
            conversation.append(("user", user_input))
            conversation.append(("assistant", response_text))
            if len(conversation) > _MAX_HISTORY * 2:
                conversation = conversation[-_MAX_HISTORY * 2:]


async def _cmd_embed(
    *,
    provider: str,
    model: str,
    text: str | None,
    dimensions: int | None,
    output_json: bool,
) -> None:
    """Generate embeddings."""
    if text is None:
        if sys.stdin.isatty():
            print("Enter text to embed (Ctrl+D to finish):", file=sys.stderr)
        text = sys.stdin.read().strip()
        if not text:
            print("No input.", file=sys.stderr)
            sys.exit(1)

    p = ai.provider(provider)

    try:
        result = await p.embed(model, EmbeddingRequest(input=text, dimensions=dimensions))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if output_json:
        out = {
            "data": [{"index": o.index, "embedding": list(o.embedding)} for o in result.data],
            "usage": {"input": result.usage.input, "total_tokens": result.usage.total_tokens},
        }
        print(json.dumps(out, indent=2))
    else:
        for obj in result.data:
            dims = len(obj.embedding)
            preview = ", ".join(f"{v:.6f}" for v in obj.embedding[:5])
            print(f"[{obj.index}] {dims} dimensions: [{preview}, ...]")
        print(f"\nTokens: {result.usage.input} input, {result.usage.total_tokens} total", file=sys.stderr)


async def _cmd_search(*, provider: str, model: str, query: str) -> None:
    """Web search."""
    p = ai.provider(provider)
    try:
        result = await p.web_search(model, query)
        if result.stop_reason in ("error", "aborted"):
            raise RuntimeError(result.error_message or "Web search failed.")
        _print_result(result, verbose=False)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


async def _cmd_image(
    *, provider: str, model: str, prompt: str, output_dir: Path, references: list[Path],
    aspect_ratio: str | None, image_size: str | None, output_json: bool,
) -> None:
    """Generate through the provider API and save image bytes, not base64 text."""
    from dataclasses import asdict

    from xdog.ai.utils.image_files import read_reference, save_images

    try:
        p = ai.provider(provider)
        if len(references) > 4:
            raise ValueError("At most four reference images are supported.")
        output_dir = output_dir.expanduser().resolve()
        if output_dir.exists() and not output_dir.is_dir():
            raise ValueError("Output directory is an existing file.")
        reference_images = tuple([await asyncio.to_thread(read_reference, path) for path in references])
        result = await p.image_generation(model, ImageGenerationRequest(
            prompt=prompt, reference_images=reference_images, aspect_ratio=aspect_ratio, image_size=image_size,
        ))
        paths = await asyncio.to_thread(save_images, result.images, output_dir)
    except (TimeoutError, httpx.TimeoutException):
        print("Error: image generation timed out; check provider status before retrying.", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPError:
        print("Error: image-generation network request failed.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if output_json:
        print(json.dumps({
            "paths": [str(path) for path in paths], "text": result.text,
            "model": result.model, "provider": result.provider, "usage": asdict(result.usage),
        }, ensure_ascii=False, indent=2))
    else:
        if result.text:
            print(result.text)
        for path in paths:
            print(f"Saved: {path}")


# ---------------------------------------------------------------------------
# Helpers
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


async def _stream_response(event_stream: Any, *, verbose: bool) -> str:
    """Stream tokens to stdout, return the full response text."""
    response_parts: list[str] = []
    thinking_parts: list[str] = []
    had_error = False

    async for event in event_stream:
        if event.type == "text_delta":
            delta = event.delta
            print(delta, end="", flush=True)
            response_parts.append(delta)
        elif event.type == "thinking_delta" and verbose:
            print(event.delta, end="", flush=True)
            thinking_parts.append(event.delta)
        elif event.type == "thinking_start" and verbose:
            print("\033[2m[thinking] ", end="", flush=True)
        elif event.type == "thinking_done" and verbose:
            print("\033[0m", end="", flush=True)
            if thinking_parts:
                print()
                thinking_parts.clear()
        elif event.type == "done" and verbose:
            assert isinstance(event, DoneEvent)
            msg = event.message
            if msg:
                cost = msg.usage.cost.total
                cost_str = f"  cost={cost}x" if cost > 0 else "  cost=free"
                print(
                    f"\033[2m[model={msg.model}  stop={event.stop_reason}"
                    f"  in={msg.usage.input}  out={msg.usage.output}{cost_str}]\033[0m",
                    file=sys.stderr,
                )
        elif event.type == "status":
            assert isinstance(event, StatusEvent)
            print(f"\033[2m[{event.detail}]\033[0m", file=sys.stderr, flush=True)
        elif event.type == "error":
            assert isinstance(event, ErrorEvent)
            had_error = True
            print(f"\nError: {event.error}", file=sys.stderr)

    text = "".join(response_parts)
    if text and not text.endswith("\n"):
        print()

    return "" if had_error else text


def _print_result(result: Any, *, verbose: bool) -> str:
    """Print a completed AssistantMessage, return text."""
    parts: list[str] = []
    for block in result.content:
        if block.type == "text":
            print(block.text)
            parts.append(block.text)
        elif block.type == "thinking" and verbose:
            print(f"\033[2m[thinking] {block.thinking}\033[0m")

    if verbose:
        cost = result.usage.cost.total
        cost_str = f"  cost={cost}x" if cost > 0 else "  cost=free"
        print(
            f"\033[2m[model={result.model}  stop={result.stop_reason}"
            f"  in={result.usage.input}  out={result.usage.output}{cost_str}]\033[0m",
            file=sys.stderr,
        )

    return "".join(parts)


if __name__ == "__main__":
    main()
