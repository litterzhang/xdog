"""Silent memory maintenance before compaction or rotation via the existing loop.

Before context reduction or rotation, the flush runner
executes a silent agent turn that saves durable facts to long-term memory
and the daily log.  Uses ``run_agent_loop()`` directly — no Agent wrapper
needed since we don't need state management, event subscriptions, or queues.
"""
from __future__ import annotations

import logging
from typing import Any

from xdog.agent.agent_loop import run_agent_loop
from xdog.agent.core import AgentContext, AgentMessage, AgentTool, StreamFn
from xdog.agent.types import AgentLoopConfig
from xdog.ai.types import StreamOptions, UserMessage
from xdog.claw.core.compaction.prompts import build_flush_prompt
from xdog.claw.core.persistence.handoff import project

logger = logging.getLogger(__name__)


class FlushRunner:
    """Runs silent memory maintenance before compaction or rotation.

    Reusable — create once per group, call ``run()`` each time compaction fires.
    """

    def __init__(self, model: str, stream_fn: StreamFn | None = None) -> None:
        self._model = model
        self._stream_fn = stream_fn

    async def run(
        self,
        messages: list[AgentMessage],
        system_prompt: str,
        tools: list[AgentTool],
        *, options: StreamOptions | None = None, ctx: dict[str, Any] | None = None,
    ) -> bool:
        """Execute a silent flush turn with access to memory tools."""
        try:
            sfn = self._stream_fn
            if sfn is None:
                import xdog.ai as ai
                from xdog.agent.helpers import stream_fn_from_provider
                sfn = stream_fn_from_provider(ai.load())

            context = AgentContext(
                system_prompt=system_prompt,
                messages=[project(message) for message in messages],
                tools=tools,
            )
            prompts: list[AgentMessage] = [UserMessage(content=build_flush_prompt())]

            failed = False

            async def emit(event: Any) -> None:
                nonlocal failed
                if event.type == "tool_execution_end" and (event.is_error or event.interrupted):
                    failed = True
                if event.type == "message_end" and getattr(event.message, "stop_reason", None) in {"error", "aborted"}:
                    failed = True

            await run_agent_loop(
                prompts, context, AgentLoopConfig(), emit,
                sfn, self._model, options if options is not None else StreamOptions(), tool_ctx=ctx,
            )
            return not failed
        except Exception:
            logger.exception("Silent memory maintenance failed")
            return False
