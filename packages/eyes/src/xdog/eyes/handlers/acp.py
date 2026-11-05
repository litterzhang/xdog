"""Serial ACP prompts or durable intake; provenance is data, never an authorization grant."""

from __future__ import annotations

import asyncio
import json

from xdog.eyes.acp_client import AcpClient, RequestError
from xdog.eyes.deliveries import Delivery
from xdog.eyes.events import DeliveryResult
from xdog.eyes.handlers.acp_payload import enqueue_parameters, prompt_parameters
from xdog.eyes.handlers.contracts import HandlerHealthError, HandlerSnapshot
from xdog.eyes.local_control import JsonValue
from xdog.eyes.models import AcpHandlerDef


class AcpHandler:
    def __init__(self, definition: AcpHandlerDef) -> None:
        self.definition = definition
        self.client = AcpClient(definition.connection, definition.session, definition.cwd,
                                delivery=definition.delivery)
        self.state = "new"
        self._health_failed = False
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

    async def start(self) -> None:
        try:
            await self.client.connect()
        except (ValueError, UnicodeError, RequestError):
            self.state = "retired"
            raise
        except Exception as exc:
            self.state = "reconnecting"
            self._health_failed = True
            raise HandlerHealthError(str(exc), recoverable=self.definition.delivery == 'enqueue') from exc
        except BaseException:
            self.state = "closed"
            raise
        self.state = "ready"
        if self.definition.delivery == 'enqueue':
            self._inbox_scope = self.client.inbox_id, self.client.branch_id

    async def deliver(self, delivery: Delivery) -> DeliveryResult:
        async with self._serial:
            if self._halted is not None:
                return self._halted
            if self.state in {"new", "reconnecting"}:
                return DeliveryResult("retry", "ACP handler is reconnecting; original delivery retained")
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
            try:
                parameters = prompt_parameters(delivery, instruction=self.definition.instruction)
                if len(json.dumps(parameters, ensure_ascii=True, allow_nan=False).encode()) > 1024 * 1024 - 4096:
                    return DeliveryResult("failed", "ACP prompt exceeds capacity; evidence was not truncated")
            except (ValueError, TypeError, UnicodeError) as exc:
                return DeliveryResult("failed", "Invalid ACP delivery: " + str(exc))
            self.state = "processing"
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
                self.state = "ready"
                return DeliveryResult("failed", str(exc))
            except Exception as exc:
                self.state = "outcome-unknown"
                self._halted = DeliveryResult("outcome-unknown", str(exc) + "; stop/rebaseline, never blind retry")
                return self._halted
            self.state = "ready"
            if response.stop_reason == "end_turn":
                return DeliveryResult("succeeded")
            if response.stop_reason == "cancelled":
                return DeliveryResult("cancelled")
            return DeliveryResult("failed", "ACP turn ended with " + response.stop_reason)

    async def _enqueue(self, delivery: Delivery) -> DeliveryResult:
        try:
            parameters = enqueue_parameters(delivery, session_id=self.definition.session,
                                            cwd=str(self.client.cwd) if self.client.cwd is not None else None,
                                            instruction=self.definition.instruction)
            canonical = json.dumps(parameters, ensure_ascii=True, allow_nan=False, sort_keys=True)
            if len(canonical.encode()) > 1024 * 1024 - 4096:
                return DeliveryResult('failed', 'ACP enqueue exceeds capacity; evidence was not truncated')
        except (ValueError, TypeError, UnicodeError) as exc:
            return DeliveryResult('failed', 'Invalid ACP enqueue: ' + str(exc))
        if self._pending_enqueue is not None and self._pending_enqueue != (delivery.id, canonical):
            return DeliveryResult('retry', 'Waiting for the original delivery ID and content to settle')
        self._pending_enqueue = delivery.id, canonical
        try:
            async with asyncio.timeout(10):
                await self.client.inbox_status()
        except (ValueError, UnicodeError, RequestError) as exc:
            self.state = 'retired'
            self._halted = DeliveryResult('failed', 'Inbox binding/refusal: ' + str(exc))
            return self._halted
        except Exception as exc:
            self.state = 'reconnecting'
            self._health_failed = True
            self._halted = DeliveryResult('retry', 'Inbox connection unavailable: ' + str(exc))
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
            self.state = 'retired'
            self._halted = DeliveryResult('failed', 'Enqueue refused; no automatic retry: ' + str(exc))
            return self._halted
        except (ValueError, UnicodeError) as exc:
            self.state = 'outcome-unknown'
            self._halted = DeliveryResult('outcome-unknown', 'Invalid enqueue receipt: ' + str(exc))
            return self._halted
        except Exception as exc:
            self.state = 'reconnecting'
            self._health_failed = True
            self._halted = DeliveryResult('retry', str(exc) + '; retry only the original message ID')
            return self._halted
        self.state = 'ready'
        self._pending_enqueue = None
        return DeliveryResult('queued', receipt_status=receipt['status'], message_state=receipt['message_state'])

    async def _resume_enqueue(self, *, allow_idle: bool = False) -> bool:
        if (self._closed or self._pending_enqueue is None and not allow_idle
                or self.state in {'processing', 'retired', 'outcome-unknown'}):
            return False
        async with self._resuming:
            try:
                async with asyncio.timeout(10):
                    await self.client.inbox_status()
            except (ValueError, UnicodeError, RequestError) as exc:
                raise HandlerHealthError(str(exc)) from exc
            except Exception:
                old = self.client
                await old.close()
                replacement = AcpClient(
                    self.definition.connection, self.definition.session, old.cwd, delivery='enqueue')
                try:
                    await replacement.connect()
                    scope = replacement.inbox_id, replacement.branch_id
                    if self._inbox_scope is not None and scope != self._inbox_scope:
                        raise ValueError('Original ACP inbox/branch binding changed; no work was resent')
                except BaseException as exc:
                    await replacement.close()
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    if isinstance(exc, (ValueError, UnicodeError, RequestError)):
                        raise HandlerHealthError(str(exc)) from exc
                    return False
                self.client = replacement
                self._inbox_scope = scope
            if self._closed:
                await self.client.close()
                return False
            self._halted = None
            self.state = 'ready'
            return True

    def validate(self, delivery: Delivery) -> None:
        parameters = (enqueue_parameters(delivery, session_id=self.definition.session,
                                            cwd=str(self.client.cwd) if self.client.cwd is not None else None,
                                          instruction=self.definition.instruction)
                      if self.definition.delivery == 'enqueue' else
                      prompt_parameters(delivery, instruction=self.definition.instruction))
        if len(json.dumps(parameters, ensure_ascii=True, allow_nan=False).encode()) > 1024 * 1024 - 4096:
            raise ValueError('ACP delivery exceeds capacity; evidence was not truncated')

    def snapshot(self) -> HandlerSnapshot:
        binding: dict[str, JsonValue] = {
            'session': self.definition.session, 'delivery': self.definition.delivery,
            'owner_generation': self.owner_generation,
            'inbox_id': self.client.inbox_id, 'branch_id': self.client.branch_id}
        return HandlerSnapshot(self.state, {**binding, 'connected': self.client.connected},
                               binding=binding, retry_safe=self.definition.delivery == 'enqueue')

    async def check(self) -> None:
        if self.state in {'processing', 'outcome-unknown', 'closed'}:
            return
        try:
            if self.state == 'retired':
                raise ValueError('ACP target binding or request was rejected')
            if not self.client.connected:
                raise ConnectionError('ACP connection ended; reconnecting to the original target')
            if self.state == 'reconnecting':
                raise ConnectionError('ACP enqueue requires same-target reconnect')
            if self.definition.delivery == 'enqueue':
                async with asyncio.timeout(2):
                    await self.client.inbox_status()
            elif self.owner_generation is not None and await self.client.generation() != self.owner_generation:
                raise ValueError('ACP owner generation changed')
            self._health_failed = False
        except Exception as exc:
            self._health_failed = True
            recoverable = self.definition.delivery == 'enqueue' and not isinstance(
                exc, (ValueError, UnicodeError, RequestError))
            raise HandlerHealthError(str(exc), recoverable=recoverable) from exc

    async def resume(self) -> bool:
        """Recover a data connection; never request permission or grant model credit."""
        if self._closed or self.definition.delivery != 'enqueue':
            return False
        task = asyncio.current_task()
        assert task is not None
        self._resumes.add(task)
        try:
            recovered = await self._resume_enqueue(allow_idle=self._health_failed or not self.client.connected)
            if recovered:
                self._health_failed = False
            return recovered
        finally:
            self._resumes.discard(task)

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
