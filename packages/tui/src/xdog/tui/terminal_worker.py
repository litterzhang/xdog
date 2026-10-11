"""Single bounded background callback; results are published, never applied off-thread."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from xdog.tui.terminal_interception import HeldTerminalInput


class TerminalHookWorker:
    def __init__(self, publish: Callable[[dict[str, Any]], None]) -> None:
        self.publish = publish
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._busy = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[dict[str, Any]] | None = None
        self._cancel = threading.Event()

    def send(self, item: HeldTerminalInput,
             run: Callable[[HeldTerminalInput], Coroutine[Any, Any, dict[str, Any]]]) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            stop = self._cancel = threading.Event()
            self._thread = threading.Thread(target=self._work, args=(item, stop, run),
                                            daemon=True, name='xdog-terminal-hook')
            self._thread.start()
        return True

    def _work(self, item: HeldTerminalInput, stop: threading.Event,
              run: Callable[[HeldTerminalInput], Coroutine[Any, Any, dict[str, Any]]]) -> None:
        async def owned() -> dict[str, Any]:
            task = asyncio.create_task(run(item))
            with self._lock:
                self._loop, self._task = asyncio.get_running_loop(), task
            if stop.is_set():
                task.cancel()
            try:
                return await task
            finally:
                with self._lock:
                    self._loop, self._task = None, None
        try:
            result = asyncio.run(owned())
        except (Exception, asyncio.CancelledError):
            result = {'error': True}
        with self._lock:
            self._busy = False
        self.publish({'type': 'terminal_input_result', 'id': item.id, 'result': result})

    def cancel(self) -> None:
        with self._lock:
            self._cancel.set()
            loop, task = self._loop, self._task
            if loop is not None and task is not None:
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass
