"""Serial ACP prompts or durable intake; provenance is data, never an authorization grant."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from xdog.eyes.acp_client import AcpClient, RequestError
from xdog.eyes.events import DeliveryResult, enqueue_parameters, prompt_parameters
from xdog.eyes.models import HandlerDef

if TYPE_CHECKING:
    from xdog.eyes.executor import Delivery


class AcpHandler:
    def __init__(self, definition: HandlerDef, *, cwd: Path) -> None:
        self.definition = definition
        self.client = AcpClient(definition.connection, definition.session, cwd, delivery=definition.delivery)
        self.state = "new"
        self.wake_budget: int | None = 0
        self._serial = asyncio.Lock()
        self._halted: DeliveryResult | None = None
        self._pending_enqueue: tuple[str, str] | None = None
        self._inbox_scope: tuple[str | None, str | None] | None = None
        self._resuming = asyncio.Lock()
        self._closed = False
        self._resumes: set[asyncio.Task[object]] = set()
        self._close_task: asyncio.Task[None] | None = None

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
        self.wake_budget = None if self.definition.delivery == 'enqueue' or self.client.automatic_budget else 1
        if self.definition.delivery == 'enqueue':
            self._inbox_scope = self.client.inbox_id, self.client.branch_id

    async def deliver(self, delivery: Delivery) -> DeliveryResult:
        async with self._serial:
            if self._halted is not None:
                return self._halted
            if self.state == "new":
                return DeliveryResult("not-sent", "ACP handler is not connected")
            if self._closed or self.state == "closed":
                return DeliveryResult("cancelled", "ACP connection closed; stop/rebaseline")
            if self.definition.delivery == 'enqueue':
                return await self._enqueue(delivery)
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

    async def _enqueue(self, delivery: Delivery) -> DeliveryResult:
        try:
            parameters = enqueue_parameters(delivery, session_id=self.definition.session, cwd=str(self.client.cwd))
            canonical = json.dumps(parameters, ensure_ascii=True, allow_nan=False, sort_keys=True)
            if len(canonical.encode()) > 1024 * 1024 - 4096:
                return DeliveryResult('failed', 'ACP enqueue exceeds capacity; evidence was not truncated')
        except (ValueError, TypeError, UnicodeError) as exc:
            return DeliveryResult('failed', 'Invalid ACP enqueue: ' + str(exc))
        if self._pending_enqueue is not None and self._pending_enqueue != (delivery.id, canonical):
            return DeliveryResult('paused', 'Explicit retry must retain the original delivery ID and content')
        self._pending_enqueue = delivery.id, canonical
        try:
            async with asyncio.timeout(10):
                await self.client.inbox_status()
        except Exception as exc:
            self.state = 'paused'
            self._halted = DeliveryResult(
                'paused', 'Inbox scope/connection unavailable: ' + str(exc), retry_on_resume=True)
            return self._halted
        self.state = 'processing'
        try:
            async with asyncio.timeout(10):
                receipt = await self.client.enqueue(parameters)
        except asyncio.CancelledError:
            self._halted = DeliveryResult('outcome-unknown', 'Enqueue cancelled; inspect/retry the same message ID',
                                          retry_on_resume=True)
            await self.client.close()
            self.state = 'closed'
            raise
        except RequestError as exc:
            self.state = 'paused'
            self._halted = DeliveryResult('paused', 'Enqueue refused; inspect before explicit retry: ' + str(exc),
                                          retry_on_resume=True)
            return self._halted
        except Exception as exc:
            self.state = 'outcome-unknown'
            self._halted = DeliveryResult('outcome-unknown', str(exc) + '; explicit same-ID retry only',
                                          retry_on_resume=True)
            return self._halted
        self.state = 'ready'
        self._pending_enqueue = None
        return DeliveryResult('queued', receipt_status=receipt['status'], message_state=receipt['message_state'])

    async def _resume_enqueue(self) -> bool:
        if self._closed or self._pending_enqueue is None or self.state == 'processing':
            return False
        async with self._resuming:
            try:
                async with asyncio.timeout(10):
                    await self.client.inbox_status()
            except (ValueError, UnicodeError):
                return False  # An accessible but different store/branch is never silently followed.
            except Exception:
                old = self.client
                await old.close()
                replacement = AcpClient(
                    self.definition.connection, self.definition.session, old.cwd, delivery='enqueue')
                try:
                    await replacement.connect()
                    if (replacement.inbox_id, replacement.branch_id) != self._inbox_scope:
                        await replacement.close()
                        return False
                except BaseException as exc:
                    await replacement.close()
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    return False
                self.client = replacement
            if self._closed:
                await self.client.close()
                return False
            self._halted = None
            self.state = 'ready'
            return True

    async def resume(self) -> bool:
        if self._closed:
            return False
        task = asyncio.current_task()
        assert task is not None
        self._resumes.add(task)
        try:
            return await self._resume_impl()
        finally:
            self._resumes.discard(task)

    async def _resume_impl(self) -> bool:
        if self.definition.delivery == 'enqueue':
            return await self._resume_enqueue()
        if self._closed:
            return False
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
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self) -> None:
        self.state = 'closed'
        tasks = tuple(self._resumes)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.close()
