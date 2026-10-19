"""TUI client — connects to the claw gateway via Unix socket.

Provides an interactive terminal chat interface that communicates
with the running gateway daemon using JSON-line protocol.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from xdog.agent.async_work import run_owned

logger = logging.getLogger(__name__)


class GatewayConnectionError(Exception):
    """Raised when the TUI cannot connect to the gateway."""


async def _send_request(
    writer: asyncio.StreamWriter,
    reader: asyncio.StreamReader,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Send a JSON-line request and read the response."""
    line = json.dumps(request) + "\n"
    writer.write(line.encode("utf-8"))
    await writer.drain()

    response_line = await asyncio.wait_for(reader.readline(), timeout=120)
    if not response_line:
        return {"type": "error", "message": "Connection closed by gateway"}

    try:
        parsed: dict[str, Any] = json.loads(response_line.decode("utf-8").strip())
        return parsed
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"type": "error", "message": "Invalid response from gateway"}


async def _interactive_loop(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    group_id: str,
) -> None:
    """Run the interactive chat loop."""
    print("=" * 60)
    print("  claw TUI — Interactive Chat")
    print(f"  Group: {group_id}")
    print("  Commands: /reset  /quit  /status")
    print("=" * 60)
    print()

    loop = asyncio.get_running_loop()

    while True:
        try:
            user_input = await loop.run_in_executor(
                None, lambda: input("You: ").strip()
            )
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        lower = user_input.lower()

        if lower in ("/quit", "/exit", "quit", "exit"):
            print("Goodbye!")
            break

        if lower == "/reset":
            response = await _send_request(writer, reader, {
                "type": "reset",
                "group_id": group_id,
            })
            if response.get("type") == "reset_ack":
                print("[Session reset — starting fresh]\n")
            else:
                print(f"[Reset failed: {response.get('message', 'unknown error')}]\n")
            continue

        if lower == "/status":
            response = await _send_request(writer, reader, {"type": "status"})
            if response.get("type") == "status":
                pid = response.get("pid", "?")
                groups = response.get("groups", [])
                print(f"[Gateway running (PID: {pid}), groups: {', '.join(groups)}]\n")
            else:
                print(f"[Status error: {response.get('message', 'unknown')}]\n")
            continue

        # Send chat message
        print("Assistant: ", end="", flush=True)
        response = await _send_request(writer, reader, {
            "type": "message",
            "group_id": group_id,
            "content": user_input,
        })

        if response.get("type") == "response":
            print(response.get("content", "(no response)"))
        elif response.get("type") == "error":
            print(f"[Error: {response.get('message', 'unknown')}]")
        else:
            print(f"[Unexpected response: {response}]")
        print()


async def connect_tui(socket_path: str, group_id: str = "main") -> None:
    """Connect to the gateway and start the interactive TUI.

    Raises GatewayConnectionError if the gateway is unreachable.
    """
    sock = Path(socket_path)

    if not await run_owned(sock.exists):
        raise GatewayConnectionError(
            f"Gateway socket not found at {sock}. "
            "Is the gateway running? Start it with: xdog-claw gateway start"
        )

    try:
        reader, writer = await asyncio.open_unix_connection(str(sock))
    except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
        raise GatewayConnectionError(
            f"Cannot connect to gateway at {sock}: {exc}. "
            "Is the gateway running? Start it with: xdog-claw gateway start"
        ) from exc

    # Verify connection with a ping
    pong = await _send_request(writer, reader, {"type": "ping"})
    if pong.get("type") != "pong":
        raise GatewayConnectionError("Gateway did not respond to ping")

    try:
        await _interactive_loop(reader, writer, group_id)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def run_tui(socket_path: str, group_id: str = "main", *, model: str = "") -> None:
    """Entry point to run the TUI client (blocking).

    Uses the tui application.
    Catches GatewayConnectionError and exits with appropriate message.
    """
    from xdog.claw.channels.tui.tui_app import run_tui_app
    run_tui_app(socket_path, group_id, model=model)
