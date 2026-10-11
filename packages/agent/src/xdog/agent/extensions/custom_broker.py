"""Loop-owned custom frame queue under the same broker future/host dialog lease."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from xdog.agent.extensions.custom import event, frame


@dataclass
class View:
    extension: str
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[Any]
    render: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=32))
    task: asyncio.Task[None] | None = None
    key_events: frozenset[str] = frozenset({'press'})
    count: int = 0
    revision: int = 0
    geometry: tuple[int, int] = (0, 0)
    finishing: bool = False
    suspended: bool = False
    proof: int = 0
    generation: int = 0
    latest_generation: int = 0
    refresh_dirty: bool = False
    refresh_pending: bool = False
    refreshed_at: float = 0
    control_generation: int = -1


class CustomFrames:
    def __init__(self, broker: Any) -> None:
        self.broker = broker
        self.views: dict[str, View] = {}
        self.lock = threading.Lock()

    def add(self, identifier: str, extension: str, loop: asyncio.AbstractEventLoop,
            future: asyncio.Future[Any], render: Any, *, key_events: list[str]) -> None:
        view = View(extension, loop, future, render, key_events=frozenset(key_events))
        with self.lock:
            self.views[identifier] = view
        view.task = asyncio.create_task(self._run(identifier, view))

    def suspend(self, identifier: str, value: bool) -> None:
        with self.lock:
            view = self.views.get(identifier)
            if view is not None:
                view.suspended = value
                view.proof += 1
                if not value:
                    self._queue_refresh_locked(view)

    def _queue_refresh_locked(self, view: View) -> None:
        if (not view.refresh_dirty or view.refresh_pending or view.suspended or view.finishing
                or view.future.done() or view.loop.is_closed() or view.count >= 32
                or view.geometry[0] <= 0 or view.geometry[1] <= 0):
            return
        request = {'kind': 'resize', 'width': view.geometry[0], 'height': view.geometry[1],
                   'painted': -1, 'generation': view.generation, '_refresh': True}
        view.count += 1
        view.refresh_pending = True
        try:
            view.loop.call_soon_threadsafe(view.queue.put_nowait, request)
        except RuntimeError:
            view.count -= 1
            view.refresh_pending = False

    def revoke(self, identifier: str) -> bool:
        with self.lock:
            view = self.views.get(identifier)
            if view is None or view.future.done() or view.finishing:
                return False
            view.proof += 1
            view.control_generation = view.latest_generation
            return True

    def refresh(self, identifier: str) -> bool:
        with self.lock:
            view = self.views.get(identifier)
            if view is None or view.future.done() or view.finishing or view.loop.is_closed():
                return False
            view.refresh_dirty = True
            view.proof += 1  # A background state change revokes in-flight completion proof immediately.
            self._queue_refresh_locked(view)
            return True

    def request(self, identifier: str, value: dict[str, Any]) -> bool:
        request = event(value)
        with self.lock:
            view = self.views.get(identifier)
            if (view is None or view.future.done() or view.finishing
                    or view.loop.is_closed()):
                return False
            if view.suspended and not (request['kind'] == 'resize' and request['height'] == 0):
                return False
            if request['kind'] == 'key' and request.get('event_type', 'press') not in view.key_events:
                return False
            generation = request.get('generation', 0)
            if generation < view.latest_generation:
                return False
            view.latest_generation = generation  # Even quota refusal fences obsolete queued completions.
            if view.count >= 32:
                return False
            view.count += 1
            try:
                view.loop.call_soon_threadsafe(view.queue.put_nowait, request)
            except RuntimeError:
                view.count -= 1
                return False
        return True

    async def _run(self, identifier: str, view: View) -> None:
        try:
            while not view.future.done():
                request = await view.queue.get()
                refresh = request.pop('_refresh', False)
                if refresh:
                    # Coalesce bursts through callback admission and cap idle background
                    # rendering at 30 FPS. This queue never fabricates input/completion.
                    await asyncio.sleep(max(0, view.refreshed_at + 1 / 30 - view.loop.time()))
                    with self.lock:
                        if view.suspended or view.geometry[1] <= 0:
                            view.count -= 1
                            view.refresh_pending = False
                            continue  # Keep dirty until this owner becomes visible again.
                        view.refresh_dirty = False
                        request.update(width=view.geometry[0], height=view.geometry[1], generation=view.generation)
                    view.refreshed_at = view.loop.time()
                before, proof = view.revision, view.proof
                from xdog.agent.extensions.dialogs import render_owner
                token = render_owner.set((self.broker, identifier))
                try:
                    data = frame(await view.render(request), request)
                finally:
                    render_owner.reset(token)
                if not self.broker.contains(identifier) or view.future.done():
                    return
                with self.lock:
                    allowed = (data['done'] and request['painted'] == before and request['height'] > 0
                               and view.geometry == (request['width'], request['height']) and before > 0
                               and view.generation == request.get('generation', 0) == view.latest_generation
                               and not view.suspended and proof == view.proof
                               and not view.refresh_dirty and not view.refresh_pending
                               and request.get('generation', 0) > view.control_generation)
                    superseded = request.get('generation', 0) != view.latest_generation
                    view.count -= 1
                    if allowed:
                        view.finishing = True
                view.revision += 1
                view.geometry = request['width'], request['height']
                view.generation = request.get('generation', 0)
                if allowed:
                    self.broker._resolve(identifier, data['value'], custom_completion=True)
                    return
                if refresh:
                    with self.lock:
                        view.refresh_pending = False
                self.broker.publish({'type': 'extension_custom_frame', 'id': identifier,
                    'extension': view.extension, 'width': request['width'], 'height': request['height'],
                    'revision': view.revision, 'generation': view.generation,
                    'lines': data['lines'], 'cursor': data['cursor'],
                    'review_required': data['done'],
                    **({'superseded': True} if superseded else {})})
                with self.lock:
                    self._queue_refresh_locked(view)
        except asyncio.CancelledError:
            raise
        except Exception:
            with self.lock:
                view.finishing = True
            if view.future.done() or not self.broker.contains(identifier):
                return
            self.broker.publish({'type': 'extension_custom_error', 'id': identifier,
                                 'extension': view.extension, 'error': 'Custom rendering/input failed; view canceled'})
            self.broker.resolve(identifier, None)

    async def remove(self, identifier: str) -> None:
        with self.lock:
            view = self.views.pop(identifier, None)
        if view is not None and view.task is not None:
            if not view.task.done() and not view.task.cancelling():
                view.task.cancel()
            await asyncio.gather(view.task, return_exceptions=True)
