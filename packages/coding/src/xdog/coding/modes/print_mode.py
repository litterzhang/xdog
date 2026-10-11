"""Print mode: non-interactive output mode for scripting and piping."""

from __future__ import annotations

import json
import sys
import threading
from typing import Any

from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCall,
)
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import (
    PermissionDecision,
    PermissionRequest,
    PermissionRequestHandler,
)


class PrintModeRenderer:
    """Renders agent output in a non-interactive format.

    Supports text, JSON, and markdown output formats.
    """

    def __init__(
        self,
        *,
        output_format: str = "text",
        stream: Any = None,
        verbose: bool = False,
    ) -> None:
        self._format = output_format
        self._stream = stream or sys.stdout
        self._verbose = verbose

    def render_message(self, message: AssistantMessage) -> None:
        """Render a single assistant message to the output stream."""
        if self._format == "json":
            self._render_json(message)
        elif self._format == "markdown":
            self._render_markdown(message)
        else:
            self._render_text(message)

    def render_handled(self, feedback: list[str], response: AssistantMessage | None) -> None:
        """Explicit system feedback, never an invented assistant response or malformed JSON stream."""
        from xdog.tui.utils import sanitize_terminal_text
        if self._format == "json":
            import io
            assistant = None
            if response is not None:
                stream = io.StringIO()
                PrintModeRenderer(output_format="json", stream=stream).render_message(response)
                assistant = json.loads(stream.getvalue())
            self._write(json.dumps({"role": "system", "type": "input_handled", "feedback": feedback,
                                    "assistant": assistant}, ensure_ascii=False, indent=2))
        else:
            for value in feedback:
                self._write(sanitize_terminal_text(value))
            if response is not None:
                self.render_message(response)

    def _render_text(self, message: AssistantMessage) -> None:
        """Render in plain text format."""
        for part in message.content:
            if isinstance(part, TextContent):
                self._write(part.text)
            elif isinstance(part, ToolCall) and self._verbose:
                self._write(f"\n[Tool: {part.name}]")

    def _render_json(self, message: AssistantMessage) -> None:
        """Render in JSON format."""
        output: dict[str, Any] = {"role": "assistant", "content": []}
        for part in message.content:
            if isinstance(part, TextContent):
                output["content"].append({"type": "text", "text": part.text})
            elif isinstance(part, ToolCall):
                output["content"].append({
                    "type": "tool_call",
                    "name": part.name,
                    "arguments": part.arguments,
                })
        self._write(json.dumps(output, indent=2, ensure_ascii=False))

    def _render_markdown(self, message: AssistantMessage) -> None:
        """Render in markdown format."""
        for part in message.content:
            if isinstance(part, TextContent):
                self._write(part.text)
            elif isinstance(part, ToolCall) and self._verbose:
                self._write(
                    f"\n**Tool: {part.name}**\n"
                    f"```json\n{json.dumps(part.arguments, indent=2)}\n```\n"
                )

    def _write(self, text: str) -> None:
        self._stream.write(text)
        self._stream.write("\n")
        self._stream.flush()


async def run_print_mode(
    session: AgentSession,
    prompt: str,
    *,
    output_format: str = "text",
    verbose: bool = False,
    images: tuple[ImageContent, ...] = (),
) -> int:
    """Run the agent in non-interactive print mode.

    Sends a single prompt and prints the full response.
    Returns 0 on success, 1 on error.
    """
    renderer = PrintModeRenderer(output_format=output_format, verbose=verbose)
    if sys.stdin.isatty() and sys.stderr.isatty():
        session.permissions.set_request_handler(
            _terminal_permission_handler(session),
        )

    try:
        response = await session.send_message(prompt, images=images) if images else await session.send_message(prompt)
        feedback = getattr(session, "handled_feedback", [])
        if isinstance(feedback, list) and feedback:
            renderer.render_handled(feedback, response)
        elif response is not None:
            renderer.render_message(response)
        if response is None:
            error = getattr(session.agent.state, "error", None)
            if isinstance(error, str) and error:
                raise RuntimeError(error)  # Pre-admission refusals are not empty successful output.
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.permissions.set_request_handler(None)
        session.permissions.deny_all()


def _terminal_permission_handler(session: AgentSession) -> PermissionRequestHandler:
    """Build a non-blocking handler that prompts on the controlling terminal."""
    prompt_lock = threading.Lock()

    def _handler(request: PermissionRequest) -> None:
        def _ask() -> None:
            with prompt_lock:
                print("\nTool permission required", file=sys.stderr)
                print(request.summary, file=sys.stderr)
                print(
                    "[y] allow once  [a] allow exact call for session  [n] deny",
                    file=sys.stderr,
                )
                decision: PermissionDecision = "deny"
                while True:
                    sys.stderr.write("permission> ")
                    sys.stderr.flush()
                    answer = sys.stdin.readline()
                    if not answer:
                        break
                    answer = answer.strip().lower()
                    if answer in ("y", "yes"):
                        decision = "allow_once"
                        break
                    if answer in ("a", "always", "session"):
                        decision = "allow_session"
                        break
                    if answer in ("n", "no", "deny", ""):
                        break
                session.permissions.resolve(request.id, decision)

        threading.Thread(target=_ask, daemon=True).start()

    return _handler
