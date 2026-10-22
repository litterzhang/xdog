"""TUI channel — bridges gateway socket connections to the orchestrator.

This channel is used by the gateway server to handle messages from
TUI clients connected via Unix socket. The gateway creates a TuiChannel
for each client connection, routing messages through the orchestrator.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.claw.channels.base import Channel
from xdog.claw.core.types import UserInput


class TuiChannel(Channel):
    """Terminal channel that bridges socket clients to the orchestrator.

    In gateway mode, `send_message` writes JSON-line responses to the
    connected client's StreamWriter. For testing, it falls back to print.
    """

    def __init__(self, writer: asyncio.StreamWriter | None = None) -> None:
        self._writer = writer

    @property
    def name(self) -> str:
        return "tui"

    async def connect(self) -> None:
        pass  # TUI is always connected

    async def disconnect(self) -> None:
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    async def send_message(self, group_id: str, text: str) -> None:
        """Send a response to the connected client."""
        if self._writer:
            response = json.dumps({
                "type": "response",
                "group_id": group_id,
                "content": text,
            }) + "\n"
            self._writer.write(response.encode("utf-8"))
            await self._writer.drain()
        else:
            # Fallback for testing / standalone use
            print(f"[{group_id}] {text}")

    async def simulate_input(self, group_id: str, text: str, sender: str = "user") -> None:
        """Simulate user input (for testing)."""
        if hasattr(self, "_on_message"):
            msg = UserInput(group_id=group_id, content=text, sender=sender, channel="tui")
            await self._on_message(msg)


class NativeNoticeChannel(Channel):
    """One live private connection's explicit notification route, never restorable."""

    def __init__(self, publish: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._publish = publish
        self._closed = False
        self._bound_group: str | None = None
        self._bound_target: str | None = None

    @property
    def name(self) -> str:
        return 'tui'

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        self._closed = True
        self._bound_group = None
        self._bound_target = None

    def bind_group(self, group_id: str) -> None:
        """Bind only this live connection; switching groups retires old targets."""
        if not isinstance(group_id, str) or not group_id.strip() or "\x00" in group_id:
            raise ValueError("Native target requires a nonempty group")
        if self._closed:
            return  # A disconnected endpoint cannot be revived by a late bind.
        if group_id != self._bound_group:
            self._bound_group = group_id
            self._bound_target = uuid.uuid4().hex

    def bound_reply(self, group_id: str) -> UserInput | None:
        if self._closed or self._bound_group != group_id or self._bound_target is None:
            return None
        return UserInput(group_id=group_id, channel=self.name,
                         metadata={"native_bound_target": self._bound_target})

    def reply_context(self, original: UserInput) -> dict[str, object]:
        # Preserve the proactive fence through host ReplyReference capture. An
        # observer's original reply has no such marker and retains its semantics.
        if "native_bound_target" in original.metadata:
            return {"native_bound_target": original.metadata["native_bound_target"]}
        return {}

    def accepts_group(self, group_id: str) -> bool:
        return False

    async def send_message(self, group_id: str, text: str) -> None:
        raise ValueError('Native notices require their original explicit reply binding')

    async def send_output(self, original: UserInput, output_id: str, text: str) -> None:
        from xdog.claw.core.outputs.delivery import DeliveryNotSent
        if self._closed:
            raise DeliveryNotSent('Original Native connection is closed')
        if "native_bound_target" in original.metadata and (
            original.channel != self.name or original.group_id != self._bound_group
            or self._bound_target is None or original.metadata["native_bound_target"] != self._bound_target
        ):
            raise DeliveryNotSent('Original Native group binding is no longer current')
        await self._publish({'type': 'notification', 'group_id': original.group_id,
                             'notification_id': output_id, 'content': text})
