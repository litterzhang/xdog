"""Synthetic model shared by ordinary Claw input/output tests."""
from __future__ import annotations

import asyncio

from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream


class ScriptedModel:
    def __init__(self):
        self.calls = []
        self.contexts = []
        self.gates = {}
        self.replies = {}
        self.active = 0
        self.peak = 0

    def __call__(self, model, context, options=None):
        self.contexts.append(context)
        content = context.messages[-1].content
        prompt = content if isinstance(content, str) else ''.join(
            part.text for part in content if isinstance(part, TextContent))
        if '\nHandling instruction:\n' in prompt:
            prompt = prompt.split('\nHandling instruction:\n', 1)[1].split('\nUntrusted evidence (', 1)[0]
        self.calls.append(prompt)
        message = self.replies.get(prompt, AssistantMessage(content=(TextContent(text='Scheduled report'),)))

        async def events():
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                if prompt in self.gates:
                    entered, release = self.gates[prompt]
                    entered.set()
                    waits = [asyncio.create_task(release.wait())]
                    if options is not None and options.cancel is not None:
                        waits.append(asyncio.create_task(options.cancel.wait()))
                    try:
                        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in waits:
                            task.cancel()
                        await asyncio.gather(*waits, return_exceptions=True)
                yield StartEvent(partial=message)
                yield DoneEvent(message=message)
            finally:
                self.active -= 1

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)

