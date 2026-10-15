"""Serial standard ACP prompts; provenance is data, never an authorization grant."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from xdog.eyes.acp_client import AcpClient, RequestError
from xdog.eyes.events import DeliveryResult, prompt_parameters
from xdog.eyes.models import HandlerDef

if TYPE_CHECKING:
    from xdog.eyes.executor import Delivery


class AcpHandler:
    def __init__(self, definition: HandlerDef, *, cwd: Path) -> None:
        self.definition = definition
        self.client = AcpClient(definition.command, definition.session, cwd)
        self.state = "new"
        self.wake_budget: int | None = 0
        self._serial = asyncio.Lock()
        self._halted: DeliveryResult | None = None

    @property
    def owner_generation(self) -> str | None:
        return self.client.owner_generation

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self.client.process

    async def connect(self) -> None:
        try:
            await self.client.connect()
        except BaseException:
            self.state = "closed"
            raise
        self.state = "ready"
        # XDOG owns human/resume credit and can unblock an already-sent
        # prompt. Do not add a second budget that would ignore human input.
        self.wake_budget = None if {"session_info", "resume"} <= self.client.extensions.keys() else 1

    async def deliver(self, delivery: Delivery) -> DeliveryResult:
        async with self._serial:
            if self._halted is not None:
                return self._halted
            if self.state == "new":
                return DeliveryResult("not-sent", "ACP handler is not connected")
            if self.state == "closed":
                return DeliveryResult("cancelled", "ACP connection closed; stop/rebaseline")
            try:
                if self.owner_generation is not None and await self.client.generation() != self.owner_generation:
                    raise ValueError("ACP owner generation changed")
            except Exception as exc:
                self.state = "retired"
                self._halted = DeliveryResult("cancelled", str(exc) + "; stop/rebaseline")
                return self._halted
            if self.wake_budget is not None and self.wake_budget <= 0:
                self.state = "paused"
                return DeliveryResult("paused", "Observer wake budget exhausted; explicit authorized resume required")
            try:
                parameters = prompt_parameters(delivery)
                if len(json.dumps(parameters, ensure_ascii=True, allow_nan=False).encode()) > 1024 * 1024 - 4096:
                    return DeliveryResult("failed", "ACP prompt exceeds capacity; evidence was not truncated")
            except (ValueError, TypeError, UnicodeError) as exc:
                return DeliveryResult("failed", "Invalid ACP delivery: " + str(exc))
            self.state = "processing"
            if self.wake_budget is not None:
                self.wake_budget -= 1
            try:
                response = await self.client.prompt(parameters)
            except asyncio.CancelledError:
                # Closing this connection settles the bridge peer; no other
                # session or UI is terminated, and this prompt is not replayed.
                self._halted = DeliveryResult("outcome-unknown", "Prompt cancelled; inspect effects before rebaseline")
                await self.client.close()
                self.state = "closed"
                raise
            except RequestError as exc:
                self.state = "paused" if self.wake_budget == 0 else "ready"
                return DeliveryResult("failed", str(exc))
            except Exception as exc:
                self.state = "outcome-unknown"
                self._halted = DeliveryResult("outcome-unknown", str(exc) + "; stop/rebaseline, never blind retry")
                return self._halted
            self.state = "paused" if self.wake_budget == 0 else "ready"
            if response.stop_reason == "end_turn":
                return DeliveryResult("succeeded")
            if response.stop_reason == "cancelled":
                return DeliveryResult("cancelled")
            return DeliveryResult("failed", "ACP turn ended with " + response.stop_reason)

    async def resume(self) -> bool:
        if self._halted is not None or self.client.conn is None or "resume" not in self.client.extensions:
            return False
        if self.owner_generation is not None:
            try:
                if await self.client.generation() != self.owner_generation:
                    return False
            except Exception:
                return False
        inflight = self.state == "processing"
        result = await self.client.conn.ext_method("xdog/resume", {"session_id": self.definition.session})
        approved = result.get("resumed") is True
        if approved:
            # A resume during an outstanding prompt covers that prompt, not an
            # additional unapproved successor that might be caused by self-edits.
            if self.wake_budget is not None:
                self.wake_budget = 0 if inflight else 1
            if not inflight:
                self.state = "ready"
        return approved

    async def close(self) -> None:
        self.state = "closed"
        await self.client.close()
