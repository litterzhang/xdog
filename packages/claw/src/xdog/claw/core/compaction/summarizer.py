"""Summarizer — generates structured conversation summaries via direct LLM call.

Uses the selected stream adapter directly instead of creating a full Agent.
Summarization is a single stateless model request with no tool execution.
"""
from __future__ import annotations

from xdog.agent import AgentMessage
from xdog.agent.core import CustomAgentMessage, StreamFn
from xdog.agent.summarization import stream_summary, summary_instructions
from xdog.ai.types import (
    Context,
    Message,
    StreamOptions,
    UserMessage,
)
from xdog.claw.core.compaction.prompts import SUMMARIZER_SYSTEM_PROMPT, build_summary_prompt


class Summarizer:
    """Generates a structured summary of a conversation.

    Reusable — create once per group, call ``summarize()`` each compaction.
    """

    def __init__(self, model: str, stream_fn: StreamFn | None = None,
                 options: StreamOptions = StreamOptions()) -> None:
        self._model = model
        self._stream_fn = stream_fn
        self._options = options

    async def summarize(
        self,
        messages: list[AgentMessage],
        previous_summary: str | None = None,
        *, instructions: str = "",
    ) -> str:
        """Produce a valid summary; failure must not destroy unsummarized history."""
        instructions = summary_instructions(instructions)
        prompt_text = build_summary_prompt(previous_summary)
        if instructions:
            prompt_text += "\n\nUser's compaction focus (not a tool request):\n" + instructions
        from xdog.agent.extensions.context_message import summary_text
        sendable: list[Message] = []
        for message in messages:
            if not isinstance(message, CustomAgentMessage):
                sendable.append(message)
            elif custom_text := summary_text(message):
                sendable.append(UserMessage(content=custom_text))

        context = Context(system_prompt=SUMMARIZER_SYSTEM_PROMPT,
                          messages=(*sendable, UserMessage(content=prompt_text)), tools=())
        stream_fn = self._stream_fn
        if stream_fn is None:
            import xdog.ai as ai
            from xdog.agent.helpers import stream_fn_from_provider
            stream_fn = stream_fn_from_provider(ai.load())
        return await stream_summary(stream_fn, self._model, context, self._options)
