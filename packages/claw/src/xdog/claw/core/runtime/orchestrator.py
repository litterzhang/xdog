"""Top-level orchestrator — message routing and lifecycle management."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any

from xdog.claw.channels.base import Channel
from xdog.claw.config import ClawConfig
from xdog.claw.core.chunker import BlockChunker
from xdog.claw.core.planning.task_scheduler import TaskScheduler
from xdog.claw.core.queue import MessageQueue
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.session import TurnResult
from xdog.claw.core.types import (
    Group,
    GroupInput,
    QueueMode,
    ScheduledTask,
    SystemInput,
    SystemInputKind,
    UserInput,
)

logger = logging.getLogger(__name__)


class Orchestrator:
    """Coordinates channels, groups, sessions, and agent execution.

    All messages enter through ``route_message()``. Background work
    (scheduled tasks, goal manager) is driven by ``tick()``.
    """

    def __init__(
        self,
        config: ClawConfig,
        *,
        model: Any = None,
        stream_fn: Any = None,
        data_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._model = model
        self._stream_fn = stream_fn
        self._data_dir = Path(data_dir or config.data_dir)
        self._channels: list[Channel] = []
        self._runtimes: dict[str, GroupRuntime] = {}
        self._queue = MessageQueue(max_concurrent=config.max_concurrent_agents)
        self._scheduler = TaskScheduler(self._data_dir / "scheduled_tasks.json")
        self._chunker = BlockChunker()

    # -- Group registration ----------------------------------------------------

    def register_group(self, group: Group) -> None:
        """Register a group and initialize its runtime."""
        runtime = GroupRuntime.create(
            group,
            self._data_dir,
            model=self._model,
            stream_fn=self._stream_fn,
            send_fn=self._send_to_channels,
        )
        # Bind route_fn so the goal system can send SystemInput
        # messages that the agent actually receives and acts on
        runtime.goal_manager._route_fn = self.route_message
        self._runtimes[group.id] = runtime

    def add_channel(self, channel: Channel) -> None:
        """Add a messaging channel."""
        self._channels.append(channel)

        async def _on_channel_message(message: GroupInput) -> None:
            result = await self.route_message(
                message,
                on_assistant_message=partial(self._send_response, message.group_id),
            )
            if result is None:
                return
            group_id = message.group_id
            if result.error:
                await self._send_to_channels(group_id, f"Error: {result.error}")

        channel.set_on_message(_on_channel_message)

    def auto_register_group(self, group_id: str) -> None:
        """Auto-register a group on first message (e.g. from WeChat users)."""
        if group_id in self._runtimes:
            return
        group = Group(id=group_id, name=group_id)
        logger.info("Auto-registering group: %s", group_id)
        self.register_group(group)

    # -- Message routing -------------------------------------------------------

    async def route_message(
        self,
        message: GroupInput,
        *,
        on_display_event: Any = None,
        on_text_delta: Any = None,
        on_assistant_message: Callable[[str], Awaitable[None]] | None = None,
        reserved: bool = False,
    ) -> TurnResult | None:
        """Single entry point for all messages."""
        group_id = message.group_id
        if group_id not in self._runtimes:
            if group_id.startswith("weixin:"):
                self.auto_register_group(group_id)
            else:
                logger.warning("Message for unknown group: %s", group_id)
                return None

        runtime = self._runtimes[group_id]
        group = runtime.group
        mode = group.config.queue_mode if group.config else QueueMode.COLLECT
        max_queued = group.config.max_queued_messages if group.config else 50
        debounce_ms = group.config.debounce_ms if group.config else 0

        if self._queue.is_running(group_id) and not reserved:
            async def _on_steer(msg: GroupInput) -> None:
                runtime.steer(msg.content)

            async def _on_follow_up(msg: GroupInput) -> None:
                runtime.follow_up(msg.content)

            await self._queue.enqueue(
                message, mode=mode, on_steer=_on_steer,
                on_follow_up=_on_follow_up, max_queued=max_queued,
            )
            return None

        result = await self._execute_with_queue(
            message,
            on_display_event=on_display_event,
            on_text_delta=on_text_delta,
            on_assistant_message=on_assistant_message,
        )

        queued = await self._queue.collect_with_debounce(group_id, debounce_ms)
        for qmsg in queued:
            qresult = await self._execute_with_queue(
                qmsg,
                on_assistant_message=partial(self._send_response, group_id),
            )
            if qresult and qresult.error:
                await self._send_to_channels(group_id, f"Error: {qresult.error}")

        return result

    async def _execute_with_queue(
        self,
        message: GroupInput,
        *,
        on_display_event: Any = None,
        on_text_delta: Any = None,
        on_assistant_message: Callable[[str], Awaitable[None]] | None = None,
    ) -> TurnResult | None:
        """Execute a message with concurrency control."""
        group_id = message.group_id
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return None

        is_user = isinstance(message, UserInput)
        async with self._queue.acquire(group_id, is_user=is_user):
            session = runtime.get_or_create_session()
            result: TurnResult | None = await session.run_turn(
                message,
                on_display_event=on_display_event,
                on_text_delta=on_text_delta,
                on_assistant_message=on_assistant_message,
            )
            return result

    # -- Channel delivery ------------------------------------------------------

    async def _send_to_channels(self, group_id: str, text: str) -> None:
        for channel in self._channels:
            await channel.send_message(group_id, text)

    async def _send_response(self, group_id: str, text: str) -> None:
        chunks = self._chunker.chunk(text)
        if not chunks:
            await self._send_to_channels(group_id, text)
            return
        for chunk in chunks:
            for channel in self._channels:
                await channel.send_chunk(group_id, chunk)

    # -- Group operations (public API for Gateway) -----------------------------

    def reset_group(self, group_id: str) -> bool:
        """Reset the active session. Returns True on success."""
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return False
        runtime.reset_session()
        return True

    def abort_group(self, group_id: str) -> bool:
        """Abort the active session for a known group."""
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return False
        runtime.abort()
        return True

    async def wait_group_idle(self, group_id: str) -> None:
        runtime = self._runtimes.get(group_id)
        if runtime:
            await runtime.wait_for_idle()

    def is_group_running(self, group_id: str) -> bool:
        return self._queue.is_running(group_id)

    def try_reserve_group(self, group_id: str) -> bool:
        return self._queue.try_reserve(group_id)

    def claim_group_reservation(self, group_id: str) -> None:
        self._queue.claim_reservation(group_id)

    def release_group_reservation(self, group_id: str) -> None:
        self._queue.release_reservation(group_id)

    def get_group_ids(self) -> list[str]:
        return list(self._runtimes.keys())

    def get_session_info(self, group_id: str) -> dict[str, Any]:
        """Session metadata + history for a group (delegates to runtime)."""
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return {}
        return runtime.get_session_info()

    def get_new_session_id(self, group_id: str) -> str | None:
        """Current session ID for a group (for reset acknowledgment)."""
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return None
        return runtime.get_new_session_id()

    def pop_goal_notifications(self, group_id: str) -> list[Any]:
        """Pop pending goal notifications for a group."""
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return []
        return runtime.pop_goal_notifications()

    @property
    def scheduler(self) -> TaskScheduler:
        return self._scheduler

    # -- Background tick -------------------------------------------------------

    async def tick(self) -> None:
        """Run one scheduler + goal manager cycle."""
        due_tasks = self._scheduler.get_due_tasks()
        for task in due_tasks:
            try:
                await self.run_scheduled_task(task)
            except Exception:
                logger.exception("Scheduled task %s failed", task.id)

        # Goal manager tick — process queued verifications and re-plans
        for group_id in self.get_group_ids():
            runtime = self._runtimes.get(group_id)
            if runtime and (runtime.goal_manager.has_active_goals()
                            or runtime.goal_manager.has_pending_work()):
                try:
                    await runtime.goal_manager.tick(
                        tools=runtime.tools,
                        tool_ctx={
                            "group_id": group_id,
                            "workspace_dir": str(runtime.workspace_dir) if runtime.workspace_dir else "",
                            "data_dir": str(runtime.data_dir),
                        },
                    )
                except Exception:
                    logger.exception("Goal manager tick failed for group %s", group_id)

    async def run_scheduled_task(self, task: ScheduledTask) -> TurnResult | None:
        """Execute a scheduled task as a system input."""
        message = SystemInput(
            group_id=task.group_id, content=task.prompt,
            kind=SystemInputKind.SCHEDULER,
        )
        result = await self._execute_with_queue(message)
        self._scheduler.mark_run(task.id)
        return result

    # -- Lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        for channel in self._channels:
            await channel.connect()

    async def stop(self) -> None:
        for channel in self._channels:
            await channel.disconnect()
